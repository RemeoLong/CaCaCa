import logging
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from catalog.models import StoreSettings
from .models import NotificationLog, Order

logger = logging.getLogger(__name__)


def _send(order, kind, recipient, subject, text_template, html_template):
    if not recipient:
        NotificationLog.objects.create(order=order, kind=kind, status=NotificationLog.Status.SKIPPED,
            error_code='RECIPIENT_NOT_CONFIGURED')
        return False
    if order.notifications.filter(kind=kind, status=NotificationLog.Status.SENT).exists():
        return True
    context = {'order': order, 'store': StoreSettings.objects.get(pk=1),
               'site_url': settings.PUBLIC_BASE_URL}
    try:
        message = EmailMultiAlternatives(subject, render_to_string(text_template, context),
            settings.DEFAULT_FROM_EMAIL, [recipient])
        message.attach_alternative(render_to_string(html_template, context), 'text/html')
        sent = message.send(fail_silently=False)
        if sent != 1:
            raise RuntimeError('Email backend did not confirm delivery.')
        NotificationLog.objects.create(order=order, kind=kind, status=NotificationLog.Status.SENT,
            recipient=recipient)
        return True
    except Exception as exc:
        error_code = type(exc).__name__[:80]
        NotificationLog.objects.create(order=order, kind=kind, status=NotificationLog.Status.FAILED,
            recipient=recipient, error_code=error_code)
        logger.exception('Transactional email failed for order %s (%s)', order.pk, kind)
        return False


def send_customer_confirmation(order_id):
    order = Order.objects.prefetch_related('items').get(pk=order_id)
    return _send(order, NotificationLog.Kind.CUSTOMER_CONFIRMATION, order.email,
        f'CaCaCa order confirmation — {order.number}', 'email/order_confirmation.txt', 'email/order_confirmation.html')


def send_owner_new_order(order_id):
    order = Order.objects.prefetch_related('items').get(pk=order_id)
    store = StoreSettings.objects.get(pk=1)
    return _send(order, NotificationLog.Kind.OWNER_NEW_ORDER, store.contact_email,
        f'New paid order — {order.number}', 'email/owner_new_order.txt', 'email/owner_new_order.html')


def send_order_notifications(order_id):
    send_customer_confirmation(order_id)
    send_owner_new_order(order_id)


def send_shipping_confirmation(order_id):
    order = Order.objects.prefetch_related('items').get(pk=order_id)
    return _send(order, NotificationLog.Kind.SHIPPING_CONFIRMATION, order.email,
        f'Your CaCaCa rod has shipped — {order.number}', 'email/shipping_confirmation.txt', 'email/shipping_confirmation.html')
