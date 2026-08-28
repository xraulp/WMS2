"""
Los filtros de la pantalla de Operaciones.

Eran tres controles que no se hablaban entre si: la busqueda llamaba a
`operations_search` con `q`, el estado llamaba a la misma vista pero solo con
`status`, y el usuario se iba a otra vista distinta, `operations_by_user`. El
ultimo que tocabas ganaba y borraba los demas sin decirlo, mientras los
desplegables se quedaban puestos asegurando un filtro que ya no estaba
aplicado.

Ahora todos entran por `operations_search` y se acumulan. Lo que se prueba aqui
es justo eso -- que se combinan -- mas los dos filtros nuevos (cliente y tipo) y
que el de usuario sigue pidiendo permiso.
"""
from django.contrib.auth.models import User
from django.test import TestCase

from .models import Catalog, Tenant, UserProfile, WarehouseOperation


class BaseConOperaciones(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')
        cls.otro_tenant = Tenant.objects.create(
            name='Bodegas del Sur', type='organization', subdomain='sur')

        def usuario(nombre, rol, tenant=None, **extra):
            u = User.objects.create_user(nombre, password='x')
            UserProfile.objects.create(user=u, tenant=tenant or cls.tenant,
                                       role=rol, **extra)
            return u

        cls.acme = Catalog.objects.create(
            category='CUSTOMER', name='Acme', tenant=cls.tenant)
        cls.zeta = Catalog.objects.create(
            category='CUSTOMER', name='Zeta', tenant=cls.tenant)

        cls.jefa   = usuario('jefa', 'admin')
        cls.staff  = usuario('operador', 'staff')
        cls.ajeno  = usuario('ajeno', 'admin', tenant=cls.otro_tenant)

        def op(custom_id, tipo, cliente, creador, despachada=''):
            return WarehouseOperation.objects.create(
                tenant=cls.tenant, operation_type=tipo, custom_id=custom_id,
                customer=cliente, created_by=creador,
                entry_dispatched=despachada)

        # Acme: una entrada en almacen, una entrada liberada, una salida.
        cls.acme_dentro   = op('ACME-1', 'ENTRY', cls.acme, cls.jefa)
        cls.acme_liberada = op('ACME-2', 'ENTRY', cls.acme, cls.staff, 'SD-9')
        cls.acme_salida   = op('ACME-3', 'EXIT',  cls.acme, cls.staff)
        # Zeta: una entrada en almacen.
        cls.zeta_dentro   = op('ZETA-1', 'ENTRY', cls.zeta, cls.jefa)

    def buscar(self, usuario=None, **parametros):
        self.client.force_login(usuario or self.jefa)
        respuesta = self.client.get('/operations/search/', parametros)
        self.assertEqual(respuesta.status_code, 200)
        return [op.custom_id for op in respuesta.context['operations']]


class FiltrosPorSeparadoTests(BaseConOperaciones):

    def test_por_cliente(self):
        self.assertEqual(sorted(self.buscar(customer=self.acme.pk)),
                         ['ACME-1', 'ACME-2', 'ACME-3'])

    def test_por_tipo(self):
        self.assertEqual(self.buscar(type='EXIT'), ['ACME-3'])

    def test_por_usuario(self):
        self.assertEqual(sorted(self.buscar(user=self.jefa.pk)),
                         ['ACME-1', 'ZETA-1'])

    def test_el_estado_solo_mira_las_entradas(self):
        """
        Una salida no esta "en almacen" ni "liberada" -- la columna de la tabla
        le pinta un guion -- asi que filtrar por estado no puede devolverla.
        """
        self.assertEqual(sorted(self.buscar(status='In Warehouse')),
                         ['ACME-1', 'ZETA-1'])
        self.assertEqual(self.buscar(status='Released Goods'), ['ACME-2'])

    def test_un_parametro_vacio_no_filtra(self):
        self.assertEqual(len(self.buscar(q='', customer='', type='', status='')), 4)

    def test_un_parametro_con_basura_no_revienta(self):
        """El pk viaja en la URL y se puede teclear a mano."""
        self.assertEqual(len(self.buscar(customer='abc', user='xyz', type='?')), 4)


class FiltrosCombinadosTests(BaseConOperaciones):
    """
    Lo que no se podia hacer antes: acotar por dos cosas a la vez.
    """

    def test_cliente_mas_tipo(self):
        self.assertEqual(sorted(self.buscar(customer=self.acme.pk, type='ENTRY')),
                         ['ACME-1', 'ACME-2'])

    def test_busqueda_mas_cliente(self):
        """La busqueda ya no se pierde al tocar un desplegable."""
        self.assertEqual(self.buscar(q='ACME', customer=self.zeta.pk), [])
        self.assertEqual(sorted(self.buscar(q='ACME', customer=self.acme.pk)),
                         ['ACME-1', 'ACME-2', 'ACME-3'])

    def test_usuario_mas_estado(self):
        self.assertEqual(self.buscar(user=self.staff.pk, status='Released Goods'),
                         ['ACME-2'])

    def test_los_cinco_a_la_vez(self):
        self.assertEqual(
            self.buscar(q='ACME', user=self.jefa.pk, customer=self.acme.pk,
                        type='ENTRY', status='In Warehouse'),
            ['ACME-1'])


class PermisoDelFiltroPorUsuarioTests(BaseConOperaciones):
    """
    El desplegable de usuarios solo lo ve quien administra, pero el pk viaja en
    la peticion: el permiso se comprueba en la vista, no al pintar la pantalla.
    """

    def test_el_staff_no_puede_filtrar_por_usuario(self):
        # No recibe "lo de la jefa": recibe todo, como si no hubiera filtro.
        self.assertEqual(len(self.buscar(self.staff, user=self.jefa.pk)), 4)

    def test_el_usuario_de_otra_empresa_no_filtra_nada(self):
        """
        `profile__tenant` impide que el pk de un usuario ajeno sirva de sonda.

        Para que la prueba muerda hace falta una operacion de esta empresa
        firmada por el de fuera: sin ella el filtro por tenant de las
        operaciones ya devolveria vacio y no se estaria comprobando nada.
        """
        WarehouseOperation.objects.create(
            tenant=self.tenant, operation_type='ENTRY', custom_id='RARA-1',
            created_by=self.ajeno)

        self.assertEqual(self.buscar(user=self.ajeno.pk), [])


class BarraDeImportacionTests(BaseConOperaciones):
    """
    `operations_import` ya rechazaba a quien no puede crear operaciones, pero la
    pantalla le seguia pintando el boton: una puerta que al empujarla da 403.
    """

    def test_el_cliente_no_ve_la_barra_de_importar(self):
        cliente = User.objects.create_user('cliente', password='x')
        UserProfile.objects.create(user=cliente, tenant=self.tenant,
                                   role='customer', customer=self.acme)
        self.client.force_login(cliente)
        respuesta = self.client.get('/operations/search/')
        self.assertNotContains(respuesta, 'Download Layout')

    def test_el_staff_si_la_ve(self):
        self.client.force_login(self.staff)
        respuesta = self.client.get('/operations/search/')
        self.assertContains(respuesta, 'Download Layout')
