from decimal import Decimal
from django.db import transaction
from .models import Wallet, WalletTransaction


def _change_balance(wallet, amount, transaction_type, description):
    amount = Decimal(amount)
    if not amount.is_finite() or amount <= 0:
        raise ValueError("Amount must be positive and finite")
    with transaction.atomic():
        locked = Wallet.objects.select_for_update().get(pk=wallet.pk)
        if transaction_type == "debit" and locked.balance < amount:
            raise ValueError("Insufficient wallet balance")
        balance_before = locked.balance
        locked.balance += amount if transaction_type == "credit" else -amount
        locked.save(update_fields=["balance", "updated_at"])
        WalletTransaction.objects.create(
            wallet=locked, amount=amount, transaction_type=transaction_type,
            description=description, balance_before=balance_before,
            balance_after=locked.balance,
        )
        wallet.balance = locked.balance
        return locked.balance


def credit_wallet(wallet, amount, description=""):
    return _change_balance(wallet, amount, "credit", description)


def debit_wallet(wallet, amount, description=""):
    return _change_balance(wallet, amount, "debit", description)
