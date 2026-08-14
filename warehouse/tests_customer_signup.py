"""
Alta del cliente nivel 2 en un solo paso.

Antes eran dos pantallas: crear el Catalog CUSTOMER, y luego ir a user_management
a crear el usuario y apuntarlo al cliente. Era facil quedarse a medias y terminar
con un usuario 'customer' sin cliente asignado, que por fail-closed no ve nada.

Lo que se prueba aqui es que la accion crea las dos cosas ya enlazadas, que
rechaza los casos malos sin dejar basura a medias, y que el alta respeta el
tenant de quien la ejecuta.
"""
from django.contrib.auth.models import User
from django.test import TestCase

from .models import Catalog, Tenant, UserProfile


class CreateCustomerAccountTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Warehouse Uno', type='organization', subdomain='uno')
        cls.otro_tenant = Tenant.objects.create(
            name='Warehouse Dos', type='organization', subdomain='dos')

        # Solo superadmin/admin pasan can_manage_users().
        cls.admin = User.objects.create_user('admin_uno', password='x')
        UserProfile.objects.create(user=cls.admin, tenant=cls.tenant, role='superadmin')

        cls.staff = User.objects.create_user('staff_uno', password='x')
        UserProfile.objects.create(user=cls.staff, tenant=cls.tenant, role='staff')

    def _alta(self, **extra):
        datos = {
            'action': 'create_customer',
            'customer_name': 'ACME Logistics',
            'username': 'acme_user',
            'password': 'secreto123',
        }
        datos.update(extra)
        return self.client.post('/users/', datos)

    def test_crea_cliente_y_usuario_ya_enlazados(self):
        self.client.force_login(self.admin)
        self._alta(abbreviation='acme', contact_email='ops@acme.com',
                   phone='555-1234', whatsapp='+5215551234')

        cat = Catalog.objects.get(name='ACME Logistics')
        self.assertEqual(cat.category, 'CUSTOMER')
        self.assertEqual(cat.tenant, self.tenant)
        self.assertEqual(cat.abbreviation, 'ACME')  # se normaliza a mayusculas
        self.assertEqual(cat.contact_email, 'ops@acme.com')
        self.assertEqual(cat.whatsapp, '+5215551234')

        perfil = UserProfile.objects.get(user__username='acme_user')
        self.assertEqual(perfil.role, 'customer')
        self.assertEqual(perfil.customer, cat)
        self.assertEqual(perfil.tenant, self.tenant)

    def test_el_usuario_creado_puede_entrar_con_esa_contrasena(self):
        """create_user debe hashear: si se guardara plana, login fallaria."""
        self.client.force_login(self.admin)
        self._alta()
        self.client.logout()
        self.assertTrue(self.client.login(username='acme_user', password='secreto123'))

    def test_los_campos_opcionales_vacios_quedan_en_null_no_en_cadena_vacia(self):
        self.client.force_login(self.admin)
        self._alta()
        cat = Catalog.objects.get(name='ACME Logistics')
        self.assertIsNone(cat.abbreviation)
        self.assertIsNone(cat.contact_email)
        self.assertIsNone(cat.phone)

    def test_username_repetido_no_crea_el_cliente(self):
        """Lo importante es que no quede un Catalog huerfano sin su usuario."""
        User.objects.create_user('acme_user', password='otro')
        self.client.force_login(self.admin)
        resp = self._alta()

        self.assertFalse(Catalog.objects.filter(name='ACME Logistics').exists())
        self.assertContains(resp, 'already taken')

    def test_cliente_repetido_no_crea_el_usuario(self):
        Catalog.objects.create(
            tenant=self.tenant, category='CUSTOMER', name='ACME Logistics')
        self.client.force_login(self.admin)
        resp = self._alta()

        self.assertFalse(User.objects.filter(username='acme_user').exists())
        self.assertEqual(Catalog.objects.filter(name='ACME Logistics').count(), 1)
        self.assertContains(resp, 'already exists')

    def test_el_duplicado_de_cliente_no_distingue_mayusculas(self):
        Catalog.objects.create(
            tenant=self.tenant, category='CUSTOMER', name='acme logistics')
        self.client.force_login(self.admin)
        self._alta()
        self.assertFalse(User.objects.filter(username='acme_user').exists())

    def test_un_cliente_del_mismo_nombre_en_otro_tenant_no_estorba(self):
        """El nombre solo choca dentro del propio tenant."""
        Catalog.objects.create(
            tenant=self.otro_tenant, category='CUSTOMER', name='ACME Logistics')
        self.client.force_login(self.admin)
        self._alta()

        perfil = UserProfile.objects.get(user__username='acme_user')
        self.assertEqual(perfil.customer.tenant, self.tenant)

    def test_faltan_campos_obligatorios(self):
        self.client.force_login(self.admin)
        for faltante in ('customer_name', 'username', 'password'):
            with self.subTest(campo=faltante):
                resp = self._alta(**{faltante: '   '})
                self.assertContains(resp, 'are all required')
                self.assertFalse(Catalog.objects.filter(name='ACME Logistics').exists())
                self.assertFalse(User.objects.filter(username='acme_user').exists())

    def test_un_staff_no_puede_dar_de_alta_clientes(self):
        self.client.force_login(self.staff)
        resp = self._alta()

        self.assertEqual(resp.status_code, 403)
        self.assertFalse(Catalog.objects.filter(name='ACME Logistics').exists())
        self.assertFalse(User.objects.filter(username='acme_user').exists())
