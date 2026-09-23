from decimal import Decimal, ROUND_HALF_UP
from django.core import signing
from catalog.models import Product, StoreSettings


class CheckoutError(Exception):
    pass


def money(value):
    return Decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def normalize_cart(raw):
    if not isinstance(raw, dict) or len(raw) > 30:
        raise CheckoutError('Please rebuild your cart.')
    result = {}
    for key, quantity in raw.items():
        if not str(key).isdigit() or len(str(key)) > 12 or type(quantity) is not int or not 1 <= quantity <= 20:
            raise CheckoutError('Invalid cart quantity.')
        result[str(int(key))] = quantity
    return result


def quote(raw, store=None, products=None):
    cart = normalize_cart(raw)
    store = store or StoreSettings.objects.get(pk=1)
    if products is None:
        products = list(Product.objects.filter(pk__in=cart).select_related('design_theme').prefetch_related('images'))
    lookup = {str(p.pk): p for p in products}
    if set(lookup) != set(cart):
        raise CheckoutError('An item in your cart is no longer listed. Remove it to continue.')
    lines = []
    for key in sorted(cart, key=int):
        product = lookup[key]
        quantity = cart[key]
        if not product.is_published or not product.is_available or quantity > product.quantity - product.reserved_quantity:
            raise CheckoutError(f'{product.name} is unavailable or reserved by another customer. Remove it to continue.')
        if product.build_type == Product.BuildType.UNIQUE and quantity != 1:
            raise CheckoutError('One-of-one rods have a maximum quantity of one.')
        lines.append({'product': product, 'quantity': quantity, 'line_total': money(product.price * quantity)})
    subtotal = sum((line['line_total'] for line in lines), Decimal('0.00'))
    count = sum(cart.values())
    shipping = money(store.shipping_rate * (count if store.shipping_mode == 'per_rod' else (1 if count else 0)))
    taxable = subtotal + (shipping if store.tax_shipping else Decimal('0.00'))
    tax = money(taxable * store.tax_rate / 100)
    total = money(subtotal + shipping + tax)
    snapshot = {'lines': [[line['product'].pk, str(line['product'].price), line['quantity']] for line in lines],
                'shipping': str(shipping), 'tax': str(tax), 'total': str(total), 'tax_rate': str(store.tax_rate),
                'tax_shipping': store.tax_shipping, 'shipping_mode': store.shipping_mode, 'shipping_rate': str(store.shipping_rate)}
    return {'lines': lines, 'subtotal': subtotal, 'shipping': shipping, 'tax': tax, 'total': total, 'count': count,
            'store': store, 'snapshot': snapshot, 'token': signing.dumps(snapshot, salt='checkout-quote')}


def verify_quote(token, current):
    try:
        previous = signing.loads(token, salt='checkout-quote', max_age=1800)
    except signing.BadSignature:
        raise CheckoutError('Your price review expired. Please review the refreshed totals and try again.')
    if previous != current['snapshot']:
        raise CheckoutError('Pricing or shipping changed. Please review the refreshed totals before continuing.')
