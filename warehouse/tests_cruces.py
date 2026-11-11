"""
La tarea de cruce: un camion, un dia, y la mercancia que va dentro.

La tarea aparece **despues** de los pedimentos y de la hoja de impuestos, y
hace otra cosa: juntar lo que ya esta listo y meterlo en un camion un dia
concreto. Por eso se puede armar incompleta -- con embarques sin pedimento y
con la factura sin llegar --, y por eso no bloquea nada por estarlo.

Lo que se prueba es lo que la hace util y lo que la hace segura: que el candado
del agente aduanal salta ya al meter el embarque y no al armar el camion, que
mover el dia exige decir por que, y que queda escrito quien pidio cada cosa --
que es lo unico que se pide de cada decision en todo el diseno.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from .models import (CambioDeDiaDeCruce, Catalog, CrossingTask, CrossingTaskItem,
                     PedidoDeEntrada, Camion,
                     OperationDocument, Pedimento, PedimentoBundle, Tenant,
                     UserProfile, WarehouseOperation)


class BaseDeCruces(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Dyser Group', type='organization', subdomain='dyser',
            short_name='DYSER')
        cls.jefa = User.objects.create_user('jefa', password='x')
        UserProfile.objects.create(user=cls.jefa, tenant=cls.tenant, role='admin')
        cls.cliente = Catalog.objects.create(
            category='CUSTOMER', name='Ferreteria Lopez', abbreviation='LBO',
            tenant=cls.tenant)
        # Un usuario del lado del cliente, para lo que solo el puede hacer.
        cls.duenio = User.objects.create_user('lopez', password='x')
        UserProfile.objects.create(user=cls.duenio, tenant=cls.tenant,
                                   role='customer', customer=cls.cliente)

    def setUp(self):
        self.client.force_login(self.jefa)
        self.viernes = timezone.localdate() + timedelta(days=4)

    def operacion(self, custom_id, **extra):
        return WarehouseOperation.objects.create(
            tenant=self.tenant, operation_type='ENTRY', custom_id=custom_id,
            customer=self.cliente, created_by=self.jefa, **extra)

    def con_pedimento(self, op, aduana='24', patente='1515',
                      consecutivo='6005000'):
        ped = Pedimento.objects.create(
            tenant=self.tenant, customer=self.cliente,
            orden=Pedimento.objects.filter(customer=self.cliente).count() + 1,
            ped_aduana=aduana, ped_patente=patente, ped_consecutivo=consecutivo)
        PedimentoBundle.objects.create(pedimento=ped, operation=op, bultos=1)
        return ped

    def tarea(self, **extra):
        datos = {'tenant': self.tenant, 'customer': self.cliente,
                 'fecha_de_cruce': self.viernes, 'created_by': self.jefa}
        datos.update(extra)
        return CrossingTask.objects.create(**datos)


class ComoNaceUnaTareaTests(BaseDeCruces):

    def test_el_numero_lleva_el_prefijo_TC(self):
        # `TD` ya esta ocupado: es el trasbordo, uno de los cuatro tipos de
        # operacion. Por eso la tarea de cruce es TC.
        t = self.tarea()
        self.assertTrue(t.custom_id.startswith('TC'), t.custom_id)
        self.assertTrue(t.custom_id.endswith('-0001'), t.custom_id)

    def test_dos_tareas_del_mismo_dia_no_repiten_numero(self):
        numeros = {self.tarea().custom_id for _ in range(3)}
        self.assertEqual(len(numeros), 3)

    def test_la_crea_el_cliente(self):
        self.client.force_login(self.duenio)
        self.client.post('/cruces/new/', {'fecha_de_cruce': str(self.viernes)})
        t = CrossingTask.objects.latest('id')
        self.assertEqual(t.origen, CrossingTask.LA_CREO_EL_CLIENTE)
        self.assertIn('Ferreteria Lopez', str(t.creada_por))
        # Cuando la pide el cliente no hay nada que confirmar: la instruccion
        # es suya.
        self.assertTrue(t.confirmada_por_el_cliente)
        self.assertFalse(t.espera_confirmacion)

    def test_la_crea_la_casa_a_nombre_del_cliente(self):
        # No falta quien llama por telefono, asi que crean los dos -- y la
        # tarea lo enseña siempre.
        self.client.post('/cruces/new/', {'customer': self.cliente.pk,
                                          'fecha_de_cruce': str(self.viernes)})
        t = CrossingTask.objects.latest('id')
        self.assertEqual(t.origen, CrossingTask.LA_CREO_LA_CASA)
        self.assertIn('DYSER /', str(t.creada_por))
        self.assertEqual(t.creada_por_quien, 'jefa')
        self.assertIn('Ferreteria Lopez', str(t.creada_por))
        # Y el cliente tiene que decir que si: es la instruccion telefonica
        # puesta por escrito.
        self.assertTrue(t.espera_confirmacion)

    def test_el_cliente_confirma_de_un_toque(self):
        self.client.post('/cruces/new/', {'customer': self.cliente.pk,
                                          'fecha_de_cruce': str(self.viernes)})
        t = CrossingTask.objects.latest('id')
        self.client.force_login(self.duenio)
        self.client.post('/cruces/%d/confirm/' % t.pk)
        t.refresh_from_db()
        self.assertTrue(t.confirmada_por_el_cliente)
        self.assertIsNotNone(t.confirmada_en)
        self.assertFalse(t.espera_confirmacion)

    def test_sin_dia_no_hay_tarea(self):
        antes = CrossingTask.objects.count()
        respuesta = self.client.post('/cruces/new/',
                                     {'customer': self.cliente.pk})
        self.assertEqual(respuesta.status_code, 422)
        self.assertEqual(CrossingTask.objects.count(), antes)


class LoQueVaDentroTests(BaseDeCruces):

    def test_meter_y_sacar_un_embarque(self):
        t = self.tarea()
        op = self.operacion('ED260901-0001', bundle_qty=2)
        self.client.post('/cruces/%d/add/' % t.pk, {'operation': op.pk})
        self.assertEqual(t.renglones.count(), 1)
        # Queda escrito quien lo metio y desde donde: los tres sitios desde los
        # que se puede hacer dejan la misma linea.
        renglon = t.renglones.get()
        self.assertEqual(renglon.added_by, self.jefa)
        self.assertEqual(renglon.desde, CrossingTaskItem.DESDE_LA_TAREA)

        self.client.post('/cruces/%d/remove/' % t.pk, {'renglon': renglon.pk})
        self.assertEqual(t.renglones.count(), 0)

    def test_un_embarque_no_va_en_dos_camiones(self):
        primera, segunda = self.tarea(), self.tarea()
        op = self.operacion('ED260901-0002', bundle_qty=1)
        self.client.post('/cruces/%d/add/' % primera.pk, {'operation': op.pk})
        respuesta = self.client.post('/cruces/%d/add/' % segunda.pk,
                                     {'operation': op.pk})
        self.assertEqual(respuesta.status_code, 422)
        self.assertEqual(segunda.renglones.count(), 0)

    def test_lo_que_ya_esta_en_un_cruce_no_se_ofrece(self):
        t = self.tarea()
        dentro = self.operacion('ED260901-0003', bundle_qty=1)
        fuera  = self.operacion('ED260901-0004', bundle_qty=1)
        self.client.post('/cruces/%d/add/' % t.pk, {'operation': dentro.pk})
        respuesta = self.client.get('/cruces/', {'customer': self.cliente.pk})
        libres = [op.pk for op in respuesta.context['libres']]
        self.assertIn(fuera.pk, libres)
        self.assertNotIn(dentro.pk, libres)

    def test_la_tarea_se_puede_armar_incompleta(self):
        # Con embarques sin pedimento y con la factura sin llegar. No bloquea
        # nada por estarlo: lo que hace es enseñar la cuenta atras.
        t = self.tarea()
        op = self.operacion('ED260901-0005', bundle_qty=1)
        respuesta = self.client.post('/cruces/%d/add/' % t.pk,
                                     {'operation': op.pk})
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual([o.custom_id for o in t.operaciones_sin_factura],
                         ['ED260901-0005'])

    def test_la_factura_que_llega_apaga_el_aviso(self):
        t = self.tarea()
        op = self.operacion('ED260901-0006', bundle_qty=1)
        CrossingTaskItem.objects.create(task=t, operation=op)
        OperationDocument.objects.create(
            tenant=self.tenant, operation=op,
            file=SimpleUploadedFile('factura.pdf', b'x'),
            original_name='factura.pdf',
            ranura=OperationDocument.RANURA_FACTURA_COMERCIAL)
        self.assertEqual(t.operaciones_sin_factura, [])


class ElCandadoEnLaTareaTests(BaseDeCruces):
    """
    Un DODA solo puede llevar pedimentos de un mismo agente aduanal.

    Se comprueba ya al meter el embarque y no solo al armar el camion: una
    tarea con dos patentes no podria subirse entera a ningun camion y habria
    que partirla. Avisar aqui cuesta un mensaje; avisar al armar el cruce
    cuesta descargar un camion.
    """

    def setUp(self):
        super().setUp()
        self.t = self.tarea()
        primera = self.operacion('ED260901-0001', bundle_qty=1)
        self.con_pedimento(primera, patente='1515', consecutivo='6005000')
        self.client.post('/cruces/%d/add/' % self.t.pk,
                         {'operation': primera.pk})

    def test_la_aduana_y_el_agente_se_leen_del_pedimento(self):
        # No se eligen en ningun sitio: viajan dentro del numero.
        self.assertEqual(self.t.aduana, '24')
        self.assertEqual(self.t.patente, '1515')

    def test_dos_patentes_no_suben_al_mismo_camion(self):
        otra = self.operacion('ED260901-0002', bundle_qty=1)
        self.con_pedimento(otra, patente='1781', consecutivo='6000200')
        respuesta = self.client.post('/cruces/%d/add/' % self.t.pk,
                                     {'operation': otra.pk})
        self.assertEqual(respuesta.status_code, 422)
        self.assertEqual(self.t.renglones.count(), 1)
        cuerpo = respuesta.content.decode()
        # El mensaje dice contra cual choca, no "valor invalido".
        self.assertIn('1781', cuerpo)
        self.assertIn('1515', cuerpo)

    def test_dos_aduanas_tampoco(self):
        otra = self.operacion('ED260901-0003', bundle_qty=1)
        self.con_pedimento(otra, aduana='80', patente='1515',
                           consecutivo='6005001')
        respuesta = self.client.post('/cruces/%d/add/' % self.t.pk,
                                     {'operation': otra.pk})
        self.assertEqual(respuesta.status_code, 422)
        self.assertIn('Colombia', respuesta.content.decode())

    def test_la_misma_patente_entra(self):
        otra = self.operacion('ED260901-0004', bundle_qty=1)
        self.con_pedimento(otra, patente='1515', consecutivo='6005002')
        respuesta = self.client.post('/cruces/%d/add/' % self.t.pk,
                                     {'operation': otra.pk})
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(self.t.renglones.count(), 2)

    def test_un_embarque_sin_pedimento_no_choca_con_nadie(self):
        # La tarea se arma incompleta a proposito. Bloquear un embarque por no
        # tener numero todavia seria impedir justo lo que se quiere permitir.
        suelto = self.operacion('ED260901-0005', bundle_qty=1)
        respuesta = self.client.post('/cruces/%d/add/' % self.t.pk,
                                     {'operation': suelto.pk})
        self.assertEqual(respuesta.status_code, 302)


class ElDiaDeCruceTests(BaseDeCruces):
    """
    Se puede cambiar, y eso era lo importante.

    Por tipo de cambio, porque quedo mal el transfer, porque no hay sistema en
    la aduana. El motivo sale de una lista corta, y la lista corta no es
    burocracia: en tres meses deja contestar "por que se nos mueven tanto los
    cruces" con numeros en vez de con recuerdos.
    """

    def test_mover_el_dia_deja_el_motivo_escrito(self):
        t = self.tarea()
        nuevo = self.viernes + timedelta(days=3)
        self.client.post('/cruces/%d/day/' % t.pk,
                         {'fecha_de_cruce': str(nuevo), 'motivo': 'ADUANA'})
        t.refresh_from_db()
        self.assertEqual(t.fecha_de_cruce, nuevo)
        cambio = t.cambios_de_dia.get()
        self.assertEqual(cambio.fecha_anterior, self.viernes)
        self.assertEqual(cambio.fecha_nueva, nuevo)
        self.assertEqual(cambio.motivo, CambioDeDiaDeCruce.ADUANA)
        self.assertEqual(cambio.created_by, self.jefa)

    def test_sin_motivo_no_se_mueve(self):
        t = self.tarea()
        respuesta = self.client.post(
            '/cruces/%d/day/' % t.pk,
            {'fecha_de_cruce': str(self.viernes + timedelta(days=3))})
        self.assertEqual(respuesta.status_code, 422)
        t.refresh_from_db()
        self.assertEqual(t.fecha_de_cruce, self.viernes)

    def test_un_motivo_inventado_tampoco_vale(self):
        t = self.tarea()
        respuesta = self.client.post(
            '/cruces/%d/day/' % t.pk,
            {'fecha_de_cruce': str(self.viernes + timedelta(days=1)),
             'motivo': 'PORQUE_SI'})
        self.assertEqual(respuesta.status_code, 422)

    def test_mover_al_mismo_dia_no_ensucia_el_historial(self):
        t = self.tarea()
        self.client.post('/cruces/%d/day/' % t.pk,
                         {'fecha_de_cruce': str(self.viernes),
                          'motivo': 'CLIENTE'})
        self.assertEqual(t.cambios_de_dia.count(), 0)

    def test_la_cuenta_atras(self):
        t = self.tarea()
        self.assertEqual(t.dias_para_el_cruce, 4)
        t.fecha_de_cruce = timezone.localdate() - timedelta(days=1)
        self.assertEqual(t.dias_para_el_cruce, -1)


class CambiarUnaTareaYaCargadaTests(BaseDeCruces):
    """
    A partir de que hay una orden de carga emitida, meter o sacar pide motivo.

    Puede hacerlo cualquiera, de los dos lados: quien manda en la carga es el
    cliente, y ponerle un permiso delante solo consigue que lo pida por
    telefono -- y entonces el cambio no queda escrito, que es el problema que
    se esta intentando matar. El candado no es el permiso: es que el cambio
    quede escrito y llegue al que esta cargando.
    """

    def setUp(self):
        super().setUp()
        self.t = self.tarea(estado=CrossingTask.CON_ORDEN)
        self.op = self.operacion('ED260901-0007', bundle_qty=1)

    def test_meter_sin_motivo_no_pasa(self):
        respuesta = self.client.post('/cruces/%d/add/' % self.t.pk,
                                     {'operation': self.op.pk})
        self.assertEqual(respuesta.status_code, 422)
        self.assertEqual(self.t.renglones.count(), 0)

    def test_meter_con_motivo_si(self):
        respuesta = self.client.post(
            '/cruces/%d/add/' % self.t.pk,
            {'operation': self.op.pk, 'motivo': 'lo pidio el cliente'})
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(self.t.renglones.get().motivo, 'lo pidio el cliente')

    def test_sacar_sin_motivo_no_pasa(self):
        renglon = CrossingTaskItem.objects.create(task=self.t, operation=self.op)
        respuesta = self.client.post('/cruces/%d/remove/' % self.t.pk,
                                     {'renglon': renglon.pk})
        self.assertEqual(respuesta.status_code, 422)
        self.assertTrue(CrossingTaskItem.objects.filter(pk=renglon.pk).exists())

    def test_el_cliente_tambien_puede(self):
        # Retirado el supuesto de "manager o superior": quien manda en la carga
        # es el cliente.
        self.client.force_login(self.duenio)
        respuesta = self.client.post(
            '/cruces/%d/add/' % self.t.pk,
            {'operation': self.op.pk, 'motivo': 'me falto este'})
        self.assertEqual(respuesta.status_code, 302)


class QuienVeQueTests(BaseDeCruces):

    def test_el_cliente_solo_ve_lo_suyo(self):
        otro_cliente = Catalog.objects.create(category='CUSTOMER', name='Zeta',
                                              tenant=self.tenant)
        ajena = CrossingTask.objects.create(
            tenant=self.tenant, customer=otro_cliente,
            fecha_de_cruce=self.viernes)
        self.client.force_login(self.duenio)
        respuesta = self.client.post('/cruces/%d/confirm/' % ajena.pk)
        self.assertEqual(respuesta.status_code, 404)

    def test_la_tarea_de_otra_empresa_no_existe(self):
        otro = Tenant.objects.create(name='Bodegas del Sur', type='organization',
                                     subdomain='sur')
        cliente_ajeno = Catalog.objects.create(category='CUSTOMER', name='Zeta',
                                               tenant=otro)
        ajena = CrossingTask.objects.create(tenant=otro, customer=cliente_ajeno,
                                            fecha_de_cruce=self.viernes)
        respuesta = self.client.post('/cruces/%d/add/' % ajena.pk,
                                     {'operation': 1})
        self.assertEqual(respuesta.status_code, 404)

    def test_cancelar_libera_los_embarques(self):
        # No se borra: alguien pidio ese cruce y se hablo de el por su numero,
        # asi que el numero tiene que seguir contestando.
        t = self.tarea()
        op = self.operacion('ED260901-0008', bundle_qty=1)
        self.client.post('/cruces/%d/add/' % t.pk, {'operation': op.pk})
        self.client.post('/cruces/%d/cancel/' % t.pk)
        t.refresh_from_db()
        self.assertEqual(t.estado, CrossingTask.CANCELADA)
        respuesta = self.client.get('/cruces/', {'customer': self.cliente.pk})
        self.assertIn(op.pk, [o.pk for o in respuesta.context['libres']])
        self.assertNotIn(t, respuesta.context['tareas'])


class ElImpuestoEnLaTareaTests(BaseDeCruces):
    """
    Cada embarque de la tarea lleva el impuesto aproximado de su hoja, y la
    tarea el total: el cliente revisa lo que cruza con lo que va a pagar al
    lado, sin ir a buscarlo a otra pantalla.
    """

    def con_impuesto(self, op, cifra):
        from decimal import Decimal

        from .models import RenglonDeImpuestos
        RenglonDeImpuestos.objects.create(
            tenant=self.tenant, customer=self.cliente, operation=op,
            impuesto_reportado=Decimal(cifra))

    def tarea_con(self, *ops):
        t = self.tarea()
        for op in ops:
            CrossingTaskItem.objects.create(task=t, operation=op,
                                            added_by=self.jefa)
        return t

    def pantalla(self, quien):
        self.client.force_login(quien)
        return self.client.get('/cruces/', {'customer': self.cliente.pk})

    def test_cada_embarque_lleva_su_impuesto_y_la_tarea_el_total(self):
        a = self.operacion('ED261012-0001', bundle_qty=15, po_order='IP45/26')
        b = self.operacion('ED261012-0002', bundle_qty=4, po_order='IP46/26')
        self.con_impuesto(a, '48000')
        self.con_impuesto(b, '31500')
        self.tarea_con(a, b)
        respuesta = self.pantalla(self.duenio)
        t = respuesta.context['tareas'][0]
        self.assertEqual([str(f.impuesto) for f in t.filas],
                         ['48000.00', '31500.00'])
        self.assertEqual(str(t.impuesto_total), '79500.00')
        self.assertContains(respuesta, '$48,000')
        self.assertContains(respuesta, '$79,500')

    def test_con_un_solo_embarque_no_hay_renglon_de_total(self):
        a = self.operacion('ED261012-0001', bundle_qty=15)
        self.con_impuesto(a, '48000')
        self.tarea_con(a)
        self.assertNotContains(self.pantalla(self.jefa), '<tfoot>')

    def test_el_que_no_tiene_cifra_se_dice_y_no_suma(self):
        a = self.operacion('ED261012-0001', bundle_qty=15)
        b = self.operacion('ED261012-0002', bundle_qty=4)
        self.con_impuesto(a, '48000')
        self.tarea_con(a, b)
        t = self.pantalla(self.jefa).context['tareas'][0]
        self.assertEqual(str(t.impuesto_total), '48000.00')
        self.assertEqual(t.sin_impuesto, 1)

    def test_cada_pedimento_es_un_enlace_a_su_tarjeta(self):
        a = self.operacion('ED261012-0001', bundle_qty=15)
        ped = self.con_pedimento(a)
        self.tarea_con(a)
        self.assertContains(
            self.pantalla(self.jefa),
            '/pedimentos/?customer=%d&solo=%d#ped-%d'
            % (self.cliente.pk, ped.pk, ped.pk))

    def test_los_embarques_del_mismo_pedimento_llevan_el_mismo_color(self):
        a = self.operacion('ED261012-0001', bundle_qty=15)
        b = self.operacion('ED261012-0002', bundle_qty=4)
        c = self.operacion('ED261012-0003', bundle_qty=4)
        ped = self.con_pedimento(a)
        PedimentoBundle.objects.create(pedimento=ped, operation=b, bultos=1)
        self.con_pedimento(c, consecutivo='6005001')
        self.tarea_con(a, b, c)
        t = self.pantalla(self.jefa).context['tareas'][0]
        self.assertEqual([f.color for f in t.filas], [0, 0, 1])

    def test_sin_pedimento_la_casa_ve_armarlo_y_el_cliente_un_guion(self):
        a = self.operacion('ED261012-0001', bundle_qty=15)
        self.tarea_con(a)
        self.assertContains(self.pantalla(self.jefa), 'Make the pedimento')
        self.assertNotContains(self.pantalla(self.duenio), 'Make the pedimento')

    def test_el_pedimento_tecleado_al_capturar_sale_en_la_tarea(self):
        a = self.operacion('ED261012-0001', bundle_qty=15,
                           pedimento='24-1780-6003577')
        self.tarea_con(a)
        self.assertContains(self.pantalla(self.jefa), '24-1780-6003577')


class MarcarYLuegoCrearTests(BaseDeCruces):
    """
    El cliente ve su mercancia, marca lo que quiere cruzar, elige el dia y crea
    la tarea: un solo envio, en PC y en el telefono.

    Lo que se prueba es que sale entera o no sale -- media tarea creada es
    peor que ninguna --, que queda escrito desde donde se mando, y que lo
    marcado en Operaciones que no puede ir se dice en vez de perderse.
    """

    def setUp(self):
        super().setUp()
        self.a = self.operacion('ED261001-0001', bundle_qty=5, po_order='PO-111')
        self.b = self.operacion('ED261001-0002', bundle_qty=20, po_order='PO-222')

    def armar(self, **datos):
        base = {'customer': self.cliente.pk, 'fecha_de_cruce': str(self.viernes),
                'ops': [self.a.pk, self.b.pk]}
        base.update(datos)
        return self.client.post('/cruces/mark/', base)

    def test_un_envio_crea_la_tarea_ya_llena(self):
        respuesta = self.armar(**{'bultos_%d' % self.b.pk: '19'})
        self.assertEqual(respuesta.status_code, 302)
        t = CrossingTask.objects.get()
        self.assertEqual(t.fecha_de_cruce, self.viernes)
        self.assertEqual(t.origen, CrossingTask.LA_CREO_LA_CASA)
        self.assertTrue(t.espera_confirmacion)
        self.assertEqual(t.renglones.count(), 2)
        # El caso de los 19 de 20 se teclea en el mismo envio.
        self.assertEqual(t.renglones.get(operation=self.b).bultos, 19)
        self.assertIsNone(t.renglones.get(operation=self.a).bultos)
        self.assertIn('#tarea-%d' % t.pk, respuesta['Location'])

    def test_el_cliente_la_crea_desde_su_telefono(self):
        self.client.force_login(self.duenio)
        respuesta = self.client.post('/cruces/mark/', {
            'fecha_de_cruce': str(self.viernes), 'ops': [self.a.pk],
            'desde': 'OPERACIONES'})
        self.assertEqual(respuesta.status_code, 302)
        t = CrossingTask.objects.get()
        self.assertEqual(t.origen, CrossingTask.LA_CREO_EL_CLIENTE)
        self.assertFalse(t.espera_confirmacion)
        self.assertEqual(t.renglones.get().desde,
                         CrossingTaskItem.DESDE_OPERACIONES)

    def test_un_desde_inventado_no_se_guarda(self):
        self.armar(desde='LO_QUE_SEA')
        self.assertEqual(
            set(CrossingTaskItem.objects.values_list('desde', flat=True)),
            {CrossingTaskItem.DESDE_LA_TAREA})

    def test_sin_marcar_nada_no_hay_tarea(self):
        respuesta = self.armar(ops=[])
        self.assertEqual(respuesta.status_code, 422)
        self.assertFalse(CrossingTask.objects.exists())

    def test_sin_dia_no_hay_tarea(self):
        respuesta = self.armar(fecha_de_cruce='')
        self.assertEqual(respuesta.status_code, 422)
        self.assertFalse(CrossingTask.objects.exists())

    def test_dos_patentes_marcadas_juntas_no_crean_nada(self):
        # El candado mira tambien lo que se acaba de meter en el mismo envio:
        # los dos marcados chocan entre ellos aunque la tarea naciera vacia.
        self.con_pedimento(self.a, patente='1515', consecutivo='6005000')
        self.con_pedimento(self.b, patente='1781', consecutivo='6000200')
        respuesta = self.armar()
        self.assertEqual(respuesta.status_code, 422)
        self.assertFalse(CrossingTask.objects.exists())
        self.assertFalse(CrossingTaskItem.objects.exists())
        # Y la pantalla vuelve con lo marcado intacto.
        marcados = [op.pk for op in respuesta.context['libres'] if op.marcado]
        self.assertEqual(sorted(marcados), sorted([self.a.pk, self.b.pk]))

    def test_bultos_de_mas_no_crean_nada(self):
        respuesta = self.armar(**{'bultos_%d' % self.a.pk: '6'})
        self.assertEqual(respuesta.status_code, 422)
        self.assertFalse(CrossingTask.objects.exists())

    def test_lo_que_ya_va_en_otro_cruce_no_entra(self):
        otra = self.tarea()
        CrossingTaskItem.objects.create(task=otra, operation=self.a)
        respuesta = self.armar()
        self.assertEqual(respuesta.status_code, 422)
        self.assertEqual(CrossingTask.objects.count(), 1)

    def test_meter_varios_en_una_tarea_que_ya_existe(self):
        t = self.tarea()
        respuesta = self.armar(tarea=t.pk, fecha_de_cruce='')
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(CrossingTask.objects.count(), 1)
        self.assertEqual(t.renglones.count(), 2)

    def test_con_la_orden_fuera_pide_motivo(self):
        t = self.tarea(estado=CrossingTask.CON_ORDEN)
        self.assertEqual(self.armar(tarea=t.pk).status_code, 422)
        self.assertEqual(t.renglones.count(), 0)
        self.assertEqual(self.armar(tarea=t.pk, motivo='lo pidio el cliente')
                         .status_code, 302)
        self.assertEqual(set(t.renglones.values_list('motivo', flat=True)),
                         {'lo pidio el cliente'})

    def test_el_cliente_no_mete_en_la_tarea_de_otro(self):
        ajeno = Catalog.objects.create(category='CUSTOMER', name='Otro',
                                       tenant=self.tenant)
        suya = self.tarea(customer=ajeno)
        self.client.force_login(self.duenio)
        respuesta = self.client.post('/cruces/mark/', {
            'tarea': suya.pk, 'ops': [self.a.pk]})
        self.assertEqual(respuesta.status_code, 404)
        self.assertEqual(suya.renglones.count(), 0)

    def test_lo_marcado_en_operaciones_llega_marcado(self):
        respuesta = self.client.get('/cruces/mark/', {
            'ops': str(self.b.pk), 'desde': 'OPERACIONES'})
        # La casa no eligio cliente: manda el del embarque marcado.
        self.assertEqual(respuesta.context['cliente'], self.cliente)
        marcados = [op.pk for op in respuesta.context['libres'] if op.marcado]
        self.assertEqual(marcados, [self.b.pk])
        self.assertEqual(respuesta.context['desde'], 'OPERACIONES')

    def test_lo_marcado_que_no_puede_ir_se_dice(self):
        salida = WarehouseOperation.objects.create(
            tenant=self.tenant, operation_type='EXIT', custom_id='SD261001-0001',
            customer=self.cliente, created_by=self.jefa)
        respuesta = self.client.get('/cruces/mark/', {
            'ops': '%d,%d' % (self.a.pk, salida.pk)})
        fuera = [f['op'].pk for f in respuesta.context['fuera']]
        self.assertEqual(fuera, [salida.pk])
        self.assertContains(respuesta, 'SD261001-0001')

    def test_el_cliente_no_ve_lo_marcado_de_otro(self):
        ajeno = Catalog.objects.create(category='CUSTOMER', name='Otro',
                                       tenant=self.tenant)
        de_otro = self.operacion('ED261001-0009', bundle_qty=1)
        de_otro.customer = ajeno
        de_otro.save()
        self.client.force_login(self.duenio)
        respuesta = self.client.get('/cruces/mark/', {'ops': str(de_otro.pk)})
        self.assertEqual(respuesta.context['fuera'], [])
        self.assertNotContains(respuesta, 'ED261001-0009')

    def test_la_pantalla_sirve_en_el_telefono(self):
        respuesta = self.client.get(
            '/cruces/mark/', {'customer': self.cliente.pk},
            HTTP_USER_AGENT='Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)')
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'PO-111')
        self.assertContains(respuesta, 'bottom-nav')

    def test_operaciones_lleva_a_mandar_a_cruce(self):
        # El cliente tambien: crean los dos.
        self.client.force_login(self.duenio)
        respuesta = self.client.get('/dashboard/')
        self.assertContains(respuesta, 'mandarACruce()')
        self.assertContains(respuesta, 'name="op_sel"')


class LaPantallaEnEspañolTests(BaseDeCruces):
    """La interfaz no mezcla idiomas: quien eligio español ve los cruces en español."""

    def test_los_cruces_salen_en_español(self):
        UserProfile.objects.filter(user=self.jefa).update(language='es')
        t = self.tarea(origen=CrossingTask.LA_CREO_LA_CASA)
        op = self.operacion('ED261002-0001', bundle_qty=20)
        CrossingTaskItem.objects.create(task=t, operation=op, bultos=19)
        respuesta = self.client.get('/cruces/', {'customer': self.cliente.pk})
        for frase in ('Sacar', '>de 20<', 'Lista de preparación', 'Mover el día del cruce al',
                      'Cancelar este cruce', 'Abierta', 'Creada por DYSER / Ferreteria Lopez'):
            self.assertContains(respuesta, frase)
        for frase in ('Take out', 'Picking list', 'Cancel this crossing', 'on behalf of'):
            self.assertNotContains(respuesta, frase)


class AvisoDeCrucePorConfirmarTests(BaseDeCruces):
    """
    "Esto es lo que entendimos, confirmalo".

    Cuando la casa arma un cruce en nombre del cliente, el cliente recibe un
    correo para confirmarlo. Sin el, la tarea se quedaba esperando una
    confirmacion que el cliente no sabia que le pedian.
    """

    def setUp(self):
        super().setUp()
        from django.core import mail
        mail.outbox = []
        self.cliente.contact_email = 'compras@lopez.com'
        self.cliente.language = 'en'
        self.cliente.save(update_fields=['contact_email', 'language'])
        self.a = self.operacion('ED261006-0001', bundle_qty=20, po_order='4500321')
        self.b = self.operacion('ED261006-0002', bundle_qty=3)

    def armar(self, **datos):
        base = {'customer': self.cliente.pk, 'fecha_de_cruce': str(self.viernes),
                'ops': [self.a.pk, self.b.pk], 'bultos_%d' % self.a.pk: '19'}
        base.update(datos)
        return self.client.post('/cruces/mark/', base)

    def bitacora(self):
        from .models import NotificationLog
        return NotificationLog.objects.filter(event='CROSSING_TO_CONFIRM')

    def test_la_casa_lo_arma_y_al_cliente_le_llega(self):
        from django.core import mail
        self.armar()
        self.assertEqual(len(mail.outbox), 1)
        correo = mail.outbox[0]
        t = CrossingTask.objects.get()
        self.assertEqual(correo.to, ['compras@lopez.com'])
        self.assertTrue(correo.subject.startswith('Crossing to confirm | PO 4500321'))
        self.assertIn(t.custom_id, correo.subject)
        # Lo que va en el camion, con el pedido delante y el 19 de 20.
        self.assertIn('<b>4500321</b>', correo.body)
        self.assertIn('19 of 20', correo.body)
        self.assertIn('ED261006-0002', correo.body)
        self.assertIn('/cruces/#tarea-%d' % t.pk, correo.body)
        self.assertEqual(self.bitacora().get().status, 'SENT')

    def test_en_el_idioma_del_cliente(self):
        from django.core import mail
        self.cliente.language = 'es'
        self.cliente.save(update_fields=['language'])
        self.armar()
        correo = mail.outbox[0]
        self.assertTrue(correo.subject.startswith('Cruce por confirmar'))
        self.assertIn('Confirmar el cruce', correo.body)
        self.assertNotIn('Confirm the crossing', correo.body)

    def test_si_lo_arma_el_cliente_no_hay_nada_que_confirmar(self):
        from django.core import mail
        self.client.force_login(self.duenio)
        self.armar(customer='')
        self.assertTrue(CrossingTask.objects.get().confirmada_por_el_cliente)
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(self.bitacora().exists())

    def test_añadir_a_una_que_ya_existe_no_vuelve_a_pedirlo(self):
        from django.core import mail
        self.armar(ops=[self.a.pk])
        mail.outbox = []
        t = CrossingTask.objects.get()
        self.armar(ops=[self.b.pk], tarea=t.pk)
        self.assertEqual(t.renglones.count(), 2)
        self.assertEqual(len(mail.outbox), 0)

    def test_tambien_desde_la_tarea_vacia(self):
        from django.core import mail
        self.client.post('/cruces/new/', {'customer': self.cliente.pk,
                                          'fecha_de_cruce': str(self.viernes)})
        self.assertEqual(len(mail.outbox), 1)

    def test_si_el_correo_falla_el_cruce_queda_igual(self):
        from unittest import mock
        with mock.patch('django.core.mail.EmailMessage.send',
                        side_effect=RuntimeError('sin correo')):
            respuesta = self.armar()
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(CrossingTask.objects.get().renglones.count(), 2)
        self.assertEqual(self.bitacora().get().status, 'FAILED')

    def test_la_casa_no_puede_confirmar_por_el_cliente(self):
        self.armar()
        t = CrossingTask.objects.get()
        respuesta = self.client.post('/cruces/%d/confirm/' % t.pk)
        self.assertEqual(respuesta.status_code, 404)
        t.refresh_from_db()
        self.assertTrue(t.espera_confirmacion)

    def test_sacar_y_cancelar_piden_confirmacion(self):
        self.armar(ops=[self.a.pk])
        t = CrossingTask.objects.get()
        html = self.client.get('/cruces/', {'customer': self.cliente.pk}).content.decode()
        self.assertIn('data-aviso="Take 4500321 out of this crossing?', html)
        self.assertIn('data-aviso="Cancel crossing %s?' % t.custom_id, html)
        self.assertEqual(html.count('onsubmit="return confirm(this.dataset.aviso)"'), 2)

    # El cliente confirma y la casa se entera.

    def confirmar(self, email='jefa@dyser.com', idioma=''):
        """El cruce armado por la casa y confirmado por el cliente."""
        from django.core import mail
        self.armar()
        mail.outbox = []
        self.jefa.email = email
        self.jefa.save(update_fields=['email'])
        UserProfile.objects.filter(user=self.jefa).update(language=idioma)
        self.client.force_login(self.duenio)
        t = CrossingTask.objects.get()
        return t, self.client.post('/cruces/%d/confirm/' % t.pk)

    def bitacora_de_confirmados(self):
        from .models import NotificationLog
        return NotificationLog.objects.filter(event='CROSSING_CONFIRMED')

    def test_el_cliente_confirma_y_a_la_casa_le_llega(self):
        from django.core import mail
        t, _respuesta = self.confirmar()
        self.assertEqual(len(mail.outbox), 1)
        correo = mail.outbox[0]
        self.assertEqual(correo.to, ['jefa@dyser.com'])
        self.assertTrue(correo.subject.startswith('Crossing confirmed | '))
        self.assertIn('PO 4500321', correo.subject)
        self.assertIn(t.custom_id, correo.subject)
        self.assertIn('<b>lopez', correo.body)
        self.assertIn('19 of 20', correo.body)
        self.assertIn('#tarea-%d' % t.pk, correo.body)
        self.assertEqual(self.bitacora_de_confirmados().get().status, 'SENT')

    def test_a_la_casa_le_llega_en_su_idioma(self):
        from django.core import mail
        self.confirmar(idioma='es')
        correo = mail.outbox[0]
        self.assertTrue(correo.subject.startswith('Cruce confirmado'))
        self.assertIn('Ver el cruce', correo.body)
        self.assertNotIn('See the crossing', correo.body)

    def test_confirmar_dos_veces_avisa_una(self):
        from django.core import mail
        t, _respuesta = self.confirmar()
        self.client.post('/cruces/%d/confirm/' % t.pk)
        self.assertEqual(len(mail.outbox), 1)

    def test_sin_operadores_con_correo_queda_anotado(self):
        from django.core import mail
        self.confirmar(email='')
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(self.bitacora_de_confirmados().get().status, 'SKIPPED')

    def test_si_falla_el_aviso_la_confirmacion_queda(self):
        from unittest import mock
        with mock.patch('django.core.mail.EmailMessage.send',
                        side_effect=[1, RuntimeError('sin correo')]):
            t, respuesta = self.confirmar()
        self.assertEqual(respuesta.status_code, 302)
        t.refresh_from_db()
        self.assertTrue(t.confirmada_por_el_cliente)
        self.assertEqual(self.bitacora_de_confirmados().get().status, 'FAILED')


class LoQueSeVeEnLaTareaTests(BaseDeCruces):
    """El aviso de revision, el estado de la tarea, los pedidos y el sacar."""

    def setUp(self):
        super().setUp()
        self.t = self.tarea()
        self.a = self.operacion('ED261110-0001', bundle_qty=2, po_order='4500321')
        self.b = self.operacion('ED261110-0002', bundle_qty=1, po_order='4500322')
        CrossingTaskItem.objects.create(task=self.t, operation=self.a)
        CrossingTaskItem.objects.create(task=self.t, operation=self.b)
        self.pa = self.con_pedimento(self.a, consecutivo='6005001')
        self.pb = self.con_pedimento(self.b, consecutivo='6005002')

    def pantalla(self, quien=None):
        if quien:
            self.client.force_login(quien)
        return self.client.get('/cruces/', {'customer': self.cliente.pk})

    def estados(self, a, b):
        Pedimento.objects.filter(pk=self.pa.pk).update(estado=a)
        Pedimento.objects.filter(pk=self.pb.pk).update(estado=b)

    def fase(self):
        self.t.refresh_from_db()
        return self.pantalla().context['tareas'][0].fase

    def test_los_pedidos_van_en_la_linea_de_la_tarea(self):
        self.assertContains(self.pantalla(), '4500321 - 4500322')

    def test_el_cliente_ve_el_aviso_y_la_cuenta_de_revision(self):
        self.estados(Pedimento.EN_REVISION, Pedimento.BORRADOR)
        respuesta = self.pantalla(self.duenio)
        self.assertContains(respuesta, 'class="en-revision"', count=1)
        self.assertContains(respuesta, '1 pedimento under review')

    def test_sin_revision_no_hay_aviso(self):
        respuesta = self.pantalla()
        self.assertNotContains(respuesta, 'class="en-revision"')
        self.assertNotContains(respuesta, 'class="alerta-revision"')

    def test_el_camino_del_rojo(self):
        self.assertEqual(self.fase(), CrossingTask.FASE_PEDIMENTOS)
        CrossingTask.objects.filter(pk=self.t.pk).update(estado=CrossingTask.CARGADA)
        self.assertEqual(self.fase(), CrossingTask.FASE_EN_CRUCE)
        self.client.post('/cruces/%d/red-light/' % self.t.pk)
        self.assertEqual(self.fase(), CrossingTask.FASE_ROJO)
        # En rojo no se entrega: primero la aduana tiene que liberar.
        self.assertEqual(
            self.client.post('/cruces/%d/delivered/' % self.t.pk).status_code, 422)
        self.client.post('/cruces/%d/released/' % self.t.pk)
        self.assertEqual(self.fase(), CrossingTask.FASE_LIBERADO)
        self.client.post('/cruces/%d/delivered/' % self.t.pk)
        self.assertEqual(self.fase(), CrossingTask.FASE_ENTREGADA)
        self.t.refresh_from_db()
        self.assertEqual(self.t.estado, CrossingTask.CRUZADA)
        self.assertEqual(self.t.entregada_por, self.jefa)

    def test_sin_remision_no_hay_rojo_ni_entregada(self):
        self.assertEqual(
            self.client.post('/cruces/%d/red-light/' % self.t.pk).status_code, 422)
        self.assertEqual(
            self.client.post('/cruces/%d/delivered/' % self.t.pk).status_code, 422)
        self.assertNotContains(self.pantalla(), '/red-light/')

    def test_en_cruce_la_casa_ve_los_botones_y_el_cliente_no(self):
        CrossingTask.objects.filter(pk=self.t.pk).update(estado=CrossingTask.CARGADA)
        self.assertContains(self.pantalla(), '/red-light/')
        self.assertContains(self.pantalla(), '/green-light/')
        # Entregar no sale hasta que la aduana la suelta.
        self.assertNotContains(self.pantalla(), '/delivered/')
        respuesta = self.pantalla(self.duenio)
        self.assertNotContains(respuesta, '/red-light/')
        self.assertNotContains(respuesta, '/green-light/')
        self.assertEqual(
            self.client.post('/cruces/%d/delivered/' % self.t.pk).status_code, 404)

    def test_el_estado_de_cada_pedimento_con_su_abreviatura(self):
        # DYSER es la casa, LBO el cliente.
        casos = [
            (Pedimento.BORRADOR, 0, 'Ped prep - DYSER'),
            (Pedimento.EN_REVISION, 0, 'Under review - LBO'),
            (Pedimento.CORRECCIONES, 1, 'Corr - DYSER'),
            (Pedimento.EN_REVISION, 1, 'Corr review - LBO'),
            (Pedimento.APROBADO, 1, 'Approval - LBO'),
            (Pedimento.VALIDADO, 0, 'Validated - DYSER'),
            (Pedimento.PAGADO, 0, 'Ped paid - DYSER'),
        ]
        for estado, veces, texto in casos:
            Pedimento.objects.filter(pk=self.pa.pk).update(
                estado=estado, veces_con_correcciones=veces)
            self.assertContains(self.pantalla(), texto)

    def test_sin_pedimento_armado_es_de_la_casa(self):
        c = self.operacion('ED261110-0003', bundle_qty=1)
        CrossingTaskItem.objects.create(task=self.t, operation=c)
        fila = [f for f in self.pantalla().context['tareas'][0].filas
                if f.operation_id == c.pk][0]
        self.assertEqual(fila.estados, [('Ped prep - DYSER', False)])

    def test_pedir_correcciones_cuenta(self):
        Pedimento.objects.filter(pk=self.pa.pk).update(estado=Pedimento.EN_REVISION)
        self.pa.refresh_from_db()
        self.pa.pedir_correcciones(self.duenio, 'el valor')
        self.assertEqual(self.pa.veces_con_correcciones, 1)
        self.assertEqual(self.pa.clave_corta, Pedimento.CORRECCIONES)

    def test_el_cliente_no_ve_sacar_ni_puede_sacar(self):
        respuesta = self.pantalla(self.duenio)
        self.assertNotContains(respuesta, '/remove/')
        renglon = self.t.renglones.first()
        respuesta = self.client.post('/cruces/%d/remove/' % self.t.pk,
                                     {'renglon': renglon.pk})
        self.assertEqual(respuesta.status_code, 404)
        self.assertEqual(self.t.renglones.count(), 2)

    def test_la_casa_si_puede_sacar(self):
        self.assertContains(self.pantalla(), '/remove/')


class NombreSinRegimenTests(TestCase):
    """En el encabezado de la tarea el cliente va sin "SA de CV"."""

    def test_quita_el_regimen(self):
        from .models import nombre_sin_regimen
        casos = {
            'Cliente Dos, SA de CV': 'Cliente Dos',
            'Aceros del Norte S.A. de C.V.': 'Aceros del Norte',
            'Transportes Rio S. de R.L. de C.V.': 'Transportes Rio',
            'DYSER Group LLC': 'DYSER Group',
            'Customer Test': 'Customer Test',
            'Grupo A': 'Grupo A',
            'SA de CV': 'SA de CV',
        }
        for entra, sale in casos.items():
            self.assertEqual(nombre_sin_regimen(entra), sale, entra)


class CrucesRealizadosTests(BaseDeCruces):
    """
    La entregada se queda en Cruces el dia de la entrega y al siguiente pasa a
    Cruces realizados.
    """

    def setUp(self):
        super().setUp()
        self.op = self.operacion('ED261110-0009', bundle_qty=2, po_order='IP99/25')
        self.ped = self.con_pedimento(self.op)
        self.t = self.tarea(estado=CrossingTask.CRUZADA)
        CrossingTaskItem.objects.create(task=self.t, operation=self.op)

    def entregar(self, hace_dias):
        self.t.entregada_en = timezone.now() - timedelta(days=hace_dias)
        self.t.save(update_fields=['entregada_en'])

    def ids_en_cruces(self):
        r = self.client.get('/cruces/', {'customer': self.cliente.pk})
        return [t.pk for t in r.context['tareas']]

    def test_el_dia_de_la_entrega_sigue_en_cruces(self):
        self.entregar(0)
        self.assertIn(self.t.pk, self.ids_en_cruces())
        r = self.client.get('/cruces/done/')
        self.assertEqual(r.context['tareas'], [])

    def test_al_dia_siguiente_pasa_a_realizados(self):
        self.entregar(1)
        self.assertNotIn(self.t.pk, self.ids_en_cruces())
        r = self.client.get('/cruces/done/')
        self.assertEqual([t.pk for t in r.context['tareas']], [self.t.pk])
        self.assertContains(r, self.t.custom_id)
        self.assertContains(r, 'IP99/25')
        self.assertContains(r, '&amp;solo=%d' % self.ped.pk)

    def test_la_que_no_se_entrego_no_pasa(self):
        self.assertIn(self.t.pk, self.ids_en_cruces())
        self.assertEqual(self.client.get('/cruces/done/').context['tareas'], [])

    def test_se_busca_por_pedido(self):
        self.entregar(2)
        r = self.client.get('/cruces/done/', {'q': 'IP99'})
        self.assertEqual(len(r.context['tareas']), 1)
        r = self.client.get('/cruces/done/', {'q': 'nada'})
        self.assertEqual(r.context['tareas'], [])

    def test_el_cliente_solo_ve_las_suyas(self):
        self.entregar(1)
        otro = Catalog.objects.create(category='CUSTOMER', name='Otro SA de CV',
                                      tenant=self.tenant)
        op = WarehouseOperation.objects.create(
            tenant=self.tenant, operation_type='ENTRY', custom_id='ED261110-0010',
            customer=otro)
        ajena = CrossingTask.objects.create(
            tenant=self.tenant, customer=otro, fecha_de_cruce=self.viernes,
            entregada_en=timezone.now() - timedelta(days=1))
        CrossingTaskItem.objects.create(task=ajena, operation=op)
        self.client.force_login(self.duenio)
        r = self.client.get('/cruces/done/', {'customer': otro.pk})
        self.assertEqual([t.pk for t in r.context['tareas']], [self.t.pk])

    def test_la_pestaña_esta_en_la_barra(self):
        r = self.client.get('/cruces/', {'customer': self.cliente.pk})
        self.assertContains(r, '/cruces/done/')


class ElPedimentoNombraSuTareaTests(BaseDeCruces):

    def test_el_encabezado_lleva_la_tarea(self):
        op = self.operacion('ED261110-0011', bundle_qty=1)
        ped = self.con_pedimento(op)
        t = self.tarea()
        CrossingTaskItem.objects.create(task=t, operation=op)
        r = self.client.get('/pedimentos/', {'customer': self.cliente.pk,
                                             'solo': ped.pk})
        self.assertContains(r, t.custom_id)
        self.assertContains(r, '#tarea-%d' % t.pk)

    def test_la_cancelada_no_cuenta(self):
        op = self.operacion('ED261110-0012', bundle_qty=1)
        ped = self.con_pedimento(op)
        t = self.tarea(estado=CrossingTask.CANCELADA)
        CrossingTaskItem.objects.create(task=t, operation=op)
        self.assertEqual(list(ped.tareas_de_cruce()), [])


class LosCrucesDeTodosLosClientesTests(BaseDeCruces):
    """
    La casa revisa cruces de varios clientes a la vez. Sin cliente elegido,
    la pantalla de cruces enseña las tareas de todos, una por linea, y no un
    "elige un cliente".
    """

    def setUp(self):
        super().setUp()
        self.otro = Catalog.objects.create(
            category='CUSTOMER', name='Textiles del Norte', abbreviation='TXN',
            tenant=self.tenant)

    def tarea_de(self, cliente, custom_id, **extra):
        op = WarehouseOperation.objects.create(
            tenant=self.tenant, operation_type='ENTRY', custom_id=custom_id,
            customer=cliente, created_by=self.jefa, bundle_qty=5, po_order='PO-' + custom_id)
        t = CrossingTask.objects.create(tenant=self.tenant, customer=cliente,
                                        fecha_de_cruce=self.viernes,
                                        created_by=self.jefa, **extra)
        CrossingTaskItem.objects.create(task=t, operation=op)
        return t, op

    def test_sin_cliente_salen_las_tareas_de_todos(self):
        a, _ = self.tarea_de(self.cliente, 'ED261111-0001')
        b, _ = self.tarea_de(self.otro, 'ED261111-0002')
        r = self.client.get('/cruces/')
        self.assertEqual({t.pk for t in r.context['todas']}, {a.pk, b.pk})
        self.assertContains(r, 'LBO')
        self.assertContains(r, 'TXN')
        self.assertContains(r, '?customer=%d#tarea-%d' % (self.otro.pk, b.pk))
        self.assertContains(r, 'PO-ED261111-0002')

    def test_la_cancelada_no_sale(self):
        c, _ = self.tarea_de(self.otro, 'ED261111-0003',
                             estado=CrossingTask.CANCELADA)
        r = self.client.get('/cruces/')
        self.assertNotIn(c.pk, [t.pk for t in r.context['todas']])

    def test_me_toca_cuando_el_paso_es_de_la_casa(self):
        # Sin pedimento: hay que elaborarlo, le toca a la casa.
        sin_ped, _ = self.tarea_de(self.cliente, 'ED261111-0004')
        # Pedimento en revision: le toca al cliente.
        en_rev, op = self.tarea_de(self.otro, 'ED261111-0005')
        ped = Pedimento.objects.create(
            tenant=self.tenant, customer=self.otro, orden=1,
            ped_aduana='24', ped_patente='1515', ped_consecutivo='6005001',
            estado=Pedimento.EN_REVISION)
        PedimentoBundle.objects.create(pedimento=ped, operation=op, bultos=1)
        r = self.client.get('/cruces/')
        me_toca = {t.pk: t.me_toca for t in r.context['todas']}
        self.assertTrue(me_toca[sin_ped.pk])
        self.assertFalse(me_toca[en_rev.pk])
        self.assertEqual(r.context['n_me_toca'], 1)

    def test_en_cruce_le_toca_a_la_casa(self):
        t, _ = self.tarea_de(self.cliente, 'ED261111-0006',
                             estado=CrossingTask.CARGADA)
        r = self.client.get('/cruces/')
        self.assertTrue([x for x in r.context['todas'] if x.pk == t.pk][0].me_toca)

    def test_el_cliente_no_ve_las_tareas_de_otros(self):
        self.tarea_de(self.otro, 'ED261111-0007')
        self.client.force_login(self.duenio)
        r = self.client.get('/cruces/')
        self.assertNotIn('todas', r.context)
        self.assertNotContains(r, 'TXN')

    def test_el_color_sale_de_la_abreviatura(self):
        from .templatetags.wms_filters import tono_de_cliente
        gemelo = Catalog(category='CUSTOMER', name='Otro nombre', abbreviation='lbo')
        self.assertEqual(tono_de_cliente(self.cliente), tono_de_cliente(gemelo))
        self.assertTrue(0 <= tono_de_cliente(self.otro) < 360)



class ElSemaforoDeLaAduanaTests(BaseDeCruces):
    """
    Verde, o rojo y despues liberado; la inspeccion de SENASICA si algun
    pedido la lleva; y la entrega de la ultima carga. Lo marca la casa y el
    cliente lo ve.
    """

    def setUp(self):
        super().setUp()
        self.op = self.operacion('ED261111-0101', bundle_qty=3)
        self.t = self.tarea(estado=CrossingTask.CARGADA)
        CrossingTaskItem.objects.create(task=self.t, operation=self.op)

    def fase(self):
        self.t.refresh_from_db()
        return self.t.fase

    def pantalla(self, quien=None):
        if quien:
            self.client.force_login(quien)
        return self.client.get('/cruces/', {'customer': self.cliente.pk})

    def test_verde_y_entregada(self):
        self.client.post('/cruces/%d/green-light/' % self.t.pk)
        self.assertEqual(self.fase(), CrossingTask.FASE_VERDE)
        self.assertEqual(self.t.verde_por, self.jefa)
        # Despues del verde ya no hay rojo.
        self.assertEqual(
            self.client.post('/cruces/%d/red-light/' % self.t.pk).status_code, 422)
        self.assertContains(self.pantalla(), '/delivered/')
        self.client.post('/cruces/%d/delivered/' % self.t.pk)
        self.assertEqual(self.fase(), CrossingTask.FASE_ENTREGADA)

    def test_liberado_solo_despues_del_rojo(self):
        self.assertEqual(
            self.client.post('/cruces/%d/released/' % self.t.pk).status_code, 422)
        self.assertNotContains(self.pantalla(), '/released/')
        self.client.post('/cruces/%d/red-light/' % self.t.pk)
        self.assertContains(self.pantalla(), '/released/')

    def test_el_cliente_ve_el_verde_pero_no_lo_marca(self):
        self.client.post('/cruces/%d/green-light/' % self.t.pk)
        respuesta = self.pantalla(self.duenio)
        self.assertContains(respuesta, 'Customs green light')
        self.assertEqual(
            self.client.post('/cruces/%d/delivered/' % self.t.pk).status_code, 404)

    def test_sin_inspeccion_no_sale_el_boton(self):
        self.client.post('/cruces/%d/green-light/' % self.t.pk)
        self.assertNotContains(self.pantalla(), '/inspection/')
        self.assertEqual(
            self.client.post('/cruces/%d/inspection/' % self.t.pk).status_code, 422)

    def test_con_inspeccion_despues_del_verde(self):
        WarehouseOperation.objects.filter(pk=self.op.pk).update(lleva_inspeccion=True)
        # Antes del verde todavia no.
        self.assertNotContains(self.pantalla(), '/inspection/')
        self.client.post('/cruces/%d/green-light/' % self.t.pk)
        self.assertContains(self.pantalla(), '/inspection/')
        self.client.post('/cruces/%d/inspection/' % self.t.pk)
        self.assertEqual(self.fase(), CrossingTask.FASE_INSPECCION)
        self.client.post('/cruces/%d/delivered/' % self.t.pk)
        self.assertEqual(self.fase(), CrossingTask.FASE_ENTREGADA)

    def test_con_inspeccion_despues_de_liberado(self):
        WarehouseOperation.objects.filter(pk=self.op.pk).update(lleva_inspeccion=True)
        self.client.post('/cruces/%d/red-light/' % self.t.pk)
        self.client.post('/cruces/%d/released/' % self.t.pk)
        self.client.post('/cruces/%d/inspection/' % self.t.pk)
        self.assertEqual(self.fase(), CrossingTask.FASE_INSPECCION)


class LaConfirmacionNoDetieneElCruceTests(BaseDeCruces):
    """
    La tarea que creo la casa por telefono se da por confirmada cuando el
    cliente aprueba la revision de su ultimo pedimento, aunque nunca haya
    tocado "Si, es eso".
    """

    def test_aprobar_el_ultimo_pedimento_la_confirma(self):
        op1 = self.operacion('ED261111-0201', bundle_qty=1)
        op2 = self.operacion('ED261111-0202', bundle_qty=1)
        p1 = self.con_pedimento(op1, consecutivo='6005201')
        p2 = self.con_pedimento(op2, consecutivo='6005202')
        t = self.tarea(origen=CrossingTask.LA_CREO_LA_CASA)
        CrossingTaskItem.objects.create(task=t, operation=op1)
        CrossingTaskItem.objects.create(task=t, operation=op2)
        Pedimento.objects.filter(pk__in=[p1.pk, p2.pk]).update(
            estado=Pedimento.EN_REVISION)
        p1.refresh_from_db()
        p2.refresh_from_db()

        self.assertTrue(p1.aprobar(self.duenio))
        t.refresh_from_db()
        self.assertTrue(t.espera_confirmacion)

        self.assertTrue(p2.aprobar(self.duenio))
        t.refresh_from_db()
        self.assertFalse(t.espera_confirmacion)
        self.assertTrue(t.confirmada_por_el_cliente)
        self.assertIsNotNone(t.confirmada_en)



class MoverOQuitarUnPedimentoTests(BaseDeCruces):
    """
    La tarea es un camion; sus renglones son pedimentos con sus pedidos. Un
    pedimento se mueve a otro dia (nace una tarea nueva con el) o se quita del
    cruce (sus pedidos quedan libres). Si era el ultimo, la tarea desaparece.
    """

    def setUp(self):
        super().setUp()
        self.a1 = self.operacion('ED261111-0401', bundle_qty=2, po_order='PO-A1')
        self.a2 = self.operacion('ED261111-0402', bundle_qty=1, po_order='PO-A2')
        self.b1 = self.operacion('ED261111-0403', bundle_qty=3, po_order='PO-B1')
        self.ped_a = self.con_pedimento(self.a1, consecutivo='6005401')
        PedimentoBundle.objects.create(pedimento=self.ped_a, operation=self.a2, bultos=1)
        self.ped_b = self.con_pedimento(self.b1, consecutivo='6005402')
        self.t = self.tarea()
        for op in (self.a1, self.a2, self.b1):
            CrossingTaskItem.objects.create(task=self.t, operation=op)
        self.lunes = self.viernes + timedelta(days=3)

    def mover(self, ped, **extra):
        datos = {'pedimento': ped.pk, 'fecha_de_cruce': self.lunes.isoformat(),
                 'motivo': CambioDeDiaDeCruce.CLIENTE}
        datos.update(extra)
        return self.client.post('/cruces/%d/pedimento/move/' % self.t.pk, datos)

    def quitar(self, ped, **extra):
        datos = {'pedimento': ped.pk}
        datos.update(extra)
        return self.client.post('/cruces/%d/pedimento/remove/' % self.t.pk, datos)

    def test_mover_crea_una_tarea_nueva_con_el_pedimento_y_sus_pedidos(self):
        self.assertEqual(self.mover(self.ped_a).status_code, 302)
        nueva = CrossingTask.objects.exclude(pk=self.t.pk).get()
        self.assertEqual(nueva.fecha_de_cruce, self.lunes)
        self.assertEqual({r.operation_id for r in nueva.renglones.all()},
                         {self.a1.pk, self.a2.pk})
        self.assertEqual([r.operation_id for r in self.t.renglones.all()], [self.b1.pk])
        cambio = nueva.cambios_de_dia.get()
        self.assertEqual(cambio.fecha_anterior, self.viernes)
        self.assertIn(self.t.custom_id, cambio.detalle)

    def test_mover_el_ultimo_hace_desaparecer_la_tarea(self):
        self.mover(self.ped_b)
        self.mover(self.ped_a)
        self.t.refresh_from_db()
        self.assertEqual(self.t.estado, CrossingTask.CANCELADA)
        r = self.client.get('/cruces/')
        self.assertNotIn(self.t.pk, [x.pk for x in r.context['todas']])

    def test_mover_sin_motivo_no_se_puede(self):
        self.assertEqual(self.mover(self.ped_a, motivo='').status_code, 422)
        self.assertEqual(CrossingTask.objects.count(), 1)

    def test_quitar_deja_libres_sus_pedidos(self):
        self.assertEqual(self.quitar(self.ped_a).status_code, 302)
        self.assertEqual([r.operation_id for r in self.t.renglones.all()], [self.b1.pk])
        self.assertFalse(CrossingTaskItem.objects.filter(operation=self.a1).exists())

    def test_quitar_el_ultimo_hace_desaparecer_la_tarea(self):
        self.quitar(self.ped_a)
        self.quitar(self.ped_b)
        self.t.refresh_from_db()
        self.assertEqual(self.t.estado, CrossingTask.CANCELADA)

    def test_con_orden_quitar_pide_motivo_y_sube_la_version(self):
        Pedimento.objects.update(estado=Pedimento.PAGADO)
        PedimentoBundle.objects.filter(operation=self.a1).update(bultos=2)
        PedimentoBundle.objects.filter(operation=self.b1).update(bultos=3)
        self.client.post('/cruces/%d/order/' % self.t.pk)
        self.t.refresh_from_db()
        self.assertEqual(self.t.estado, CrossingTask.CON_ORDEN)
        self.assertEqual(self.quitar(self.ped_a).status_code, 422)
        self.assertEqual(self.quitar(self.ped_a, motivo='El cliente lo pide').status_code, 302)
        self.assertEqual(self.t.ordenes.order_by('-version').first().version, 2)

    def test_ya_cargada_no_se_mueve(self):
        CrossingTask.objects.filter(pk=self.t.pk).update(estado=CrossingTask.CARGADA)
        self.assertEqual(self.mover(self.ped_a).status_code, 422)
        self.assertEqual(self.quitar(self.ped_a).status_code, 422)

    def test_el_cliente_no_puede(self):
        self.client.force_login(self.duenio)
        self.assertEqual(self.mover(self.ped_a).status_code, 404)
        self.assertEqual(self.quitar(self.ped_a).status_code, 404)

    def test_un_pedimento_que_no_esta_en_la_tarea(self):
        otro = self.operacion('ED261111-0499')
        ajeno = self.con_pedimento(otro, consecutivo='6005499')
        self.assertEqual(self.quitar(ajeno).status_code, 404)

    def test_sacar_el_ultimo_pedido_hace_desaparecer_la_tarea(self):
        t = self.tarea()
        op = self.operacion('ED261111-0498')
        r = CrossingTaskItem.objects.create(task=t, operation=op)
        self.client.post('/cruces/%d/remove/' % t.pk, {'renglon': r.pk})
        t.refresh_from_db()
        self.assertEqual(t.estado, CrossingTask.CANCELADA)

    def test_los_botones_salen_en_el_renglon_del_pedimento(self):
        r = self.client.get('/cruces/', {'customer': self.cliente.pk})
        self.assertContains(r, '/pedimento/move/')
        self.assertContains(r, '/pedimento/remove/')
        self.client.force_login(self.duenio)
        r = self.client.get('/cruces/')
        self.assertNotContains(r, '/pedimento/move/')


class SacarUnPedidoDelPedimentoTests(BaseDeCruces):
    """
    Raro, pero pasa incluso con el pedimento pagado: un pedido no cruza. Lo
    que se queda en bodega pasa al -A de la entrada (sub2), libre para otro
    cruce; la original (sub1) se queda con lo que se fue. El pedimento puede
    llevar su numero de rectificacion.
    """

    def setUp(self):
        super().setUp()
        self.op = self.operacion('ED261111-0501', bundle_qty=10,
                                 po_order='4500123, 4500124',
                                 weight_lbs=Decimal('1000'), weight_kgs=Decimal('453.59'))
        self.ped = self.con_pedimento(self.op, consecutivo='6005501')
        PedimentoBundle.objects.filter(operation=self.op).update(bultos=10)
        self.ped.estado = Pedimento.PAGADO
        self.ped.save()
        self.t = self.tarea()
        self.renglon = CrossingTaskItem.objects.create(task=self.t, operation=self.op)

    def sacar(self, **extra):
        datos = {'renglon': self.renglon.pk, 'pedido': '4500124', 'bultos': '4',
                 'peso_lbs': '300', 'motivo': 'El cliente lo deja para despues'}
        datos.update(extra)
        return self.client.post('/cruces/%d/order-out/' % self.t.pk, datos)

    def test_nace_el_menos_a_con_lo_que_se_queda(self):
        r = self.sacar(rectificacion='24 1515 6009999')
        self.assertEqual(r.status_code, 302)
        a = WarehouseOperation.objects.get(custom_id='ED261111-0501-A')
        self.assertEqual(a.bundle_qty, 4)
        self.assertEqual(a.weight_lbs, Decimal('300'))
        self.assertEqual(a.po_order, '4500124')
        self.assertEqual(a.subdivision, WarehouseOperation.SUB2)
        self.assertEqual(a.dividida_de, self.op)
        self.assertEqual(a.pedimento, '')
        self.assertFalse(a.renglones_de_pedimento.exists())
        self.assertFalse(a.cruces.exists())

        self.op.refresh_from_db()
        self.assertEqual(self.op.bundle_qty, 6)
        self.assertEqual(self.op.weight_lbs, Decimal('700'))
        self.assertEqual(self.op.po_order, '4500123')
        self.assertEqual(self.op.subdivision, WarehouseOperation.SUB1)
        self.assertEqual(self.op.renglones_de_pedimento.get().bultos, 6)

        self.ped.refresh_from_db()
        self.assertEqual(self.ped.rectificacion, '24 1515 6009999')
        self.assertEqual(self.ped.estado, Pedimento.PAGADO)

    def test_el_segundo_es_menos_b(self):
        self.sacar()
        self.sacar(pedido='4500123', bultos='2', peso_lbs='')
        self.assertTrue(WarehouseOperation.objects.filter(custom_id='ED261111-0501-B').exists())

    def test_el_menos_a_no_ocupa_numero_nuevo(self):
        self.sacar()
        siguiente = WarehouseOperation.objects.create(
            tenant=self.tenant, operation_type='ENTRY', customer=self.cliente,
            date=self.op.date, created_by=self.jefa)
        self.assertTrue(siguiente.custom_id.endswith('-0002'), siguiente.custom_id)

    def test_se_puede_pistolear_el_menos_a(self):
        from . import bultos
        self.assertEqual(bultos.leer('ED261111-0501-A-3'), ('ED261111-0501-A', 3))
        self.assertEqual(bultos.leer('ED261111-0501-3'), ('ED261111-0501', 3))

    def test_sin_motivo_o_sin_pedido_no(self):
        self.assertEqual(self.sacar(motivo='').status_code, 422)
        self.assertEqual(self.sacar(pedido='').status_code, 422)
        self.assertFalse(WarehouseOperation.objects.filter(subdivision='SUB2').exists())

    def test_no_se_quedan_mas_de_los_que_ampara_el_pedimento(self):
        PedimentoBundle.objects.filter(operation=self.op).update(bultos=3)
        self.assertEqual(self.sacar(bultos='3').status_code, 422)
        self.assertEqual(self.sacar(bultos='2').status_code, 302)

    def test_no_se_puede_quedar_todo(self):
        self.assertEqual(self.sacar(bultos='10').status_code, 422)
        self.assertEqual(self.sacar(bultos='0').status_code, 422)

    def test_cargada_ya_no(self):
        CrossingTask.objects.filter(pk=self.t.pk).update(estado=CrossingTask.CARGADA)
        self.assertEqual(self.sacar().status_code, 422)

    def test_el_cliente_no_puede(self):
        self.client.force_login(self.duenio)
        self.assertEqual(self.sacar().status_code, 404)

    def test_la_pantalla_avisa_de_las_etiquetas(self):
        r = self.sacar()
        a = WarehouseOperation.objects.get(custom_id='ED261111-0501-A')
        pantalla = self.client.get(r['Location'])
        self.assertContains(pantalla, '/operations/%d/label/' % a.pk)
        self.assertContains(pantalla, 'sub1')

    def test_el_boton_sale_solo_en_pedidos_con_pedimento(self):
        r = self.client.get('/cruces/', {'customer': self.cliente.pk})
        self.assertContains(r, '/order-out/')


class LosPedidosDeUnaEntradaTests(BaseDeCruces):
    """
    Una entrada puede traer varios pedidos, cada uno con sus bultos y su peso.
    El PO de la entrada se compone de ellos, y al sacar uno de su pedimento se
    elige de la lista y se va con su renglon al -A.
    """

    def capturar(self, **extra):
        cat = lambda c, n: Catalog.objects.get_or_create(
            category=c, name=n, tenant=self.tenant)[0].pk
        datos = {'date': '2026-11-11', 'operation_type': 'ENTRY',
                 'customer_id': self.cliente.pk, 'bundle_qty': '10',
                 'shipper_id': cat('SHIPPER', 'ABC LLC'),
                 'carrier_id': cat('CARRIER', 'XPO'),
                 'bundle_type_id': cat('BUNDLE_TYPE', 'Pallet'),
                 'description': 'Cajas',
                 'weight_lbs': '1000', 'po_order': 'lo que sea',
                 'pedidos_present': '1'}
        datos.update(extra)
        return self.client.post('/operations/create/', datos)

    def test_la_captura_guarda_la_lista_y_compone_el_po(self):
        r = self.capturar(pedido_numero=['4500123', '4500124', ''],
                          pedido_bultos=['6', '4', ''],
                          pedido_peso=['700', '300', ''])
        self.assertEqual(r.status_code, 200)
        op = WarehouseOperation.objects.latest('id')
        self.assertEqual([(x.numero, x.bultos, x.peso_lbs) for x in op.pedidos.all()],
                         [('4500123', 6, Decimal('700')), ('4500124', 4, Decimal('300'))])
        self.assertEqual(op.po_order, '4500123, 4500124')

    def test_sin_lista_el_po_se_queda_como_se_tecleo(self):
        self.capturar(pedido_numero=[''], pedido_bultos=[''], pedido_peso=[''])
        op = WarehouseOperation.objects.latest('id')
        self.assertFalse(op.pedidos.exists())
        self.assertEqual(op.po_order, 'lo que sea')

    def test_la_casilla_sale_en_la_captura_y_en_la_edicion(self):
        op = self.operacion('ED261111-0601', po_order='X')
        PedidoDeEntrada.objects.create(operation=op, numero='4500999', bultos=2)
        r = self.client.get('/operations/%d/edit/' % op.pk)
        self.assertContains(r, 'value="4500999"')
        self.assertContains(r, 'name="pedidos_present"')

    def test_sacar_un_pedido_de_la_lista(self):
        op = self.operacion('ED261111-0602', bundle_qty=10, po_order='A1, A2',
                            weight_lbs=Decimal('1000'))
        a1 = PedidoDeEntrada.objects.create(operation=op, numero='A1', bultos=6,
                                            peso_lbs=Decimal('700'), orden=0)
        a2 = PedidoDeEntrada.objects.create(operation=op, numero='A2', bultos=4,
                                            peso_lbs=Decimal('300'), orden=1)
        ped = self.con_pedimento(op, consecutivo='6005601')
        PedimentoBundle.objects.filter(operation=op).update(bultos=10)
        t = self.tarea()
        renglon = CrossingTaskItem.objects.create(task=t, operation=op)
        pantalla = self.client.get('/cruces/', {'customer': self.cliente.pk})
        self.assertContains(pantalla, 'name="pedido_id"')
        self.assertContains(pantalla, 'data-bultos="4"')

        r = self.client.post('/cruces/%d/order-out/' % t.pk, {
            'renglon': renglon.pk, 'pedido_id': a2.pk, 'bultos': '4',
            'peso_lbs': '300', 'motivo': 'no cruza'})
        self.assertEqual(r.status_code, 302)
        menos_a = WarehouseOperation.objects.get(custom_id='ED261111-0602-A')
        self.assertEqual(menos_a.po_order, 'A2')
        self.assertEqual(list(menos_a.pedidos.all()), [a2])
        op.refresh_from_db()
        self.assertEqual(op.po_order, 'A1')
        self.assertEqual(list(op.pedidos.all()), [a1])


class LosBultosDeLosPedidosCuadranTests(BaseDeCruces):

    def test_avisa_cuando_no_suman_los_de_la_entrada(self):
        op = self.operacion('ED261111-0701', bundle_qty=10)
        PedidoDeEntrada.objects.create(operation=op, numero='P1', bultos=6)
        PedidoDeEntrada.objects.create(operation=op, numero='P2', bultos=3)
        self.assertEqual(op.pedidos_no_cuadran, (9, 10))
        r = self.client.get('/operations/%d/edit/' % op.pk)
        self.assertContains(r, 'The orders add up to 9 bundles, but the entry has 10.')

    def test_cuando_cuadra_no_avisa(self):
        op = self.operacion('ED261111-0702', bundle_qty=10)
        PedidoDeEntrada.objects.create(operation=op, numero='P1', bultos=6)
        PedidoDeEntrada.objects.create(operation=op, numero='P2', bultos=4)
        self.assertIsNone(op.pedidos_no_cuadran)

    def test_sin_bultos_tecleados_no_dice_nada(self):
        op = self.operacion('ED261111-0703', bundle_qty=10)
        PedidoDeEntrada.objects.create(operation=op, numero='P1')
        self.assertIsNone(op.pedidos_no_cuadran)


class ElCamionCompartidoTests(BaseDeCruces):
    """
    Tareas de clientes distintos en la misma caja el mismo dia. Comparten el
    transfer y la suerte en la aduana; cada una conserva lo suyo. El cliente
    nunca se entera.
    """

    def setUp(self):
        super().setUp()
        self.otro = Catalog.objects.create(
            category='CUSTOMER', name='Textiles del Norte', abbreviation='TXN',
            tenant=self.tenant)
        self.u_otro = User.objects.create_user('txn', password='x')
        UserProfile.objects.create(user=self.u_otro, tenant=self.tenant,
                                   role='customer', customer=self.otro)
        self.a = self.tarea_con('ED261111-0801', self.cliente)
        self.b = self.tarea_con('ED261111-0802', self.otro)

    def tarea_con(self, custom_id, cliente, **extra):
        op = WarehouseOperation.objects.create(
            tenant=self.tenant, operation_type='ENTRY', custom_id=custom_id,
            customer=cliente, created_by=self.jefa, bundle_qty=2)
        t = CrossingTask.objects.create(tenant=self.tenant, customer=cliente,
                                        fecha_de_cruce=self.viernes,
                                        created_by=self.jefa, **extra)
        CrossingTaskItem.objects.create(task=t, operation=op)
        return t

    def juntar(self, t, otra):
        return self.client.post('/cruces/%d/truck/join/' % t.pk, {'otra': otra.pk})

    def test_juntar_dos_tareas_crea_el_camion(self):
        self.assertEqual(self.juntar(self.a, self.b).status_code, 302)
        self.a.refresh_from_db(); self.b.refresh_from_db()
        self.assertIsNotNone(self.a.camion)
        self.assertEqual(self.a.camion, self.b.camion)
        self.assertTrue(self.a.camion.custom_id.startswith('CM'))

    def test_una_tercera_entra_al_mismo_camion(self):
        self.juntar(self.a, self.b)
        c = self.tarea_con('ED261111-0803', self.cliente)
        self.juntar(c, self.b)
        c.refresh_from_db(); self.b.refresh_from_db()
        self.assertEqual(c.camion, self.b.camion)
        self.assertEqual(Camion.objects.count(), 1)

    def test_solo_tareas_del_mismo_dia(self):
        lunes = self.tarea_con('ED261111-0804', self.otro)
        CrossingTask.objects.filter(pk=lunes.pk).update(
            fecha_de_cruce=self.viernes + timedelta(days=3))
        self.assertEqual(self.juntar(self.a, lunes).status_code, 404)

    def test_mover_el_dia_la_saca_del_camion_y_las_demas_siguen(self):
        self.juntar(self.a, self.b)
        c = self.tarea_con('ED261111-0805', self.otro)
        self.juntar(c, self.a)
        camion = Camion.objects.get()
        self.client.post('/cruces/%d/day/' % self.a.pk, {
            'fecha_de_cruce': (self.viernes + timedelta(days=3)).isoformat(),
            'motivo': CambioDeDiaDeCruce.CLIENTE})
        self.a.refresh_from_db()
        self.assertIsNone(self.a.camion)
        self.assertEqual(set(camion.tareas_vivas()), {self.b, c})

    def test_el_rojo_de_una_es_rojo_para_todo_el_camion_y_se_liberan_juntas(self):
        self.juntar(self.a, self.b)
        CrossingTask.objects.filter(pk__in=[self.a.pk, self.b.pk]).update(
            estado=CrossingTask.CARGADA)
        # Una ya habia salido verde: el rojo del camion la alcanza igual.
        self.client.post('/cruces/%d/green-light/' % self.b.pk)
        self.client.post('/cruces/%d/red-light/' % self.a.pk)
        self.a.refresh_from_db(); self.b.refresh_from_db()
        self.assertEqual(self.a.fase, CrossingTask.FASE_ROJO)
        self.assertEqual(self.b.fase, CrossingTask.FASE_ROJO)
        self.client.post('/cruces/%d/released/' % self.b.pk)
        self.a.refresh_from_db(); self.b.refresh_from_db()
        self.assertEqual(self.a.fase, CrossingTask.FASE_LIBERADO)
        self.assertEqual(self.b.fase, CrossingTask.FASE_LIBERADO)

    def test_la_casa_ve_el_camion_en_la_tarea_y_en_la_lista(self):
        self.juntar(self.a, self.b)
        camion = Camion.objects.get()
        r = self.client.get('/cruces/', {'customer': self.cliente.pk})
        self.assertContains(r, camion.custom_id)
        self.assertContains(r, 'TXN')
        r = self.client.get('/cruces/')
        self.assertContains(r, 'camion-bloque')
        self.assertContains(r, camion.custom_id)

    def test_el_cliente_no_se_entera(self):
        self.juntar(self.a, self.b)
        camion = Camion.objects.get()
        for usuario in (self.duenio, self.u_otro):
            self.client.force_login(usuario)
            r = self.client.get('/cruces/')
            self.assertNotContains(r, camion.custom_id)
            self.assertNotContains(r, '/truck/')
            self.assertNotContains(r, 'Shared truck')
        self.client.force_login(self.duenio)
        self.assertNotContains(self.client.get('/cruces/'), 'TXN')
        self.assertEqual(self.juntar(self.a, self.b).status_code, 404)

    def test_salir_del_camion(self):
        self.juntar(self.a, self.b)
        self.client.post('/cruces/%d/truck/leave/' % self.a.pk)
        self.a.refresh_from_db()
        self.assertIsNone(self.a.camion)

    def test_cargada_ya_no_cambia_de_camion(self):
        CrossingTask.objects.filter(pk=self.a.pk).update(estado=CrossingTask.CARGADA)
        self.assertEqual(self.juntar(self.a, self.b).status_code, 422)


class LaHojaDeImpuestosSeParteConElMenosATests(BaseDeCruces):
    """
    Al sacar un pedido, la hoja de impuestos se parte igual que la entrada: el
    -A nace con su renglon y su valor, y a la original se le resta, asi que su
    estimado se recalcula con lo que si cruza.
    """

    def setUp(self):
        super().setUp()
        from .models import RenglonDeImpuestos
        self.op = self.operacion('ED261111-0901', bundle_qty=10, po_order='P1, P2')
        self.hoja = RenglonDeImpuestos.objects.create(
            tenant=self.tenant, customer=self.cliente, operation=self.op,
            valor_expresion='500+700', valor_mercancia=Decimal('1200'),
            fletes=Decimal('100'), tasa_igi=Decimal('0.05'))
        self.con_pedimento(self.op, consecutivo='6005901')
        PedimentoBundle.objects.filter(operation=self.op).update(bultos=10)
        self.t = self.tarea()
        self.renglon = CrossingTaskItem.objects.create(task=self.t, operation=self.op)

    def sacar(self, **extra):
        datos = {'renglon': self.renglon.pk, 'pedido': 'P2', 'bultos': '4',
                 'motivo': 'no cruza'}
        datos.update(extra)
        return self.client.post('/cruces/%d/order-out/' % self.t.pk, datos)

    def test_con_valor_tecleado(self):
        self.assertEqual(self.sacar(valor_usd='300').status_code, 302)
        a = WarehouseOperation.objects.get(custom_id='ED261111-0901-A')
        self.assertEqual(a.renglon_de_impuestos.valor_mercancia, Decimal('300'))
        self.assertEqual(a.renglon_de_impuestos.fletes, Decimal('40.00'))
        self.assertEqual(a.renglon_de_impuestos.tasa_igi, Decimal('0.05'))
        self.hoja.refresh_from_db()
        self.assertEqual(self.hoja.valor_mercancia, Decimal('900'))
        self.assertEqual(self.hoja.valor_expresion, '500+700-300')
        self.assertEqual(self.hoja.fletes, Decimal('60.00'))

    def test_sin_valor_se_reparte_por_bultos(self):
        self.sacar()
        a = WarehouseOperation.objects.get(custom_id='ED261111-0901-A')
        self.assertEqual(a.renglon_de_impuestos.valor_mercancia, Decimal('480.00'))
        self.hoja.refresh_from_db()
        self.assertEqual(self.hoja.valor_mercancia, Decimal('720.00'))

    def test_no_se_queda_mas_valor_del_que_hay(self):
        self.assertEqual(self.sacar(valor_usd='1200').status_code, 422)
        self.assertEqual(self.sacar(valor_usd='abc').status_code, 422)
        self.hoja.refresh_from_db()
        self.assertEqual(self.hoja.valor_mercancia, Decimal('1200'))
        self.assertFalse(WarehouseOperation.objects.filter(subdivision='SUB2').exists())

    def test_los_documentos_no_se_copian(self):
        self.sacar(valor_usd='300')
        a = WarehouseOperation.objects.get(custom_id='ED261111-0901-A')
        self.assertFalse(a.documents.exists())
