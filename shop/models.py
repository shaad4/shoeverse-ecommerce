from django.db import models
from django.conf import settings
from products.models import ProductVariant, Product
from users.models import Address
from decimal import Decimal

from datetime import timedelta
from django.utils import timezone
from coupons.models import Coupon,CouponUsage

import uuid

def generate_order_id():
    return f"SV-{uuid.uuid4().hex[:10].upper()}"


# Create your models here.


class CartItem(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='cart_items')
    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)
    added_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.variant.product.name} - Size {self.variant.size} - Qty {self.quantity}"

    @property
    def total_price(self):
        quantity = min(self.quantity, 4)
        return self.variant.product.final_price * quantity
    
    @property
    def unit_price(self):
        return self.variant.product.final_price



class Wishlist(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete= models.CASCADE, related_name='wishlist')
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user','product')


class Order(models.Model):
    STATUS_CHOICES = [
        ('Pending', 'Pending'),
        ('Processing', 'Processing'),
        ('Shipped', 'Shipped'),
        ('Delivered' , 'Delivered'),
        ('Cancelled' , 'Cancelled'),

    ]

    PAYMENT_METHOD_CHOICES = [
        ('COD','Cash on Delivery'),
        ('ONLINE', 'Online Payment'),

    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    address = models.ForeignKey(Address, on_delete=models.SET_NULL, null=True, blank=True)

    order_id = models.CharField(max_length=20, unique=True, blank=True)

    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    gst = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    delivery_charge = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    status  = models.CharField(max_length=20, choices=STATUS_CHOICES,  default='Pending')
    payment_method = models.CharField(max_length=10, choices=PAYMENT_METHOD_CHOICES, default='COD')

    cancel_reason = models.TextField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    coupon = models.ForeignKey(Coupon, null=True, blank=True, on_delete=models.SET_NULL)
    discount_amount = models.DecimalField(max_digits=10,  decimal_places=2, default=0)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Order  #{self.id} - {self.user.email}"
    
    def save(self, *args, **kwargs):
        if not self.order_id:
            self.order_id = generate_order_id()
        super().save(*args, **kwargs)

    def calculate_total(self):
        total = sum(item.total_price for item in self.items.all())
        self.total_amount = total
        self.save()


class OrderItem(models.Model):

    ORDER_STATUS_CHOICES = [
        ('Pending', 'Pending'),
        ('Processing', 'Processing'),
        ('Shipped', 'Shipped'),
        ('Delivered' , 'Delivered'),
        ('Cancelled' , 'Cancelled'),
        ('Returned', 'Returned'),
        ('Refunded', 'Refunded'),
    ]

    order = models.ForeignKey(Order, on_delete=models.CASCADE ,related_name = 'items')
    variant = models.ForeignKey(ProductVariant, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=1)
    price = models.DecimalField(max_digits=10, decimal_places=2, default = 0)
    status = models.CharField(max_length=20, choices=ORDER_STATUS_CHOICES, default='Pending')

    @property
    def total_price(self):
        return self.price * (self.quantity)
    
    def __str__(self):
        return f"{self.variant.product.name} - Qty: {self.quantity}"
    
    
    def is_return_eligible(self):
        if self.status == "Cancelled":
            return False
        
        # First, check if this item is from a delivered order
        if self.order.status != 'Delivered' or not self.order.delivered_at:
            return False   # Return not allowed

        # Now check if today is within 10 days of the delivery date
        return timezone.now().date() <= (self.order.delivered_at + timedelta(days=10)).date()
    

# returns

class ReturnRequest(models.Model):

    RETURN_STATUS_CHOICES = [
        ('REQUESTED', 'Requested'),
        ('APPROVED', 'Approved'),
        ('PICKUP_SCHEDULED', 'Pickup Scheduled'),
        ('PICKED_UP', 'Item Picked Up'),
        ('REFUND_INITIATED', 'Refund Initiated'),
        ('REFUNDED', 'Refund Completed'),
        ('DECLINED', 'Declined'),
    ]

    order_item = models.ForeignKey(OrderItem, on_delete=models.CASCADE, related_name="return_requests")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="user_returns")

    pickup_address = models.ForeignKey(Address, on_delete=models.SET_NULL, null=True, blank=True, related_name='return_pickups')

    reason = models.TextField()
    comments = models.TextField(blank=True, null=True) # admin comments

    image1 = models.ImageField(upload_to="returns/%Y/%m/%d/", blank=True, null=True)
    image2 = models.ImageField(upload_to="returns/%Y/%m/%d/", blank=True, null=True)
    image3 = models.ImageField(upload_to="returns/%Y/%m/%d/", blank=True, null=True)

    pickup_date = models.DateField(blank=True, null=True)

    refund_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=25, choices=RETURN_STATUS_CHOICES, default='REQUESTED')

    stock_updated = models.BooleanField(default=False)

    requested_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-requested_at']
        verbose_name = "Return Request"
        verbose_name_plural = "Return Requests"

    def __str__(self):
        return f"Return {self.order_item.variant.product.name} ({self.status})"

    def calculate_refund_amount(self):
        order = self.order_item.order
        # Allocate the actual merchandise/tax payment; shipping is not refundable.
        # Cumulative rounding makes all item refunds add up exactly to this pool.
        items = list(order.items.exclude(status="Cancelled").order_by("id"))
        subtotal = sum((item.total_price for item in items), Decimal("0"))
        if subtotal <= 0:
            return Decimal("0.00")
        pool = max(Decimal("0"), min(
            order.total_amount - order.delivery_charge,
            order.subtotal - order.discount_amount + order.gst,
        ))
        cumulative = Decimal("0")
        for item in items:
            before = (pool * cumulative / subtotal).quantize(Decimal("0.01"))
            cumulative += item.total_price
            after = (pool * cumulative / subtotal).quantize(Decimal("0.01"))
            if item.pk == self.order_item.pk:
                return after - before
        return Decimal("0.00")

    def save(self, *args, **kwargs):
        if not self.refund_amount:
            self.refund_amount = self.calculate_refund_amount()

        # 🔹 Restore stock ONLY when status becomes REFUNDED and not yet updated
        if self.status == 'REFUNDED' and not self.stock_updated:
            variant = self.order_item.variant
            variant.stock += self.order_item.quantity  # restore stock
            variant.save()
            self.stock_updated = True   # mark as done to prevent double update

        super().save(*args, **kwargs)


#