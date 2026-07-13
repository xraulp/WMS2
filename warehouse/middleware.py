class MobileRedirectMiddleware:
    """Auto-detect mobile and redirect to /mobile/ interface."""
    MOBILE_AGENTS = ['mobile','android','iphone','ipad','ipod','tablet']
    SKIP_PATHS    = ['/mobile/', '/logout/', '/static/', '/media/',
                     '/debug/', '/admin/', '/operations/', '/digital/',
                     '/reports/', '/catalog/', '/users/']

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        # Skip if already on mobile or excluded paths
        if not any(path.startswith(p) for p in self.SKIP_PATHS):
            ua = request.META.get('HTTP_USER_AGENT', '').lower()
            if any(agent in ua for agent in self.MOBILE_AGENTS):
                # Only redirect authenticated users on dashboard
                if path in ('/', '/dashboard/') and request.user.is_authenticated:
                    from django.shortcuts import redirect
                    return redirect('mobile_dashboard')
        return self.get_response(request)
