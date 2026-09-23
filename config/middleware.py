from django.conf import settings
from django.http import HttpResponseForbidden


class OwnerReviewReadOnlyMiddleware:
    """Keep the short-lived review copy from accepting inventory/order edits."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if settings.REVIEW_MODE and request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            owner_write = request.path.startswith('/owner/') and request.path != '/owner/setup/'
            admin_write = request.path.startswith('/admin/') and request.path not in {
                '/admin/login/', '/admin/logout/', '/admin/password_change/',
            }
            if owner_write or admin_write:
                return HttpResponseForbidden('This temporary owner review is read-only. Changes are not saved.')
        return self.get_response(request)


class OwnerNoStoreMiddleware:
    """Prevent browsers and intermediaries from retaining private owner screens."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith('/owner/') and request.path not in {
            '/owner/manifest.webmanifest', '/owner/sw.js',
        }:
            if 'no-store' not in response.get('Cache-Control', ''):
                response['Cache-Control'] = 'private, no-store, max-age=0'
            response['Pragma'] = 'no-cache'
        return response
