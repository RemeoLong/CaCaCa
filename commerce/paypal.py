"""Minimal server-only sandbox adapter; secrets and response bodies are never logged."""
import base64
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler
from django.conf import settings
from django.views.decorators.debug import sensitive_variables


class PayPalError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def configured():
    return all([settings.PAYPAL_CLIENT_ID, settings.PAYPAL_CLIENT_SECRET,
                settings.PAYPAL_WEBHOOK_ID, settings.PAYPAL_MERCHANT_ID])


def provider_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Z0-9]{1,64}', value):
        raise PayPalError('Invalid payment reference.')
    return value


@sensitive_variables()
def transport(method, path, data=None, headers=None):
    request = Request(settings.PAYPAL_API_BASE + path, data=data, method=method, headers=headers or {})
    try:
        with build_opener(NoRedirect()).open(request, timeout=20) as response:
            result = json.loads(response.read(2 * 1024 * 1024))
            if not isinstance(result, dict):
                raise PayPalError('Invalid payment provider response.')
            return result
    except (HTTPError, URLError, TimeoutError, ValueError, OSError):
        raise PayPalError('PayPal could not confirm this operation. Please retry or check the order status.') from None


@sensitive_variables()
def api(method, path, payload=None, request_id=None):
    if not configured():
        raise PayPalError('PayPal sandbox is not configured yet.')
    basic = base64.b64encode(f'{settings.PAYPAL_CLIENT_ID}:{settings.PAYPAL_CLIENT_SECRET}'.encode()).decode()
    auth = transport('POST', '/v1/oauth2/token', b'grant_type=client_credentials', {
        'Authorization': f'Basic {basic}', 'Content-Type': 'application/x-www-form-urlencoded'})
    token = auth.get('access_token')
    if not token:
        raise PayPalError('PayPal authentication failed.')
    headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json', 'Prefer': 'return=representation'}
    if request_id:
        headers['PayPal-Request-Id'] = request_id
    return transport(method, path, json.dumps(payload).encode() if payload is not None else None, headers)


def create(order):
    value = lambda amount: {'currency_code': 'USD', 'value': str(amount)}
    payload = {'intent': 'CAPTURE', 'purchase_units': [{
        'reference_id': str(order.pk), 'custom_id': str(order.pk), 'invoice_id': order.number,
        'payee': {'merchant_id': settings.PAYPAL_MERCHANT_ID},
        'amount': value(order.total) | {'breakdown': {'item_total': value(order.subtotal), 'shipping': value(order.shipping), 'tax_total': value(order.tax)}},
        'items': [{'name': line.name[:127], 'sku': line.rod_id, 'quantity': str(line.quantity),
                   'unit_amount': value(line.price), 'category': 'PHYSICAL_GOODS'} for line in order.items.all()],
        'shipping': {'name': {'full_name': order.name}, 'address': order.address},
    }], 'payment_source': {'paypal': {'experience_context': {
        'brand_name': 'CaCaCa', 'shipping_preference': 'SET_PROVIDED_ADDRESS', 'user_action': 'CONTINUE',
        'return_url': settings.PUBLIC_BASE_URL + order.get_absolute_url(),
        'cancel_url': settings.PUBLIC_BASE_URL + order.get_absolute_url() + '?paypal_cancelled=1',
    }}}}
    return api('POST', '/v2/checkout/orders', payload, f'{order.pk}-c')


def get_order(paypal_order_id):
    return api('GET', '/v2/checkout/orders/' + provider_id(paypal_order_id))


def capture(order):
    return api('POST', '/v2/checkout/orders/' + provider_id(order.paypal_order_id) + '/capture', {}, f'{order.pk}-p')


def approval_link(data):
    for link in data.get('links', []):
        if link.get('rel') in ['payer-action', 'approve']:
            url = link.get('href', '')
            parsed = urlparse(url)
            if parsed.scheme == 'https' and parsed.hostname in ['www.sandbox.paypal.com', 'sandbox.paypal.com'] and not parsed.username:
                return url
    raise PayPalError('PayPal did not provide a valid sandbox approval link.')


def verify_webhook(headers, event):
    fields = {'auth_algo': 'PAYPAL-AUTH-ALGO', 'cert_url': 'PAYPAL-CERT-URL',
              'transmission_id': 'PAYPAL-TRANSMISSION-ID', 'transmission_sig': 'PAYPAL-TRANSMISSION-SIG',
              'transmission_time': 'PAYPAL-TRANSMISSION-TIME'}
    payload = {key: headers.get(header, '') for key, header in fields.items()}
    if not all(payload.values()):
        return False
    payload.update(webhook_id=settings.PAYPAL_WEBHOOK_ID, webhook_event=event)
    return api('POST', '/v1/notifications/verify-webhook-signature', payload).get('verification_status') == 'SUCCESS'
