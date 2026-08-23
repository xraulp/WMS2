"""
En la base no queda ninguna contrasena legible.

`UserProfile` guardaba `plain_password`, una copia en claro de la contrasena de
acceso, con el unico fin de que la pantalla de usuarios pudiera volver a
mostrarla cuando alguien la olvidara. Se retiro, y lo que se prueba aqui es que
ninguno de los cuatro caminos que la escribian —alta de usuario, alta de cliente
con su login, cambio de contrasena y alta de empresa desde la plataforma— deja
rastro de ella, y que la pantalla tampoco la pinta.

La contrasena de Django siempre estuvo cifrada, asi que cada prueba comprueba
ademas que el usuario entra: quitar la copia en claro no puede costar el acceso.
"""
from django.contrib.auth.models import User
from django.test import TestCase

from .models import Catalog, PlatformUser, Tenant, UserProfile


CLAVE = 'secreto-de-diego-123'


def valores_guardados(perfil):
    """Todo lo que el perfil tiene escrito, como texto, para poder buscar dentro."""
    return ' '.join(
        str(getattr(perfil, campo.attname))
        for campo in perfil._meta.fields
    )


class ElModeloYaNoTieneElCampoTests(TestCase):

    def test_userprofile_no_declara_plain_password(self):
        nombres = {campo.name for campo in UserProfile._meta.get_fields()}
        self.assertNotIn('plain_password', nombres)


class AltaDeUsuarioTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')
        cls.admin = User.objects.create_user('admin_norte', password='x')
        UserProfile.objects.create(user=cls.admin, tenant=cls.tenant, role='superadmin')

    def setUp(self):
        self.client.force_login(self.admin)

    def test_el_alta_no_guarda_la_contrasena_en_claro(self):
        self.client.post('/users/', {
            'action': 'create', 'username': 'nuevo', 'password': CLAVE, 'role': 'staff'})

        perfil = UserProfile.objects.get(user__username='nuevo')
        self.assertNotIn(CLAVE, valores_guardados(perfil))

    def test_el_usuario_recien_creado_entra_con_esa_contrasena(self):
        self.client.post('/users/', {
            'action': 'create', 'username': 'nuevo', 'password': CLAVE, 'role': 'staff'})

        self.assertTrue(
            self.client.login(username='nuevo', password=CLAVE))

    def test_el_alta_avisa_de_que_la_contrasena_no_se_guarda(self):
        respuesta = self.client.post('/users/', {
            'action': 'create', 'username': 'nuevo', 'password': CLAVE, 'role': 'staff'})

        self.assertIn('not stored', respuesta.content.decode())

    def test_el_acceso_que_se_da_a_un_cliente_tampoco_la_guarda(self):
        """
        El acceso de un cliente se reparte desde su ficha desde que dejo de
        haber dos caminos para lo mismo. La regla de la contrasena no cambia
        porque cambie la pantalla: se asigna, no se consulta.
        """
        cliente = Catalog.objects.create(
            tenant=self.tenant, category='CUSTOMER', name='ACME')
        self.client.post(f'/catalog/{cliente.pk}/access/',
                         {'username': 'acme_user', 'password': CLAVE})

        perfil = UserProfile.objects.get(user__username='acme_user')
        self.assertEqual(perfil.customer, cliente)
        self.assertNotIn(CLAVE, valores_guardados(perfil))
        self.assertTrue(self.client.login(username='acme_user', password=CLAVE))


class CambioDeContrasenaTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')
        cls.admin = User.objects.create_user('admin_norte', password='x')
        UserProfile.objects.create(user=cls.admin, tenant=cls.tenant, role='superadmin')
        cls.operador = User.objects.create_user('operador', password='vieja123')
        UserProfile.objects.create(user=cls.operador, tenant=cls.tenant, role='staff')

    def setUp(self):
        self.client.force_login(self.admin)

    def _cambiar(self):
        return self.client.post('/users/', {
            'action': 'change_password', 'user_id': self.operador.pk,
            'new_password': CLAVE})

    def test_la_nueva_contrasena_sirve_y_la_vieja_no(self):
        self._cambiar()

        self.assertFalse(self.client.login(username='operador', password='vieja123'))
        self.assertTrue(self.client.login(username='operador', password=CLAVE))

    def test_el_cambio_no_deja_la_contrasena_en_el_perfil(self):
        self._cambiar()

        perfil = UserProfile.objects.get(user=self.operador)
        self.assertNotIn(CLAVE, valores_guardados(perfil))


class LaPantallaNoLaMuestraTests(TestCase):
    """
    La columna Password repintaba la contrasena guardada, de modo que abrir la
    pestana Users era ver de golpe las de toda la empresa. Ahora el campo sirve
    para asignar una nueva y llega vacio.
    """

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')
        cls.admin = User.objects.create_user('admin_norte', password='x')
        UserProfile.objects.create(user=cls.admin, tenant=cls.tenant, role='superadmin')

    def test_la_pantalla_de_usuarios_no_trae_ninguna_contrasena(self):
        self.client.force_login(self.admin)
        self.client.post('/users/', {
            'action': 'create', 'username': 'nuevo', 'password': CLAVE, 'role': 'staff'})

        cuerpo = self.client.get('/users/').content.decode()

        self.assertIn('nuevo', cuerpo)          # el usuario si aparece
        self.assertNotIn(CLAVE, cuerpo)         # su contrasena no

        # Tampoco el hash: pintarlo seria regalar el material con el que se
        # ataca la contrasena sin limite de intentos ni registro de nada.
        self.assertNotIn(User.objects.get(username='nuevo').password, cuerpo)


class AltaDeEmpresaDesdeLaPlataformaTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.plat_admin = User.objects.create_user('plat_admin', password='x')
        PlatformUser.objects.create(user=cls.plat_admin, role='admin')

    def test_el_admin_creado_con_la_empresa_no_guarda_su_contrasena(self):
        self.client.force_login(self.plat_admin)
        self.client.post('/platform/tenants/', {
            'action': 'create', 'name': 'Empresa Nueva', 'plan': 'starter',
            'admin_username': 'admin_nuevo', 'admin_password': CLAVE})

        perfil = UserProfile.objects.get(user__username='admin_nuevo')
        self.assertEqual(perfil.role, 'admin')
        self.assertEqual(perfil.tenant, Tenant.objects.get(name='Empresa Nueva'))
        self.assertNotIn(CLAVE, valores_guardados(perfil))
        self.assertTrue(self.client.login(username='admin_nuevo', password=CLAVE))
