"""
Dos huecos de permisos dentro de una misma empresa.

No son de aislamiento entre tenants -eso se cubre en `tests_isolation`- sino de
quien puede que dentro del mismo tenant. Los dos comparten la misma forma: la
interfaz no ofrecia la accion, pero la vista la aceptaba igual, y un formulario
se puede mandar a mano.
"""
import io

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from .models import Catalog, Tenant, UserProfile


class BaseEmpresa(TestCase):
    """
    Una empresa con un usuario de cada nivel, para cruzar quien hace que.
    """

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')

        def usuario(nombre, rol, **extra):
            u = User.objects.create_user(nombre, password='x', **extra)
            UserProfile.objects.create(user=u, tenant=cls.tenant, role=rol,
                                       delete_password='borrar123')
            return u

        cls.admin      = usuario('admin_tenant', 'admin')
        cls.manager    = usuario('manager', 'manager')
        cls.staff      = usuario('staff', 'staff')
        cls.superadmin = usuario('superadmin', 'superadmin')


class CatalogoPorCategoriaTests(BaseEmpresa):
    """
    Dar de alta un cliente y dar de alta un carrier eran la misma operacion con
    un valor distinto en el desplegable, asi que cualquiera que pudiera mantener
    el catalogo operativo podia crear clientes. Un cliente decide a quien se le
    mandan los avisos y quien puede tener acceso, de modo que es cosa del
    administrador de la empresa.
    """

    def _alta(self, usuario, category, name='Nueva entrada'):
        self.client.force_login(usuario)
        return self.client.post('/catalog/create/',
                                {'category': category, 'name': name})

    def test_el_manager_no_puede_crear_un_cliente(self):
        respuesta = self._alta(self.manager, 'CUSTOMER', 'Cliente colado')

        self.assertEqual(respuesta.status_code, 403)
        self.assertFalse(Catalog.objects.filter(name='Cliente colado').exists())

    def test_el_staff_tampoco(self):
        respuesta = self._alta(self.staff, 'CUSTOMER', 'Cliente colado')

        self.assertEqual(respuesta.status_code, 403)
        self.assertFalse(Catalog.objects.filter(name='Cliente colado').exists())

    def test_el_admin_si(self):
        self._alta(self.admin, 'CUSTOMER', 'Cliente legitimo')

        self.assertTrue(Catalog.objects.filter(name='Cliente legitimo').exists())

    def test_el_manager_conserva_el_catalogo_operativo(self):
        """
        Lo que se acota es la categoria de clientes, no el catalogo entero: los
        carriers, shippers y tipos de bulto son trabajo diario de quien captura.
        """
        for categoria in ('SHIPPER', 'CARRIER', 'BUNDLE_TYPE', 'TYPE_OP'):
            with self.subTest(categoria=categoria):
                self._alta(self.manager, categoria, f'Operativo {categoria}')
                self.assertTrue(
                    Catalog.objects.filter(name=f'Operativo {categoria}').exists())

    def test_el_manager_no_puede_editar_un_cliente(self):
        cliente = Catalog.objects.create(
            tenant=self.tenant, category='CUSTOMER', name='Cliente',
            contact_email='avisos@cliente.com')
        self.client.force_login(self.manager)

        respuesta = self.client.post(f'/catalog/{cliente.pk}/edit/',
                                     {'name': 'Cliente', 'contact_email': 'otro@ladron.com'})

        self.assertEqual(respuesta.status_code, 403)
        cliente.refresh_from_db()
        self.assertEqual(cliente.contact_email, 'avisos@cliente.com')

    def test_el_manager_no_puede_dar_de_baja_un_cliente(self):
        cliente = Catalog.objects.create(
            tenant=self.tenant, category='CUSTOMER', name='Cliente')
        self.client.force_login(self.manager)

        respuesta = self.client.post(f'/catalog/{cliente.pk}/delete/')

        self.assertEqual(respuesta.status_code, 403)
        cliente.refresh_from_db()
        self.assertTrue(cliente.active)


class ImportacionDelCatalogoTests(BaseEmpresa):
    """
    El Excel es la puerta de atras del catalogo.

    `catalog_import` creaba cualquier categoria que viniera escrita en el
    archivo, asi que acotar el formulario y dejar la importacion abierta habria
    sido un cierre de mentira: basta con escribir CUSTOMER en una celda.
    """

    def _excel(self, filas):
        """
        Un libro con las dos filas de encabezado que la vista se salta.
        """
        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(['Category', 'Name', 'Email', 'Phone', 'WhatsApp', 'Address', 'Notes'])
        ws.append([])
        for fila in filas:
            ws.append(fila)
        buf = io.BytesIO()
        wb.save(buf)
        return SimpleUploadedFile(
            'catalogo.xlsx', buf.getvalue(),
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    def _importar(self, usuario, filas):
        self.client.force_login(usuario)
        return self.client.post('/catalog/import/',
                                {'import_file': self._excel(filas)})

    def test_el_manager_no_cuela_clientes_por_el_excel(self):
        self._importar(self.manager, [
            ['CUSTOMER', 'Cliente colado', '', '', '', '', ''],
        ])

        self.assertFalse(Catalog.objects.filter(name='Cliente colado').exists())

    def test_las_demas_filas_del_mismo_archivo_si_entran(self):
        """
        Una fila rechazada no puede tumbar la importacion entera: el operador
        sube un archivo con todo mezclado y lo que puede crear, se crea.
        """
        self._importar(self.manager, [
            ['CUSTOMER', 'Cliente colado', '', '', '', '', ''],
            ['CARRIER', 'Transportes del Norte', '', '', '', '', ''],
        ])

        self.assertFalse(Catalog.objects.filter(name='Cliente colado').exists())
        self.assertTrue(Catalog.objects.filter(name='Transportes del Norte').exists())

    def test_el_admin_si_importa_clientes(self):
        self._importar(self.admin, [
            ['CUSTOMER', 'Cliente legitimo', 'avisos@cliente.com', '', '', '', ''],
        ])

        self.assertTrue(Catalog.objects.filter(name='Cliente legitimo').exists())


class EscaladaDeRolesTests(BaseEmpresa):
    """
    La gestion de usuarios tomaba el rol del formulario y lo guardaba tal cual,
    asi que un administrador podia nombrar un 'superadmin' -el nivel mas alto
    dentro de la empresa- y quedar por debajo de alguien a quien acababa de
    crear.

    Validar solo el rol repartido dejaba la puerta entornada, porque la misma
    pantalla cambia contrasenas: sin comprobar tambien sobre quien se actua, un
    administrador le cambiaba la contrasena al superadmin que ya hubiera y
    entraba como el.
    """

    def _post(self, usuario, datos):
        self.client.force_login(usuario)
        return self.client.post('/users/', datos)

    def test_la_gestion_de_usuarios_exige_sesion(self):
        """
        Al meter un ayudante justo encima de la vista, `@login_required` acabo
        decorando al ayudante y la vista quedo sin el. Lo detecto una prueba de
        otra cosa; esta lo fija.
        """
        respuesta = self.client.post('/users/', {'action': 'create'})

        self.assertIn(respuesta.status_code, (302, 403))

    def test_el_manager_no_entra_a_la_gestion_de_usuarios(self):
        respuesta = self._post(self.manager, {'action': 'create',
                                              'username': 'colado',
                                              'password': 'secreta123',
                                              'role': 'staff'})

        self.assertEqual(respuesta.status_code, 403)
        self.assertFalse(User.objects.filter(username='colado').exists())

    def test_el_admin_no_puede_crear_un_superadmin(self):
        self._post(self.admin, {'action': 'create', 'username': 'colado',
                                'password': 'secreta123', 'role': 'superadmin'})

        self.assertFalse(User.objects.filter(username='colado').exists())

    def test_el_admin_puede_crear_hasta_su_propio_nivel(self):
        self._post(self.admin, {'action': 'create', 'username': 'otro_admin',
                                'password': 'secreta123', 'role': 'admin'})

        creado = User.objects.get(username='otro_admin')
        self.assertEqual(creado.profile.role, 'admin')

    def test_el_admin_no_puede_ascender_a_nadie_a_superadmin(self):
        self._post(self.admin, {'action': 'update_role',
                                'user_id': self.staff.pk, 'role': 'superadmin'})

        self.staff.profile.refresh_from_db()
        self.assertEqual(self.staff.profile.role, 'staff')

    def test_el_admin_no_puede_cambiarle_la_contrasena_al_superadmin(self):
        """
        Es la toma de control por la puerta de al lado: si se puede cambiar la
        contrasena de alguien de mas nivel, da igual no poder nombrarlo.
        """
        anterior = User.objects.get(pk=self.superadmin.pk).password

        self._post(self.admin, {'action': 'change_password',
                                'user_id': self.superadmin.pk,
                                'new_password': 'tomada123'})

        self.assertEqual(User.objects.get(pk=self.superadmin.pk).password, anterior)

    def test_el_admin_no_puede_borrar_al_superadmin(self):
        self._post(self.admin, {'action': 'delete', 'user_id': self.superadmin.pk})

        self.assertTrue(User.objects.filter(pk=self.superadmin.pk).exists())

    def test_el_admin_si_gestiona_a_los_de_menor_nivel(self):
        """El cierre no puede estorbar el trabajo normal del administrador."""
        anterior = User.objects.get(pk=self.staff.pk).password

        self._post(self.admin, {'action': 'change_password',
                                'user_id': self.staff.pk,
                                'new_password': 'nueva123456'})

        self.assertNotEqual(User.objects.get(pk=self.staff.pk).password, anterior)

    def test_el_superadmin_sigue_pudiendo_con_todo(self):
        self._post(self.superadmin, {'action': 'create', 'username': 'otro_super',
                                     'password': 'secreta123', 'role': 'superadmin'})

        creado = User.objects.get(username='otro_super')
        self.assertEqual(creado.profile.role, 'superadmin')

    def test_un_rol_inventado_no_pasa(self):
        self._post(self.superadmin, {'action': 'create', 'username': 'raro',
                                     'password': 'secreta123', 'role': 'dueno'})

        self.assertFalse(User.objects.filter(username='raro').exists())
