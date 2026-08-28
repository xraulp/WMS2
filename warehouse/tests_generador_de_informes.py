"""
Los filtros del generador de informes.

La pantalla se puso al dia con la de Operaciones y de paso salieron a la luz
tres cosas que no estaban bien: al cliente se le pedia elegir cliente cuando
solo podia haber uno --el suyo-- y sin marcarlo la busqueda contestaba con un
error; el manager veia el desplegable de autores con un solo nombre dentro, el
suyo, porque la plantilla se lo pintaba y la vista no se lo llenaba; y el filtro
por estado devolvia salidas, que no tienen estado.
"""
from django.contrib.auth.models import User
from django.test import TestCase

from .models import Catalog, Tenant, UserProfile, WarehouseOperation


class BaseDelInforme(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')

        cls.acme = Catalog.objects.create(
            category='CUSTOMER', name='Acme', tenant=cls.tenant)
        cls.zeta = Catalog.objects.create(
            category='CUSTOMER', name='Zeta', tenant=cls.tenant)

        def usuario(nombre, rol, **extra):
            u = User.objects.create_user(nombre, password='x')
            UserProfile.objects.create(user=u, tenant=cls.tenant, role=rol, **extra)
            return u

        cls.jefa    = usuario('jefa', 'admin')
        cls.manager = usuario('encargado', 'manager')
        cls.staff   = usuario('operador', 'staff')
        cls.cliente = usuario('cliente_acme', 'customer', customer=cls.acme)

        def op(custom_id, tipo, cliente, creador, despachada=''):
            return WarehouseOperation.objects.create(
                tenant=cls.tenant, operation_type=tipo, custom_id=custom_id,
                customer=cliente, created_by=creador,
                entry_dispatched=despachada)

        cls.acme_dentro   = op('ACME-1', 'ENTRY', cls.acme, cls.jefa)
        cls.acme_liberada = op('ACME-2', 'ENTRY', cls.acme, cls.staff, 'SD-9')
        cls.acme_salida   = op('ACME-3', 'EXIT',  cls.acme, cls.staff)
        cls.zeta_dentro   = op('ZETA-1', 'ENTRY', cls.zeta, cls.jefa)

    def informe(self, usuario, **parametros):
        self.client.force_login(usuario)
        parametros.setdefault('search', '1')
        respuesta = self.client.get('/reports/', parametros)
        self.assertEqual(respuesta.status_code, 200)
        return respuesta


class ElClienteNoEligeClienteTests(BaseDelInforme):

    def test_el_cliente_saca_su_informe_sin_elegir_nada(self):
        respuesta = self.informe(self.cliente)

        self.assertIsNone(respuesta.context['error'])
        self.assertEqual(
            sorted(op.custom_id for op in respuesta.context['results']),
            ['ACME-1', 'ACME-2', 'ACME-3'])

    def test_al_cliente_no_se_le_pinta_el_bloque_de_clientes(self):
        respuesta = self.informe(self.cliente)

        # Solo el marcador del bloque: el guion de abajo nombra los mismos
        # controles en sus selectores y da falsos positivos.
        self.assertNotContains(respuesta, 'id="rpt-all-chk"')
        self.assertEqual(list(respuesta.context['customers']), [])

    def test_a_los_demas_si_se_les_exige_elegir(self):
        """
        Un informe de todo el almacen mandado por correo al cliente equivocado
        es un accidente caro, asi que quien tiene entre que elegir, elige.
        """
        respuesta = self.informe(self.staff)

        self.assertIsNotNone(respuesta.context['error'])
        self.assertIsNone(respuesta.context['results'])


class FiltrosDelInformeTests(BaseDelInforme):

    def resultados(self, usuario, **parametros):
        parametros.setdefault('all_customers', '1')
        return sorted(op.custom_id
                      for op in self.informe(usuario, **parametros).context['results'])

    def test_el_estado_solo_mira_las_entradas(self):
        self.assertEqual(self.resultados(self.jefa, status_filter='In Warehouse'),
                         ['ACME-1', 'ZETA-1'])
        self.assertEqual(self.resultados(self.jefa, status_filter='Released Goods'),
                         ['ACME-2'])

    def test_por_autor(self):
        self.assertEqual(self.resultados(self.jefa, created_by=self.jefa.pk),
                         ['ACME-1', 'ZETA-1'])

    def test_autor_mas_tipo_mas_cliente(self):
        self.assertEqual(
            self.resultados(self.jefa, all_customers='', customer_ids=self.acme.pk,
                            created_by=self.staff.pk, op_type='EXIT'),
            ['ACME-3'])

    def test_el_staff_no_puede_filtrar_por_autor(self):
        """
        El pk viaja en la peticion, asi que el permiso se comprueba en la vista
        y no solo al pintar el desplegable. Sin filtro, recibe todo.
        """
        self.assertEqual(len(self.resultados(self.staff, created_by=self.jefa.pk)), 4)


class DesplegableDeAutoresTests(BaseDelInforme):

    def test_el_manager_ve_a_todos(self):
        """
        Antes la plantilla le pintaba el desplegable y la vista solo le metia
        dentro su propio nombre: un filtro de un valor, que no filtra nada.
        """
        respuesta = self.informe(self.manager, all_customers='1')

        nombres = sorted(u.username for u in respuesta.context['users'])
        self.assertEqual(nombres, ['cliente_acme', 'encargado', 'jefa', 'operador'])

    def test_el_staff_solo_se_ve_a_si_mismo(self):
        respuesta = self.informe(self.staff)

        self.assertEqual([u.username for u in respuesta.context['users']],
                         ['operador'])


class UnSoloCampoDeCorreoTests(BaseDelInforme):
    """
    Habia dos campos de correo con el mismo id. `getElementById` devuelve el
    primero, asi que el segundo se veia pero el envio no lo leia nunca.
    """

    def test_el_id_del_campo_de_correo_no_se_repite(self):
        respuesta = self.informe(self.jefa, all_customers='1')

        self.assertEqual(respuesta.content.decode().count('id="rpt-email-to"'), 1)
