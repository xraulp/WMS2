"""
El comando `limpiar_pedimentos_cancelados`: lo que se cancelo antes de que
cancelar limpiara solo (22 nov 2026).
"""
from datetime import timedelta
from io import StringIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command

from .models import (CambioDePedimento, CrossingTask, CrossingTaskItem, Pedimento,
                     PedimentoBundle, PedimentoDocument)
from .tests_cruces import BaseDeCruces


class LimpiarCanceladosTests(BaseDeCruces):

    def pedimento_en_tarea(self, custom_id, estado, cancelada=True, consecutivo='6005000'):
        t = self.tarea(estado=CrossingTask.CANCELADA if cancelada else CrossingTask.ABIERTA)
        op = self.operacion(custom_id, bundle_qty=1)
        CrossingTaskItem.objects.create(task=t, operation=op)
        ped = self.con_pedimento(op, consecutivo=consecutivo)
        Pedimento.objects.filter(pk=ped.pk).update(estado=estado)
        PedimentoDocument.objects.create(
            pedimento=ped, ranura=PedimentoDocument.COVE, original_name='x.pdf',
            file=SimpleUploadedFile('x.pdf', b'%PDF-1.4 x'))
        ped.refresh_from_db()
        return ped

    def correr(self, *args):
        salida = StringIO()
        call_command('limpiar_pedimentos_cancelados', *args, stdout=salida)
        return salida.getvalue()

    def test_sin_confirmar_solo_informa(self):
        ped = self.pedimento_en_tarea('ED261101-0001', Pedimento.APROBADO)
        texto = self.correr()
        self.assertIn(ped.etiqueta, texto)
        self.assertIn('1 pedimento(s) se limpiarian', texto)
        ped.refresh_from_db()
        self.assertEqual(ped.estado, Pedimento.APROBADO)
        self.assertEqual(ped.documentos.count(), 1)

    def test_con_confirmar_limpia_como_al_cancelar(self):
        ped = self.pedimento_en_tarea('ED261101-0001', Pedimento.APROBADO)
        self.correr('--confirmar')
        ped.refresh_from_db()
        self.assertEqual(ped.estado, Pedimento.BORRADOR)
        self.assertEqual(ped.documentos.count(), 0)
        self.assertTrue(CambioDePedimento.objects.filter(
            pedimento=ped, accion=CambioDePedimento.SALIO_DEL_CRUCE).exists())

    def test_validados_y_pagados_no(self):
        val = self.pedimento_en_tarea('ED261101-0001', Pedimento.VALIDADO)
        pag = self.pedimento_en_tarea('ED261101-0002', Pedimento.PAGADO, consecutivo='6005001')
        texto = self.correr('--confirmar')
        self.assertIn('validado o pagado', texto)
        for p, estado in ((val, Pedimento.VALIDADO), (pag, Pedimento.PAGADO)):
            p.refresh_from_db()
            self.assertEqual(p.estado, estado)
            self.assertEqual(p.documentos.count(), 1)

    def test_el_que_sigue_en_otra_tarea_viva_no(self):
        ped = self.pedimento_en_tarea('ED261101-0001', Pedimento.APROBADO)
        otra_op = self.operacion('ED261101-0009', bundle_qty=1)
        PedimentoBundle.objects.create(pedimento=ped, operation=otra_op, bultos=1)
        viva = self.tarea(fecha_de_cruce=self.viernes + timedelta(days=7))
        CrossingTaskItem.objects.create(task=viva, operation=otra_op)
        texto = self.correr('--confirmar')
        self.assertIn('sigue en otra tarea', texto)
        ped.refresh_from_db()
        self.assertEqual(ped.documentos.count(), 1)

    def test_los_de_tareas_vivas_ni_aparecen(self):
        ped = self.pedimento_en_tarea('ED261101-0001', Pedimento.APROBADO, cancelada=False)
        texto = self.correr('--confirmar')
        self.assertNotIn(ped.etiqueta, texto)
        ped.refresh_from_db()
        self.assertEqual(ped.documentos.count(), 1)
