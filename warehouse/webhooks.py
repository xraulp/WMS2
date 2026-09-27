"""
Lo que Resend cuenta de un correo después de aceptarlo.

`NotificationLog` anotaba «enviada» en cuanto la API contestaba 200, y eso solo
quiere decir que Resend **aceptó** el mensaje. Si la dirección no existe, el
rebote llega minutos después y nadie se enteraba: un `billing_email` mal
tecleado se veía como enviado para siempre, y la bitácora de envíos contestaba
«sí le llegó» justo en el caso en que no.

Resend avisa por webhook de cada cosa que le pasa al correo. Aquí se reciben
esos avisos y se mueve el renglón que tiene el mismo `provider_id`:

    email.delivered         SENT            -> DELIVERED
    email.bounced           SENT/DELIVERED  -> BOUNCED, con el motivo
    email.complained        SENT/DELIVERED  -> COMPLAINED
    email.failed            SENT/DELIVERED  -> FAILED, con el motivo
    email.delivery_delayed  se anota en el detalle, el estado no cambia

**Un estado nunca retrocede.** Los avisos no llegan necesariamente en orden y
Resend reintenta los que no se le confirmaron, así que un «delivered» atrasado
no puede tapar un rebote que ya se registró. Por la misma razón procesar el
mismo aviso dos veces no cambia nada.

Configuración: en Resend, **Webhooks → Add Endpoint** con la URL
`https://<dominio>/webhooks/resend/` y los eventos de arriba; la *Signing
Secret* que muestra va en la variable de entorno `RESEND_WEBHOOK_SECRET`.
`docs/configurar-correo-resend.md` lo explica paso a paso.
"""
import base64
import hashlib
import hmac
import json
import logging
import time

from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import NotificationLog

logger = logging.getLogger(__name__)

# Cuánto puede tener de viejo un aviso firmado. Es la ventana que usa la propia
# librería de Svix, que es quien firma por Resend: sin ella, alguien que
# capturara un aviso legítimo podría reenviarlo cuando quisiera.
TOLERANCIA_SEGUNDOS = 5 * 60

# Qué pesa cada estado. Uno solo se sustituye por otro de más peso.
PESO = {
    'SENT': 0,
    'DELIVERED': 1,
    'BOUNCED': 2,
    'COMPLAINED': 2,
    'FAILED': 2,
}

ESTADO_DEL_EVENTO = {
    'email.delivered': 'DELIVERED',
    'email.bounced': 'BOUNCED',
    'email.complained': 'COMPLAINED',
    'email.failed': 'FAILED',
}


# ── FIRMA ─────────────────────────────────────────────────────────────────────

def firma_valida(secreto, cuerpo, cabeceras, ahora=None):
    """
    Comprueba la firma que Svix pone en nombre de Resend.

    Lo firmado es `"{svix-id}.{svix-timestamp}.{cuerpo}"` con HMAC-SHA256, y la
    llave es la parte en base64 del secreto `whsec_...`. La cabecera
    `svix-signature` puede traer varias firmas separadas por espacios (durante
    una rotación de secreto llegan la vieja y la nueva), cada una como `v1,<b64>`;
    basta con que coincida una.

    Se escribe aquí y no con la librería `svix` porque son quince líneas y no
    justifican una dependencia más en el despliegue.
    """
    id_del_aviso = cabeceras.get('svix-id') or ''
    sello = cabeceras.get('svix-timestamp') or ''
    firmas = cabeceras.get('svix-signature') or ''
    if not (secreto and id_del_aviso and sello and firmas):
        return False

    try:
        sello_int = int(sello)
    except ValueError:
        return False
    ahora = time.time() if ahora is None else ahora
    if abs(ahora - sello_int) > TOLERANCIA_SEGUNDOS:
        return False

    llave = secreto.split('_', 1)[1] if secreto.startswith('whsec_') else secreto
    try:
        llave = base64.b64decode(llave)
    except (ValueError, TypeError):
        logger.error('RESEND_WEBHOOK_SECRET no es base64 valido')
        return False

    if isinstance(cuerpo, str):
        cuerpo = cuerpo.encode('utf-8')
    firmado = id_del_aviso.encode() + b'.' + sello.encode() + b'.' + cuerpo
    esperada = base64.b64encode(
        hmac.new(llave, firmado, hashlib.sha256).digest()).decode()

    for parte in firmas.split():
        version, _, valor = parte.partition(',')
        if version == 'v1' and hmac.compare_digest(valor, esperada):
            return True
    return False


# ── LO QUE CADA AVISO LE HACE AL RENGLÓN ──────────────────────────────────────

def motivo_del_aviso(tipo, datos):
    """
    El texto que queda en el detalle del renglón, para que soporte lea el porqué.

    El de un rebote es lo que el servidor de destino contestó —«mailbox does not
    exist» y parecidos—, que es exactamente lo que hay que enseñarle a quien
    tecleó la dirección.
    """
    if tipo == 'email.bounced':
        rebote = datos.get('bounce') or {}
        clase = ' / '.join(x for x in (rebote.get('type'), rebote.get('subType')) if x)
        mensaje = rebote.get('message') or ''
        return ('Bounced (%s): %s' % (clase, mensaje) if clase
                else 'Bounced: %s' % mensaje).strip().rstrip(':')
    if tipo == 'email.complained':
        return 'The recipient marked it as spam'
    if tipo == 'email.failed':
        fallo = datos.get('failed') or {}
        return 'Failed after sending: %s' % (fallo.get('reason') or 'no reason given')
    if tipo == 'email.delivery_delayed':
        return 'Delivery delayed; the provider keeps retrying'
    return ''


def aplicar_aviso(evento):
    """
    Mueve el renglón que corresponde al aviso. Devuelve cuántos se tocaron.

    Un correo con varios destinatarios es un solo renglón y un solo id, así que
    el rebote de una de las direcciones marca el renglón entero. Es lo correcto
    para la pregunta que se le hace a la bitácora: si una de las direcciones
    rebotó, alguien no recibió el aviso, y el motivo dice cuál.
    """
    tipo = evento.get('type') or ''
    datos = evento.get('data') or {}
    id_del_correo = datos.get('email_id') or ''
    if not id_del_correo:
        return 0

    renglones = NotificationLog.objects.filter(provider_id=id_del_correo,
                                               channel='EMAIL')
    nuevo = ESTADO_DEL_EVENTO.get(tipo)
    motivo = motivo_del_aviso(tipo, datos)
    tocados = 0

    for renglon in renglones:
        cambios = []
        if nuevo and PESO.get(nuevo, 0) > PESO.get(renglon.status, 99):
            renglon.status = nuevo
            cambios.append('status')
            if motivo:
                renglon.detail = motivo
                cambios.append('detail')
        elif (not nuevo and motivo and renglon.status == 'SENT'
              and not renglon.detail):
            # Un retraso no es un estado: el correo puede llegar todavía. Solo
            # se anota mientras nada más definitivo haya dicho otra cosa.
            renglon.detail = motivo
            cambios.append('detail')

        if cambios:
            renglon.save(update_fields=cambios)
            tocados += 1
    return tocados


# ── LA VISTA ──────────────────────────────────────────────────────────────────

@csrf_exempt
@require_POST
def resend_webhook(request):
    """
    Recibe los avisos de Resend.

    Sin sesión y sin CSRF, porque quien llama es Resend y no un navegador; lo
    que lo protege es la firma. **Sin secreto configurado no se acepta nada**:
    un endpoint sin firmar le permitiría a cualquiera marcar como rebotados los
    correos que quisiera.

    A un aviso válido que no corresponde a ningún renglón se le contesta 200
    igual: son los correos que no pasan por la bitácora —la recuperación de
    contraseña, los informes— y contestar con error solo haría que Resend los
    reintentara durante días.
    """
    secreto = getattr(settings, 'RESEND_WEBHOOK_SECRET', '') or ''
    if not secreto:
        logger.warning('Aviso de Resend rechazado: RESEND_WEBHOOK_SECRET no configurada')
        return HttpResponse('Webhook not configured', status=503)

    cuerpo = request.body
    cabeceras = {
        'svix-id': request.headers.get('svix-id'),
        'svix-timestamp': request.headers.get('svix-timestamp'),
        'svix-signature': request.headers.get('svix-signature'),
    }
    if not firma_valida(secreto, cuerpo, cabeceras):
        logger.warning('Aviso de Resend con firma invalida (svix-id=%s)',
                       cabeceras['svix-id'])
        return HttpResponse('Invalid signature', status=401)

    try:
        evento = json.loads(cuerpo.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return HttpResponseBadRequest('Invalid JSON')
    if not isinstance(evento, dict):
        return HttpResponseBadRequest('Invalid JSON')

    tocados = aplicar_aviso(evento)
    logger.info('Aviso de Resend %s para %s: %s renglon(es)', evento.get('type'),
                (evento.get('data') or {}).get('email_id'), tocados)
    return HttpResponse('ok')
