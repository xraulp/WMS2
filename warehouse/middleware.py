from django.http import Http404
from django.db import connection
from .models import Tenant

class TenantMiddleware:
    """
    Middleware para detectar el tenant: primero por subdominio (para cuando
    haya subdominios reales por tenant), y si no aplica, usando el tenant
    asignado al perfil del usuario autenticado. Ya no lanza 404 si no
    encuentra nada; simplemente deja request.tenant en None y cada vista
    decide si el tenant es obligatorio (via get_tenant_or_404).
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.get_host().split(':')[0]
        parts = host.split('.')
        subdomain = parts[0] if len(parts) >= 3 else None

        tenant = None
        if subdomain and subdomain not in ('www',):
            tenant = Tenant.objects.filter(subdomain=subdomain, is_active=True).first()

        if not tenant and request.user.is_authenticated:
            profile = getattr(request.user, 'profile', None)
            if profile and profile.tenant_id:
                tenant = profile.tenant

        request.tenant = tenant

        # Configurar RLS si hay tenant. SET es sintaxis exclusiva de PostgreSQL:
        # sin este guard, cualquier request truena con OperationalError al correr
        # sobre SQLite (el fallback local de settings.py y la base de los tests).
        if (request.tenant and request.user.is_authenticated
                and connection.vendor == 'postgresql'):
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