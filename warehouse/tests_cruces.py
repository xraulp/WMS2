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

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from .models import (CambioDeDiaDeCruce, Catalog, CrossingTask, CrossingTaskItem,
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
        self.assertIn('jefa', str(t.creada_por))
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
                      'Cancelar este cruce', 'Abierta', 'a nombre de Ferreteria Lopez'):
            self.assertContains(respuesta, frase)
        for frase in ('Take out', 'Picking list', 'Cancel this crossing', 'on behalf of'):
            self.assertNotContains(respuesta, frase)
