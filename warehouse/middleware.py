from django.http import Http404
from django.db import connection
from .models import Tenant

class TenantMiddleware:
    """
    Middleware para detectar el tenant por subdominio y configurar RLS.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.get_host()
        subdomain = host.split('.')[0] if '.' in host else None
        
        if subdomain:
            try:
                request.tenant = Tenant.objects.get(subdomain=subdomain, is_active=True)
            except Tenant.DoesNotExist:
                raise Http404("Tenant no encontrado")
        else:
            # Sin subdominio -> panel de superadmin
            #####request.tenant = None
            #####072526 12:23 ds En desarrollo local, usar tenant por defecto (subdomain='default')
            request.tenant = Tenant.objects.filter(subdomain='default', is_active=True).first()

        # Configurar RLS si hay tenant
        if request.tenant and request.user.is_authenticated:
            with connection.cursor() as cursor:
                cursor.execute("SET app.current_tenant_id = %s", [str(request.tenant.id)])
                cursor.execute("SET app.current_user_id = %s", [str(request.user.id)])

        response = self.get_response(request)
        return response


class TenantPermissionsMiddleware:
    """
    Middleware para cargar permisos del usuario basados en su rol.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            try:
                profile = request.user.profile
                if profile and profile.role:
                    request.user_permissions = profile.role.get_all_permissions()
                else:
                    request.user_permissions = set()
            except:
                request.user_permissions = set()
        else:
            request.user_permissions = set()

        response = self.get_response(request)
        return response


class TenantContextMiddleware:
    """
    Middleware para agregar el tenant al contexto de las plantillas.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        
        if hasattr(request, 'tenant') and request.tenant:
            response.context_data = getattr(response, 'context_data', {})
            response.context_data['current_tenant'] = request.tenant
        
        return response