"""
La salida (SD) que nace sola al subir la foto de la mercancia cargada.

Lo que se prueba: que la primera foto de la carga genera una SD con lo que va
en la tarea -- bultos y pesos sumados, pedidos y descripciones unidos, el
transporte de la remision --; que libera las entradas que cruzan completas y
no la de 19 de 20; que le llega al cliente un correo con el PDF de la SD y las
fotos; que las fotos siguientes se suman al expediente sin generar otra SD ni
otro correo; y que las otras ranuras no generan nada.
"""
from decimal import Decimal

from django.core import mail
from django.utils import timezone

from .models import (CrossingTask, CrossingTaskItem, DocumentoDeEntrega,
                     WarehouseOperation)
from .tests_documentos_de_entrega import STORAGE_LOCAL, foto, pdf
from .tests_pedimentos import BaseDeAlmacen


@STORAGE_LOCAL
class LaSalidaNaceConLaFotoDeCargaTests(BaseDeAlmacen):

    def setUp(self):
        self.cliente.contact_email = 'compras@acme.test'
        self.cliente.save()
        self.client.force_login(self.jefa)
        self.a = self.operacion('ED261115-0001', bundle_qty=4, po_order='PO-1',
                                description='Bombas', weight_lbs=Decimal('100'),
                                weight_kgs=Decimal('45.36'),
                                shipper_name_manual='Grundfos')
        self.b = self.operacion('ED261115-0002', bundle_qty=20, po_order='PO-2',
                                description='Valvulas', weight_lbs=Decimal('200'),
                                weight_kgs=Decimal('90.72'),
                                shipper_name_manual='Parker')
        self.tarea = CrossingTask.objects.create(
            tenant=self.tenant, customer=self.cliente,
            fecha_de_cruce=timezone.localdate(), created_by=self.jefa,
            estado=CrossingTask.CARGADA)
        CrossingTaskItem.objects.create(task=self.tarea, operation=self.a)
        # El caso de 19 de 20: uno se queda en bodega.
        CrossingTaskItem.objects.create(task=self.tarea, operation=self.b,
                                        bultos=19)

    def subir(self, tipo, *archivos):
        return self.client.post(f'/cruces/{self.tarea.pk}/documents/upload/',
                                {'tipo': tipo, 'archivo': list(archivos)})

    def salida(self):
        self.tarea.refresh_from_db()
        return self.tarea.salida

    def test_la_primera_foto_genera_la_sd(self):
        self.subir(DocumentoDeEntrega.FOTOS_CARGA, foto('a.jpg'), foto('b.jpg'))
        sd = self.salida()
        self.assertIsNotNone(sd)
        self.assertEqual(sd.operation_type, 'EXIT')
        self.assertTrue(sd.custom_id.startswith('SD'))
        self.assertEqual(sd.customer, self.cliente)
        self.assertEqual(sd.bundle_qty, 4 + 19)
        # El peso del 19 de 20 va en proporcion.
        self.assertEqual(sd.weight_lbs, Decimal('100') + Decimal('190.00'))
        self.assertEqual(sd.po_order, 'PO-1 / PO-2')
        self.assertEqual(sd.description, 'Bombas / Valvulas')
        self.assertEqual(sd.shipper_name_manual, 'VARIOUS')
        self.assertEqual(sd.entry_dispatched, 'ED261115-0001, ED261115-0002')
        # Las fotos quedan en el expediente de la SD.
        self.assertEqual(sd.documents.filter(file_type='PHOTO').count(), 2)

    def test_libera_la_entrada_completa_y_no_la_parcial(self):
        self.subir(DocumentoDeEntrega.FOTOS_CARGA, foto())
        sd = self.salida()
        self.a.refresh_from_db()
        self.b.refresh_from_db()
        self.assertEqual(self.a.entry_dispatched, sd.custom_id)
        self.assertEqual(self.a.status, 'Released Goods')
        self.assertEqual(self.b.status, 'In Warehouse')

    def test_al_cliente_le_llega_el_reporte_con_las_fotos(self):
        self.subir(DocumentoDeEntrega.FOTOS_CARGA, foto('carga.jpg'))
        sd = self.salida()
        self.assertEqual(len(mail.outbox), 1)
        correo = mail.outbox[0]
        self.assertIn('compras@acme.test', correo.to)
        nombres = [a[0] for a in correo.attachments if isinstance(a, tuple)]
        self.assertIn(f'{sd.custom_id}.pdf', nombres)
        self.assertIn('carga.jpg', nombres)

    def test_la_segunda_foto_no_hace_otra_sd_ni_otro_correo(self):
        self.subir(DocumentoDeEntrega.FOTOS_CARGA, foto('a.jpg'))
        sd = self.salida()
        self.subir(DocumentoDeEntrega.FOTOS_CARGA, foto('b.jpg'))
        self.assertEqual(self.salida(), sd)
        self.assertEqual(
            WarehouseOperation.objects.filter(operation_type='EXIT').count(), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(sd.documents.filter(file_type='PHOTO').count(), 2)

    def test_las_otras_ranuras_no_generan_salida(self):
        self.subir(DocumentoDeEntrega.SHIPPER, pdf())
        self.subir(DocumentoDeEntrega.REMISION_FIRMADA, foto())
        self.assertIsNone(self.salida())
        self.assertEqual(len(mail.outbox), 0)

    def test_la_tarea_ensena_la_sd(self):
        self.subir(DocumentoDeEntrega.FOTOS_CARGA, foto())
        sd = self.salida()
        html = self.client.get(f'/cruces/?customer={self.cliente.pk}').content.decode()
        self.assertIn(sd.custom_id, html)
        self.assertIn(f'/operations/{sd.pk}/pdf/', html)
