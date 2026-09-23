import logging
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from django.conf import settings
from django.db import connection, transaction
from django.db.models import Sum
from django.utils import timezone
from catalog.models import Product, StoreSettings
from .cart import CheckoutError, normalize_cart, quote, verify_quote
from .models import Order, OrderItem, OrderAudit, Refund
from . import paypal

logger = logging.getLogger(__name__)
S = Order.Status
RELEASABLE = [S.PENDING, S.APPROVAL]


def audit(order, action, actor=None):
    OrderAudit.objects.create(order=order, action=action, actor=actor)


def checkout_requirements(store=None):
    store = store or StoreSettings.objects.get(pk=1)
    return {
        'database': connection.vendor == 'postgresql',
        'shipping': store.shipping_configured,
        'paypal': paypal.configured(),
        'owner_switch': store.checkout_enabled,
    }


def ready(store=None):
    return all(checkout_requirements(store).values())


@transaction.atomic
def reserve_order(raw_cart, owner_key, checkout_key, token, data):
    if connection.vendor != 'postgresql':
        raise CheckoutError('Checkout requires the PostgreSQL database.')
    # Serialize settings/checkout creation to make repeated checkout submissions idempotent.
    store = StoreSettings.objects.select_for_update().get(pk=1)
    previous = Order.objects.filter(checkout_key=checkout_key, owner_key=owner_key).first()
    if previous:
        return previous
    if not ready(store):
        raise CheckoutError('Payment checkout is not open yet. Your cart is saved.')
    cart = normalize_cart(raw_cart)
    if not cart:
        raise CheckoutError('Your cart is empty.')
    # Use ordered locks without nullable joins (supported by PostgreSQL).
    products = list(Product.objects.select_for_update().filter(pk__in=cart).order_by('pk'))
    current = quote(cart, store, products)
    verify_quote(token, current)
    from .forms import US_STATES
    address = data.get('address', {})
    if address.get('country_code') != 'US' or address.get('admin_area_1') not in dict(US_STATES):
        raise CheckoutError('Shipping is currently available to the 50 US states and Washington, DC only.')
    order = Order.objects.create(checkout_key=checkout_key, owner_key=owner_key,
        name=data['name'], email=data['email'], phone=data.get('phone',''), notes=data.get('notes',''), address=address,
        subtotal=current['subtotal'], shipping=current['shipping'], tax=current['tax'], total=current['total'],
        tax_rate=store.tax_rate, tax_shipping=store.tax_shipping, shipping_mode=store.shipping_mode,
        shipping_rate=store.shipping_rate, reserved_until=timezone.now() + timedelta(minutes=settings.RESERVATION_MINUTES))
    for line in current['lines']:
        product = line['product']
        OrderItem.objects.create(order=order, product=product, name=product.name, rod_id=product.rod_id,
            serial_number=product.serial_number, design=str(product.design_theme) if product.design_theme_id else '',
            quantity=line['quantity'], price=product.price, unit_cost=product.build_cost,
            line_total=line['line_total'])
        product.reserved_quantity += line['quantity']
        product.save(update_fields=['reserved_quantity'])
    audit(order, 'Inventory reserved; server-calculated amounts saved')
    return order


def release_locked(order, status):
    if order.reservation_active:
        lines = list(order.items.all())
        products = {p.pk: p for p in Product.objects.select_for_update().filter(pk__in=[l.product_id for l in lines]).order_by('pk')}
        for line in lines:
            product = products[line.product_id]
            if product.reserved_quantity < line.quantity:
                raise CheckoutError('Inventory requires owner review.')
            product.reserved_quantity -= line.quantity
            product.save(update_fields=['reserved_quantity'])
        order.reservation_active = False
    order.status = status
    order.save(update_fields=['status', 'reservation_active', 'updated_at'])
    audit(order, f'Reservation released: {status}')


@transaction.atomic
def cancel_order(order_id, owner_key):
    order = Order.objects.select_for_update().get(pk=order_id, owner_key=owner_key)
    if order.status not in RELEASABLE:
        raise CheckoutError('This order cannot be cancelled while payment is being verified or after payment.')
    release_locked(order, S.CANCELLED)
    return order


def expire_reservations():
    count = 0
    ids = Order.objects.filter(status__in=RELEASABLE, reserved_until__lt=timezone.now(), reservation_active=True).values_list('pk', flat=True)
    for order_id in list(ids):
        with transaction.atomic():
            order = Order.objects.select_for_update().get(pk=order_id)
            if order.status in RELEASABLE and order.reserved_until < timezone.now() and order.reservation_active:
                release_locked(order, S.EXPIRED)
                count += 1
    return count


def mark_review(order, code):
    order.status = S.REVIEW
    order.error_code = code
    order.save(update_fields=['status', 'error_code', 'updated_at'])
    audit(order, code)
    logger.warning('Order %s requires review: %s', order.pk, code)


def begin_payment(order_id, owner_key):
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id, owner_key=owner_key)
        if not ready():
            raise CheckoutError('PayPal checkout is not enabled.')
        if order.status == S.APPROVAL and order.reserved_until >= timezone.now():
            return order.approval_url
        if order.status not in [S.PENDING, S.CREATING]:
            raise CheckoutError('This order is not awaiting payment setup.')
        if order.status == S.PENDING and order.reserved_until < timezone.now():
            release_locked(order, S.EXPIRED)
            return None
        # Do not repeat a potentially-successful create outside PayPal's default idempotency window.
        if order.status == S.CREATING and timezone.now() - order.created_at > timedelta(hours=5):
            mark_review(order, 'CREATE_RECONCILIATION_REQUIRED')
            return None
        order.status = S.CREATING
        order.save(update_fields=['status', 'updated_at'])
    try:
        result = paypal.create(order)
        payment_id = paypal.provider_id(result.get('id'))
        url = paypal.approval_link(result)
    except paypal.PayPalError:
        logger.warning('PayPal create unconfirmed for order %s', order.pk)
        raise
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id)
        if order.status == S.CREATING:
            order.paypal_order_id = payment_id
            order.approval_url = url
            order.status = S.APPROVAL
            order.reserved_until = timezone.now() + timedelta(minutes=settings.RESERVATION_MINUTES)
            order.save(update_fields=['paypal_order_id', 'approval_url', 'status', 'reserved_until', 'updated_at'])
            audit(order, 'PayPal sandbox order created')
        return order.approval_url


def validated_unit(order, data, expected_status):
    """Check the saved merchant, amount and address before charging as well as after."""
    try:
        if data['id'] != order.paypal_order_id or data['status'] != expected_status:
            raise ValueError
        units = data['purchase_units']
        if len(units) != 1:
            raise ValueError
        unit = units[0]
        if unit['custom_id'] != str(order.pk) or unit['payee']['merchant_id'] != settings.PAYPAL_MERCHANT_ID:
            raise ValueError
        if unit['amount']['currency_code'] != 'USD' or Decimal(unit['amount']['value']) != order.total:
            raise ValueError
        remote_address = unit['shipping']['address']
        normalize = lambda s: ' '.join(str(s).upper().split())
        for key, value in order.address.items():
            if normalize(remote_address.get(key, '')) != normalize(value):
                raise ValueError
        return unit
    except (KeyError, ValueError, TypeError, InvalidOperation):
        raise CheckoutError('PAYMENT_VERIFICATION_MISMATCH') from None


def validated_capture(order, data):
    """Validate identifiers, merchant, saved address, full amounts and capture currency."""
    unit = validated_unit(order, data, 'COMPLETED')
    try:
        captures = unit['payments']['captures']
        if len(captures) != 1:
            raise ValueError
        capture = captures[0]
        if capture['status'] not in ['COMPLETED', 'PARTIALLY_REFUNDED', 'REFUNDED']:
            raise ValueError
        if capture['amount']['currency_code'] != 'USD' or Decimal(capture['amount']['value']) != order.total:
            raise ValueError
        if not capture.get('final_capture', False):
            raise ValueError
        return paypal.provider_id(capture['id'])
    except (KeyError, ValueError, TypeError, InvalidOperation, paypal.PayPalError):
        raise CheckoutError('PAYMENT_VERIFICATION_MISMATCH') from None


@transaction.atomic
def settle_order(order_id, data):
    order = Order.objects.select_for_update().get(pk=order_id)
    if order.payment_confirmed:
        return order
    try:
        capture_id = validated_capture(order, data)
    except CheckoutError:
        mark_review(order, 'PAYMENT_VERIFICATION_MISMATCH')
        return order
    if not order.reservation_active or order.status not in [S.CAPTURING, S.APPROVAL, S.CREATING, S.REVIEW]:
        mark_review(order, 'PAYMENT_WITHOUT_ACTIVE_RESERVATION')
        return order
    lines = list(order.items.all())
    products = {p.pk: p for p in Product.objects.select_for_update().filter(pk__in=[l.product_id for l in lines]).order_by('pk')}
    for line in lines:
        p = products[line.product_id]
        if p.quantity < line.quantity or p.reserved_quantity < line.quantity:
            mark_review(order, 'INVENTORY_VERIFICATION_MISMATCH')
            return order
    # Check every item before mutating any inventory.
    for line in lines:
        p = products[line.product_id]
        p.quantity -= line.quantity
        p.reserved_quantity -= line.quantity
        if p.quantity == 0:
            p.status = Product.Status.SOLD
            p.show_in_gallery = True
        p.save(update_fields=['quantity', 'reserved_quantity', 'status', 'show_in_gallery', 'updated_at'])
    order.capture_id = capture_id
    order.status = S.PAID
    order.fulfillment_status = Order.FulfillmentStatus.NEEDS_PACKING
    order.paid_at = timezone.now()
    order.reservation_active = False
    order.error_code = ''
    order.save(update_fields=['capture_id', 'status', 'fulfillment_status', 'paid_at', 'reservation_active', 'error_code', 'updated_at'])
    audit(order, 'Payment verified; stock sold; ready for packing')
    from .notifications import send_order_notifications
    transaction.on_commit(lambda: send_order_notifications(order.pk))
    return order


def capture_payment(order_id, owner_key):
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order_id, owner_key=owner_key)
        if order.payment_confirmed:
            return order
        if order.status not in [S.APPROVAL, S.CAPTURING] or not order.paypal_order_id:
            raise CheckoutError('This order is not ready for payment confirmation.')
        if order.status == S.APPROVAL and order.reserved_until < timezone.now():
            release_locked(order, S.EXPIRED)
            return order
        # Once capture starts, no expiration/cancellation can release inventory on a timeout.
        order.status = S.CAPTURING
        order.save(update_fields=['status', 'updated_at'])
        audit(order, 'Server payment confirmation started')
    remote = paypal.get_order(order.paypal_order_id)
    if remote.get('status') == 'COMPLETED':
        return settle_order(order.pk, remote)
    if remote.get('status') != 'APPROVED':
        raise CheckoutError('PayPal has not confirmed approval yet. Your items remain held while payment is checked.')
    try:
        validated_unit(order, remote, 'APPROVED')
    except CheckoutError:
        with transaction.atomic():
            locked = Order.objects.select_for_update().get(pk=order.pk)
            if not locked.payment_confirmed:
                mark_review(locked, 'PRE_CAPTURE_VERIFICATION_MISMATCH')
        raise
    # A fresh read above makes retry safe after a lost capture response; stable request ID adds protection.
    if timezone.now() - order.created_at > timedelta(hours=5):
        with transaction.atomic():
            locked = Order.objects.select_for_update().get(pk=order.pk)
            if not locked.payment_confirmed:
                mark_review(locked, 'CAPTURE_RECONCILIATION_REQUIRED')
        return Order.objects.get(pk=order.pk)
    paypal.capture(order)
    # Never trust the browser or only a redirect/capture response; retrieve the complete order server-side.
    remote = paypal.get_order(order.paypal_order_id)
    if remote.get('status') != 'COMPLETED':
        raise CheckoutError('Payment is still pending at PayPal. Please check the status again shortly.')
    return settle_order(order.pk, remote)


@transaction.atomic
def record_refund(order_id, data):
    order = Order.objects.select_for_update().get(pk=order_id)
    try:
        refund_id = paypal.provider_id(data['id'])
        amount = Decimal(data['amount']['value'])
        if data['amount']['currency_code'] != 'USD' or not amount.is_finite() or amount <= 0 or amount > order.total:
            raise ValueError
        # Refund resource must link to this order's capture, not merely a matching amount.
        capture_paths = [link.get('href', '').split('?')[0].rstrip('/').split('/')[-1]
                         for link in data.get('links', []) if link.get('rel') == 'up']
        if order.capture_id not in capture_paths or not order.payment_confirmed:
            raise ValueError
        status = data['status']
        if status not in ['COMPLETED', 'PENDING', 'FAILED', 'CANCELLED']:
            raise ValueError
    except (KeyError, ValueError, TypeError, InvalidOperation):
        raise CheckoutError('Refund could not be matched to the paid order.') from None
    previous = Refund.objects.filter(paypal_id=refund_id).first()
    if previous and previous.order_id != order.pk:
        raise CheckoutError('Refund belongs to another order.')
    if previous and previous.status == 'COMPLETED':
        return order
    refund, _ = Refund.objects.update_or_create(paypal_id=refund_id,
        defaults={'order': order, 'amount': amount, 'status': status})
    if status == 'COMPLETED' and not refund.completed_at:
        refund.completed_at = timezone.now()
        refund.save(update_fields=['completed_at', 'updated_at'])
    total = order.refunds.filter(status='COMPLETED').aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    if total > order.total:
        raise CheckoutError('Refund total exceeds the order.')
    order.refunded_amount = total
    if total:
        order.status = S.REFUNDED if total == order.total else S.PARTIAL_REFUND
    order.save(update_fields=['status', 'refunded_amount', 'updated_at'])
    audit(order, f'Refund synchronized: {status}')
    # A refund never automatically relists a shipped/damaged one-of-one rod.
    return order
