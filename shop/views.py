from django.shortcuts import render,  get_object_or_404, redirect
from products.models import Product, SubCategory, ProductReview
from django.core.paginator import Paginator
from django.db.models import Count, Q, Avg
from django.views.decorators.csrf import csrf_exempt
from products.models import Product, ProductVariant
from django.contrib import messages
from .models import CartItem, Wishlist, Address
from decimal import Decimal
from django.views.decorators.http import require_POST
from django.http import JsonResponse
import json
from .models import Order, OrderItem, ReturnRequest
from django.utils import timezone

from datetime import timedelta
from django.utils.timezone import now
from users.decorator import user_required
from wallet.models import Wallet, WalletTransaction
import razorpay
from django.conf import settings
from payments.models import Payment
from payments.utils import verify_captured_payment
from django.db import transaction
from coupons.models import Coupon, CouponUsage
from django.db.models import IntegerField
from django.db.models.functions import Cast
import logging
from .utils import get_cart_totals

logger = logging.getLogger(__name__)

# Create your views here.




def product_list_view(request, category=None):

    logger.info("Product list view called with category: %s", category)

    products = (
        Product.objects.filter(is_active=True)
        .prefetch_related("images")
        .annotate(
            in_stock_count=Count('variants', filter=Q(variants__stock__gt=0)),
            avg_rating=Avg('reviews__rating'),  
            review_count=Count('reviews')       
        )
    )
    logger.debug("Initial product count: %s", products.count())

    category_param = request.GET.get("category")

    if category_param:
        category = category_param.lower()

    if category:
        normalized_category = category.upper()   # men -> MEN
        if normalized_category in ["MEN", "WOMEN", "KIDS"]:
            products = products.filter(category=normalized_category)

    active_category = category  # keep lowercase for UI

    

    


    q = request.GET.get("q","").strip()
    if q:
        products = products.filter(name__icontains=q)

    #size
    active_size = request.GET.getlist("size")
    if active_size:
        products  = products.filter(variants__size__in=active_size).distinct()


    #price
    min_price = request.GET.get("min_price","")
    max_price = request.GET.get("max_price","")

    if min_price:
        products = products.filter(price__gte=min_price)

    if max_price:
        products = products.filter(price__lte=max_price)

    #sort
    sort = request.GET.get("sort","")
    if sort == "priceLow":
        products = products.order_by("price")
    elif sort == "priceHigh":
        products = products.order_by("-price")
    elif  sort == "new":
        products = products.order_by("-created_at")
    elif sort == "nameAsc":
        products = products.order_by("name")
    elif sort == "nameDesc":
        products = products.order_by("-name")
    else:
        products = products.order_by("-id")

    #color
    active_color = request.GET.get("color", "").strip()

    if active_color:
        products = products.filter(color__icontains=active_color)

    #subcategory 
    active_subcategory = request.GET.get("subcategory","")

    if active_subcategory:
        products = products.filter(subcategory_id= active_subcategory)

    if category:
        subcategories = SubCategory.objects.filter(
            category = category.upper(),
            is_active = True
        )
    else:
        subcategories = SubCategory.objects.none()

    filters = request.GET.copy()
    if 'page' in filters:
        filters.pop('page')
    querystring = filters.urlencode()

    paginator = Paginator(products, 6)
    page_number  = request.GET.get("page")
    page_obj  = paginator.get_page(page_number)

    categories = ["men", "women", "kids"]

    # Dynamic sizes based on category
    if category == "kids":
        sizes = ["1", "2", "3", "4", "5"]
    else:  # (default)
        sizes = ["6", "7", "8", "9", "10", "11"]


    wishlist_ids = []
    if request.user.is_authenticated:
        wishlist_ids = request.user.wishlist.values_list('product_id', flat=True)


    

    context = {
        "products" : page_obj,
        "category" : category,
        "page_obj" : page_obj,
        "paginator" : paginator,
        "q":q,
        "sort": sort,
        "active_color" :  active_color,
        "min_price" : min_price,
        "max_price" : max_price,
        "active_category" : active_category,
        "active_sizes" : active_size,
        "sizes":sizes,
        "categories":categories,
        "page_query" : querystring,
        "subcategories":subcategories,
        "active_subcategory":active_subcategory,
        "wishlist_ids": wishlist_ids,
    }

    return render(request, "shop/product_list.html", context)
    


def  product_detail_view(request, product_id):

    logger.info("Product detail view called for product_id=%s", product_id)

    #get the product
    product = get_object_or_404(
        Product.objects.prefetch_related("images","variants"),
        id=product_id
    )
    logger.debug("Loaded product: %s", product.name)

    # if not product.is_active:
    #     logger.warning("Inactive product (ID=%s) accessed", product_id)
    #     return redirect("shop_products")
    
    #fetching all the product variantes
    variants = (
        product.variants
        .filter(is_active=True)
        .annotate(size_int=Cast("size", IntegerField()))
        .order_by("size_int")
    )

    #fetch images
    images = product.images.all().order_by("-is_primary","id")

    #related_products
    related_products = Product.objects.filter(
        category=product.category,
        is_active = True,
    ).exclude(id=product.id)[:6]

    #stock check
    total_stock = sum(v.stock for v in variants)
    is_out_of_stock = total_stock == 0
    logger.debug("Stock check for product_id=%s | Total stock=%s", product_id, total_stock)

    #cart
    if request.GET.get("action") == "add_to_cart":
        logger.info("Add-to-cart request for product_id=%s", product_id)
        if is_out_of_stock:
            logger.warning("Attempt to add out-of-stock product_id=%s to cart", product_id)
            return redirect("shop_products")
        ##cart logics later##

    is_in_wishlist = False
    if request.user.is_authenticated:
        is_in_wishlist = Wishlist.objects.filter(user=request.user, product=product).exists()
        logger.debug("Wishlist status for product_id=%s | In wishlist=%s", product_id, is_in_wishlist)

    #review
    reviews = product.reviews.all().order_by('-created_at')
    review_count = reviews.count()
    avg_rating = reviews.aggregate(Avg('rating'))['rating__avg'] or 0

    star_distribution = {
        5: reviews.filter(rating=5).count(),
        4: reviews.filter(rating=4).count(),
        3: reviews.filter(rating=3).count(),
        2: reviews.filter(rating=2).count(),
        1: reviews.filter(rating=1).count(),
    }

    # Check if user can review (Has bought + Delivered)
    can_review = False
    user_review = None

    if request.user.is_authenticated:
        has_purchased = OrderItem.objects.filter(
            order__user=request.user,
            order__status='Delivered',
            variant__product=product
        ).exists()

        user_review = reviews.filter(user=request.user).first()

        if has_purchased:
            can_review = True
    
    context = {
        "product" : product,
        "variants" : variants,
        "images" : images,
        "related_products" : related_products,
        "is_out_of_stock" : is_out_of_stock,
        "is_in_wishlist": is_in_wishlist,
        'reviews': reviews,
        'review_count': review_count,
        'avg_rating': round(avg_rating, 1),
        'star_distribution': star_distribution,
        'can_review': can_review,
        'user_review': user_review, 
    }

    return render(request, "shop/product_detail.html", context)



#cart

@user_required
def add_to_cart(request, variant_id=None):
    if not request.user.is_authenticated:
        messages.error(request, "Please log in to add items to cart.")
        return redirect("login")
    
    is_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'
    
    if request.method != "POST":
        if is_ajax:
            return JsonResponse({'status': 'error', 'message': 'Invalid request method'}, status=405)
        return redirect('shop_products')
    
    variant_id = variant_id or request.POST.get("variant_id")

    if not variant_id:
        msg = "Invalid product selection"
        if is_ajax:
            return JsonResponse({'status': 'error', 'message': msg}, status=400)
        messages.error(request, msg)
        return redirect(request.META.get('HTTP_REFERER', 'shop_products'))
    
    variant = get_object_or_404(ProductVariant, id = variant_id)

    if not variant.product.is_active  or not variant.is_active:
        msg = "This product is not available"
        if is_ajax:
            return JsonResponse({'status': 'error', 'message': msg}, status=400)
        messages.error(request, msg)
        return redirect(request.META.get('HTTP_REFERER', 'shop_products'))

    if variant.stock <= 0:
        msg = "This product is out of stock"
        if is_ajax:
            return JsonResponse({'status': 'error', 'message': msg}, status=400)
        messages.error(request, msg)
        return redirect(request.META.get('HTTP_REFERER', 'shop_products'))
    
    try:
        quantity = int(request.POST.get('quantity', 1))
    except ValueError:
        quantity = 1

    max_qty = min(variant.stock, 4)
    quantity = max(1, min(quantity, max_qty))
    
    cart_item, created = CartItem.objects.get_or_create(
        user = request.user,
        variant=variant,
    )

    if created:
        cart_item.quantity = quantity     # FIXED
        cart_item.save()
        msg = "Item added to cart"
    else:
        if cart_item.quantity < min(variant.stock, 4):
            cart_item.quantity += 1
            cart_item.save()
            msg = "Quantity updated in cart"
        else:
            msg = "Maximum quantity reached"
            if is_ajax:
                return JsonResponse({'status': 'error', 'message': msg}, status=400)
            messages.warning(request, msg)
            return redirect(request.META.get('HTTP_REFERER', 'shop_products'))

   
    Wishlist.objects.filter(user=request.user, product=variant.product).delete()

    if is_ajax:
        return JsonResponse({
            'status': 'success', 
            'message': msg,
            'cart_count': CartItem.objects.filter(user=request.user).count()
        })

    messages.success(request, msg)
    return redirect(request.META.get('HTTP_REFERER', 'shop_products'))

@user_required
def cart_view(request):
    if not request.user.is_authenticated:
        messages.error(request, "Please log in to view your cart.")
        return redirect("login")
    

    all_cart_items = CartItem.objects.filter(user=request.user).select_related("variant", "variant__product")

    # 1. Active Items (Both Product and Variant are active)
    active_items = all_cart_items.filter(
        variant__is_active=True,
        variant__product__is_active=True,
    )

    # 2. Unlisted Items (Either Product or Variant is inactive)
    unlisted_items = all_cart_items.filter(
        Q(variant__is_active=False) | Q(variant__product__is_active=False)
    )

    out_of_stock_items = active_items.filter(variant__stock__lte=0)
    in_stock_items = active_items.filter(variant__stock__gt=0)

    subtotal = Decimal("0")
    total_items = 0

    # if not valid_items.exists():
    #     context = {
    #         "cart_items": [],
    #         "subtotal": Decimal('0'),
    #         "gst": Decimal('0'),
    #         "delivery_charge": Decimal('0'),
    #         "grand_total": Decimal('0'),
    #         "total_items": 0,
            
    #     }
    #     return render(request, "shop/cart.html", context)
    

    for item in in_stock_items:
        product = item.variant.product
        item_final_price  = product.final_price
        subtotal += item_final_price * item.quantity
        total_items += item.quantity

        item.sorted_variants = (
            product.variants
            .annotate(size_int=Cast("size", IntegerField()))
            .order_by("size_int")
        )

    subtotal = subtotal.quantize(Decimal("0.01"))
        
    gst = (subtotal * Decimal('0.18')).quantize(Decimal('0.01'))

    delivery_charge = Decimal('0') if subtotal >= Decimal('1000') else Decimal('100')

    grand_total  = subtotal + gst + delivery_charge if total_items > 0 else Decimal('0')


    context = {
        "cart_items": in_stock_items,
        "out_of_stock_items": out_of_stock_items,
        "unlisted_items": unlisted_items,  # Added to context
        "subtotal": subtotal,
        "gst": gst,
        "delivery_charge": delivery_charge,
        "grand_total": grand_total,
        "total_items": total_items,

    }

    return render(request,  "shop/cart.html", context)

@user_required
def remove_cart_item(request, item_id):
    item = get_object_or_404(CartItem, id = item_id, user = request.user)
    item.delete()
    messages.success(request, "Item removed form cart")
    return redirect("cart")

@user_required
@require_POST
def update_size(request, item_id):
    try:
        data = json.loads(request.body)
        variant_id = data.get("variant_id")

        cart_item = CartItem.objects.get(id = item_id, user=request.user)
        new_variant = ProductVariant.objects.get(id=variant_id)

        if new_variant.product != cart_item.variant.product:
            return JsonResponse({"error": "Invalid size selection"}, status=400)
        
        if new_variant.stock <= 0:
            return JsonResponse({"success":False, "error": "Selected size is out of stock"}, status=400)
        
        cart_item.variant = new_variant
        # Reset quantity to 1 if new size has less stock than current quantity
        if cart_item.quantity > new_variant.stock:
            cart_item.quantity = new_variant.stock
        cart_item.save()

        cart_data = get_cart_data(request.user)

        return JsonResponse({
            "success": True,
            "item_total": float(cart_item.variant.product.final_price * cart_item.quantity),
            "cart_data": cart_data       
        })

    except CartItem.DoesNotExist:
        return JsonResponse({"error": "cart item not found"}, status=404)
    
    except ProductVariant.DoesNotExist:
        return JsonResponse({"error": "Variant not found"}, status=404)


def get_cart_data(user):
    cart_items = CartItem.objects.filter(user=user, variant__stock__gt=0, variant__is_active=True)

    if cart_items.exists():
        subtotal = sum(item.variant.product.final_price * item.quantity for item in cart_items)
        gst = (subtotal * Decimal('0.18')).quantize(Decimal('0.01'))
        delivery_charge = Decimal('0') if subtotal >= Decimal('1000') else Decimal('100')
        grand_total = subtotal + gst + delivery_charge
        total_items = sum(item.quantity for item in cart_items)
    else:
        subtotal = gst = delivery_charge = grand_total = Decimal('0')
        total_items = 0

    return {
        "subtotal": subtotal,
        "gst": gst,
        "delivery_charge": delivery_charge,
        "grand_total": grand_total,
        "total_items": total_items
    }

@user_required
@require_POST
def update_cart_quantity(request, item_id):
    if not request.user.is_authenticated:
        return JsonResponse({"success": False, "error": "Login required"}, status=401)

    try:
        data = json.loads(request.body)

        # Handle null/undefined quantity
        qty_param = data.get("quantity")
        if qty_param is None:
            new_qty = 1 
        else:
            new_qty = int(qty_param)

        cart_item = CartItem.objects.get(id=item_id, user=request.user)
        max_allowed = min(cart_item.variant.stock, 4)

        # --- REMOVE ITEM LOGIC ---
        if new_qty < 1: 
            cart_item.delete()
            cart_data = get_cart_data(request.user) 
            
            return JsonResponse({
                "success": True, 
                "removed": True, 
                "cart_totals": cart_data  
            })

        # --- MAX LIMIT LOGIC ---
        if new_qty > max_allowed:
            return JsonResponse({
                "success": False,
                "error": f"Max quantity allowed is {max_allowed}"
            }, status=400)

        # --- UPDATE LOGIC ---
        cart_item.quantity = new_qty
        cart_item.save()
        
        cart_data = get_cart_data(request.user)

        return JsonResponse({
            "success": True,
            "removed": False,
            # Keys now match JavaScript expectations
            "item_qty": cart_item.quantity,        
            "item_total": float(cart_item.variant.product.final_price * cart_item.quantity), 
            "cart_totals": cart_data,                
        })

    except CartItem.DoesNotExist:
        return JsonResponse({"success": False, "error": "Item not found"}, status=404)

    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)}, status=500)

#wishlist
@user_required
def add_to_wishlist(request,  product_id):
    if not request.user.is_authenticated:
        messages.error(request, "Please log in to save items")
        return redirect("login")
    
    if request.method == "POST":
        product = get_object_or_404(Product, id=product_id)

        wishlist_item = Wishlist.objects.filter(user=request.user, product=product)

        if wishlist_item.exists():
            wishlist_item.delete()
            added = False
            message = "Removed from wishlist"
        else:
            Wishlist.objects.create(user=request.user, product=product)
            added = True
            message = "Added to wishlist"
        
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({
                'status': 'success',
                'added': added,
                'message': message
            })


    return redirect(request.META.get('HTTP_REFERER', 'shop_products'))

@user_required
def wishlist_view(request):
    if not request.user.is_authenticated:
        return redirect("login")
    
    wishlist_items = Wishlist.objects.filter(user=request.user).select_related('product')

    wishlist_total = sum(item.product.final_price for item in  wishlist_items)
    has_active_items = wishlist_items.filter(product__is_active=True).exists()
    
    return render(request, "shop/wishlist.html", {"wishlist_items":wishlist_items, "wishlist_total":wishlist_total, "has_active_items" : has_active_items})

@user_required
def remove_wishlist_item(request, item_id):
    item = get_object_or_404(Wishlist, id=item_id, user=request.user)
    item.delete()
    messages.success(request, "Removed from wishlist")
    return redirect('wishlist')


@user_required
def move_to_cart(request, item_id):
    wishlist_item = get_object_or_404(Wishlist, id=item_id, user=request.user)
    
    variant = wishlist_item.product.variants.filter(is_active=True, stock__gt=0).first()

    if not variant:
        messages.error(request, "No valid variant available")
        return redirect("wishlist")
    
    CartItem.objects.get_or_create(user=request.user, variant=variant)
    wishlist_item.delete()
    messages.success(request, "Moved to cart")

    return redirect("cart")
    
# @user_required
# def move_all_to_cart(request):
#     if not request.user.is_authenticated:
#         return redirect("login")
    
#     wishlist_items = Wishlist.objects.filter(user=request.user)

#     if not wishlist_items.exists():
#         messages.warning(request, "Your wishlist is empty.")
#         return redirect("wishlist")
    
#     for item in wishlist_items:
#         product = item.product
#         variant = product.variants.filter(is_active=True, stock__gt=0).first()

#         if variant:
#             CartItem.objects.get_or_create(user=request.user, variant=variant)

#         item.delete()

#     messages.success(request, "All items moved to cart successfully")
#     return redirect("cart")


@user_required
def move_all_to_cart(request):
    if not request.user.is_authenticated:
        return redirect("login")
    
    wishlist_items = Wishlist.objects.filter(user=request.user)

    if not wishlist_items.exists():
        messages.warning(request, "Your wishlist is empty.")
        return redirect("wishlist")
    
    moved_count = 0
    
    for item in wishlist_items:
        product = item.product
        
        # 1. Check if the parent product is active
        if product.is_active:
            # 2. Find the first available variant
            variant = product.variants.filter(is_active=True, stock__gt=0).first()

            if variant:
                # 3. Add to Cart
                cart_item, created = CartItem.objects.get_or_create(
                    user=request.user, 
                    variant=variant
                )
                
                # Optional: If item already exists, you might want to increase quantity
                # if not created:
                #     cart_item.quantity += 1
                #     cart_item.save()

                # 4. Only delete from wishlist if successfully moved
                item.delete()
                moved_count += 1

    # Feedback logic
    if moved_count == 0:
        messages.warning(request, "No available items found to move to cart.")
    elif moved_count < wishlist_items.count() + moved_count: 
        # Note: logic above is slightly tricky because we deleted items. 
        # Easier: check if any items remain in wishlist.
        remaining = Wishlist.objects.filter(user=request.user).count()
        if remaining > 0:
            messages.success(request, f"{moved_count} items moved to cart. {remaining} unavailable items remain in wishlist.")
        else:
             messages.success(request, "All items moved to cart successfully.")
    else:
        messages.success(request, "All items moved to cart successfully.")

    return redirect("cart")



@user_required
def clear_all_wishlist(request):
    if not request.user.is_authenticated:
        return redirect("login")
    
    Wishlist.objects.filter(user=request.user).delete()
    messages.success(request, "All items cleared from wishlist.")
    return redirect('wishlist')

    
#checkout views

@user_required
def checkout_view(request):

    if not request.user.is_authenticated:
        messages.error(request, "Please log in to proceed to checkout")
        return  redirect("login")
    
    cart_items = CartItem.objects.filter(
        user=request.user,
        variant__is_active=True,
        variant__product__is_active=True,
    ).select_related('variant', 'variant__product')

    if not cart_items.exists():
        messages.error(request, "Your cart is empty!")
        return redirect("cart")
    
    out_of_stock_items = []
    for item in cart_items:
        if item.quantity > item.variant.stock:
            out_of_stock_items.append(item.variant.product.name)

    if out_of_stock_items:
        messages.error(request,"Some items in your cart are out of stock. Please update your cart before checkout.")
        return redirect("cart")

    
    addresses = Address.objects.filter(user=request.user)

    
    subtotal = Decimal("0")

    for item in cart_items:
        final_price = item.variant.product.final_price
        subtotal += final_price * item.quantity
        
    subtotal = subtotal.quantize(Decimal("0.01"))

    discount_amount = Decimal("0")
    coupon_code = request.session.get("applied_coupon")
    applied_coupon = None

    if coupon_code:
        try:
            applied_coupon = Coupon.objects.get(code=coupon_code, isActive=True)

            today = timezone.localtime().date()
            start_date = timezone.localtime(applied_coupon.validFrom).date()
            end_date = timezone.localtime(applied_coupon.validTill).date()

            if  not (start_date <= today <= end_date):
                raise ValueError("Expired")
            

            if subtotal < applied_coupon.minCartValue:
                messages.warning(request, f"Coupon removed.  Min cart value is ₹{applied_coupon.minCartValue}")
                del request.session["applied_coupon"]
                applied_coupon = None


            elif applied_coupon.category:
                has_category = any(
                    item.variant.product.subcategory.id == applied_coupon.category.id 
                    for item in cart_items if item.variant.product.subcategory
                )
                if not has_category:
                    messages.warning(request, f"Coupon removed. Only valid for {applied_coupon.category.name}")
                    del request.session["applied_coupon"]
                    applied_coupon = None

            if applied_coupon:
                if applied_coupon.discountType == 'percent':
                    discount_amount = (subtotal * applied_coupon.discountValue) / 100
                else:
                    discount_amount = applied_coupon.discountValue
                
                
                if discount_amount > subtotal:
                    discount_amount = subtotal 

        except Coupon.DoesNotExist:
            del request.session["applied_coupon"]
        except Exception:
            del request.session["applied_coupon"]      


    taxable_amount = (subtotal - discount_amount).quantize(Decimal("0.01"))

    gst = (taxable_amount * Decimal('0.18')).quantize(Decimal('0.01'))

    delivery_charge = Decimal('0') if taxable_amount >= Decimal('1000') else Decimal('100')
    
    is_free_delivery = (delivery_charge == Decimal('0'))

    grand_total = taxable_amount + gst + delivery_charge 

    if grand_total < 0:
        grand_total = Decimal("0.00")

    estimated_delivery_date = (now() + timedelta(days=5)).strftime("%d %b %Y")


    context = {
        'addresses': addresses,
        'cart_items': cart_items,
        'subtotal': subtotal,
        'gst': gst,
        'delivery_charge': delivery_charge,
        'discount_amount': discount_amount.quantize(Decimal("0.01")),
        'grand_total': grand_total.quantize(Decimal("0.01")),
        'total_items': sum(item.quantity for item in cart_items),
        'estimated_delivery_date': estimated_delivery_date,
        'is_free_delivery': is_free_delivery,
        'applied_coupon': coupon_code if applied_coupon else None,


    }
    return render(request, 'shop/checkout.html', context)


@user_required
def payment_view(request, address_id):
    if not request.user.is_authenticated:
        messages.error(request, "Please log in to continue payment.")
        return redirect("login")
    
    address = get_object_or_404(Address, id=address_id, user=request.user)

    data = get_cart_totals(request.user)
    if not data:
        messages.error(request, "Your cart is empty")
        return redirect("cart")


    subtotal = data['subtotal']
    
    


    discount_amount = Decimal("0")
    coupon_code = request.session.get("applied_coupon")

    if coupon_code:
        try:
            coupon = Coupon.objects.get(code=coupon_code, isActive=True)
            today  = timezone.localtime().date()
            if coupon.validFrom.date() <= today <= coupon.validTill.date():
                if subtotal >= coupon.minCartValue:
                    if coupon.discountType == 'percent':
                        discount_amount = (subtotal * coupon.discountValue) / 100
                    else:
                        discount_amount = coupon.discountValue
                    
                    if discount_amount > subtotal:
                        discount_amount = subtotal
        except Coupon.DoesNotExist:
            pass

    discount_amount = discount_amount.quantize(Decimal("0.01"))
    
    taxable_amount = (subtotal - discount_amount).quantize(Decimal("0.01"))
    gst = (taxable_amount * Decimal("0.18")).quantize(Decimal("0.01"))
    delivery_charge = Decimal('0') if taxable_amount >= Decimal('1000') else Decimal('100')
    grand_total = taxable_amount + gst + delivery_charge

    if grand_total < 0:
        grand_total = Decimal("0.00")

    # wallet
    wallet,_ = Wallet.objects.get_or_create(user=request.user)
    wallet_balance = wallet.balance

    client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
    razorpay_order = client.order.create({
        "amount":int(grand_total * 100),
        "currency" :"INR",
        "payment_capture":1,
    })

    context = {
        'address': address,
        'cart_items': data['cart_items'],
        'subtotal': subtotal,
        'gst': gst,
        'delivery_charge': delivery_charge,
        'discount_amount': discount_amount,
        'grand_total': grand_total,
        'wallet_balance' : wallet_balance,

        "rzp_key_id": settings.RAZORPAY_KEY_ID,
        "rzp_order_id": razorpay_order["id"],
        "rzp_amount": int(grand_total * 100),
        "order_number": razorpay_order["id"],
    }

    return render(request, 'shop/payment.html', context)



@user_required
def place_order(request):
    if request.method != "POST":
        return redirect('checkout')

    address_id = request.POST.get('address_id')
    payment_method = request.POST.get("payment_method")

    if not address_id:
        messages.error(request, "Please select a delivery address.")
        return redirect("checkout")

    address = get_object_or_404(Address, id=address_id, user=request.user)

    data = get_cart_totals(request.user)
    if not data:
        messages.error(request, "Your cart is empty")
        return redirect("cart")

    cart_items = data['cart_items']
    subtotal = data['subtotal']
    

    if not cart_items.exists():
        messages.error(request, "Your cart is empty")
        return redirect("cart")

    # COUPON LOGIC 
    discount_amount = Decimal("0")
    coupon_code = request.session.get("applied_coupon")
    applied_coupon = None

    if coupon_code:
        try:
            coupon = Coupon.objects.get(code=coupon_code, isActive=True)
            today = timezone.localtime().date()

            if coupon.validFrom.date() <= today <= coupon.validTill.date():
                if subtotal >= coupon.minCartValue:
                    if coupon.discountType == "percent":
                        discount_amount = (subtotal * coupon.discountValue) / 100
                    else:
                        discount_amount = coupon.discountValue

                    if discount_amount > subtotal:
                        discount_amount = subtotal

                    applied_coupon = coupon
        except Coupon.DoesNotExist:
            pass
    
    discount_amount = discount_amount.quantize(Decimal("0.01"))
    
    taxable_amount = (subtotal - discount_amount).quantize(Decimal("0.01"))

    gst = (taxable_amount * Decimal("0.18")).quantize(Decimal("0.01"))

    delivery_charge = Decimal('0') if taxable_amount >= Decimal('1000') else Decimal('100')

    grand_total = taxable_amount + gst + delivery_charge

    if grand_total < 0:
        grand_total = Decimal("0.00")

    # RAZORPAY FLOW 
    if payment_method == "razorpay":
        client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
        razorpay_order = client.order.create({
            "amount": int(grand_total * 100),
            "currency": "INR",
            "payment_capture": 1,
        })

        payment = Payment.objects.create(
            user=request.user,
            amount=grand_total,
            purpose="order_payment",
            razorpay_order_id=razorpay_order['id'],
            status="PENDING",
            checkout_snapshot={
                "address_id": address.id,
                "subtotal": str(subtotal),
                "gst": str(gst),
                "delivery_charge": str(delivery_charge),
                "discount_amount": str(discount_amount),
                "coupon_id": applied_coupon.id if applied_coupon else None,
                "items": [
                    {"cart_item_id": item.id, "variant_id": item.variant_id,
                     "quantity": item.quantity, "price": str(item.unit_price)}
                    for item in cart_items
                ],
            },
        )

        return render(request, "shop/razorpay_payment.html", {
            "razorpay_order": razorpay_order,
            "razorpay_key": settings.RAZORPAY_KEY_ID,
            "payment": payment,
            "amount": grand_total,
            "address": address,
        })

   
    try:
        with transaction.atomic():

           
            variant_ids = cart_items.values_list("variant_id", flat=True)
            variants = ProductVariant.objects.select_for_update().filter(id__in=variant_ids)
            variant_map = {v.id: v for v in variants}

       
            for item in cart_items:
                variant = variant_map[item.variant_id]
                if variant.stock < item.quantity:
                    messages.error(
                        request,
                        f"{variant.product.name} is out of stock"
                    )
                    return redirect("checkout")

            payment_status = "PENDING"

            # WALLET LOGIC 
            if payment_method == "wallet":
                wallet, _ = Wallet.objects.get_or_create(user=request.user)
                wallet = Wallet.objects.select_for_update().get(user=request.user)

                if wallet.balance >= grand_total:
                    balance_before = wallet.balance
                    wallet.balance -= grand_total
                    wallet.save()

                    WalletTransaction.objects.create(
                        wallet=wallet,
                        amount=grand_total,
                        transaction_type="debit",
                        description="Order Payment from Wallet",
                        balance_before=balance_before,
                        balance_after=wallet.balance
                    )
                    payment_status = "SUCCESS"
                else:
                    messages.error(request, "Insufficient wallet balance")
                    return redirect("payment", address_id=address_id)

            elif payment_method == "cod":
                if grand_total < 5000:
                    payment_status = "PENDING"
                else:
                    messages.error(
                        request,
                        "Cash on Delivery is available only for orders below ₹5000."
                    )
                    return redirect("payment", address_id=address_id)

            else:
                messages.error(request, "Invalid payment option")
                return redirect("payment", address_id=address_id)

            #  CREATE ORDER -
            order = Order.objects.create(
                user=request.user,
                address=address,
                subtotal=subtotal,
                gst=gst,
                delivery_charge=delivery_charge,
                total_amount=grand_total,
                coupon=applied_coupon,
                discount_amount=discount_amount,
                payment_method=payment_method,
                status="Processing" if payment_status == "SUCCESS" else "Pending",
            )

            #ORDER ITEMS + STOCK DEDUCTION
            for item in cart_items:
                variant = variant_map[item.variant_id]

                OrderItem.objects.create(
                    order=order,
                    variant=variant,
                    quantity=item.quantity,
                    price=item.variant.product.final_price,
                )

                variant.stock -= item.quantity
                variant.save()

            cart_items.delete()

            return redirect('order_success', order_id=order.id)

    except Exception as e:
        print("Order Error:", e)
        messages.error(request, "An error occurred. Please try again.")
        return redirect('checkout')


@csrf_exempt
@user_required
def razorpay_payment_verify(request):
    payment_id = request.GET.get("payment_id")
    order_id = request.GET.get("order_id")
    signature = request.GET.get("signature")
    address_id = request.GET.get("address_id")

    client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))

    try:
        client.utility.verify_payment_signature({
            "razorpay_order_id": order_id,
            "razorpay_payment_id": payment_id,
            "razorpay_signature": signature,
        })

        with transaction.atomic():

            payment = get_object_or_404(
                Payment.objects.select_for_update(), razorpay_order_id=order_id,
                user=request.user, purpose="order_payment",
            )
            if payment.order_id:
                return redirect("order_success", order_id=payment.order_id)
            if payment.status == "SUCCESS":
                raise ValueError("This payment has already been processed.")
            verify_captured_payment(client, payment, payment_id)
            snapshot = payment.checkout_snapshot
            if not snapshot or not snapshot.get("items"):
                raise ValueError("Checkout details are missing. Please contact support.")
            address = get_object_or_404(
                Address, id=snapshot["address_id"], user=request.user,
            )

            quantities = {}
            for item in snapshot["items"]:
                quantities[item["variant_id"]] = (
                    quantities.get(item["variant_id"], 0) + item["quantity"]
                )
            variants = {
                variant.id: variant
                for variant in ProductVariant.objects.select_for_update()
                .filter(id__in=quantities).order_by("id")
            }
            for variant_id, quantity in quantities.items():
                variant = variants.get(variant_id)
                if (
                    quantity <= 0 or variant is None
                    or not variant.is_active or not variant.product.is_active
                    or variant.stock < quantity
                ):
                    messages.error(
                        request,
                        "Payment received, but an item is unavailable. "
                        "Please contact support with payment reference " + payment_id
                        + " to arrange a refund or fulfillment.",
                    )
                    return redirect("cart")

            order = Order.objects.create(
                user=request.user, address=address,
                subtotal=Decimal(snapshot["subtotal"]),
                gst=Decimal(snapshot["gst"]),
                delivery_charge=Decimal(snapshot["delivery_charge"]),
                discount_amount=Decimal(snapshot["discount_amount"]),
                coupon_id=snapshot["coupon_id"],
                total_amount=payment.amount,
                payment_method="Razorpay", status="Processing",
            )
            for item in snapshot["items"]:
                variant = variants[item["variant_id"]]
                OrderItem.objects.create(
                    order=order, variant=variant,
                    quantity=item["quantity"], price=Decimal(item["price"]),
                )
                variant.stock -= item["quantity"]
                variant.save()
                # Keep items added or edited while the payment window was open.
                CartItem.objects.filter(
                    id=item["cart_item_id"], user=request.user,
                    variant_id=item["variant_id"], quantity=item["quantity"],
                ).delete()

            payment.order = order
            payment.status = "SUCCESS"
            payment.razorpay_payment_id = payment_id
            payment.razorpay_signature = signature
            payment.save()

        return redirect('order_success', order_id=order.id)


    except razorpay.errors.SignatureVerificationError:
        payment = Payment.objects.filter(razorpay_order_id=order_id, user=request.user, purpose="order_payment").first()
        messages.error(request, "Payment verification failed.")
        return redirect('payment_failed', order_id=payment.id if payment else 0)
    except Exception as e:
        messages.error(request, "An error occurred during order creation.")
        print(e)
        return redirect("cart")

@user_required
def order_success(request, order_id):
    order = get_object_or_404(Order, id=order_id, user=request.user)
    estimated_delivery_date = (now() + timedelta(days=5)).strftime("%d %b %Y")

    coupon_code = request.session.get("applied_coupon")
    if coupon_code:
        try:
            coupon = Coupon.objects.get(code = coupon_code)
            usage, created = CouponUsage.objects.get_or_create(
                user=request.user,
                coupon = coupon
            )
            usage.used_count  += 1
            usage.save()
        except Coupon.DoesNotExist:
            pass

    Wishlist.objects.filter(
        user=request.user,
        product__in = order.items.values_list("variant__product", flat=True)).delete()


    request.session.pop("applied_coupon", None)
    request.session.modified = True


    return render(request, 'shop/order_success.html',{'order': order, "estimated_delivery_date" : estimated_delivery_date })

@user_required
def order_failed_view(request, order_id):
    order = None
    address_id = None

    try:
        order = Order.objects.get(id =order_id, user=request.user)
        address_id = order.address.id
    except Order.DoesNotExist:
        order = None

        address_id = request.GET.get('address_id')

    error_description = request.GET.get('description', 'Transaction failed')
        

    return render(request, "shop/order_failed.html",{
        "order":order,
        "order_id_display": order_id,
        "address_id": address_id,
        "error_description": error_description,
    })

@user_required
def razorpay_payment_failed(request):
    order_id = request.GET.get('order_id')
    error_description = request.GET.get('description')
    address_id = request.GET.get('address_id')

    payment = Payment.objects.filter(razorpay_order_id=order_id, user=request.user, purpose="order_payment").first()
    
    Payment.objects.filter(
        razorpay_order_id=order_id, user=request.user,
        purpose="order_payment", status="PENDING",
    ).update(status="FAILED")

    return render(request, "shop/order_failed.html", {
        "payment": payment,
        "order_id_display": order_id,
        "error_description": error_description,
        "address_id": address_id
    })



@user_required
def cancel_order(request, order_id):
    order = get_object_or_404(Order, order_id=order_id, user=request.user)

    if order.status in ["Shipped", "Delivered", "Cancelled"]:
        messages.error(request, "Cannot cancel this order. Please contact support.")
        return redirect('order_detail', order_id=order_id)


    if request.method == "POST":
        reason = request.POST.get("cancel_reason")

        try:
            with transaction.atomic():
                order_items = OrderItem.objects.filter(order=order)
                for item in order_items:
                    item.variant.stock += item.quantity
                    item.variant.save()


                if order.coupon:
                    usage = CouponUsage.objects.filter(
                        user=request.user,
                        coupon=order.coupon
                    ).first()
                    if usage and usage.used_count > 0:
                        usage.used_count -= 1
                        usage.save()

                if order.payment_method in ["wallet","razorpay","Razorpay"]:
                    wallet,_ = Wallet.objects.get_or_create(user=request.user)
                    refund_amount = order.total_amount

                    balance_before = wallet.balance
                    wallet.balance += refund_amount
                    wallet.save()
                    balance_after = wallet.balance

                    WalletTransaction.objects.create(
                        wallet=wallet,
                        amount=refund_amount,
                        transaction_type="credit",
                        description = f"Refund for Order #{order.order_id}",
                        balance_before =balance_before,
                        balance_after = balance_after
                    )
                    messages.success(request, f"Order cancelled. ₹{refund_amount} refunded to wallet.")
                else:
                    messages.success(request, "Order cancelled successfully.")

                order.status = "Cancelled"
                order.cancel_reason = reason
                order.save()
                    
        except Exception as e:
            messages.error(request, f"Error cancelling order: {e}")

    return redirect('order_detail', order_id=order_id)

@user_required
def cancel_order_item(request, item_id):
    item = get_object_or_404(OrderItem , id = item_id, order__user=request.user)
    order = item.order

    if order.status in ["Shipped", "Delivered", "Cancelled"]:
        messages.error(request, "Cannot cancel items at this stage.")
        return redirect('order_detail', order_id=order.order_id)
    

    if request.method == "POST":
        try:
            with transaction.atomic():
                old_grand_total = order.total_amount

                item.status = "Cancelled"
                item.save()

                item.variant.stock += item.quantity
                item.variant.save()
                
                active_items = order.items.exclude(status='Cancelled')

                if not active_items.exists():

                    order.status = "Cancelled"
                    order.cancel_reason = "All items were cancelled individually"
                    
                    refund_amount = old_grand_total

                    if order.coupon:
                        usage = CouponUsage.objects.filter(user=request.user,coupon = order.coupon).first()
                        if usage and usage.used_count > 0:
                            usage.used_count -= 1
                            usage.save()
                    
                    order.save()

                else:
                    new_subtotal = sum(i.total_price for i in active_items)
                    order.subtotal = new_subtotal


                    new_discount = Decimal("0")

                    if order.coupon:
                        if new_subtotal < order.coupon.minCartValue:
                            order.coupon = None
                            new_discount = Decimal("0")
                            messages.warning(request, "Coupon is removed because order total fell  below minimum requirement")
                        else:
                            if order.coupon.discountType == "percent":
                                new_discount = (new_subtotal * order.coupon.discountValue) / 100
                            else:
                                new_discount = order.coupon.discountValue

                        if new_discount  > new_subtotal:
                            new_subtotal = new_subtotal

                    order.discount_amount = new_discount.quantize(Decimal("0.01"))

                    taxable_amount = (new_subtotal - order.discount_amount).quantize(Decimal("0.01"))
                  

                    order.gst = (taxable_amount * Decimal('0.18')).quantize(Decimal('0.01')) 


                    
                    if order.subtotal >= 1000:
                        order.delivery_charge = Decimal(0)
                    else:
                        order.delivery_charge = Decimal(100)



                    order.total_amount = taxable_amount + order.gst + order.delivery_charge
                    order.save()
                    

                    refund_amount = old_grand_total - order.total_amount

                if refund_amount > 0 and order.payment_method in ["wallet", "razorpay", "Razorpay"]:
                    wallet,_ = Wallet.objects.get_or_create(user=request.user)

                    balance_before = wallet.balance
                    wallet.balance += refund_amount
                    wallet.save()
                    balance_after = wallet.balance

                    WalletTransaction.objects.create(
                        wallet=wallet,
                        amount=refund_amount,
                        transaction_type="credit",
                        description=f"Refund for Item: {item.variant.product.name} (Order #{order.order_id}",
                        balance_before=balance_before,
                        balance_after=balance_after
                    )
                    messages.success(request, f"Item cancelled. ₹{refund_amount} refunded to wallet.")
                else:
                    messages.success(request, "Item cancelled and order updated.")

        except Exception as e:
            messages.error(request, f"Error cancelling item: {e}")

    return redirect('order_detail', order_id=order.order_id)



#return 
@user_required
def return_order_items(request, order_id):
    order = get_object_or_404(Order, order_id = order_id, user=request.user)


    if order.status != "Delivered" or not order.delivered_at:
        messages.error(request, "Return request is only allowed after delivery.")
        return redirect('order_detail', order_id=order_id)
    
    expiry_date = (order.delivered_at + timedelta(days=10)).date()
    today = now().date()

    if today > expiry_date:
        messages.error(request, "Return window has expired for this order.")
        return redirect('order_detail', order_id=order_id)
    
    already_requested_ids = ReturnRequest.objects.filter(
        order_item__order=order
    ).values_list('order_item_id', flat=True)

    eligible_items = order.items.exclude(id__in=already_requested_ids).exclude(status="Cancelled")

    if not eligible_items.exists():
        messages.info(request, "All eligible items in this order have already been requested for return.")
        return redirect('order_detail', order_id=order_id)

    context = {
        "order": order,
        "items": eligible_items,
        "addresses": request.user.addresses.all(),
        "expiry_date": expiry_date,
    }

    return render(request, "shop/returns.html", context)

@user_required
def submit_return_request(request, order_id):
    if request.method == "POST":
        order = get_object_or_404(Order, order_id=order_id, user=request.user)

        selected_item_ids = request.POST.getlist('item_id')

        if not selected_item_ids:
            messages.error(request, "Please select at least one item to return.")
            return redirect('return_order_items', order_id=order_id)


        
        reason = request.POST.get('reason')
        pickup_address_id = request.POST.get('pickup_address')
    
        uploaded_images = request.FILES.getlist('images')


        for item_id in selected_item_ids:
            order_item = get_object_or_404(OrderItem, id=item_id, order=order)

            if not order_item.is_return_eligible():
                messages.error(
                    request,
                    f"Return window expired for {order_item.variant.product.name}. "
                    f"Returns are allowed only within 10 days of delivery."
                )
                return redirect('order_detail', order_id=order_id)
            
            if ReturnRequest.objects.filter(order_item=order_item).exists():
                continue

            return_request = ReturnRequest.objects.create(
                order_item = order_item,
                user=request.user,
                reason = reason,
                pickup_address_id = pickup_address_id,
            )

            if uploaded_images:
                if len(uploaded_images) > 0:
                    return_request.image1 = uploaded_images[0]
                if len(uploaded_images) > 1:
                    return_request.image2 = uploaded_images[1]
                if len(uploaded_images) > 2:
                    return_request.image3 = uploaded_images[2]
                return_request.save()
            
        messages.success(request, "Return request submitted successfully")
        return redirect('order_detail', order_id = order.order_id)
    
    return redirect('return_order_items', order_id=order_id)


#reviews
@user_required
def submit_review(request, product_id):
    product = get_object_or_404(Product, id=product_id)

    has_purchased  = OrderItem.objects.filter(
        order__user = request.user,
        order__status = 'Delivered',
        variant__product = product
    ).exists()

    if  not has_purchased:
        messages.error(request,  "You can only review products you have purchased")
        return redirect(request.META.get('HTTP_REFERER', '/'))
    
    existing_review = ProductReview.objects.filter(user=request.user, product=product).first()

    if request.method == "POST":
        rating = request.POST.get('rating')
        comment = request.POST.get('comment')

        if not rating:
            messages.error(request, "Please select a star rating")
            return redirect(request.path)
        
        if existing_review:
            existing_review.rating = rating
            existing_review.comment = comment
            existing_review.save()
            messages.success(request, "Your review has been updated!")
        else:
            ProductReview.objects.create(
                product=product,
                user = request.user,
                rating = rating,
                comment =  comment,

            )
            messages.success(request, "Thank you for your review!")

        return redirect(request.META.get('HTTP_REFERER', '/'))
    
    context = {
        'product' : product,
        'review' : existing_review
    }

    return render(request, 'shop/submit_review.html', context)


from django.utils import timezone
from datetime import datetime

@user_required
def apply_coupon(request):
    if request.method != "POST":
        return redirect("checkout")

    code = request.POST.get("coupon_code","").strip().upper()

    if not code:
        messages.error(request, "Please enter a coupon code.")
        return redirect("checkout")
    
    try:
        coupon = Coupon.objects.get(code__iexact = code, isActive=True)
    except Coupon.DoesNotExist:
        messages.error(request, "Invalid or inactive coupon")
        return redirect("checkout")
    
    today =  timezone.localtime().date()
    start_date = timezone.localtime(coupon.validFrom).date()
    end_date = timezone.localtime(coupon.validTill).date()

    
    if not (start_date <= today <= end_date):
        messages.error(request, "This coupon is expired or not yet active.")
        return redirect("checkout")
    

    cart_items = request.user.cart_items.select_related("variant", "variant__product")
    if not cart_items:
        messages.error(request, "Your cart is empty. Add items  before  applying a coupon.")
        return redirect("checkout")
    
    cart_total = sum(item.total_price for item in cart_items)

    if cart_total < coupon.minCartValue:
        messages.error(request, f"Minimum cart  value must be ₹{coupon.minCartValue} to use this coupon.")
        return redirect("checkout")

    if coupon.category:
        allowed = any(
            item.variant.product.subcategory and
            item.variant.product.subcategory.id == coupon.category.id
            for item in cart_items
        )

        if not allowed:
            messages.error(request, "This coupon is not valid for the product in your cart.")
            return redirect("checkout")

    usage, created = CouponUsage.objects.get_or_create(
        user = request.user,
        coupon = coupon,
    )
            
    if usage.used_count >= coupon.userLimit:
        messages.error(request, "You already used this coupon the maximum allowed times.")
        return redirect("checkout")
    
    request.session["applied_coupon"] = coupon.code
    request.session.modified = True

    messages.success(request, f"Coupon '{coupon.code}' applied successfully!")
    return redirect("checkout")
 
            
@user_required
def remove_coupon(request):
    if "applied_coupon" in request.session:
        del request.session["applied_coupon"]
        messages.success(request, "Coupon removed successfully.")
    return redirect("checkout")








    
    


