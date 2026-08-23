"""
El acceso de un cliente, que se reparte desde su propia ficha.

Dar de alta al cliente y darle acceso vivian en pantallas distintas, y de ahi
salian las dos formas de quedarse a medias: un usuario 'customer' sin cliente
-- que por fail-closed no ve nada -- y un cliente sin nadie que pueda entrar.

La primera ya es imposible: el rol y el cliente se ponen juntos y no se eligen.
La segunda se ve en la lista de clientes, marcada "sin acceso", y se arregla
desde el mismo boton que la muestra.

Lo que se prueba aqui es que ese boton crea al usuario ya enlazado, que respeta
el tenant de quien lo usa, y que no lo abre cualquiera.
"""
from django.contrib.auth.models import User
from django.test import TestCase

from .models import Catalog, Tenant, UserProfile


class AccesoDeUnClienteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Warehouse Uno', type='organization', subdomain='uno')
        cls.otro_tenant = Tenant.objects.create(
            name='Warehouse Dos', type='organization', subdomain='dos')

        cls.cliente = Catalog.objects.create(
            tenant=cls.tenant, category='CUSTOMER', name='ACME Logistics')
        cls.cliente_ajeno = Catalog.objects.create(
            tenant=cls.otro_tenant, category='CUSTOMER', name='Cliente De Otra')

        # Solo superadmin/admin pasan can_manage_users().
        cls.admin = User.objects.create_user('admin_uno', password='x')
        UserProfile.objects.create(user=cls.admin, tenant=cls.tenant,
                                   role='superadmin')
        cls.staff = User.objects.create_user('staff_uno', password='x')
        UserProfile.objects.create(user=cls.staff, tenant=cls.tenant, role='staff')

    def _dar_acceso(self, cliente=None, **extra):
        datos = {'username': 'acme_user', 'password': 'secreto123'}
        datos.update(extra)
        return self.client.post(
            f'/catalog/{(cliente or self.cliente).pk}/access/', datos)

    def test_el_usuario_nace_enlazado_a_ese_cliente(self):
        """El rol y el cliente no se eligen: salen de la ficha desde la que se abre."""
        self.client.force_login(self.admin)
        self._dar_acceso()

        perfil = UserProfile.objects.get(user__username='acme_user')
        self.assertEqual(perfil.role, 'customer')
        self.assertEqual(perfil.customer, self.cliente)
        self.assertEqual(perfil.tenant, self.tenant)

    def test_el_usuario_creado_puede_entrar_con_esa_contrasena(self):
        self.client.force_login(self.admin)
        self._dar_acceso()

        self.client.logout()
        self.assertTrue(self.client.login(username='acme_user',
                                          password='secreto123'))

    def test_el_rol_no_se_puede_colar_por_el_formulario(self):
        self.client.force_login(self.admin)
        self._dar_acceso(role='admin')

        self.assertEqual(
            UserProfile.objects.get(user__username='acme_user').role, 'customer')

    def test_un_nombre_ya_tomado_no_crea_nada(self):
        """
        El nombre de usuario es unico en toda la plataforma, no por empresa: sin
        esta comprobacion el alta reventaria a mitad.
        """
        self.client.force_login(self.admin)
        resp = self._dar_acceso(username='staff_uno')

        self.assertIn('already taken', resp.context['msg'])
        self.assertTrue(resp.context['msg_is_error'])
        self.assertEqual(
            UserProfile.objects.get(user=self.staff).role, 'staff')

    def test_faltan_campos_obligatorios(self):
        self.client.force_login(self.admin)
        resp = self._dar_acceso(password='')

        self.assertTrue(resp.context['msg_is_error'])
        self.assertFalse(User.objects.filter(username='acme_user').exists())

    def test_el_panel_lista_a_quien_ya_entra_por_ese_cliente(self):
        self.client.force_login(self.admin)
        self._dar_acceso(username='primero')
        resp = self._dar_acceso(username='segundo')

        self.assertEqual([u.username for u in resp.context['usuarios']],
                         ['primero', 'segundo'])

    def test_un_cliente_sin_nadie_lo_dice(self):
        self.client.force_login(self.admin)
        resp = self.client.get(f'/catalog/{self.cliente.pk}/access/')

        self.assertEqual(list(resp.context['usuarios']), [])
        self.assertContains(resp, 'Nadie de este cliente puede entrar')

    def test_no_se_alcanza_el_cliente_de_otra_empresa(self):
        self.client.force_login(self.admin)
        resp = self._dar_acceso(cliente=self.cliente_ajeno, username='colado')

        self.assertEqual(resp.status_code, 404)
        self.assertFalse(User.objects.filter(username='colado').exists())

    def test_un_staff_no_reparte_accesos(self):
        """
        Dar acceso es repartir una llave, asi que va por el permiso de usuarios
        y no por el del catalogo.
        """
        self.client.force_login(self.staff)
        resp = self._dar_acceso(username='colado')

        self.assertEqual(resp.status_code, 403)
        self.assertFalse(User.objects.filter(username='colado').exists())

    def test_un_staff_tampoco_ve_quien_tiene_acceso(self):
        self.client.force_login(self.staff)
        self.assertEqual(
            self.client.get(f'/catalog/{self.cliente.pk}/access/').status_code, 403)
