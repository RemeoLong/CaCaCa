import uuid
from decimal import Decimal
from django.conf import settings
from django.db import models
from django.urls import reverse


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Awaiting payment'
        CREATING = 'creating', 'Connecting to PayPal'
        APPROVAL = 'approval', 'Awaiting PayPal approval'
        CAPTURING = 'capturing', 'Payment verification pending'
        PAID = 'paid', 'Paid — needs packing'
        CANCELLED = 'cancelled', 'Cancelled'
        EXPIRED = 'expired', 'Reservation expired'
        FAILED = 'failed', 'Payment failed'
        REVIEW = 'review', 'Payment needs review'
        PARTIAL_REFUND = 'partial_refund', 'Partially refunded'
        REFUNDED = 'refunded', 'Refunded'

    class FulfillmentStatus(models.TextChoices):
        UNFULFILLED = 'unfulfilled', 'Waiting for payment'
        NEEDS_PACKING = 'needs_packing', 'Needs packing'
        PACKED = 'packed', 'Packed'
        SHIPPED = 'shipped', 'Shipped'
        DELIVERED = 'delivered', 'Delivered'
        COMPLETED = 'completed', 'Completed'

    class Carrier(models.TextChoices):
        USPS = 'usps', 'USPS'
        UPS = 'ups', 'UPS'
        FEDEX = 'fedex', 'FedEx'
        OTHER = 'other', 'Other'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    checkout_key = models.UUIDField(unique=True)
    owner_key = models.CharField(max_length=64, db_index=True, editable=False)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    name = models.CharField(max_length=120)
    email = models.EmailField()
    phone = models.CharField(max_length=40, blank=True)
    address = models.JSONField()
    notes = models.TextField(blank=True)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2)
    shipping = models.DecimalField(max_digits=12, decimal_places=2)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2)
    tax_shipping = models.BooleanField(default=False)
    tax = models.DecimalField(max_digits=12, decimal_places=2)
    total = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default='USD')
    shipping_mode = models.CharField(max_length=12)
    shipping_rate = models.DecimalField(max_digits=8, decimal_places=2)
    paypal_order_id = models.CharField(max_length=64, null=True, blank=True, unique=True)
    capture_id = models.CharField(max_length=64, null=True, blank=True, unique=True)
    approval_url = models.URLField(max_length=1000, blank=True)
    reserved_until = models.DateTimeField()
    reservation_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=80, blank=True)
    refunded_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    fulfillment_status = models.CharField(max_length=20, choices=FulfillmentStatus.choices,
        default=FulfillmentStatus.UNFULFILLED, db_index=True)
    carrier = models.CharField(max_length=10, choices=Carrier.choices, blank=True)
    carrier_other = models.CharField(max_length=80, blank=True)
    tracking_number = models.CharField(max_length=100, blank=True)
    actual_shipping_cost = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    packed_at = models.DateTimeField(null=True, blank=True)
    shipped_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [models.CheckConstraint(condition=models.Q(total__gt=0, subtotal__gt=0, tax__gte=0, shipping__gte=0), name='nonnegative_order_amounts')]

    @property
    def number(self):
        return 'CAC-' + self.id.hex[:12].upper()

    @property
    def payment_confirmed(self):
        return self.status in [self.Status.PAID, self.Status.PARTIAL_REFUND, self.Status.REFUNDED]

    def get_absolute_url(self):
        return reverse('order_detail', args=[self.pk])

    @property
    def tracking_url(self):
        from urllib.parse import quote
        number = quote(self.tracking_number)
        return {
            self.Carrier.USPS: f'https://tools.usps.com/go/TrackConfirmAction?tLabels={number}',
            self.Carrier.UPS: f'https://www.ups.com/track?loc=en_US&tracknum={number}',
            self.Carrier.FEDEX: f'https://www.fedex.com/fedextrack/?trknbr={number}',
        }.get(self.carrier, '') if self.tracking_number else ''

    def __str__(self):
        return self.number


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name='items')
    product = models.ForeignKey('catalog.Product', on_delete=models.PROTECT)
    name = models.CharField(max_length=180)
    rod_id = models.CharField(max_length=60)
    serial_number = models.CharField(max_length=80, blank=True)
    design = models.CharField(max_length=80, blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True,
        help_text='Private build-cost snapshot captured when the order was placed.')
    quantity = models.PositiveIntegerField()
    line_total = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['order', 'product'], name='one_line_per_product'),
                       models.CheckConstraint(condition=models.Q(quantity__gt=0, price__gt=0), name='positive_order_item')]


class PaymentEvent(models.Model):
    event_id = models.CharField(max_length=100, unique=True)
    event_type = models.CharField(max_length=100)
    order = models.ForeignKey(Order, null=True, blank=True, on_delete=models.PROTECT)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    outcome = models.CharField(max_length=100, blank=True)


class Refund(models.Model):
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name='refunds')
    paypal_id = models.CharField(max_length=64, unique=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=30)
    completed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)


class OrderAudit(models.Model):
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name='audit')
    action = models.CharField(max_length=100)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']


class NotificationLog(models.Model):
    class Kind(models.TextChoices):
        CUSTOMER_CONFIRMATION = 'customer_confirmation', 'Customer order confirmation'
        OWNER_NEW_ORDER = 'owner_new_order', 'Owner new-order notice'
        SHIPPING_CONFIRMATION = 'shipping_confirmation', 'Customer shipping confirmation'

    class Status(models.TextChoices):
        SENT = 'sent', 'Sent'
        FAILED = 'failed', 'Failed'
        SKIPPED = 'skipped', 'Skipped'

    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name='notifications')
    kind = models.CharField(max_length=30, choices=Kind.choices)
    status = models.CharField(max_length=12, choices=Status.choices)
    recipient = models.EmailField(blank=True)
    attempted_at = models.DateTimeField(auto_now_add=True)
    error_code = models.CharField(max_length=80, blank=True)

    class Meta:
        ordering = ['-attempted_at']


class BusinessExpense(models.Model):
    class Category(models.TextChoices):
        PAYMENT_FEE = 'payment_fee', 'Payment processing fee'
        SHIPPING = 'shipping', 'Additional shipping expense'
        SUPPLIES = 'supplies', 'Shop supplies'
        MARKETING = 'marketing', 'Marketing'
        SOFTWARE = 'software', 'Software and services'
        OTHER = 'other', 'Other expense'

    incurred_on = models.DateField(db_index=True)
    category = models.CharField(max_length=20, choices=Category.choices, db_index=True)
    description = models.CharField(max_length=180)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reference = models.CharField(max_length=100, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-incurred_on', '-created_at']
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0), name='positive_business_expense')]

    def __str__(self):
        return f'{self.get_category_display()} — {self.amount}'
