"""
El `is_superuser` de Django deja de repartir permisos por su cuenta.

Ese flag daba tres cosas a la vez: el admin de Django, el panel de plataforma y
el rol mas alto dentro de la empresa a la que perteneciera la persona. Lo
tercero es lo que se retira aqui: los permisos de empresa salen del rol escrito
en el perfil y de nada mas, de modo que los niveles 1 y 2 dejan de ser la misma
persona por construccion.

Para el panel de plataforma la llave se conserva, pero solo mientras no haya un
administrador de plataforma de verdad: en cuanto existe el primero, deja de
abrir. Esa condicion es la que permite crear al primero sin dejar a nadie fuera,
y la que evita que la llave se quede puesta para siempre.
"""
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from .models import ROLE_RANK, PlatformUser, Tenant, UserProfile
from .views import get_profile, platform_role


class LosPermisosDeEmpresaSalenDelRolTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')
        cls.root = User.objects.create_superuser('root', password='x')
        cls.perfil = UserProfile.objects.create(
            user=cls.root, tenant=cls.tenant, role='staff')

    def test_un_superusuario_con_rol_staff_es_staff_y_nada_mas(self):
        self.assertFalse(self.perfil.is_superadmin())
        self.assertFalse(self.perfil.is_home())
        self.assertFalse(self.perfil.can_manage_users())
        self.assertFalse(self.perfil.can_see_deletion_log())
        self.assertFalse(self.perfil.can_purge_documents())

        # Lo que si tiene por ser staff sigue estando.
        self.assertTrue(self.perfil.is_operator())
        self.assertTrue(self.perfil.can_edit_operations())

    def test_no_puede_repartir_roles_por_encima_del_suyo(self):
        self.assertFalse(self.perfil.can_assign_role('superadmin'))
        self.assertFalse(self.perfil.can_assign_role('admin'))

    def test_el_rango_sale_del_rol_y_no_del_flag(self):
        """
        `role_rank` coronaba al superusuario, de modo que un admin con el flag
        podia nombrar superadmins y quedar por debajo de quien acababa de crear.
        Aqui si llega a comprobarse: un admin pasa can_manage_users(), asi que
        `can_assign_role` no se corta antes de mirar el rango.
        """
        jefe = User.objects.create_superuser('root_admin', password='x')
        perfil = UserProfile.objects.create(
            user=jefe, tenant=self.tenant, role='admin')

        self.assertEqual(perfil.role_rank(), ROLE_RANK['admin'])
        self.assertTrue(perfil.can_manage_users())
        self.assertTrue(perfil.can_assign_role('admin'))
        self.assertFalse(perfil.can_assign_role('superadmin'))

    def test_un_superusuario_con_rol_customer_sigue_siendo_cliente(self):
        cliente = User.objects.create_superuser('root_cliente', password='x')
        perfil = UserProfile.objects.create(
            user=cliente, tenant=self.tenant, role='customer')

        self.assertTrue(perfil.is_customer())
        self.assertFalse(perfil.is_home())
        self.assertFalse(perfil.is_operator())

    def test_la_pantalla_de_usuarios_le_cierra_la_puerta(self):
        self.client.force_login(self.root)

        self.assertEqual(self.client.get('/users/').status_code, 403)


class SinPerfilNoHayRolTests(TestCase):
    """
    `get_profile` fabricaba el perfil que faltaba: 'superadmin' si era
    superusuario y 'manager' para cualquier otro. Lo segundo era lo grave —
    bastaba existir en auth_user para quedar de manager en una empresa — y
    ademas quedaba escrito en la base como si alguien lo hubiera decidido.
    """

    def test_no_crea_ninguna_fila(self):
        usuario = User.objects.create_user('sin_perfil', password='x')

        get_profile(usuario)

        self.assertFalse(UserProfile.objects.filter(user=usuario).exists())

    def test_el_perfil_vacio_no_da_ningun_permiso(self):
        usuario = User.objects.create_user('sin_perfil', password='x')

        perfil = get_profile(usuario)

        self.assertFalse(perfil.is_home())
        self.assertFalse(perfil.is_operator())
        self.assertFalse(perfil.can_manage_users())
        self.assertFalse(perfil.can_delete())
        self.assertEqual(perfil.role_rank(), 0)

    def test_tampoco_al_superusuario(self):
        root = User.objects.create_superuser('root', password='x')

        perfil = get_profile(root)

        self.assertFalse(perfil.is_superadmin())
        self.assertFalse(perfil.is_home())
        self.assertFalse(UserProfile.objects.filter(user=root).exists())


class LaLlaveMaestraCedeAnteElSucesorTests(TestCase):

    def setUp(self):
        self.root = User.objects.create_superuser('root', password='x')

    def test_mientras_no_haya_admin_de_plataforma_el_superusuario_entra(self):
        self.assertEqual(platform_role(self.root), 'admin')

    def test_en_cuanto_existe_uno_la_llave_deja_de_abrir(self):
        ana = User.objects.create_user('ana', password='x')
        PlatformUser.objects.create(user=ana, role='admin')

        self.assertEqual(platform_role(ana), 'admin')
        self.assertIsNone(platform_role(self.root))

    def test_un_admin_de_plataforma_staff_no_basta_para_retirarla(self):
        """
        El soporte mira y no toca, asi que no puede suceder a la llave: si solo
        hay staff, todavia no hay quien de de alta una empresa.
        """
        soporte = User.objects.create_user('soporte', password='x')
        PlatformUser.objects.create(user=soporte, role='staff')

        self.assertEqual(platform_role(self.root), 'admin')

    def test_el_acceso_escrito_manda_sobre_el_flag(self):
        """
        Un superusuario con acceso de soporte es soporte, no administrador: lo
        que dice el modelo pesa mas que el flag heredado.
        """
        PlatformUser.objects.create(user=self.root, role='staff')

        self.assertEqual(platform_role(self.root), 'staff')

    def test_el_panel_de_plataforma_se_cierra_con_la_llave(self):
        ana = User.objects.create_user('ana', password='x')
        PlatformUser.objects.create(user=ana, role='admin')

        self.client.force_login(self.root)

        self.assertEqual(self.client.get('/platform/').status_code, 403)


class ComandoDeRetiradaTests(TestCase):

    def setUp(self):
        self.root = User.objects.create_superuser('root', password='x')

    def _correr(self, *args):
        salida = StringIO()
        call_command('retirar_superusuario', *args, stdout=salida, stderr=salida)
        return salida.getvalue()

    def test_se_niega_si_no_queda_quien_administre_la_plataforma(self):
        with self.assertRaises(CommandError):
            self._correr('root')

        self.root.refresh_from_db()
        self.assertTrue(self.root.is_superuser)

    def test_con_un_sucesor_retira_el_flag_y_el_acceso_al_admin_de_django(self):
        ana = User.objects.create_user('ana', password='x')
        PlatformUser.objects.create(user=ana, role='admin')

        self._correr('root')

        self.root.refresh_from_db()
        self.assertFalse(self.root.is_superuser)
        self.assertFalse(self.root.is_staff)

    def test_puede_conservarse_el_acceso_al_admin_de_django(self):
        ana = User.objects.create_user('ana', password='x')
        PlatformUser.objects.create(user=ana, role='admin')

        self._correr('root', '--conservar-admin-django')

        self.root.refresh_from_db()
        self.assertFalse(self.root.is_superuser)
        self.assertTrue(self.root.is_staff)

    def test_force_retira_el_flag_aunque_no_haya_sucesor(self):
        self._correr('root', '--force')

        self.root.refresh_from_db()
        self.assertFalse(self.root.is_superuser)

    def test_el_informe_no_toca_nada_y_dice_quien_es_quien(self):
        tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')
        UserProfile.objects.create(user=self.root, tenant=tenant, role='admin')

        salida = self._correr()

        self.root.refresh_from_db()
        self.assertTrue(self.root.is_superuser)
        self.assertIn('root', salida)
        self.assertIn('Almacenes del Norte', salida)

    def test_no_retira_a_quien_no_es_superusuario(self):
        User.objects.create_user('normal', password='x')

        with self.assertRaises(CommandError):
            self._correr('normal')


class LaMigracionConservaElRolQueYaEjercianTests(TestCase):
    """
    Sin ella, el deploy degrada de golpe a quien tuviera el flag y un rol menor
    escrito en el perfil: hasta ese momento mandaba, y de repente no.
    """

    def _correr_la_migracion(self):
        import importlib

        from django.apps import apps as apps_reales

        modulo = importlib.import_module(
            'warehouse.migrations.0016_superusuarios_con_rol_propio')
        modulo.escribir_el_rol_que_ya_ejercian(apps_reales, None)

    def setUp(self):
        self.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')

    def _perfil(self, username, role, superusuario):
        crear = User.objects.create_superuser if superusuario else User.objects.create_user
        usuario = crear(username, password='x')
        return UserProfile.objects.create(
            user=usuario, tenant=self.tenant, role=role)

    def test_al_superusuario_con_rol_menor_se_le_escribe_superadmin(self):
        perfil = self._perfil('root_manager', 'manager', superusuario=True)

        self._correr_la_migracion()

        perfil.refresh_from_db()
        self.assertEqual(perfil.role, 'superadmin')

    def test_no_toca_a_quien_no_es_superusuario(self):
        perfil = self._perfil('un_manager', 'manager', superusuario=False)

        self._correr_la_migracion()

        perfil.refresh_from_db()
        self.assertEqual(perfil.role, 'manager')

    def test_no_asciende_a_un_cliente(self):
        """
        Un cliente que ademas sea superusuario es un error de datos, no un
        permiso que valga la pena conservar.
        """
        perfil = self._perfil('root_cliente', 'customer', superusuario=True)

        self._correr_la_migracion()

        perfil.refresh_from_db()
        self.assertEqual(perfil.role, 'customer')

    def test_no_inventa_perfiles(self):
        User.objects.create_superuser('root_sin_perfil', password='x')

        self._correr_la_migracion()

        self.assertFalse(
            UserProfile.objects.filter(user__username='root_sin_perfil').exists())
