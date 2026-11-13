from decimal import Decimal


def verify_captured_payment(client, payment, payment_id):
    """A checkout signature alone does not establish capture or the paid amount."""
    details = client.payment.fetch(payment_id)
    if (
        details.get("order_id") != payment.razorpay_order_id
        or details.get("amount") != int(payment.amount * Decimal("100"))
        or details.get("currency") != "INR"
        or details.get("status") != "captured"
    ):
        raise ValueError("Payment has not been captured for the expected amount.")
