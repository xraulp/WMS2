"""
Al cancelar una tarea de cruce, o al sacarle un pedimento, sus pedimentos
pierden los documentos y vuelven a borrador, para que al volver a un cruce se
suban actualizados (22 nov 2026).

Los validados y pagados no se tocan: ya se pagaron en aduana.
"""
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from .models import (Camion, CambioDePedimento, CrossingTask, CrossingTaskItem,
                     Pedimento, PedimentoDocument)
from .tests_cruces import BaseDeCruces


class Base(BaseDeCruces):

    def setUp(self):
        super().setUp()
        self.t = self.tarea()
        self.op = self.operacion('ED261122-0001', bundle_qty=1)
        CrossingTaskItem.objects.create(task=self.t, operation=self.op)
        self.ped = self.con_pedimento(self.op)

    def en_estado(self, estado, ped=None):
        ped = ped or self.ped
        ahora = timezone.now()
        Pedimento.objects.filter(pk=ped.pk).update(
            estado=estado, enviado_a_revision_en=ahora, aprobado_en=ahora,
            revisado_por=self.duenio,
            fecha_de_pago=timezone.localdate() + timedelta(days=2))
        for ranura in (PedimentoDocument.COVE, PedimentoDocument.MANIFESTACION):
            PedimentoDocument.objects.create(
                pedimento=ped, ranura=ranura, original_name='x.pdf',
                file=SimpleUploadedFile('x.pdf', b'%PDF-1.4 x'))
        ped.refresh_from_db()
        return ped

    def cancelar(self):
        return self.client.post(f'/cruces/{self.t.pk}/cancel/')

    def quitar(self, ped=None):
        return self.client.post(f'/cruces/{self.t.pk}/pedimento/remove/',
                                {'pedimento': (ped or self.ped).pk})


class AlCancelarLaTareaTests(Base):

    def test_borra_los_documentos_y_vuelve_a_borrador(self):
        self.en_estado(Pedimento.APROBADO)
        self.cancelar()
        self.ped.refresh_from_db()
        self.assertEqual(self.ped.documentos.count(), 0)
        self.assertEqual(self.ped.estado, Pedimento.BORRADOR)
        self.assertIsNone(self.ped.aprobado_en)
        self.assertIsNone(self.ped.enviado_a_revision_en)
        self.assertIsNone(self.ped.fecha_de_pago)
        self.assertIsNone(self.ped.revisado_por)

    def test_queda_anotado_quien_y_desde_que_estado(self):
        self.en_estado(Pedimento.EN_REVISION)
        self.cancelar()
        cambio = CambioDePedimento.objects.get(pedimento=self.ped)
        self.assertEqual(cambio.accion, CambioDePedimento.SALIO_DEL_CRUCE)
        self.assertEqual(cambio.estado_anterior, Pedimento.EN_REVISION)
        self.assertEqual(cambio.usuario, self.jefa)

    def test_en_borrador_tambien_se_vacian_sus_ranuras(self):
        self.en_estado(Pedimento.BORRADOR)
        self.cancelar()
        self.assertEqual(self.ped.documentos.count(), 0)

    def test_validado_y_pagado_no_se_tocan(self):
        for estado in (Pedimento.VALIDADO, Pedimento.PAGADO):
            with self.subTest(estado=estado):
                CrossingTask.objects.filter(pk=self.t.pk).update(estado=CrossingTask.ABIERTA)
                ped = self.en_estado(estado)
                self.cancelar()
                ped.refresh_from_db()
                self.assertEqual(ped.estado, estado)
                self.assertEqual(ped.documentos.count(), 2)
                PedimentoDocument.objects.filter(pedimento=ped).delete()

    def test_el_cliente_que_cancela_tambien_los_limpia(self):
        self.en_estado(Pedimento.APROBADO)
        self.client.force_login(self.duenio)
        self.cancelar()
        self.assertEqual(self.ped.documentos.count(), 0)

    def test_si_sigue_en_otra_tarea_viva_no_se_toca(self):
        otra_op = self.operacion('ED261122-0002', bundle_qty=1)
        from .models import PedimentoBundle
        PedimentoBundle.objects.create(pedimento=self.ped, operation=otra_op, bultos=1)
        otra = self.tarea(fecha_de_cruce=self.viernes + timedelta(days=7))
        CrossingTaskItem.objects.create(task=otra, operation=otra_op)
        self.en_estado(Pedimento.APROBADO)
        self.cancelar()
        self.ped.refresh_from_db()
        self.assertEqual(self.ped.estado, Pedimento.APROBADO)
        self.assertEqual(self.ped.documentos.count(), 2)

    def test_el_aviso_de_cancelar_lo_dice(self):
        respuesta = self.client.get('/cruces/', {'customer': self.cliente.pk})
        self.assertContains(respuesta, 'documents of its pedimentos will be deleted')


class AlSacarElPedimentoTests(Base):

    def test_borra_sus_documentos_y_vuelve_a_borrador(self):
        otra_op = self.operacion('ED261122-0002', bundle_qty=1)
        CrossingTaskItem.objects.create(task=self.t, operation=otra_op)
        otro = self.con_pedimento(otra_op, consecutivo='6005001')
        self.en_estado(Pedimento.APROBADO)
        self.en_estado(Pedimento.APROBADO, ped=otro)
        self.quitar()
        self.ped.refresh_from_db()
        otro.refresh_from_db()
        self.assertEqual(self.ped.estado, Pedimento.BORRADOR)
        self.assertEqual(self.ped.documentos.count(), 0)
        # El que se queda en la tarea conserva lo suyo.
        self.assertEqual(otro.estado, Pedimento.APROBADO)
        self.assertEqual(otro.documentos.count(), 2)

    def test_pagado_no_se_toca(self):
        self.en_estado(Pedimento.PAGADO)
        self.quitar()
        self.ped.refresh_from_db()
        self.assertEqual(self.ped.estado, Pedimento.PAGADO)
        self.assertEqual(self.ped.documentos.count(), 2)


class AlCancelarElCamionTests(Base):

    def test_limpia_los_pedimentos_de_todas_sus_tareas(self):
        camion = Camion.objects.create(tenant=self.tenant, fecha_de_cruce=self.viernes)
        CrossingTask.objects.filter(pk=self.t.pk).update(camion=camion)
        self.en_estado(Pedimento.APROBADO)
        self.client.post(f'/cruces/truck/{camion.pk}/cancel/')
        self.ped.refresh_from_db()
        self.assertEqual(self.ped.estado, Pedimento.BORRADOR)
        self.assertEqual(self.ped.documentos.count(), 0)
