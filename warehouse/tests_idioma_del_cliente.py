"""
En que idioma se le escribe a cada cliente.

Un correo y un PDF no se escriben en el idioma de quien los manda sino en el
del que los lee, asi que el idioma vive en la ficha del cliente. Lo que se
prueba aqui es que el idioma del operador que aprieta el boton no se cuela en
lo que recibe el cliente.
"""
from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone, translation

from . import notifications
from .models import Catalog, Tenant, UserProfile, WarehouseOperation
from .utils import generate_pdf_report


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class IdiomaDelClienteTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')
        cls.usuario = User.objects.create_user('operador', password='x')
        UserProfile.objects.create(user=cls.usuario, tenant=cls.tenant,
                                   role='staff')

        def cliente(nombre, idioma):
            return Catalog.objects.create(
                category='CUSTOMER', name=nombre, tenant=cls.tenant,
                contact_email=nombre.lower() + '@x.com', language=idioma,
                notify_email=True, notify_on_create=True,
                notify_on_release=True, notify_on_documents=True)

        cls.en_espanol = cliente('Espanol', 'es')
        cls.en_ingles = cliente('Ingles', 'en')
        cls.sin_idioma = cliente('Neutro', '')

    def operacion(self, cliente, tipo='ENTRY'):
        return WarehouseOperation.objects.create(
            tenant=self.tenant, operation_type=tipo, date=timezone.now().date(),
            customer=cliente, description='x', bundle_qty=1)

    # ── El correo ────────────────────────────────────────────────────────────

    def test_el_correo_va_en_el_idioma_de_la_ficha(self):
        mail.outbox = []
        notifications.notify_operation_created(self.operacion(self.en_espanol))

        cuerpo = mail.outbox[0].body
        self.assertIn('Apreciado socio comercial', cuerpo)
        self.assertNotIn('Dear business partner', cuerpo)

    def test_otro_cliente_lo_recibe_en_ingles(self):
        mail.outbox = []
        notifications.notify_operation_created(self.operacion(self.en_ingles))

        cuerpo = mail.outbox[0].body
        self.assertIn('Dear business partner', cuerpo)
        self.assertNotIn('Apreciado socio', cuerpo)

    def test_el_idioma_del_operador_no_se_cuela(self):
        """
        Lo que da sentido a todo esto: quien captura trabaja en el idioma que
        quiere, y el cliente recibe el suyo.
        """
        mail.outbox = []
        with translation.override('es'):
            notifications.notify_operation_created(self.operacion(self.en_ingles))

        self.assertIn('Dear business partner', mail.outbox[0].body)

    def test_sin_idioma_en_la_ficha_manda_el_de_la_casa(self):
        """
        El de la casa, no el que tuviera puesto quien apreto el boton:
        `LANGUAGE_CODE` es 'en'.
        """
        mail.outbox = []
        with translation.override('es'):
            notifications.notify_operation_created(self.operacion(self.sin_idioma))

        self.assertIn('Dear business partner', mail.outbox[0].body)

    def test_el_asunto_tambien(self):
        mail.outbox = []
        notifications.notify_operation_created(self.operacion(self.en_espanol))

        self.assertIn('Recepción de mercancías', mail.outbox[0].subject)

    # ── El PDF ───────────────────────────────────────────────────────────────

    def test_el_titulo_del_documento_sigue_al_cliente(self):
        op = self.operacion(self.en_espanol)

        with notifications.en_el_idioma_de(self.en_espanol):
            self.assertEqual(notifications.nombre_del_tipo(op.operation_type),
                             'Recepción de mercancías')
        with notifications.en_el_idioma_de(self.en_ingles):
            self.assertEqual(notifications.nombre_del_tipo(op.operation_type),
                             'Goods Receipt')

    def test_el_pdf_descargado_se_genera_en_el_idioma_del_cliente(self):
        """
        Bajarlo de la pantalla y recibirlo por correo no pueden dar dos
        documentos distintos.
        """
        op = self.operacion(self.en_espanol)
        self.client.force_login(self.usuario)

        with translation.override('en'):
            respuesta = self.client.get('/operations/%d/pdf/' % op.pk)

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta['Content-Type'], 'application/pdf')
        with notifications.en_el_idioma_de(self.en_espanol):
            self.assertTrue(generate_pdf_report(op))

    # ── El WhatsApp ──────────────────────────────────────────────────────────

    def test_el_whatsapp_tambien_lleva_el_idioma_del_cliente(self):
        op = self.operacion(self.en_espanol)

        with notifications.en_el_idioma_de(self.en_espanol):
            cuerpo = notifications._whatsapp_body(op, notifications.EVENT_CREATED)

        self.assertIn('Cliente:', cuerpo)
        self.assertNotIn('Customer:', cuerpo)


class IdiomaEnLaFichaTests(TestCase):
    """El campo se pone al dar de alta el cliente y al editarlo."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')
        cls.jefa = User.objects.create_user('jefa', password='x')
        UserProfile.objects.create(user=cls.jefa, tenant=cls.tenant, role='admin')

    def setUp(self):
        self.client.force_login(self.jefa)

    def test_se_guarda_al_dar_de_alta(self):
        self.client.post('/catalog/create/', {
            'category': 'CUSTOMER', 'name': 'Acme', 'language': 'es'})

        self.assertEqual(Catalog.objects.get(name='Acme').language, 'es')

    def test_un_idioma_inventado_no_entra(self):
        self.client.post('/catalog/create/', {
            'category': 'CUSTOMER', 'name': 'Acme', 'language': 'fr'})

        self.assertEqual(Catalog.objects.get(name='Acme').language, '')

    def test_las_demas_categorias_no_lo_llevan(self):
        """Solo a un cliente se le escribe; a un transportista no."""
        self.client.post('/catalog/create/', {
            'category': 'CARRIER', 'name': 'Transportes', 'language': 'es'})

        self.assertEqual(Catalog.objects.get(name='Transportes').language, '')

    def test_se_puede_cambiar_despues(self):
        cliente = Catalog.objects.create(category='CUSTOMER', name='Acme',
                                         tenant=self.tenant)

        self.client.post('/catalog/%d/edit/' % cliente.pk,
                         {'name': 'Acme', 'language': 'en'})

        cliente.refresh_from_db()
        self.assertEqual(cliente.language, 'en')
