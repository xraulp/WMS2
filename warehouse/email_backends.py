"""
Backend de correo que envía por la API HTTPS de Resend en vez de por SMTP.

Existe por una razón muy concreta: **Render bloquea el tráfico saliente a los
puertos SMTP** (25, 465 y 587) en los servicios web del plan gratuito desde
septiembre de 2025. La conexión no se rechaza, se queda colgada; gunicorn mata
al worker a los 30 segundos y el operador ve un "Internal Server Error". Se
comprobó que el servidor de correo propio (`vmail.globalpc.net`) solo atiende el
465 con SSL y que desde una red sin restricciones funciona, así que el problema
es la salida desde Render, no las credenciales.

Como Resend recibe el correo por HTTPS, el bloqueo de puertos no le afecta.

Todo el envío del sistema pasa por `EmailMessage.send()` (ver
`warehouse/notifications.py`), así que sustituir el backend es suficiente: no
hay que tocar ni el sistema de notificaciones ni las plantillas.

Configuración (variables de entorno):

    EMAIL_PROVIDER=resend
    RESEND_API_KEY=re_...
    DEFAULT_FROM_EMAIL=avisos@dysergroup.com   # dominio verificado en Resend

El remitente **tiene que pertenecer a un dominio verificado en Resend**; con
cualquier otro la API responde 403 y el fallo queda anotado en NotificationLog.
"""
import base64
import logging
import time

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

logger = logging.getLogger(__name__)

API_URL = 'https://api.resend.com/emails'

# El plan gratuito de Resend limita a 2 peticiones por segundo. Los avisos se
# mandan dentro del request (una salida que despacha varias entradas puede
# generar varios correos seguidos), así que un 429 es plausible y se reintenta
# en vez de darlo por fallido.
MAX_RETRIES = 2
RETRY_WAIT_SECONDS = 1.0


class ResendAPIError(Exception):
    """Error devuelto por la API de Resend, con el texto tal cual lo mandó."""


class ResendBackend(BaseEmailBackend):
    """
    Manda cada mensaje con un POST a la API de Resend.

    No abre ni cierra conexiones — cada envío es una petición HTTPS
    independiente — así que `open()` y `close()` de la clase base no hacen nada.
    """

    def __init__(self, fail_silently=False, api_key=None, timeout=None, **kwargs):
        super().__init__(fail_silently=fail_silently, **kwargs)
        self.api_key = api_key or getattr(settings, 'RESEND_API_KEY', '') or ''
        self.timeout = timeout or getattr(settings, 'EMAIL_TIMEOUT', 10)

    def send_messages(self, email_messages):
        if not email_messages:
            return 0

        if not self.api_key:
            # Sin llave no hay nada que intentar. Se levanta la excepción para
            # que el motivo quede en el `detail` de NotificationLog en vez de
            # aparecer como un envío exitoso.
            if self.fail_silently:
                logger.warning('RESEND_API_KEY no configurada; no se envio ningun correo')
                return 0
            raise ResendAPIError('RESEND_API_KEY no configurada')

        enviados = 0
        for message in email_messages:
            try:
                self._send(message)
            except Exception:
                if not self.fail_silently:
                    raise
                logger.exception('Fallo el envio por Resend')
                continue
            enviados += 1
        return enviados

    # ── construcción del payload ──────────────────────────────────────────────

    def _send(self, message):
        recipients = message.recipients()
        if not recipients:
            return

        payload = self._build_payload(message)
        self._post(payload)

    def _build_payload(self, message):
        payload = {
            'from': message.from_email or settings.DEFAULT_FROM_EMAIL,
            'to': list(message.to),
            'subject': message.subject or '',
        }
        if message.cc:
            payload['cc'] = list(message.cc)
        if message.bcc:
            payload['bcc'] = list(message.bcc)
        if message.reply_to:
            payload['reply_to'] = list(message.reply_to)

        payload.update(self._bodies(message))

        adjuntos = self._attachments(message)
        if adjuntos:
            payload['attachments'] = adjuntos

        # Resend exige al menos uno de html/text; un correo sin cuerpo se
        # rechaza con 422 y el mensaje de error no es evidente.
        if not payload.get('html') and not payload.get('text'):
            payload['text'] = ''
        return payload

    def _bodies(self, message):
        """
        Reparte el cuerpo entre `html` y `text`.

        Las notificaciones marcan `content_subtype = 'html'` y mandan el HTML en
        el body; los `EmailMultiAlternatives` traen el HTML como alternativa. Se
        cubren los dos casos.
        """
        cuerpo = message.body or ''
        if getattr(message, 'content_subtype', 'plain') == 'html':
            bodies = {'html': cuerpo}
        else:
            bodies = {'text': cuerpo}

        for contenido, mimetype in getattr(message, 'alternatives', None) or []:
            if mimetype == 'text/html':
                bodies['html'] = contenido if isinstance(contenido, str) else str(contenido)
                break
        return bodies

    def _attachments(self, message):
        """
        Convierte los adjuntos de Django al formato de Resend: nombre más
        contenido en base64.

        `message.attachments` trae tuplas `(nombre, contenido, mimetype)` — que
        es lo que produce `EmailMessage.attach()` — o objetos MIME cuando se usó
        `attach()` con un objeto ya construido.
        """
        adjuntos = []
        for adjunto in getattr(message, 'attachments', None) or []:
            if isinstance(adjunto, (tuple, list)):
                nombre = adjunto[0] or 'adjunto'
                contenido = adjunto[1]
            else:
                nombre = adjunto.get_filename() or 'adjunto'
                contenido = adjunto.get_payload(decode=True)

            if contenido is None:
                continue
            if isinstance(contenido, str):
                contenido = contenido.encode('utf-8')

            adjuntos.append({
                'filename': nombre,
                'content': base64.b64encode(contenido).decode('ascii'),
            })
        return adjuntos

    # ── llamada HTTP ──────────────────────────────────────────────────────────

    def _post(self, payload):
        # Import local: `requests` lo arrastran boto3 y twilio, pero mantenerlo
        # fuera del import del módulo evita que un entorno sin él (por ejemplo
        # una consola de management) reviente al cargar los settings.
        import requests

        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
        }

        intento = 0
        while True:
            respuesta = requests.post(API_URL, json=payload, headers=headers,
                                      timeout=self.timeout)
            if respuesta.status_code == 429 and intento < MAX_RETRIES:
                intento += 1
                time.sleep(RETRY_WAIT_SECONDS * intento)
                continue
            break

        if respuesta.status_code >= 400:
            raise ResendAPIError(self._describe_error(respuesta))
        return respuesta

    @staticmethod
    def _describe_error(respuesta):
        """
        Mensaje legible para el `detail` de NotificationLog.

        Resend contesta `{"statusCode":..,"name":..,"message":..}`, pero ante un
        error de infraestructura puede llegar HTML; en ese caso se recorta para
        no meter una página entera en la bitácora.
        """
        try:
            datos = respuesta.json()
            detalle = datos.get('message') or datos.get('name') or str(datos)
        except ValueError:
            detalle = (respuesta.text or '')[:200]
        return f'Resend HTTP {respuesta.status_code}: {detalle}'
