from decimal import Decimal
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from payments.models import Payment
from .models import Wallet, WalletTransaction
from .utils import credit_wallet, debit_wallet


class WalletTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email="wallet@example.com", fullName="Buyer")
        self.client.force_login(self.user)
        self.wallet = Wallet.objects.create(user=self.user, balance="100")
        self.payment = Payment.objects.create(
            user=self.user, amount="50", purpose="wallet_topup", razorpay_order_id="order_topup",
        )
        self.patch_provider = patch("wallet.views.get_razorpay_client")
        self.provider = self.patch_provider.start().return_value
        self.addCleanup(self.patch_provider.stop)
        self.provider.payment.fetch.return_value = {
            "order_id": "order_topup", "amount": 5000, "currency": "INR", "status": "captured",
        }

    def verify(self):
        return self.client.get(reverse("wallet_payment_success"), {
            "payment_id": "pay_topup", "order_id": "order_topup", "signature": "valid",
        })

    def test_topup_is_credited_only_once(self):
        self.verify()
        self.verify()
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("150"))
        self.assertEqual(WalletTransaction.objects.count(), 1)
        self.assertEqual(Payment.objects.get().status, "SUCCESS")

    def test_ledger_failure_rolls_back_payment_and_balance(self):
        with patch("wallet.utils.WalletTransaction.objects.create", side_effect=RuntimeError("ledger failed")):
            with self.assertRaises(RuntimeError):
                self.verify()
        self.wallet.refresh_from_db()
        self.payment.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("100"))
        self.assertEqual(self.payment.status, "PENDING")
        self.assertFalse(WalletTransaction.objects.exists())
        self.verify()
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("150"))

    def test_checkout_payment_cannot_credit_wallet(self):
        self.payment.purpose = "order_payment"
        self.payment.save()
        self.assertEqual(self.verify().status_code, 404)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("100"))

    def test_mismatched_capture_does_not_credit_wallet(self):
        self.provider.payment.fetch.return_value["amount"] = 1
        self.verify()
        self.assertFalse(WalletTransaction.objects.exists())
        self.assertEqual(Payment.objects.get().status, "PENDING")

    def test_stale_wallet_objects_do_not_overwrite_new_balances(self):
        stale = Wallet.objects.get(pk=self.wallet.pk)
        credit_wallet(self.wallet, "10")
        credit_wallet(stale, "20")
        debit_wallet(self.wallet, "5")
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("125"))
        self.assertEqual(WalletTransaction.objects.count(), 3)

    def test_invalid_topup_amounts_are_rejected(self):
        for amount in ["-1", "0", "NaN", "Infinity", "abc", "1.001", "100000000"]:
            with self.subTest(amount=amount):
                self.client.post(reverse("wallet_add_money"), {"amount": amount})
                self.assertEqual(Payment.objects.count(), 1)
        self.provider.order.create.assert_not_called()
