"""
El nivel de plataforma: quien administra el SaaS, no una empresa.

Hasta ahora ese nivel tenia una sola llave, el `is_superuser` de Django, asi que
un equipo de soporte era imposible: o se daba acceso total -el admin de Django y
los datos de todas las empresas- o no se daba ninguno.

Lo que se comprueba aqui es la separacion en los dos sentidos. Que un usuario de
plataforma no alcanza los datos de ninguna empresa, y que un administrador de
empresa no alcanza la plataforma. Y dentro del nivel, que el soporte mira pero
no toca.
"""
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from .models import (Catalog, PlatformUser, Tenant, UserProfile,
                     WarehouseOperation)


class BasePlataforma(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')

        # Nivel de plataforma: sin empresa, a proposito.
        cls.plat_admin = User.objects.create_user('plat_admin', password='x')
        PlatformUser.objects.create(user=cls.plat_admin, role='admin')
        cls.plat_staff = User.objects.create_user('plat_staff', password='x')
        PlatformUser.objects.create(user=cls.plat_staff, role='staff')

        # Nivel de empresa.
        cls.admin_empresa = User.objects.create_user('admin_tenant', password='x')
        UserProfile.objects.create(user=cls.admin_empresa, tenant=cls.tenant,
                                   role='admin', delete_password='borrar123')

        # La llave maestra que sigue existiendo mientras no se retire a mano.
        cls.superusuario = User.objects.create_superuser('root', password='x')


class SeparacionDeLosDosNivelesTests(BasePlataforma):

    def test_el_admin_de_empresa_no_entra_a_la_plataforma(self):
        """
        Administrar una empresa no da ningun derecho sobre el producto: es el
        sentido de la separacion que faltaba.
        """
        self.client.force_login(self.admin_empresa)

        self.assertEqual(self.client.get('/platform/').status_code, 403)
        self.assertEqual(self.client.get('/platform/tenants/').status_code, 403)
        self.assertEqual(self.client.get('/platform/users/').status_code, 403)

    def test_el_usuario_de_plataforma_no_alcanza_los_datos_de_una_empresa(self):
        """
        No tiene tenant, y esa ausencia es la garantia: el tablero y todo lo que
        cuelga de el pasan por get_tenant_or_404.
        """
        WarehouseOperation.objects.create(
            tenant=self.tenant, operation_type='ENTRY', custom_id='ED260818-0001',
            description='Mercancia de prueba')
        self.client.force_login(self.plat_admin)

        self.assertEqual(self.client.get('/dashboard/').status_code, 404)
        self.assertEqual(self.client.get('/catalog/list/').status_code, 404)
        self.assertEqual(self.client.get('/users/').status_code, 404)

    def test_el_anonimo_no_entra(self):
        respuesta = self.client.get('/platform/')

        self.assertIn(respuesta.status_code, (302, 403))

    def test_el_usuario_de_plataforma_no_es_superusuario_de_django(self):
        """
        Es la mitad que hacia imposible un equipo: dar plataforma no puede
        significar dar el admin de Django.
        """
        self.assertFalse(self.plat_admin.is_superuser)
        self.assertFalse(self.plat_staff.is_superuser)


class DondeAterrizaCadaUnoTests(BasePlataforma):

    def test_el_usuario_de_plataforma_entra_a_su_pantalla(self):
        respuesta = self.client.post('/', {'username': 'plat_staff', 'password': 'x'})

        self.assertRedirects(respuesta, '/platform/')

    def test_el_de_una_empresa_entra_a_su_tablero(self):
        respuesta = self.client.post('/', {'username': 'admin_tenant', 'password': 'x'})

        self.assertRedirects(respuesta, '/dashboard/', target_status_code=200)

    def test_quien_tiene_los_dos_niveles_va_al_tablero(self):
        """
        Es la situacion de hoy con el superusuario: administra su empresa y
        ademas la plataforma. Se le manda a su tablero, y la plataforma le queda
        en la pestana.
        """
        UserProfile.objects.create(user=self.superusuario, tenant=self.tenant,
                                   role='admin')

        respuesta = self.client.post('/', {'username': 'root', 'password': 'x'})

        self.assertRedirects(respuesta, '/dashboard/', target_status_code=200)


class SoporteMiraPeroNoTocaTests(BasePlataforma):

    def test_el_soporte_ve_las_empresas(self):
        self.client.force_login(self.plat_staff)

        respuesta = self.client.get('/platform/tenants/')

        self.assertEqual(respuesta.status_code, 200)
        self.assertIn('Almacenes del Norte', respuesta.content.decode())

    def test_pero_no_puede_crear_una_empresa(self):
        self.client.force_login(self.plat_staff)

        respuesta = self.client.post('/platform/tenants/', {
            'action': 'create', 'name': 'Empresa colada', 'plan': 'starter'})

        self.assertEqual(respuesta.status_code, 403)
        self.assertFalse(Tenant.objects.filter(name='Empresa colada').exists())

    def test_ni_desactivar_una(self):
        self.client.force_login(self.plat_staff)

        respuesta = self.client.post('/platform/tenants/', {
            'action': 'toggle_active', 'tenant_id': self.tenant.pk})

        self.assertEqual(respuesta.status_code, 403)
        self.tenant.refresh_from_db()
        self.assertTrue(self.tenant.is_active)

    def test_ni_repartir_acceso_de_plataforma(self):
        """Lo mas critico del panel: quien lo tiene nombra administradores de
        empresa."""
        self.client.force_login(self.plat_staff)

        respuesta = self.client.post('/platform/users/', {
            'action': 'create', 'username': 'colado',
            'password': 'secreta123', 'role': 'admin'})

        self.assertEqual(respuesta.status_code, 403)
        self.assertFalse(User.objects.filter(username='colado').exists())

    def test_el_soporte_si_consulta_la_bitacora_de_envios(self):
        """Es su trabajo diario: 'a este cliente no le llegan los correos'."""
        self.client.force_login(self.plat_staff)

        self.assertEqual(self.client.get('/platform/notifications/').status_code, 200)

    def test_el_admin_de_plataforma_si_crea_empresas(self):
        self.client.force_login(self.plat_admin)

        self.client.post('/platform/tenants/', {
            'action': 'create', 'name': 'Empresa Nueva', 'plan': 'starter'})

        self.assertTrue(Tenant.objects.filter(name='Empresa Nueva').exists())


class GestionDeUsuariosDePlataformaTests(BasePlataforma):

    def _post(self, datos):
        self.client.force_login(self.plat_admin)
        return self.client.post('/platform/users/', datos)

    def test_crea_un_usuario_de_soporte(self):
        self._post({'action': 'create', 'username': 'soporte_ana',
                    'password': 'secreta123', 'role': 'staff'})

        creado = User.objects.get(username='soporte_ana')
        self.assertEqual(creado.platform_access.role, 'staff')

    def test_el_usuario_creado_no_pertenece_a_ninguna_empresa(self):
        """
        Es lo que lo mantiene fuera de los datos de los clientes. Si se le
        creara un perfil con tenant, el 404 que lo protege desapareceria.
        """
        self._post({'action': 'create', 'username': 'soporte_ana',
                    'password': 'secreta123', 'role': 'staff'})

        creado = User.objects.get(username='soporte_ana')
        self.assertFalse(UserProfile.objects.filter(user=creado).exists())
        self.assertFalse(creado.is_superuser)

    def test_no_acepta_un_nivel_inventado(self):
        self._post({'action': 'create', 'username': 'raro',
                    'password': 'secreta123', 'role': 'dueno'})

        self.assertFalse(User.objects.filter(username='raro').exists())

    def test_no_puede_quitarse_a_si_mismo_el_acceso(self):
        """
        Si el ultimo administrador se revoca, el panel se queda sin nadie que
        pueda volver a repartir acceso.
        """
        propio = PlatformUser.objects.get(user=self.plat_admin)

        self._post({'action': 'revoke', 'platform_user_id': propio.pk})

        self.assertTrue(PlatformUser.objects.filter(pk=propio.pk).exists())

    def test_no_puede_degradarse_a_si_mismo(self):
        propio = PlatformUser.objects.get(user=self.plat_admin)

        self._post({'action': 'update_role', 'platform_user_id': propio.pk,
                    'role': 'staff'})

        propio.refresh_from_db()
        self.assertEqual(propio.role, 'admin')

    def test_revocar_retira_el_acceso_pero_conserva_la_persona(self):
        acceso = PlatformUser.objects.get(user=self.plat_staff)

        self._post({'action': 'revoke', 'platform_user_id': acceso.pk})

        self.assertFalse(PlatformUser.objects.filter(pk=acceso.pk).exists())
        self.assertTrue(User.objects.filter(username='plat_staff').exists())


class ComandoDeAltaTests(TestCase):
    """
    El camino que no pasa por la interfaz. Hace falta por el problema del huevo
    y la gallina: la pantalla que reparte este acceso solo la ve quien ya lo
    tiene, asi que sin comando no habria forma de volver a entrar una vez
    retirado el `is_superuser`.
    """

    def test_crea_el_usuario_y_le_da_el_acceso(self):
        call_command('create_platform_user', 'ana', role='admin', password='secreta123')

        usuario = User.objects.get(username='ana')
        self.assertEqual(usuario.platform_access.role, 'admin')
        self.assertFalse(usuario.is_superuser)

    def test_sobre_un_usuario_que_ya_existe_no_pide_contrasena(self):
        User.objects.create_user('ana', password='la_suya')

        call_command('create_platform_user', 'ana', role='staff')

        self.assertEqual(User.objects.get(username='ana').platform_access.role, 'staff')

    def test_sin_contrasena_y_sin_usuario_previo_falla(self):
        with self.assertRaises(CommandError):
            call_command('create_platform_user', 'nadie', role='staff')

        self.assertFalse(User.objects.filter(username='nadie').exists())

    def test_repetirlo_cambia_el_nivel_en_vez_de_duplicar(self):
        call_command('create_platform_user', 'ana', role='staff', password='secreta123')
        call_command('create_platform_user', 'ana', role='admin')

        self.assertEqual(PlatformUser.objects.filter(user__username='ana').count(), 1)
        self.assertEqual(User.objects.get(username='ana').platform_access.role, 'admin')
