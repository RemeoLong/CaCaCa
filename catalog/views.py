import time
from urllib.parse import urljoin
from xml.etree import ElementTree
from django.conf import settings
from django.core.paginator import Paginator
from django.db import connection
from django.db.models import Q, F
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods
from .forms import CustomBuildForm, RodInquiryForm
from .models import Product, Species, DesignTheme, CustomBuildRequest, StoreSettings

def public_products():
    public_sale = Q(is_published=True, price__isnull=False) & ~Q(status=Product.Status.DRAFT)
    public_preview = Q(status=Product.Status.DRAFT, is_published=False, show_in_gallery=True)
    return Product.objects.filter(public_sale | public_preview).select_related(
        'design_theme', 'specification', 'specification__primary_target_species').prefetch_related('images', 'specification__additional_target_species')

def available_rods():
    return public_products().filter(item_type=Product.ItemType.ROD, status__in=['available', 'ready'], quantity__gt=F('reserved_quantity'),
        build_type__in=[Product.BuildType.STOCK, Product.BuildType.UNIQUE])


def collection_rods():
    return public_products().filter(item_type=Product.ItemType.ROD).filter(
        Q(status__in=[Product.Status.AVAILABLE, Product.Status.READY], is_published=True,
          quantity__gt=F('reserved_quantity'), build_type__in=[Product.BuildType.STOCK, Product.BuildType.UNIQUE])
        | Q(status=Product.Status.DRAFT, is_published=False, show_in_gallery=True))

@require_GET
def home(request):
    rods = list(collection_rods().order_by('-is_published', '-featured', '-created_at')[:6])
    hero_rod = next((rod for rod in rods if rod.cover), None)
    tackle_items = public_products().filter(item_type=Product.ItemType.TACKLE, is_published=True,
        status__in=[Product.Status.AVAILABLE, Product.Status.READY]).order_by('-created_at')[:3]
    return render(request, 'catalog/home.html', {'featured': rods, 'hero_rod': hero_rod, 'tackle_items': tackle_items})

@require_GET
def shop(request, salmon=False, gallery=False):
    rods = public_products()
    if gallery:
        rods = rods.filter(item_type=Product.ItemType.ROD, show_in_gallery=True)
    else:
        rods = collection_rods()
    if salmon:
        rods = rods.filter(specification__salmon_focused=True)
    query = request.GET.get('q', '').strip()[:100]
    if query:
        rods = rods.filter(Q(name__icontains=query) | Q(description__icontains=query) | Q(rod_id__icontains=query)
            | Q(design_theme__name__icontains=query) | Q(design_story__icontains=query))
    design = request.GET.get('design', '')
    if design.isdigit():
        rods = rods.filter(design_theme_id=design)
    build_type = request.GET.get('build', '')
    if build_type in dict(Product.BuildType.choices):
        rods = rods.filter(build_type=build_type)
    species = request.GET.get('species', '')
    if species.isdigit():
        rods = rods.filter(Q(specification__primary_target_species_id=species) | Q(specification__additional_target_species__id=species)).distinct()
    sort = request.GET.get('sort', '')
    ordering = {'price_asc': F('price').asc(nulls_last=True),
                'price_desc': F('price').desc(nulls_last=True), 'name': 'name'}
    rods = rods.order_by(ordering.get(sort, '-created_at'), 'id')
    page = Paginator(rods, 12).get_page(request.GET.get('page'))
    params = request.GET.copy()
    params.pop('page', None)
    return render(request, 'catalog/shop.html', {'page_obj': page, 'query': query, 'species_list': Species.objects.all(),
        'build_types': Product.BuildType.choices, 'design_themes': DesignTheme.objects.all(), 'gallery': gallery, 'salmon': salmon,
        'pagination_query': params.urlencode(), 'title': 'The wrap gallery' if gallery else 'Salmon rods' if salmon else 'Hand-wrapped rod collection'})


@require_GET
def tackle(request):
    items = public_products().filter(item_type=Product.ItemType.TACKLE, is_published=True).order_by('-created_at')
    return render(request, 'catalog/tackle.html', {'items': items})

@require_http_methods(['GET', 'POST'])
def contact(request):
    rod = public_products().filter(item_type=Product.ItemType.ROD, slug=request.GET.get('rod', '')).first()
    tackle_item = public_products().filter(item_type=Product.ItemType.TACKLE,
        slug=request.GET.get('tackle', '')).first()
    item = rod or tackle_item
    initial = {'notes': f'I have a question about {item.name} ({item.rod_id}).'} if item else {}
    form = RodInquiryForm(request.POST if request.method == 'POST' else None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        if time.time() - request.session.get('last_request_at', 0) < 60:
            form.add_error(None, 'Your previous message was saved. Please wait a minute before sending another.')
        else:
            inquiry = form.save(commit=False)
            inquiry.kind = CustomBuildRequest.Kind.ROD if rod else CustomBuildRequest.Kind.QUESTION
            inquiry.product = item
            if item:
                label = 'Rod' if rod else 'Tackle'
                inquiry.notes = f'{label} inquiry: {item.name} ({item.rod_id})\n\n{inquiry.notes}'
            inquiry.save()
            request.session['last_request_at'] = time.time()
            return redirect('inquiry_success')
    return render(request, 'catalog/contact.html', {'form': form, 'rod': rod, 'tackle_item': tackle_item})

@require_GET
def product(request, slug):
    rod = get_object_or_404(public_products().filter(item_type=Product.ItemType.ROD), slug=slug)
    cover = rod.cover
    context = {
        'rod': rod, 'seo_title': f'{rod.name} | CaCaCa',
        'seo_description': rod.meta_description or rod.description[:160],
    }
    if cover:
        context['seo_image'] = urljoin(settings.PUBLIC_BASE_URL + '/', cover.image.url)
    return render(request, 'catalog/product.html', context)


@require_GET
def tackle_product(request, slug):
    item = get_object_or_404(public_products().filter(item_type=Product.ItemType.TACKLE,
        is_published=True), slug=slug)
    cover = item.cover
    from commerce import services
    context = {'item': item, 'seo_title': f'{item.name} | CaCaCa',
        'checkout_ready': services.ready(StoreSettings.objects.get(pk=1)),
        'seo_description': item.meta_description or item.description[:160]}
    if cover:
        context['seo_image'] = urljoin(settings.PUBLIC_BASE_URL + '/', cover.image.url)
    return render(request, 'catalog/tackle_product.html', context)

@require_http_methods(['GET', 'POST'])
def custom(request):
    initial = {}
    if request.method == 'GET' and request.GET.get('rod'):
        rod = public_products().filter(slug=request.GET['rod']).first()
        if rod:
            initial['notes'] = f'I would like to ask about {rod.name} ({rod.rod_id}).'
    form = CustomBuildForm(request.POST if request.method == 'POST' else None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        if time.time() - request.session.get('last_request_at', 0) < 60:
            form.add_error(None, 'Your previous request was saved. Please wait a minute before sending another.')
        else:
            form.save()
            request.session['last_request_at'] = time.time()
            return redirect('custom_success')
    return render(request, 'catalog/custom.html', {'form': form})

@require_GET
def page(request, page):
    return render(request, f'catalog/{page}.html')

@require_GET
def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
    except Exception:
        return JsonResponse({'status': 'unavailable'}, status=503)
    return JsonResponse({'status': 'ok'})

@require_GET
def robots(request):
    if not settings.SITE_INDEXABLE:
        body = 'User-agent: *\nDisallow: /\n'
    else:
        paths = ['/admin/', '/owner/', '/cart/', '/checkout/', '/orders/', '/payments/',
                 '/contact/received/', '/custom-rods/received/', '/health/']
        body = 'User-agent: *\n' + ''.join(f'Disallow: {path}\n' for path in paths)
        body += f'Sitemap: {settings.PUBLIC_BASE_URL}/sitemap.xml\n'
    return HttpResponse(body, content_type='text/plain')


@require_GET
def sitemap(request):
    namespace = 'http://www.sitemaps.org/schemas/sitemap/0.9'
    ElementTree.register_namespace('', namespace)
    root = ElementTree.Element(f'{{{namespace}}}urlset')
    static_routes = ['home', 'shop', 'tackle', 'salmon', 'gallery', 'about', 'faq', 'contact', 'custom', 'returns']
    from django.urls import reverse

    def add_url(path, lastmod=None):
        entry = ElementTree.SubElement(root, f'{{{namespace}}}url')
        ElementTree.SubElement(entry, f'{{{namespace}}}loc').text = settings.PUBLIC_BASE_URL + path
        if lastmod:
            ElementTree.SubElement(entry, f'{{{namespace}}}lastmod').text = lastmod.date().isoformat()

    for route in static_routes:
        add_url(reverse(route))
    for slug, updated_at, item_type in public_products().values_list('slug', 'updated_at', 'item_type').iterator(chunk_size=1000):
        route = 'tackle_product' if item_type == Product.ItemType.TACKLE else 'product'
        add_url(reverse(route, kwargs={'slug': slug}), updated_at)
    return HttpResponse(ElementTree.tostring(root, encoding='utf-8', xml_declaration=True),
                        content_type='application/xml')
