"""
WhatsApp por la Cloud API de Meta: el envío y lo que Meta cuenta después.

Igual que con el correo, que la API conteste 200 solo quiere decir que Meta
**aceptó** el mensaje. Si no lo puede entregar —el número no tiene WhatsApp, se
mandó texto libre fuera de la ventana de 24 horas, la cuenta no tiene método de
pago— el fallo llega después y solo por webhook. Sin él, la pantalla de Meta dice
«enviado» y el mensaje no llega nunca, que es justo lo que pasó en la primera
prueba con el número de prueba.

Meta avisa de cada cambio de estado de un mensaje, y aquí se mueve el renglón de
`NotificationLog` que tiene el mismo `provider_id` (el `wamid` que dio la API):

    sent        -> SENT
    delivered   -> DELIVERED
    read        -> READ
    failed      -> FAILED, con el código y el motivo que da Meta

**Un estado nunca retrocede**, por lo mismo que en `webhooks.py`: los avisos no
llegan en orden (un «read» puede llegar antes que su «delivered») y Meta reintenta
los que no se le confirmaron.

Configuración: `docs/configurar-whatsapp-meta.md`.
"""
import hashlib
import hmac
import json
import logging

from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt

from .models import NotificationLog

logger = logging.getLogger(__name__)

GRAPH = 'https://graph.facebook.com/v21.0'

PESO = {
    'SENT': 0,
    'DELIVERED': 1,
    'READ': 2,
    'FAILED': 3,
}

ESTADO_DE_META = {
    'sent': 'SENT',
    'delivered': 'DELIVERED',
    'read': 'READ',
    'failed': 'FAILED',
}


# ── ENVÍO ─────────────────────────────────────────────────────────────────────

def configurado():
    return bool(getattr(settings, 'WHATSAPP_TOKEN', '')
                and getattr(settings, 'WHATSAPP_PHONE_NUMBER_ID', ''))


def solo_digitos(numero):
    """
    El número como lo quiere la API: lada de país y número, sin `+` ni espacios.

    Para México basta `52` + los 10 dígitos: Meta agrega por su cuenta el `1`
    que WhatsApp usa por dentro (`521…`), lo que se comprobó en la prueba.
    """
    return ''.join(c for c in (numero or '') if c.isdigit())


def enviar(numero, texto=None, plantilla=None, idioma='es_MX', variables=None):
    """
    Manda un mensaje y devuelve `(wamid, error)`; uno de los dos va vacío.

    `texto` es texto libre: Meta solo lo entrega si el destinatario le escribió
    al número en las últimas 24 horas. Fuera de esa ventana hay que mandar una
    `plantilla` aprobada, con sus `variables` en orden ({{1}}, {{2}}, …).
    """
    import requests

    token = getattr(settings, 'WHATSAPP_TOKEN', '')
    phone_id = getattr(settings, 'WHATSAPP_PHONE_NUMBER_ID', '')
    if not (token and phone_id):
        return '', 'whatsapp_not_configured'

    cuerpo = {'messaging_product': 'whatsapp', 'to': solo_digitos(numero)}
    if plantilla:
        plantilla_json = {'name': plantilla, 'language': {'code': idioma}}
        if variables:
            plantilla_json['components'] = [{
                'type': 'body',
                'parameters': [{'type': 'text', 'text': str(v)} for v in variables],
            }]
        cuerpo.update(type='template', template=plantilla_json)
    else:
        cuerpo.update(type='text', text={'body': texto or ''})

    try:
        r = requests.post(f'{GRAPH}/{phone_id}/messages', json=cuerpo, timeout=15,
                          headers={'Authorization': f'Bearer {token}'})
        datos = r.json()
    except Exception as e:  # red caída, respuesta que no es JSON
        return '', str(e)

    if 'error' in datos:
        err = datos['error']
        detalle = (err.get('error_data') or {}).get('details') or ''
        return '', ' '.join(x for x in (
            f"{err.get('code', '')}:", err.get('message', ''), detalle) if x).strip()
    try:
        return datos['messages'][0]['id'], None
    except (KeyError, IndexError, TypeError):
        return '', f'Respuesta inesperada de Meta: {datos}'


# ── FIRMA ─────────────────────────────────────────────────────────────────────

def firma_valida(secreto, cuerpo, cabecera):
    """
    Meta firma el cuerpo crudo con HMAC-SHA256 y el *App Secret* de la app, y lo
    manda en `X-Hub-Signature-256: sha256=<hex>`.
    """
    if not (secreto and cabecera and cabecera.startswith('sha256=')):
        return False
    if isinstance(cuerpo, str):
        cuerpo = cuerpo.encode('utf-8')
    esperada = hmac.new(secreto.encode('utf-8'), cuerpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(cabecera[len('sha256='):], esperada)


# ── LO QUE CADA AVISO LE HACE AL RENGLÓN ──────────────────────────────────────

def motivo_del_fallo(estado):
    """
    `131047 Re-engagement message: Message failed to send because more than 24
    hours have passed…` — el código es lo que se busca en la documentación de
    Meta, y el resto es lo que hay que leerle a quien pregunte por qué no llegó.
    """
    partes = []
    for e in estado.get('errors') or []:
        detalle = (e.get('error_data') or {}).get('details') or e.get('message') or ''
        titulo = e.get('title') or ''
        texto = f"{e.get('code', '')} {titulo}".strip()
        partes.append(f'{texto}: {detalle}' if detalle and detalle != titulo else texto)
    return ' | '.join(partes) or 'Failed (Meta did not say why)'


def aplicar_estado(estado):
    """Mueve los renglones del mensaje. Devuelve cuántos se tocaron."""
    wamid = estado.get('id') or ''
    nuevo = ESTADO_DE_META.get(estado.get('status') or '')
    if not (wamid and nuevo):
        return 0

    tocados = 0
    for renglon in NotificationLog.objects.filter(provider_id=wamid, channel='WHATSAPP'):
        if PESO[nuevo] <= PESO.get(renglon.status, -1):
            continue
        renglon.status = nuevo
        cambios = ['status']
        if nuevo == 'FAILED':
            renglon.detail = motivo_del_fallo(estado)
            cambios.append('detail')
        renglon.save(update_fields=cambios)
        tocados += 1

    if nuevo == 'FAILED':
        # Va al log aunque no haya renglón: los que se mandan desde la pantalla
        # de Meta no pasan por la bitácora y su porqué también hay que verlo.
        logger.warning('WhatsApp %s fallo: %s', wamid, motivo_del_fallo(estado))
    return tocados


def aplicar_aviso(aviso):
    """Recorre un aviso de Meta, que puede traer varios estados a la vez."""
    tocados = 0
    for entrada in aviso.get('entry') or []:
        for cambio in entrada.get('changes') or []:
            valor = cambio.get('value') or {}
            for estado in valor.get('statuses') or []:
                tocados += aplicar_estado(estado)
            for mensaje in valor.get('messages') or []:
                # Los mensajes que los clientes le escriben al número. Todavía
                # no se contestan desde el WMS; solo se deja constancia de que
                # llegó uno, sin su contenido.
                logger.info('WhatsApp: mensaje entrante (%s)', mensaje.get('type'))
    return tocados


# ── LA VISTA ──────────────────────────────────────────────────────────────────

@csrf_exempt
def whatsapp_webhook(request):
    """
    GET: la verificación con la que Meta da de alta la URL. Manda `hub.mode`,
    `hub.verify_token` y `hub.challenge`, y espera que se le devuelva el
    challenge si el token es el que se puso en la app.

    POST: los avisos. Sin `WHATSAPP_APP_SECRET` no se acepta ninguno, por lo
    mismo que el de Resend: sin firma, cualquiera podría marcar mensajes como
    leídos o fallidos.
    """
    if request.method == 'GET':
        esperado = getattr(settings, 'WHATSAPP_WEBHOOK_VERIFY_TOKEN', '') or ''
        if (esperado and request.GET.get('hub.mode') == 'subscribe'
                and hmac.compare_digest(request.GET.get('hub.verify_token', ''), esperado)):
            return HttpResponse(request.GET.get('hub.challenge', ''),
                                content_type='text/plain')
        return HttpResponse('Forbidden', status=403)

    if request.method != 'POST':
        return HttpResponse(status=405)

    secreto = getattr(settings, 'WHATSAPP_APP_SECRET', '') or ''
    if not secreto:
        logger.warning('Aviso de WhatsApp rechazado: WHATSAPP_APP_SECRET no configurada')
        return HttpResponse('Webhook not configured', status=503)

    cuerpo = request.body
    if not firma_valida(secreto, cuerpo, request.headers.get('X-Hub-Signature-256', '')):
        logger.warning('Aviso de WhatsApp con firma invalida')
        return HttpResponse('Invalid signature', status=401)

    try:
        aviso = json.loads(cuerpo.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return HttpResponseBadRequest('Invalid JSON')
    if not isinstance(aviso, dict):
        return HttpResponseBadRequest('Invalid JSON')

    tocados = aplicar_aviso(aviso)
    logger.info('Aviso de WhatsApp: %s renglon(es)', tocados)
    return HttpResponse('ok')
