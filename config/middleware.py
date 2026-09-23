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
