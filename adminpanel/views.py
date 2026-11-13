from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.contrib.auth import get_user_model
import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q,Avg,Count,Sum,Min,Max,F
from django.utils.crypto import get_random_string
from django.views.decorators.http import require_http_methods
from products.models import Product, ProductVariant, ProductImage, SubCategory, Offer
from products.forms import ProductForm, ProductVarientForm
from shop.models import  Order, ReturnRequest, OrderItem
from .decorator import admin_required
from django.utils.timezone import now
from decimal import Decimal
from django.utils.timezone import now
from django.db import transaction
from wallet.models import Wallet, WalletTransaction
from datetime import datetime, timedelta
from django.utils import timezone
from coupons.models import Coupon
from django.db.models.functions import TruncMonth, TruncDay
import json
from .utils import render_excel_view, render_pdf_view
from users.models import Banner
from wallet.utils import credit_wallet
from django.db.models import IntegerField
from django.db.models.functions import Cast
from django.http import JsonResponse
import json
from support.models import SupportTicket, SupportMessage
from support.views import get_ticket_safe

logger = logging.getLogger("users")
User = get_user_model()

# Create your views here.

def clean_input(value: str) -> str:
    if not value:
        return ""
    return value.strip()


def admin_login_view(request):

    if request.user.is_authenticated and  request.user.role == "admin":
        return redirect("admin_dashboard")
    
    if request.method == "POST":
        email = clean_input(request.POST.get("email"))
        password = clean_input(request.POST.get("password"))

        logger.info(f"Admin login attempt: {email}")

        if not email and password:
            messages.error(request, "Email and password are required")
            return redirect("admin_login")
        
        user = authenticate(email = email , password = password)

        if user is None:
            logger.warning(f"Admin login failed: invalid credentials {email}")
            messages.error(request, "Invalid email or password.")
            return redirect("admin_login")

        if user.role != "admin":
            logger.warning(f"Unauthorized access attempt bt {email}")
            messages.error(request, "Access denied. Admins only.")
            return  redirect("admin_login")
        
        login(request, user)
        logger.info(f"Admin logged in: {email}")
        messages.success(request, "Logged successfuly")
        return redirect("admin_dashboard")
    
    return render(request, "adminpanel/admin_login.html")



@admin_required
def admin_dashboard(request):

    product_count = Product.objects.count()
    user_count = User.objects.filter(role="user").count()
    total_orders = Order.objects.count()

    

    total_revenue_data = Order.objects.filter(status="Delivered").aggregate(Sum('total_amount'))
    total_revenue = total_revenue_data["total_amount__sum"] or 0

    recent_orders = Order.objects.select_related('user').order_by("-created_at")[:5]


    recent_customers = User.objects.filter(role="user").order_by("-date_joined")[:4]

    six_months_ago = timezone.now() - timedelta(days=180)
    monthly_sales = Order.objects.filter(status="Delivered", created_at__gte = six_months_ago).annotate(month=TruncMonth('created_at')).values('month').annotate(total=Sum('total_amount')).order_by('month')


    sales_data = []
    if monthly_sales:
        max_sales = max(item['total'] for item in monthly_sales)
        for entry in monthly_sales:
            sales_data.append({
                'month' : entry['month'].strftime("%b"),
                'total' : entry['total'],
                'height_percent' : (entry['total'] / max_sales) * 100
            })

    category_sales = (
        Order.objects.filter(status="Delivered")
        .values(cat_name=F("items__variant__product__category"))
        .annotate(total=Sum(F("items__price") * F("items__quantity")))
        .order_by("-total")[:3]
    )
    


    if category_sales:
        
        max_val = category_sales[0]['total'] or 1 

        for cat in category_sales:
           
            cat['percent'] = (cat['total'] / max_val) * 100
    
    #best selling product
    best_selling_products = (
        OrderItem.objects
        .values(
            name=F("variant__product__name"),
            color=F("variant__product__color")
                )
        .annotate(total_sold=Sum("quantity"))
        .order_by("-total_sold")[:10]
    )

    product_labels = [p["name"] for p in best_selling_products]
    product_values = [p["total_sold"] for p in best_selling_products]
    product_colors = [p["color"] or "#cccccc" for p in best_selling_products]

    #best selling category
    best_selling_category = (
        OrderItem.objects
        .values(category=F("variant__product__subcategory__name"))
        .annotate(total_sold=Sum("quantity"))
        .order_by("-total_sold")[:10]
    )

    category_labels = [c["category"] or "Uncategorized" for c in best_selling_category]
    category_values = [c["total_sold"] for c in best_selling_category]

    
 
    breadcrumbs = [
        {"label": "Dashboard", "url": "/adminpanel/dashboard/"},
    ]
    return render(request, "adminpanel/dashboard.html", {
        "breadcrumbs": breadcrumbs,
        "active_page": "dashboard",

        "product_count": product_count,
        "user_count": user_count,
        "total_orders": total_orders,
        "total_revenue": total_revenue,

        "recent_customers": recent_customers,
        "recent_orders": recent_orders,

        "sales_data": sales_data,
        "category_sales": category_sales,

        "product_labels": json.dumps(product_labels),
        "product_values": json.dumps(product_values),
        "product_colors": json.dumps(product_colors),

        "category_labels": json.dumps(category_labels),
        "category_values": json.dumps(category_values),

    })


@admin_required
def user_list(request):

    breadcrumbs = [
        {"label": "Dashboard", "url": "/adminpanel/dashboard/"},
        {"label": "Customers", "url":  "/adminpanel/users/"},

    ]



    search_query = request.GET.get("search", "").strip()
    role_filter = request.GET.get("role","").strip()
    status_filter = request.GET.get("status","").strip()
    joined_sort = request.GET.get("joined","").strip()

    users = User.objects.all().order_by("-date_joined")

    if search_query:
        users = users.filter(
            Q(fullName__icontains=search_query) |
            Q(email__icontains=search_query)
        )

    if role_filter == "user":
        users = users.filter(role="user")
    elif role_filter == "admin":
        users = users.filter(role="admin")

    if status_filter == "active":
        users = users.filter(is_active=True)      
    elif status_filter == "blocked":
        users = users.filter(is_active=False)

    if joined_sort == "newest":
        users = users.order_by("-createdAt")
    elif joined_sort == "oldest":
        users = users.order_by("createdAt")

    paginator = Paginator(users, 10)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    context = {
        "breadcrumbs" : breadcrumbs,
        "page_obj" : page_obj,
        "search_query":  search_query,
        "active_page": "customers", 

    }

    return render(request, "adminpanel/user_list.html", context)

@admin_required
@require_http_methods(["POST"])
def block_user(request, user_id):
    


    user = get_object_or_404(User, id=user_id)

    if request.user.id == user.id:
        messages.error(request, "You cannot block yourself.")
        return redirect("admin_user_list")
    
    if user.role == "admin":
        messages.error(request,  "You cannot block admin.")
        return redirect("admin_user_list")
    
    user.is_active = False
    user.save()

    messages.success(request, f"{user.fullName} has been blocked")
    return redirect("admin_user_list")


@admin_required
def unblock_user(request, user_id):
    user = get_object_or_404(User , id=user_id)

    user.is_active  = True
    user.save()

    messages.success(request, f"{user.fullName} has been unblocked")
    return redirect("admin_user_list")

@admin_required
def admin_add_user(request):
    if  request.method == "POST":
        fullName = clean_input(request.POST.get("fullName"))
        email = clean_input(request.POST.get("email"))
        phone = clean_input(request.POST.get("phoneNumber"))
        dob = clean_input(request.POST.get("dateOfBirth")) or None
        gender = clean_input(request.POST.get("gender"))
        role  = clean_input(request.POST.get("role"))
        profileImage = request.FILES.get("profileImage")

        if User.objects.filter(email=email).exists():
            messages.error(request, "Email already exists")
            return redirect("admin_user_list")
        
        password = get_random_string(12)

        user = User.objects.create(
            fullName=fullName,
            email=email,
            phoneNumber=phone,
            dateOfBirth=dob,
            gender=gender,
            role=role,
            is_active=True,
        )

        if profileImage:
            user.profileImage = profileImage

        user.set_password(password)
        user.save()

        messages.success(request, f"User '{fullName}' created successfully.")
        return  redirect("admin_user_list")
    
    return redirect("admin_user_list")

@admin_required
def admin_edit_user(request, user_id):

    user = get_object_or_404(User, id=user_id)

    if request.method == "POST":
        user.fullName = clean_input(request.POST.get("fullName"))
        user.email = clean_input(request.POST.get("email"))
        user.phoneNumber = clean_input(request.POST.get("phoneNumber"))
        dob = clean_input(request.POST.get("dateOfBirth")) 
        user.dateOfBirth = dob if dob else None
        user.gender = clean_input(request.POST.get("gender"))
        user.role = clean_input(request.POST.get("role"))

        if "profileImage" in  request.FILES:
            user.profileImage = request.FILES["profileImage"]

        if request.POST.get("removeImage") == "on":
            user.profileImage.delete(save=False)
            user.profileImage = None

        user.save()

        messages.success(request, "User updated successfully!")
        return redirect("admin_user_list")
    
    

@admin_required
def product_list_view(request):
    query = request.GET.get("q","")
    products = Product.objects.order_by("-created_at").prefetch_related("images")


    
    category_filter = request.GET.get("category", "")
    if category_filter:
        products = products.filter(category=category_filter)

    status_filter = request.GET.get("status", "")

    if status_filter == "listed":
        products = products.filter(is_active=True)

    elif status_filter == "unlisted":
        products = products.filter(is_active=False)


    if query:
        products = products.filter(
            Q(name__icontains=query) |
            Q(color__icontains=query) |
            Q(category__icontains=query)
        )


    sort_by = request.GET.get("sort","")

    if sort_by == "nameAsc":
        products = products.order_by("name")
    elif sort_by == "nameDesc":
        products = products.order_by("-name")
    elif sort_by == "priceLow":
        products = products.order_by("price")
    elif sort_by == "priceHigh":
        products = products.order_by("-price")
    elif sort_by == "stockLow":
        products = sorted(products, key=lambda p : p.total_stock())
    elif sort_by == "stockHigh":
        products = sorted(products, key=lambda p : p.total_stock(), reverse=True)
    elif sort_by == "newest":
        products = products.order_by("-created_at")
    elif sort_by == "oldest":
        products = products.order_by("created_at")
    else:
        products = products.order_by("-id")


    paginator = Paginator(products, 10)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    form = ProductForm()

    # context = {
    #     "page_obj" : page_obj,
    #     "query" : query,
    #     "breadcrumbs" : [
    #         {"name" : "Dashboard", "url": "admin_dashboard"},
    #         {"name" : "Product Management", "url": "admin_product_list"},

    #     ],
    #     "active_page": "products",
    #      "form": form, 
    # }
    context = {
        "page_obj": page_obj,
        "query": query,
        "breadcrumbs": [
            {"name": "Dashboard", "url": "admin_dashboard"},
            {"name": "Product Management", "url": "admin_product_list"},
        ],
        "active_page": "products",
        "form": form,
        "sort":sort_by,
        "category_filter":category_filter,
        "status_filter" : status_filter,
    }

    
    return  render(request, "adminpanel/products/product_list.html", context)
    

@admin_required
def product_add_view(request):
    if request.method == "POST":
        form = ProductForm(request.POST)
        images = request.FILES.getlist("images")

        if len(images) != 3:
            messages.error(request, f"You must upload exactly 3 images. (Current: {len(images)})")
            return redirect("admin_product_list")

        if form.is_valid():
            product = form.save()

            for index, image  in enumerate(images):
                ProductImage.objects.create(
                    product=product,
                    image=image,
                    is_primary=(index==0)
                )
            messages.success(request, "Product added successfully with images!")
            return redirect("admin_product_list")
        else:
            messages.error(request, "Please fix the errors below.")
            return redirect("admin_product_list")
 
    return redirect("admin_product_list")
    
@admin_required
def product_variant_list_view(request, product_id):
    product = get_object_or_404(Product, id=product_id)
    variants = (
        product.variants
        .filter(is_active=True)
        .annotate(size_int=Cast("size", IntegerField()))
        .order_by("size_int")
    )

    form = ProductVarientForm()

    
    context = {
        "product": product,
        "variants": variants,
        "form": form,
        "breadcrumbs": [
            {"name": "Dashboard", "url": "admin_dashboard"},
            {"name": "Products", "url": "admin_product_list"},
            {"name": f"Variants of {product.name}", "url": "admin_product_variants", "args": [product.id]},
        ],
    }



    return render(request, "adminpanel/products/product_variant_list.html", context)

@admin_required
def product_variant_add_view(request,  product_id):
    product = get_object_or_404(Product, id=product_id)
    if request.method  == "POST":
        form = ProductVarientForm(request.POST)
        if form.is_valid():
            variant = form.save(commit=False) #not saving to DB
            variant.product = product #adding the product id
            variant.save() #now saving to db
            messages.success(request, f"Variant '{variant.size}' added successfully!")
            return redirect("admin_product_variants", product_id = product.id)
        else:
            messages.error(request, "Please fix the errors below")
    return redirect("admin_product_variants", product_id=product.id)

@admin_required
def product_edit_view(request, product_id):
    product  = get_object_or_404(Product, id=product_id)
    existing_images = product.images.all()


    if request.method == "POST":
        form = ProductForm(request.POST, instance=product)
        new_images = request.FILES.getlist("images")  
        remove_image_ids = request.POST.getlist("remove_images") 

        current_count = existing_images.count()
        to_remove_count = len(remove_image_ids)
        to_add_count = len(new_images)

        final_count = current_count - to_remove_count + to_add_count
        if final_count != 3:
            messages.error(
                request, 
                f"A product must have exactly 3 images. "
                f"Current: {current_count}, Removing: {to_remove_count}, Adding: {to_add_count}. "
                f"Result would be {final_count}."
            )
    
            return render(request, "adminpanel/products/product_edit.html", {
                "form": form, "product": product, "existing_images": existing_images
            })
        
        if form.is_valid():
            form.save()
            
            for img_id in remove_image_ids:
                try:
                    img = ProductImage.objects.get(id=img_id, product=product) #image takes from perticular id
                    img.image.delete(save=False) # delete from media
                    img.delete() # delete url from db
                except ProductImage.DoesNotExist:
                    pass

            
            for index, img_file in enumerate(new_images):
                is_primary = not product.images.filter(is_primary=True).exists() and index == 0

                ProductImage.objects.create(
                    product=product,
                    image=img_file,
                    is_primary=is_primary
                )

            messages.success(request, f"Product '{product.name}' updated successfully!")
            return redirect("admin_product_list")
        else:
            messages.error(request, "Please fix the errors below.")
    else:
        form = ProductForm(instance=product)

    
    context = {
        "form": form,
        "product": product,
        "existing_images": existing_images,
        "breadcrumbs": [
            {"name": "Dashboard", "url": "admin_dashboard"},
            {"name": "Product Management", "url": "admin_product_list"},
            {"name": f"Edit {product.name}", "url": "admin_product_edit", "args": [product.id]},
        ],
        "active_page": "products",
    }


    return render(request, "adminpanel/products/product_edit.html",  context)


@admin_required
def product_toggle_view(request, product_id):
    product = get_object_or_404(Product, id=product_id)

    product.is_active = not product.is_active # toggle the is_active
    product.save()

    if product.is_active:
        messages.success(request, f"Product '{product.name}' is now listed.")
    else:
        messages.warning(request, f"Product '{product.name}' has been unlisted.")

    return redirect("admin_product_list")

@admin_required
def product_variant_edit_view(request, product_id, variant_id):
    product = get_object_or_404(Product, id=product_id)
    variant = get_object_or_404(ProductVariant, id=variant_id)

    if request.method == "POST":
        form = ProductVarientForm(request.POST, instance=variant)
        if form.is_valid():
            form.save()
            messages.success(request, f"Variant '{variant.size}' updated successfully!")
            return redirect("admin_product_variants", product_id=product.id)
        else:
            messages.error(request, "Please fix the errors below.")
    return redirect("admin_product_variants", product_id=product.id)

@admin_required
def product_variant_delete_view(request, product_id, variant_id):
    product = get_object_or_404(Product, id=product_id)
    variant = get_object_or_404(ProductVariant, id=variant_id, product=product)

    # Delete the variant
    variant.delete()
    messages.success(request, f"Variant (Size {variant.size}) deleted successfully!")

    return redirect("admin_product_variants", product_id=product.id)


@admin_required
def admin_logout_view(request):
    logout(request)
    messages.success(request, "Logged out successfully!")
    return redirect("admin_login")


@admin_required
def admin_category_list(request):
    subcategories = SubCategory.objects.all().order_by("category","name")

    context = {
        "subcategories" : subcategories,
        "active_page" : "categories",
        "breadcrumbs" :[
            {"label": "Dashboard", "url": "admin_dashboard"},
            {"label": "Category Management", "url": "admin_category_list"},
        ]
    }
    
    return render(request, "adminpanel/category/admin_category_list.html", context)

@admin_required
def admin_category_add(request):
    if request.method == "POST":
        name = request.POST.get("name").strip()
        category = request.POST.get("category")
        is_active = request.POST.get("is_active") == "on"

        if SubCategory.objects.filter(category=category,name__iexact=name).exists():
                messages.error(request, "Subcategory with this name already exists.")
                return redirect("admin_category_list")
        
        SubCategory.objects.create(
                name=name,
                category=category,
                is_active=is_active
            )
        
        messages.success(request, f"Subcategory '{name}' created successfully.")
        return redirect("admin_category_list")
    
    return redirect("admin_category_list")

@admin_required
def admin_category_edit(request , id):
    subcategory = get_object_or_404(SubCategory, id=id)

    if request.method == "POST":
        new_name = request.POST.get("name").strip()
        new_category = request.POST.get("category")
        is_active = request.POST.get("is_active") == "on"

        if SubCategory.objects.filter(category=new_category, name__iexact=new_name).exclude(id=id).exists():
            messages.error(request, f"Subcategory '{new_name}' already exists in this category.")
            return redirect("admin_category_edit", id=id)
        
        subcategory.name = new_name
        subcategory.category = new_category
        subcategory.is_active = is_active
        subcategory.save()

        messages.success(request, f"Subcategory '{subcategory.name}' updated successfully.")
        return redirect("admin_category_list")
    
    context = {
        "subcategory": subcategory,
        "active_page": "categories",
        "breadcrumbs": [
            {"label": "Dashboard", "url": "admin_dashboard"},
            {"label": "Category Management", "url": "admin_category_list"},
            {"label": "Edit Subcategory", "url": ""},
        ],
    }

    return render(request, "adminpanel/category/admin_category_edit.html", context)

@admin_required
def admin_category_delete(request, id):
    subcategory = get_object_or_404(SubCategory, id=id)

    subcategory.delete()
    messages.success(request, f"Subcategory '{subcategory.name}' deleted successfully.")

    return redirect("admin_category_list")


# order list
@admin_required
def admin_order_list(request):
    search_query = request.GET.get('search',"")
    status_filter = request.GET.get("status","")
    sort = request.GET.get('sort','')

    orders = Order.objects.all()

    if search_query:
        orders = orders.filter(
            Q(order_id__icontains=search_query)|
            Q(user__fullName__icontains=search_query)|
            Q(user__email__icontains = search_query)
        )


    if status_filter:
        orders = orders.filter(status = status_filter)

    if sort == 'date_desc':
        orders = orders.order_by('-created_at')
    elif sort == 'date_asc':
        orders = orders.order_by('created_at')
    elif sort ==  'amount_desc':
        orders = orders.order_by('-total_amount')
    elif sort == 'amount_asc':
        orders = orders.order_by('total_amount')
    else:
        orders = orders.order_by('-created_at')

    paginator = Paginator(orders, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'page_obj' : page_obj,
        'search_query' : search_query,
        'status_filter' : status_filter,
        'sort' : sort,
        'status_choices' : Order.STATUS_CHOICES,
        'active_page' : "orders",
    }

    return render(request, 'adminpanel/order_list.html', context)

#order detail
@admin_required
def admin_order_detail(request, order_id):
    order = get_object_or_404(Order, order_id = order_id)

    return render(request, 'adminpanel/order_detail.html', {"order":order})

@admin_required
def update_order_status(request, order_id):
    order = get_object_or_404(Order, order_id=order_id)

    if request.method == "POST":
        new_status = request.POST.get('status')

        if new_status == order.status:
                messages.info(request, "No changes made.")
                return redirect('admin_order_detail', order_id=order.order_id)
        try:
            with transaction.atomic():

                if new_status == 'Cancelled' and order.status != 'Cancelled':
                    for item in order.items.all():
                        if item.status != 'Cancelled':
                            item.status = 'Cancelled'
                            item.variant.stock += item.quantity
                            item.variant.save()
                            item.save()

                    order.cancel_reason = "Cancelled by Admin"


                else:
                    for item in order.items.all():
                        if item.status not in ['Cancelled','Returned']:
                            item.status = new_status
                            item.save()

    
                order.status = new_status

                if new_status == "Delivered":
                    order.delivered_at = now()

                elif new_status != "Delivered":
                    order.delivered_at = None

                order.save()

        
                messages.success(request, f"Order status updated to {new_status}.")

        except Exception as e:
            messages.error(request,f"An error occurred: {str(e)}" )

    return redirect('admin_order_detail', order_id=order.order_id)

@admin_required
def admin_cancel_order_item(request, item_id):
    item = get_object_or_404(OrderItem, id=item_id)
    order = item.order

    if order.status in ["Delivered", "Cancelled"]:
        messages.error(request, "Cannot cancel items for Delivered or Cancelled orders.")
        return redirect('admin_order_detail', order_id=order.order_id)
    
    if item.status == 'Cancelled':
        messages.warning(request, "Item is already cancelled.")
        return redirect('admin_order_detail', order_id=order.order_id)

    item_gst = (item.total_price * Decimal('0.18')).quantize(Decimal('0.01'))
    refund_amount = item.total_price + item_gst
    
    item.status = "Cancelled"
    item.variant.stock += item.quantity
    item.variant.save()
    item.save()

    if order.payment_method.upper() != 'COD':
        wallet, _ = Wallet.objects.get_or_create(user=order.user)
        wallet.balance += refund_amount
        wallet.save()
        credit_wallet(wallet, refund_amount, f"Refund for cancelled item: {item.variant.product.name}")
        messages.success(request, f"Refund of ₹{refund_amount} credited to wallet.")
    else:
        messages.info(request, "Item cancelled. No refund processed for COD order.")

    active_items = order.items.exclude(status='Cancelled')

    if not active_items.exists():
        # If no items left, cancel the whole order
        order.status = "Cancelled"
        order.cancel_reason = "All items cancelled by Admin"

        if order.payment_method.upper() != 'COD' and order.delivery_charge > 0:
            wallet.balance += order.delivery_charge
            wallet.save()
            credit_wallet(wallet, order.delivery_charge, "Refund for Delivery Charges (Order Cancelled)")
        
    else:
        new_subtotal = sum(i.total_price for i in active_items)
        order.subtotal = new_subtotal
        order.gst = (new_subtotal * Decimal('0.18')).quantize(Decimal('0.01'))
        
        if order.subtotal >= 1000:
            order.delivery_charge = Decimal(0)
        else:
            order.delivery_charge = Decimal(100)
            
        order.total_amount = order.subtotal + order.gst + order.delivery_charge

    order.save()
    messages.success(request, f"Item '{item.variant.product.name}' cancelled.")
    return redirect('admin_order_detail', order_id=order.order_id)

#return 
@admin_required
def admin_return_list(request):
    returns = ReturnRequest.objects.select_related(
        'order_item__order',
        'order_item__variant__product',
        'user'
    )

    search_query = request.GET.get('search', '').strip()
    if search_query:
        returns = returns.filter(
            Q(order_item__order__order_id__icontains=search_query) |
            Q(user__email__icontains=search_query) |
            Q(order_item__variant__product__name__icontains=search_query)
        )

    status_filter = request.GET.get('status')
    if status_filter:
        returns = returns.filter(status=status_filter)

    sort = request.GET.get('sort', '')
    if sort == 'newest':
        returns = returns.order_by('-requested_at')
    elif sort == 'oldest':
        returns = returns.order_by('requested_at')
    elif sort == 'amount_desc':
        returns = returns.order_by('-refund_amount')
    elif sort == 'amount_asc':
        returns = returns.order_by('refund_amount')

    paginator = Paginator(returns, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'page_obj': page_obj,
        'search_query': search_query,
        'status_filter': status_filter,
        'sort': sort,
        'return_status_choices': ReturnRequest.RETURN_STATUS_CHOICES,
        'active_page': 'returns',
    }

    return render(request, 'adminpanel/returns_list.html', context)


@admin_required
def admin_return_detail(request, return_id):
    return_request = get_object_or_404(
        ReturnRequest.objects.select_related(
            "order_item__order",
            "order_item__variant__product",
            "user",
            "pickup_address"
        ),
        id=return_id
    )

    if request.method == "POST":
        new_status = request.POST.get("status")
        admin_comments = request.POST.get("comments", "")
        refund_mode = request.POST.get("refund_mode", "")
        pickup_date = request.POST.get("pickup_date", "")

        try:
            with transaction.atomic():

                previous_status = return_request.status

                return_request.status = new_status

                if pickup_date:
                    return_request.pickup_date = pickup_date

                if admin_comments:
                    return_request.comments = admin_comments

                if refund_mode:
                    return_request.refund_mode = refund_mode

                if not return_request.refund_amount:
                    return_request.refund_amount = return_request.calculate_refund_amount()

                if new_status == "REFUNDED" and previous_status != "REFUNDED":

                    # Recalculate pending requests created before the refund fix too.
                    return_request.refund_amount = return_request.calculate_refund_amount()

                    wallet,_ = Wallet.objects.get_or_create(user=return_request.user)

                    balance_before = wallet.balance
                    wallet.balance += return_request.refund_amount
                    wallet.save()

                    balance_after = wallet.balance

                    WalletTransaction.objects.create(
                        wallet=wallet,
                        amount=return_request.refund_amount,
                        transaction_type = "credit",
                        description=f"Refund for Return #{return_request.id} (Item:{return_request.order_item.variant.product.name})",
                        balance_before = balance_before,
                        balance_after = balance_after,
                    )

                    order_item = return_request.order_item
                    order_item.status = 'Returned'
                    order_item.save()

                    order = order_item.order
                    total_items = order.items.count()
                    processed_items = order.items.filter(status__in=['Returned', 'Cancelled']).count()

                    if total_items == processed_items:
                        order.status = "Refunded"
                        order.save()

                    messages.success(request, f"Refund of ₹{return_request.refund_amount} credited to user's wallet.")

                return_request.save()

                messages.success(request, f"Return status updated to {new_status}.")
        except Exception as e:
            messages.error(request, f"Error updating return: {str(e)}")
            return redirect("admin_return_detail", return_id = return_id)
        
        return redirect("admin_return_detail", return_id=return_id)
    
    context = {
        "return_request": return_request,
        "status_choices": ReturnRequest.RETURN_STATUS_CHOICES,
        "active_page": "returns",
    }


    return render(request, 'adminpanel/return_detail.html', context)


#offers (Product and Category)

@admin_required
def offer_list_view(request):
    offers = Offer.objects.all().order_by("-created_at")
    products = Product.objects.filter(is_active=True)
    subcategories_data = list(SubCategory.objects.filter(is_active=True).values('id', 'name', 'category'))
    main_categories =  Product.CATEGORY_CHOICE

    search_query = request.GET.get("search")

    if search_query:
        offers = offers.filter(title__icontains = search_query)

    context = {
        "offers" : offers,
        "products" :  products,
        "subcategories" : subcategories_data,
        "active_page": "offers",
        "main_categories": main_categories,

    }

    return render(request, "adminpanel/offer_list.html", context)


@admin_required
def admin_offer_add(request):
    if request.method == "POST":
        title = request.POST.get("title")
        offer_type = request.POST.get("offer_type")
        discount = request.POST.get("discount")

        start_date_str = request.POST.get("start_date")
        end_date_str = request.POST.get("end_date")

        if not start_date_str or not end_date_str:
            messages.error(request, "Start and End date are required")
            return redirect("admin_offers")
        
        try:
            start_date_naive = datetime.strptime(start_date_str, "%Y-%m-%d %H:%M")
            end_date_naive = datetime.strptime(end_date_str, "%Y-%m-%d %H:%M")

            start_date = timezone.make_aware(start_date_naive)
            end_date = timezone.make_aware(end_date_naive)
        except ValueError:
            messages.error(request, "Invalid date format")
            return redirect("admin_offers")
        
        if offer_type == "product":
            product_ids  = request.POST.getlist("product_id")


            if not product_ids:
                messages.error(request, "Please select atleast one product")
                return redirect("admin_offers")
            
    
            offer = Offer.objects.create(
                title=title,
                offer_type="product",
                discount_percent = discount,
                start_date = start_date,
                end_date = end_date,
            )

            offer.products.set(product_ids)
                
            messages.success(request, f"Successfully created offers for {len(product_ids)} products.")
            return redirect("admin_offers")
           
        
        elif offer_type == "category":
            subcategory_ids = request.POST.getlist("subcategory")

            if not subcategory_ids:
                messages.error(request, "Please select at least one subcategory")
                return redirect("admin_offers")
            
           

            offer = Offer.objects.create(
                    title =  title,
                    offer_type = "category",
                    discount_percent = discount,
                    start_date = start_date,
                    end_date = end_date,
                )

                
            offer.subcategories.set(subcategory_ids)
            messages.success(request,  f"Successfully created offers for {len(subcategory_ids)} subcategories")
            return redirect("admin_offers")
            
        
    return redirect("admin_offers")

@admin_required
def admin_offer_edit(request, offer_id):
    offer = get_object_or_404(Offer, id=offer_id)

    products = Product.objects.filter(is_active = True)
    subcategories = SubCategory.objects.all()
    main_categories = Product.CATEGORY_CHOICE

    if request.method == "POST":
        offer.title = request.POST.get("title")
        offer.discount_percent = request.POST.get("discount")
        

        start = request.POST.get("start_date")
        end = request.POST.get("end_date")

        offer.start_date = timezone.make_aware(datetime.strptime(start, "%Y-%m-%d %H:%M"))
        offer.end_date = timezone.make_aware(datetime.strptime(end, "%Y-%m-%d %H:%M"))

        if  offer.offer_type  == "product":
            product_ids = request.POST.getlist("product_id")
            offer.products.set(product_ids)

        if offer.offer_type == "category":
            sub_ids = request.POST.getlist("subcategory")
            offer.subcategories.set(sub_ids)

        offer.save()
        messages.success(request, "Offer updated successfully")
        return redirect("admin_offers")
    
    context = {
        "offer" :  offer,
        "products" : products,
        "subcategories" : subcategories,
        "main_categories" : main_categories,
    }

    return redirect("admin_offers")

@admin_required
def admin_offer_toggle(request, offer_id):
    offer  = get_object_or_404(Offer, id=offer_id)

    offer.is_active = not offer.is_active
    offer.save()

    messages.success(request, f"Offer {offer.title} is now {'Active' if offer.is_active else "Inactive"}")

    return redirect("admin_offers")


@admin_required
def admin_offer_delete(request, offer_id):
    offer = get_object_or_404(Offer, id=offer_id)

    title = offer.title
    offer.delete()

    messages.success(request, f"Offer '{title}' deleted successfully")
    return redirect("admin_offers")


#coupons

@admin_required
def coupon_list_view(request):
    coupons = Coupon.objects.select_related('category').order_by('-created_at')
    search_query = request.GET.get("search")

    if search_query:
        coupons = Coupon.objects.filter(Q(name__icontains = search_query)|Q(code__icontains = search_query))

    context = {
        "coupons" : coupons,
        "subcategories"  : SubCategory.objects.all(),
        "active_page" : "coupons"
    }

    return render(request, 'adminpanel/coupons_list.html', context)

@admin_required
def coupon_add_view(request):
    if request.method != "POST":
        return redirect("admin_coupon_list")

    name = request.POST.get("name")
    code = request.POST.get("code", "").upper()
    discountType = request.POST.get("discountType")
    discountValue = request.POST.get("discountValue")
    minCartValue = request.POST.get("minCartValue") or 0
    userLimit = request.POST.get("userLimit") or 1
    category_id = request.POST.get("category") or None
    validFrom = request.POST.get("validFrom", "").strip()
    validTill = request.POST.get("validTill", "").strip()

  
    if Coupon.objects.filter(code__iexact=code).exists():
        messages.error(request, "This coupon code already exists.")
        return redirect("admin_coupon_list")
    


   
    try:
        dt_from = datetime.strptime(validFrom, "%Y-%m-%d")
        dt_till = datetime.strptime(validTill, "%Y-%m-%d")

        dt_till = dt_till.replace(hour=23, minute=59, second=59)

        start_dt = timezone.make_aware(dt_from)
        end_dt = timezone.make_aware(dt_till)


        if start_dt > end_dt:
            messages.error(request, "Valid From must be before Valid Till.")
            return redirect("admin_coupon_list")

    except ValueError:
        messages.error(request, "Invalid date format.")
        return redirect("admin_coupon_list")

 
    coupon = Coupon(
        name=name,
        code=code,
        discountType=discountType,
        discountValue=discountValue,
        minCartValue=minCartValue,
        userLimit=userLimit,
        validFrom=start_dt,
        validTill=end_dt,
    )


    if category_id:
        try:
            coupon.category = SubCategory.objects.get(id=category_id)
        except SubCategory.DoesNotExist:
            messages.error(request, "Selected category does not exist.")
            return redirect("admin_coupon_list")
    else:
        coupon.category = None

    coupon.save()

    messages.success(request, "Coupon created successfully.")
    return redirect("admin_coupon_list")

        


@admin_required
def coupon_toggle_view(request, coupon_id):
    coupon = Coupon.objects.get(id = coupon_id)

    coupon.isActive = not coupon.isActive
    coupon.save()

    if coupon.isActive:
        messages.success(request, f"Coupon {coupon.name} - {coupon.code} is now Active")
    else:
        messages.info(request, f"Coupon {coupon.name} - {coupon.code} has been Disabled")

    return redirect("admin_coupon_list")


@admin_required
def coupon_edit_view(request, coupon_id):
    coupon = get_object_or_404(Coupon, id=coupon_id)

    if request.method != "POST":
        return redirect("admin_coupon_list")

    name = request.POST.get("name")
    code = request.POST.get("code", "").upper()
    discountType = request.POST.get("discountType")
    discountValue = request.POST.get("discountValue")
    minCartValue = request.POST.get("minCartValue") or 0
    userLimit = request.POST.get("userLimit") or 1
    category_id = request.POST.get("category") or None
    validFrom = request.POST.get("validFrom", "").strip()
    validTill = request.POST.get("validTill", "").strip()

    # Unique code except this coupon
    if Coupon.objects.exclude(id=coupon.id).filter(code__iexact=code).exists():
        messages.error(request, "Another coupon with this code already exists.")
        return redirect("admin_coupon_list")

    

    try:
        dt_from = datetime.strptime(validFrom, "%Y-%m-%d") 
        dt_till = datetime.strptime(validTill, "%Y-%m-%d")

        dt_till = dt_till.replace(hour=23, minute=59, second=59)

        start_dt = timezone.make_aware(dt_from)
        end_dt = timezone.make_aware(dt_till)

        if start_dt > end_dt:
            messages.error(request, "Valid From must be before Valid Till.")
            return redirect("admin_coupon_list")

    except ValueError:
        messages.error(request, "Invalid date format.")
        return redirect("admin_coupon_list")

    # Update model
    coupon.name = name
    coupon.code = code
    coupon.discountType = discountType
    coupon.discountValue = discountValue
    coupon.minCartValue = minCartValue
    coupon.userLimit = userLimit
    coupon.validFrom = start_dt
    coupon.validTill = end_dt

    # Category
    if category_id:
        try:
            coupon.category = SubCategory.objects.get(id=category_id)
        except SubCategory.DoesNotExist:
            messages.error(request, "Selected category does not exist.")
            return redirect("admin_coupon_list")
    else:
        coupon.category = None

    coupon.save()

    messages.success(request, "Coupon updated successfully")
    return redirect("admin_coupon_list")


@admin_required
def coupon_delete_view(request, coupon_id):
    coupon = get_object_or_404(Coupon, id = coupon_id)
    
    try:
        coupon.delete()
        messages.success(request, f"Coupon '{coupon.code}' has been deleted successfully")
    except  Exception:
        messages.error(request, "Failed to delete the coupon. Try again later")

    return redirect("admin_coupon_list")

    
     


#analytics
@admin_required
def analytics_view(request):

    #filtering
    end_date_str = request.GET.get("end_date", timezone.now().strftime('%Y-%m-%d'))
    start_date_str = request.GET.get("start_date", (timezone.now() - timedelta(days=30)).date().strftime('%Y-%m-%d'))

    try:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
    except ValueError:
        start_date =(timezone.now() - timedelta(days=30)).date()
        end_date = timezone.now().date()

    orders = Order.objects.filter(
        status = "Delivered",
        created_at__date__range=[start_date,end_date]
    )

    #KPI Cards
    summary = orders.aggregate(
        total_revenue = Sum('total_amount'),
        orders_count=Count('id'),
        avg_order_value = Avg('total_amount'),
        total_discount = Sum('discount_amount')
    )

    total_products_sold = OrderItem.objects.filter(order__in=orders).aggregate(total_qty=Sum('quantity'))['total_qty'] or 0

    kpi_data = {
        'revenue' : summary['total_revenue'] or 0,
        'orders'  : summary['orders_count'] or 0,
        'avg_value' : round(summary['avg_order_value'] or 0, 2),
        'discount' : summary['total_discount'],
        'products_sold': total_products_sold,

    }
    #chart  1 : sales
    days_diff = (end_date - start_date).days

    if days_diff > 60:
        sales_data = orders.annotate(period=TruncMonth("created_at")).values('period').annotate(
            revenue = Sum('total_amount')
        ).order_by('period')
        date_format = '%b %Y'
    else:
        sales_data = orders.annotate(period=TruncDay("created_at")).values('period').annotate(
            revenue = Sum('total_amount')
        ).order_by('period')
        date_format = '%d %b'


    chart_dates = [item['period'].strftime(date_format) for item in sales_data]
    chart_revenues =  [float(item["revenue"]) for  item in sales_data]

    #chart 2 : category

    category_data = OrderItem.objects.filter(order__in = orders).values(
        'variant__product__subcategory__name'
    ).annotate(
        revenue = Sum(F('price') *  F('quantity'))
    ).order_by('-revenue')

    cat_labels = [item['variant__product__subcategory__name'] for item in category_data]
    cat_values = [float(item['revenue']) for item in  category_data]

    #tables  

    top_products_query = OrderItem.objects.filter(order__in=orders).values(
        'variant__product__id',
        'variant__product__name',
        'variant__product__category',
    ).annotate(
        total_sold = Sum('quantity'),
        total_revenue  = Sum(F('price') * F('quantity'))
    ).order_by("-total_revenue")[:5]


    products_table = []
    for item in top_products_query:

        product = Product.objects.get(id=item['variant__product__id'])

        img_obj = product.images.filter(is_primary=True).first()
        if not img_obj:
            img_obj = product.images.first()

        image_url = img_obj.image.url if img_obj else None

        products_table.append(
            {
                'name' : item['variant__product__name'],
                'category': item['variant__product__category'],
                'image' : image_url,
                'price' : product.price,
                'sold' : item['total_sold'],
                'revenue' : item['total_revenue'],
                'stock' : product.total_stock()
            }
        )

    context = {
        'kpi' : kpi_data,
        'chart_dates' : json.dumps(chart_dates),
        'chart_revenues' : json.dumps(chart_revenues),
        'cat_labels' : json.dumps(cat_labels),
        'cat_values' : json.dumps(cat_values),
        'products_table' : products_table,
        'start_date' : start_date_str,
        'end_date' : end_date_str,
        'active_page' : "analytics"
    }

    return render(request, 'adminpanel/analytics.html', context)

@admin_required
def product_performance_report(request):
    end_date_str = request.GET.get("end_date", timezone.now().strftime('%Y-%m-%d'))
    start_date_str = request.GET.get("start_date", (timezone.now() - timedelta(days=30)).date().strftime('%Y-%m-%d'))

    try:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
    except ValueError:
        start_date = (timezone.now()-timedelta(days=30)).date()
        end_date = timezone.now().date()


    orders = Order.objects.filter(
        status = "Delivered",
        created_at__date__range=[start_date,end_date]
    )

    products_query = OrderItem.objects.filter(order__in=orders).values(
        'variant__product__id',
        'variant__product__name',
        'variant__product__category',
    ).annotate(
        total_sold=Sum('quantity'),
        total_revenue=Sum(F('price') * F('quantity'))
    ).order_by("-total_revenue")


    paginator = Paginator(products_query, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    products_table = []
    for item in page_obj:
        product = Product.objects.get(id=item['variant__product__id'])
        
        img_obj = product.images.filter(is_primary=True).first()
        if not img_obj:
            img_obj = product.images.first()
        image_url = img_obj.image.url if img_obj else None

        products_table.append({
            'name': item['variant__product__name'],
            'category': item['variant__product__category'],
            'image': image_url,
            'price': product.price,
            'sold': item['total_sold'],
            'revenue': item['total_revenue'],
            'stock': product.total_stock()
        })

    context = {
    'products_table': products_table,
    'page_obj': page_obj, 
    'start_date': start_date_str,
    'end_date': end_date_str,
    "active_page" : "analytics"
    }

    return render(request, 'adminpanel/product_report.html', context)

@admin_required
def sales_report_view(request):
    report_type = request.GET.get('report_type', 'custom')
    start_date = request.GET.get('start_date')
    end_date =  request.GET.get('end_date')
    status_filter = request.GET.get('status','')
    download_format = request.GET.get('download')

    today = timezone.now().date()

    if not start_date or not end_date:
        start_date = today - timedelta(days=30)
        end_date = today
    else:
        if isinstance(start_date, str):
            start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
        if isinstance(end_date, str):
            end_date = datetime.strptime(end_date, '%Y-%m-%d').date()


    orders = Order.objects.all().order_by("-created_at")

    if report_type == "daily":
        orders = orders.filter(created_at__date =  today)
    elif report_type == "weekly":
        orders = orders.filter(created_at__date__gte = today - timedelta(days=7))
    elif report_type == "monthly":
        orders = orders.filter(created_at__date__gte = today - timedelta(days=30))
    elif  report_type == "yearly":
        orders = orders.filter(created_at__date__gte = today - timedelta(days=365))
    else:
        orders = orders.filter(created_at__date__range=[start_date, end_date])

    
    if status_filter:
        orders = orders.filter(status=status_filter)


    total_revenue_query = OrderItem.objects.filter(
        order__in=orders,
        status="Delivered"
    ).aggregate(
        net_revenue=Sum(F('price') * F('quantity'))
    )

    net_subtotal = total_revenue_query['net_revenue'] or Decimal('0.00')
    total_revenue = (net_subtotal * Decimal('1.18')).quantize(Decimal('0.01'))


    totals = orders.aggregate(
        total_count = Count("id"),
        total_value = Sum('total_amount'),
        total_discount=Sum('discount_amount')
    )

    context = {
        'orders' : orders,
        'total_count' : totals['total_count'] or 0,
        'total_value' : totals['total_value'] or 0,
        'total_revenue' : total_revenue,
        'total_discount' : totals['total_discount'] or 0,

        'report_type' : report_type,
        'start_date' : start_date if isinstance(start_date, str) else start_date.strftime('%Y-%m-%d'),
        'end_date' : end_date if  isinstance(end_date, str) else end_date.strftime('%Y-%m-%d'),
        'status_filter' : status_filter,
        "active_page" : "analytics",
    }

    if download_format == 'pdf':
        return render_pdf_view('adminpanel/pdf/sales_report_pdf.html', context)
    
    if download_format == 'excel':
        return render_excel_view(orders)

    return render(request, 'adminpanel/sales_report.html', context)


#banner management

@admin_required
def admin_banner_add(request):
    if request.method == "POST":

        image = request.FILES.get("image")
        subtitle = request.POST.get('subtitle')
        title = request.POST.get("title")
        button_text = request.POST.get("button_text")
        button_link = request.POST.get("button_link")
        order = request.POST.get("order")

        is_active  = request.POST.get("is_active") == 'on'

        if not image or not title:
            messages.error(request, "Image and Main Title are required")
            return render(request, 'admin/banner_add.html')
        
        try:
            Banner.objects.create(
                image=image,
                subtitle=subtitle,
                title=title,
                button_text = button_text,
                button_link = button_link,
                order = int(order) if order else 0,
                is_active = is_active
            )
            messages.success(request, "Banner added Successfully")
            return redirect('admin_banner_manager')
        except Exception as e:
            messages.error(request, f"Error : {e}")

    return render(request, 'adminpanel/banner_add.html')


@admin_required
def admin_banner_manager(request):

    if request.method == "POST":
        action = request.POST.get("action")
        banner_id = request.POST.get("banner_id")

        if banner_id:
            banner = get_object_or_404(Banner, id=banner_id)

            if action == "toggle_status":
                banner.is_active = not banner.is_active
                banner.save()
                status_msg = "Activated" if banner.is_active else "Decativated"
                messages.success(request, f"Banner {banner.title} is now {status_msg}")

            elif action == "delete":
                banner.delete()
                messages.warning(request, "Banner deleted permanently")

            return redirect('admin_banner_manager')
        
    banners = Banner.objects.all().order_by('order')

    context = {
        "banners"  :  banners,
        "title" : "banner Manager",
        "site_header" : "Shoeverse Admin"
    }

    return render(request, 'adminpanel/banner_manager.html', context)


