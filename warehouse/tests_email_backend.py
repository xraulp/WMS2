"""
Envio de correo por la API de Resend.

El motivo de que este backend exista es que Render bloquea la salida SMTP en el
plan gratuito, asi que estas pruebas cuidan lo que de eso depende: que el correo
que hoy sale por SMTP se traduzca sin perder nada al formato de la API — cuerpo
HTML, copias y sobre todo los adjuntos, que son el PDF del reporte y los
archivos del expediente.

No se toca la red: se sustituye `requests.post` y se inspecciona el JSON que el
backend habria mandado.
"""
import base64
import json
from email.mime.text import MIMEText
from unittest.mock import patch

from django.core.mail import EmailMessage, EmailMultiAlternatives
from django.test import SimpleTestCase, override_settings

from .email_backends import ResendBackend, ResendAPIError

RESEND = 'warehouse.email_backends.ResendBackend'
BACKEND_SETTINGS = dict(
    EMAIL_BACKEND='warehouse.email_backends.ResendBackend',
    RESEND_API_KEY='re_prueba',
    DEFAULT_FROM_EMAIL='avisos@dysergroup.com',
)


class RespuestaFalsa:
    """Lo minimo de una respuesta de `requests` que el backend consulta."""

    def __init__(self, status_code=200, payload=None, text=''):
        self.status_code = status_code
        self._payload = payload if payload is not None else {'id': 'msg_1'}
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError('sin JSON')
        return self._payload


class BackendTestBase(SimpleTestCase):
    def _enviar(self, message, respuestas=None, **kwargs):
        """
        Manda el mensaje con la red sustituida y devuelve (payloads, mock).

        `payloads` son los cuerpos JSON que el backend habria enviado, ya
        deserializados, en orden.
        """
        respuestas = respuestas or [RespuestaFalsa()]
        with patch('requests.post', side_effect=respuestas) as post:
            backend = ResendBackend(api_key='re_prueba', **kwargs)
            enviados = backend.send_messages([message])
        payloads = [llamada.kwargs['json'] for llamada in post.call_args_list]
        return payloads, post, enviados


class PayloadTests(BackendTestBase):
    """Como se traduce un EmailMessage al formato de la API."""

    def test_manda_remitente_destinatarios_y_asunto(self):
        msg = EmailMessage(subject='Recepcion', body='hola',
                           from_email='avisos@dysergroup.com',
                           to=['compras@cliente-a.com', 'juan@cliente-a.com'])

        (payload,), post, enviados = self._enviar(msg)

        self.assertEqual(enviados, 1)
        self.assertEqual(payload['from'], 'avisos@dysergroup.com')
        self.assertEqual(payload['to'], ['compras@cliente-a.com', 'juan@cliente-a.com'])
        self.assertEqual(payload['subject'], 'Recepcion')
        self.assertEqual(post.call_args.args[0], 'https://api.resend.com/emails')
        self.assertEqual(post.call_args.kwargs['headers']['Authorization'],
                         'Bearer re_prueba')

    def test_el_cuerpo_html_va_en_html_no_en_text(self):
        """
        Las notificaciones marcan content_subtype='html' y meten el HTML en el
        body. Si eso llegara como `text`, el cliente veria las etiquetas.
        """
        msg = EmailMessage(subject='s', body='<p>Mercancia recibida</p>',
                           to=['x@y.com'])
        msg.content_subtype = 'html'

        (payload,), _, _ = self._enviar(msg)

        self.assertEqual(payload['html'], '<p>Mercancia recibida</p>')
        self.assertNotIn('text', payload)

    def test_el_cuerpo_plano_va_en_text(self):
        msg = EmailMessage(subject='s', body='texto pelado', to=['x@y.com'])

        (payload,), _, _ = self._enviar(msg)

        self.assertEqual(payload['text'], 'texto pelado')
        self.assertNotIn('html', payload)

    def test_toma_el_html_de_las_alternativas(self):
        msg = EmailMultiAlternatives(subject='s', body='version texto',
                                     to=['x@y.com'])
        msg.attach_alternative('<b>version html</b>', 'text/html')

        (payload,), _, _ = self._enviar(msg)

        self.assertEqual(payload['text'], 'version texto')
        self.assertEqual(payload['html'], '<b>version html</b>')

    def test_incluye_copias_y_reply_to(self):
        """Los CC_EMAIL del catalogo llegan por aqui; perderlos seria silencioso."""
        msg = EmailMessage(subject='s', body='b', to=['x@y.com'],
                           cc=['copia@dysergroup.com'],
                           bcc=['oculta@dysergroup.com'],
                           reply_to=['operaciones@dysergroup.com'])

        (payload,), _, _ = self._enviar(msg)

        self.assertEqual(payload['cc'], ['copia@dysergroup.com'])
        self.assertEqual(payload['bcc'], ['oculta@dysergroup.com'])
        self.assertEqual(payload['reply_to'], ['operaciones@dysergroup.com'])

    def test_omite_las_claves_de_copia_cuando_no_hay(self):
        msg = EmailMessage(subject='s', body='b', to=['x@y.com'])

        (payload,), _, _ = self._enviar(msg)

        self.assertNotIn('cc', payload)
        self.assertNotIn('bcc', payload)
        self.assertNotIn('reply_to', payload)

    def test_un_mensaje_sin_destinatarios_no_llama_a_la_api(self):
        msg = EmailMessage(subject='s', body='b', to=[])

        payloads, post, _ = self._enviar(msg)

        self.assertEqual(payloads, [])
        post.assert_not_called()

    def test_el_payload_es_json_serializable(self):
        """
        Se manda con `json=`, asi que cualquier valor no serializable reventaria
        en produccion y no aqui. Se comprueba explicitamente.
        """
        msg = EmailMessage(subject='s', body='b', to=['x@y.com'])
        msg.attach('reporte.pdf', b'%PDF-1.4 binario', 'application/pdf')

        (payload,), _, _ = self._enviar(msg)

        json.dumps(payload)  # no debe levantar


class AdjuntosTests(BackendTestBase):
    """
    Los adjuntos son la parte delicada: el PDF del reporte y los archivos del
    expediente ya se perdieron una vez en silencio con la mudanza a R2.
    """

    def test_adjunta_en_base64_conservando_el_nombre(self):
        msg = EmailMessage(subject='s', body='b', to=['x@y.com'])
        msg.attach('ED260816-0001.pdf', b'%PDF-1.4 binario', 'application/pdf')

        (payload,), _, _ = self._enviar(msg)

        self.assertEqual(len(payload['attachments']), 1)
        adjunto = payload['attachments'][0]
        self.assertEqual(adjunto['filename'], 'ED260816-0001.pdf')
        self.assertEqual(base64.b64decode(adjunto['content']), b'%PDF-1.4 binario')

    def test_adjunta_varios_en_orden(self):
        msg = EmailMessage(subject='s', body='b', to=['x@y.com'])
        msg.attach('uno.pdf', b'uno', 'application/pdf')
        msg.attach('dos.jpg', b'dos', 'image/jpeg')

        (payload,), _, _ = self._enviar(msg)

        self.assertEqual([a['filename'] for a in payload['attachments']],
                         ['uno.pdf', 'dos.jpg'])

    def test_convierte_el_contenido_de_texto(self):
        """`attach()` acepta str; base64 solo trabaja con bytes."""
        msg = EmailMessage(subject='s', body='b', to=['x@y.com'])
        msg.attach('nota.txt', 'contenido de texto', 'text/plain')

        (payload,), _, _ = self._enviar(msg)

        self.assertEqual(
            base64.b64decode(payload['attachments'][0]['content']),
            b'contenido de texto')

    def test_acepta_un_adjunto_ya_construido_como_objeto_mime(self):
        """`attach()` con un objeto MIME no produce tupla, sino el objeto."""
        parte = MIMEText('desde mime')
        parte.add_header('Content-Disposition', 'attachment', filename='mime.txt')
        msg = EmailMessage(subject='s', body='b', to=['x@y.com'])
        msg.attach(parte)

        (payload,), _, _ = self._enviar(msg)

        adjunto = payload['attachments'][0]
        self.assertEqual(adjunto['filename'], 'mime.txt')
        self.assertEqual(base64.b64decode(adjunto['content']), b'desde mime')

    def test_sin_adjuntos_no_manda_la_clave(self):
        msg = EmailMessage(subject='s', body='b', to=['x@y.com'])

        (payload,), _, _ = self._enviar(msg)

        self.assertNotIn('attachments', payload)


class ErroresTests(BackendTestBase):
    """
    Que un fallo se pueda leer despues en NotificationLog.

    El backend anterior escondia los errores: el `except: pass` del WhatsApp y
    los adjuntos que no salian con R2 pasaron meses sin que nadie se enterara.
    Aqui el error tiene que llegar arriba con su texto.
    """

    def test_un_error_de_la_api_levanta_excepcion_con_el_motivo(self):
        respuesta = RespuestaFalsa(
            status_code=403,
            payload={'statusCode': 403, 'name': 'validation_error',
                     'message': 'The dysergroup.com domain is not verified'})

        with patch('requests.post', return_value=respuesta):
            backend = ResendBackend(api_key='re_prueba')
            with self.assertRaises(ResendAPIError) as ctx:
                backend.send_messages([
                    EmailMessage(subject='s', body='b', to=['x@y.com'])])

        self.assertIn('403', str(ctx.exception))
        self.assertIn('domain is not verified', str(ctx.exception))

    def test_un_error_sin_json_no_mete_una_pagina_entera_en_la_bitacora(self):
        respuesta = RespuestaFalsa(status_code=502, payload=None, text='<html>' + 'x' * 5000)

        with patch('requests.post', return_value=respuesta):
            backend = ResendBackend(api_key='re_prueba')
            with self.assertRaises(ResendAPIError) as ctx:
                backend.send_messages([
                    EmailMessage(subject='s', body='b', to=['x@y.com'])])

        self.assertLess(len(str(ctx.exception)), 300)

    def test_con_fail_silently_no_propaga_y_no_cuenta_el_envio(self):
        respuesta = RespuestaFalsa(status_code=500, payload={'message': 'boom'})

        with patch('requests.post', return_value=respuesta):
            backend = ResendBackend(api_key='re_prueba', fail_silently=True)
            enviados = backend.send_messages([
                EmailMessage(subject='s', body='b', to=['x@y.com'])])

        self.assertEqual(enviados, 0)

    def test_sin_llave_configurada_falla_en_vez_de_aparentar_exito(self):
        """
        Aparentar exito seria lo peor posible: el renglon de la bitacora diria
        Enviada y el cliente no recibiria nada.
        """
        with override_settings(RESEND_API_KEY=''):
            with patch('requests.post') as post:
                backend = ResendBackend(api_key='')
                with self.assertRaises(ResendAPIError):
                    backend.send_messages([
                        EmailMessage(subject='s', body='b', to=['x@y.com'])])
        post.assert_not_called()

    def test_reintenta_cuando_la_api_responde_429(self):
        """
        El plan gratuito limita a 2 peticiones por segundo y los avisos salen
        dentro del request, uno tras otro.
        """
        respuestas = [RespuestaFalsa(status_code=429, payload={'message': 'rate limit'}),
                      RespuestaFalsa()]

        with patch('warehouse.email_backends.time.sleep') as dormir:
            payloads, post, enviados = self._enviar(
                EmailMessage(subject='s', body='b', to=['x@y.com']),
                respuestas=respuestas)

        self.assertEqual(enviados, 1)
        self.assertEqual(post.call_count, 2)
        dormir.assert_called_once()

    def test_deja_de_reintentar_y_reporta_si_el_429_no_cede(self):
        respuestas = [RespuestaFalsa(status_code=429, payload={'message': 'rate limit'})
                      for _ in range(5)]

        with patch('warehouse.email_backends.time.sleep'):
            with patch('requests.post', side_effect=respuestas) as post:
                backend = ResendBackend(api_key='re_prueba')
                with self.assertRaises(ResendAPIError) as ctx:
                    backend.send_messages([
                        EmailMessage(subject='s', body='b', to=['x@y.com'])])

        self.assertEqual(post.call_count, 3)  # el intento inicial mas dos reintentos
        self.assertIn('429', str(ctx.exception))

    def test_usa_el_timeout_configurado(self):
        """
        Sin timeout, un servidor que no contesta cuelga el request hasta que
        gunicorn mata al worker. Fue exactamente lo que paso con el SMTP.
        """
        with override_settings(EMAIL_TIMEOUT=7):
            with patch('requests.post', return_value=RespuestaFalsa()) as post:
                ResendBackend(api_key='re_prueba').send_messages([
                    EmailMessage(subject='s', body='b', to=['x@y.com'])])

        self.assertEqual(post.call_args.kwargs['timeout'], 7)


@override_settings(**BACKEND_SETTINGS)
class SeleccionDeBackendTests(SimpleTestCase):
    """Que `EmailMessage.send()` acabe llamando a la API sin tocar el resto."""

    def test_el_envio_normal_de_django_pasa_por_resend(self):
        msg = EmailMessage(subject='s', body='<p>b</p>', to=['x@y.com'])
        msg.content_subtype = 'html'

        with patch('requests.post', return_value=RespuestaFalsa()) as post:
            enviados = msg.send()

        self.assertEqual(enviados, 1)
        post.assert_called_once()
        self.assertEqual(post.call_args.kwargs['json']['html'], '<p>b</p>')

    def test_toma_el_remitente_por_omision_de_los_settings(self):
        with patch('requests.post', return_value=RespuestaFalsa()) as post:
            EmailMessage(subject='s', body='b', to=['x@y.com']).send()

        self.assertEqual(post.call_args.kwargs['json']['from'],
                         'avisos@dysergroup.com')
