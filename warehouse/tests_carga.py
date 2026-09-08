"""
Los dos papeles, el escaneo y la remision.

La historia que hay que romper tiene cinco eslabones: oficina emite la orden en
cuanto el cliente da la primera instruccion, el papel sale de la impresora y se
va a la bodega, el cliente cambia la instruccion, nadie se lo dice a bodega, y
el transfer se va con mercancia de mas o de menos -- y el problema aparece en
la aduana, que es el peor sitio posible para descubrirlo.

El eslabon que se rompe no es el tercero: el cliente va a seguir cambiando de
opinion, eso es el negocio. Es el segundo -- que exista un papel definitivo
circulando desde antes de que la informacion sea definitiva.

Lo que se prueba aqui es cada uno de esos candados, por separado y encadenados.
"""
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from . import bultos
from .models import (Catalog, CrossingTask, CrossingTaskItem, EscaneoDeBulto,
                     LoadOrder, Pedimento, PedimentoBundle, Remision, Tenant,
                     UserProfile, WarehouseOperation)


class BaseDeCarga(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Dyser Group', type='organization', subdomain='dyser',
            short_name='DYSER')
        cls.cliente = Catalog.objects.create(
            category='CUSTOMER', name='Ferreteria Lopez', abbreviation='LBO',
            tenant=cls.tenant, rfc='FLO980412K33',
            linea_de_enlace='Autotransportes del Bravo S.A. de C.V.',
            domicilio_de_enlace='Patio fiscal km 21, Nuevo Laredo')

        def usuario(nombre, rol, **extra):
            u = User.objects.create_user(nombre, password='x')
            UserProfile.objects.create(user=u, tenant=cls.tenant, role=rol,
                                       **extra)
            return u

        cls.jefa   = usuario('jefa', 'manager')
        cls.mozo   = usuario('mozo', 'staff')
        cls.duenio = usuario('lopez', 'customer', customer=cls.cliente)

    def setUp(self):
        self.client.force_login(self.jefa)
        self.tarea = CrossingTask.objects.create(
            tenant=self.tenant, customer=self.cliente,
            fecha_de_cruce=timezone.localdate() + timedelta(days=3),
            created_by=self.jefa)

    def operacion(self, custom_id, bultos_=2, **extra):
        return WarehouseOperation.objects.create(
            tenant=self.tenant, operation_type='ENTRY', custom_id=custom_id,
            customer=self.cliente, bundle_qty=bultos_, **extra)

    def meter(self, op):
        return CrossingTaskItem.objects.create(task=self.tarea, operation=op)

    def con_pedimento(self, op, estado=Pedimento.PAGADO, consecutivo='6005000'):
        ped = Pedimento.objects.create(
            tenant=self.tenant, customer=self.cliente,
            orden=Pedimento.objects.filter(customer=self.cliente).count() + 1,
            ped_aduana='24', ped_patente='1515', ped_consecutivo=consecutivo,
            estado=estado)
        PedimentoBundle.objects.create(pedimento=ped, operation=op,
                                       bultos=op.bundle_qty or 1)
        return ped

    def tarea_lista(self):
        """Una tarea con un embarque de dos bultos y su pedimento pagado."""
        op = self.operacion('ED260901-0001', 2)
        self.meter(op)
        self.con_pedimento(op)
        return op

    def emitir(self):
        self.client.post('/cruces/%d/order/' % self.tarea.pk)
        return self.tarea.ordenes.order_by('-version').first()

    def pistolear(self, op, numero, fase='CARGA'):
        return self.client.post(
            '/cruces/%d/verify/scan/' % self.tarea.pk,
            {'codigo': bultos.codigo(op.custom_id, numero), 'fase': fase})


class CuandoSePuedeEmitirLaOrdenTests(BaseDeCarga):
    """
    Se habilita despues de "pedimentos pagados".

    En ese punto la instruccion ya costo dinero y ya no cambia casi nunca, asi
    que el papel definitivo nace cuando la informacion ya es definitiva. Entre
    que la tarea se crea y que los pedimentos se pagan pueden pasar dias sin
    ninguna orden: es a proposito, y lo que cubre ese hueco es la lista de
    preparacion.
    """

    def test_una_tarea_vacia_no_tiene_orden(self):
        faltan = [str(f) for f in LoadOrder.faltantes_para_emitir(self.tarea)]
        self.assertIn('No shipments in this crossing yet', faltan)

    def test_sin_pedimento_no_hay_orden(self):
        self.meter(self.operacion('ED260901-0001'))
        faltan = [str(f) for f in LoadOrder.faltantes_para_emitir(self.tarea)]
        self.assertTrue(any('pedimento' in f for f in faltan), faltan)

    def test_con_el_pedimento_sin_pagar_tampoco(self):
        op = self.operacion('ED260901-0002')
        self.meter(op)
        self.con_pedimento(op, estado=Pedimento.EN_REVISION)
        faltan = [str(f) for f in LoadOrder.faltantes_para_emitir(self.tarea)]
        self.assertTrue(any('not paid' in f for f in faltan), faltan)

    def test_con_bultos_sin_pedimento_tampoco(self):
        # Esta comprobacion existia antes como puerta de la revision. Su sitio
        # es este, que es donde de verdad importa que no falte nada.
        op = self.operacion('ED260901-0003', bultos_=5)
        self.meter(op)
        ped = Pedimento.objects.create(
            tenant=self.tenant, customer=self.cliente, orden=1,
            ped_aduana='24', ped_patente='1515', ped_consecutivo='6005000',
            estado=Pedimento.PAGADO)
        PedimentoBundle.objects.create(pedimento=ped, operation=op, bultos=3)
        faltan = [str(f) for f in LoadOrder.faltantes_para_emitir(self.tarea)]
        self.assertTrue(any('ED260901-0003' in f for f in faltan), faltan)

    def test_el_boton_forzado_por_la_url_tampoco_pasa(self):
        self.meter(self.operacion('ED260901-0004'))
        respuesta = self.client.post('/cruces/%d/order/' % self.tarea.pk)
        self.assertEqual(respuesta.status_code, 422)
        self.assertFalse(self.tarea.ordenes.exists())

    def test_con_todo_pagado_la_orden_sale(self):
        self.tarea_lista()
        self.assertEqual(LoadOrder.faltantes_para_emitir(self.tarea), [])
        respuesta = self.client.post('/cruces/%d/order/' % self.tarea.pk)
        self.assertEqual(respuesta.status_code, 302)
        orden = self.tarea.ordenes.get()
        self.assertEqual(orden.version, 1)
        self.assertTrue(orden.custom_id.startswith('OC'))
        self.tarea.refresh_from_db()
        self.assertEqual(self.tarea.estado, CrossingTask.CON_ORDEN)

    def test_el_cliente_no_emite_la_orden(self):
        self.tarea_lista()
        self.client.force_login(self.duenio)
        respuesta = self.client.post('/cruces/%d/order/' % self.tarea.pk)
        self.assertEqual(respuesta.status_code, 404)


class LaOrdenEsUnaCopiaTests(BaseDeCarga):
    """
    Su contenido es, siempre, lo que la tarea decia al emitirla.

    Se copia y no se mira en vivo: la orden que alguien lleva impresa tiene que
    poder compararse contra lo que decia cuando se imprimio, no contra lo que
    dice la tarea ahora. Ese es el eslabon que se esta rompiendo.
    """

    def test_los_renglones_se_copian(self):
        op = self.operacion('ED260901-0001', 2, po_order='MD07/26',
                            invoice='INV-55120')
        self.meter(op)
        self.con_pedimento(op)
        orden = self.emitir()
        renglon = orden.renglones.get()
        self.assertEqual(renglon.bultos, 2)
        self.assertEqual(renglon.po_order, 'MD07/26')
        self.assertEqual(renglon.invoice, 'INV-55120')
        self.assertEqual(renglon.pedimento, '24-1515-6005000')

    def test_cambiar_la_operacion_no_cambia_la_orden_emitida(self):
        op = self.operacion('ED260901-0001', 2, po_order='MD07/26')
        self.meter(op)
        self.con_pedimento(op)
        orden = self.emitir()
        op.po_order = 'OTRO/26'
        op.save()
        self.assertEqual(orden.renglones.get().po_order, 'MD07/26')


class LaOrdenSubeDeVersionTests(BaseDeCarga):
    """
    Cualquier cambio en la tarea despues de emitida la sube a v2.

    La anterior no se borra: alguien la tiene impresa en la mano, y el sistema
    tiene que poder contestarle que ya no vale cuando la pistolee.
    """

    def setUp(self):
        super().setUp()
        self.op = self.tarea_lista()
        self.orden = self.emitir()

    def test_meter_un_embarque_sube_la_version(self):
        otra = self.operacion('ED260901-0002', 1)
        self.con_pedimento(otra, consecutivo='6005001')
        self.client.post('/cruces/%d/add/' % self.tarea.pk,
                         {'operation': otra.pk, 'motivo': 'lo pidio el cliente'})
        self.assertEqual(self.tarea.ordenes.count(), 2)
        nueva = self.tarea.ordenes.order_by('-version').first()
        self.assertEqual(nueva.version, 2)
        self.orden.refresh_from_db()
        self.assertTrue(self.orden.obsoleta)
        # Y la version nueva ya lleva el embarque nuevo dentro.
        self.assertEqual(nueva.renglones.count(), 2)

    def test_sacar_un_embarque_tambien(self):
        renglon = self.tarea.renglones.get()
        self.client.post('/cruces/%d/remove/' % self.tarea.pk,
                         {'renglon': renglon.pk, 'motivo': 'se queda'})
        self.assertEqual(self.tarea.ordenes.count(), 2)
        self.orden.refresh_from_db()
        self.assertTrue(self.orden.obsoleta)

    def test_el_numero_no_cambia_al_subir_de_version(self):
        otra = self.operacion('ED260901-0003', 1)
        self.con_pedimento(otra, consecutivo='6005002')
        self.client.post('/cruces/%d/add/' % self.tarea.pk,
                         {'operation': otra.pk, 'motivo': 'x'})
        numeros = set(self.tarea.ordenes.values_list('custom_id', flat=True))
        self.assertEqual(len(numeros), 1)

    def test_el_qr_de_la_hoja_vieja_dice_que_esta_vencida(self):
        # El papel sigue sin saber nada -- pero ahora se le puede preguntar al
        # sistema en cinco segundos, sin llamar a oficina.
        otra = self.operacion('ED260901-0004', 1)
        self.con_pedimento(otra, consecutivo='6005003')
        self.client.post('/cruces/%d/add/' % self.tarea.pk,
                         {'operation': otra.pk, 'motivo': 'x'})
        respuesta = self.client.get('/orden/%d/v%d/'
                                    % (self.orden.pk, self.orden.version))
        self.assertEqual(respuesta.status_code, 200)
        self.assertFalse(respuesta.context['esta_vigente'])
        self.assertEqual(respuesta.context['vigente'].version, 2)

    def test_el_qr_de_la_hoja_vigente_dice_que_adelante(self):
        respuesta = self.client.get('/orden/%d/v%d/'
                                    % (self.orden.pk, self.orden.version))
        self.assertTrue(respuesta.context['esta_vigente'])


class NadaSeCargaSinPistolearTests(BaseDeCarga):
    """
    El teléfono compara contra la orden vigente y avisa de las tres cosas que
    pueden salir mal: falta, sobra y repetido.

    `sobra` es la grave: mercancia que se va sin amparar, y que aparece en la
    aduana.
    """

    def setUp(self):
        super().setUp()
        self.op = self.tarea_lista()
        self.orden = self.emitir()

    def test_al_principio_no_hay_nada_escaneado(self):
        cuadre = self.orden.cuadre()
        self.assertEqual(cuadre['verificados'], 0)
        self.assertEqual(cuadre['esperados'], 2)
        self.assertFalse(cuadre['cuadra'])

    def test_pistolear_los_dos_bultos_cuadra(self):
        self.pistolear(self.op, 1)
        self.pistolear(self.op, 2)
        cuadre = self.orden.cuadre()
        self.assertEqual(cuadre['verificados'], 2)
        self.assertTrue(cuadre['cuadra'])
        self.assertEqual(cuadre['faltan'], [])

    def test_falta_uno(self):
        self.pistolear(self.op, 1)
        cuadre = self.orden.cuadre()
        self.assertFalse(cuadre['cuadra'])
        # Se cuenta por operacion y no por bulto concreto: con un embarque
        # parcial la orden dice cuantos van, no cuales, y el que se queda es
        # sencillamente el que no se pistoleo.
        self.assertEqual(cuadre['faltan'], ['ED260901-0001: 1 missing'])

    def test_sobra_uno(self):
        # Mercancia que se iba a ir sin amparar.
        ajena = self.operacion('ED260901-0099', 1)
        self.pistolear(self.op, 1)
        self.pistolear(self.op, 2)
        self.pistolear(ajena, 1)
        cuadre = self.orden.cuadre()
        self.assertEqual(cuadre['sobran'], ['ED260901-0099-1'])
        self.assertFalse(cuadre['cuadra'])

    def test_el_mismo_bulto_dos_veces_no_cuenta_dos(self):
        self.pistolear(self.op, 1)
        respuesta = self.pistolear(self.op, 1)
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(self.orden.cuadre()['verificados'], 1)
        self.assertEqual(EscaneoDeBulto.objects.filter(task=self.tarea).count(), 1)

    def test_un_codigo_que_no_se_entiende_se_rechaza(self):
        respuesta = self.client.post('/cruces/%d/verify/scan/' % self.tarea.pk,
                                     {'codigo': 'basura', 'fase': 'CARGA'})
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(EscaneoDeBulto.objects.filter(task=self.tarea).count(), 0)

    def test_una_etiqueta_sin_numero_de_bulto_no_vale_para_cargar(self):
        # Sin numero no se puede decir cuales 19 se subieron, que es todo el
        # punto de pistolear.
        respuesta = self.client.post('/cruces/%d/verify/scan/' % self.tarea.pk,
                                     {'codigo': self.op.custom_id, 'fase': 'CARGA'})
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(EscaneoDeBulto.objects.filter(task=self.tarea).count(), 0)

    def test_las_dos_pasadas_no_se_mezclan(self):
        # La de preparar no habilita nada; la de cargar es la que cuenta.
        self.pistolear(self.op, 1, fase='PREPARACION')
        self.pistolear(self.op, 2, fase='PREPARACION')
        self.assertTrue(self.orden.cuadre('PREPARACION')['cuadra'])
        self.assertFalse(self.orden.cuadre('CARGA')['cuadra'])

    def test_se_puede_deshacer_un_disparo(self):
        # Pasa: se pistolea el bulto de al lado.
        self.pistolear(self.op, 1)
        escaneo = EscaneoDeBulto.objects.get(task=self.tarea)
        self.client.post('/cruces/%d/verify/undo/' % self.tarea.pk,
                         {'escaneo': escaneo.pk})
        self.assertEqual(self.orden.cuadre()['verificados'], 0)


class LaRemisionTests(BaseDeCarga):
    """
    Se habilita solo si el escaneo cuadra, y tiene salida de emergencia.

    Lo que pasa siempre que un candado no tiene salida es que el embarque se va
    por fuera del sistema. Por eso la excepcion existe -- pero se ve: sale con
    la diferencia impresa en el papel que lleva el chofer, porque si la
    excepcion no se ve, deja de ser una excepcion.
    """

    def setUp(self):
        super().setUp()
        self.op = self.tarea_lista()
        self.orden = self.emitir()

    def emitir_remision(self, **extra):
        datos = {'transfer_empresa': 'Transfers Rio Bravo',
                 'transfer_chofer': 'Ramon Escobedo',
                 'transfer_unidad': 'TRL-4471', 'sello': 'MX-0099821'}
        datos.update(extra)
        return self.client.post('/cruces/%d/remision/' % self.tarea.pk, datos)

    def test_sin_cuadrar_no_sale(self):
        self.pistolear(self.op, 1)
        respuesta = self.emitir_remision()
        self.assertEqual(respuesta.status_code, 422)
        self.assertFalse(Remision.objects.exists())

    def test_cuadrando_sale(self):
        self.pistolear(self.op, 1)
        self.pistolear(self.op, 2)
        respuesta = self.emitir_remision()
        self.assertEqual(respuesta.status_code, 302)
        rem = Remision.objects.get()
        self.assertTrue(rem.custom_id.startswith('RM'))
        self.assertEqual(rem.order, self.orden)
        self.assertEqual(rem.bultos_verificados, 2)
        self.assertFalse(rem.con_discrepancia)
        self.tarea.refresh_from_db()
        self.assertEqual(self.tarea.estado, CrossingTask.CARGADA)

    def test_la_cadena_de_entrega_se_propone_de_la_ficha_del_cliente(self):
        # Un dato que se teclea una vez al ano en vez de una vez por embarque
        # es un dato que casi nunca sale mal, y aqui salir mal significa que la
        # mercancia se le entrega a quien no es.
        self.pistolear(self.op, 1)
        self.pistolear(self.op, 2)
        self.emitir_remision()
        rem = Remision.objects.get()
        self.assertEqual(rem.linea_de_enlace,
                         'Autotransportes del Bravo S.A. de C.V.')
        self.assertEqual(rem.destinatario_rfc, 'FLO980412K33')

    def test_se_puede_cambiar_ese_dia_si_toca_otra_linea(self):
        self.pistolear(self.op, 1)
        self.pistolear(self.op, 2)
        self.emitir_remision(linea_de_enlace='Otra Linea S.A.')
        self.assertEqual(Remision.objects.get().linea_de_enlace, 'Otra Linea S.A.')

    def test_el_staff_no_puede_sacarla_con_diferencia(self):
        # Nunca staff, nunca sin motivo.
        self.pistolear(self.op, 1)
        self.client.force_login(self.mozo)
        respuesta = self.emitir_remision(motivo='hay prisa')
        self.assertEqual(respuesta.status_code, 422)
        self.assertFalse(Remision.objects.exists())

    def test_un_manager_si_pero_con_motivo(self):
        self.pistolear(self.op, 1)
        sin_motivo = self.emitir_remision()
        self.assertEqual(sin_motivo.status_code, 422)

        con_motivo = self.emitir_remision(
            motivo='el cliente pidio dejar una caja para el siguiente cruce')
        self.assertEqual(con_motivo.status_code, 302)
        rem = Remision.objects.get()
        self.assertTrue(rem.con_discrepancia)
        self.assertEqual(rem.autorizada_por, self.jefa)
        self.assertEqual(rem.bultos_verificados, 1)
        self.assertEqual(rem.bultos_de_la_orden, 2)
        # La diferencia va escrita, para que salga impresa en el papel que
        # lleva el chofer.
        self.assertEqual(rem.diferencia, 'ED260901-0001: 1 missing')

    def test_si_la_tarea_cambia_la_remision_se_vuelve_a_bloquear(self):
        # Una remision pertenece a la version con la que se emitio, igual que
        # una aprobacion pertenece a la proforma que se aprobo.
        self.pistolear(self.op, 1)
        self.pistolear(self.op, 2)
        otra = self.operacion('ED260901-0005', 1)
        self.con_pedimento(otra, consecutivo='6005004')
        self.client.post('/cruces/%d/add/' % self.tarea.pk,
                         {'operation': otra.pk, 'motivo': 'x'})
        nueva = self.tarea.ordenes.order_by('-version').first()
        self.assertFalse(nueva.puede_emitir_remision)
        respuesta = self.emitir_remision()
        self.assertEqual(respuesta.status_code, 422)

    def test_sin_orden_no_hay_remision(self):
        otra_tarea = CrossingTask.objects.create(
            tenant=self.tenant, customer=self.cliente,
            fecha_de_cruce=timezone.localdate())
        respuesta = self.client.post('/cruces/%d/remision/' % otra_tarea.pk,
                                     {'transfer_empresa': 'X'})
        self.assertEqual(respuesta.status_code, 422)

    def test_el_cliente_no_emite_remisiones(self):
        self.pistolear(self.op, 1)
        self.pistolear(self.op, 2)
        self.client.force_login(self.duenio)
        respuesta = self.emitir_remision()
        self.assertEqual(respuesta.status_code, 404)


class LaPantallaDeEscaneoTests(BaseDeCarga):

    def test_se_abre_aunque_no_haya_orden(self):
        # Bodega adelanta trabajo desde el dia uno; lo que no hay es orden.
        respuesta = self.client.get('/cruces/%d/verify/' % self.tarea.pk)
        self.assertEqual(respuesta.status_code, 200)
        self.assertIsNone(respuesta.context['orden'])

    def test_ensena_el_avance_por_operacion(self):
        op = self.tarea_lista()
        self.emitir()
        self.pistolear(op, 1)
        respuesta = self.client.get('/cruces/%d/verify/' % self.tarea.pk)
        fila = respuesta.context['por_operacion'][0]
        self.assertEqual(fila['hechos'], 1)
        self.assertFalse(fila['completo'])

    def test_la_tarea_de_otra_empresa_no_existe(self):
        otro = Tenant.objects.create(name='Bodegas del Sur', type='organization',
                                     subdomain='sur')
        ajeno = Catalog.objects.create(category='CUSTOMER', name='Zeta',
                                       tenant=otro)
        ajena = CrossingTask.objects.create(tenant=otro, customer=ajeno,
                                            fecha_de_cruce=timezone.localdate())
        respuesta = self.client.get('/cruces/%d/verify/' % ajena.pk)
        self.assertEqual(respuesta.status_code, 404)


class LosDiecinueveDeVeinteTests(BaseDeCarga):
    """
    El cliente pide importar solo 19 pallets y el numero 20 se queda.

    Sin poder decirlo, la unica salida seria sacar la operacion entera de la
    tarea -- que es falso, porque 19 de esos pallets si van -- y la orden de
    carga no podria decir "3 de 5", que es como se ve en el papel.

    Y el que se queda es sencillamente el que no se pistoleo: ese dato lo tiene
    la bodega en la mano sin apuntarlo en ningun lado.
    """

    def setUp(self):
        super().setUp()
        self.op = self.operacion('ED260901-0010', 5)
        # Solo tres bultos van a pedimento; los otros dos se quedan.
        ped = Pedimento.objects.create(
            tenant=self.tenant, customer=self.cliente, orden=1,
            ped_aduana='24', ped_patente='1515', ped_consecutivo='6005000',
            estado=Pedimento.PAGADO)
        PedimentoBundle.objects.create(pedimento=ped, operation=self.op,
                                       bultos=3)

    def test_con_la_operacion_entera_la_orden_no_se_puede_emitir(self):
        CrossingTaskItem.objects.create(task=self.tarea, operation=self.op)
        faltan = [str(f) for f in LoadOrder.faltantes_para_emitir(self.tarea)]
        self.assertTrue(any('ED260901-0010' in f for f in faltan), faltan)

    def test_diciendo_que_van_tres_si(self):
        CrossingTaskItem.objects.create(task=self.tarea, operation=self.op,
                                        bultos=3)
        self.assertEqual(LoadOrder.faltantes_para_emitir(self.tarea), [])

    def test_la_orden_dice_tres_de_cinco(self):
        CrossingTaskItem.objects.create(task=self.tarea, operation=self.op,
                                        bultos=3)
        orden = self.emitir()
        renglon = orden.renglones.get()
        self.assertEqual(renglon.bultos, 3)
        self.assertEqual(orden.total_bultos, 3)

    def test_el_escaneo_espera_tres_y_no_cinco(self):
        CrossingTaskItem.objects.create(task=self.tarea, operation=self.op,
                                        bultos=3)
        orden = self.emitir()
        for n in (1, 2, 4):          # los que se subieron, sean cuales sean
            self.pistolear(self.op, n)
        cuadre = orden.cuadre()
        self.assertEqual(cuadre['esperados'], 3)
        self.assertTrue(cuadre['cuadra'])

    def test_pistolear_uno_de_mas_no_cuadra(self):
        CrossingTaskItem.objects.create(task=self.tarea, operation=self.op,
                                        bultos=3)
        orden = self.emitir()
        for n in (1, 2, 3, 4):
            self.pistolear(self.op, n)
        cuadre = orden.cuadre()
        self.assertFalse(cuadre['cuadra'])
        self.assertTrue(any('more than the order says' in s
                            for s in cuadre['sobran']), cuadre['sobran'])

    def test_un_numero_de_bulto_que_esa_operacion_no_tiene(self):
        # Mercancia que se iba a ir sin amparar, o una etiqueta equivocada.
        CrossingTaskItem.objects.create(task=self.tarea, operation=self.op,
                                        bultos=3)
        orden = self.emitir()
        self.pistolear(self.op, 9)
        self.assertIn('ED260901-0010-9', orden.cuadre()['sobran'])

    def test_no_se_pueden_meter_mas_bultos_de_los_que_hay(self):
        respuesta = self.client.post('/cruces/%d/add/' % self.tarea.pk,
                                     {'operation': self.op.pk, 'bultos': '9'})
        self.assertEqual(respuesta.status_code, 422)
        self.assertFalse(self.tarea.renglones.exists())


class LosTresPapelesTests(BaseDeCarga):
    """
    Que los tres salen y que dicen lo que tienen que decir.

    No se comprueba como se ven -- eso se mira imprimiendolos --, sino que cada
    papel lleva lo que lo hace util: la banda de la lista de preparacion, el
    aviso del QR en la orden, y la diferencia en rojo de la remision cuando
    sale con una.
    """

    def setUp(self):
        super().setUp()
        self.op = self.tarea_lista()
        self.orden = self.emitir()

    def textos(self, datos):
        """Los textos dibujados dentro del PDF, para poder buscarlos."""
        import re
        from warehouse import papeles
        original = papeles._documento

        def sin_comprimir(buffer, titulo):
            doc = original(buffer, titulo)
            doc.pageCompression = 0
            return doc

        papeles._documento = sin_comprimir
        try:
            crudo = datos()
        finally:
            papeles._documento = original
        dibujado = re.compile(rb"\((?:[^()\\]|\\.)*\)\s*(?:Tj|TJ)")
        trozos = []
        for m in dibujado.finditer(crudo):
            texto = m.group(0)
            trozos.append(texto[texto.index(b'(') + 1:texto.rindex(b')')]
                          .decode('latin-1'))
        return ' '.join(trozos)

    def test_la_lista_de_preparacion_dice_que_no_ampara_nada(self):
        from warehouse import papeles
        texto = self.textos(lambda: papeles.lista_de_preparacion(self.tarea))
        self.assertIn('NOT A SHIPPING DOCUMENT', texto)
        self.assertIn('It may have changed', texto)
        self.assertIn(self.tarea.custom_id, texto)
        # No lleva firmas ni totales aduanales.
        self.assertNotIn('TOTALS', texto)

    def test_la_orden_lleva_su_version_y_el_aviso_del_qr(self):
        from warehouse import papeles
        texto = self.textos(lambda: papeles.orden_de_carga(self.orden))
        self.assertIn(self.orden.custom_id, texto)
        self.assertIn('SCAN THIS CODE BEFORE PICKING', texto)
        self.assertIn('TOTALS', texto)
        self.assertIn(self.op.custom_id, texto)

    def test_la_orden_ensena_el_pedimento_de_cada_renglon(self):
        # Salia vacio porque leia el campo suelto de la operacion en vez del
        # reparto por bultos, que es de donde sale desde que el pedimento es
        # una entidad.
        self.assertEqual(self.orden.renglones.get().pedimento,
                         '24-1515-6005000')

    def test_la_remision_dice_quien_conto_la_carga(self):
        from warehouse import papeles
        self.pistolear(self.op, 1)
        self.pistolear(self.op, 2)
        self.client.post('/cruces/%d/remision/' % self.tarea.pk,
                         {'transfer_empresa': 'Transfers Rio Bravo',
                          'transfer_chofer': 'Ramon Escobedo'})
        rem = Remision.objects.get()
        texto = self.textos(lambda: papeles.remision(rem))
        self.assertIn(rem.custom_id, texto)
        self.assertIn('Transfers Rio Bravo', texto)
        # Los tres bloques de la cadena de custodia.
        self.assertIn('TRANSFER CROSSING THE MERCHANDISE', texto)
        self.assertIn('HAND OVER AT THE MEXICAN BORDER TO', texto)
        self.assertIn('FINAL CONSIGNEE', texto)
        # Y la frase que mas valor tiene del documento.
        self.assertIn('verified one by one with a scanner', texto)
        self.assertIn('Autotransportes del Bravo', texto)

    def test_la_remision_con_diferencia_lo_grita(self):
        # Va impreso en el papel que lleva el chofer, no escondido en una
        # pantalla: si la excepcion no se ve, deja de ser una excepcion.
        from warehouse import papeles
        self.pistolear(self.op, 1)
        self.client.post('/cruces/%d/remision/' % self.tarea.pk,
                         {'transfer_empresa': 'X',
                          'motivo': 'el cliente pidio dejar una caja'})
        rem = Remision.objects.get()
        texto = self.textos(lambda: papeles.remision(rem))
        self.assertIn('ISSUED WITH A DIFFERENCE', texto)
        self.assertIn('1 missing', texto)
        self.assertIn('dejar una caja', texto)

    def test_los_tres_se_sirven_por_su_url(self):
        respuesta = self.client.get('/cruces/%d/picking.pdf' % self.tarea.pk)
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta['Content-Type'], 'application/pdf')
        self.assertTrue(respuesta.content.startswith(b'%PDF'))

        respuesta = self.client.get('/cruces/%d/order.pdf' % self.tarea.pk)
        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta.content.startswith(b'%PDF'))

    def test_se_puede_pedir_una_version_anterior_de_la_orden(self):
        # Cuando alguien llama con una hoja vieja en la mano, lo que hace falta
        # es mirar exactamente esa hoja.
        otra = self.operacion('ED260901-0011', 1)
        self.con_pedimento(otra, consecutivo='6005009')
        self.client.post('/cruces/%d/add/' % self.tarea.pk,
                         {'operation': otra.pk, 'motivo': 'x'})
        respuesta = self.client.get('/cruces/%d/order.pdf?v=1' % self.tarea.pk)
        self.assertEqual(respuesta.status_code, 200)

    def test_sin_orden_no_hay_pdf_de_orden(self):
        otra_tarea = CrossingTask.objects.create(
            tenant=self.tenant, customer=self.cliente,
            fecha_de_cruce=timezone.localdate())
        respuesta = self.client.get('/cruces/%d/order.pdf' % otra_tarea.pk)
        self.assertEqual(respuesta.status_code, 404)

    def test_la_lista_de_preparacion_sale_desde_el_minuto_uno(self):
        # Sin orden de carga y sin pedimentos pagados: es justo para lo que
        # existe.
        vacia = CrossingTask.objects.create(
            tenant=self.tenant, customer=self.cliente,
            fecha_de_cruce=timezone.localdate())
        respuesta = self.client.get('/cruces/%d/picking.pdf' % vacia.pk)
        self.assertEqual(respuesta.status_code, 200)
