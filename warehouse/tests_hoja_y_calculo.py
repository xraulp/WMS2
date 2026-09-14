"""
Las dos pantallas de impuestos, y la linea que las separa.

La hoja es del cliente y se parece a su Excel: los seis status, las fechas, la
entrada, el pedido, el pedimento y el impuesto aproximado. La pantalla del
calculo es del tenant y del agente aduanal, y tiene lo otro -- el valor de
mercancia, los fletes, los incrementables, la tasa de IGI, el DTA, el IVA y el
colchon del tipo de cambio.

Lo que estas pruebas cuidan es que esa linea no se borre con el tiempo: que el
cliente no llegue al desglose ni por la pantalla ni por la URL, y que lo que
gana la hoja por ser web -- agregar embarques y quitarlos -- no acabe borrando
una entrada capturada en bodega.
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from .models import (Catalog, ParametrosDeImpuestos, RenglonDeImpuestos,
                     Tenant, UserProfile, WarehouseOperation)


class BaseDeLaHoja(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Dyser Group', type='organization', subdomain='dyser')
        cls.jefa = User.objects.create_user('jefa', password='x')
        UserProfile.objects.create(user=cls.jefa, tenant=cls.tenant, role='admin')
        cls.cliente = Catalog.objects.create(
            category='CUSTOMER', name='Acme', tenant=cls.tenant)
        cls.comprador = User.objects.create_user('comprador', password='x')
        UserProfile.objects.create(user=cls.comprador, tenant=cls.tenant,
                                   role='customer', customer=cls.cliente)
        ParametrosDeImpuestos.objects.create(
            tenant=cls.tenant, tipo_de_cambio=Decimal('19.0000'),
            prevalidacion=Decimal('300.00'))

    def setUp(self):
        self.op = WarehouseOperation.objects.create(
            tenant=self.tenant, operation_type='ENTRY', custom_id='ED-C1',
            customer=self.cliente, created_by=self.jefa, bundle_qty=1)
        self.r = RenglonDeImpuestos.objects.create(
            tenant=self.tenant, customer=self.cliente, operation=self.op,
            valor_mercancia=Decimal('10395.00'),
            incrementables=Decimal('200.00'))


class ElClienteNoVeLosCalculosTests(BaseDeLaHoja):
    """
    El desglose y el colchon son del tenant y del agente aduanal.

    El colchon es una decision de la casa -- el tipo de cambio va alto a
    proposito --, y enseñarselo al cliente seria enseñarle de cuanto se le esta
    pidiendo de mas.
    """

    def test_el_cliente_no_entra_al_calculo_ni_por_la_url(self):
        self.client.force_login(self.comprador)
        respuesta = self.client.get('/impuestos/%d/calculo/' % self.r.pk)
        self.assertEqual(respuesta.status_code, 404)

    def test_el_tenant_si_entra(self):
        self.client.force_login(self.jefa)
        respuesta = self.client.get('/impuestos/%d/calculo/' % self.r.pk)
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(str(respuesta.context['cuenta']['T_total_pedimento']),
                         '34376.91')

    def test_la_hoja_del_cliente_no_ofrece_el_desglose(self):
        self.client.force_login(self.comprador)
        respuesta = self.client.get('/impuestos/')
        self.assertFalse(respuesta.context['ve_los_calculos'])
        self.assertNotContains(respuesta, '/calculo/')

    def test_la_hoja_del_tenant_si_lo_ofrece(self):
        self.client.force_login(self.jefa)
        respuesta = self.client.get('/impuestos/', {'customer': self.cliente.pk})
        self.assertTrue(respuesta.context['ve_los_calculos'])
        self.assertContains(respuesta, '/calculo/')

    def test_el_cliente_tampoco_guarda_el_calculo(self):
        self.client.force_login(self.comprador)
        respuesta = self.client.post('/impuestos/%d/save/' % self.r.pk,
                                     {'valor': '999'})
        self.assertEqual(respuesta.status_code, 404)
        self.r.refresh_from_db()
        self.assertEqual(str(self.r.valor_mercancia), '10395.00')


class QuitarNoEsBorrarTests(BaseDeLaHoja):
    """
    Quitar un renglon lo saca de la hoja y de ningun otro sitio.

    La hoja la arma tambien el cliente, asi que equivocarse ordenandola no
    puede costar una entrada capturada en bodega.
    """

    def test_quitar_saca_el_renglon_de_la_hoja(self):
        self.client.force_login(self.jefa)
        self.client.post('/impuestos/%d/remove/' % self.r.pk)
        respuesta = self.client.get('/impuestos/', {'customer': self.cliente.pk})
        self.assertEqual(list(respuesta.context['renglones']), [])
        self.assertEqual(respuesta.context['quitados'], 1)

    def test_quitar_no_borra_ni_el_renglon_ni_la_operacion(self):
        self.client.force_login(self.jefa)
        self.client.post('/impuestos/%d/remove/' % self.r.pk)
        self.r.refresh_from_db()
        self.assertFalse(self.r.en_la_hoja)
        self.assertIsNotNone(self.r.quitado_en)
        self.assertEqual(self.r.quitado_por, self.jefa)
        self.assertTrue(
            WarehouseOperation.objects.filter(pk=self.op.pk).exists())
        self.assertTrue(
            RenglonDeImpuestos.objects.filter(pk=self.r.pk).exists())

    def test_devolver_lo_vuelve_a_poner(self):
        self.client.force_login(self.jefa)
        self.client.post('/impuestos/%d/remove/' % self.r.pk)
        self.client.post('/impuestos/%d/restore/' % self.r.pk)
        self.r.refresh_from_db()
        self.assertTrue(self.r.en_la_hoja)
        self.assertIsNone(self.r.quitado_en)

    def test_el_cliente_quita_de_su_hoja(self):
        self.client.force_login(self.comprador)
        self.client.post('/impuestos/%d/remove/' % self.r.pk)
        self.r.refresh_from_db()
        self.assertFalse(self.r.en_la_hoja)

    def test_el_cliente_no_quita_de_la_hoja_de_otro(self):
        ajeno = Catalog.objects.create(category='CUSTOMER', name='Zeta',
                                       tenant=self.tenant)
        suyo = RenglonDeImpuestos.objects.create(
            tenant=self.tenant, customer=ajeno, po_order='ZZ-1')
        self.client.force_login(self.comprador)
        respuesta = self.client.post('/impuestos/%d/remove/' % suyo.pk)
        self.assertEqual(respuesta.status_code, 404)
        suyo.refresh_from_db()
        self.assertTrue(suyo.en_la_hoja)

    def test_un_renglon_quitado_no_suma_en_el_total(self):
        self.client.force_login(self.jefa)
        respuesta = self.client.get('/impuestos/', {'customer': self.cliente.pk})
        self.assertEqual(respuesta.context['total'], self.r.impuesto_estimado)
        self.client.post('/impuestos/%d/remove/' % self.r.pk)
        respuesta = self.client.get('/impuestos/', {'customer': self.cliente.pk})
        self.assertEqual(respuesta.context['total'], Decimal('0'))


class LaLineaQueSeTecleaTests(BaseDeLaHoja):
    """
    La linea a mano, para el embarque que todavia no ha llegado.

    Es la unica alta que vive en la hoja: los demas renglones entran solos al
    capturar la entrada. Y es de la casa, no del cliente.
    """

    def test_la_casa_agrega_una_linea(self):
        self.client.force_login(self.jefa)
        self.client.post('/impuestos/line/', {'customer': self.cliente.pk,
                                              'po_order': 'MD07/26',
                                              'status': 'NO HA LLEGADO',
                                              'factura': 'si',
                                              'pedimento': '6003298',
                                              'impuesto': '7000'})
        nuevo = RenglonDeImpuestos.objects.get(po_order='MD07/26')
        self.assertEqual(nuevo.customer, self.cliente)
        self.assertIsNone(nuevo.operation)
        # El impuesto se teclea tal cual: sin factura y sin proforma no hay
        # valor del que calcularlo.
        self.assertEqual(str(nuevo.impuesto_estimado), '7000.00')

    def test_lo_tecleado_es_lo_que_se_lee(self):
        self.client.force_login(self.jefa)
        self.client.post('/impuestos/line/', {'customer': self.cliente.pk,
                                              'po_order': 'MD08/26',
                                              'status': 'ETA 15-09',
                                              'factura': 'si',
                                              'pedimento': '6003298'})
        linea = RenglonDeImpuestos.objects.get(po_order='MD08/26')
        # El status va tal cual, sin la abreviacion del tenant pegada: lo que
        # se tecleo es lo que se quiso decir.
        self.assertEqual(linea.status_texto, 'ETA 15-09')
        self.assertTrue(linea.tiene_factura)
        self.assertEqual(linea.pedimento_texto, '6003298')

    def test_la_factura_se_puede_dejar_en_no(self):
        self.client.force_login(self.jefa)
        self.client.post('/impuestos/line/', {'customer': self.cliente.pk,
                                              'po_order': 'MD09/26',
                                              'factura': 'no'})
        linea = RenglonDeImpuestos.objects.get(po_order='MD09/26')
        self.assertFalse(linea.tiene_factura)

    def test_al_llegar_la_mercancia_manda_el_sistema(self):
        """
        Lo tecleado vale mientras no haya entrada. Cuando la mercancia llega y
        el renglon se enlaza, el status y la factura vuelven a salir solos: a
        partir de ahi hay de donde leerlos, y un dato de agosto no puede seguir
        contradiciendo lo que el expediente dice en octubre.
        """
        self.client.force_login(self.jefa)
        self.client.post('/impuestos/line/', {'customer': self.cliente.pk,
                                              'po_order': 'MD10/26',
                                              'status': 'NO HA LLEGADO',
                                              'factura': 'si'})
        linea = RenglonDeImpuestos.objects.get(po_order='MD10/26')
        self.assertEqual(linea.status_texto, 'NO HA LLEGADO')
        self.assertTrue(linea.tiene_factura)

        # La mercancia llega y se captura la entrada. El enlace lo hace la
        # captura, asi que la prueba pasa por el formulario y no por el ORM.
        self.client.post('/operations/create/', {
            'operation_type': 'ENTRY', 'date': '2026-09-13',
            'customer_id': self.cliente.pk, 'shipper_text': 'Proveedor',
            'carrier_text': 'Transportista', 'bundle_type_text': 'Tarima',
            'bundle_qty': '2', 'weight_lbs': '100',
            'description': 'Mercancia', 'po_order': 'MD10/26',
        })
        llegada = WarehouseOperation.objects.get(po_order='MD10/26')
        linea.refresh_from_db()
        self.assertEqual(linea.operation, llegada)
        # Ya hay entrada, asi que el status lo escribe el sistema.
        self.assertNotEqual(linea.status_texto, 'NO HA LLEGADO')
        # La factura marcada si sobrevive: decia «el cliente ya la mando», y
        # que llegue la mercancia no lo desmiente. Lo que falta es el archivo,
        # y de eso avisa `operaciones_sin_factura`, no esta columna.
        self.assertTrue(linea.tiene_factura)
        self.assertIsNone(linea.factura_comercial)

    def test_sin_pedido_no_se_crea_la_linea(self):
        self.client.force_login(self.jefa)
        self.client.post('/impuestos/line/', {'customer': self.cliente.pk,
                                              'impuesto': '7000'})
        self.assertFalse(RenglonDeImpuestos.objects
                         .filter(impuesto_reportado=Decimal('7000')).exists())

    def test_el_cliente_no_agrega_lineas(self):
        self.client.force_login(self.comprador)
        respuesta = self.client.post('/impuestos/line/',
                                     {'customer': self.cliente.pk,
                                      'po_order': 'ZZ-9'})
        self.assertEqual(respuesta.status_code, 404)
        self.assertFalse(RenglonDeImpuestos.objects
                         .filter(po_order='ZZ-9').exists())


class DesdeOperacionesALaHojaTests(BaseDeLaHoja):
    """
    Los embarques marcados en la pestana Operaciones.

    Una entrada capturada entra sola, asi que esta puerta es para devolver a la
    hoja lo que se quito y para los embarques anteriores al calculo.
    """

    def test_devuelve_a_la_hoja_lo_que_se_habia_quitado(self):
        self.client.force_login(self.jefa)
        self.client.post('/impuestos/%d/remove/' % self.r.pk)
        self.client.post('/impuestos/from-operations/',
                         {'op_sel': [str(self.op.pk)]})
        self.r.refresh_from_db()
        self.assertTrue(self.r.en_la_hoja)

    def test_una_entrada_vieja_sin_renglon_entra(self):
        vieja = WarehouseOperation.objects.create(
            tenant=self.tenant, operation_type='ENTRY', custom_id='ED-VIEJA',
            customer=self.cliente, created_by=self.jefa, bundle_qty=1,
            po_order='MF15/26')
        RenglonDeImpuestos.objects.filter(operation=vieja).delete()
        self.client.force_login(self.jefa)
        self.client.post('/impuestos/from-operations/',
                         {'op_sel': [str(vieja.pk)]})
        self.assertTrue(RenglonDeImpuestos.objects
                        .filter(operation=vieja, en_la_hoja=True).exists())

    def test_una_salida_no_entra_a_la_hoja(self):
        salida = WarehouseOperation.objects.create(
            tenant=self.tenant, operation_type='EXIT', custom_id='SD-1',
            customer=self.cliente, created_by=self.jefa)
        self.client.force_login(self.jefa)
        respuesta = self.client.post('/impuestos/from-operations/',
                                     {'op_sel': [str(salida.pk)]})
        self.assertFalse(RenglonDeImpuestos.objects
                         .filter(operation=salida).exists())
        self.assertContains(respuesta, 'only entries')

    def test_el_cliente_no_puede_mandar_nada_a_la_hoja(self):
        self.client.force_login(self.comprador)
        respuesta = self.client.post('/impuestos/from-operations/',
                                     {'op_sel': [str(self.op.pk)]})
        self.assertEqual(respuesta.status_code, 422)


class LaHojaEnPapelTests(BaseDeLaHoja):
    """El PDF y el correo salen de los mismos renglones que la pantalla."""

    def test_el_pdf_es_un_pdf(self):
        self.client.force_login(self.jefa)
        respuesta = self.client.get('/impuestos/pdf/',
                                    {'customer': self.cliente.pk})
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta['Content-Type'], 'application/pdf')
        self.assertTrue(respuesta.content.startswith(b'%PDF'))

    def test_el_cliente_puede_bajar_su_propia_hoja(self):
        self.client.force_login(self.comprador)
        respuesta = self.client.get('/impuestos/pdf/')
        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta.content.startswith(b'%PDF'))

    def test_el_correo_lleva_la_hoja_adjunta(self):
        from django.core import mail
        self.client.force_login(self.jefa)
        self.client.post('/impuestos/email/',
                         {'customer': self.cliente.pk,
                          'para': 'compras@acme.com'})
        self.assertEqual(len(mail.outbox), 1)
        enviado = mail.outbox[0]
        self.assertEqual(enviado.to, ['compras@acme.com'])
        self.assertEqual(len(enviado.attachments), 1)
        self.assertEqual(enviado.attachments[0][0], 'impuestos.pdf')

    def test_sin_destinatario_no_se_manda_nada(self):
        from django.core import mail
        self.client.force_login(self.jefa)
        self.client.post('/impuestos/email/', {'customer': self.cliente.pk,
                                               'para': '  '})
        self.assertEqual(len(mail.outbox), 0)

    def test_el_cliente_no_manda_su_hoja_a_nadie(self):
        from django.core import mail
        self.client.force_login(self.comprador)
        respuesta = self.client.post('/impuestos/email/',
                                     {'customer': self.cliente.pk,
                                      'para': 'otro@acme.com'})
        self.assertEqual(respuesta.status_code, 404)
        self.assertEqual(len(mail.outbox), 0)


class ElValorSeTecleaAlCapturarTests(BaseDeLaHoja):
    """
    El valor de mercancia va en el formulario de la operacion.

    Es el momento en que el papel del embarque esta delante, y con el la
    entrada nace con su estimado hecho en vez de con un renglon vacio.
    """

    def datos_de_entrada(self, **extra):
        datos = {
            'operation_type': 'ENTRY',
            'date': '2026-09-13',
            'customer_id': self.cliente.pk,
            'shipper_text': 'Proveedor',
            'carrier_text': 'Transportista',
            'bundle_type_text': 'Tarima',
            'bundle_qty': '2',
            'weight_lbs': '100',
            'description': 'Mercancia',
        }
        datos.update(extra)
        return datos

    def test_el_valor_tecleado_llega_al_renglon(self):
        self.client.force_login(self.jefa)
        self.client.post('/operations/create/',
                         self.datos_de_entrada(po_order='MD30/26',
                                               valor_mercancia='10395.00'))
        op = WarehouseOperation.objects.get(po_order='MD30/26')
        renglon = RenglonDeImpuestos.objects.get(operation=op)
        self.assertEqual(str(renglon.valor_mercancia), '10395.00')

    def test_se_guarda_la_cuenta_y_no_solo_el_total(self):
        self.client.force_login(self.jefa)
        self.client.post('/operations/create/',
                         self.datos_de_entrada(po_order='MD31/26',
                                               valor_mercancia='6000+4395'))
        renglon = RenglonDeImpuestos.objects.get(
            operation__po_order='MD31/26')
        self.assertEqual(renglon.valor_expresion, '6000+4395')
        self.assertEqual(str(renglon.valor_mercancia), '10395.00')

    def test_sin_valor_el_renglon_se_crea_igual(self):
        self.client.force_login(self.jefa)
        self.client.post('/operations/create/',
                         self.datos_de_entrada(po_order='MD32/26'))
        renglon = RenglonDeImpuestos.objects.get(
            operation__po_order='MD32/26')
        self.assertIsNone(renglon.valor_mercancia)

    def test_un_valor_mal_escrito_no_tumba_la_captura(self):
        self.client.force_login(self.jefa)
        self.client.post('/operations/create/',
                         self.datos_de_entrada(po_order='MD33/26',
                                               valor_mercancia='mil pesos'))
        # La operacion se guarda y el renglon existe: el valor se teclea
        # despues, que es lo mismo que pasa cuando todavia no se sabe.
        renglon = RenglonDeImpuestos.objects.get(
            operation__po_order='MD33/26')
        self.assertIsNone(renglon.valor_mercancia)


class LaHojaTraeLoQueElExcelTraeTests(BaseDeLaHoja):
    """Las columnas de la hoja salen de lo que ya paso, no de teclearlas."""

    def test_la_fecha_de_envio_a_revision_sale_del_pedimento(self):
        from django.utils import timezone

        from .models import Pedimento, PedimentoBundle
        ped = Pedimento.objects.create(
            tenant=self.tenant, customer=self.cliente, orden=1,
            estado=Pedimento.EN_REVISION,
            enviado_a_revision_en=timezone.now())
        PedimentoBundle.objects.create(pedimento=ped, operation=self.op,
                                       bultos=1)
        self.r.refresh_from_db()
        self.assertEqual(self.r.fecha_enviado_a_rev_y_mv,
                         ped.enviado_a_revision_en)

    def test_sin_pedimento_la_fecha_va_vacia(self):
        self.assertIsNone(self.r.fecha_enviado_a_rev_y_mv)


class LaHojaSeCierraSolaTests(BaseDeLaHoja):
    """
    Lo que ya cruzo y esta pagado deja de salir en la hoja.

    Las dos cosas hacen falta. Un pedimento pagado cuya mercancia sigue en
    bodega es justo lo que el cliente quiere ver, asi que ese se queda.
    """

    def pedimento_pagado(self, operacion):
        from django.utils import timezone

        from .models import Pedimento, PedimentoBundle
        ped = Pedimento.objects.create(
            tenant=self.tenant, customer=self.cliente, orden=1,
            estado=Pedimento.PAGADO, pagado_en=timezone.now())
        PedimentoBundle.objects.create(pedimento=ped, operation=operacion,
                                       bultos=1)
        return ped

    def cruce_cruzado(self, operacion):
        from .models import CrossingTask, CrossingTaskItem
        from django.utils import timezone
        tarea = CrossingTask.objects.create(
            tenant=self.tenant, customer=self.cliente,
            estado=CrossingTask.CRUZADA, custom_id='TC-1',
            fecha_de_cruce=timezone.localdate())
        CrossingTaskItem.objects.create(task=tarea, operation=operacion)
        return tarea

    def test_pagado_y_cruzado_sale_de_la_hoja(self):
        self.pedimento_pagado(self.op)
        self.cruce_cruzado(self.op)
        self.client.force_login(self.jefa)
        respuesta = self.client.get('/impuestos/', {'customer': self.cliente.pk})
        self.assertEqual(list(respuesta.context['renglones']), [])
        self.r.refresh_from_db()
        self.assertFalse(self.r.en_la_hoja)
        self.assertIsNotNone(self.r.cerrado_en)

    def test_pagado_pero_sin_cruzar_se_queda(self):
        self.pedimento_pagado(self.op)
        self.client.force_login(self.jefa)
        respuesta = self.client.get('/impuestos/', {'customer': self.cliente.pk})
        self.assertEqual(len(respuesta.context['renglones']), 1)
        self.r.refresh_from_db()
        self.assertTrue(self.r.en_la_hoja)

    def test_cruzado_pero_sin_pagar_se_queda(self):
        self.cruce_cruzado(self.op)
        self.client.force_login(self.jefa)
        respuesta = self.client.get('/impuestos/', {'customer': self.cliente.pk})
        self.assertEqual(len(respuesta.context['renglones']), 1)

    def test_cerrar_no_borra_nada(self):
        self.pedimento_pagado(self.op)
        self.cruce_cruzado(self.op)
        self.client.force_login(self.jefa)
        self.client.get('/impuestos/', {'customer': self.cliente.pk})
        self.assertTrue(
            RenglonDeImpuestos.objects.filter(pk=self.r.pk).exists())
        self.assertTrue(
            WarehouseOperation.objects.filter(pk=self.op.pk).exists())

    def test_lo_cerrado_no_suma_en_el_total(self):
        self.pedimento_pagado(self.op)
        self.cruce_cruzado(self.op)
        self.client.force_login(self.jefa)
        respuesta = self.client.get('/impuestos/', {'customer': self.cliente.pk})
        self.assertEqual(respuesta.context['total'], Decimal('0'))

    def test_se_puede_devolver_y_no_se_vuelve_a_cerrar_sin_motivo(self):
        self.pedimento_pagado(self.op)
        cruce = self.cruce_cruzado(self.op)
        self.client.force_login(self.jefa)
        self.client.get('/impuestos/', {'customer': self.cliente.pk})

        # Se cancela el cruce: el embarque ya no esta cruzado, asi que al
        # devolverlo a la hoja tiene que quedarse.
        from .models import CrossingTask
        cruce.estado = CrossingTask.CANCELADA
        cruce.save(update_fields=['estado'])

        self.client.post('/impuestos/%d/restore/' % self.r.pk)
        self.r.refresh_from_db()
        self.assertTrue(self.r.en_la_hoja)
        self.assertIsNone(self.r.cerrado_en)
        respuesta = self.client.get('/impuestos/', {'customer': self.cliente.pk})
        self.assertEqual(len(respuesta.context['renglones']), 1)

    def test_el_pdf_tampoco_lleva_lo_cerrado(self):
        self.pedimento_pagado(self.op)
        self.cruce_cruzado(self.op)
        self.client.force_login(self.jefa)
        respuesta = self.client.get('/impuestos/pdf/',
                                    {'customer': self.cliente.pk})
        self.assertEqual(respuesta.status_code, 200)
        self.r.refresh_from_db()
        self.assertFalse(self.r.en_la_hoja)


class ElValorSeCorrigeDesdeLaEdicionTests(BaseDeLaHoja):
    """
    El valor de mercancia tambien se teclea en la edicion de la operacion.

    Es el caso de quien captura sin tener la factura delante: antes habia que
    acordarse de ir despues a la hoja de impuestos a escribirlo.
    """

    def setUp(self):
        super().setUp()
        # Un renglon sin valor, que es el caso que esto resuelve. Los
        # incrementables se quedan: son los 200 del ejemplo del Excel, y lo que
        # falta es el valor.
        self.r.valor_mercancia = None
        self.r.valor_expresion = ''
        self.r.save()

    def test_el_valor_tecleado_al_editar_llega_al_renglon(self):
        self.client.force_login(self.jefa)
        self.client.post('/operations/%d/edit/' % self.op.pk,
                         {'date': '2026-09-13', 'valor_mercancia': '10395.00'})
        self.r.refresh_from_db()
        self.assertEqual(str(self.r.valor_mercancia), '10395.00')

    def test_admite_una_suma(self):
        self.client.force_login(self.jefa)
        self.client.post('/operations/%d/edit/' % self.op.pk,
                         {'date': '2026-09-13', 'valor_mercancia': '6000+4395'})
        self.r.refresh_from_db()
        self.assertEqual(self.r.valor_expresion, '6000+4395')
        self.assertEqual(str(self.r.valor_mercancia), '10395.00')

    def test_el_estimado_aparece_en_la_hoja_en_ese_momento(self):
        self.client.force_login(self.jefa)
        respuesta = self.client.get('/impuestos/', {'customer': self.cliente.pk})
        self.assertIsNone(respuesta.context['renglones'][0].estimado)

        self.client.post('/operations/%d/edit/' % self.op.pk,
                         {'date': '2026-09-13', 'valor_mercancia': '10395.00'})
        respuesta = self.client.get('/impuestos/', {'customer': self.cliente.pk})
        self.assertEqual(str(respuesta.context['renglones'][0].estimado),
                         '34376.91')

    def test_un_valor_mal_escrito_no_se_guarda_y_avisa(self):
        self.client.force_login(self.jefa)
        respuesta = self.client.post(
            '/operations/%d/edit/' % self.op.pk,
            {'date': '2026-09-13', 'valor_mercancia': 'mil pesos'})
        self.assertEqual(respuesta.status_code, 422)
        self.r.refresh_from_db()
        self.assertIsNone(self.r.valor_mercancia)

    def test_sin_el_campo_en_el_formulario_no_se_borra_el_valor(self):
        self.r.valor_mercancia = Decimal('500')
        self.r.valor_expresion = '500'
        self.r.save()
        self.client.force_login(self.jefa)
        # Una pantalla de edicion que no lleva el campo: ausencia no es borrado.
        self.client.post('/operations/%d/edit/' % self.op.pk,
                         {'date': '2026-09-13'})
        self.r.refresh_from_db()
        self.assertEqual(str(self.r.valor_mercancia), '500.00')


class ElAvisoDeLasLineasSueltasTests(BaseDeLaHoja):
    """
    Al capturar una entrada se avisa de las lineas a mano que siguen sueltas.

    El enlace se hace por el numero de pedido, asi que un pedido escrito de
    otra forma deja dos renglones del mismo embarque en la hoja del cliente.
    """

    def capturar(self, pedido):
        return self.client.post('/operations/create/', {
            'operation_type': 'ENTRY', 'date': '2026-09-13',
            'customer_id': self.cliente.pk, 'shipper_text': 'Proveedor',
            'carrier_text': 'Transportista', 'bundle_type_text': 'Tarima',
            'bundle_qty': '2', 'weight_lbs': '100',
            'description': 'Mercancia', 'po_order': pedido,
        })

    def test_avisa_de_la_linea_que_quedo_suelta(self):
        RenglonDeImpuestos.objects.create(
            tenant=self.tenant, customer=self.cliente, po_order='MD07/26',
            status_manual='NO HA LLEGADO')
        self.client.force_login(self.jefa)
        # Se captura con el pedido escrito de otra forma: no hay enlace.
        respuesta = self.capturar('MD-07/26')
        self.assertContains(respuesta, 'MD07/26')

    def test_si_se_engancho_no_avisa_de_ella(self):
        RenglonDeImpuestos.objects.create(
            tenant=self.tenant, customer=self.cliente, po_order='MD07/26',
            status_manual='NO HA LLEGADO')
        self.client.force_login(self.jefa)
        respuesta = self.capturar('MD07/26')
        # Se engancho con la entrada, asi que ya no esta suelta.
        self.assertNotContains(respuesta, 'MD07/26')

    def test_sin_lineas_sueltas_no_hay_aviso(self):
        self.client.force_login(self.jefa)
        respuesta = self.capturar('MF15/26')
        self.assertNotContains(respuesta, 'waiting for')


class ElCheckDeLaFacturaTests(BaseDeLaHoja):
    """
    El check de «ya tenemos la factura», al capturar y al editar.

    Contesta «la tenemos», que es lo que la hoja enseña en su columna, y no
    «esta en el expediente»: para eso esta el archivo, que se sigue pidiendo y
    del que siguen avisando la pantalla de pedimentos y la tarea de cruce.
    """

    def datos(self, **extra):
        datos = {
            'operation_type': 'ENTRY', 'date': '2026-09-13',
            'customer_id': self.cliente.pk, 'shipper_text': 'Proveedor',
            'carrier_text': 'Transportista', 'bundle_type_text': 'Tarima',
            'bundle_qty': '2', 'weight_lbs': '100',
            'description': 'Mercancia',
        }
        datos.update(extra)
        return datos

    def test_marcarlo_al_capturar_pone_la_columna_en_si(self):
        self.client.force_login(self.jefa)
        self.client.post('/operations/create/',
                         self.datos(po_order='MD40/26', factura_present='1',
                                    tiene_factura='1'))
        renglon = RenglonDeImpuestos.objects.get(operation__po_order='MD40/26')
        self.assertTrue(renglon.tiene_factura)

    def test_sin_marcarlo_la_columna_queda_en_no(self):
        self.client.force_login(self.jefa)
        self.client.post('/operations/create/',
                         self.datos(po_order='MD41/26', factura_present='1'))
        renglon = RenglonDeImpuestos.objects.get(operation__po_order='MD41/26')
        self.assertFalse(renglon.tiene_factura)

    def test_marcarlo_quita_el_provisional_del_valor(self):
        self.client.force_login(self.jefa)
        self.client.post('/operations/create/',
                         self.datos(po_order='MD42/26',
                                    valor_mercancia='10395.00',
                                    factura_present='1', tiene_factura='1'))
        renglon = RenglonDeImpuestos.objects.get(operation__po_order='MD42/26')
        self.assertFalse(renglon.valor_es_provisional)
        self.assertEqual(renglon.estado_del_valor, 'FACTURA')

    def test_sin_marcarlo_el_valor_sigue_siendo_provisional(self):
        self.client.force_login(self.jefa)
        self.client.post('/operations/create/',
                         self.datos(po_order='MD43/26',
                                    valor_mercancia='10395.00',
                                    factura_present='1'))
        renglon = RenglonDeImpuestos.objects.get(operation__po_order='MD43/26')
        self.assertTrue(renglon.valor_es_provisional)

    def test_se_puede_marcar_despues_al_editar(self):
        self.client.force_login(self.jefa)
        self.assertFalse(self.r.tiene_factura)
        self.client.post('/operations/%d/edit/' % self.op.pk,
                         {'date': '2026-09-13', 'factura_present': '1',
                          'tiene_factura': '1'})
        self.r.refresh_from_db()
        self.assertTrue(self.r.tiene_factura)

    def test_y_se_puede_desmarcar(self):
        self.r.factura_manual = True
        self.r.save()
        self.client.force_login(self.jefa)
        self.client.post('/operations/%d/edit/' % self.op.pk,
                         {'date': '2026-09-13', 'factura_present': '1'})
        self.r.refresh_from_db()
        self.assertFalse(self.r.tiene_factura)

    def test_el_archivo_manda_sobre_el_check(self):
        """
        Un documento cargado no admite discusion: aunque nadie marcara el
        check, la factura esta.
        """
        from django.core.files.base import ContentFile

        from .models import OperationDocument
        doc = OperationDocument(
            tenant=self.tenant, operation=self.op,
            ranura=OperationDocument.RANURA_FACTURA_COMERCIAL)
        doc.file.save('factura.pdf', ContentFile(b'%PDF-1.4'), save=True)
        self.r.factura_manual = False
        self.r.save()
        self.assertTrue(self.r.tiene_factura)

    def test_el_check_no_mete_el_archivo_en_el_expediente(self):
        """
        Marcar el check no sube nada: el expediente lo sigue pidiendo, y por
        eso la tarea de cruce sigue avisando de la factura que falta.
        """
        from django.utils import timezone

        from .models import CrossingTask, CrossingTaskItem
        self.r.factura_manual = True
        self.r.save()
        tarea = CrossingTask.objects.create(
            tenant=self.tenant, customer=self.cliente, custom_id='TC-9',
            fecha_de_cruce=timezone.localdate())
        CrossingTaskItem.objects.create(task=tarea, operation=self.op)
        self.assertEqual([o.custom_id for o in tarea.operaciones_sin_factura],
                         ['ED-C1'])


class ElClienteSubeSuFacturaTests(BaseDeLaHoja):
    """
    La factura la sube tambien el cliente.

    Es suya: se la manda su proveedor, y el sitio donde lee que falta -- el NO
    de su hoja -- tiene que ser el sitio donde la sube. Lo unico acotado es a
    cuales: solo los embarques que ya puede ver.
    """

    def archivo(self, nombre='factura.pdf'):
        from django.core.files.uploadedfile import SimpleUploadedFile
        return SimpleUploadedFile(nombre, b'%PDF-1.4 factura',
                                  content_type='application/pdf')

    def subir(self, op, **extra):
        datos = {'archivo': self.archivo(), 'customer': self.cliente.pk,
                 'desde': 'impuestos'}
        datos.update(extra)
        return self.client.post('/operations/%d/invoice/upload/' % op.pk, datos)

    def test_el_cliente_sube_la_factura_de_su_embarque(self):
        self.client.force_login(self.comprador)
        self.assertFalse(self.r.tiene_factura)
        self.subir(self.op)
        self.r.refresh_from_db()
        self.assertTrue(self.r.tiene_factura)
        self.assertIsNotNone(self.r.factura_comercial)

    def test_y_con_eso_el_valor_deja_de_ser_provisional(self):
        self.client.force_login(self.comprador)
        self.assertTrue(self.r.valor_es_provisional)
        self.subir(self.op)
        self.r.refresh_from_db()
        self.assertEqual(self.r.estado_del_valor, 'FACTURA')

    def test_el_cliente_no_sube_nada_al_embarque_de_otro(self):
        ajeno = Catalog.objects.create(category='CUSTOMER', name='Zeta',
                                       tenant=self.tenant)
        suyo = WarehouseOperation.objects.create(
            tenant=self.tenant, operation_type='ENTRY', custom_id='ED-ZETA',
            customer=ajeno, created_by=self.jefa, bundle_qty=1)
        self.client.force_login(self.comprador)
        respuesta = self.subir(suyo)
        self.assertEqual(respuesta.status_code, 404)
        self.assertFalse(suyo.documents.exists())

    def test_se_vuelve_a_la_hoja_y_no_a_pedimentos(self):
        self.client.force_login(self.comprador)
        respuesta = self.subir(self.op)
        self.assertEqual(respuesta.status_code, 302)
        self.assertIn('/impuestos/', respuesta['Location'])

    def test_sin_archivo_avisa_y_vuelve_a_la_hoja(self):
        self.client.force_login(self.comprador)
        respuesta = self.client.post(
            '/operations/%d/invoice/upload/' % self.op.pk,
            {'customer': self.cliente.pk, 'desde': 'impuestos'})
        self.assertEqual(respuesta.status_code, 302)
        self.assertIn('/impuestos/', respuesta['Location'])
        self.assertFalse(self.op.documents.exists())

    def test_el_cliente_marca_una_que_ya_estaba_en_el_expediente(self):
        """
        La factura pudo llegar por el hilo del chat o adjunta al capturar: esta
        en el expediente pero nadie ha dicho que sea ella.
        """
        from .models import OperationDocument
        doc = OperationDocument(tenant=self.tenant, operation=self.op,
                                original_name='factura-proveedor.pdf')
        doc.file.save('factura-proveedor.pdf', self.archivo(), save=True)
        self.assertFalse(self.r.tiene_factura)

        self.client.force_login(self.comprador)
        self.client.post('/operations/%d/invoice/mark/' % self.op.pk,
                         {'documento': doc.pk, 'customer': self.cliente.pk,
                          'desde': 'impuestos'})
        self.r.refresh_from_db()
        self.assertTrue(self.r.tiene_factura)
        doc.refresh_from_db()
        self.assertEqual(doc.ranura,
                         OperationDocument.RANURA_FACTURA_COMERCIAL)

    def test_el_cliente_no_marca_nada_del_embarque_de_otro(self):
        from .models import OperationDocument
        ajeno = Catalog.objects.create(category='CUSTOMER', name='Zeta',
                                       tenant=self.tenant)
        suyo = WarehouseOperation.objects.create(
            tenant=self.tenant, operation_type='ENTRY', custom_id='ED-ZETA2',
            customer=ajeno, created_by=self.jefa, bundle_qty=1)
        doc = OperationDocument(tenant=self.tenant, operation=suyo)
        doc.file.save('ajena.pdf', self.archivo(), save=True)

        self.client.force_login(self.comprador)
        respuesta = self.client.post(
            '/operations/%d/invoice/mark/' % suyo.pk,
            {'documento': doc.pk, 'customer': ajeno.pk, 'desde': 'impuestos'})
        self.assertEqual(respuesta.status_code, 404)
        doc.refresh_from_db()
        self.assertEqual(doc.ranura, '')

    def test_la_hoja_ofrece_los_archivos_que_ya_tiene(self):
        from .models import OperationDocument
        doc = OperationDocument(tenant=self.tenant, operation=self.op,
                                original_name='guia.pdf')
        doc.file.save('guia.pdf', self.archivo(), save=True)
        self.client.force_login(self.comprador)
        respuesta = self.client.get('/impuestos/')
        self.assertEqual(list(respuesta.context['renglones'][0].del_expediente),
                         [doc])

    def test_con_la_factura_puesta_ya_no_ofrece_elegir(self):
        self.client.force_login(self.comprador)
        self.subir(self.op)
        respuesta = self.client.get('/impuestos/')
        self.assertEqual(respuesta.context['renglones'][0].del_expediente, [])

    def test_la_casa_sigue_volviendo_a_pedimentos(self):
        """Quien sube desde la pantalla de pedimentos se queda en ella."""
        self.client.force_login(self.jefa)
        respuesta = self.client.post(
            '/operations/%d/invoice/upload/' % self.op.pk,
            {'archivo': self.archivo(), 'customer': self.cliente.pk})
        self.assertEqual(respuesta.status_code, 302)
        self.assertIn('/pedimentos/', respuesta['Location'])
