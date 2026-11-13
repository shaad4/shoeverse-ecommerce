from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import get_user_model, login, authenticate, logout
from django.contrib import messages
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.utils import timezone
from datetime import timedelta
from .utils import generate_otp, send_otp_email, password_reset_email, email_change_confirmation
from django.conf import settings
from users.models import EmailOTP

from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes
from django.db.models import Q
from .decorator import user_required
from django.http import JsonResponse
from django.core.paginator import Paginator
from .forms import AddressForm

from products.models import Product, ProductVariant
from .models import Address
from shop.models import Order, ReturnRequest
import logging

from shop.utils import generate_invoice
from django.http import HttpResponse
import json

from wallet.utils import credit_wallet, debit_wallet
from wallet.models import WalletTransaction, Wallet
from decimal import Decimal
from users.models import Banner

logger = logging.getLogger('users')
User = get_user_model()

def clean_input(value: str) -> str:
    if not value:
        return ""
    return value.strip()

# Create your views here.

def signup_view(request):
    referral = request.GET.get('ref','')

    if request.method == "POST":
        email = clean_input(request.POST.get('email'))
        fullName  = clean_input(request.POST.get('fullName'))
        password = clean_input(request.POST.get('password'))
        confirm_password = clean_input(request.POST.get('confirm_password'))
        referral = clean_input(request.POST.get('referral'))

        logger.info(f"Signup attempt: email={email}, referral={referral}")

        #Validations

        if not email or not fullName or not password or not confirm_password:
            logger.warning("Signup failed: Missing required fields")
            messages.error(request, "All fields must be filled.", extra_tags="signup")
            return redirect("signup")
        
        try:
            validate_email(email)
        except ValidationError:
            logger.warning(f"Signup failed: Invalid email ({email})")
            messages.error(request, "Enter a valid email." , extra_tags="signup")
            return redirect("signup")
        
        if User.objects.filter(email=email).exists():
            logger.warning(f"Signup failed: Email already exists ({email})")
            messages.error(request, "Email already registered.", extra_tags="signup")
            return redirect("signup")
        
        if len(password) < 6:
            logger.warning(f"Signup failed: Weak password for {email}")
            messages.error(request, "Password must be at least 6 characters.", extra_tags="signup")
            return redirect("signup")

        if password.isnumeric():
            logger.warning(f"Signup failed: Numeric-only password ({email})")
            messages.error(request, "Password cannot be numeric only.", extra_tags="signup")
            return redirect("signup")

        if password != confirm_password:
            logger.warning(f"Signup failed: Password mismatch ({email})")
            messages.error(request, "Passwords do not match.", extra_tags="signup")
            return redirect("signup")
        
        referredBy = None
        if referral:
            try:
                referredBy = User.objects.get(referralCode=referral)
                logger.info(f"User {email} was referred by {referredBy.email}")
            except User.DoesNotExist:
                logger.warning(f"Invalid referral code used: {referral}")
                messages.error(request, "Invalid referral code.", extra_tags="signup")
                return redirect("signup")
        #user created - not verified  
        user = User.objects.create_user(
            email = email,
            fullName = fullName,
            password=password,
            referredBy = referredBy,
            is_active = False
        )

        logger.info(f"User created (not verified): {email}")

        #OTP generate
        otp = generate_otp()
        expires_at = timezone.now() + timedelta(minutes=5)

        #delete old otp if exists
        EmailOTP.objects.filter(user=user).delete()

        #store otp
        EmailOTP.objects.create(
            user=user,
            otp=otp,
            expires_at=expires_at,
            is_verified  = False
        )

        logger.info(f"OTP generated for {email}: {otp}")

        #send OTP EMAIL
        try:
            send_otp_email(email, otp)
            logger.info(f"OTP email sent to {email}")
        except Exception as e:
            logger.error(f"Email sending failed for {email}: {e}")
            messages.error(request, "Unable to send OTP right now. Please try again.", extra_tags="signup")
            user.delete()  # rollback user creation
            return redirect("signup")
        
        #save session for user to verify 
        request.session["pending_user_id"] = user.id

        messages.success(
            request,
            "OTP sent to your email. Please verify to continue.", extra_tags="signup"
        )

        return redirect("verify_otp")
    
    return render(request, "users/signup.html", {"referral_code": referral})


def verify_otp_view(request):

    user_id = request.session.get("pending_user_id")

    if not user_id:
        logger.warning("OTP verification failed: No pending user session.")
        messages.error(request, "Session expired. Please sign up again.")
        return redirect("signup")
    
    try:
        user = User.objects.get(id=user_id)
        logger.info(f"OTP verification for user: {user.email}")
    except User.DoesNotExist:
        logger.error(f"Pending user not found for ID: {user_id}")
        messages.error(request, "User not found. Please sign up again.")
        return redirect("signup")
    
    try:
        otp_obj = EmailOTP.objects.get(user=user)
    except EmailOTP.DoesNotExist:
        logger.error(f"OTP record missing for user: {user.email}")
        messages.error(request, "OTP not found. Please sign up again.")
        return redirect("signup")
    
    if otp_obj.is_verified:
        logger.info(f"User {user.email} already verified. Logging in.")
        login(request, user)
        return redirect("home")
    
    


    if request.method == "POST":
        entered_otp = clean_input(request.POST.get('otp'))

        if not entered_otp:
            logger.warning(f"Empty OTP submission by user: {user.email}")
            messages.error(request, "Please enter the OTP.")
            return redirect("verify_otp")
        
        
        if timezone.now() > otp_obj.expires_at:
                logger.warning(f"OTP expired for {user.email}. Deleting account.")
                otp_obj.delete()
                user.delete()
                request.session.pop("pending_user_id", None)
                messages.error(request, "OTP expired. Please sign up again.")
                return redirect("signup")
        
        if entered_otp != otp_obj.otp:
                logger.warning(f"Incorrect OTP attempt for {user.email}. Entered: {entered_otp}")
                messages.error(request, "Incorrect OTP. Please try again.")
                return redirect("verify_otp")
        

        #if otp is success
        otp_obj.is_verified=True
        otp_obj.save()

        user.is_active = True
        user.save()

        logger.info(f"OTP verified successfully for {user.email}")

        login(request, user, backend=settings.AUTHENTICATION_BACKENDS[0])
        

        wallet , created = Wallet.objects.get_or_create(user=user)

        if user.referredBy:
            if not WalletTransaction.objects.filter(
                wallet = wallet,
                description__icontains = "Referral"
            ).exists():

                # Credit 500 to new user
                credit_wallet(wallet, 500, "Referral signup reward")
                #for referer 
                credit_wallet(user.referredBy.wallet, 500, f"Referral bonus for inviting {user.email}")

                


        #Remove session
        request.session.pop("pending_user_id", None)

        messages.success(request, "Email verified successfully!")
        logger.info(f"User logged in after OTP verification: {user.email}")

        return redirect("home")

    return render(request, "users/verify_otp.html",{"email":user.email})







def resend_otp_view(request):

    user_id = request.session.get("pending_user_id")

    if not user_id:
        messages.error(request, "Session expired. Please sign up again.")
        return redirect("signup")
    
    try:
        user = User.objects.get(id=user_id)
        otp_obj = EmailOTP.objects.get(user=user)
        logger.info(f"Resend OTP requested by: {user.email}")
    except:
        messages.error(request, "User or OTP record not found.")
        return redirect("signup")
    

    if otp_obj.last_sent_at and (timezone.now() - otp_obj.last_sent_at).seconds < 120:
        wait_time = 120 - (timezone.now() - otp_obj.last_sent_at).seconds
        messages.warning(request, f"Wait {wait_time} seconds before requesting new OTP.")
        return redirect("verify_otp")
    
    new_otp = generate_otp()
    otp_obj.otp = new_otp
    otp_obj.expires_at = timezone.now() + timedelta(minutes=5)
    otp_obj.last_sent_at = timezone.now()
    otp_obj.save()

    logger.info(f"New OTP generated for {user.email}: {new_otp}")

    send_otp_email(user.email, new_otp)
    logger.info(f"Resent OTP to {user.email}")

    messages.success(request, "A new OTP has been sent to your email.")
    return redirect("verify_otp")






def login_view(request):

    storage = messages.get_messages(request)
    for _ in storage:
        pass 

    if request.method == "POST":
        email = clean_input(request.POST.get('email'))
        password = clean_input(request.POST.get('password'))

        logger.info(f"Login attempt for email: {email}")

        if not email and password:
            messages.error(request, "Please enter both email and password.")
            return redirect('login')
        
        try:
            user_obj = User.objects.get(email=email)
            if not user_obj.is_active:
                if EmailOTP.objects.filter(user=user_obj, is_verified=False).exists():
                    request.session["pending_user_id"] = user_obj.id
                    messages.info(request, "Please verify your email to activate your account.")
                    return redirect("verify_otp")
                else:
                    messages.error(request, "Your account is deactivated. Contact support.")
                    return redirect("login")
        except User.DoesNotExist:
            messages.error(request, "Invalid email or password.")
            return redirect("login")
        
        if not user_obj.is_active:
            messages.error(request, "Your account is blocked. For more info, conatct support.")
            return redirect("login")
        
        
        user = authenticate(request, email=email, password=password)

       

        if user is None:
            logger.warning(f"Login failed for email: {email}")
            messages.error(request, "Invalid email or password.")
            return redirect('login')
        
        login(request, user)
        logger.info(f"User logged in successfully: {email}")
       

        return redirect("home")
    
    return render(request, "users/login.html")


def forget_password_view(request):
    if  request.method  == "POST":
        email  = clean_input(request.POST.get("email"))

        try:
            user =  User.objects.get(email=email)
        except User.DoesNotExist:
            messages.error(request, "No account found with this email.")
            return redirect("forgot_password")
        

        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)

        reset_link = request.build_absolute_uri(
            f"/reset-password/{uid}/{token}/"
        )

        try:
            password_reset_email(reset_link,  email)
            logger.info(f"reset email sent to {email}")
            messages.success(request, "Password reset link sent to your email.")
        except Exception as e:
            logger.error(f"Reset Email sending failed for {email}: {e}")
            messages.error(request, "Unable to send resend email right now. Please try again.")

        return redirect("forgot_password")
    return render(request, "users/forget_password.html")


def reset_password_view(request, uidb64, token):
    try:
        uid = urlsafe_base64_decode(uidb64).decode()
        user = User.objects.get(pk=uid)
    except Exception:
        user = None

    if user is None or not default_token_generator.check_token(user,  token):
        messages.error(request, "Invalid or expired reset link.")
        return redirect("forgot_password")
    

    if request.method == "POST":
        password = clean_input(request.POST.get("password"))
        confirm = clean_input(request.POST.get("confirm_password"))

        if password != confirm:
            messages.error(request, "Password do not match.")
            return redirect(request.path)
        
        if len(password) < 6:
            messages.error(request, "Password must be at least 6 characters.")
            return redirect(request.path)
        
        user.set_password(password)
        user.save()

        messages.success(request, "Password reset successfull. You can login in.")
        return redirect('login')
    
    return render(request, "users/reset_password.html")


    



def home_view(request):

    banners = Banner.objects.filter(is_active = True).order_by('order')

    products = Product.objects.filter(is_active=True).order_by("-created_at")
    featured_ids = settings.HOMEPAGE_FEATURED_PRODUCT_IDS
    if featured_ids:
        products = products.filter(id__in=featured_ids)
    context = {
        "featured_products": products.prefetch_related("images")[:4],
        "banners": banners,
    }


    return render(request, "users/home.html", context)


def logout_view(request):
    logout(request)
    messages.success(request,"logout of successfully")
    
    return redirect("home")  # Change to your landing page

#profile  

@user_required
def profile_view(request):

    user = request.user
    referral_link = request.build_absolute_uri(f"/signup/?ref={user.referralCode}")
    context = {
        "user" : user,
        "referral_link": referral_link,
    }

    return render(request, "users/profile_view.html", context)

@user_required
def profile_edit_view(request):
    user = request.user

    if request.method == "POST":

        if request.POST.get("remove_image") == "1" and user.profileImage:
            user.profileImage.delete(save=False)
            user.profileImage = None


        user.fullName = request.POST.get("fullName")
        phone_number = request.POST.get("phoneNumber")
        user.gender = request.POST.get("gender")
        date_of_birth = request.POST.get("dateOfBirth")
        user.dateOfBirth = date_of_birth if date_of_birth else None

        if phone_number:
            phone_exists = User.objects.filter(phoneNumber=phone_number).exclude(id=user.id).exists()
            
            if phone_exists:
                messages.error(request, f"The phone number {phone_number} is already linked to another account.")
                return render(request, "users/profile_edit.html", {"user": user})
            
        user.phoneNumber = phone_number


        if request.FILES.get("profileImage"):
            user.profileImage = request.FILES.get("profileImage")

        user.save()
        messages.success(request, "Profile updated successfully!")
        return redirect("profile")
    
    return render(request, "users/profile_edit.html", {"user":user})

@user_required
def change_password_request(request):
    user = request.user 

    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)

    reset_link = request.build_absolute_uri(
        f"/reset-password/{uid}/{token}/"
    )

    try:
        password_reset_email(reset_link , user.email)
        messages.success(request, f"password reset link sent to your email {user.email}")
        logger.info(f"Password reset link sent to {user.email}")
    except Exception as e:
        messages.error(request, "Something went wrong. Please try again later.")
        logger.error(f"Password reset failed for {user.email}: {e}")

    return redirect("profile_edit")


#address
@user_required
def address_list(request):
    addresses = Address.objects.filter(user=request.user)
    return render(request, 'users/address_list.html', {'addresses': addresses})

# @user_required
# def address_add_view(request):

#     next_page = request.GET.get('next', '')

#     if request.method == "POST":
#         form = AddressForm(request.POST)
#         if form.is_valid():
#             address = form.save(commit=False)
#             address.user = request.user
#             address.save()
#             messages.success(request, "Address saved successfully!")

#             if next_page == 'checkout':
#                 return redirect("checkout")
            
#             return redirect("address")
#     else:
#         form = AddressForm()
    
#     context = {
#         "form":form,
#         "next_page":next_page
#     }

#     return render(request, 'users/address_add.html', context) 

@user_required
def address_add_view(request):
    next_page = request.GET.get('next',"")

    if request.method == "POST":
        full_name = request.POST.get("full_name")
        phone_number = request.POST.get("phone_number")
        email = request.POST.get("email")
        address_type = request.POST.get("address_type") # Returns "HOME", "WORK", or "OTHER"
        address_line1 = request.POST.get("address_line1")
        address_line2 = request.POST.get("address_line2")
        city = request.POST.get("city")
        state = request.POST.get("state")
        pincode = request.POST.get("pincode")

        if not full_name or not phone_number or not address_line1 or not city or not state or not pincode:
            messages.error(request, "Please fill in all required fields.")

            context = {
                "values": request.POST, 
                "next_page": next_page
            }
            return render(request, 'users/address_add.html', context)
        
        Address.objects.create(
            user=request.user,
            full_name=full_name,
            phone_number=phone_number,
            email=email,
            address_type=address_type,
            address_line1=address_line1,
            address_line2=address_line2,
            city=city,
            state=state,
            pincode=pincode
        )

        messages.success(request, "Address saved successfully!")

        if next_page == 'checkout':
            return redirect("checkout")
        
        return redirect("address")
    
    return render(request, 'users/address_add.html', {"next_page": next_page})
    
@user_required
def address_edit_view(request, pk):
    address = get_object_or_404(Address, id=pk, user=request.user)

    if request.method == "POST":

        address.full_name = request.POST.get("full_name")
        address.phone_number = request.POST.get("phone_number")
        address.email = request.POST.get("email")
        address.address_type = request.POST.get("address_type")
        address.address_line1 = request.POST.get("address_line1")
        address.address_line2 = request.POST.get("address_line2")
        address.city = request.POST.get("city")
        address.state = request.POST.get("state")
        address.pincode = request.POST.get("pincode")
        address.save()

        messages.success(request, "Address updated successfully!")
        return redirect("address")
    
    return render(request,  "users/address_edit.html", {"address":address})

@user_required
def address_delete_view(request, pk):
    address = get_object_or_404(Address, id = pk , user = request.user)

    if request.method == "POST":
        address.delete()
        messages.success(request, "Address deleted successfully")
        return redirect("address")
    
    

#orders

@user_required
def order_list_view(request):
    filter_value = request.GET.get('filter', 'ALL')
    filter_options = [
        ('ALL','All'),
        ('Pending','Pending'),
        ('Processing','Processing'),
        ('Shipped', 'Shipped'),
        ('Delivered', 'Delivered'),
        ('Cancelled', 'Cancelled'),
    ]
    orders = Order.objects.filter(user = request.user).order_by("-created_at")


    search_query = request.GET.get("search")

    if search_query:
        orders = orders.filter(
            Q(order_id__icontains = search_query)|
            Q(items__variant__product__name__icontains = search_query)
        )

    if filter_value != "ALL":
        orders = orders.filter(status = filter_value).order_by("-created_at")

    paginator = Paginator(orders,4)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    context  = {

        'orders' : page_obj,
        'filter_options' : filter_options,
        'current_filter' : filter_value,
        "page_obj" : page_obj,
        "search_query" : search_query,
    }

    return render(request, 'users/order_list.html' , context)



#order list

# @user_required
# def order_detail_view(request, order_id):
#     order = get_object_or_404(Order, order_id=order_id, user=request.user)
#     order_items = order.items.all()
    
#     active_subtotal = Decimal("0.01")
#     for item in order_items:
#         if item.status not in ['Cancelled', 'Returned']:
#             active_subtotal += (item.price * item.quantity)

#     active_gst = (active_subtotal * Decimal('0.18')).quantize(Decimal('0.01'))
#     active_total = (active_subtotal + active_gst + order.delivery_charge - order.discount_amount).max(Decimal('0.00'))


#     today = timezone.now().date()
#     return_deadline = None

#     # Fetch all return requests for this order
#     return_requests = ReturnRequest.objects.filter(order_item__order=order)
    
#     # Create a lookup (map) by order_item.id
#     return_request_map = {ret.order_item_id: ret for ret in return_requests}

#     has_returnable_items = False

#     if order.status == "Delivered" and order.delivered_at:
#         return_deadline = order.delivered_at.date() + timedelta(days=10)

#         for item in order_items:
#             # Attach the return request to item if exists
#             item.return_request = return_request_map.get(item.id, None)

#             if item.return_request:
#                 item.return_status = item.return_request.status
#                 item.return_request_id = item.return_request.id
#                 item.is_return_eligible = False
#             else:
#                 item.return_status = None
#                 item.return_request_id = None
#                 item.is_return_eligible = item.is_return_eligible()

#         has_returnable_items = any(item.is_return_eligible for item in order_items)

#     context = {
#         "order": order,
#         "order_items": order_items,
#         "has_returnable_items": has_returnable_items,
#         "active_subtotal": active_subtotal,
#         "active_gst": active_gst,
#         "active_total": active_total,
#     }

#     return render(request, "users/order_detail.html", context)



@user_required
def order_detail_view(request, order_id):
    order = get_object_or_404(Order, order_id=order_id, user=request.user)
    order_items = order.items.all()

    # ✅ Active items = not cancelled or returned
    active_items = order_items.exclude(status__in=['Cancelled', 'Returned'])

    # ✅ If all items are cancelled/returned → show original order totals
    if not active_items.exists():
        refund_items = order_items.filter(status__in=['Cancelled', 'Returned'])

        active_subtotal = Decimal("0.00")
        for item in refund_items:
            active_subtotal += item.price * item.quantity

        active_subtotal = active_subtotal.quantize(Decimal("0.01"))
        active_gst = (active_subtotal * Decimal("0.18")).quantize(Decimal("0.01"))
        active_total = (active_subtotal + active_gst).quantize(Decimal("0.01"))

        is_fully_cancelled = True
    else:
        # ✅ Calculate only active items
        active_subtotal = Decimal("0.00")
        for item in active_items:
            active_subtotal += item.price * item.quantity

        # active_subtotal = active_subtotal.quantize(Decimal("0.01"))
        # active_gst = (active_subtotal * Decimal('0.18')).quantize(Decimal('0.01'))
        # active_total = (
        #     active_subtotal +
        #     active_gst +
        #     order.delivery_charge -
        #     order.discount_amount
        # ).max(Decimal('0.00'))
        active_subtotal = order.subtotal
        active_gst = order.gst
        active_total = order.total_amount

        is_fully_cancelled = False

    
    today = timezone.now().date()
    return_deadline = None

    return_requests = ReturnRequest.objects.filter(order_item__order=order)
    return_request_map = {ret.order_item_id: ret for ret in return_requests}

    has_returnable_items = False

    if order.status == "Delivered" and order.delivered_at:
        return_deadline = order.delivered_at.date() + timedelta(days=10)

        for item in order_items:
            item.return_request = return_request_map.get(item.id, None)

            if item.return_request:
                item.return_status = item.return_request.status
                item.return_request_id = item.return_request.id
                item.is_return_eligible = False
            else:
                item.return_status = None
                item.return_request_id = None
                item.is_return_eligible = item.is_return_eligible()

        has_returnable_items = any(item.is_return_eligible for item in order_items)

    context = {
        "order": order,
        "order_items": order_items,
        "has_returnable_items": has_returnable_items,
        "active_subtotal": active_subtotal,
        "active_gst": active_gst,
        "active_total": active_total,
        "is_fully_cancelled": is_fully_cancelled,
    }

    return render(request, "users/order_detail.html", context)



@user_required
def download_invoice(request, order_id):
    order = get_object_or_404(Order, order_id = order_id, user = request.user)
    pdf_buffer = generate_invoice(order)

    response  = HttpResponse(pdf_buffer, content_type='application/pdf')
    response['Content-Dispostion'] = f"attachment; filename='Invoice_{order.order_id}.pdf'"
    return response


@user_required
def return_request_detail(request, return_id):
    return_request = get_object_or_404(ReturnRequest, id=return_id, user=request.user)
    
    context = {
        'return_request': return_request,
    }
    return render(request, 'users/return_detail.html', context)


@user_required
def  send_email_change_link(request):
    if request.method == "POST":
        try:
            
            data = json.loads(request.body)
            new_email = clean_input(data.get("email"))
        except Exception:
            return JsonResponse({"success": False, "error": "Invalid request data."})

        if User.objects.filter(email=new_email).exists():
            return JsonResponse({"success": False, "error": "Email already in use."})
        
        user = request.user

        request.session["pending_email_change"] = new_email

        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)

        confirm_link = request.build_absolute_uri(
            f"/confirm-email-change/{uid}/{token}/"
        )

        try:
            email_change_confirmation(confirm_link, new_email)
            return JsonResponse({"success": True, "email": new_email})
        except Exception as e:
            return JsonResponse({"success": False, "error": "Failed to send email."})
        
    return JsonResponse({"success": False, "error": "Invalid request method."})



def confirm_email_change(request, uidb64, token):
    try:
        uid = urlsafe_base64_decode(uidb64).decode()
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None

    if user and default_token_generator.check_token(user, token):
        new_email = request.session.get("pending_email_change")

        if not new_email:
            messages.error(request, "Session expired. Try again.")
            return redirect("profile")
        
        user.email = new_email
        user.save()
        request.session.pop("pending_email_change", None)
        request.session.modified = True

        messages.success(request, "Email updated successfully!")
        return redirect("profile")
    else:
        messages.error(request, "Invalid or expired confirmation link.")
        return redirect("profile")
    



def aboutus_view(request):
    return render(request, "aboutus.html")

def privacy_policy(request):
    return render(request, "privacypolicy.html")

def terms_view(request):
    return render(request, "terms.html")

def social_placeholder_view(request):
    return render(request, "social_placeholder.html")