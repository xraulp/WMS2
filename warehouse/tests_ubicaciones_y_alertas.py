"""
Bodegas, posiciones, alertas de permanencia y los cuatro tipos de operacion.

Las cuatro cosas se prueban juntas porque se tocan: la ubicacion solo la lleva
la entrada (ED), y la alerta de permanencia solo mira entradas, que es justo lo
que el trasbordo (TD) y la revision (RD) no son.
"""
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from .models import (Catalog, Location, Tenant, UserProfile, Warehouse,
                     WarehouseOperation)
from .views import expandir_rango


class BaseDeBodegas(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte',
            alert_days_default=7)
        cls.otro = Tenant.objects.create(
            name='Bodegas del Sur', type='organization', subdomain='sur')

        def usuario(nombre, rol, tenant=None, **extra):
            u = User.objects.create_user(nombre, password='x')
            UserProfile.objects.create(user=u, tenant=tenant or cls.tenant,
                                       role=rol, **extra)
            return u

        cls.jefa  = usuario('jefa', 'admin')
        cls.staff = usuario('operador', 'staff')

        cls.acme = Catalog.objects.create(
            category='CUSTOMER', name='Acme', tenant=cls.tenant)
        cls.lrd = Warehouse.objects.create(
            tenant=cls.tenant, name='Bodega Laredo', code='LRD')
        cls.ajena = Warehouse.objects.create(
            tenant=cls.otro, name='Bodega del Sur', code='SUR')


class ExpandirRangoTests(TestCase):
    """
    Sin esto no hay generador, y sin generador nadie da de alta seiscientas
    posiciones a mano.
    """

    def test_una_lista(self):
        self.assertEqual(expandir_rango('A,B,C'), ['A', 'B', 'C'])

    def test_un_rango_de_numeros(self):
        self.assertEqual(expandir_rango('1-4'), ['1', '2', '3', '4'])

    def test_el_cero_de_delante_se_conserva(self):
        """01-03 ordena bien como texto; 1-3 pone el 10 antes que el 2."""
        self.assertEqual(expandir_rango('01-03'), ['01', '02', '03'])

    def test_listas_y_rangos_mezclados(self):
        self.assertEqual(expandir_rango('A, 1-3, Z'), ['A', '1', '2', '3', 'Z'])

    def test_no_repite(self):
        self.assertEqual(expandir_rango('A,A,B'), ['A', 'B'])

    def test_vacio(self):
        self.assertEqual(expandir_rango(''), [])
        self.assertEqual(expandir_rango(None), [])

    def test_un_guion_que_no_es_un_rango_se_respeta(self):
        """Un pasillo se puede llamar "A-1"; eso no es un rango."""
        self.assertEqual(expandir_rango('A-1'), ['A-1'])

    def test_un_rango_al_reves_no_se_expande(self):
        self.assertEqual(expandir_rango('9-2'), ['9-2'])


class CodigoDeLaUbicacionTests(BaseDeBodegas):

    def test_se_arma_con_lo_que_hay(self):
        ubi = Location.objects.create(tenant=self.tenant, warehouse=self.lrd,
                                      zone='A', aisle='3', level='2')
        self.assertEqual(ubi.code, 'LRD-A-3-2')

    def test_los_niveles_vacios_se_saltan(self):
        ubi = Location.objects.create(tenant=self.tenant, warehouse=self.lrd,
                                      zone='RECEPCION')
        self.assertEqual(ubi.code, 'LRD-RECEPCION')

    def test_el_codigo_no_cambia_despues(self):
        """
        Se guarda y no se recalcula: una ubicacion ya usada por cien
        operaciones no puede cambiar de nombre porque alguien edite un campo.
        """
        ubi = Location.objects.create(tenant=self.tenant, warehouse=self.lrd,
                                      zone='A', aisle='3')
        ubi.aisle = '4'
        ubi.save()
        ubi.refresh_from_db()
        self.assertEqual(ubi.code, 'LRD-A-3')


class GenerarUbicacionesTests(BaseDeBodegas):

    def generar(self, usuario=None, **datos):
        self.client.force_login(usuario or self.jefa)
        datos.setdefault('warehouse', self.lrd.pk)
        return self.client.post('/locations/generate/', datos)

    def test_cruza_todos_los_niveles(self):
        self.generar(zones='A,B', aisles='1-3', levels='1-2')

        self.assertEqual(Location.objects.filter(warehouse=self.lrd).count(), 12)
        self.assertTrue(Location.objects.filter(code='LRD-A-1-1').exists())
        self.assertTrue(Location.objects.filter(code='LRD-B-3-2').exists())

    def test_repetir_no_duplica(self):
        """Ampliar una bodega es volver a generar con un pasillo mas."""
        self.generar(zones='A', aisles='1-2')
        self.generar(zones='A', aisles='1-3')

        self.assertEqual(Location.objects.filter(warehouse=self.lrd).count(), 3)

    def test_el_tope_corta_un_descuido(self):
        respuesta = self.generar(zones='1-30', aisles='1-30', levels='1-30')

        self.assertEqual(Location.objects.count(), 0)
        self.assertIn('over the limit', respuesta.context['error'])

    def test_sin_ningun_nivel_no_crea_nada(self):
        respuesta = self.generar()

        self.assertEqual(Location.objects.count(), 0)
        self.assertIsNotNone(respuesta.context['error'])

    def test_el_staff_no_configura_la_bodega(self):
        """
        Capturar operaciones si; decidir como se numeran los pasillos no. Es una
        decision de la casa que luego lleva pegada cada operacion.
        """
        respuesta = self.generar(self.staff, zones='A')

        self.assertEqual(respuesta.status_code, 403)
        self.assertEqual(Location.objects.count(), 0)

    def test_no_se_puede_generar_en_la_bodega_de_otra_empresa(self):
        respuesta = self.generar(warehouse=self.ajena.pk, zones='A')

        self.assertEqual(Location.objects.filter(warehouse=self.ajena).count(), 0)
        self.assertIsNotNone(respuesta.context['error'])


class BaseConPosiciones(BaseDeBodegas):
    """Una bodega con dos posiciones, otra bodega, y una posicion ajena.

    La comparten el alta y la edicion: las dos ponen el mismo dato, y probar la
    correccion sin las mismas posiciones seria probar otra cosa.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.a1 = Location.objects.create(tenant=cls.tenant, warehouse=cls.lrd,
                                         zone='A', aisle='1')
        cls.a2 = Location.objects.create(tenant=cls.tenant, warehouse=cls.lrd,
                                         zone='A', aisle='2')
        cls.otra_bodega = Warehouse.objects.create(
            tenant=cls.tenant, name='Bodega Dos', code='DOS')
        cls.b1 = Location.objects.create(tenant=cls.tenant,
                                         warehouse=cls.otra_bodega, zone='X')
        cls.ajena = Location.objects.create(tenant=cls.otro, warehouse=cls.ajena,
                                            zone='Z')

    def alta(self, **extra):
        self.client.force_login(self.staff)
        datos = {
            'date': str(timezone.now().date()), 'operation_type': 'ENTRY',
            'customer_text': 'Acme', 'shipper_text': 'Ship',
            'carrier_text': 'Carrier', 'bundle_type_text': 'PALLET',
            'bundle_qty': '1', 'weight_lbs': '10', 'description': 'x',
        }
        datos.update(extra)
        self.client.post('/operations/create/', datos)
        return WarehouseOperation.objects.order_by('-pk').first()


class AltaConUbicacionTests(BaseConPosiciones):

    def test_se_guarda_donde_queda_la_mercancia(self):
        op = self.alta(warehouse_id=self.lrd.pk, location_id=self.a1.pk)

        self.assertEqual(op.warehouse, self.lrd)
        self.assertEqual(op.location, self.a1)

    def test_la_bodega_se_deduce_de_la_posicion(self):
        """Son el mismo dato dicho dos veces; pedirlo dos veces solo descuadra."""
        op = self.alta(location_id=self.a1.pk)

        self.assertEqual(op.warehouse, self.lrd)

    def test_una_posicion_de_otra_bodega_no_se_guarda(self):
        """Diria que la carga esta en un pasillo que esa nave no tiene."""
        op = self.alta(warehouse_id=self.lrd.pk, location_id=self.b1.pk)

        self.assertEqual(op.warehouse, self.lrd)
        self.assertIsNone(op.location)

    def test_una_posicion_de_otra_empresa_no_se_guarda(self):
        op = self.alta(location_id=self.ajena.pk)

        self.assertIsNone(op.location)
        self.assertIsNone(op.warehouse)

    def test_la_ubicacion_solo_se_guarda_en_una_entrada(self):
        """
        La pantalla esconde los dos desplegables en los demas tipos, pero el
        POST llega igual: una salida guardada con posicion diria que la carga
        sigue en el estante del que acaba de salir.
        """
        for tipo in ('EXIT', 'TD', 'RD'):
            with self.subTest(tipo=tipo):
                op = self.alta(operation_type=tipo, warehouse_id=self.lrd.pk,
                               location_id=self.a1.pk)
                self.assertIsNone(op.location)
                self.assertIsNone(op.warehouse)

        entrada = self.alta(operation_type='ENTRY', location_id=self.a1.pk)
        self.assertEqual(entrada.location, self.a1)

    def test_sin_ubicacion_se_guarda_igual(self):
        """Ochenta y seis operaciones viejas no tienen ninguna, y una empresa
        de una sola nave no tiene por que rellenarlo."""
        op = self.alta()

        self.assertIsNone(op.warehouse)
        self.assertIsNone(op.location)


class EdicionDeUbicacionTests(BaseConPosiciones):
    """
    Corregir donde quedo la mercancia. Hasta ahora la posicion solo se ponia al
    capturar, asi que una mal elegida se quedaba puesta para siempre.
    """

    def campos(self, op, **extra):
        datos = {
            'date': str(op.date), 'description': op.description or 'x',
            'bundle_qty': '1',
        }
        datos.update(extra)
        return datos

    def test_se_puede_corregir_la_posicion(self):
        op = self.alta(location_id=self.a1.pk)
        self.client.force_login(self.jefa)

        self.client.post(f'/operations/{op.pk}/edit/',
                         self.campos(op, warehouse_id=self.lrd.pk,
                                     location_id=self.a2.pk))

        op.refresh_from_db()
        self.assertEqual(op.location, self.a2)

    def test_se_puede_dejar_sin_posicion(self):
        op = self.alta(location_id=self.a1.pk)
        self.client.force_login(self.jefa)

        self.client.post(f'/operations/{op.pk}/edit/', self.campos(op))

        op.refresh_from_db()
        self.assertIsNone(op.location)

    def test_una_posicion_de_otra_empresa_no_entra_por_la_edicion(self):
        op = self.alta(location_id=self.a1.pk)
        self.client.force_login(self.jefa)

        self.client.post(f'/operations/{op.pk}/edit/',
                         self.campos(op, location_id=self.ajena.pk))

        op.refresh_from_db()
        self.assertIsNone(op.location)

    def test_una_salida_no_recibe_posicion_al_editarla(self):
        """El tipo no se toca al editar, asi que el que manda es el guardado."""
        op = self.alta(operation_type='EXIT')
        self.client.force_login(self.jefa)

        self.client.post(f'/operations/{op.pk}/edit/',
                         self.campos(op, location_id=self.a1.pk))

        op.refresh_from_db()
        self.assertIsNone(op.location)

    def test_la_entrada_ofrece_los_desplegables_y_la_salida_no(self):
        entrada = self.alta(operation_type='ENTRY')
        salida  = self.alta(operation_type='EXIT')
        self.client.force_login(self.jefa)

        self.assertContains(self.client.get(f'/operations/{entrada.pk}/edit/'),
                            'ed-location')
        self.assertNotContains(self.client.get(f'/operations/{salida.pk}/edit/'),
                               'ed-location')


class TiposNuevosTests(BaseDeBodegas):

    def alta(self, tipo):
        self.client.force_login(self.staff)
        self.client.post('/operations/create/', {
            'date': str(timezone.now().date()), 'operation_type': tipo,
            'customer_text': 'Acme', 'shipper_text': 'Ship',
            'carrier_text': 'Carrier', 'bundle_type_text': 'PALLET',
            'bundle_qty': '1', 'weight_lbs': '10', 'description': 'x',
        })
        return WarehouseOperation.objects.order_by('-pk').first()

    def test_el_trasbordo_y_la_revision_se_aceptan(self):
        self.assertEqual(self.alta('TD').operation_type, 'TD')
        self.assertEqual(self.alta('RD').operation_type, 'RD')

    def test_cada_tipo_tiene_su_prefijo(self):
        self.assertTrue(self.alta('TD').custom_id.startswith('TD'))
        self.assertTrue(self.alta('RD').custom_id.startswith('RD'))
        self.assertTrue(self.alta('ENTRY').custom_id.startswith('ED'))
        self.assertTrue(self.alta('EXIT').custom_id.startswith('SD'))

    def test_un_tipo_inventado_se_rechaza(self):
        self.client.force_login(self.staff)
        respuesta = self.client.post('/operations/create/', {
            'date': str(timezone.now().date()), 'operation_type': 'XX',
            'customer_text': 'Acme', 'description': 'x',
        })
        self.assertEqual(respuesta.status_code, 422)

    def test_se_pueden_filtrar_en_la_tabla(self):
        self.alta('TD')
        self.client.force_login(self.jefa)
        respuesta = self.client.get('/operations/search/', {'type': 'TD'})

        self.assertEqual([op.operation_type for op in respuesta.context['operations']],
                         ['TD'])

    def test_ni_el_trasbordo_ni_la_revision_tienen_estado(self):
        """
        Solo una entrada esta "en almacen" o "liberada". Colar un trasbordo en
        el filtro de estado seria decir que hay mercancia guardada que no esta.
        """
        self.alta('TD')
        self.client.force_login(self.jefa)
        respuesta = self.client.get('/operations/search/', {'status': 'In Warehouse'})

        self.assertEqual(list(respuesta.context['operations']), [])


class OcupacionTests(BaseConPosiciones):
    """
    Que hay guardado en cada posicion. Cuenta lo mismo que la alerta de
    permanencia -- entradas sin liberar -- porque una entrada ya liberada no
    ocupa estante.
    """

    def guardar(self, ubicacion, liberada=False):
        return WarehouseOperation.objects.create(
            tenant=self.tenant, operation_type='ENTRY',
            date=timezone.now().date(), warehouse=ubicacion.warehouse,
            location=ubicacion, customer=self.acme,
            entry_dispatched='SD260101-0001' if liberada else '')

    def panel(self, **params):
        self.client.force_login(self.jefa)
        return self.client.get('/locations/', params)

    def ocupacion(self, respuesta):
        return {u.code: u.guardadas for u in respuesta.context['ubicaciones']}

    def test_cuenta_lo_que_hay_en_cada_posicion(self):
        self.guardar(self.a1)
        self.guardar(self.a1)
        self.guardar(self.a2)

        ocupacion = self.ocupacion(self.panel())

        self.assertEqual(ocupacion[self.a1.code], 2)
        self.assertEqual(ocupacion[self.a2.code], 1)

    def test_lo_liberado_deja_de_ocupar(self):
        self.guardar(self.a1, liberada=True)

        self.assertEqual(self.ocupacion(self.panel())[self.a1.code], 0)

    def test_una_salida_no_ocupa(self):
        """Solo la entrada guarda mercancia; los demas tipos ni la llevan."""
        WarehouseOperation.objects.create(
            tenant=self.tenant, operation_type='EXIT', date=timezone.now().date(),
            warehouse=self.lrd, location=self.a1, customer=self.acme)

        self.assertEqual(self.ocupacion(self.panel())[self.a1.code], 0)

    def test_solo_ocupadas_esconde_las_vacias(self):
        self.guardar(self.a1)

        respuesta = self.panel(ocupadas='1')

        self.assertEqual(list(self.ocupacion(respuesta)), [self.a1.code])

    def test_el_contador_de_ocupadas_no_cuenta_las_vacias(self):
        self.guardar(self.a1)

        self.assertEqual(self.panel().context['posiciones_ocupadas'], 1)

    def test_la_tabla_tiene_tantas_celdas_como_cabeceras(self):
        """
        Al meter la columna de ocupacion se perdio la de Kind, y todo lo que
        venia detras se corrio un sitio: el numero de guardado se leia bajo
        "Kind" y el estado bajo "Stored". La pantalla seguia pintandose sin
        quejarse, asi que solo se ve mirandola.
        """
        self.guardar(self.a1)

        html = self.panel().content.decode()
        cuerpo = html.split('<tbody>')[1].split('</tbody>')[0]
        primera_fila = cuerpo.split('<tr')[1]

        self.assertEqual(primera_fila.count('<td'),
                         html.split('<thead>')[1].split('</thead>')[0].count('<th'))

    def test_la_tabla_se_puede_acotar_a_una_posicion(self):
        aqui = self.guardar(self.a1)
        self.guardar(self.a2)
        self.client.force_login(self.jefa)

        respuesta = self.client.get('/operations/search/', {'location': self.a1.pk})

        self.assertEqual([op.pk for op in respuesta.context['operations']], [aqui.pk])

    def test_una_posicion_de_otra_empresa_no_acota_nada(self):
        self.guardar(self.a1)
        self.client.force_login(self.jefa)

        respuesta = self.client.get('/operations/search/', {'location': self.ajena.pk})

        self.assertEqual(list(respuesta.context['operations']), [])


class AlertaDePermanenciaTests(BaseDeBodegas):

    def entrada(self, dias, cliente=None):
        return WarehouseOperation.objects.create(
            tenant=self.tenant, operation_type='ENTRY',
            custom_id='OP-%d-%s' % (dias, cliente.name if cliente else 'x'),
            customer=cliente,
            date=timezone.now().date() - timedelta(days=dias))

    def test_por_debajo_del_plazo_no_avisa(self):
        self.assertIsNone(self.entrada(3).alerta_permanencia)

    def test_al_cumplirse_el_plazo_avisa(self):
        self.assertEqual(self.entrada(7).alerta_permanencia, 'vencida')

    def test_al_doble_del_plazo_sube_de_tono(self):
        self.assertEqual(self.entrada(14).alerta_permanencia, 'urgente')

    def test_manda_el_plazo_del_cliente(self):
        """Una semana para uno es lo normal y para otro ya es almacenaje."""
        self.acme.alert_days = 30
        self.acme.save()

        self.assertIsNone(self.entrada(10, self.acme).alerta_permanencia)
        self.assertEqual(self.entrada(30, self.acme).alerta_permanencia, 'vencida')

    def test_una_salida_nunca_avisa(self):
        salida = WarehouseOperation.objects.create(
            tenant=self.tenant, operation_type='EXIT', custom_id='SD-1',
            date=timezone.now().date() - timedelta(days=90))

        self.assertIsNone(salida.alerta_permanencia)

    def test_lo_ya_liberado_deja_de_contar(self):
        op = self.entrada(90)
        op.entry_dispatched = 'SD260101-0001'
        op.save()

        self.assertIsNone(op.alerta_permanencia)
        self.assertIsNone(op.dias_en_bodega)


class PantallaDeVencidasTests(BaseDeBodegas):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        def entrada(custom_id, dias):
            return WarehouseOperation.objects.create(
                tenant=cls.tenant, operation_type='ENTRY', custom_id=custom_id,
                customer=cls.acme, date=timezone.now().date() - timedelta(days=dias))
        cls.reciente = entrada('OP-NUEVA', 2)
        cls.vencida  = entrada('OP-VIEJA', 10)
        cls.urgente  = entrada('OP-ANTIGUA', 40)

    def test_solo_salen_las_vencidas_y_lo_mas_viejo_primero(self):
        """
        El orden normal de la tabla --lo reciente arriba-- dejaria justo abajo
        lo que mas corre prisa.
        """
        self.client.force_login(self.jefa)
        respuesta = self.client.get('/operations/aging/')

        self.assertEqual([op.custom_id for op in respuesta.context['operations']],
                         ['OP-ANTIGUA', 'OP-VIEJA'])

    def test_el_contador_del_tablero_cuenta_lo_mismo(self):
        self.client.force_login(self.jefa)
        respuesta = self.client.get('/dashboard/')

        self.assertEqual(respuesta.context['alertas'],
                         {'total': 2, 'urgentes': 1})

    def test_el_filtro_de_la_tabla_hace_lo_mismo(self):
        self.client.force_login(self.jefa)
        respuesta = self.client.get('/operations/search/', {'aging': '1'})

        self.assertEqual(sorted(op.custom_id for op in respuesta.context['operations']),
                         ['OP-ANTIGUA', 'OP-VIEJA'])

    def test_un_cliente_solo_ve_lo_suyo(self):
        otro_cliente = Catalog.objects.create(
            category='CUSTOMER', name='Zeta', tenant=self.tenant)
        WarehouseOperation.objects.create(
            tenant=self.tenant, operation_type='ENTRY', custom_id='OP-ZETA',
            customer=otro_cliente, date=timezone.now().date() - timedelta(days=50))
        usuario = User.objects.create_user('cliente_acme', password='x')
        UserProfile.objects.create(user=usuario, tenant=self.tenant,
                                   role='customer', customer=self.acme)

        self.client.force_login(usuario)
        respuesta = self.client.get('/operations/aging/')

        self.assertNotIn('OP-ZETA',
                         [op.custom_id for op in respuesta.context['operations']])


class UbicacionEnElMovilTests(BaseConPosiciones):
    """
    La pantalla del movil es donde se captura de verdad -- quien registra una
    entrada anda por la bodega con el telefono y esta delante del estante --, y
    era la unica que no preguntaba donde queda la mercancia.

    El guardado no cambia: `ubicacion_del_post` valida el POST venga de la
    pantalla que venga. Lo que faltaba era ofrecerlo.
    """

    def setUp(self):
        self.client.force_login(self.staff)

    def test_la_pantalla_ofrece_las_bodegas_y_las_posiciones(self):
        respuesta = self.client.get('/mobile/')

        self.assertContains(respuesta, 'name="warehouse_id"')
        self.assertContains(respuesta, 'name="location_id"')
        self.assertContains(respuesta, self.a1.code)
        self.assertContains(respuesta, self.lrd.code)

    def test_no_ofrece_las_posiciones_de_otra_empresa(self):
        respuesta = self.client.get('/mobile/')

        self.assertNotContains(respuesta, 'value="%s"' % self.ajena.pk)

    def test_cada_posicion_dice_de_que_bodega_es(self):
        """Es lo que permite filtrarlas al elegir bodega sin volver a preguntar
        al servidor."""
        respuesta = self.client.get('/mobile/')

        self.assertContains(respuesta, 'data-bodega="%s"' % self.lrd.pk)

    def test_la_ubicacion_nace_escondida_y_solo_la_ensena_una_entrada(self):
        respuesta = self.client.get('/mobile/')

        self.assertContains(respuesta, 'id="mob-location-row" style="display:none"')
        self.assertContains(respuesta, "MOB_TIPOS_CON_UBICACION = ['ENTRY']")

    def test_una_entrada_capturada_desde_el_movil_guarda_su_posicion(self):
        """El formulario del movil manda los mismos dos campos que el del
        tablero, asi que el alta es la misma."""
        op = self.alta(warehouse_id=self.lrd.pk, location_id=self.a1.pk)

        self.assertEqual(op.location, self.a1)
        self.assertEqual(op.warehouse, self.lrd)


class TiposDeOperacionEnElMovilTests(BaseConPosiciones):
    """
    Los cuatro tipos, tambien en el movil.

    Estaban escritos a mano en la plantilla y solo habia dos: el trasbordo y la
    revision no se podian capturar desde la pantalla en la que mas se captura.
    Es el mismo vicio que el tablero ya habia resuelto sacandolos del modelo.
    """

    def setUp(self):
        self.client.force_login(self.staff)

    def test_la_pantalla_ofrece_los_cuatro(self):
        respuesta = self.client.get('/mobile/')

        for valor, _etiqueta in WarehouseOperation.TYPE_CHOICES:
            with self.subTest(tipo=valor):
                self.assertContains(respuesta, 'value="%s"' % valor)

    def test_salen_del_modelo_y_no_escritos_a_mano(self):
        """Anadir un quinto tipo no puede obligar a acordarse de esta
        plantilla."""
        respuesta = self.client.get('/mobile/')

        self.assertContains(respuesta, 'Transfer (TD)')
        self.assertContains(respuesta, 'Revision (RD)')

    def test_el_trasbordo_tambien_consume_entradas(self):
        """La salida y el trasbordo sacan mercancia de una entrada guardada; la
        revision no, porque la carga no se va, solo se mira."""
        respuesta = self.client.get('/mobile/')

        self.assertContains(respuesta, "MOB_TIPOS_QUE_DESPACHAN = ['EXIT','TD']")

    def test_un_trasbordo_capturado_desde_el_movil_se_guarda(self):
        op = self.alta(operation_type='TD')

        self.assertEqual(op.operation_type, 'TD')
        self.assertTrue(op.custom_id.startswith('TD'))


class ElContadorDeVencidasSePoneAlDiaTests(BaseConPosiciones):
    """
    Cambiar el plazo de un cliente guardaba bien el dato y dejaba el contador
    diciendo lo de antes: se pinta al cargar el tablero y no lo refrescaba
    nadie, asi que desde fuera parecia que el cambio no habia servido de nada.

    Lo mismo pasaba al capturar la salida que libera una entrada, que es lo que
    mas veces al dia mueve esta cuenta.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.cliente = Catalog.objects.create(
            tenant=cls.tenant, category='CUSTOMER', name='Cliente con plazo')

    def setUp(self):
        self.client.force_login(self.jefa)
        self.vieja = WarehouseOperation.objects.create(
            tenant=self.tenant, operation_type='ENTRY', custom_id='ED-VIEJA-1',
            customer=self.cliente, description='x',
            date=timezone.now().date() - timedelta(days=20))

    def _contador(self):
        return self.client.get('/operations/aging-count/').json()

    def test_el_contador_se_puede_consultar_sin_recargar(self):
        self.assertEqual(self._contador()['total'], 1)

    def test_ampliar_el_plazo_apaga_la_alerta(self):
        """Veinte dias en bodega dejan de ser tarde si el cliente admite
        treinta."""
        self.cliente.alert_days = 30
        self.cliente.save(update_fields=['alert_days'])

        self.assertEqual(self._contador()['total'], 0)

    def test_guardar_el_plazo_avisa_a_la_pantalla(self):
        respuesta = self.client.post('/catalog/%s/edit/' % self.cliente.pk, {
            'name': self.cliente.name, 'alert_days': '30',
        })

        self.assertEqual(respuesta.headers.get('HX-Trigger'), 'alertas-cambiadas')

    def test_guardar_la_ficha_sin_tocar_el_plazo_no_avisa(self):
        """El aviso repinta la tabla de operaciones; mandarlo cuando nada
        cambio seria trabajo para nada en cada guardado."""
        respuesta = self.client.post('/catalog/%s/edit/' % self.cliente.pk, {
            'name': 'Otro nombre',
        })

        self.assertIsNone(respuesta.headers.get('HX-Trigger'))

    def test_la_salida_que_libera_una_entrada_tambien_avisa(self):
        respuesta = self.client.post('/operations/create/', {
            'date': str(timezone.now().date()), 'operation_type': 'EXIT',
            'customer_id': self.cliente.pk, 'shipper_text': 'Ship',
            'carrier_text': 'Carrier', 'bundle_type_text': 'PALLET',
            'bundle_qty': '1', 'weight_lbs': '10', 'description': 'x',
            'entry_dispatched': self.vieja.custom_id,
        })

        self.assertEqual(respuesta.headers.get('HX-Trigger'), 'alertas-cambiadas')
        self.assertEqual(self._contador()['total'], 0)
