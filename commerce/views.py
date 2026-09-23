import json
import logging
import uuid
import csv
from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlencode, urlparse
from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import F, Sum
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from catalog.models import CustomBuildRequest, Product, StoreSettings
from catalog.forms import OwnerInventoryForm, OwnerRodForm, OwnerStoreForm, OwnerTackleForm, listing_gaps
from .cart import CheckoutError, normalize_cart, quote
from .forms import BusinessExpenseForm, CheckoutForm, FulfillmentActionForm, OwnerMessageStatusForm, ReportFilterForm, ShippingForm
from .models import BusinessExpense, NotificationLog, Order, PaymentEvent
from . import fulfillment, notifications, paypal, reporting, services

logger = logging.getLogger(__name__)


@require_GET
def owner_manifest(request):
    """Public install metadata; the owner pages still require staff login."""
    data = {
        'id': '/owner/', 'name': 'CaCaCa Owner', 'short_name': 'CaCaCa Owner',
        'description': 'Manage CaCaCa rods, tackle, photos, inventory, and shipping.',
        'start_url': '/owner/', 'scope': '/owner/', 'display': 'standalone',
        'background_color': '#082d3f', 'theme_color': '#082d3f',
        'prefer_related_applications': False,
        'icons': [
            {'src': static('brand/owner-app-192.png'), 'sizes': '192x192', 'type': 'image/png'},
            {'src': static('brand/owner-app-512.png'), 'sizes': '512x512', 'type': 'image/png'},
        ],
        'shortcuts': [
            {'name': 'Add a rod', 'url': '/owner/rods/add/'},
            {'name': 'Add tackle', 'url': '/owner/tackle/add/'},
            {'name': 'Rod inventory', 'url': '/owner/rods/'},
            {'name': 'Shipping', 'url': '/owner/shipping/'},
        ],
    }
    response = JsonResponse(data, content_type='application/manifest+json')
    response['Cache-Control'] = 'public, max-age=3600'
    return response


@require_GET
def owner_service_worker(request):
    script = (settings.BASE_DIR / 'static' / 'js' / 'owner-sw.js').read_text(encoding='utf-8')
    response = HttpResponse(script, content_type='text/javascript')
    response['Cache-Control'] = 'no-store'
    response['Service-Worker-Allowed'] = '/owner/'
    return response


def owner_key(request):
    if not request.session.get('checkout_owner'):
        request.session['checkout_owner'] = uuid.uuid4().hex
    return request.session['checkout_owner']


def owned_order(request, order_id):
    return get_object_or_404(Order.objects.prefetch_related('items'), pk=order_id, owner_key=owner_key(request))


@require_GET
@never_cache
def cart_view(request):
    services.expire_reservations()
    cart = request.session.get('cart', {})
    try:
        summary = quote(cart)
        error = ''
    except CheckoutError as exc:
        summary, error = None, str(exc)
    products = Product.objects.filter(pk__in=[key for key in cart if str(key).isdigit() and len(str(key)) <= 12]).prefetch_related('images') if isinstance(cart, dict) else []
    rows = [{'product': p, 'quantity': cart.get(str(p.pk), 1)} for p in products]
    return render(request, 'commerce/cart.html', {'summary': summary, 'cart_rows': rows, 'cart_error': error})


@require_POST
def cart_change(request, product_id):
    if any(key in request.POST for key in ['price', 'total', 'subtotal', 'tax', 'shipping']):
        return HttpResponse('Prices must be calculated by the server.', status=400)
    try:
        cart = normalize_cart(request.session.get('cart', {}))
        quantity = int(request.POST.get('quantity', '1'))
        if quantity == 0:
            cart.pop(str(product_id), None)
        else:
            product = get_object_or_404(Product, pk=product_id, is_published=True)
            if not 1 <= quantity <= 20 or not product.is_available or quantity > product.quantity - product.reserved_quantity:
                raise CheckoutError('That quantity is not available.')
            if product.build_type == 'unique' and quantity != 1:
                raise CheckoutError('This item is one of one.')
            cart[str(product.pk)] = quantity
            if len(cart) > 30:
                raise CheckoutError('The cart is full.')
        request.session['cart'] = cart
        request.session.pop('checkout_key', None)
    except (ValueError, CheckoutError) as exc:
        messages.error(request, str(exc) if isinstance(exc, CheckoutError) else 'Enter a valid quantity.')
    return redirect('cart')


@require_POST
def cart_clear(request):
    request.session['cart'] = {}
    request.session.pop('checkout_key', None)
    return redirect('cart')


@require_http_methods(['GET', 'POST'])
@never_cache
def checkout(request):
    services.expire_reservations()
    who = owner_key(request)
    request.session.setdefault('checkout_key', str(uuid.uuid4()))
    key = request.session['checkout_key']
    # Repeat submissions/refreshes return the original order instead of reserving a second copy.
    existing = Order.objects.filter(checkout_key=key, owner_key=who).first()
    if existing:
        return redirect(existing)
    try:
        summary = quote(request.session.get('cart', {}))
    except CheckoutError as exc:
        messages.error(request, str(exc))
        return redirect('cart')
    if not summary['lines']:
        return redirect('cart')
    form = CheckoutForm(request.POST if request.method == 'POST' else None,
                        initial={'quote_token': summary['token'], 'checkout_key': key})
    if request.method == 'POST' and form.is_valid():
        if str(form.cleaned_data['checkout_key']) != key:
            form.add_error(None, 'This checkout has changed. Reload the page before continuing.')
        else:
            try:
                order = services.reserve_order(request.session.get('cart', {}), who,
                    form.cleaned_data['checkout_key'], form.cleaned_data['quote_token'], form.order_data())
                return redirect(order)
            except CheckoutError as exc:
                form.add_error(None, str(exc))
        # Bind the refreshed server quote for the next explicit submission; keep address fields.
        refreshed = form.data.copy()
        refreshed['quote_token'] = summary['token']
        form.data = refreshed
    return render(request, 'commerce/checkout.html', {'form': form, 'summary': summary, 'payment_ready': services.ready()})


@require_GET
@never_cache
def order_detail(request, order_id):
    order = owned_order(request, order_id)
    if order.payment_confirmed:
        cart = request.session.get('cart', {})
        for line in order.items.all():
            cart.pop(str(line.product_id), None)
        request.session['cart'] = cart
    # PayPal's return query string is only navigation. It never marks an order paid.
    return render(request, 'commerce/order.html', {'order': order,
        'can_cancel': order.status in services.RELEASABLE,
        'can_begin': order.status in [Order.Status.PENDING, Order.Status.CREATING],
        'can_capture': order.status in [Order.Status.APPROVAL, Order.Status.CAPTURING]})


@require_GET
@never_cache
def my_orders(request):
    return render(request, 'commerce/orders.html', {'orders': Order.objects.filter(owner_key=owner_key(request))[:50]})


@require_GET
@never_cache
def receipt(request, order_id):
    order = owned_order(request, order_id)
    if not order.payment_confirmed:
        raise Http404
    return render(request, 'commerce/receipt.html', {'order': order, 'store': StoreSettings.objects.get(pk=1)})


@staff_member_required(login_url='admin:login')
@require_GET
@never_cache
def owner_dashboard(request):
    paid_statuses = [Order.Status.PAID, Order.Status.PARTIAL_REFUND, Order.Status.REFUNDED]
    today = timezone.localdate()
    orders = Order.objects.filter(status__in=paid_statuses).prefetch_related('items')
    store = StoreSettings.objects.get(pk=1)
    draft_rods = Product.objects.filter(item_type=Product.ItemType.ROD, status=Product.Status.DRAFT).select_related(
        'specification').prefetch_related('images').order_by('-updated_at')[:6]
    context = {
        'orders_to_pack': orders.filter(fulfillment_status=Order.FulfillmentStatus.NEEDS_PACKING),
        'packed_orders': orders.filter(fulfillment_status=Order.FulfillmentStatus.PACKED),
        'recent_orders': orders[:12],
        'sales_today': orders.filter(paid_at__date=today).aggregate(value=Sum('total'))['value'] or 0,
        'available_rods': Product.objects.filter(item_type=Product.ItemType.ROD, is_published=True, price__isnull=False,
                                                 status__in=[Product.Status.AVAILABLE, Product.Status.READY],
                                                 quantity__gt=F('reserved_quantity')).count(),
        'preview_rod_count': Product.objects.filter(item_type=Product.ItemType.ROD, status=Product.Status.DRAFT, show_in_gallery=True).count(),
        'private_draft_count': Product.objects.filter(item_type=Product.ItemType.ROD, status=Product.Status.DRAFT, show_in_gallery=False).count(),
        'tackle_count': Product.objects.filter(item_type=Product.ItemType.TACKLE, is_published=True,
            status__in=[Product.Status.AVAILABLE, Product.Status.READY]).count(),
        'draft_rows': [{'rod': rod, 'gaps': listing_gaps(rod)} for rod in draft_rods],
        'sold_rods': Product.objects.filter(item_type=Product.ItemType.ROD, status__in=[Product.Status.SOLD, Product.Status.SHIPPED]).prefetch_related('images').order_by('-updated_at')[:8],
        'sold_rod_count': Product.objects.filter(item_type=Product.ItemType.ROD, status__in=[Product.Status.SOLD, Product.Status.SHIPPED]).count(),
        'open_requests': CustomBuildRequest.objects.exclude(status='closed').count(),
        'store': store,
        'checkout_ready': services.ready(store),
    }
    context['shipping_action_count'] = context['orders_to_pack'].count() + context['packed_orders'].count()
    return render(request, 'owner/dashboard.html', context)


@staff_member_required(login_url='admin:login')
@require_http_methods(['GET', 'POST'])
def owner_rod_add(request):
    form = OwnerRodForm(request.POST, request.FILES) if request.method == 'POST' else OwnerRodForm()
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            rod = form.save()
        messages.success(request, f'{rod.name} was added. You can review its photos and details below.')
        return redirect('owner_rod_edit', product_id=rod.pk)
    return render(request, 'owner/rod_form.html', {'form': form, 'rod': None})


@staff_member_required(login_url='admin:login')
@require_http_methods(['GET', 'POST'])
def owner_rod_edit(request, product_id):
    if request.method == 'GET':
        rod = get_object_or_404(Product.objects.filter(item_type=Product.ItemType.ROD).select_related('specification').prefetch_related('images'), pk=product_id)
        return render(request, 'owner/rod_form.html', {'form': OwnerRodForm(instance=rod), 'rod': rod,
                                                       'listing_gaps': listing_gaps(rod)})
    else:
        with transaction.atomic():
            rod = get_object_or_404(Product.objects.filter(item_type=Product.ItemType.ROD).select_for_update(), pk=product_id)
            form = OwnerRodForm(request.POST, request.FILES, instance=rod)
            if form.is_valid():
                rod = form.save()
                remove_ids = [value for value in request.POST.getlist('remove_photos') if value.isdigit()]
                for photo in rod.images.filter(pk__in=remove_ids):
                    image_name = photo.image.name
                    storage = photo.image.storage
                    photo.delete()
                    transaction.on_commit(lambda name=image_name, store=storage: store.delete(name))
                chosen_cover = request.POST.get('cover_photo', '')
                if chosen_cover.isdigit():
                    photos = list(rod.images.all())
                    cover = next((photo for photo in photos if photo.pk == int(chosen_cover)), None)
                    if cover:
                        for position, photo in enumerate([cover] + [p for p in photos if p.pk != cover.pk]):
                            if photo.position != position:
                                photo.position = position
                                photo.save(update_fields=['position'])
                messages.success(request, f'{rod.name} was saved.')
                return redirect('owner_rod_edit', product_id=rod.pk)
        return render(request, 'owner/rod_form.html', {'form': form, 'rod': rod,
                                                       'listing_gaps': listing_gaps(rod)})


@staff_member_required(login_url='admin:login')
@require_GET
@never_cache
def owner_inventory(request):
    rods = list(Product.objects.filter(item_type=Product.ItemType.ROD).select_related('design_theme', 'specification').prefetch_related('images').order_by('-updated_at'))
    rows = [{'rod': rod, 'form': OwnerInventoryForm(instance=rod, prefix=f'rod-{rod.pk}'),
             'gaps': listing_gaps(rod)}
            for rod in rods]
    return render(request, 'owner/inventory.html', {
        'rows': [row for row in rows if row['rod'].status not in [Product.Status.SOLD, Product.Status.SHIPPED]],
        'sold_rows': [row for row in rows if row['rod'].status in [Product.Status.SOLD, Product.Status.SHIPPED]],
        'available_count': sum(1 for rod in rods if rod.is_available),
        'preview_count': sum(1 for rod in rods if rod.is_public_preview),
        'draft_count': sum(1 for rod in rods if rod.status == Product.Status.DRAFT and not rod.show_in_gallery),
        'sold_count': sum(1 for rod in rods if rod.status in [Product.Status.SOLD, Product.Status.SHIPPED]),
    })


@staff_member_required(login_url='admin:login')
@require_POST
def owner_inventory_update(request, product_id):
    with transaction.atomic():
        rod = get_object_or_404(Product.objects.filter(item_type=Product.ItemType.ROD).select_for_update(), pk=product_id)
        form = OwnerInventoryForm(request.POST, instance=rod, prefix=f'rod-{rod.pk}')
        if form.is_valid():
            form.save()
            messages.success(request, f'{rod.name} inventory was updated.')
        else:
            messages.error(request, f'{rod.name}: ' + ' '.join(
                str(error) for errors in form.errors.values() for error in errors))
    return redirect('owner_inventory')


@staff_member_required(login_url='admin:login')
@require_GET
@never_cache
def owner_tackle(request):
    items = Product.objects.filter(item_type=Product.ItemType.TACKLE).prefetch_related('images').order_by('-updated_at')
    return render(request, 'owner/tackle.html', {'items': items})


@staff_member_required(login_url='admin:login')
@require_http_methods(['GET', 'POST'])
def owner_tackle_add(request):
    form = OwnerTackleForm(request.POST, request.FILES) if request.method == 'POST' else OwnerTackleForm()
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            item = form.save()
        messages.success(request, f'{item.name} was added.')
        return redirect('owner_tackle_edit', product_id=item.pk)
    return render(request, 'owner/tackle_form.html', {'form': form, 'item': None})


@staff_member_required(login_url='admin:login')
@require_http_methods(['GET', 'POST'])
def owner_tackle_edit(request, product_id):
    if request.method == 'GET':
        item = get_object_or_404(Product.objects.filter(item_type=Product.ItemType.TACKLE).prefetch_related('images'), pk=product_id)
        return render(request, 'owner/tackle_form.html', {'form': OwnerTackleForm(instance=item), 'item': item})
    with transaction.atomic():
        item = get_object_or_404(Product.objects.filter(item_type=Product.ItemType.TACKLE).select_for_update(), pk=product_id)
        form = OwnerTackleForm(request.POST, request.FILES, instance=item)
        if form.is_valid():
            item = form.save()
            remove_ids = [value for value in request.POST.getlist('remove_photos') if value.isdigit()]
            for photo in item.images.filter(pk__in=remove_ids):
                image_name, storage = photo.image.name, photo.image.storage
                photo.delete()
                transaction.on_commit(lambda name=image_name, store=storage: store.delete(name))
            chosen_cover = request.POST.get('cover_photo', '')
            if chosen_cover.isdigit():
                photos = list(item.images.all())
                cover = next((photo for photo in photos if photo.pk == int(chosen_cover)), None)
                if cover:
                    for position, photo in enumerate([cover] + [p for p in photos if p.pk != cover.pk]):
                        if photo.position != position:
                            photo.position = position
                            photo.save(update_fields=['position'])
            messages.success(request, f'{item.name} was saved.')
            return redirect('owner_tackle_edit', product_id=item.pk)
    return render(request, 'owner/tackle_form.html', {'form': form, 'item': item})


@staff_member_required(login_url='admin:login')
@require_GET
@never_cache
def owner_messages(request):
    selected = request.GET.get('show', 'open')
    if selected not in ['open', 'closed', 'all']:
        selected = 'open'
    records = CustomBuildRequest.objects.select_related('product').order_by('-created_at')
    if selected == 'open':
        records = records.exclude(status='closed')
    elif selected == 'closed':
        records = records.filter(status='closed')
    return render(request, 'owner/messages.html', {
        'page_obj': Paginator(records, 20).get_page(request.GET.get('page')),
        'selected': selected,
        'new_count': CustomBuildRequest.objects.filter(status='received').count(),
        'open_count': CustomBuildRequest.objects.exclude(status='closed').count(),
        'closed_count': CustomBuildRequest.objects.filter(status='closed').count(),
    })


@staff_member_required(login_url='admin:login')
@require_http_methods(['GET', 'POST'])
@never_cache
def owner_message(request, message_id):
    if request.method == 'POST':
        with transaction.atomic():
            record = get_object_or_404(CustomBuildRequest.objects.select_for_update(), pk=message_id)
            form = OwnerMessageStatusForm(request.POST, instance=record)
            if form.is_valid():
                form.save()
                messages.success(request, 'Message status was saved. No reply was sent to the customer.')
                return redirect('owner_message', message_id=record.pk)
    else:
        record = get_object_or_404(CustomBuildRequest.objects.select_related('product'), pk=message_id)
        form = OwnerMessageStatusForm(instance=record)
    return render(request, 'owner/message_detail.html', {'record': record, 'form': form})


@staff_member_required(login_url='admin:login')
@require_GET
@never_cache
def owner_shipping(request):
    paid = Order.objects.filter(status__in=[Order.Status.PAID, Order.Status.PARTIAL_REFUND,
                                            Order.Status.REFUNDED]).prefetch_related('items')
    return render(request, 'owner/shipping.html', {
        'needs_packing': paid.filter(fulfillment_status=Order.FulfillmentStatus.NEEDS_PACKING),
        'packed': paid.filter(fulfillment_status=Order.FulfillmentStatus.PACKED),
        'shipped': paid.filter(fulfillment_status__in=[Order.FulfillmentStatus.SHIPPED,
                                                       Order.FulfillmentStatus.DELIVERED,
                                                       Order.FulfillmentStatus.COMPLETED])[:20],
        'store': StoreSettings.objects.get(pk=1),
    })


@staff_member_required(login_url='admin:login')
@require_http_methods(['GET', 'POST'])
def owner_settings(request):
    store = StoreSettings.objects.get(pk=1)
    form = OwnerStoreForm(request.POST if request.method == 'POST' else None, instance=store)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Store settings were saved.')
        return redirect('owner_settings')
    if request.method == 'POST':
        store.refresh_from_db()
    public_url = urlparse(settings.PUBLIC_BASE_URL)
    return render(request, 'owner/settings.html', {
        'form': form, 'store': store,
        'checkout_checks': services.checkout_requirements(store),
        'checkout_ready': services.ready(store),
        'available_item': Product.objects.filter(is_published=True, price__isnull=False,
            status__in=[Product.Status.AVAILABLE, Product.Status.READY],
            quantity__gt=F('reserved_quantity')).exists(),
        'public_https_url': public_url.scheme == 'https' and bool(public_url.hostname) and public_url.hostname not in
            ['localhost', '127.0.0.1', '::1'],
    })


def report_period(query):
    today = timezone.localdate()
    default = {'start': today.replace(day=1).isoformat(), 'end': today.isoformat()}
    form = ReportFilterForm(query if query.get('start') or query.get('end') else default)
    if not form.is_valid():
        return form, None, None
    return form, form.cleaned_data['start'], form.cleaned_data['end']


@staff_member_required(login_url='admin:login')
@require_GET
@never_cache
def owner_reports(request):
    form, start, end = report_period(request.GET)
    today = timezone.localdate()
    context = {
        'filter_form': form, 'expense_form': BusinessExpenseForm(),
        'preset_month': urlencode({'start': today.replace(day=1), 'end': today}),
        'preset_30': urlencode({'start': today - timedelta(days=29), 'end': today}),
        'preset_year': urlencode({'start': today.replace(month=1, day=1), 'end': today}),
    }
    if start:
        context.update({'report': reporting.report_data(start, end),
                        'inventory': reporting.inventory_data(), 'start': start, 'end': end,
                        'period_query': urlencode({'start': start, 'end': end})})
    return render(request, 'owner/reports.html', context)


@staff_member_required(login_url='admin:login')
@require_POST
def owner_expense_add(request):
    form = BusinessExpenseForm(request.POST)
    if form.is_valid():
        expense = form.save(commit=False)
        expense.created_by = request.user
        expense.save()
        messages.success(request, 'Expense added to the report.')
    else:
        messages.error(request, 'Review the expense details and try again.')
    period_form = ReportFilterForm({'start': request.POST.get('period_start'),
                                    'end': request.POST.get('period_end')})
    query = ''
    if period_form.is_valid():
        query = '?' + urlencode(period_form.cleaned_data)
    return redirect(reverse('owner_reports') + query)


def csv_value(value):
    text = '' if value is None else str(value)
    return "'" + text if text.startswith(('=', '+', '-', '@')) else text


def csv_response(name):
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="cacaca-{name}.csv"'
    response['Cache-Control'] = 'private, no-store'
    response.write('\ufeff')
    return response


@staff_member_required(login_url='admin:login')
@require_GET
def owner_report_export(request, kind):
    form, start, end = report_period(request.GET)
    if not start:
        return HttpResponse('Choose a valid reporting period.', status=400)
    report = reporting.report_data(start, end)
    response = csv_response(f'{kind}-{start}-to-{end}')
    writer = csv.writer(response)
    if kind == 'accounting':
        writer.writerow(['CaCaCa operational accounting summary'])
        writer.writerow(['Period', start, end])
        writer.writerow([])
        writer.writerow(['Metric', 'USD'])
        for label, key in [
            ('Gross product sales', 'gross_sales'), ('Shipping collected', 'shipping_collected'),
            ('Tax originally collected', 'tax_collected'), ('Refunds', 'refunds_negative'),
            ('Product build costs', 'product_cost'), ('Shipping expense', 'shipping_expense'),
            ('Payment processing fees', 'payment_fees'), ('Other expenses', 'other_expenses'),
            ('Estimated gross profit before tax', 'estimated_profit'),
        ]:
            writer.writerow([label, report[key]])
        writer.writerow([])
        writer.writerow(['Paid orders', report['order_count']])
        writer.writerow(['Sold item lines missing build cost', report['missing_cost_lines']])
    elif kind == 'orders':
        writer.writerow(['Order', 'Paid date', 'Customer', 'Email', 'State', 'Payment status',
                         'Fulfillment', 'Rod IDs', 'Subtotal', 'Shipping collected', 'Tax', 'Total',
                         'Refunded', 'Net collected', 'Product cost', 'Shipping expense', 'Carrier',
                         'Tracking', 'PayPal capture'])
        for order in report['orders']:
            product_cost = sum(((item.unit_cost or Decimal('0.00')) * item.quantity for item in order.items.all()), Decimal('0.00'))
            writer.writerow([order.number, timezone.localtime(order.paid_at).date(), csv_value(order.name),
                csv_value(order.email), csv_value(order.address.get('admin_area_1', '')), order.get_status_display(),
                order.get_fulfillment_status_display(), ' | '.join(csv_value(item.rod_id) for item in order.items.all()),
                order.subtotal, order.shipping, order.tax, order.total, order.refunded_amount,
                order.total - order.refunded_amount, product_cost, order.actual_shipping_cost or '',
                order.get_carrier_display() if order.carrier else '', csv_value(order.tracking_number), order.capture_id])
    elif kind == 'inventory':
        writer.writerow(['Rod ID', 'Name', 'Design', 'Status', 'Build type', 'Price', 'Build cost',
                         'Quantity', 'Reserved', 'Available units', 'Published', 'Serial number'])
        for product in reporting.inventory_data()['products']:
            writer.writerow([csv_value(product.rod_id), csv_value(product.name), csv_value(product.design_theme or ''),
                product.get_status_display(), product.get_build_type_display(), product.price,
                product.build_cost if product.build_cost is not None else '', product.quantity,
                product.reserved_quantity, product.available_units, product.is_published,
                csv_value(product.serial_number)])
    elif kind == 'requests':
        writer.writerow(['Received', 'Name', 'Email', 'Phone', 'Status', 'Fishing style', 'Target species',
                         'Location', 'Budget', 'Timeline'])
        requests = CustomBuildRequest.objects.filter(created_at__date__range=(start, end))
        for item in requests:
            writer.writerow([timezone.localtime(item.created_at).date(), csv_value(item.name), csv_value(item.email),
                csv_value(item.phone), item.get_status_display(), csv_value(item.fishing_style),
                csv_value(item.target_species), csv_value(item.fishing_location), csv_value(item.budget),
                csv_value(item.timeline)])
    elif kind == 'expenses':
        writer.writerow(['Date', 'Category', 'Description', 'Amount', 'Reference', 'Entered by'])
        for expense in report['expenses']:
            writer.writerow([expense.incurred_on, expense.get_category_display(), csv_value(expense.description),
                expense.amount, csv_value(expense.reference), expense.created_by.get_username() if expense.created_by else ''])
    else:
        raise Http404
    return response


@staff_member_required(login_url='admin:login')
@require_GET
@never_cache
def owner_order(request, order_id):
    order = get_object_or_404(Order.objects.prefetch_related('items', 'notifications', 'audit__actor'), pk=order_id)
    shipping_form = ShippingForm(initial={
        'carrier': order.carrier, 'carrier_other': order.carrier_other,
        'tracking_number': order.tracking_number, 'actual_shipping_cost': order.actual_shipping_cost,
    })
    return render(request, 'owner/order_detail.html', {
        'order': order, 'shipping_form': shipping_form,
        'action_form': FulfillmentActionForm(),
    })


@staff_member_required(login_url='admin:login')
@require_POST
def owner_fulfillment(request, order_id):
    action_form = FulfillmentActionForm(request.POST)
    if not action_form.is_valid():
        messages.error(request, 'Choose a valid fulfillment action.')
        return redirect('owner_order', order_id=order_id)
    action = action_form.cleaned_data['action']
    shipping = None
    if action == 'ship':
        shipping_form = ShippingForm(request.POST)
        if not shipping_form.is_valid():
            order = get_object_or_404(Order.objects.prefetch_related('items', 'notifications', 'audit__actor'), pk=order_id)
            return render(request, 'owner/order_detail.html', {
                'order': order, 'shipping_form': shipping_form, 'action_form': action_form,
            }, status=400)
        shipping = shipping_form.cleaned_data
    try:
        fulfillment.update_fulfillment(order_id, action, shipping, request.user)
        messages.success(request, 'Order status updated.')
    except CheckoutError as exc:
        messages.error(request, str(exc))
    return redirect('owner_order', order_id=order_id)


@staff_member_required(login_url='admin:login')
@require_GET
@never_cache
def owner_receipt(request, order_id):
    order = get_object_or_404(Order.objects.prefetch_related('items'), pk=order_id)
    if not order.payment_confirmed:
        raise Http404
    return render(request, 'commerce/receipt.html', {'order': order, 'store': StoreSettings.objects.get(pk=1),
                                                      'owner_view': True})


@staff_member_required(login_url='admin:login')
@require_POST
def resend_notification(request, order_id, kind):
    order = get_object_or_404(Order, pk=order_id)
    senders = {
        NotificationLog.Kind.CUSTOMER_CONFIRMATION: notifications.send_customer_confirmation,
        NotificationLog.Kind.OWNER_NEW_ORDER: notifications.send_owner_new_order,
        NotificationLog.Kind.SHIPPING_CONFIRMATION: notifications.send_shipping_confirmation,
    }
    sender = senders.get(kind)
    if not sender or (kind == NotificationLog.Kind.SHIPPING_CONFIRMATION and not order.shipped_at):
        raise Http404
    if sender(order.pk):
        messages.success(request, 'Email sent or was already delivered.')
    else:
        messages.error(request, 'Email could not be sent. Check the email settings and try again.')
    return redirect('owner_order', order_id=order_id)


@require_POST
def start_payment(request, order_id):
    order = owned_order(request, order_id)
    try:
        url = services.begin_payment(order.pk, owner_key(request))
        if url:
            return redirect(url)
    except (CheckoutError, paypal.PayPalError) as exc:
        messages.error(request, str(exc))
    return redirect(order)


@require_POST
def confirm_payment(request, order_id):
    order = owned_order(request, order_id)
    try:
        services.capture_payment(order.pk, owner_key(request))
    except (CheckoutError, paypal.PayPalError) as exc:
        messages.error(request, str(exc))
    return redirect(order)


@require_POST
def cancel(request, order_id):
    order = owned_order(request, order_id)
    try:
        services.cancel_order(order.pk, owner_key(request))
        request.session.pop('checkout_key', None)
    except CheckoutError as exc:
        messages.error(request, str(exc))
    return redirect(order)


@csrf_exempt
@require_POST
def webhook(request):
    try:
        content_length = int(request.META.get('CONTENT_LENGTH') or 0)
    except ValueError:
        return HttpResponse(status=400)
    if content_length > 1024 * 1024:
        return HttpResponse(status=413)
    if len(request.body) > 1024 * 1024:
        return HttpResponse(status=413)
    try:
        event = json.loads(request.body)
        if not isinstance(event, dict) or not isinstance(event.get('resource'), dict):
            return HttpResponse(status=400)
        event_id, event_type = event.get('id'), event.get('event_type')
        if not isinstance(event_id, str) or not isinstance(event_type, str) or not 1 <= len(event_id) <= 100 or len(event_type) > 100:
            return HttpResponse(status=400)
        if not paypal.verify_webhook(request.headers, event):
            return HttpResponse(status=400)
        with transaction.atomic():
            entry, _ = PaymentEvent.objects.get_or_create(event_id=event_id, defaults={'event_type': event_type})
            entry = PaymentEvent.objects.select_for_update().get(pk=entry.pk)
            if entry.processed_at:
                return JsonResponse({'status': 'already processed'})
            resource = event['resource']
            order = None
            if event_type in ['PAYMENT.CAPTURE.COMPLETED', 'PAYMENT.CAPTURE.PENDING', 'PAYMENT.CAPTURE.DENIED']:
                paypal_id = resource.get('supplementary_data', {}).get('related_ids', {}).get('order_id')
                order = Order.objects.filter(paypal_order_id=paypal_id).first() if paypal_id else None
                if not order:
                    # A webhook may race the API response/local save. Return non-2xx so PayPal retries.
                    return HttpResponse(status=503)
                remote = paypal.get_order(order.paypal_order_id)
                if event_type == 'PAYMENT.CAPTURE.COMPLETED' and remote.get('status') == 'COMPLETED':
                    services.settle_order(order.pk, remote)
                elif event_type == 'PAYMENT.CAPTURE.PENDING':
                    locked = Order.objects.select_for_update().get(pk=order.pk)
                    if not locked.payment_confirmed:
                        locked.status = Order.Status.CAPTURING
                        locked.save(update_fields=['status', 'updated_at'])
                        services.audit(locked, 'PayPal reports capture pending; inventory hold preserved')
                elif event_type == 'PAYMENT.CAPTURE.DENIED':
                    locked = Order.objects.select_for_update().get(pk=order.pk)
                    captures = [c for unit in remote.get('purchase_units', []) for c in unit.get('payments', {}).get('captures', [])]
                    if any(c.get('id') == resource.get('id') and c.get('status') in ['DECLINED', 'FAILED', 'DENIED'] for c in captures):
                        if locked.status == Order.Status.CAPTURING:
                            services.release_locked(locked, Order.Status.FAILED)
                entry.outcome = 'Capture status synchronized'
            elif event_type in ['PAYMENT.CAPTURE.REFUNDED', 'PAYMENT.REFUND.PENDING', 'PAYMENT.REFUND.FAILED']:
                refund = paypal.api('GET', '/v2/payments/refunds/' + paypal.provider_id(resource.get('id')))
                capture_ids = [link.get('href', '').split('?')[0].rstrip('/').split('/')[-1]
                               for link in refund.get('links', []) if link.get('rel') == 'up']
                order = Order.objects.filter(capture_id__in=capture_ids).first()
                if not order:
                    return HttpResponse(status=503)
                services.record_refund(order.pk, refund)
                entry.outcome = 'Refund synchronized'
            else:
                entry.outcome = 'Ignored event type'
            entry.order = order
            entry.processed_at = timezone.now()
            entry.save(update_fields=['order', 'outcome', 'processed_at'])
        return JsonResponse({'status': 'processed'})
    except (ValueError, KeyError, TypeError):
        return HttpResponse(status=400)
    except (paypal.PayPalError, CheckoutError):
        logger.warning('PayPal webhook could not be processed; provider retry required')
        return HttpResponse(status=503)
