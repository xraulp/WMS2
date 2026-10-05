"""
Lo que toda pantalla necesita saber de quien la esta mirando.

De momento, como quiere verla: el tema y el idioma. Van en un procesador de
contexto y no en cada vista porque los pinta la plantilla base --el atributo
`data-theme` del <html>-- y esa la usan el escritorio, el movil y la
plataforma: ponerlo vista por vista seria acordarse de ello en cada pantalla
nueva, y olvidarlo significa que el tema se pierde justo en esa.
"""


def preferencias(request):
    from django.conf import settings

    # Los idiomas que se ofrecen salen de la configuracion: anadir uno no
    # puede obligar a acordarse tambien de la barra de cada pantalla.
    idiomas = list(settings.LANGUAGES)
    # Los del cliente llevan ademas la opcion vacia: "el de la casa".
    from .models import Catalog
    idiomas_del_cliente = list(Catalog.LANGUAGE_CHOICES)
    usuario = getattr(request, 'user', None)
    if not usuario or not usuario.is_authenticated:
        return {'tema_usuario': '', 'idioma_usuario': '', 'idiomas': idiomas,
                'idiomas_del_cliente': idiomas_del_cliente}
    perfil = getattr(usuario, 'profile', None)
    return {
        # Vacio significa "el que tenga el sistema operativo": la plantilla no
        # pinta `data-theme` y manda `prefers-color-scheme`.
        'tema_usuario': getattr(perfil, 'theme', '') or '',
        'idioma_usuario': getattr(perfil, 'language', '') or '',
        'idiomas': idiomas,
        'idiomas_del_cliente': idiomas_del_cliente,
    }


def es_telefono(request):
    """
    Si quien mira esta en un telefono.

    Manda lo que la persona haya elegido en esta sesion con el enlace de la
    cuenta ("vista de escritorio" / "vista movil"); si no eligio nada, el
    navegador. "Mobi" es la marca que recomienda MDN: la llevan Chrome y Safari
    de telefono y no la de una tableta, que tiene sitio para el tablero.
    """
    sesion = getattr(request, 'session', None)
    elegida = sesion.get('vista') if sesion is not None else None
    if elegida == 'escritorio':
        return False
    if elegida == 'movil':
        return True
    agente = request.META.get('HTTP_USER_AGENT', '')
    return 'Mobi' in agente or 'iPhone' in agente


def vista(request):
    # Lo usan las pantallas aparte -- pedimentos, cruces, impuestos -- para
    # saber si pintan la barra de abajo y a donde lleva su "volver".
    return {'es_telefono': es_telefono(request)}


def barra_superior(request):
    """
    Lo que pinta la barra de arriba en las pantallas aparte: quien es, de que
    empresa y si ademas administra la plataforma.

    Pedimentos, cruces e impuestos no son paneles del tablero sino pantallas
    propias, y al entrar en ellas se perdia la barra entera: el nombre de la
    empresa, quien estaba trabajando, el tema, el idioma y la salida. Ahora
    pintan la misma que el tablero, y lo que esa barra necesita sale de aqui y
    no de cada vista.

    Va perezoso porque este procesador corre en cada peticion, tambien en los
    trozos que repinta htmx, y las consultas solo hacen falta donde se pinta la
    barra.
    """
    from django.utils.functional import SimpleLazyObject

    def datos():
        usuario = getattr(request, 'user', None)
        if not usuario or not usuario.is_authenticated:
            return {}
        from .views import (get_profile, platform_role, resumen_de_alertas,
                            resumen_sin_leer)
        tenant = getattr(request, 'tenant', None)
        datos = {
            'perfil': get_profile(usuario),
            'tenant': tenant,
            'plataforma': platform_role(usuario),
        }
        # Los dos contadores de la derecha del tablero: mensajes sin leer y
        # mercancia pasada de tiempo. Los mismos numeros que alli, porque es
        # la misma barra.
        if tenant is not None:
            datos['sin_leer'] = resumen_sin_leer(usuario, tenant)
            datos['alertas'] = resumen_de_alertas(usuario, tenant)
        return datos

    return {'barra': SimpleLazyObject(datos)}
