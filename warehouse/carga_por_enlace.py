"""
Subir la factura y los documentos de una entrada desde el correo que la avisa.

El correo de entrada pedia "favor de enviar la factura, BOL y demas
documentacion", y la respuesta llegaba como adjunto a un buzon: alguien tenia
que bajarla, buscar la entrada y subirla al expediente a mano. Mientras tanto la
tarea de cruce enseñaba la entrada sin factura.

Un correo no puede llevar un campo para subir archivos -- Gmail y Outlook
quitan los formularios --, asi que lo que lleva es un boton a esta pagina. La
pagina **no pide sesion**: quien recibe el aviso muchas veces es el proveedor o
el agente del cliente, gente sin usuario en el sistema. Lo que la protege es el
enlace mismo, firmado con la SECRET_KEY:

* sirve para **una sola entrada**, la del correo;
* **caduca** (`ENLACE_DOCUMENTOS_DIAS`, 60 por omision);
* **solo sube**: no enseña ni deja bajar lo que ya hay en el expediente, asi que
  un correo reenviado de mas no expone nada del cliente.

Lo que se sube entra por `guardar_en_expediente`, el mismo camino que el panel
Digital, y la factura se marca en la ranura de factura comercial: es lo que
mira `TareaDeCruce.operaciones_sin_factura`, de modo que la entrada llega a su
tarea de cruce ya con factura.
"""
import base64
import logging

from django.conf import settings
from django.core import signing
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from .models import OperationDocument, WarehouseOperation
from .utils import tenant_public_url

logger = logging.getLogger(__name__)

SAL = 'warehouse.carga-por-enlace'

# Lo que se acepta. La factura mexicana es un XML (CFDI) con su PDF, y lo demas
# son escaneos o fotos del papel; un ejecutable o un ZIP no tiene que entrar al
# expediente por una puerta sin sesion.
EXTENSIONES = ('pdf', 'xml', 'jpg', 'jpeg', 'png', 'webp', 'heic', 'gif',
               'doc', 'docx', 'xls', 'xlsx', 'csv', 'txt')
MAX_MB_POR_ARCHIVO = 20
MAX_ARCHIVOS = 15


def dias_de_vigencia():
    return int(getattr(settings, 'ENLACE_DOCUMENTOS_DIAS', 60))


def token_de(operation):
    """El token firmado que identifica la entrada. Lleva tambien la empresa."""
    return signing.dumps({'op': operation.pk, 't': operation.tenant_id},
                         salt=SAL, compress=True)


def enlace_para_subir(operation):
    """La URL completa que va en el boton del correo."""
    ruta = reverse('subir_por_enlace', args=[token_de(operation)])
    return f"{tenant_public_url(operation.tenant)}{ruta}"


def operacion_del_token(token):
    """
    La operacion a la que apunta el token, o `(None, motivo)`.

    El motivo distingue un enlace caducado de uno que no vale: al primero se le
    dice que pida otro, al segundo solo que no sirve.
    """
    try:
        datos = signing.loads(token, salt=SAL,
                              max_age=dias_de_vigencia() * 24 * 3600)
    except signing.SignatureExpired:
        return None, 'expired'
    except signing.BadSignature:
        return None, 'invalid'

    op = (WarehouseOperation.objects
          .filter(pk=datos.get('op'), tenant_id=datos.get('t'))
          .select_related('tenant', 'customer')
          .first())
    if op is None:
        return None, 'invalid'
    return op, ''


def _extension(nombre):
    return nombre.rsplit('.', 1)[-1].lower() if '.' in (nombre or '') else ''


def _problema_con(archivo):
    """Por que no se acepta un archivo, o cadena vacia si se acepta."""
    if _extension(archivo.name) not in EXTENSIONES:
        return _('%(nombre)s: this type of file is not accepted.') % {
            'nombre': archivo.name}
    if archivo.size > MAX_MB_POR_ARCHIVO * 1024 * 1024:
        return _('%(nombre)s: it is over %(mb)s MB.') % {
            'nombre': archivo.name, 'mb': MAX_MB_POR_ARCHIVO}
    return ''


def _logo_en_linea(tenant):
    """
    El logo de la empresa como data URI.

    El almacen es privado y esta pagina no tiene sesion para pedir la URL
    firmada; el logo pesa poco (2 MB de tope) y asi no hace falta abrir otra
    puerta publica solo para el.
    """
    archivo = getattr(tenant, 'logo', None)
    if not archivo:
        return ''
    try:
        archivo.open('rb')
        try:
            contenido = archivo.read()
        finally:
            archivo.close()
    except Exception:
        logger.warning('No se pudo leer el logo de %s para la carga por enlace',
                       tenant.name)
        return ''
    ext = _extension(archivo.name)
    tipo = {'jpg': 'jpeg', 'jpeg': 'jpeg', 'gif': 'gif'}.get(ext, 'png')
    return 'data:image/%s;base64,%s' % (tipo, base64.b64encode(contenido).decode('ascii'))


def _factura_actual(op):
    return op.documents.filter(
        ranura=OperationDocument.RANURA_FACTURA_COMERCIAL).first()


@require_http_methods(['GET', 'POST'])
def subir_por_enlace(request, token):
    # Import local: `views` importa medio proyecto y este modulo lo importa
    # `urls`, igual que a `views`; asi no hay ciclo al arrancar.
    from .notifications import en_el_idioma_de, resolve_customer
    from .views import guardar_en_expediente

    op, motivo = operacion_del_token(token)
    if op is None:
        return render(request, 'warehouse/carga_por_enlace.html',
                      {'motivo': motivo}, status=410 if motivo == 'expired' else 404)

    # La pagina se lee en el idioma del cliente, igual que el correo que la
    # trajo: quien llega aqui no tiene perfil del que sacar otro.
    with en_el_idioma_de(resolve_customer(op)):
        errores = []
        if request.method == 'POST':
            factura = request.FILES.get('factura')
            otros = request.FILES.getlist('documentos')
            todos = ([factura] if factura else []) + list(otros)

            if not todos:
                errores.append(_('Pick at least one file.'))
            elif len(todos) > MAX_ARCHIVOS:
                errores.append(_('Up to %(n)s files at a time.') % {'n': MAX_ARCHIVOS})
            else:
                errores = [p for p in map(_problema_con, todos) if p]

            if not errores:
                # La factura primero: asi queda delante en el expediente.
                guardados = guardar_en_expediente(op, todos, None)
                if factura:
                    # Una entrada tiene una factura comercial: la nueva
                    # sustituye la marca de la anterior, que se queda en el
                    # expediente como un documento mas.
                    op.documents.filter(
                        ranura=OperationDocument.RANURA_FACTURA_COMERCIAL
                    ).update(ranura='')
                    guardados[0].ranura = OperationDocument.RANURA_FACTURA_COMERCIAL
                    guardados[0].save(update_fields=['ranura'])
                logger.info('Carga por enlace en %s: %s archivo(s)',
                            op.custom_id, len(guardados))
                # PRG: recargar la pagina no vuelve a subir lo mismo.
                return HttpResponseRedirect(
                    f"{request.path}?ok={len(guardados)}"
                    f"{'&f=1' if factura else ''}")

        ok = request.GET.get('ok', '')
        return render(request, 'warehouse/carga_por_enlace.html', {
            'operation': op,
            'tenant': op.tenant,
            'logo': _logo_en_linea(op.tenant),
            'factura': _factura_actual(op),
            'errores': errores,
            'subidos': int(ok) if ok.isdigit() else 0,
            'subio_factura': request.GET.get('f') == '1',
            'aceptados': ','.join('.' + e for e in EXTENSIONES),
            'max_mb': MAX_MB_POR_ARCHIVO,
            'max_archivos': MAX_ARCHIVOS,
        }, status=400 if errores else 200)
