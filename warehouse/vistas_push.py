"""
Las rutas de los avisos push: el service worker, el manifest y el alta y baja
de cada dispositivo. Lo que se manda y a quien vive en `avisos_push.py`.
"""
import json

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.templatetags.static import static
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from . import avisos_push
from .models import PushSubscription, UserProfile


@never_cache
def service_worker(request):
    """
    El service worker, que es quien recibe el aviso aunque el WMS este cerrado.

    Se sirve desde la raiz y no desde `/static/`: un service worker solo puede
    atender las paginas que cuelgan de la carpeta en la que vive, y desde
    `/static/` no alcanzaria ni al tablero ni a la pantalla movil. Sin cache,
    para que un cambio llegue a los telefonos en la siguiente visita.
    """
    respuesta = render(request, 'warehouse/push/sw.js',
                       content_type='application/javascript')
    respuesta['Service-Worker-Allowed'] = '/'
    return respuesta


def manifest(request):
    """
    Lo que hace al WMS instalable: el nombre y el icono con que queda en la
    pantalla de inicio. En iPhone es obligatorio, porque Safari solo deja
    recibir avisos a lo que se agrego a la pantalla de inicio.
    """
    datos = {
        'name': 'WMS',
        'short_name': 'WMS',
        'start_url': '/',
        'scope': '/',
        'display': 'standalone',
        'background_color': '#0f172a',
        'theme_color': '#0f172a',
        'icons': [
            {'src': static('warehouse/push/icono-192.png'), 'sizes': '192x192',
             'type': 'image/png', 'purpose': 'any'},
            {'src': static('warehouse/push/icono-512.png'), 'sizes': '512x512',
             'type': 'image/png', 'purpose': 'any maskable'},
        ],
    }
    return HttpResponse(json.dumps(datos), content_type='application/manifest+json')


def _es_operador(usuario):
    perfil = UserProfile.objects.filter(user=usuario).first()
    return perfil if (perfil and perfil.is_operator()) else None


@login_required
@require_POST
def suscribir(request):
    """
    Da de alta el dispositivo desde el que se pulso «Activar avisos».

    Solo operadores, por ahora: los dos avisos que existen son para ellos, y
    a un cliente suscrito no le llegaria nada. Si el mismo navegador ya estaba
    suscrito -- otro usuario en la misma computadora, o el mismo que volvio a
    pulsar --, la suscripcion pasa a quien la pide ahora.
    """
    perfil = _es_operador(request.user)
    if perfil is None:
        return JsonResponse({'ok': False, 'error': _('Permission denied.')}, status=403)
    if not avisos_push.push_disponible():
        return JsonResponse({'ok': False, 'error': _('Notifications are not configured.')},
                            status=400)
    try:
        datos = json.loads(request.body or b'{}')
        endpoint = str(datos['endpoint']).strip()
        p256dh = str(datos['keys']['p256dh']).strip()
        auth = str(datos['keys']['auth']).strip()
    except (ValueError, KeyError, TypeError):
        return JsonResponse({'ok': False, 'error': 'bad_subscription'}, status=400)
    if not endpoint.startswith('https://') or not p256dh or not auth:
        return JsonResponse({'ok': False, 'error': 'bad_subscription'}, status=400)

    PushSubscription.objects.update_or_create(
        endpoint=endpoint,
        defaults={
            'user': request.user, 'tenant': perfil.tenant,
            'p256dh': p256dh[:200], 'auth': auth[:100],
            'user_agent': request.META.get('HTTP_USER_AGENT', '')[:300],
            'es_movil': bool(datos.get('es_movil')),
        })
    return JsonResponse({'ok': True, 'dispositivos': request.user.push_subscriptions.count()})


@login_required
@require_POST
def quitar(request):
    """Da de baja este dispositivo. Solo los del propio usuario."""
    try:
        endpoint = str(json.loads(request.body or b'{}').get('endpoint', '')).strip()
    except (ValueError, AttributeError):
        endpoint = ''
    if endpoint:
        PushSubscription.objects.filter(user=request.user, endpoint=endpoint).delete()
    return JsonResponse({'ok': True, 'dispositivos': request.user.push_subscriptions.count()})


@login_required
@require_POST
def probar(request):
    """Manda un aviso de prueba a los dispositivos de quien lo pide."""
    if not avisos_push.push_disponible():
        return JsonResponse({'ok': False, 'error': _('Notifications are not configured.')},
                            status=400)
    llego, errores = avisos_push.mandar_prueba(request.user)
    if llego:
        return JsonResponse({'ok': True})
    return JsonResponse({
        'ok': False,
        'error': errores[0] if errores else _('No device is active for your user.'),
    }, status=400 if not errores else 502)
