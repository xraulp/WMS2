"""
Aislamiento del Tenant nivel 2 (cliente-de-tu-cliente) dentro de un mismo tenant.

El aislamiento ENTRE tenants ya lo cubre el filtro tenant=tenant de cada vista.
Lo que se prueba aqui es el nivel de abajo: que un usuario con rol 'customer'
solo alcance las operaciones de SU cliente, incluso conociendo el pk, y que un
'customer' sin cliente asignado no alcance ninguna (fail-closed).
"""
from django.contrib.auth.models import User
from django.test import TestCase

from .models import Catalog, Tenant, UserProfile, WarehouseOperation


class CustomerOperationIsolationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Warehouse Uno', type='organization', subdomain='uno')

        cls.cliente_a = Catalog.objects.create(
            tenant=cls.tenant, category='CUSTOMER', name='Cliente A')
        cls.cliente_b = Catalog.objects.create(
            tenant=cls.tenant, category='CUSTOMER', name='Cliente B')

        cls.op_a = WarehouseOperation.objects.create(
            tenant=cls.tenant, operation_type='ENTRY',
            custom_id='OP-A', customer=cls.cliente_a)
        cls.op_b = WarehouseOperation.objects.create(
            tenant=cls.tenant, operation_type='ENTRY',
            custom_id='OP-B', customer=cls.cliente_b)

        # Usuario del Cliente A.
        cls.user_a = User.objects.create_user('cliente_a', password='x')
        UserProfile.objects.create(
            user=cls.user_a, tenant=cls.tenant, role='customer',
            customer=cls.cliente_a)

        # Usuario con rol customer pero SIN cliente asignado: el caso que antes
        # se colaba, porque el chequeo era "if profile.customer and ...".
        cls.user_huerfano = User.objects.create_user('huerfano', password='x')
        UserProfile.objects.create(
            user=cls.user_huerfano, tenant=cls.tenant, role='customer',
            customer=None)

        # Usuario interno del tenant: debe seguir viendo todo.
        cls.user_staff = User.objects.create_user('staff_interno', password='x')
        UserProfile.objects.create(
            user=cls.user_staff, tenant=cls.tenant, role='manager')

    def _rutas_de(self, op):
        return [
            f'/operations/{op.pk}/',
            f'/operations/{op.pk}/pdf/',
            f'/operations/{op.pk}/label/',
            f'/operations/{op.pk}/download-all/',
            f'/operations/{op.pk}/edit/',
        ]

    def test_customer_accede_a_su_propia_operacion(self):
        self.client.force_login(self.user_a)
        for url in self._rutas_de(self.op_a):
            with self.subTest(url=url):
                self.assertNotEqual(self.client.get(url).status_code, 403)

    def test_customer_no_accede_a_operacion_de_otro_cliente(self):
        self.client.force_login(self.user_a)
        for url in self._rutas_de(self.op_b):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_customer_sin_cliente_asignado_no_accede_a_nada(self):
        self.client.force_login(self.user_huerfano)
        for op in (self.op_a, self.op_b):
            for url in self._rutas_de(op):
                with self.subTest(url=url):
                    self.assertEqual(self.client.get(url).status_code, 403)

    def test_customer_no_puede_mandarse_por_email_operacion_ajena(self):
        """El PDF se enviaba a un correo elegido por quien hace el POST."""
        self.client.force_login(self.user_a)
        resp = self.client.post(
            f'/operations/{self.op_b.pk}/email/',
            {'recipient_email': 'fuga@example.com'})
        self.assertEqual(resp.status_code, 403)

    def test_digital_search_oculta_operacion_ajena(self):
        self.client.force_login(self.user_a)
        resp = self.client.get('/digital/search/', {'q': 'OP-B'})
        self.assertIsNone(resp.context['operation'])

        resp = self.client.get('/digital/search/', {'q': 'OP-A'})
        self.assertEqual(resp.context['operation'], self.op_a)

    def test_usuario_interno_no_queda_restringido(self):
        self.client.force_login(self.user_staff)
        for op in (self.op_a, self.op_b):
            for url in self._rutas_de(op):
                with self.subTest(url=url):
                    self.assertNotEqual(self.client.get(url).status_code, 403)
