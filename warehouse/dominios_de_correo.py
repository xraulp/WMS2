"""
El remitente propio de cada empresa: que sus avisos y reportes salgan de su
dominio (`avisos@dysergroup.com`) y no del de la plataforma.

La regla es que el correo de una empresa sale de su dominio **solo si Resend lo
tiene verificado**. Con un dominio sin verificar la API responde 403 y el aviso
no llega; por eso, mientras no se verifique, se sigue mandando como siempre --
desde el dominio de la plataforma con el nombre de la empresa a la vista -- y
el envio nunca se rompe por un tramite de DNS pendiente.

Quien sabe si el dominio esta verificado es Resend, no una casilla que alguien
marca a mano: `revisar_remitente()` se lo pregunta a su API y deja anotado el
resultado en la empresa. Se pregunta al guardar la ficha y con el boton
"Verificar" de la lista de empresas; mandar un correo no consulta nada, solo
lee lo anotado.

Lo que nunca sale de aqui: la recuperacion de contrasena y la facturacion de la
plataforma. Esos correos son de quien opera el SaaS y salen siempre de su
dominio (`DEFAULT_FROM_EMAIL`, `BILLING_FROM_EMAIL`), configure lo que configure
la empresa.
"""
import logging

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

API_DOMINIOS = 'https://api.resend.com/domains'

# Los estados que devuelve Resend, mas dos propios: el dominio no esta dado de
# alta en la cuenta, o no se pudo preguntar.
VERIFICADO = 'verified'
NO_DADO_DE_ALTA = 'not_found'
SIN_CONSULTAR = 'error'


def dominio_de(correo):
    """`avisos@DysErGroup.com` -> `dysergroup.com`. Vacio si no hay arroba."""
    correo = (correo or '').strip()
    if '@' not in correo:
        return ''
    return correo.rsplit('@', 1)[1].lower()


def se_puede_usar_dominio_propio():
    """
    Solo por Resend. Por SMTP el correo sale del servidor de la plataforma, y
    firmar desde el dominio de otra empresa ahi es suplantarla: el servidor de
    destino lo marcaria como spam o lo rechazaria.
    """
    return (getattr(settings, 'EMAIL_PROVIDER', '') == 'resend'
            and bool(getattr(settings, 'RESEND_API_KEY', '')))


def consultar_dominio(dominio):
    """
    Pregunta a Resend en que estado esta `dominio`.

    Devuelve `(estado, detalle)`. El estado es el de Resend (`verified`,
    `pending`, `failed`, `not_started`, `temporary_failure`) o uno de los dos
    propios; el detalle es una frase para el operador cuando algo fallo.
    """
    import requests

    llave = getattr(settings, 'RESEND_API_KEY', '')
    if not llave:
        return SIN_CONSULTAR, 'RESEND_API_KEY no configurada'
    try:
        respuesta = requests.get(
            API_DOMINIOS, headers={'Authorization': f'Bearer {llave}'},
            timeout=getattr(settings, 'EMAIL_TIMEOUT', 10))
    except requests.RequestException as e:
        logger.warning('No se pudo consultar Resend: %s', e)
        return SIN_CONSULTAR, f'No se pudo contactar a Resend: {e}'

    if respuesta.status_code == 401 or respuesta.status_code == 403:
        # Una llave de "solo envio" puede mandar correo pero no listar
        # dominios. Es la causa mas probable y la que hay que decir.
        return SIN_CONSULTAR, ('La llave de Resend no puede leer los dominios: '
                               'hace falta una con permiso "Full access".')
    if respuesta.status_code >= 400:
        return SIN_CONSULTAR, f'Resend respondio {respuesta.status_code}'

    try:
        dominios = respuesta.json().get('data') or []
    except ValueError:
        return SIN_CONSULTAR, 'Resend devolvio una respuesta ilegible'

    for d in dominios:
        if (d.get('name') or '').lower() == dominio:
            return (d.get('status') or '').lower() or SIN_CONSULTAR, ''
    return NO_DADO_DE_ALTA, ''


def revisar_remitente(tenant):
    """
    Consulta el dominio del remitente propio de la empresa y anota el
    resultado. Devuelve el estado. Sin remitente propio, limpia lo anotado.
    """
    dominio = dominio_de(tenant.sender_email)
    if not dominio:
        estado, verificado = '', False
    elif not se_puede_usar_dominio_propio():
        estado, verificado = SIN_CONSULTAR, False
    else:
        estado, detalle = consultar_dominio(dominio)
        if detalle:
            logger.info('Remitente de %s: %s', tenant, detalle)
        verificado = estado == VERIFICADO

    tenant.sender_domain_status = estado
    tenant.sender_domain_verified = verificado
    tenant.sender_domain_checked_at = timezone.now() if dominio else None
    tenant.save(update_fields=['sender_domain_status', 'sender_domain_verified',
                               'sender_domain_checked_at'])
    return estado


def remitente_propio(tenant):
    """
    La direccion propia con la que sale el correo de la empresa, o None si
    debe salir del dominio de la plataforma.

    Una sucursal sin remitente propio usa el de su corporativo: es la misma
    empresa ante el cliente.
    """
    if not se_puede_usar_dominio_propio():
        return None
    for t in (tenant, getattr(tenant, 'parent', None)):
        if t is not None and t.sender_email and t.sender_domain_verified:
            return t.sender_email
    return None
