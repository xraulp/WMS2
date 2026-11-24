"""
Cada empresa con su dominio y la plataforma con el suyo.

Los avisos y reportes de una empresa salen de su remitente propio
(`avisos@norte.com`) solo cuando Resend tiene su dominio verificado; mientras
no, salen del de la plataforma con su nombre a la vista, como siempre. La
recuperacion de contrasena y la factura del SaaS nunca usan el de la empresa.
"""
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from . import dominios_de_correo, notifications
from .models import Invoice, Tenant, UserProfile
from .tests_remitentes import REMITENTES, BaseDeRemitentes

POR_RESEND = dict(REMITENTES, EMAIL_PROVIDER='resend', RESEND_API_KEY='re_prueba',
                  EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')


def respuesta_de_resend(status=200, dominios=None):
    r = mock.Mock(status_code=status)
    r.json.return_value = {'data': dominios or []}
    return r


def con_dominios(*pares, status=200):
    """Simula `GET /domains` con los dominios `(nombre, estado)` dados."""
    datos = [{'name': n, 'status': e} for n, e in pares]
    return mock.patch('requests.get', return_value=respuesta_de_resend(status, datos))


@override_settings(**POR_RESEND)
class ElRemitenteDeLaEmpresaTests(BaseDeRemitentes):

    def _con_remitente(self, verificado):
        self.tenant.sender_email = 'avisos@norte.com'
        self.tenant.sender_domain_verified = verificado
        self.tenant.save()

    def test_con_dominio_verificado_sale_de_su_dominio(self):
        self._con_remitente(True)
        self.assertEqual(self._avisar().from_email,
                         'Almacenes del Norte <avisos@norte.com>')

    def test_sin_verificar_sale_del_dominio_de_la_plataforma(self):
        self._con_remitente(False)
        self.assertEqual(self._avisar().from_email,
                         'Almacenes del Norte <reportes@plataforma.com>')

    def test_conserva_su_reply_to(self):
        self._con_remitente(True)
        self.assertEqual(self._avisar().reply_to, ['operaciones@norte.com'])

    def test_una_sucursal_usa_el_de_su_corporativo(self):
        self._con_remitente(True)
        sucursal = Tenant.objects.create(name='Norte Laredo', type='branch',
                                         subdomain='norte-laredo', parent=self.tenant)
        de, _ = notifications.remitente_de(sucursal)
        self.assertEqual(de, 'Norte Laredo <avisos@norte.com>')

    @override_settings(EMAIL_PROVIDER='smtp')
    def test_por_smtp_nunca_usa_el_dominio_de_la_empresa(self):
        """Por SMTP seria suplantarla desde el servidor de la plataforma."""
        self._con_remitente(True)
        self.assertIn('reportes@plataforma.com', self._avisar().from_email)

    def test_la_recuperacion_de_contrasena_sigue_saliendo_de_la_plataforma(self):
        self._con_remitente(True)
        User.objects.create_user('ana', password='x', email='ana@norte.com')
        self.client.post('/password/reset/', {'email': 'ana@norte.com'})
        self.assertEqual(mail.outbox[-1].from_email, 'no-reply@plataforma.com')

    def test_la_factura_del_saas_sigue_saliendo_de_la_plataforma(self):
        self._con_remitente(True)
        hoy = timezone.localdate()
        factura = Invoice.objects.create(
            tenant=self.tenant, numero='F-0001',
            periodo_inicio=hoy - timedelta(days=30), periodo_fin=hoy,
            emitida_el=hoy, vence_el=hoy + timedelta(days=15),
            plan=self.tenant.plan, monto_usd=Decimal('100.00'))
        notifications.enviar_factura(factura)
        self.assertEqual(mail.outbox[-1].from_email, 'billing@plataforma.com')


@override_settings(**POR_RESEND)
class RevisarElDominioTests(BaseDeRemitentes):

    def setUp(self):
        self.tenant.sender_email = 'avisos@Norte.com'
        self.tenant.save()

    def test_verificado_en_resend_queda_verificado(self):
        with con_dominios(('otro.com', 'verified'), ('norte.com', 'verified')):
            estado = dominios_de_correo.revisar_remitente(self.tenant)
        self.tenant.refresh_from_db()
        self.assertEqual(estado, 'verified')
        self.assertTrue(self.tenant.sender_domain_verified)
        self.assertIsNotNone(self.tenant.sender_domain_checked_at)

    def test_pendiente_no_queda_verificado(self):
        with con_dominios(('norte.com', 'pending')):
            dominios_de_correo.revisar_remitente(self.tenant)
        self.tenant.refresh_from_db()
        self.assertFalse(self.tenant.sender_domain_verified)
        self.assertEqual(self.tenant.sender_domain_status, 'pending')

    def test_no_dado_de_alta(self):
        with con_dominios(('otro.com', 'verified')):
            estado = dominios_de_correo.revisar_remitente(self.tenant)
        self.assertEqual(estado, dominios_de_correo.NO_DADO_DE_ALTA)
        self.assertFalse(self.tenant.sender_domain_verified)

    def test_una_llave_sin_permiso_de_leer_dominios_no_verifica(self):
        with con_dominios(status=401):
            estado = dominios_de_correo.revisar_remitente(self.tenant)
        self.assertEqual(estado, dominios_de_correo.SIN_CONSULTAR)
        self.assertFalse(self.tenant.sender_domain_verified)

    def test_si_resend_no_contesta_no_verifica(self):
        import requests
        with mock.patch('requests.get', side_effect=requests.ConnectionError('x')):
            estado = dominios_de_correo.revisar_remitente(self.tenant)
        self.assertEqual(estado, dominios_de_correo.SIN_CONSULTAR)
        self.assertFalse(self.tenant.sender_domain_verified)


@override_settings(**POR_RESEND)
class LaPantallaDeEmpresasTests(BaseDeRemitentes):

    def setUp(self):
        self.admin = User.objects.create_superuser('plataforma', password='x')
        self.client.force_login(self.admin)

    def _editar(self, remitente):
        return self.client.post('/platform/tenants/', {
            'action': 'update', 'tenant_id': self.tenant.pk,
            'name': self.tenant.name, 'subdomain': self.tenant.subdomain,
            'plan': self.tenant.plan, 'billing_email': 'pagos@norte.com',
            'reply_to_email': 'operaciones@norte.com', 'sender_email': remitente})

    def test_guardar_un_remitente_lo_revisa_en_resend(self):
        with con_dominios(('norte.com', 'verified')):
            r = self._editar('avisos@norte.com')
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.sender_email, 'avisos@norte.com')
        self.assertTrue(self.tenant.sender_domain_verified)
        self.assertContains(r, 'norte.com')

    def test_cambiar_el_remitente_pierde_la_verificacion_anterior(self):
        self.tenant.sender_email = 'avisos@norte.com'
        self.tenant.sender_domain_verified = True
        self.tenant.save()
        with con_dominios(('norte.com', 'verified')):
            self._editar('avisos@otro-dominio.com')
        self.tenant.refresh_from_db()
        self.assertFalse(self.tenant.sender_domain_verified)

    def test_vaciarlo_vuelve_al_dominio_de_la_plataforma(self):
        self.tenant.sender_email = 'avisos@norte.com'
        self.tenant.sender_domain_verified = True
        self.tenant.save()
        self._editar('')
        self.tenant.refresh_from_db()
        self.assertIsNone(self.tenant.sender_email)
        self.assertFalse(self.tenant.sender_domain_verified)

    def test_el_boton_verificar_vuelve_a_preguntar(self):
        self.tenant.sender_email = 'avisos@norte.com'
        self.tenant.save()
        with con_dominios(('norte.com', 'verified')):
            self.client.post('/platform/tenants/', {
                'action': 'check_sender', 'tenant_id': self.tenant.pk})
        self.tenant.refresh_from_db()
        self.assertTrue(self.tenant.sender_domain_verified)

    def test_solo_el_administrador_de_plataforma(self):
        self.client.force_login(self.staff)
        self.tenant.sender_email = 'avisos@norte.com'
        self.tenant.save()
        with con_dominios(('norte.com', 'verified')):
            self.client.post('/platform/tenants/', {
                'action': 'check_sender', 'tenant_id': self.tenant.pk})
        self.tenant.refresh_from_db()
        self.assertFalse(self.tenant.sender_domain_verified)
