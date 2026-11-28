"""El webhook de WhatsApp (Cloud API de Meta) y el envío."""
import hashlib
import hmac
import json
from unittest import mock

from django.test import TestCase, override_settings

from . import whatsapp
from .models import NotificationLog

SECRETO = 'secreto-de-la-app'


def firmar(cuerpo):
    return 'sha256=' + hmac.new(SECRETO.encode(), cuerpo, hashlib.sha256).hexdigest()


def aviso(*estados):
    return {'object': 'whatsapp_business_account', 'entry': [{'changes': [{
        'field': 'messages', 'value': {'statuses': list(estados)}}]}]}


@override_settings(WHATSAPP_APP_SECRET=SECRETO, WHATSAPP_WEBHOOK_VERIFY_TOKEN='palabra')
class WebhookTests(TestCase):
    url = '/webhooks/whatsapp/'

    def setUp(self):
        self.renglon = NotificationLog.objects.create(
            channel='WHATSAPP', event='MANUAL', status='SENT',
            recipient='5218671234567', provider_id='wamid.A')

    def mandar(self, datos, firma=None):
        cuerpo = json.dumps(datos).encode()
        return self.client.post(self.url, cuerpo, content_type='application/json',
                                HTTP_X_HUB_SIGNATURE_256=firma or firmar(cuerpo))

    def estado(self):
        self.renglon.refresh_from_db()
        return self.renglon.status

    # La verificación con la que Meta da de alta la URL

    def test_verificacion_devuelve_el_challenge(self):
        r = self.client.get(self.url, {'hub.mode': 'subscribe',
                                       'hub.verify_token': 'palabra',
                                       'hub.challenge': '12345'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, b'12345')

    def test_verificacion_con_otra_palabra_se_rechaza(self):
        r = self.client.get(self.url, {'hub.mode': 'subscribe',
                                       'hub.verify_token': 'otra',
                                       'hub.challenge': '12345'})
        self.assertEqual(r.status_code, 403)

    @override_settings(WHATSAPP_WEBHOOK_VERIFY_TOKEN='')
    def test_sin_palabra_configurada_no_se_verifica_nada(self):
        r = self.client.get(self.url, {'hub.mode': 'subscribe',
                                       'hub.verify_token': '',
                                       'hub.challenge': '12345'})
        self.assertEqual(r.status_code, 403)

    # La firma

    def test_firma_invalida_no_toca_nada(self):
        r = self.mandar(aviso({'id': 'wamid.A', 'status': 'delivered'}),
                        firma='sha256=' + '0' * 64)
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.estado(), 'SENT')

    @override_settings(WHATSAPP_APP_SECRET='')
    def test_sin_secreto_no_se_acepta_nada(self):
        r = self.mandar(aviso({'id': 'wamid.A', 'status': 'delivered'}))
        self.assertEqual(r.status_code, 503)
        self.assertEqual(self.estado(), 'SENT')

    # Los estados

    def test_entregado_y_leido(self):
        self.mandar(aviso({'id': 'wamid.A', 'status': 'delivered'}))
        self.assertEqual(self.estado(), 'DELIVERED')
        self.mandar(aviso({'id': 'wamid.A', 'status': 'read'}))
        self.assertEqual(self.estado(), 'READ')

    def test_un_entregado_atrasado_no_tapa_el_leido(self):
        self.mandar(aviso({'id': 'wamid.A', 'status': 'read'},
                          {'id': 'wamid.A', 'status': 'delivered'}))
        self.assertEqual(self.estado(), 'READ')

    def test_el_fallo_guarda_el_codigo_y_el_motivo(self):
        r = self.mandar(aviso({'id': 'wamid.A', 'status': 'failed', 'errors': [{
            'code': 131047, 'title': 'Re-engagement message',
            'error_data': {'details': 'More than 24 hours have passed'}}]}))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.estado(), 'FAILED')
        self.assertEqual(self.renglon.detail,
                         '131047 Re-engagement message: More than 24 hours have passed')

    def test_un_aviso_de_otro_mensaje_no_toca_el_renglon(self):
        r = self.mandar(aviso({'id': 'wamid.OTRO', 'status': 'delivered'}))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.estado(), 'SENT')

    def test_no_toca_correos_con_el_mismo_id(self):
        correo = NotificationLog.objects.create(
            channel='EMAIL', event='MANUAL', status='SENT', provider_id='wamid.A')
        self.mandar(aviso({'id': 'wamid.A', 'status': 'delivered'}))
        correo.refresh_from_db()
        self.assertEqual(correo.status, 'SENT')

    def test_mensaje_entrante_se_contesta_200(self):
        r = self.mandar({'entry': [{'changes': [{'value': {
            'messages': [{'from': '5218671234567', 'type': 'text',
                          'text': {'body': 'hola'}}]}}]}]})
        self.assertEqual(r.status_code, 200)

    def test_json_invalido(self):
        cuerpo = b'no es json'
        r = self.client.post(self.url, cuerpo, content_type='application/json',
                             HTTP_X_HUB_SIGNATURE_256=firmar(cuerpo))
        self.assertEqual(r.status_code, 400)


@override_settings(WHATSAPP_TOKEN='tok', WHATSAPP_PHONE_NUMBER_ID='123')
class EnvioTests(TestCase):

    def respuesta(self, datos):
        r = mock.Mock()
        r.json.return_value = datos
        return r

    def test_texto_libre_devuelve_el_wamid(self):
        with mock.patch('requests.post', return_value=self.respuesta(
                {'messages': [{'id': 'wamid.X'}]})) as post:
            self.assertEqual(whatsapp.enviar('+52 867 123 4567', texto='hola'),
                             ('wamid.X', None))
        cuerpo = post.call_args.kwargs['json']
        self.assertEqual(cuerpo['to'], '528671234567')
        self.assertEqual(cuerpo['text'], {'body': 'hola'})

    def test_plantilla_con_variables(self):
        with mock.patch('requests.post', return_value=self.respuesta(
                {'messages': [{'id': 'wamid.Y'}]})) as post:
            whatsapp.enviar('528671234567', plantilla='operacion_registrada',
                            idioma='es_MX', variables=['ENT-1', 'PO 77'])
        t = post.call_args.kwargs['json']['template']
        self.assertEqual(t['name'], 'operacion_registrada')
        self.assertEqual(t['language'], {'code': 'es_MX'})
        self.assertEqual([p['text'] for p in t['components'][0]['parameters']],
                         ['ENT-1', 'PO 77'])

    def test_error_de_meta_en_palabras(self):
        with mock.patch('requests.post', return_value=self.respuesta({'error': {
                'code': 131030, 'message': '(#131030) Recipient phone number not in allowed list',
                'error_data': {'details': 'Add recipient phone number'}}})):
            wamid, error = whatsapp.enviar('1555', texto='hola')
        self.assertEqual(wamid, '')
        self.assertIn('131030', error)
        self.assertIn('Add recipient phone number', error)

    @override_settings(WHATSAPP_TOKEN='')
    def test_sin_configurar(self):
        self.assertEqual(whatsapp.enviar('1555', texto='hola'),
                         ('', 'whatsapp_not_configured'))
