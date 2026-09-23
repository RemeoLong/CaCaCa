from .models import StoreSettings
from django.conf import settings
from django.templatetags.static import static
from urllib.parse import urljoin

def store_settings(request):
    return {'store': StoreSettings.objects.filter(pk=1).first() or StoreSettings(),
            'review_mode': settings.REVIEW_MODE}


PUBLIC_PAGE_TITLES = {
    'home': 'CaCaCa | Hand-wrapped fishing rods',
    'shop': 'Hand-wrapped rod collection | CaCaCa',
    'salmon': 'Salmon rods | CaCaCa',
    'gallery': 'The wrap gallery | CaCaCa',
    'about': 'Our story | CaCaCa',
    'faq': 'Frequently asked questions | CaCaCa',
    'contact': 'Ask about a rod | CaCaCa',
    'custom': 'Special requests | CaCaCa',
    'returns': 'Returns and cancellations | CaCaCa',
    'product': 'Fishing rod | CaCaCa',
}


def seo_metadata(request):
    name = request.resolver_match.url_name if request.resolver_match else None
    public_page = name in PUBLIC_PAGE_TITLES
    # Search and other filtered collection URLs are useful to visitors, but should
    # not create duplicate entries for every combination of filters.
    filtered_collection = name in {'shop', 'salmon', 'gallery'} and any(
        key != 'page' for key in request.GET
    )
    base = settings.PUBLIC_BASE_URL
    canonical_path = request.path
    if name in {'shop', 'salmon', 'gallery'}:
        page = request.GET.get('page', '')
        canonical_path += f'?page={int(page)}' if page.isdigit() and len(page) <= 8 and int(page) > 1 else ''
    return {
        'seo_indexable': settings.SITE_INDEXABLE and public_page and not filtered_collection,
        'seo_canonical': base + canonical_path if public_page and not filtered_collection else '',
        'seo_title': PUBLIC_PAGE_TITLES.get(name, ''),
        'seo_image': urljoin(base + '/', static('brand/cacaca-logo-accented.png')),
    }
