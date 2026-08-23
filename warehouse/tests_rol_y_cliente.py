"""
Un usuario de cliente lleva rol 'customer' y su cliente. Nada mas.

La pantalla de usuarios ofrece el rol y el cliente como dos campos sueltos, asi
que se podia dar de alta a alguien "para un cliente" dejandole el 'staff' que
viene por omision. El perfil quedaba con cliente **y** con rol de la casa, y
`customer_ops_filter` solo acota a quien tiene rol 'customer': esa cuenta veia
todas las operaciones de la empresa, incluidas las de los demas clientes,
mientras quien la creo pensaba haber dado un acceso limitado.

Aqui se fija que esa combinacion no se pueda escribir, en el alta y en la
edicion, y se deja constancia de por que importa.
"""
from django.contrib.auth.models import User
from django.test import TestCase

from .models import Catalog, Tenant, UserProfile, WarehouseOperation
from .views import customer_ops_filter


class RolYClienteBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Almacen Uno', type='organization', subdomain='uno')
        cls.cliente = Catalog.objects.create(
            tenant=cls.tenant, category='CUSTOMER', name='Cliente A')
        cls.otro_cliente = Catalog.objects.create(
            tenant=cls.tenant, category='CUSTOMER', name='Cliente B')

        cls.jefa = User.objects.create_user('jefa', password='x')
        UserProfile.objects.create(user=cls.jefa, tenant=cls.tenant, role='admin')

    def setUp(self):
        self.client.force_login(self.jefa)

    def _crear(self, username, role, customer_id=''):
        return self.client.post('/users/', {
            'action': 'create', 'username': username, 'password': 'secreta',
            'role': role, 'customer_id': customer_id,
        })


class AltaDeUsuarioTests(RolYClienteBase):
    def test_no_se_crea_un_operador_con_cliente_asignado(self):
        """
        Es el caso que aparecio en produccion: una cuenta creada "para el
        cliente" con el rol que viene por omision.
        """
        resp = self._crear('custtes', 'staff', str(self.cliente.pk))

        self.assertFalse(User.objects.filter(username='custtes').exists())
        self.assertTrue(resp.context['msg_is_error'])
        self.assertIn('must have the role "customer"', resp.context['msg'])

    def test_no_se_crea_un_cliente_sin_su_cliente(self):
        """Una cuenta que no alcanza ni una sola operacion no es un acceso."""
        resp = self._crear('suelto', 'customer')

        self.assertFalse(User.objects.filter(username='suelto').exists())
        self.assertTrue(resp.context['msg_is_error'])
        self.assertIn('needs the customer it belongs to', resp.context['msg'])

    def test_el_alta_buena_de_un_usuario_de_cliente(self):
        self._crear('cliente_a', 'customer', str(self.cliente.pk))

        perfil = UserProfile.objects.get(user__username='cliente_a')
        self.assertEqual(perfil.role, 'customer')
        self.assertEqual(perfil.customer, self.cliente)

    def test_el_alta_buena_de_un_operador_sigue_funcionando(self):
        self._crear('operador', 'staff')

        perfil = UserProfile.objects.get(user__username='operador')
        self.assertEqual(perfil.role, 'staff')
        self.assertIsNone(perfil.customer)


class EdicionDeUsuarioTests(RolYClienteBase):
    def setUp(self):
        super().setUp()
        self.usuario = User.objects.create_user('cliente_a', password='x')
        self.perfil = UserProfile.objects.create(
            user=self.usuario, tenant=self.tenant, role='customer',
            customer=self.cliente)

    def _actualizar(self, role, customer_id=''):
        return self.client.post('/users/', {
            'action': 'update_role', 'user_id': self.usuario.pk,
            'role': role, 'customer_id': customer_id,
        })

    def test_no_se_asciende_a_un_usuario_de_cliente_conservandole_el_cliente(self):
        """
        Editar es la otra puerta a la misma incoherencia, y mas peligrosa: la
        cuenta ya se la entregaron a alguien de fuera de la empresa.
        """
        resp = self._actualizar('staff', str(self.cliente.pk))

        self.perfil.refresh_from_db()
        self.assertEqual(self.perfil.role, 'customer')
        self.assertEqual(self.perfil.customer, self.cliente)
        self.assertTrue(resp.context['msg_is_error'])

    def test_no_se_deja_a_un_usuario_de_cliente_sin_cliente(self):
        resp = self._actualizar('customer')

        self.perfil.refresh_from_db()
        self.assertEqual(self.perfil.customer, self.cliente)
        self.assertTrue(resp.context['msg_is_error'])

    def test_cambiar_de_cliente_si_se_puede(self):
        self._actualizar('customer', str(self.otro_cliente.pk))

        self.perfil.refresh_from_db()
        self.assertEqual(self.perfil.customer, self.otro_cliente)

    def test_convertir_a_operador_soltando_el_cliente_si_se_puede(self):
        """La persona cambia de bando: deja de ser del cliente y entra a la casa."""
        self._actualizar('staff')

        self.perfil.refresh_from_db()
        self.assertEqual(self.perfil.role, 'staff')
        self.assertIsNone(self.perfil.customer)


class PorQueImportaTests(RolYClienteBase):
    def test_un_operador_con_cliente_asignado_lo_ve_todo(self):
        """
        Prueba de caracterizacion: deja escrito el hecho que hace peligrosa la
        combinacion. El filtro acota por rol, no por tener cliente, y esta bien
        que asi sea -- el `customer` del perfil de un operador no significa
        nada. Por eso la puerta se cierra en la pantalla y no aqui.
        """
        colado = User.objects.create_user('colado', password='x')
        UserProfile.objects.create(user=colado, tenant=self.tenant,
                                   role='staff', customer=self.cliente)

        WarehouseOperation.objects.create(
            tenant=self.tenant, operation_type='ENTRY',
            custom_id='OP-A', customer=self.cliente)
        WarehouseOperation.objects.create(
            tenant=self.tenant, operation_type='ENTRY',
            custom_id='OP-B', customer=self.otro_cliente)

        visibles = customer_ops_filter(
            colado, WarehouseOperation.objects.filter(tenant=self.tenant))
        self.assertEqual(visibles.count(), 2)
