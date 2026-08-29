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
