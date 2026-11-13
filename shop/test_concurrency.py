"""Run against PostgreSQL; SQLite does not implement SELECT FOR UPDATE."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connections
from django.test import Client, TransactionTestCase, skipUnlessDBFeature
from django.urls import reverse

from payments.models import Payment
from products.models import Product, ProductVariant
from users.models import Address
from wallet.models import Wallet, WalletTransaction
from .models import Order


@skipUnlessDBFeature("has_select_for_update")
class PaymentConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email="race@example.com", fullName="Buyer")
        self.address = Address.objects.create(
            user=self.user, full_name="Buyer", phone_number="9999999999",
            address_line1="Test", city="Test", state="Test", pincode="123456",
        )
        product = Product.objects.create(name="Last shoe", price="1000", color="Black", category="MEN")
        self.variant = ProductVariant.objects.create(product=product, size="9", stock=1)
        self.payment = self.make_payment("order_first")

    def make_payment(self, provider_order):
        return Payment.objects.create(
            user=self.user, amount="1180", purpose="order_payment",
            razorpay_order_id=provider_order,
            checkout_snapshot={
                "address_id": self.address.pk, "subtotal": "1000", "gst": "180",
                "delivery_charge": "0", "discount_amount": "0", "coupon_id": None,
                "items": [{"cart_item_id": 99999, "variant_id": self.variant.pk,
                           "quantity": 1, "price": "1000"}],
            },
        )

    def call(self, url, provider_order):
        try:
            client = Client()
            client.force_login(get_user_model().objects.get(pk=self.user.pk))
            response = client.get(url, {
                "payment_id": "pay_" + provider_order, "order_id": provider_order,
                "signature": "valid",
            })
            return response.status_code, response.url
        finally:
            connections.close_all()

    def run_callbacks(self, url, order_ids):
        barrier = Barrier(2)
        with patch("shop.views.razorpay.Client") as provider_factory:
            provider = provider_factory.return_value
            provider.utility.verify_payment_signature.side_effect = lambda _: barrier.wait(timeout=10)
            provider.payment.fetch.side_effect = lambda payment_id: {
                "order_id": payment_id.removeprefix("pay_"), "amount": 118000,
                "currency": "INR", "status": "captured",
            }
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = [pool.submit(self.call, url, order_id) for order_id in order_ids]
                return [future.result(timeout=20) for future in results]

    def test_simultaneous_replay_creates_one_order(self):
        results = self.run_callbacks(
            reverse("razorpay_payment_verify"), ["order_first", "order_first"],
        )
        self.assertEqual(results[0], results[1])
        self.assertEqual(Order.objects.count(), 1)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 0)

    def test_competing_payments_cannot_sell_the_same_last_unit(self):
        self.make_payment("order_second")
        self.run_callbacks(reverse("razorpay_payment_verify"), ["order_first", "order_second"])
        self.assertEqual(Order.objects.count(), 1)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 0)

    def test_simultaneous_topup_callbacks_credit_once(self):
        self.payment.purpose = "wallet_topup"
        self.payment.save()
        self.run_callbacks(reverse("wallet_payment_success"), ["order_first", "order_first"])
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "SUCCESS")
        self.assertEqual(Wallet.objects.get(user=self.user).balance, self.payment.amount)
        self.assertEqual(WalletTransaction.objects.count(), 1)
