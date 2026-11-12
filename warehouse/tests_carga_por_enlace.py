"""
El correo de entrada con el logo de la empresa y el boton para subir documentos.

Lo que fijan estas pruebas:

* El aviso de una **entrada** lleva el enlace para subir la factura y los
  documentos; el de una salida no.
* El logo de la empresa va **incrustado** (Content-ID) y la plantilla lo pide
  con `cid:` solo cuando de verdad se adjunta. Sin logo, el correo sale igual.
* El backend de Resend manda el `content_id` de la imagen incrustada.
* La pagina del enlace no pide sesion, pero **solo vale para su entrada**, se
  rechaza si la firma no cuadra y caduca.
* Lo que se sube como factura queda en la ranura de factura comercial, que es
  lo que mira la tarea de cruce.
"""
import tempfile
import time
from datetime import date
from io import BytesIO
from unittest import mock

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from . import notifications
from .carga_por_enlace import enlace_para_subir, token_de
from .email_backends import ResendBackend
from .models import Catalog, OperationDocument, Tenant, WarehouseOperation

STORAGE_LOCAL = override_settings(
    STORAGES={
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage',
                    'OPTIONS': {'location': tempfile.mkdtemp()}},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    },
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    AVISOS_EN_HILO=False,
)


def _png():
    from PIL import Image
    buffer = BytesIO()
    Image.new('RGB', (40, 24), (200, 30, 50)).save(buffer, format='PNG')
    return buffer.getvalue()


def _pdf(nombre='factura.pdf'):
    return SimpleUploadedFile(nombre, b'%PDF-1.4 prueba', content_type='application/pdf')


@STORAGE_LOCAL
class BaseCarga(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.norte = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')
        cls.sur = Tenant.objects.create(
            name='Bodegas del Sur', type='organization', subdomain='sur')
        cls.cliente = Catalog.objects.create(
            tenant=cls.norte, category='CUSTOMER', name='ACME',
            contact_email='compras@acme.test')

    _n = 0

    def _operacion(self, tipo='ENTRY', tenant=None):
        type(self)._n += 1
        return WarehouseOperation.objects.create(
            tenant=tenant or self.norte, operation_type=tipo, date=date.today(),
            custom_id='%s-%04d' % ('ED' if tipo == 'ENTRY' else 'SD', type(self)._n),
            customer=self.cliente, description='Mercancia')

    def _con_logo(self):
        self.norte.logo = SimpleUploadedFile('logo.png', _png(), content_type='image/png')
        self.norte.save()


class CorreoDeEntradaTests(BaseCarga):

    def test_la_entrada_lleva_el_boton_para_subir(self):
        op = self._operacion()
        enviado, _ = notifications.notify_operation_created(op)
        self.assertTrue(enviado)
        cuerpo = mail.outbox[0].body
        self.assertIn(enlace_para_subir(op), cuerpo)
        self.assertNotIn('PLEASE SEND THE INVOICE', cuerpo)

    def test_la_salida_no_lleva_el_boton(self):
        op = self._operacion(tipo='EXIT')
        notifications.notify_operation_created(op)
        self.assertNotIn('/entrada/documentos/', mail.outbox[0].body)

    def test_el_reenvio_manual_tambien_lo_lleva(self):
        op = self._operacion()
        notifications.send_manual_email(op, 'otro@acme.test', 'Asunto')
        self.assertIn(enlace_para_subir(op), mail.outbox[0].body)

    def test_el_logo_va_incrustado_a_la_derecha_del_titulo(self):
        self._con_logo()
        op = self._operacion()
        notifications.notify_operation_created(op)
        correo = mail.outbox[0]
        self.assertIn('cid:logo-empresa', correo.body)
        incrustados = [a for a in correo.attachments
                       if not isinstance(a, tuple)
                       and a.get('Content-ID') == '<logo-empresa>']
        self.assertEqual(len(incrustados), 1)
        self.assertEqual(incrustados[0].get_payload(decode=True), _png())

    def test_sin_logo_no_pide_un_cid_que_no_existe(self):
        op = self._operacion()
        notifications.notify_operation_created(op)
        self.assertNotIn('cid:', mail.outbox[0].body)

    def test_resend_recibe_el_content_id(self):
        self._con_logo()
        op = self._operacion()
        notifications.notify_operation_created(op)
        payload = ResendBackend(api_key='x')._build_payload(mail.outbox[0])
        con_cid = [a for a in payload['attachments'] if a.get('content_id')]
        self.assertEqual([a['content_id'] for a in con_cid], ['logo-empresa'])


class PaginaDelEnlaceTests(BaseCarga):

    def _url(self, op):
        return '/entrada/documentos/%s/' % token_de(op)

    def test_abre_sin_sesion(self):
        op = self._operacion()
        r = self.client.get(self._url(op))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, op.custom_id)

    def test_firma_alterada_no_sirve(self):
        op = self._operacion()
        r = self.client.get(self._url(op)[:-3] + 'xx/')
        self.assertEqual(r.status_code, 404)

    def test_caduca(self):
        op = self._operacion()
        url = self._url(op)
        dentro_de_un_anio = time.time() + 365 * 24 * 3600
        with mock.patch('django.core.signing.time.time', return_value=dentro_de_un_anio):
            r = self.client.get(url)
        self.assertEqual(r.status_code, 410)

    def test_la_factura_queda_en_su_ranura(self):
        op = self._operacion()
        r = self.client.post(self._url(op), {
            'factura': _pdf(),
            'documentos': [_pdf('bol.pdf'), _pdf('packing.pdf')],
        })
        self.assertEqual(r.status_code, 302)
        self.assertEqual(op.documents.count(), 3)
        factura = op.documents.get(ranura=OperationDocument.RANURA_FACTURA_COMERCIAL)
        self.assertEqual(factura.original_name, 'factura.pdf')
        self.assertEqual(factura.tenant, self.norte)
        self.assertTrue(factura.digital_name)

    def test_una_factura_nueva_sustituye_la_marca(self):
        op = self._operacion()
        self.client.post(self._url(op), {'factura': _pdf('vieja.pdf')})
        self.client.post(self._url(op), {'factura': _pdf('nueva.pdf')})
        marcadas = op.documents.filter(ranura=OperationDocument.RANURA_FACTURA_COMERCIAL)
        self.assertEqual([d.original_name for d in marcadas], ['nueva.pdf'])
        self.assertEqual(op.documents.count(), 2)

    def test_otros_documentos_no_se_marcan_como_factura(self):
        op = self._operacion()
        self.client.post(self._url(op), {'documentos': [_pdf('bol.pdf')]})
        self.assertFalse(op.documents.filter(
            ranura=OperationDocument.RANURA_FACTURA_COMERCIAL).exists())

    def test_rechaza_lo_que_no_es_documento(self):
        op = self._operacion()
        r = self.client.post(self._url(op), {
            'documentos': [SimpleUploadedFile('virus.exe', b'MZ')]})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(op.documents.count(), 0)

    def test_sin_archivos_avisa(self):
        op = self._operacion()
        r = self.client.post(self._url(op), {})
        self.assertEqual(r.status_code, 400)

    def test_el_token_no_se_puede_llevar_a_otra_empresa(self):
        """Mismo pk, otra empresa: el token firma las dos cosas."""
        op = self._operacion()
        WarehouseOperation.objects.filter(pk=op.pk).update(tenant=self.sur)
        r = self.client.get(self._url(op))
        self.assertEqual(r.status_code, 404)
