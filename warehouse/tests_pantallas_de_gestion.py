"""
Las cuentas que hacen falta para administrar, y la correccion de un alta.

Son cuatro cosas que faltaban y que se piden desde el trabajo diario: saber si
un cliente tiene a alguien que pueda entrar, leer los usuarios de un cliente
juntos y no repartidos por toda la lista, ver de un vistazo el tamano de cada
empresa desde la plataforma, y poder corregir un alta sin bajar al admin de
Django.
"""
from django.contrib.auth.models import User
from django.test import TestCase

from .models import (Catalog, PlatformUser, Subscription, Tenant, UserProfile,
                     WarehouseOperation)


class BaseGestion(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Almacen Uno', type='organization', subdomain='uno',
            plan='starter', billing_email='pagos@uno.com')
        Subscription.objects.create(tenant=cls.tenant, plan='starter')

        cls.cliente_a = Catalog.objects.create(
            tenant=cls.tenant, category='CUSTOMER', name='Aceros del Bajio')
        cls.cliente_b = Catalog.objects.create(
            tenant=cls.tenant, category='CUSTOMER', name='Zapatos Zeta')
        cls.cliente_sin_gente = Catalog.objects.create(
            tenant=cls.tenant, category='CUSTOMER', name='Nadie Entra SA')
        # Del catalogo operativo: no debe contarse como cliente.
        Catalog.objects.create(
            tenant=cls.tenant, category='CARRIER', name='Transportes Rapidos')

        cls.jefa = User.objects.create_user('jefa', password='x')
        UserProfile.objects.create(user=cls.jefa, tenant=cls.tenant, role='admin')
        cls.operador = User.objects.create_user('operador', password='x')
        UserProfile.objects.create(user=cls.operador, tenant=cls.tenant, role='staff')

        for nombre, cliente in (('ana_aceros', cls.cliente_a),
                                ('beto_aceros', cls.cliente_a),
                                ('zoe_zapatos', cls.cliente_b)):
            u = User.objects.create_user(nombre, password='x')
            UserProfile.objects.create(user=u, tenant=cls.tenant,
                                       role='customer', customer=cliente)


class UsuariosPorClienteTests(BaseGestion):
    """En la pantalla de clientes, cuanta gente tiene cada uno."""

    def setUp(self):
        self.client.force_login(self.jefa)

    def test_cada_cliente_dice_cuantos_usuarios_tiene(self):
        resp = self.client.get('/catalog/list/', {'scope': 'customers'})
        cuenta = {e.name: e.usuarios for e in resp.context['catalog_entries']}

        self.assertEqual(cuenta['Aceros del Bajio'], 2)
        self.assertEqual(cuenta['Zapatos Zeta'], 1)

    def test_un_cliente_sin_nadie_se_ve_como_tal(self):
        """
        Un cliente con cero usuarios no puede entrar al sistema, y eso hasta
        ahora solo se descubria cuando llamaba.
        """
        resp = self.client.get('/catalog/list/', {'scope': 'customers'})
        cuenta = {e.name: e.usuarios for e in resp.context['catalog_entries']}

        self.assertEqual(cuenta['Nadie Entra SA'], 0)
        self.assertContains(resp, 'sin acceso')

    def test_el_personal_de_la_casa_no_cuenta_como_usuario_de_un_cliente(self):
        """La jefa y el operador no cuelgan de ningun cliente."""
        resp = self.client.get('/catalog/list/', {'scope': 'customers'})
        self.assertEqual(sum(e.usuarios for e in resp.context['catalog_entries']), 3)


class UsuariosAgrupadosTests(BaseGestion):
    """La lista de usuarios, con la gente de cada cliente junta."""

    def setUp(self):
        self.client.force_login(self.jefa)

    def test_primero_la_casa_y_luego_cada_cliente_seguido(self):
        resp = self.client.get('/users/')
        nombres = [u.username for u in resp.context['users']]

        self.assertEqual(nombres[:2], ['jefa', 'operador'])
        # Aceros antes que Zapatos, y su gente sin nadie en medio.
        self.assertEqual(nombres[2:], ['ana_aceros', 'beto_aceros', 'zoe_zapatos'])

    def test_la_lista_lleva_el_nombre_del_cliente_como_separador(self):
        resp = self.client.get('/users/')
        self.assertContains(resp, 'Aceros del Bajio')
        self.assertContains(resp, 'De la empresa')


class CuentasDeLaPlataformaTests(BaseGestion):
    """Lo que la plataforma puede saber de una empresa sin abrir sus datos."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.plat_admin = User.objects.create_user('plat_admin', password='x')
        PlatformUser.objects.create(user=cls.plat_admin, role='admin')
        WarehouseOperation.objects.create(
            tenant=cls.tenant, operation_type='ENTRY', custom_id='OP-1',
            customer=cls.cliente_a)

    def setUp(self):
        self.client.force_login(self.plat_admin)

    def _fila(self):
        resp = self.client.get('/platform/tenants/')
        return [t for t in resp.context['tenants'] if t.pk == self.tenant.pk][0]

    def test_los_usuarios_de_la_empresa_no_incluyen_a_los_de_sus_clientes(self):
        """
        Contaba a todo el mundo junto, asi que una empresa con dos operarios y
        treinta clientes salia con treinta y dos y ese numero no respondia a
        ninguna pregunta.
        """
        self.assertEqual(self._fila().user_count, 2)

    def test_cuantos_clientes_tiene_la_empresa(self):
        """Solo del catalogo de clientes: un carrier no es un cliente."""
        self.assertEqual(self._fila().customer_count, 3)

    def test_cuantos_usuarios_tienen_esos_clientes(self):
        self.assertEqual(self._fila().customer_user_count, 3)

    def test_las_operaciones_se_siguen_contando_bien(self):
        """
        Las cuatro cuentas salen de la misma consulta: sin `distinct` en cada
        una, los cruces se multiplican entre si y todos los numeros mienten.
        """
        self.assertEqual(self._fila().op_count, 1)


class EditarElAltaDeUnaEmpresaTests(BaseGestion):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.plat_admin = User.objects.create_user('plat_admin', password='x')
        PlatformUser.objects.create(user=cls.plat_admin, role='admin')
        cls.plat_staff = User.objects.create_user('plat_staff', password='x')
        PlatformUser.objects.create(user=cls.plat_staff, role='staff')
        cls.otra = Tenant.objects.create(
            name='Almacen Dos', type='organization', subdomain='dos')

    def _editar(self, **campos):
        datos = {
            'action': 'update', 'tenant_id': self.tenant.pk,
            'name': self.tenant.name, 'subdomain': self.tenant.subdomain,
            'plan': self.tenant.plan, 'billing_email': self.tenant.billing_email or '',
        }
        datos.update(campos)
        return self.client.post('/platform/tenants/', datos)

    def test_se_corrige_el_nombre_y_el_correo(self):
        self.client.force_login(self.plat_admin)
        self._editar(name='Almacen Uno SA de CV', billing_email='cobros@uno.com')

        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.name, 'Almacen Uno SA de CV')
        self.assertEqual(self.tenant.billing_email, 'cobros@uno.com')

    def test_el_plan_se_cambia_en_los_dos_sitios(self):
        """
        El plan vive en la empresa y en su suscripcion, y la facturacion lee el
        de la suscripcion: cambiar solo uno los deja discordes y se factura por
        el plan viejo.
        """
        self.client.force_login(self.plat_admin)
        self._editar(plan='pro')

        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.plan, 'pro')
        self.assertEqual(Subscription.objects.get(tenant=self.tenant).plan, 'pro')

    def test_el_subdominio_no_puede_pisar_al_de_otra_empresa(self):
        self.client.force_login(self.plat_admin)
        resp = self._editar(subdomain='dos')

        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.subdomain, 'uno')
        self.assertIn('already in use', resp.context['msg'])

    def test_cambiar_el_subdominio_avisa_de_lo_que_rompe(self):
        """Es la direccion por la que entra su gente, no un detalle de captura."""
        self.client.force_login(self.plat_admin)
        resp = self._editar(subdomain='almacen-uno')

        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.subdomain, 'almacen-uno')
        self.assertIn('no longer reach', resp.context['msg'])

    def test_un_nombre_vacio_no_borra_el_que_hay(self):
        self.client.force_login(self.plat_admin)
        resp = self._editar(name='   ')

        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.name, 'Almacen Uno')
        self.assertIn('required', resp.context['msg'])

    def test_el_soporte_mira_pero_no_edita(self):
        """Editar una empresa es del administrador de plataforma, no del soporte."""
        self.client.force_login(self.plat_staff)
        resp = self._editar(name='Cambiado por soporte')

        self.assertEqual(resp.status_code, 403)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.name, 'Almacen Uno')

    def test_un_admin_de_empresa_no_edita_la_suya_desde_aqui(self):
        self.client.force_login(self.jefa)
        resp = self._editar(name='Me asciendo solo')

        self.assertEqual(resp.status_code, 403)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.name, 'Almacen Uno')
