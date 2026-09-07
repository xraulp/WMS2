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
        self.assertEqual(cuadre['faltan'], ['ED260901-0001-2'])

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
        # La diferencia exacta va escrita, para que salga impresa en el papel
        # que lleva el chofer.
        self.assertIn('ED260901-0001-2', rem.diferencia)

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
