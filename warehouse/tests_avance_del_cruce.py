"""
El avance de la tarea de cruce: una sola linea de pasos en vez de fase, status
y cuenta atras.

Lo que se prueba: que cada paso lo prende su evidencia -- pedimentos aprobados,
pagados, la foto de la carga, la marca de la aduana, la remision firmada --;
que un paso hecho da por hechos los de antes; que se dice a quien le toca, con
el nombre que corresponde a quien mira; que la remision firmada entrega la
tarea sola, aunque nadie haya marcado el verde; que la inspeccion solo sale si
aplica; y que la migracion entrega las tareas que ya tenian la remision.
"""
import importlib

from django.apps import apps
from django.contrib.auth.models import User
from django.utils import timezone

from .models import (CrossingTask, CrossingTaskItem, DocumentoDeEntrega,
                     Pedimento, PedimentoBundle, UserProfile)
from .tests_documentos_de_entrega import STORAGE_LOCAL, foto
from .tests_pedimentos import BaseDeAlmacen


@STORAGE_LOCAL
class ElAvanceDeLaTareaTests(BaseDeAlmacen):

    def setUp(self):
        self.cliente.abbreviation = 'ACM'
        self.cliente.save()
        self.client.force_login(self.jefa)
        self.entrada = self.operacion('ED261115-0001', bundle_qty=2)
        self.tarea = CrossingTask.objects.create(
            tenant=self.tenant, customer=self.cliente,
            fecha_de_cruce=timezone.localdate(), created_by=self.jefa)
        CrossingTaskItem.objects.create(task=self.tarea, operation=self.entrada)

    def con_pedimento(self, estado):
        ped = Pedimento.objects.create(
            tenant=self.tenant, customer=self.cliente, orden=1, estado=estado,
            ped_aduana='24', ped_patente='1780', ped_consecutivo='6004587')
        PedimentoBundle.objects.create(pedimento=ped, operation=self.entrada,
                                       bultos=2)
        return ped

    def fresca(self):
        return CrossingTask.objects.get(pk=self.tarea.pk)

    def actual(self):
        p = self.fresca().paso_actual
        return p['clave'] if p else None

    def subir(self, tipo, *archivos):
        return self.client.post(f'/cruces/{self.tarea.pk}/documents/upload/',
                                {'tipo': tipo, 'archivo': list(archivos)})

    def test_sin_pedimento_va_en_pedimentos_y_nos_toca(self):
        t = self.fresca()
        self.assertEqual(self.actual(), CrossingTask.PASO_PEDIMENTOS)
        self.assertFalse(t.le_toca_al_cliente)

    def test_en_revision_le_toca_al_cliente(self):
        self.con_pedimento(Pedimento.EN_REVISION)
        t = self.fresca()
        self.assertEqual(self.actual(), CrossingTask.PASO_PEDIMENTOS)
        self.assertTrue(t.le_toca_al_cliente)
        self.assertEqual(str(t.lo_que_sigue), 'review and approve the pedimentos')

    def test_pagado_espera_la_carga(self):
        self.con_pedimento(Pedimento.PAGADO)
        self.assertEqual(self.actual(), CrossingTask.PASO_SALIO)

    def test_todo_el_camino_hasta_entregada(self):
        self.con_pedimento(Pedimento.PAGADO)
        self.subir(DocumentoDeEntrega.FOTOS_CARGA, foto())
        self.assertEqual(self.actual(), CrossingTask.PASO_ADUANA)
        salio = self.fresca().avance[2]
        self.assertTrue(salio['hecho'])
        self.assertTrue(salio['detalle'].startswith('SD'))
        CrossingTask.objects.filter(pk=self.tarea.pk).update(
            estado=CrossingTask.CARGADA)
        self.client.post(f'/cruces/{self.tarea.pk}/green-light/')
        self.assertEqual(self.actual(), CrossingTask.PASO_ENTREGADO)
        self.subir(DocumentoDeEntrega.REMISION_FIRMADA, foto('firmada.jpg'))
        t = self.fresca()
        self.assertIsNone(t.paso_actual)
        self.assertEqual(t.estado, CrossingTask.CRUZADA)
        self.assertEqual(t.entregada_por, self.jefa)

    def test_la_remision_entrega_aunque_no_haya_verde(self):
        self.subir(DocumentoDeEntrega.REMISION_FIRMADA, foto('firmada.jpg'))
        t = self.fresca()
        self.assertIsNotNone(t.entregada_en)
        # Lo de despues manda: todos los pasos quedan hechos.
        self.assertTrue(all(p['hecho'] for p in t.avance))

    def test_el_rojo_es_alerta_hasta_que_lo_liberan(self):
        CrossingTask.objects.filter(pk=self.tarea.pk).update(
            estado=CrossingTask.CARGADA)
        self.client.post(f'/cruces/{self.tarea.pk}/red-light/')
        aduana = self.fresca().paso_actual
        self.assertEqual(aduana['clave'], CrossingTask.PASO_ADUANA)
        self.assertTrue(aduana['alerta'])
        self.client.post(f'/cruces/{self.tarea.pk}/released/')
        self.assertEqual(self.actual(), CrossingTask.PASO_ENTREGADO)

    def test_la_inspeccion_solo_si_aplica(self):
        claves = [p['clave'] for p in self.fresca().avance]
        self.assertNotIn(CrossingTask.PASO_INSPECCION, claves)
        self.entrada.lleva_inspeccion = True
        self.entrada.save()
        claves = [p['clave'] for p in self.fresca().avance]
        self.assertIn(CrossingTask.PASO_INSPECCION, claves)

    def test_el_cliente_ve_el_mismo_avance_con_su_turno(self):
        self.con_pedimento(Pedimento.EN_REVISION)
        duenio = User.objects.create_user('acme', password='x')
        UserProfile.objects.create(user=duenio, tenant=self.tenant,
                                   role='customer', customer=self.cliente)
        self.client.force_login(duenio)
        r = self.client.get('/cruces/')
        self.assertContains(r, 'Your turn:')
        self.assertContains(r, 'Left the warehouse')
        self.assertNotContains(r, 'Status:')
        self.client.force_login(self.jefa)
        r = self.client.get(f'/cruces/?customer={self.cliente.pk}')
        self.assertContains(r, "ACM's turn:")


@STORAGE_LOCAL
class LaMigracionEntregaLasQueYaTenianRemisionTests(BaseDeAlmacen):

    def test_entrega_con_la_fecha_de_la_remision(self):
        tarea = CrossingTask.objects.create(
            tenant=self.tenant, customer=self.cliente,
            fecha_de_cruce=timezone.localdate(), created_by=self.jefa,
            estado=CrossingTask.CARGADA)
        doc = DocumentoDeEntrega.objects.create(
            task=tarea, tipo=DocumentoDeEntrega.REMISION_FIRMADA,
            file=foto('firmada.jpg'), uploaded_by=self.jefa)
        sin_remision = CrossingTask.objects.create(
            tenant=self.tenant, customer=self.cliente,
            fecha_de_cruce=timezone.localdate(), created_by=self.jefa,
            estado=CrossingTask.CARGADA)
        migracion = importlib.import_module(
            'warehouse.migrations.0067_entregadas_por_remision_firmada')
        migracion.entregar(apps, None)
        tarea.refresh_from_db()
        sin_remision.refresh_from_db()
        self.assertEqual(tarea.entregada_en, doc.uploaded_at)
        self.assertEqual(tarea.entregada_por, self.jefa)
        self.assertEqual(tarea.estado, CrossingTask.CRUZADA)
        self.assertIsNone(sin_remision.entregada_en)
