"""
El PDF de la factura y su envío al cliente.

Una factura que solo existe dentro del sistema no cobra nada: hay que poder
mandársela a la empresa y volver a descargarla cuando pregunte. Estas pruebas
fijan lo que hace falta para que eso sea fiable:

* **El PDF dice el estado de hoy.** El mismo documento se adjunta al emitir y se
  vuelve a descargar meses después; lo peor que puede hacer es seguir diciendo
  «pendiente» de algo que ya se cobró.
* **Emitida y enviada no son lo mismo.** Una factura puede llevar semanas en el
  sistema sin haber salido nunca, y eso hay que verlo antes de reclamar un pago.
* **Cada envío queda en la bitácora**, incluidos los que no salieron. Es la
  misma pantalla donde se mira si a un cliente le llegan los correos, porque la
  pregunta es la misma.
* **Quien no puede facturar tampoco manda facturas**, aunque sí pueda abrir el
  PDF: el soporte atiende al cliente que dice no haber recibido nada.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import Invoice, NotificationLog, PlatformUser, Tenant, UserProfile
from .utils import generar_pdf_factura, sello_de_estado

CORREO_EN_MEMORIA = override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')


@CORREO_EN_MEMORIA
class BaseFacturaPDF(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte',
            plan='pro', billing_email='pagos@norte.example')
        cls.sin_correo = Tenant.objects.create(
            name='Bodegas del Sur', type='organization', subdomain='sur',
            plan='starter', billing_email='')

        cls.admin_plataforma = User.objects.create_user('plataforma', password='x')
        PlatformUser.objects.create(user=cls.admin_plataforma, role='admin')

        cls.soporte = User.objects.create_user('soporte', password='x')
        PlatformUser.objects.create(user=cls.soporte, role='staff')

        cls.admin_empresa = User.objects.create_user('admin_norte', password='x')
        UserProfile.objects.create(user=cls.admin_empresa, tenant=cls.empresa,
                                   role='admin')

    def _factura(self, empresa=None, monto='250.00', vence=None):
        hoy = timezone.localdate()
        empresa = empresa or self.empresa
        return Invoice.objects.create(
            tenant=empresa, numero=Invoice.siguiente_numero(),
            periodo_inicio=hoy.replace(day=1), periodo_fin=hoy.replace(day=28),
            emitida_el=hoy, vence_el=vence or (hoy + timedelta(days=15)),
            plan=empresa.plan, monto_usd=Decimal(monto))

    def _enviar(self, factura):
        return self.client.post('/platform/invoices/',
                                {'action': 'enviar', 'invoice_id': factura.pk})


class ElPdfSeGenera(BaseFacturaPDF):

    def test_es_un_pdf_de_verdad(self):
        pdf = generar_pdf_factura(self._factura())

        self.assertTrue(pdf.startswith(b'%PDF'))
        self.assertGreater(len(pdf), 1000)

    def test_no_revienta_sin_notas_ni_datos_del_emisor(self):
        """Sin configurar nada, el PDF tiene que salir igual."""
        pdf = generar_pdf_factura(self._factura())

        self.assertTrue(pdf.startswith(b'%PDF'))

    @override_settings(PLATFORM_BILLING_NAME='RDL Systems LLC',
                       PLATFORM_BILLING_EMAIL='billing@rdl.example',
                       PLATFORM_BILLING_ADDRESS='1234 Example St')
    def test_con_emisor_configurado_tampoco(self):
        pdf = generar_pdf_factura(self._factura())

        self.assertTrue(pdf.startswith(b'%PDF'))

    def test_sale_para_los_tres_estados(self):
        """El sello de estado se pinta distinto en cada uno; los tres deben salir."""
        pendiente = self._factura()
        pagada = self._factura()
        pagada.marcar_pagada(referencia='spei 1')
        cancelada = self._factura()
        cancelada.cancelar('duplicada')
        vencida = self._factura(vence=timezone.localdate() - timedelta(days=2))

        for factura in (pendiente, pagada, cancelada, vencida):
            with self.subTest(estado=factura.estado):
                self.assertTrue(generar_pdf_factura(factura).startswith(b'%PDF'))

    def test_la_descarga_lo_entrega(self):
        factura = self._factura()
        self.client.force_login(self.admin_plataforma)

        respuesta = self.client.get('/platform/invoices/%s/pdf/' % factura.pk)

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta['Content-Type'], 'application/pdf')
        self.assertIn(factura.numero, respuesta['Content-Disposition'])

    def test_el_soporte_puede_descargarlo(self):
        """Atiende al cliente que dice no haber recibido nada; tiene que verlo."""
        factura = self._factura()
        self.client.force_login(self.soporte)

        self.assertEqual(
            self.client.get('/platform/invoices/%s/pdf/' % factura.pk).status_code, 200)

    def test_el_administrador_de_una_empresa_no_lo_descarga(self):
        factura = self._factura()
        self.client.force_login(self.admin_empresa)

        self.assertEqual(
            self.client.get('/platform/invoices/%s/pdf/' % factura.pk).status_code, 403)


class ElEnvioAlCliente(BaseFacturaPDF):

    def setUp(self):
        self.client.force_login(self.admin_plataforma)

    def test_la_factura_sale_al_correo_de_facturacion(self):
        factura = self._factura()

        self._enviar(factura)

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['pagos@norte.example'])
        self.assertIn(factura.numero, mail.outbox[0].subject)

    def test_lleva_el_pdf_adjunto(self):
        """El cuerpo es un resumen; el documento es el adjunto."""
        factura = self._factura()

        self._enviar(factura)

        adjuntos = mail.outbox[0].attachments
        self.assertEqual(len(adjuntos), 1)
        nombre, contenido, tipo = adjuntos[0]
        self.assertEqual(nombre, '%s.pdf' % factura.numero)
        self.assertEqual(tipo, 'application/pdf')
        self.assertTrue(contenido.startswith(b'%PDF'))

    def test_queda_marcada_como_enviada(self):
        factura = self._factura()
        self.assertIsNone(factura.enviada_el)

        self._enviar(factura)

        factura.refresh_from_db()
        self.assertIsNotNone(factura.enviada_el)

    def test_sin_correo_de_facturacion_no_se_manda_y_lo_dice(self):
        """El mensaje tiene que decir qué falta, no «error»."""
        factura = self._factura(empresa=self.sin_correo)

        respuesta = self._enviar(factura)

        self.assertEqual(len(mail.outbox), 0)
        self.assertContains(respuesta, 'has no billing email')
        factura.refresh_from_db()
        self.assertIsNone(factura.enviada_el)

    def test_una_cancelada_no_se_manda(self):
        factura = self._factura()
        factura.cancelar('duplicada')

        respuesta = self._enviar(factura)

        self.assertEqual(len(mail.outbox), 0)
        self.assertContains(respuesta, 'cancelada')

    def test_una_pagada_si_se_puede_reenviar(self):
        """Sirve de comprobante, y el PDF ya dice que está pagada."""
        factura = self._factura()
        factura.marcar_pagada()

        self._enviar(factura)

        self.assertEqual(len(mail.outbox), 1)

    def test_el_soporte_no_manda_facturas(self):
        factura = self._factura()
        self.client.force_login(self.soporte)

        respuesta = self._enviar(factura)

        self.assertEqual(respuesta.status_code, 403)
        self.assertEqual(len(mail.outbox), 0)


class QuedaEnLaBitacoraDeEnvios(BaseFacturaPDF):

    def setUp(self):
        self.client.force_login(self.admin_plataforma)

    def test_un_envio_deja_su_renglon(self):
        factura = self._factura()

        self._enviar(factura)

        registro = NotificationLog.objects.get()
        self.assertEqual(registro.event, 'INVOICE_SENT')
        self.assertEqual(registro.status, 'SENT')
        self.assertEqual(registro.recipient, 'pagos@norte.example')
        self.assertEqual(registro.triggered_by, self.admin_plataforma)

    def test_el_renglon_lleva_la_empresa(self):
        """Sin ella no se podría filtrar, que es para lo que se mira esa pantalla."""
        factura = self._factura()

        self._enviar(factura)

        self.assertEqual(NotificationLog.objects.get().tenant, self.empresa)

    def test_el_que_no_salio_tambien_queda(self):
        """Un envío que no ocurre es justo lo que hay que poder ver después."""
        factura = self._factura(empresa=self.sin_correo)

        self._enviar(factura)

        registro = NotificationLog.objects.get()
        self.assertEqual(registro.status, 'SKIPPED')
        self.assertEqual(registro.detail, 'no_billing_email')
        self.assertEqual(registro.tenant, self.sin_correo)

    def test_un_fallo_del_correo_queda_registrado_y_no_revienta(self):
        from unittest import mock
        factura = self._factura()

        with mock.patch('django.core.mail.EmailMessage.send',
                        side_effect=RuntimeError('smtp caido')):
            respuesta = self._enviar(factura)

        registro = NotificationLog.objects.get()
        self.assertEqual(registro.status, 'FAILED')
        self.assertIn('smtp caido', registro.detail)
        self.assertContains(respuesta, 'Could not send')
        factura.refresh_from_db()
        self.assertIsNone(factura.enviada_el)

    def test_la_bitacora_de_plataforma_lo_muestra(self):
        """Es la pantalla donde se responde «¿le llegó?», sea de quien sea el envío."""
        self._enviar(self._factura())

        respuesta = self.client.get('/platform/notifications/')

        self.assertContains(respuesta, 'pagos@norte.example')

class LoQueDiceElPdfSobreElEstado(BaseFacturaPDF):
    """
    El sello impreso, comprobado sin abrir el PDF.

    Una prueba que solo mire que el archivo empieza por %PDF no distingue un
    documento correcto de uno que le dice «pendiente» al cliente de algo que ya
    pagó. Esto es lo que de verdad lee quien recibe la factura.
    """

    def _texto(self, factura):
        return sello_de_estado(factura)[0]

    def test_una_pendiente_dice_cuando_vence(self):
        factura = self._factura()

        texto = self._texto(factura)

        self.assertIn('PENDING', texto)
        self.assertIn(factura.vence_el.strftime('%Y-%m-%d'), texto)

    def test_una_pagada_lo_dice_con_su_fecha_y_referencia(self):
        """El mismo PDF se reenvía como comprobante: tiene que decir que se pagó."""
        factura = self._factura()
        factura.marcar_pagada(referencia='spei 4471')

        texto = self._texto(factura)

        self.assertIn('PAID', texto)
        self.assertIn(factura.pagada_el.strftime('%Y-%m-%d'), texto)
        self.assertIn('spei 4471', texto)

    def test_una_pagada_sin_referencia_no_arrastra_un_separador_suelto(self):
        """Comprobado con el texto exacto: mirar solo el final deja pasar un
        separador seguido de espacio, que es lo que se imprimiria."""
        factura = self._factura()
        factura.marcar_pagada()

        self.assertEqual(self._texto(factura),
                         'PAID · %s' % factura.pagada_el.strftime('%Y-%m-%d'))

    def test_una_vencida_dice_cuantos_dias_lleva(self):
        factura = self._factura(vence=timezone.localdate() - timedelta(days=7))

        texto = self._texto(factura)

        self.assertIn('OVERDUE', texto)
        self.assertIn('7 day', texto)

    def test_una_cancelada_lo_dice_y_nada_mas(self):
        factura = self._factura()
        factura.cancelar('duplicada')

        self.assertEqual(self._texto(factura), 'CANCELLED')

    def test_el_sello_es_el_de_hoy_y_no_el_de_la_emision(self):
        """Se emitió pendiente y hoy está pagada: el PDF de hoy dice pagada."""
        factura = self._factura()
        self.assertIn('PENDING', self._texto(factura))

        factura.marcar_pagada()

        self.assertIn('PAID', self._texto(factura))

    def test_una_pagada_fuera_de_plazo_no_dice_vencida(self):
        factura = self._factura(vence=timezone.localdate() - timedelta(days=30))
        factura.marcar_pagada()

        self.assertIn('PAID', self._texto(factura))
        self.assertNotIn('OVERDUE', self._texto(factura))
