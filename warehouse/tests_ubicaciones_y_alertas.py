"""
Bodegas, posiciones, alertas de permanencia y los dos tipos nuevos.

Las cuatro cosas se prueban juntas porque llegaron juntas y se tocan: un
reacomodo (RD) no significa nada sin ubicacion de origen, y la alerta de
permanencia solo mira entradas, que es justo lo que los tipos nuevos no son.
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


class AltaConUbicacionTests(BaseDeBodegas):

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

    def test_el_origen_solo_se_guarda_en_un_reacomodo(self):
        reacomodo = self.alta(operation_type='RD', location_id=self.a1.pk,
                              location_from_id=self.a2.pk)
        self.assertEqual(reacomodo.location_from, self.a2)

        entrada = self.alta(operation_type='ENTRY', location_id=self.a1.pk,
                            location_from_id=self.a2.pk)
        self.assertIsNone(entrada.location_from)

    def test_sin_ubicacion_se_guarda_igual(self):
        """Ochenta y seis operaciones viejas no tienen ninguna, y una empresa
        de una sola nave no tiene por que rellenarlo."""
        op = self.alta()

        self.assertIsNone(op.warehouse)
        self.assertIsNone(op.location)


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

    def test_el_trasbordo_y_el_reacomodo_se_aceptan(self):
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

    def test_ni_el_trasbordo_ni_el_reacomodo_tienen_estado(self):
        """
        Solo una entrada esta "en almacen" o "liberada". Colar un trasbordo en
        el filtro de estado seria decir que hay mercancia guardada que no esta.
        """
        self.alta('TD')
        self.client.force_login(self.jefa)
        respuesta = self.client.get('/operations/search/', {'status': 'In Warehouse'})

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
