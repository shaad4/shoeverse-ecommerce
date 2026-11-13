from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from payments.models import Payment
from products.models import Product, ProductVariant
from users.models import Address
from wallet.models import Wallet
from .models import CartItem, Order, OrderItem, ReturnRequest


class CommerceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="buyer@example.com", fullName="Buyer", password="test-password",
        )
        self.client.force_login(self.user)
        self.address = Address.objects.create(
            user=self.user, full_name="Buyer", phone_number="9999999999",
            address_line1="1 Test Street", city="Test", state="Test", pincode="123456",
        )
        self.product = Product.objects.create(
            name="Shoe", price="1000.00", color="Black", category="MEN",
        )
        self.variant = ProductVariant.objects.create(product=self.product, size="9", stock=10)
        self.cart_item = CartItem.objects.create(user=self.user, variant=self.variant)
        self.provider_patch = patch("shop.views.razorpay.Client")
        self.provider = self.provider_patch.start().return_value
        self.addCleanup(self.provider_patch.stop)
        self.provider.order.create.return_value = {"id": "order_test", "amount": 118000}
        self.provider.payment.fetch.return_value = {
            "order_id": "order_test", "amount": 118000,
            "currency": "INR", "status": "captured",
        }

    def start_payment(self):
        response = self.client.post(reverse("place_order"), {
            "address_id": self.address.pk, "payment_method": "razorpay",
        })
        self.assertEqual(response.status_code, 200)
        return Payment.objects.get(razorpay_order_id="order_test")

    def verify(self):
        return self.client.get(reverse("razorpay_payment_verify"), {
            "payment_id": "pay_test", "order_id": "order_test",
            "signature": "valid-signature", "address_id": self.address.pk,
        })

    def test_payment_uses_snapshot_after_cart_and_price_change(self):
        payment = self.start_payment()
        self.cart_item.quantity = 4
        self.cart_item.save()
        self.product.price = "9000.00"
        self.product.save()
        self.verify()
        order = Order.objects.get()
        self.assertEqual(order.total_amount, payment.amount)
        self.assertEqual(order.items.get().quantity, 1)
        self.assertEqual(order.items.get().price, Decimal("1000"))
        self.assertEqual(CartItem.objects.get().quantity, 4)

    def test_payment_fulfills_snapshot_even_if_cart_was_cleared(self):
        self.start_payment()
        CartItem.objects.all().delete()
        self.verify()
        self.assertEqual(Order.objects.count(), 1)

    def test_payment_replay_returns_original_order(self):
        payment = self.start_payment()
        first_response = self.verify()
        new_item = CartItem.objects.create(user=self.user, variant=self.variant)
        second_response = self.verify()
        self.assertEqual(first_response.url, second_response.url)
        self.assertEqual(Order.objects.count(), 1)
        self.assertTrue(CartItem.objects.filter(pk=new_item.pk).exists())
        self.variant.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(self.variant.stock, 9)
        self.assertEqual(payment.order_id, Order.objects.get().pk)

    def test_another_users_payment_is_rejected(self):
        payment = self.start_payment()
        other = get_user_model().objects.create_user(email="other@example.com", fullName="Other")
        payment.user = other
        payment.save()
        self.verify()
        self.assertFalse(Order.objects.exists())
        payment.refresh_from_db()
        self.assertEqual(payment.status, "PENDING")

    def test_wallet_topup_cannot_authorize_an_order(self):
        payment = self.start_payment()
        payment.purpose = "wallet_topup"
        payment.save()
        self.verify()
        self.assertFalse(Order.objects.exists())

    def test_wrong_amount_currency_order_or_capture_is_rejected(self):
        self.start_payment()
        for field, value in (
            ("amount", 100), ("currency", "USD"),
            ("order_id", "other_order"), ("status", "authorized"),
        ):
            with self.subTest(field=field):
                details = {
                    "order_id": "order_test", "amount": 118000,
                    "currency": "INR", "status": "captured", field: value,
                }
                self.provider.payment.fetch.return_value = details
                self.verify()
                self.assertFalse(Order.objects.exists())
        self.assertEqual(Payment.objects.get().status, "PENDING")

    def test_missing_legacy_snapshot_is_not_fulfilled(self):
        payment = self.start_payment()
        payment.checkout_snapshot = {}
        payment.save()
        self.verify()
        self.assertFalse(Order.objects.exists())

    def test_stock_change_during_payment_prevents_overselling(self):
        self.start_payment()
        self.variant.stock = 0
        self.variant.save()
        response = self.verify()
        self.assertFalse(Order.objects.exists())
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 0)
        self.assertContains(self.client.get(response.url), "Payment received")

    def test_inactive_variant_is_not_fulfilled(self):
        self.start_payment()
        self.variant.is_active = False
        self.variant.save()
        self.verify()
        self.assertFalse(Order.objects.exists())

    def test_client_failure_cannot_downgrade_success(self):
        self.start_payment()
        self.verify()
        self.client.get(reverse("razorpay_payment_failed"), {"order_id": "order_test"})
        self.assertEqual(Payment.objects.get().status, "SUCCESS")

    def make_order(self):
        order = Order.objects.create(
            user=self.user, address=self.address, payment_method="wallet",
            status="Processing", subtotal="2000", gst="360", total_amount="2360",
        )
        items = [OrderItem.objects.create(
            order=order, variant=self.variant, quantity=1, price="1000",
        ) for _ in range(2)]
        return order, items

    def test_repeated_item_cancellation_restores_stock_once(self):
        order, items = self.make_order()
        url = reverse("cancel_order_item", args=[items[0].pk])
        self.client.post(url)
        balance = Wallet.objects.get(user=self.user).balance
        self.client.post(url)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 11)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, balance)
        order.refresh_from_db()
        self.assertEqual(order.status, "Processing")

    def test_whole_cancellation_skips_previously_cancelled_item(self):
        order, items = self.make_order()
        self.client.post(reverse("cancel_order_item", args=[items[0].pk]))
        self.client.post(reverse("cancel_order", args=[order.order_id]))
        self.client.post(reverse("cancel_order", args=[order.order_id]))
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 12)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("2360"))
        self.assertFalse(order.items.exclude(status="Cancelled").exists())

    def test_return_refund_excludes_coupon_and_shipping(self):
        order, items = self.make_order()
        order.subtotal = Decimal("2000")
        order.discount_amount = Decimal("1000")
        order.gst = Decimal("180")
        order.delivery_charge = Decimal("100")
        order.total_amount = Decimal("1280")
        order.save()
        refunds = [ReturnRequest(order_item=item, user=self.user).calculate_refund_amount()
                   for item in items]
        self.assertEqual(refunds, [Decimal("590"), Decimal("590")])
        self.assertEqual(sum(refunds), order.total_amount - order.delivery_charge)

    def test_refund_rounding_never_exceeds_paid_pool(self):
        order, items = self.make_order()
        items.append(OrderItem.objects.create(order=order, variant=self.variant, price="1000"))
        order.subtotal = Decimal("3000")
        order.discount_amount = Decimal("2999.99")
        order.gst = Decimal("0")
        order.total_amount = Decimal("0.01")
        order.save()
        refunds = [ReturnRequest(order_item=item, user=self.user).calculate_refund_amount()
                   for item in items]
        self.assertEqual(sum(refunds), Decimal("0.01"))
