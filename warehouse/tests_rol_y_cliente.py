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


class UsuarioParaUnClienteQueYaExisteTests(RolYClienteBase):
    """
    El camino propio para dar acceso a otra persona de un cliente.

    Existe porque el alta de personal dejo de ofrecer el rol 'customer': era el
    desplegable que permitia crear usuarios "para un cliente" con rol de
    operador. Sin este formulario, un cliente que necesita que entren dos
    personas se quedaria sin manera de conseguirlo -- el alta de cliente crea el
    cliente y su primer usuario a la vez, y se niega si el cliente ya existe.
    """

    def _anadir(self, username, customer_id):
        return self.client.post('/users/', {
            'action': 'create_customer_user', 'username': username,
            'password': 'secreta', 'customer_id': customer_id,
        })

    def test_el_usuario_nace_como_cliente_y_con_su_cliente(self):
        self._anadir('segunda_persona', str(self.cliente.pk))

        perfil = UserProfile.objects.get(user__username='segunda_persona')
        self.assertEqual(perfil.role, 'customer')
        self.assertEqual(perfil.customer, self.cliente)
        self.assertEqual(perfil.tenant, self.tenant)

    def test_el_rol_no_se_puede_elegir_por_aqui(self):
        """Aunque el formulario mande un rol, este camino solo fabrica clientes."""
        self.client.post('/users/', {
            'action': 'create_customer_user', 'username': 'listillo',
            'password': 'secreta', 'customer_id': str(self.cliente.pk),
            'role': 'admin',
        })

        perfil = UserProfile.objects.get(user__username='listillo')
        self.assertEqual(perfil.role, 'customer')

    def test_sin_cliente_no_se_crea_nada(self):
        resp = self._anadir('suelto', '')

        self.assertFalse(User.objects.filter(username='suelto').exists())
        self.assertTrue(resp.context['msg_is_error'])

    def test_no_vale_un_cliente_de_otra_empresa(self):
        ajeno = Catalog.objects.create(
            tenant=Tenant.objects.create(name='Otra', type='organization',
                                         subdomain='otra'),
            category='CUSTOMER', name='Cliente Ajeno')
        resp = self._anadir('colado', str(ajeno.pk))

        self.assertFalse(User.objects.filter(username='colado').exists())
        self.assertTrue(resp.context['msg_is_error'])

    def test_un_nombre_ya_tomado_no_pisa_al_usuario_que_hay(self):
        """
        El nombre de usuario es unico en toda la plataforma, no por empresa: si
        esto no se comprobara, el alta reventaria a mitad.
        """
        resp = self._anadir('jefa', str(self.cliente.pk))

        self.assertTrue(resp.context['msg_is_error'])
        self.assertIn('already taken', resp.context['msg'])
        self.assertEqual(UserProfile.objects.get(user=self.jefa).role, 'admin')


class ElDesplegableDeRolesTests(RolYClienteBase):
    def test_el_alta_de_personal_no_ofrece_el_rol_de_cliente(self):
        """
        Ofrecerlo junto a los demas fue lo que permitio el error: se elegia un
        cliente y se dejaba el 'staff' que viene por omision.
        """
        self.client.force_login(self.jefa)
        resp = self.client.get('/users/')
        html = resp.content.decode()

        alta = html.split('Create New User')[1].split('</form>')[0]
        self.assertIn('value="staff"', alta)
        self.assertNotIn('value="customer"', alta)

    def test_quien_ya_es_cliente_conserva_su_rol_en_la_edicion(self):
        """
        Sin la opcion en su propio desplegable, guardar cualquier otro cambio
        suyo lo convertiria en personal de la empresa sin querer.
        """
        usuario = User.objects.create_user('cliente_a', password='x')
        UserProfile.objects.create(user=usuario, tenant=self.tenant,
                                   role='customer', customer=self.cliente)

        self.client.force_login(self.jefa)
        html = self.client.get('/users/').content.decode()
        self.assertIn('<option value="customer" selected>Customer</option>', html)
