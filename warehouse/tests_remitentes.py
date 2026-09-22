"""
De quien sale cada correo, y a donde contesta quien lo recibe.

Hasta ahora todo salia de la misma direccion: la factura que la plataforma le
cobra a una empresa, el enlace de recuperacion y el aviso que esa empresa le
manda a su cliente llegaban con el mismo remitente y sin Reply-To. El cliente
de un almacen recibia un correo de un dominio del que nunca ha oido hablar, y
si contestaba, su respuesta caia en un buzon que no lee nadie.

Son tres direcciones del mismo dominio verificado, no tres dominios. Mandar
desde el dominio de cada empresa exigiria verificar su DNS una por una, y en el
correo de recuperacion seria ademas peligroso: lleva un enlace a esta
plataforma, y un dominio ajeno avalando un enlace que no controla es la forma
exacta de un fraude. Lo que si viaja por empresa es el nombre visible y el
Reply-To, que son cabeceras y no necesitan verificar nada.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from . import notifications
from .models import (Catalog, Invoice, Tenant, UserProfile, WarehouseOperation)


REMITENTES = dict(
    DEFAULT_FROM_EMAIL='no-reply@plataforma.com',
    BILLING_FROM_EMAIL='billing@plataforma.com',
    NOTIFICATIONS_FROM_EMAIL='reportes@plataforma.com',
    PLATFORM_BILLING_EMAIL='cobranza@plataforma.com',
)


class BaseDeRemitentes(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte',
            billing_email='pagos@norte.com',
            reply_to_email='operaciones@norte.com')
        cls.cliente = Catalog.objects.create(
            tenant=cls.tenant, category='CUSTOMER', name='Ferreteria del Valle',
            contact_email='compras@ferreteria.com')
        cls.staff = User.objects.create_user('operador', password='x')
        UserProfile.objects.create(user=cls.staff, tenant=cls.tenant, role='admin')

    def _avisar(self):
        op = WarehouseOperation.objects.create(
            tenant=self.tenant, operation_type='ENTRY', custom_id='ED-0001',
            customer=self.cliente, description='Mercancia de prueba')
        notifications.notify_operation_created(op, triggered_by=self.staff)
        return mail.outbox[-1]


@override_settings(**REMITENTES)
class ElAvisoAlClienteTests(BaseDeRemitentes):

    def test_sale_de_la_direccion_de_avisos_y_no_de_la_de_facturacion(self):
        self.assertIn('reportes@plataforma.com', self._avisar().from_email)

    def test_lo_firma_el_nombre_de_la_empresa(self):
        """
        Lo que el cliente lee en la bandeja es el nombre de su proveedor. La
        direccion sigue siendo la de la plataforma -- es el unico dominio
        verificado -- pero eso no tiene por que leerlo el destinatario.
        """
        self.assertEqual(self._avisar().from_email,
                         'Almacenes del Norte <reportes@plataforma.com>')

    def test_se_contesta_a_la_empresa(self):
        self.assertEqual(self._avisar().reply_to, ['operaciones@norte.com'])

    def test_sin_correo_de_respuesta_configurado_no_lleva_ninguno(self):
        """
        Vacio no se rellena con otra cosa. Poner ahi el de facturacion mandaria
        a contabilidad las preguntas sobre un embarque.
        """
        self.tenant.reply_to_email = None
        self.tenant.save(update_fields=['reply_to_email'])

        self.assertEqual(self._avisar().reply_to, [])


@override_settings(**REMITENTES)
class LaFacturaDeLaPlataformaTests(BaseDeRemitentes):

    def _facturar(self):
        hoy = timezone.localdate()
        factura = Invoice.objects.create(
            tenant=self.tenant, numero='F-0001',
            periodo_inicio=hoy - timedelta(days=30), periodo_fin=hoy,
            emitida_el=hoy, vence_el=hoy + timedelta(days=15),
            plan=self.tenant.plan, monto_usd=Decimal('100.00'))
        notifications.enviar_factura(factura)
        return mail.outbox[-1]

    def test_sale_de_la_direccion_de_facturacion(self):
        self.assertEqual(self._facturar().from_email, 'billing@plataforma.com')

    def test_no_lleva_el_nombre_ni_la_respuesta_de_la_empresa(self):
        """
        Este no lo manda la empresa: lo manda quien le cobra. Firmarlo con su
        propio nombre seria mandarle una factura que parece suya.
        """
        correo = self._facturar()

        self.assertNotIn('Almacenes del Norte', correo.from_email)
        self.assertNotIn('operaciones@norte.com', correo.reply_to)

    def test_se_contesta_a_cobranza(self):
        self.assertEqual(self._facturar().reply_to, ['cobranza@plataforma.com'])


@override_settings(**REMITENTES)
class LaRecuperacionDeContrasenaTests(BaseDeRemitentes):

    def test_sale_de_la_direccion_neutra_y_sin_nombre_de_empresa(self):
        """
        Nunca del dominio ni del nombre de una empresa. El correo lleva un
        enlace a esta plataforma; hacerlo pasar por otra casa es la forma de un
        fraude, y ademas el destinatario puede pertenecer a varias.
        """
        User.objects.create_user('ana', password='x', email='ana@norte.com')

        self.client.post('/password/reset/', {'email': 'ana@norte.com'})

        self.assertEqual(mail.outbox[-1].from_email, 'no-reply@plataforma.com')


class SinConfigurarNadaTests(BaseDeRemitentes):
    """
    Una instalacion que no separe los remitentes sigue funcionando igual que
    antes: las tres variables caen en `DEFAULT_FROM_EMAIL`.
    """

    @override_settings(DEFAULT_FROM_EMAIL='correo@plataforma.com',
                       BILLING_FROM_EMAIL=None, NOTIFICATIONS_FROM_EMAIL=None)
    def test_el_aviso_cae_en_el_remitente_de_siempre(self):
        self.assertIn('correo@plataforma.com', self._avisar().from_email)

    @override_settings(DEFAULT_FROM_EMAIL='', NOTIFICATIONS_FROM_EMAIL='')
    def test_sin_ninguna_direccion_no_se_inventa_una_cabecera_a_medias(self):
        """
        Sin remitente configurado, Django pone el suyo. Anteponerle el nombre
        de la empresa daria una cabecera como "Empresa <>", que no es una
        direccion.
        """
        de, _responder = notifications.remitente_de(self.tenant)

        self.assertFalse(de)
