"""
Enlaces a los archivos del expediente.

Los archivos viven en un bucket de Cloudflare R2 y hasta ahora la pantalla
enlazaba directo a el: `AWS_S3_CUSTOM_DOMAIN` esta definida, asi que
`FieldFile.url` devuelve `https://<dominio>/<ruta>` **sin firma**, y para que
ese enlace sirva el bucket tiene que estar publicado. Es decir: cualquiera que
acierte una ruta se lleva el archivo sin pasar por el sistema, sin sesion y sin
dejar rastro. El dominio no es secreto — sale en el HTML de cualquier pantalla
con archivos, incluida la de un cliente.

Este modulo es la mitad de la salida. La otra mitad es la vista
`document_file`, que comprueba permisos y redirige aqui: en vez del enlace
publico se entrega una **URL firmada recien hecha y de vida corta**. Asi el
enlace que guarda el navegador (`/documents/<id>/file/`) no caduca nunca, y el
que llega al bucket caduca en minutos.

`url_firmada` devuelve `None` cuando el almacen no sabe firmar — el sistema de
archivos local, que es lo que corre en desarrollo y en las pruebas. La vista
sirve entonces el archivo ella misma. Ese camino no es un parche: es lo que
mantiene la pantalla igual de funcional sin R2 delante.
"""
import copy
import logging

from django.utils.http import content_disposition_header

logger = logging.getLogger(__name__)

# Cuanto vive la URL que llega al bucket. Corta a proposito: el enlace del
# sistema no depende de ella, asi que alargarla no gana nada y solo amplia la
# ventana en la que una URL copiada del inspector sigue sirviendo el archivo a
# quien sea. Cinco minutos cubren de sobra abrir un PDF o cargar una miniatura.
SEGUNDOS_DE_VIDA = 300


def _sabe_firmar(almacen):
    """Si el almacen es el de S3/R2. El local no firma nada."""
    return hasattr(almacen, 'bucket_name') and hasattr(almacen, 'custom_domain')


def _almacen_que_firma(almacen):
    """
    El mismo almacen, pero sin dominio publico.

    `S3Storage.url()` mira `custom_domain` primero: mientras este definido
    devuelve el enlace publico sin firma, que es justo lo que queremos dejar de
    repartir. La copia superficial comparte la conexion de boto3 y el bucket ya
    resueltos, asi que no abre nada nuevo; lo unico que cambia es de donde sale
    la URL.

    Cuando `AWS_S3_CUSTOM_DOMAIN` se retire del entorno, esta funcion dejara de
    tener trabajo y el codigo seguira siendo correcto.
    """
    if not getattr(almacen, 'custom_domain', None):
        return almacen
    firmante = copy.copy(almacen)
    firmante.custom_domain = None
    firmante.querystring_auth = True
    return firmante


def url_firmada(archivo, descargar_como=None, segundos=SEGUNDOS_DE_VIDA):
    """
    URL temporal para un `FieldFile`, o `None` si el almacen no sabe firmar.

    Con `descargar_como` el bucket devuelve el archivo con su nombre de verdad
    -- el que puso quien lo subio, que vive en `original_name` -- en vez del
    nombre de la ruta, que lleva un identificador aleatorio por delante. El
    atributo `download` del enlace no sirve para esto: el navegador lo ignora
    cuando el archivo viene de otro dominio, que es siempre el caso aqui.
    """
    if not archivo or not archivo.name:
        return None

    almacen = archivo.storage
    if not _sabe_firmar(almacen):
        return None

    parametros = {}
    if descargar_como:
        cabecera = content_disposition_header(True, descargar_como)
        if cabecera:
            parametros['ResponseContentDisposition'] = cabecera

    try:
        return _almacen_que_firma(almacen).url(
            archivo.name, parameters=parametros or None, expire=segundos)
    except Exception as e:
        # Firmar habla con boto3 y puede fallar por credenciales o por red. Que
        # no se pueda firmar no debe tumbar la pantalla: la vista sirve el
        # archivo ella misma, mas lento pero igual de correcto.
        logger.warning('No se pudo firmar la URL de %s: %s', archivo.name, e)
        return None
