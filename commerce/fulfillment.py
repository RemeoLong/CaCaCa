from django.db import transaction
from django.utils import timezone
from catalog.models import Product
from .cart import CheckoutError
from .models import Order
from .notifications import send_shipping_confirmation
from .services import audit


@transaction.atomic
def update_fulfillment(order_id, action, shipping=None, actor=None):
    order = Order.objects.select_for_update().get(pk=order_id)
    if not order.payment_confirmed:
        raise CheckoutError('Only a verified paid order can be fulfilled.')
    if order.status == Order.Status.REFUNDED:
        raise CheckoutError('A fully refunded order cannot be fulfilled.')
    now = timezone.now()
    if action == 'pack':
        if order.fulfillment_status != Order.FulfillmentStatus.NEEDS_PACKING:
            raise CheckoutError('This order is not waiting to be packed.')
        order.fulfillment_status = Order.FulfillmentStatus.PACKED
        order.packed_at = now
        fields = ['fulfillment_status', 'packed_at', 'updated_at']
        label = 'Order marked packed'
    elif action == 'ship':
        if order.fulfillment_status != Order.FulfillmentStatus.PACKED or not shipping:
            raise CheckoutError('Pack the order and enter shipping details first.')
        for field in ['carrier', 'carrier_other', 'tracking_number', 'actual_shipping_cost']:
            setattr(order, field, shipping.get(field))
        order.fulfillment_status = Order.FulfillmentStatus.SHIPPED
        order.shipped_at = now
        fields = ['carrier', 'carrier_other', 'tracking_number', 'actual_shipping_cost',
                  'fulfillment_status', 'shipped_at', 'updated_at']
        label = 'Order marked shipped; tracking saved'
        product_ids = order.items.values_list('product_id', flat=True)
        Product.objects.select_for_update().filter(pk__in=product_ids, status=Product.Status.SOLD).update(
            status=Product.Status.SHIPPED)
    elif action == 'deliver':
        if order.fulfillment_status != Order.FulfillmentStatus.SHIPPED:
            raise CheckoutError('Only a shipped order can be marked delivered.')
        order.fulfillment_status = Order.FulfillmentStatus.DELIVERED
        order.delivered_at = now
        fields = ['fulfillment_status', 'delivered_at', 'updated_at']
        label = 'Order marked delivered'
    elif action == 'complete':
        if order.fulfillment_status != Order.FulfillmentStatus.DELIVERED:
            raise CheckoutError('Mark the order delivered before completing it.')
        order.fulfillment_status = Order.FulfillmentStatus.COMPLETED
        order.completed_at = now
        fields = ['fulfillment_status', 'completed_at', 'updated_at']
        label = 'Order completed'
    else:
        raise CheckoutError('Unknown fulfillment action.')
    order.save(update_fields=fields)
    audit(order, label, actor)
    if action == 'ship':
        transaction.on_commit(lambda: send_shipping_confirmation(order.pk))
    return order
