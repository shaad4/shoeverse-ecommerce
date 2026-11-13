from django.shortcuts import render, get_object_or_404, redirect
from users.decorator import user_required
from .models import Wallet, WalletTransaction
import razorpay
from decimal import Decimal, InvalidOperation
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.contrib import messages
from .utils import credit_wallet
from django.db import transaction
from payments.utils import verify_captured_payment
from payments.models import Payment
from django.core.paginator import Paginator
# Create your views here.



@user_required
def wallet_view(request):
    wallet, created = Wallet.objects.get_or_create(user=request.user)
    transactions = wallet.transactions.all().order_by("-created_at")

    paginator = Paginator(transactions,5)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)


    context = {
        'wallet': wallet,
        'transactions': page_obj,
    }

    return render(request, 'wallet.html', context)


def get_razorpay_client():
    return razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))

@user_required
def wallet_add_money_view(request):
    if request.method == "POST":
        amount = request.POST.get('amount')
        if not amount:
            messages.error(request, "Enter an amount")
            return redirect('wallet')
        
        try:
            amount = Decimal(amount)
            if (not amount.is_finite() or amount <= 0
                    or amount != amount.quantize(Decimal("0.01"))
                    or amount > Decimal("99999999.99")):
                raise ValueError("Invalid amount")
        except (InvalidOperation, ValueError):
            messages.error(request, "Enter a positive amount with at most two decimal places.")
            return redirect("wallet")

        payment = Payment.objects.create(
            user=request.user,
            amount=amount,
            purpose = "wallet_topup",
        )

        client = get_razorpay_client()
        order_data = {
            "amount" : int(amount * 100),
            "currency" : "INR",
            "receipt" : f"wallet_{payment.id}",
            "payment_capture" : 1,
        }

        order = client.order.create(order_data)

        payment.razorpay_order_id = order['id']
        payment.save()

        context = {
            "payment" : payment,
            "order" : order,
            "razorpay_key_id" : settings.RAZORPAY_KEY_ID,
            "user" : request.user,
        }
        return render(request, "wallet_add_money_razorpay.html", context)
    
    return redirect('wallet')



@csrf_exempt
@user_required
def wallet_payment_success_view(request):
    payment_id = request.GET.get("payment_id")
    order_id = request.GET.get("order_id")
    signature = request.GET.get("signature")

    if not payment_id or not order_id or not signature:
        messages.error(request, "Invalid payment details.")
        return redirect("wallet")
    
    client = get_razorpay_client()

    params_dict = {
        "razorpay_order_id": order_id,
        "razorpay_payment_id": payment_id,
        "razorpay_signature": signature,
    }

    try:
        # Razorpay signature verification
        client.utility.verify_payment_signature(params_dict)

        with transaction.atomic():
            payment = get_object_or_404(
                Payment.objects.select_for_update(), razorpay_order_id=order_id,
                user=request.user, purpose="wallet_topup",
            )
            if payment.status != "SUCCESS":
                verify_captured_payment(client, payment, payment_id)
                wallet, _ = Wallet.objects.get_or_create(user=request.user)
                credit_wallet(wallet, payment.amount, "Wallet Top-up via Razorpay")
                payment.status = "SUCCESS"
                payment.razorpay_payment_id = payment_id
                payment.razorpay_signature = signature
                payment.save()

        messages.success(request, f"₹{payment.amount} added to wallet successfully!")
        return redirect("wallet")

    except (razorpay.errors.SignatureVerificationError, ValueError):
        messages.error(request, "Payment verification failed.")
        return redirect("wallet")
    

@user_required
def wallet_payment_failed_view(request):
    messages.error(request, "Payment failed or canceled. Please try again.")
    return redirect("wallet")

