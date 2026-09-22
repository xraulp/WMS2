"""
Nadie nace sin forma de recuperar su contrasena.

La recuperacion desde fuera ya existia -- el enlace de un solo uso de Django,
con sus plantillas puestas -- pero no servia para casi nadie, porque ninguna de
las cuatro pantallas de alta pedia correo: un usuario recien creado tenia el
campo vacio, y para llenarlo desde su pantalla de cuenta habia que entrar
primero. Quien olvidaba la contrasena el segundo dia no tenia salida propia.

Se comprueba en las cuatro altas, que son cuatro pantallas distintas y no una:
el usuario de un cliente, los usuarios de la casa, el primer administrador de
una empresa nueva y el usuario de plataforma. El correo es opcional en todas
-- se puede dar acceso a quien no lo tiene a mano -- pero cuando se escribe
tiene que quedar guardado, y cuando esta mal no debe crearse nada.

Lo que se ve -- que el campo quepa en la fila del formulario -- se miro en un
navegador.
"""
from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase

from .models import Catalog, PlatformUser, Tenant, UserProfile


class BaseDelAlta(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')
        cls.admin = User.objects.create_user('admin_tenant', password='x')
        UserProfile.objects.create(user=cls.admin, tenant=cls.tenant, role='admin')
        cls.cliente = Catalog.objects.create(
            tenant=cls.tenant, category='CUSTOMER', name='Ferreteria del Valle',
            contact_email='compras@ferreteria.com')
        cls.plat_admin = User.objects.create_user('plat_admin', password='x')
        PlatformUser.objects.create(user=cls.plat_admin, role='admin')


class ElUsuarioDeUnClienteTests(BaseDelAlta):

    def _alta(self, **extra):
        self.client.force_login(self.admin)
        datos = {'username': 'compras_ana', 'password': 'secreta123'}
        datos.update(extra)
        return self.client.post('/catalog/%d/access/' % self.cliente.pk, datos)

    def test_el_correo_escrito_queda_en_la_cuenta(self):
        self._alta(email='ana@ferreteria.com')

        self.assertEqual(User.objects.get(username='compras_ana').email,
                         'ana@ferreteria.com')

    def test_sin_correo_el_alta_sigue_saliendo(self):
        """
        Opcional de verdad: hay clientes a los que se les da acceso en el
        mostrador y el correo se pone despues.
        """
        self._alta()

        self.assertTrue(User.objects.filter(username='compras_ana').exists())

    def test_pero_se_avisa_de_lo_que_eso_significa(self):
        respuesta = self._alta()

        self.assertIn('recovery email', respuesta.content.decode().lower())

    def test_un_correo_mal_escrito_no_crea_al_usuario(self):
        """
        Se comprueba antes de crear nada. Crear la cuenta y perder el correo
        dejaria justo el estado que esta pantalla vino a evitar, y sin avisar.
        """
        respuesta = self._alta(email='ana(arroba)ferreteria')

        self.assertFalse(User.objects.filter(username='compras_ana').exists())
        self.assertIn('email address', respuesta.content.decode())

    def test_tampoco_uno_que_ya_use_otra_cuenta(self):
        User.objects.create_user('otro', email='ana@ferreteria.com', password='x')

        self._alta(email='ana@ferreteria.com')

        self.assertFalse(User.objects.filter(username='compras_ana').exists())

    def test_se_propone_el_correo_de_la_ficha_del_cliente(self):
        """
        El tenant ya lo capturo al dar de alta al cliente; volver a teclearlo es
        la forma de que quede mal escrito.
        """
        self.client.force_login(self.admin)

        respuesta = self.client.get('/catalog/%d/access/' % self.cliente.pk)

        self.assertIn('compras@ferreteria.com', respuesta.content.decode())

    def test_pero_no_se_propone_si_ya_lo_usa_alguien(self):
        """
        La ficha lleva un correo y sus usuarios pueden ser varios. Proponer el
        mismo al segundo seria proponerle un error, porque el correo de
        recuperacion no se repite entre cuentas.
        """
        User.objects.create_user('primero', email='compras@ferreteria.com',
                                 password='x')
        self.client.force_login(self.admin)

        respuesta = self.client.get('/catalog/%d/access/' % self.cliente.pk)

        self.assertNotIn('value="compras@ferreteria.com"',
                         respuesta.content.decode())


class LosUsuariosDeLaCasaTests(BaseDelAlta):

    def _alta(self, **extra):
        self.client.force_login(self.admin)
        datos = {'action': 'create', 'username': 'bodega_luis',
                 'password': 'secreta123', 'role': 'staff'}
        datos.update(extra)
        return self.client.post('/users/', datos)

    def test_el_correo_escrito_queda_en_la_cuenta(self):
        self._alta(email='luis@almacenes.com')

        self.assertEqual(User.objects.get(username='bodega_luis').email,
                         'luis@almacenes.com')

    def test_sin_correo_el_alta_sigue_saliendo(self):
        self._alta()

        self.assertTrue(User.objects.filter(username='bodega_luis').exists())

    def test_un_correo_mal_escrito_no_crea_al_usuario(self):
        self._alta(email='luis arroba almacenes')

        self.assertFalse(User.objects.filter(username='bodega_luis').exists())


class ElPrimerAdministradorDeUnaEmpresaTests(BaseDelAlta):

    def _alta(self, **extra):
        self.client.force_login(self.plat_admin)
        datos = {'action': 'create', 'name': 'Empresa Nueva', 'plan': 'starter',
                 'admin_username': 'admin_nueva', 'admin_password': 'secreta123'}
        datos.update(extra)
        return self.client.post('/platform/tenants/', datos)

    def test_el_correo_escrito_queda_en_la_cuenta(self):
        self._alta(admin_email='director@empresanueva.com')

        self.assertEqual(User.objects.get(username='admin_nueva').email,
                         'director@empresanueva.com')

    def test_no_es_el_correo_de_facturacion(self):
        """
        Dos campos distintos en la misma pantalla y a proposito: la factura la
        lee contabilidad y el otro abre una cuenta. Guardarlos juntos seria dar
        acceso al sistema a quien solo pidio la factura.
        """
        self._alta(admin_email='director@empresanueva.com',
                   billing_email='pagos@empresanueva.com')

        self.assertEqual(Tenant.objects.get(name='Empresa Nueva').billing_email,
                         'pagos@empresanueva.com')
        self.assertEqual(User.objects.get(username='admin_nueva').email,
                         'director@empresanueva.com')

    def test_un_correo_mal_escrito_no_crea_ni_la_empresa(self):
        """
        Aqui se comprueba antes que en ningun otro sitio: una empresa creada a
        medias hay que borrarla a mano, con su suscripcion y su subdominio
        ocupado.
        """
        self._alta(admin_email='director(arroba)empresanueva')

        self.assertFalse(Tenant.objects.filter(name='Empresa Nueva').exists())
        self.assertFalse(User.objects.filter(username='admin_nueva').exists())


class ElUsuarioDePlataformaTests(BaseDelAlta):

    def _alta(self, **extra):
        self.client.force_login(self.plat_admin)
        datos = {'action': 'create', 'username': 'soporte_ana',
                 'password': 'secreta123', 'role': 'staff'}
        datos.update(extra)
        return self.client.post('/platform/users/', datos)

    def test_el_correo_escrito_queda_en_la_cuenta(self):
        self._alta(email='ana@plataforma.com')

        self.assertEqual(User.objects.get(username='soporte_ana').email,
                         'ana@plataforma.com')

    def test_sin_correo_se_avisa_de_que_nadie_puede_reasignarla(self):
        """
        Por encima de un administrador de plataforma no hay nadie. El aviso
        dice eso y no el generico, porque aqui la consecuencia es entrar al
        servidor.
        """
        respuesta = self._alta(role='admin')

        self.assertIn('no level above it', respuesta.content.decode())

    def test_un_correo_mal_escrito_no_crea_al_usuario(self):
        self._alta(email='ana arroba plataforma')

        self.assertFalse(User.objects.filter(username='soporte_ana').exists())


class LaRecuperacionYaLlegaTests(BaseDelAlta):
    """
    Lo que justifica todo lo anterior: con el correo puesto desde el alta, el
    enlace de "olvide mi contrasena" sale de verdad. Antes no salia -- Django
    calla cuando ningun usuario tiene ese correo -- y la pantalla decia lo
    mismo en los dos casos, asi que el fallo era invisible.
    """

    def test_el_enlace_sale_para_un_usuario_creado_con_correo(self):
        self.client.force_login(self.admin)
        self.client.post('/catalog/%d/access/' % self.cliente.pk, {
            'username': 'compras_ana', 'password': 'secreta123',
            'email': 'ana@ferreteria.com'})
        self.client.logout()

        self.client.post('/password/reset/', {'email': 'ana@ferreteria.com'})

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['ana@ferreteria.com'])
