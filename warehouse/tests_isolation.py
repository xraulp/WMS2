"""
Aislamiento entre empresas (Tenant nivel 1) en las vistas que reciben un pk.

Todas estas pruebas comparten la misma forma: se montan dos tenants completos y
se intenta alcanzar un objeto del segundo desde una sesion del primero, pasando
su pk a pelo. Es el ataque realista, porque los pk van visibles en las URL y en
los formularios; basta cambiar un numero.

Se cubre tambien el borrado de archivos del storage, que no es aislamiento pero
sale del mismo error de fondo: `FieldFile.path` no existe cuando los archivos
viven en R2, y los tres sitios que lo usaban fallaban en silencio.
"""
import tempfile
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings

from .middleware import TenantMiddleware
from .models import (PREFIJO_PAPELERA, Catalog, OperationDocument, Tenant,
                     UserProfile, WarehouseOperation)

STORAGE_LOCAL = override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage',
                'OPTIONS': {'location': tempfile.mkdtemp()}},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})


class DosTenantsTestBase(TestCase):
    """
    Dos empresas montadas igual, para poder cruzar pk de una a la otra.

    'uno' es desde donde se ataca; 'dos' es la victima.
    """

    @classmethod
    def setUpTestData(cls):
        cls.tenant_uno = Tenant.objects.create(
            name='Warehouse Uno', type='organization', subdomain='uno')
        cls.tenant_dos = Tenant.objects.create(
            name='Warehouse Dos', type='organization', subdomain='dos')

        cls.cliente_uno = Catalog.objects.create(
            tenant=cls.tenant_uno, category='CUSTOMER', name='Cliente de Uno',
            contact_email='compras@cliente-uno.com')
        cls.cliente_dos = Catalog.objects.create(
            tenant=cls.tenant_dos, category='CUSTOMER', name='Cliente de Dos',
            contact_email='compras@cliente-dos.com')

        cls.manager_uno = User.objects.create_user('manager_uno', password='x')
        UserProfile.objects.create(user=cls.manager_uno, tenant=cls.tenant_uno,
                                   role='manager', delete_password='borrar123')

        cls.manager_dos = User.objects.create_user('manager_dos', password='x')
        UserProfile.objects.create(user=cls.manager_dos, tenant=cls.tenant_dos,
                                   role='manager', delete_password='borrar456')

    def _operacion(self, tenant, customer=None, custom_id='ED260817-0001',
                   created_by=None):
        return WarehouseOperation.objects.create(
            tenant=tenant, operation_type='ENTRY', custom_id=custom_id,
            customer=customer, description='Mercancia de prueba',
            created_by=created_by)


class AltaDeOperacionTests(DosTenantsTestBase):
    """
    El alta resolvia los catalogos por pk sin acotar por tenant.

    Lo que estaba en juego no era solo el dato: el aviso de alta se manda al
    correo del `Catalog` que quede en la operacion, asi que una operacion de una
    empresa podia terminar avisandole al cliente de otra.
    """

    def setUp(self):
        self.client.force_login(self.manager_uno)
        self.shipper = Catalog.objects.create(
            tenant=self.tenant_uno, category='SHIPPER', name='Shipper de Uno')
        self.carrier = Catalog.objects.create(
            tenant=self.tenant_uno, category='CARRIER', name='Carrier de Uno')
        self.bulto = Catalog.objects.create(
            tenant=self.tenant_uno, category='BUNDLE_TYPE', name='Tarima')

    def _alta(self, **extra):
        """
        Alta con todos los campos obligatorios resueltos, para que lo unico que
        cambie de una prueba a otra sea el pk que se esta probando.
        """
        datos = {
            'operation_type': 'ENTRY', 'date': '2026-08-17',
            'customer_id': str(self.cliente_uno.pk),
            'shipper_id': str(self.shipper.pk),
            'carrier_id': str(self.carrier.pk),
            'bundle_type_id': str(self.bulto.pk),
            'bundle_qty': '3', 'weight_lbs': '150',
            'description': 'Mercancia de prueba',
        }
        datos.update(extra)
        return self.client.post('/operations/create/', datos)

    def test_rechaza_un_cliente_de_otra_empresa(self):
        """
        Sin cliente propio ni nombre a mano el alta no procede, asi que un pk
        ajeno tiene que acabar en el error de campo obligatorio y no en una
        operacion creada.
        """
        respuesta = self._alta(customer_id=str(self.cliente_dos.pk))

        self.assertEqual(respuesta.status_code, 422)
        self.assertIn('Customer', respuesta.content.decode())
        self.assertEqual(WarehouseOperation.objects.count(), 0)

    def test_no_deja_la_operacion_apuntando_al_catalogo_ajeno(self):
        """
        Con el nombre escrito a mano el alta si procede; lo que no puede pasar es
        que el pk ajeno quede guardado en el campo `customer`.
        """
        self._alta(customer_id=str(self.cliente_dos.pk),
                   customer_text='Cliente escrito a mano')

        op = WarehouseOperation.objects.get()
        self.assertIsNone(op.customer_id)
        self.assertEqual(op.customer_name_manual, 'Cliente escrito a mano')

    def test_acepta_el_cliente_propio(self):
        """El filtro nuevo no puede estorbar el caso normal."""
        self._alta()

        op = WarehouseOperation.objects.get()
        self.assertEqual(op.customer_id, self.cliente_uno.pk)
        self.assertEqual(op.shipper_id, self.shipper.pk)
        self.assertEqual(op.bundle_type_id, self.bulto.pk)

    def test_no_toma_un_shipper_ni_un_carrier_de_otra_empresa(self):
        shipper_ajeno = Catalog.objects.create(
            tenant=self.tenant_dos, category='SHIPPER', name='Shipper de Dos')
        carrier_ajeno = Catalog.objects.create(
            tenant=self.tenant_dos, category='CARRIER', name='Carrier de Dos')

        self._alta(shipper_id=str(shipper_ajeno.pk), shipper_text='A mano',
                   carrier_id=str(carrier_ajeno.pk), carrier_text='A mano')

        op = WarehouseOperation.objects.get()
        self.assertIsNone(op.shipper_id)
        self.assertIsNone(op.carrier_id)


@STORAGE_LOCAL
class BorradoDeDocumentosTests(DosTenantsTestBase):
    """
    Borrar un documento del expediente.

    `digital_delete_file` acotaba por tenant en la rama de contraseña incorrecta
    pero no en la de contraseña correcta, o sea justo en la que borra.
    """

    def setUp(self):
        self.op_dos = self._operacion(self.tenant_dos, self.cliente_dos)
        self.doc_dos = OperationDocument.objects.create(
            tenant=self.tenant_dos, operation=self.op_dos, file_type='DOCUMENT',
            file=SimpleUploadedFile('ajeno.pdf', b'%PDF-1.4 ajeno'),
            original_name='ajeno.pdf', digital_name='170826-1')

        self.op_uno = self._operacion(self.tenant_uno, self.cliente_uno,
                                      custom_id='ED260817-0002')
        self.doc_uno = OperationDocument.objects.create(
            tenant=self.tenant_uno, operation=self.op_uno, file_type='DOCUMENT',
            file=SimpleUploadedFile('propio.pdf', b'%PDF-1.4 propio'),
            original_name='propio.pdf', digital_name='170826-2')

    def _borrar(self, doc, password='borrar123', motivo='Subido por error'):
        self.client.force_login(self.manager_uno)
        return self.client.post(f'/digital/file/{doc.pk}/delete/',
                                {'confirm_password': password,
                                 'delete_reason': motivo})

    def test_no_borra_el_documento_de_otra_empresa(self):
        respuesta = self._borrar(self.doc_dos)

        self.assertEqual(respuesta.status_code, 404)
        self.doc_dos.refresh_from_db()
        self.assertIsNone(self.doc_dos.deleted_at)

    def test_saca_del_expediente_el_documento_propio(self):
        """
        `OperationDocument.objects` esconde lo que esta en la papelera, que es
        justo lo que se quiere comprobar: el archivo deja de existir para el
        expediente aunque el registro siga en la base.
        """
        respuesta = self._borrar(self.doc_uno)

        self.assertEqual(respuesta.status_code, 200)
        self.assertFalse(
            OperationDocument.objects.filter(pk=self.doc_uno.pk).exists())
        self.doc_uno.refresh_from_db()
        self.assertEqual(self.doc_uno.deleted_by, self.manager_uno)
        self.assertEqual(self.doc_uno.delete_reason, 'Subido por error')

    def test_el_archivo_sobrevive_en_la_papelera(self):
        """
        Lo contrario de lo que se probaba antes, y a proposito: el archivo se
        queda para poder devolverlo. Quien lo destruye es la purga, y esa es
        del administrador.

        Sobrevive, pero no donde estaba: se mueve bajo `papelera/`, de modo que
        el enlace repartido antes deja de servir. Mientras compartieron ruta,
        un archivo retirado de la pantalla seguia abriendose para quien tuviera
        su URL, porque el bucket sirve sin preguntar quien mira.
        """
        ruta_original = self.doc_uno.file.name
        storage = self.doc_uno.file.storage

        self._borrar(self.doc_uno)

        self.doc_uno.refresh_from_db()
        self.assertTrue(storage.exists(self.doc_uno.file.name))
        self.assertTrue(self.doc_uno.file.name.startswith(PREFIJO_PAPELERA))
        self.assertFalse(storage.exists(ruta_original))

    def test_sin_motivo_no_se_borra(self):
        respuesta = self._borrar(self.doc_uno, motivo='')

        # 400 y no 200: la pantalla anunciaba "archivo enviado a la papelera"
        # pasara lo que pasara, porque un rechazo era indistinguible de un
        # exito para quien recibia la respuesta.
        self.assertEqual(respuesta.status_code, 400)
        self.assertTrue(
            OperationDocument.objects.filter(pk=self.doc_uno.pk).exists())

    def test_con_la_contraseña_equivocada_no_borra_nada(self):
        respuesta = self._borrar(self.doc_uno, password='incorrecta')

        self.assertEqual(respuesta.status_code, 400)
        self.assertTrue(
            OperationDocument.objects.filter(pk=self.doc_uno.pk).exists())
        # Y lo dice: el motivo del rechazo no se pintaba en ninguna parte, asi
        # que el panel volvia mudo y el aviso daba el borrado por hecho.
        self.assertIn('incorrecta', respuesta.content.decode())

    def test_el_borrado_multiple_no_alcanza_documentos_de_otra_empresa(self):
        self.client.force_login(self.manager_uno)

        self.client.post('/digital/delete-multiple/', {
            'ids': f'{self.doc_uno.pk},{self.doc_dos.pk}',
            'confirm_password': 'borrar123',
            'delete_reason': 'Duplicados'})

        self.assertFalse(
            OperationDocument.objects.filter(pk=self.doc_uno.pk).exists())
        self.doc_dos.refresh_from_db()
        self.assertIsNone(self.doc_dos.deleted_at)

    def test_el_borrado_multiple_no_depende_del_storage(self):
        """
        Con R2 el `os.path.exists(doc.file.path)` de antes lanzaba
        NotImplementedError **antes** del `doc.delete()`, dentro de un try que
        solo apuntaba el error: el borrado multiple no borraba nada en
        produccion y reportaba que algunos archivos habian fallado.

        Mandar a la papelera ya ni siquiera toca el storage, asi que el
        escenario se comprueba ahora sobre la purga, que es la que borra el
        archivo de verdad.
        """
        self.client.force_login(self.manager_uno)

        def sin_ruta(*args, **kwargs):
            raise NotImplementedError('Este backend no tiene rutas locales.')

        with patch('warehouse.views.os.path.exists', side_effect=sin_ruta):
            respuesta = self.client.post('/digital/delete-multiple/', {
                'ids': str(self.doc_uno.pk), 'confirm_password': 'borrar123',
                'delete_reason': 'Duplicado'})

        self.assertFalse(
            OperationDocument.objects.filter(pk=self.doc_uno.pk).exists())
        self.assertIn('papelera', respuesta.content.decode())


class OperacionesPorUsuarioTests(DosTenantsTestBase):
    """
    `operations_by_user` recibe el pk de un usuario en la URL.

    Tenia dos fallos a la vez: no acotaba el usuario al tenant, con lo que
    confirmaba la existencia de usuarios de otras empresas y devolvia su
    username; y el filtro por usuario estaba comentado, asi que la tabla salia
    con todas las operaciones del tenant sin importar a quien se le pedia
    filtrar.
    """

    def setUp(self):
        self.client.force_login(self.manager_uno)
        self.otro_de_uno = User.objects.create_user('staff_uno', password='x')
        UserProfile.objects.create(user=self.otro_de_uno, tenant=self.tenant_uno,
                                   role='staff')

        self.mia = self._operacion(self.tenant_uno, self.cliente_uno,
                                   custom_id='ED260817-0010',
                                   created_by=self.manager_uno)
        self.del_otro = self._operacion(self.tenant_uno, self.cliente_uno,
                                        custom_id='ED260817-0011',
                                        created_by=self.otro_de_uno)

    def test_no_expone_usuarios_de_otra_empresa(self):
        respuesta = self.client.get(f'/operations/by-user/{self.manager_dos.pk}/')

        self.assertEqual(respuesta.status_code, 404)

    def test_devuelve_solo_las_operaciones_del_usuario_pedido(self):
        respuesta = self.client.get(f'/operations/by-user/{self.otro_de_uno.pk}/')
        cuerpo = respuesta.content.decode()

        self.assertEqual(respuesta.status_code, 200)
        self.assertIn('ED260817-0011', cuerpo)
        self.assertNotIn('ED260817-0010', cuerpo)


class ContextoRLSTests(TestCase):
    """
    El contexto de RLS se limpia al terminar el request.

    `SET` sin `LOCAL` guarda la variable en la **conexion**, y las conexiones son
    persistentes (conn_max_age=600). Sin limpiar, la peticion siguiente que
    reutilice la conexion arranca con el tenant de la anterior — y si esa
    peticion no vuelve a fijarlo, porque su usuario no tiene tenant o es
    anonima, hereda el contexto ajeno y RLS le deja leer lo que no es suyo.

    Se prueba con la conexion sustituida: en local la base es SQLite, donde el
    middleware no entra a este camino porque `SET` es sintaxis de PostgreSQL.
    """

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Warehouse Uno', type='organization', subdomain='uno')
        cls.user = User.objects.create_user('manager_uno', password='x')
        UserProfile.objects.create(user=cls.user, tenant=cls.tenant, role='manager')

    def _conexion_falsa(self):
        conexion = MagicMock()
        conexion.vendor = 'postgresql'
        return conexion, conexion.cursor.return_value.__enter__.return_value

    @staticmethod
    def _sentencias(cursor):
        return [llamada.args[0] for llamada in cursor.execute.call_args_list]

    def _request(self, user=None):
        request = RequestFactory().get('/dashboard/')
        request.user = user or self.user
        return request

    def test_fija_el_contexto_y_lo_limpia_al_terminar(self):
        conexion, cursor = self._conexion_falsa()
        middleware = TenantMiddleware(lambda r: HttpResponse('ok'))

        with patch('warehouse.middleware.connection', conexion):
            middleware(self._request())

        self.assertEqual(self._sentencias(cursor), [
            'SET app.current_tenant_id = %s',
            'SET app.current_user_id = %s',
            'RESET app.current_tenant_id',
            'RESET app.current_user_id',
        ])

    def test_lo_limpia_tambien_cuando_la_vista_revienta(self):
        """
        Es el caso que mas importa: la conexion se devuelve al pool igual, asi
        que un error de la vista no puede dejar el tenant pegado.
        """
        conexion, cursor = self._conexion_falsa()

        def revienta(request):
            raise ValueError('la vista fallo')

        middleware = TenantMiddleware(revienta)
        with patch('warehouse.middleware.connection', conexion):
            with self.assertRaises(ValueError):
                middleware(self._request())

        self.assertIn('RESET app.current_tenant_id', self._sentencias(cursor))
        self.assertIn('RESET app.current_user_id', self._sentencias(cursor))

    def test_no_toca_el_contexto_cuando_no_hay_tenant(self):
        sin_tenant = User.objects.create_user('sin_tenant', password='x')
        conexion, _ = self._conexion_falsa()

        middleware = TenantMiddleware(lambda r: HttpResponse('ok'))
        with patch('warehouse.middleware.connection', conexion):
            middleware(self._request(sin_tenant))

        conexion.cursor.assert_not_called()


class FiltroDeUsuariosDelDashboardTests(DosTenantsTestBase):
    """
    El desplegable con el que se filtran las operaciones por usuario.

    Se llenaba con `User.objects.filter(is_active=True)`, sin acotar la empresa,
    asi que los nombres de usuario de una aparecian en la pantalla de otra. Es
    el mismo hueco que tenia `operations_by_user`, que se cerro antes; este
    quedaba abierto porque la lista se arma en el propio dashboard.
    """

    def test_no_lista_usuarios_de_otra_empresa(self):
        self.client.force_login(self.manager_uno)

        respuesta = self.client.get('/dashboard/')

        usuarios = respuesta.context['users']
        self.assertIn(self.manager_uno, usuarios)
        self.assertNotIn(self.manager_dos, usuarios)

    def test_un_usuario_sin_perfil_no_se_cuela(self):
        """
        `profile__tenant` es un JOIN: un usuario sin perfil no tiene tenant que
        comparar y no debe aparecer en la lista de nadie.
        """
        User.objects.create_user('suelto', password='x')
        self.client.force_login(self.manager_uno)

        respuesta = self.client.get('/dashboard/')

        nombres = [u.username for u in respuesta.context['users']]
        self.assertNotIn('suelto', nombres)


class MarcaDeLaBarraSuperiorTests(DosTenantsTestBase):
    """
    La barra superior del dashboard llevaba 'WMS - DYSER GROUP LLC' escrito a
    mano, asi que cualquier otra empresa trabajaba todo el dia bajo el nombre de
    una ajena. La version movil no lo tenia, pero por poner solo 'WMS'.
    """

    def test_el_dashboard_lleva_el_nombre_de_la_empresa_de_quien_entra(self):
        self.client.force_login(self.manager_dos)

        html = self.client.get('/dashboard/').content.decode()

        self.assertIn('WAREHOUSE DOS', html)
        self.assertNotIn('DYSER', html)

    def test_la_version_movil_tambien(self):
        self.client.force_login(self.manager_dos)

        html = self.client.get('/mobile/').content.decode()

        self.assertIn('WAREHOUSE DOS', html)
