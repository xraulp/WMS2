import logging

from django.http import Http404
from django.db import connection
from .models import Tenant

logger = logging.getLogger(__name__)

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
        rls = (request.tenant and request.user.is_authenticated
               and connection.vendor == 'postgresql')
        if rls:
            with connection.cursor() as cursor:
                cursor.execute("SET app.current_tenant_id = %s", [str(request.tenant.id)])
                cursor.execute("SET app.current_user_id = %s", [str(request.user.id)])

        try:
            response = self.get_response(request)
        finally:
            # Las variables de sesion viven en la **conexion**, no en el
            # request, y las conexiones son persistentes (conn_max_age=600 en
            # settings.py). Sin limpiar al terminar, la siguiente peticion que
            # reutilice esta conexion arranca con el tenant y el usuario de la
            # anterior; si esa peticion no entra por este if — un usuario sin
            # tenant, o una peticion anonima — hereda el contexto ajeno y RLS lo
            # deja leer datos de otra empresa.
            #
            # `SET LOCAL` no sirve aqui: se limita a la transaccion en curso y
            # con autocommit no habria ninguna, asi que RLS se quedaria sin
            # contexto. Limpiar al final del request si.
            if rls:
                self._reset_rls()

        return response

    @staticmethod
    def _reset_rls():
        try:
            with connection.cursor() as cursor:
                cursor.execute("RESET app.current_tenant_id")
                cursor.execute("RESET app.current_user_id")
        except Exception:
            # La conexion puede estar ya cerrada o en estado de error si la
            # vista revento; no vale la pena tapar esa excepcion con esta. La
            # conexion en error se descarta y no se reutiliza, asi que el
            # contexto no se filtra.
            logger.debug('No se pudo limpiar el contexto de RLS', exc_info=True)


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

class IdiomaDelPerfilMiddleware:
    """
    Deja puesto el idioma que el usuario eligio en su perfil.

    `LocaleMiddleware` ya atiende la cookie y el `Accept-Language`, pero corre
    antes de la autenticacion y no puede mirar el perfil. Sin esto, quien
    eligio espanol y entra desde otra computadora --donde la cookie no esta--
    recibiria la pantalla en el idioma del navegador de esa computadora, que es
    justo lo que el habia decidido no dejar al azar.

    La cookie gana sobre el perfil a proposito: es lo ultimo que se toco en
    este navegador, y cambiarla desde el selector tiene que verse aqui mismo.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.conf import settings
        from django.utils import translation

        usuario = getattr(request, 'user', None)
        pedido_en_el_navegador = request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME)
        if usuario is not None and usuario.is_authenticated and not pedido_en_el_navegador:
            perfil = getattr(usuario, 'profile', None)
            idioma = getattr(perfil, 'language', '') or ''
            if idioma:
                translation.activate(idioma)
                request.LANGUAGE_CODE = idioma

        respuesta = self.get_response(request)
        # El idioma se activo para esta peticion; dejarlo puesto contaminaria
        # la siguiente, que puede ser de otra persona en el mismo proceso.
        translation.deactivate()
        return respuesta
