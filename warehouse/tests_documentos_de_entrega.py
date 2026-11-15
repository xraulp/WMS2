"""
Los documentos de entrega de la tarea de cruce.

Lo que se prueba: que la tarea junta los pedimentos pagados de las ranuras de
sus pedimentos sin pedir subirlos otra vez; que el shipper acepta varios PDF y
las fotos acepta imagenes, y que cada ranura rechaza lo que no es suyo; que el
ZIP y el correo llevan lo marcado -- o un solo archivo con el sobre --; que el
correo va a los correos de la ficha del cliente; que el cliente ve y descarga
pero no sube ni manda; y que el shipper ya no esta entre las ranuras del
pedimento.
"""
import io
import tempfile
import zipfile

from django.contrib.auth.models import User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone

from .models import (CrossingTask, CrossingTaskItem, DocumentoDeEntrega,
                     Pedimento, PedimentoBundle, PedimentoDocument, UserProfile)
from .tests_pedimentos import BaseDeAlmacen

STORAGE_LOCAL = override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage',
                'OPTIONS': {'location': tempfile.mkdtemp()}},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})


def pdf(nombre='x.pdf'):
    return SimpleUploadedFile(nombre, b'%PDF-1.4 x', content_type='application/pdf')


def foto(nombre='carga.jpg'):
    return SimpleUploadedFile(nombre, b'\xff\xd8\xff fake', content_type='image/jpeg')


@STORAGE_LOCAL
class DocumentosDeEntregaTests(BaseDeAlmacen):

    def setUp(self):
        self.cliente.contact_email = 'compras@acme.test'
        self.cliente.save()
        self.client.force_login(self.jefa)
        self.entrada = self.operacion('ED261115-0001', bundle_qty=2)
        self.ped = Pedimento.objects.create(
            tenant=self.tenant, customer=self.cliente, orden=1,
            ped_aduana='24', ped_patente='1780', ped_consecutivo='6004587')
        PedimentoBundle.objects.create(pedimento=self.ped,
                                       operation=self.entrada, bultos=2)
        self.tarea = CrossingTask.objects.create(
            tenant=self.tenant, customer=self.cliente,
            fecha_de_cruce=timezone.localdate(), created_by=self.jefa)
        CrossingTaskItem.objects.create(task=self.tarea, operation=self.entrada,
                                        added_by=self.jefa)
        self.pagado = PedimentoDocument.objects.create(
            pedimento=self.ped, ranura=PedimentoDocument.PEDIMENTO_PAGADO,
            file=pdf('pagado.pdf'), original_name='pagado.pdf')

    def url(self, nombre):
        return f'/cruces/{self.tarea.pk}/documents/{nombre}'

    def subir(self, tipo, *archivos):
        return self.client.post(self.url('upload/'),
                                {'tipo': tipo, 'archivo': list(archivos)})

    def zip_de(self, respuesta):
        return zipfile.ZipFile(io.BytesIO(respuesta.content)).namelist()

    # -- Lo que junta la tarea ------------------------------------------------

    def test_el_pagado_sale_de_la_ranura_del_pedimento(self):
        respuesta = self.client.get(f'/cruces/?customer={self.cliente.pk}')
        html = respuesta.content.decode()
        self.assertIn(f'pag:{self.pagado.pk}', html)
        self.assertIn('6004587', html)

    def test_el_shipper_ya_no_es_ranura_del_pedimento(self):
        self.assertNotIn(PedimentoDocument.SHIPPER,
                         PedimentoDocument.RANURAS_POSTERIORES)
        self.assertIn(PedimentoDocument.PEDIMENTO_PAGADO,
                      PedimentoDocument.RANURAS_POSTERIORES)

    # -- Subir ----------------------------------------------------------------

    def test_el_shipper_acepta_varios_pdf(self):
        self.subir(DocumentoDeEntrega.SHIPPER, pdf('s1.pdf'), pdf('s2.pdf'))
        self.assertEqual(self.tarea.documentos_de_entrega.filter(
            tipo=DocumentoDeEntrega.SHIPPER).count(), 2)

    def test_el_shipper_no_acepta_fotos(self):
        self.subir(DocumentoDeEntrega.SHIPPER, foto())
        self.assertFalse(self.tarea.documentos_de_entrega.exists())

    def test_las_fotos_de_la_carga_y_la_remision_firmada(self):
        self.subir(DocumentoDeEntrega.FOTOS_CARGA, foto('a.jpg'), foto('b.jpg'))
        self.subir(DocumentoDeEntrega.REMISION_FIRMADA, foto('firma.jpg'))
        self.assertEqual(self.tarea.documentos_de_entrega.count(), 3)

    def test_la_remision_firmada_es_solo_foto(self):
        self.subir(DocumentoDeEntrega.REMISION_FIRMADA, pdf('firma.pdf'))
        self.assertFalse(self.tarea.documentos_de_entrega.exists())

    def test_quitar(self):
        self.subir(DocumentoDeEntrega.SHIPPER, pdf('s1.pdf'))
        doc = self.tarea.documentos_de_entrega.get()
        self.client.post(self.url('remove/'), {'documento': doc.pk})
        self.assertFalse(self.tarea.documentos_de_entrega.exists())

    # -- Descargar ------------------------------------------------------------

    def test_el_zip_lleva_todo_lo_marcado(self):
        self.subir(DocumentoDeEntrega.SHIPPER, pdf('s1.pdf'))
        self.subir(DocumentoDeEntrega.FOTOS_CARGA, foto('a.jpg'))
        shipper = self.tarea.documentos_de_entrega.get(tipo='SHIPPER')
        nombres = self.zip_de(self.client.post(self.url('zip/'), {
            'sel': [f'pag:{self.pagado.pk}', f'ent:{shipper.pk}']}))
        self.assertEqual(len(nombres), 2)
        self.assertTrue(all(n.startswith(self.tarea.custom_id) for n in nombres))
        self.assertTrue(any('24-1780-6004587' in n for n in nombres), nombres)

    # -- El correo ------------------------------------------------------------

    def test_el_correo_va_a_la_ficha_con_lo_marcado(self):
        self.subir(DocumentoDeEntrega.SHIPPER, pdf('s1.pdf'))
        self.subir(DocumentoDeEntrega.REMISION_FIRMADA, foto('firma.jpg'))
        mail.outbox = []
        self.client.post(self.url('email/'), {
            'sel': [f'pag:{self.pagado.pk}'] + [
                f'ent:{d.pk}' for d in self.tarea.documentos_de_entrega.all()]})
        self.assertEqual(len(mail.outbox), 1)
        correo = mail.outbox[0]
        self.assertEqual(correo.to, ['compras@acme.test'])
        self.assertEqual(len(correo.attachments), 3)
        self.assertIn(self.tarea.custom_id, correo.subject)

    def test_el_sobre_manda_un_solo_archivo(self):
        self.subir(DocumentoDeEntrega.SHIPPER, pdf('s1.pdf'))
        mail.outbox = []
        self.client.post(self.url('email/'), {
            'sel': [f'pag:{self.pagado.pk}'], 'solo': f'pag:{self.pagado.pk}'})
        self.assertEqual(len(mail.outbox[0].attachments), 1)

    def test_sin_correo_en_la_ficha_no_se_manda(self):
        self.cliente.contact_email = ''
        self.cliente.save()
        mail.outbox = []
        self.client.post(self.url('email/'), {'sel': [f'pag:{self.pagado.pk}']})
        self.assertEqual(mail.outbox, [])

    # -- El cliente -----------------------------------------------------------

    def test_el_cliente_descarga_pero_no_sube_ni_manda(self):
        u = User.objects.create_user('cli_entrega', password='x')
        UserProfile.objects.create(user=u, tenant=self.tenant, role='customer',
                                   customer=self.cliente)
        self.client.force_login(u)
        self.assertEqual(self.client.get(self.url('')).status_code, 200)
        self.assertEqual(len(self.zip_de(self.client.post(self.url('zip/')))), 1)
        self.assertEqual(self.subir('SHIPPER', pdf()).status_code, 404)
        self.assertEqual(self.client.post(self.url('email/')).status_code, 404)

    def test_otro_cliente_no_entra(self):
        from .models import Catalog
        otro = Catalog.objects.create(category='CUSTOMER', name='Beta',
                                      tenant=self.tenant)
        u = User.objects.create_user('cli_ajeno', password='x')
        UserProfile.objects.create(user=u, tenant=self.tenant, role='customer',
                                   customer=otro)
        self.client.force_login(u)
        self.assertEqual(self.client.get(self.url('')).status_code, 404)
