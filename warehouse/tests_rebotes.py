"""
Lo que le pasa a un correo después de que Resend lo acepta.

La bitácora anotaba «enviada» con el 200 de la API, que solo dice que el
proveedor tomó el mensaje. Un `billing_email` mal tecleado rebotaba minutos
después y la bitácora seguía diciendo «enviada» para siempre. Ahora el id que
Resend le da a cada correo se guarda en el renglón, y el webhook mueve ese
renglón a entregada, rebotada o marcada como spam.
"""
import base64
import hashlib
import hmac
import json
import time
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.mail import EmailMessage
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import notifications
from .email_backends import ResendBackend
from .models import (Catalog, Invoice, NotificationLog, Tenant, UserProfile,
                     WarehouseOperation)
from .tests_email_backend import RespuestaFalsa
from .webhooks import firma_valida

# Un secreto con la forma de los de Svix: `whsec_` y la llave en base64.
LLAVE = b'llave-de-prueba-de-32-bytes-1234'
SECRETO = 'whsec_' + base64.b64encode(LLAVE).decode()

RESEND = dict(EMAIL_BACKEND='warehouse.email_backends.ResendBackend',
              RESEND_API_KEY='re_prueba',
              DEFAULT_FROM_EMAIL='no-reply@plataforma.com')


def firmar(cuerpo, id_del_aviso='msg_aviso_1', sello=None, llave=LLAVE):
    """Las tres cabeceras que Svix le pone a un aviso, firmadas con `llave`."""
    sello = str(int(time.time()) if sello is None else sello)
    firmado = f'{id_del_aviso}.{sello}.'.encode() + cuerpo
    firma = base64.b64encode(hmac.new(llave, firmado, hashlib.sha256).digest()).decode()
    return {'svix-id': id_del_aviso, 'svix-timestamp': sello,
            'svix-signature': f'v1,{firma}'}


def aviso(tipo, email_id='re_correo_1', **datos):
    return json.dumps({
        'type': tipo,
        'created_at': '2026-09-27T12:00:00.000Z',
        'data': {'email_id': email_id, 'to': ['pagos@norte.com'], **datos},
    }).encode()


# ── EL ID DEL ENVÍO ───────────────────────────────────────────────────────────

class ElBackendGuardaElIdTests(SimpleTestCase):

    def _mandar(self, respuesta):
        msg = EmailMessage(subject='x', body='y', from_email='a@plataforma.com',
                           to=['b@cliente.com'])
        with patch('requests.post', return_value=respuesta):
            ResendBackend(api_key='re_prueba').send_messages([msg])
        return msg

    def test_deja_en_el_mensaje_el_id_que_devuelve_resend(self):
        msg = self._mandar(RespuestaFalsa(payload={'id': 're_abc123'}))
        self.assertEqual(msg.resend_id, 're_abc123')

    def test_una_respuesta_sin_json_no_convierte_el_envio_en_fallo(self):
        """El correo ya salió; lo único que se pierde es enterarse del rebote."""
        respuesta = RespuestaFalsa()
        respuesta._payload = None
        msg = self._mandar(respuesta)
        self.assertEqual(msg.resend_id, '')


class BaseConEmpresa(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte',
            billing_email='pagos@norte.com')
        cls.cliente = Catalog.objects.create(
            tenant=cls.tenant, category='CUSTOMER', name='Ferreteria del Valle',
            contact_email='compras@ferreteria.com')
        cls.staff = User.objects.create_user('operador', password='x')
        UserProfile.objects.create(user=cls.staff, tenant=cls.tenant, role='admin')


@override_settings(**RESEND)
class LaBitacoraGuardaElIdTests(BaseConEmpresa):

    def test_el_aviso_de_una_operacion(self):
        op = WarehouseOperation.objects.create(
            tenant=self.tenant, operation_type='ENTRY', custom_id='ED-0001',
            customer=self.cliente, description='Mercancia de prueba')
        with patch('requests.post',
                   return_value=RespuestaFalsa(payload={'id': 're_aviso'})):
            notifications.notify_operation_created(op, triggered_by=self.staff)

        renglon = NotificationLog.objects.get(channel='EMAIL', status='SENT')
        self.assertEqual(renglon.provider_id, 're_aviso')

    def test_la_factura_de_la_plataforma(self):
        """El caso que motivó todo: un correo de facturación mal tecleado."""
        hoy = timezone.localdate()
        factura = Invoice.objects.create(
            tenant=self.tenant, numero='F-0001',
            periodo_inicio=hoy - timedelta(days=30), periodo_fin=hoy,
            emitida_el=hoy, vence_el=hoy + timedelta(days=15),
            plan=self.tenant.plan, monto_usd=Decimal('100.00'))
        with patch('requests.post',
                   return_value=RespuestaFalsa(payload={'id': 're_factura'})):
            notifications.enviar_factura(factura)

        renglon = NotificationLog.objects.get(event='INVOICE_SENT')
        self.assertEqual(renglon.provider_id, 're_factura')


# ── LA FIRMA ──────────────────────────────────────────────────────────────────

class LaFirmaTests(SimpleTestCase):
    cuerpo = aviso('email.delivered')

    def test_una_firma_correcta_pasa(self):
        self.assertTrue(firma_valida(SECRETO, self.cuerpo, firmar(self.cuerpo)))

    def test_otro_secreto_no_pasa(self):
        cabeceras = firmar(self.cuerpo, llave=b'otra-llave-cualquiera-de-32-byte')
        self.assertFalse(firma_valida(SECRETO, self.cuerpo, cabeceras))

    def test_un_cuerpo_alterado_no_pasa(self):
        """Firmar un rebote y mandar otro: justo lo que la firma impide."""
        cabeceras = firmar(self.cuerpo)
        otro = aviso('email.bounced', email_id='re_de_otro')
        self.assertFalse(firma_valida(SECRETO, otro, cabeceras))

    def test_un_aviso_viejo_no_pasa_aunque_este_bien_firmado(self):
        """Sin ventana de tiempo, un aviso capturado se podría reenviar siempre."""
        viejo = int(time.time()) - 10 * 60
        self.assertFalse(firma_valida(SECRETO, self.cuerpo,
                                      firmar(self.cuerpo, sello=viejo)))

    def test_durante_una_rotacion_basta_con_que_coincida_una(self):
        cabeceras = firmar(self.cuerpo)
        cabeceras['svix-signature'] = 'v1,firmaVieja= ' + cabeceras['svix-signature']
        self.assertTrue(firma_valida(SECRETO, self.cuerpo, cabeceras))

    def test_sin_cabeceras_no_pasa(self):
        self.assertFalse(firma_valida(SECRETO, self.cuerpo, {}))

    def test_sin_secreto_no_pasa_nada(self):
        self.assertFalse(firma_valida('', self.cuerpo, firmar(self.cuerpo)))


# ── EL WEBHOOK ────────────────────────────────────────────────────────────────

@override_settings(RESEND_WEBHOOK_SECRET=SECRETO)
class ElWebhookTests(BaseConEmpresa):

    def setUp(self):
        self.renglon = NotificationLog.objects.create(
            tenant=self.tenant, channel='EMAIL', event='INVOICE_SENT',
            status='SENT', recipient='pagos@norte.com', subject='Invoice F-0001',
            provider_id='re_correo_1')

    def _llamar(self, cuerpo, cabeceras=None):
        cabeceras = firmar(cuerpo) if cabeceras is None else cabeceras
        extra = {'HTTP_' + k.upper().replace('-', '_'): v for k, v in cabeceras.items()}
        return self.client.post(reverse('resend_webhook'), data=cuerpo,
                                content_type='application/json', **extra)

    def _estado(self):
        self.renglon.refresh_from_db()
        return self.renglon.status

    def test_un_rebote_marca_el_renglon_y_dice_por_que(self):
        cuerpo = aviso('email.bounced', bounce={
            'type': 'Permanent', 'subType': 'General',
            'message': "The recipient's mailbox does not exist"})
        self.assertEqual(self._llamar(cuerpo).status_code, 200)

        self.assertEqual(self._estado(), 'BOUNCED')
        self.assertIn('mailbox does not exist', self.renglon.detail)
        self.assertIn('Permanent', self.renglon.detail)

    def test_una_entrega_la_marca_entregada(self):
        self._llamar(aviso('email.delivered'))
        self.assertEqual(self._estado(), 'DELIVERED')

    def test_marcado_como_spam(self):
        self._llamar(aviso('email.complained'))
        self.assertEqual(self._estado(), 'COMPLAINED')

    def test_una_entrega_atrasada_no_tapa_un_rebote(self):
        """
        Los avisos no llegan necesariamente en orden. Con varios destinatarios
        puede además haber rebote de uno y entrega de otro: lo que importa es
        que alguien no lo recibió.
        """
        self._llamar(aviso('email.bounced', bounce={'message': 'no existe'}))
        self._llamar(aviso('email.delivered'))
        self.assertEqual(self._estado(), 'BOUNCED')

    def test_el_mismo_aviso_dos_veces_no_cambia_nada(self):
        """Resend reintenta lo que no se le confirmó."""
        cuerpo = aviso('email.bounced', bounce={'message': 'no existe'})
        self._llamar(cuerpo)
        detalle = (self._estado(), self.renglon.detail)
        self.assertEqual(self._llamar(cuerpo).status_code, 200)
        self.assertEqual((self._estado(), self.renglon.detail), detalle)

    def test_un_retraso_se_anota_sin_cambiar_el_estado(self):
        self._llamar(aviso('email.delivery_delayed'))
        self.assertEqual(self._estado(), 'SENT')
        self.assertIn('delayed', self.renglon.detail)

    def test_no_toca_un_whatsapp_con_el_mismo_id(self):
        wa = NotificationLog.objects.create(
            tenant=self.tenant, channel='WHATSAPP', event='MANUAL',
            status='SENT', provider_id='re_correo_1')
        self._llamar(aviso('email.bounced', bounce={'message': 'x'}))
        wa.refresh_from_db()
        self.assertEqual(wa.status, 'SENT')

    def test_un_id_que_no_esta_en_la_bitacora_se_contesta_bien(self):
        """
        La recuperación de contraseña y los informes no pasan por la bitácora.
        Contestarles con error solo haría que Resend los reintentara días.
        """
        respuesta = self._llamar(aviso('email.bounced', email_id='re_desconocido'))
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(self._estado(), 'SENT')

    def test_con_firma_invalida_no_toca_nada(self):
        cuerpo = aviso('email.bounced', bounce={'message': 'x'})
        cabeceras = firmar(cuerpo, llave=b'otra-llave-cualquiera-de-32-byte')
        self.assertEqual(self._llamar(cuerpo, cabeceras).status_code, 401)
        self.assertEqual(self._estado(), 'SENT')

    def test_sin_firma_no_toca_nada(self):
        cuerpo = aviso('email.bounced', bounce={'message': 'x'})
        self.assertEqual(self._llamar(cuerpo, cabeceras={}).status_code, 401)
        self.assertEqual(self._estado(), 'SENT')

    @override_settings(RESEND_WEBHOOK_SECRET='')
    def test_sin_secreto_configurado_no_acepta_nada(self):
        """Sin firma que comprobar, cualquiera podría marcar correos como rebotados."""
        respuesta = self._llamar(aviso('email.bounced', bounce={'message': 'x'}))
        self.assertEqual(respuesta.status_code, 503)
        self.assertEqual(self._estado(), 'SENT')

    def test_un_json_roto_se_rechaza(self):
        self.assertEqual(self._llamar(b'{no es json').status_code, 400)

    def test_solo_acepta_post(self):
        self.assertEqual(self.client.get(reverse('resend_webhook')).status_code, 405)
