"""
Borrar dejo de ser una frontera de rol y paso a ser una accion con rastro.

Buscar al administrador para quitar un archivo mal subido o un registro
equivocado paraba el trabajo del dia -y, con la regla anterior, ni siquiera el
administrador podia: solo borraba lo que el mismo hubiera capturado-. Lo que
sustituye al permiso denegado es el control: contrasena de borrado, motivo
escrito, renglon en la bitacora y, en el expediente, papelera de la que se
puede volver.

Aqui se comprueban las cuatro cosas a la vez, porque quitar cualquiera de ellas
deja el permiso sin contrapeso.
"""
import tempfile
from datetime import date

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import NoReverseMatch, reverse

from .models import (Catalog, DeletionLog, OperationDocument, Tenant,
                     UserProfile, WarehouseOperation)

STORAGE_LOCAL = override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage',
                'OPTIONS': {'location': tempfile.mkdtemp()}},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})


class BaseBorrado(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')

        def usuario(nombre, rol, con_contrasena=True):
            u = User.objects.create_user(nombre, password='x')
            perfil = UserProfile.objects.create(user=u, tenant=cls.tenant, role=rol)
            if con_contrasena:
                perfil.set_delete_password('borrar123')
                perfil.save(update_fields=['delete_password'])
            return u

        cls.staff   = usuario('staff', 'staff')
        cls.manager = usuario('manager', 'manager')
        cls.admin   = usuario('admin_tenant', 'admin')

        cls.cliente = Catalog.objects.create(
            tenant=cls.tenant, category='CUSTOMER', name='Cliente del Norte')

    def setUp(self):
        self.op = WarehouseOperation.objects.create(
            tenant=self.tenant, operation_type='ENTRY', date=date.today(),
            custom_id='ED260819-0001', customer=self.cliente,
            description='Mercancia de prueba', created_by=self.manager)
        self.client.force_login(self.staff)

    def _borrar_operacion(self, password='borrar123', motivo='Capturada dos veces'):
        return self.client.post('/operations/%d/delete-confirm/' % self.op.pk,
                                {'confirm_password': password,
                                 'delete_reason': motivo})


class BorradoDeOperacionesTests(BaseBorrado):

    def test_el_staff_borra_una_operacion(self):
        self._borrar_operacion()

        self.assertFalse(
            WarehouseOperation.objects.filter(pk=self.op.pk).exists())

    def test_borra_tambien_lo_que_capturo_otro(self):
        """
        La regla anterior -cada quien borra solo lo suyo, salvo el superadmin-
        obligaba a llamar a un superusuario para deshacer lo capturado en otro
        turno. La operacion de esta prueba la creo el manager.
        """
        self.assertEqual(self.op.created_by, self.manager)

        self._borrar_operacion()

        self.assertFalse(
            WarehouseOperation.objects.filter(pk=self.op.pk).exists())

    def test_queda_el_renglon_con_el_motivo(self):
        self._borrar_operacion(motivo='La captura correcta es la ED260819-0002')

        registro = DeletionLog.objects.get()
        self.assertEqual(registro.kind, 'OPERATION')
        self.assertEqual(registro.deleted_by, self.staff)
        self.assertEqual(registro.custom_id, 'ED260819-0001')
        self.assertEqual(registro.customer_name, 'Cliente del Norte')
        self.assertEqual(registro.reason, 'La captura correcta es la ED260819-0002')

    def test_sin_motivo_no_borra(self):
        respuesta = self._borrar_operacion(motivo='   ')

        self.assertTrue(WarehouseOperation.objects.filter(pk=self.op.pk).exists())
        self.assertContains(respuesta, 'motivo')

    def test_con_la_contrasena_equivocada_no_borra(self):
        self._borrar_operacion(password='otra')

        self.assertTrue(WarehouseOperation.objects.filter(pk=self.op.pk).exists())
        self.assertFalse(DeletionLog.objects.exists())

    def test_sin_contrasena_de_borrado_configurada_no_borra(self):
        """
        Antes, quien no tuviera contrasena de borrado pasaba con la de su
        sesion -la que el navegador ya tiene escrita-, de modo que el control
        solo existia para quien se habia molestado en configurarlo.
        """
        perfil = self.staff.profile
        perfil.delete_password = None
        perfil.save(update_fields=['delete_password'])

        respuesta = self._borrar_operacion(password='x')

        self.assertTrue(WarehouseOperation.objects.filter(pk=self.op.pk).exists())
        self.assertContains(respuesta, 'contrasena de borrado')

    def test_el_cliente_no_borra(self):
        cliente_usuario = User.objects.create_user('cliente_final', password='x')
        UserProfile.objects.create(user=cliente_usuario, tenant=self.tenant,
                                   role='customer', customer=self.cliente)
        self.client.force_login(cliente_usuario)

        respuesta = self._borrar_operacion()

        self.assertEqual(respuesta.status_code, 403)
        self.assertTrue(WarehouseOperation.objects.filter(pk=self.op.pk).exists())

    def test_la_ruta_sin_contrasena_ya_no_existe(self):
        """
        `/operations/<pk>/delete/` borraba con un POST pelado mientras la
        pantalla usaba delete-confirm. Con el staff pudiendo borrar, esa puerta
        volvia decorativo todo lo demas.
        """
        with self.assertRaises(NoReverseMatch):
            reverse('operation_delete', args=[self.op.pk])

        respuesta = self.client.post('/operations/%d/delete/' % self.op.pk)

        self.assertEqual(respuesta.status_code, 404)
        self.assertTrue(WarehouseOperation.objects.filter(pk=self.op.pk).exists())


@STORAGE_LOCAL
class PapeleraTests(BaseBorrado):
    """
    La papelera es la mitad recuperable del trato: el operador quita, el
    administrador devuelve o destruye.
    """

    def setUp(self):
        super().setUp()
        self.doc = OperationDocument.objects.create(
            tenant=self.tenant, operation=self.op, file_type='DOCUMENT',
            file=SimpleUploadedFile('guia.pdf', b'%PDF-1.4 guia'),
            original_name='guia.pdf', digital_name='190826-1')

    def _archivar(self):
        self.client.force_login(self.staff)
        return self.client.post('/digital/file/%d/delete/' % self.doc.pk,
                                {'confirm_password': 'borrar123',
                                 'delete_reason': 'Guia del embarque anterior'})

    def test_el_staff_no_entra_a_la_papelera(self):
        """
        El staff deja rastro, no lo audita: la pantalla es vigilancia sobre el
        trabajo ajeno.
        """
        respuesta = self.client.get('/deletions/')

        self.assertEqual(respuesta.status_code, 403)

    def test_el_manager_y_el_admin_si_entran(self):
        for usuario in (self.manager, self.admin):
            with self.subTest(usuario=usuario.username):
                self.client.force_login(usuario)
                respuesta = self.client.get('/deletions/')
                self.assertEqual(respuesta.status_code, 200)

    def test_el_archivo_archivado_aparece_en_la_papelera(self):
        self._archivar()
        self.client.force_login(self.admin)

        respuesta = self.client.get('/deletions/')

        self.assertContains(respuesta, '190826-1')
        self.assertContains(respuesta, 'Guia del embarque anterior')

    def test_restaurar_lo_devuelve_al_expediente(self):
        self._archivar()
        self.assertFalse(OperationDocument.objects.filter(pk=self.doc.pk).exists())
        self.client.force_login(self.admin)

        self.client.post('/deletions/%d/restore/' % self.doc.pk)

        self.assertTrue(OperationDocument.objects.filter(pk=self.doc.pk).exists())
        self.doc.refresh_from_db()
        self.assertIsNone(self.doc.deleted_at)

    def test_el_staff_no_restaura(self):
        self._archivar()

        respuesta = self.client.post('/deletions/%d/restore/' % self.doc.pk)

        self.assertEqual(respuesta.status_code, 403)

    def test_solo_el_admin_destruye(self):
        self._archivar()
        self.client.force_login(self.manager)

        respuesta = self.client.post('/deletions/%d/purge/' % self.doc.pk,
                                     {'confirm_password': 'borrar123'})

        self.assertEqual(respuesta.status_code, 403)
        self.assertTrue(OperationDocument.todos.filter(pk=self.doc.pk).exists())

    def test_destruir_borra_el_registro_y_el_archivo(self):
        """
        Es lo unico irreversible de la pantalla. Si el archivo se quedara en
        R2, seguiria descargable: el dominio publico sirve los objetos sin
        pedir credenciales.
        """
        ruta = self.doc.file.name
        storage = self.doc.file.storage
        self._archivar()
        self.client.force_login(self.admin)

        self.client.post('/deletions/%d/purge/' % self.doc.pk,
                         {'confirm_password': 'borrar123'})

        self.assertFalse(OperationDocument.todos.filter(pk=self.doc.pk).exists())
        self.assertFalse(storage.exists(ruta))

    def test_destruir_exige_la_contrasena(self):
        self._archivar()
        self.client.force_login(self.admin)

        self.client.post('/deletions/%d/purge/' % self.doc.pk,
                         {'confirm_password': 'otra'})

        self.assertTrue(OperationDocument.todos.filter(pk=self.doc.pk).exists())

    def test_no_destruye_lo_que_sigue_en_el_expediente(self):
        self.client.force_login(self.admin)

        self.client.post('/deletions/%d/purge/' % self.doc.pk,
                         {'confirm_password': 'borrar123'})

        self.assertTrue(OperationDocument.objects.filter(pk=self.doc.pk).exists())

    def test_la_pantalla_trae_su_propio_token(self):
        """
        El panel se carga por htmx y restaurar y destruir son POST. Sin token
        propio, el navegador recibe un 403 que en las pruebas no se ve: el
        cliente de test no comprueba CSRF salvo que se le pida.
        """
        from django.test import Client

        self._archivar()
        navegador = Client(enforce_csrf_checks=True)
        navegador.force_login(self.admin)

        panel = navegador.get('/deletions/')
        self.assertContains(panel, 'csrfmiddlewaretoken')

        token = panel.context['csrf_token']
        respuesta = navegador.post('/deletions/%d/restore/' % self.doc.pk,
                                   {'csrfmiddlewaretoken': str(token)})

        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(OperationDocument.objects.filter(pk=self.doc.pk).exists())

    def test_el_archivado_no_sale_en_el_expediente_ni_en_el_zip(self):
        self._archivar()

        panel = self.client.get('/digital/search/', {'q': self.op.custom_id})
        zip_resp = self.client.get('/operations/%d/download-all/' % self.op.pk)

        self.assertNotContains(panel, '190826-1')
        self.assertNotIn(b'190826-1', zip_resp.content)

    def test_el_numero_del_archivado_no_se_reutiliza(self):
        """
        El consecutivo se siembra contando tambien la papelera: un nombre que
        ya salio impreso o adjunto no puede reasignarse, y menos cuando el
        archivo puede volver.
        """
        self._archivar()
        self.client.force_login(self.staff)

        self.client.post('/digital/%d/upload/' % self.op.pk,
                         {'files': SimpleUploadedFile('nueva.jpg', b'x', 'image/jpeg')})

        nuevo = OperationDocument.objects.order_by('pk').last()
        self.assertNotEqual(nuevo.digital_name, '190826-1')


class ContrasenaDeBorradoTests(TestCase):
    """
    La contrasena de borrado se guardaba en claro y se pintaba en la pantalla
    de usuarios, en un campo de texto y en una columna de la tabla. Mientras
    borrar estuvo reservado a los roles de casa era una fealdad; desde que es
    lo que autoriza la accion, es el control mismo.
    """

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')
        cls.admin = User.objects.create_user('admin_tenant', password='x')
        cls.perfil_admin = UserProfile.objects.create(
            user=cls.admin, tenant=cls.tenant, role='admin')
        cls.perfil_admin.set_delete_password('borrar123')
        cls.perfil_admin.save(update_fields=['delete_password'])

    def test_no_se_guarda_en_claro(self):
        self.assertNotEqual(self.perfil_admin.delete_password, 'borrar123')
        self.assertTrue(self.perfil_admin.check_delete_password('borrar123'))
        self.assertFalse(self.perfil_admin.check_delete_password('otra'))

    def test_una_contrasena_heredada_en_claro_sigue_valiendo_y_se_cifra(self):
        """
        Las bases que ya estan en produccion tienen el valor sin cifrar. La
        migracion las convierte, y esto cubre el caso de que alguna se escape:
        vale una vez y queda cifrada.
        """
        usuario = User.objects.create_user('viejo', password='x')
        perfil = UserProfile.objects.create(user=usuario, tenant=self.tenant,
                                            role='manager',
                                            delete_password='enclaro')

        self.assertTrue(perfil.check_delete_password('enclaro'))

        perfil.refresh_from_db()
        self.assertNotEqual(perfil.delete_password, 'enclaro')
        self.assertTrue(perfil.check_delete_password('enclaro'))

    def test_una_contrasena_vacia_nunca_vale(self):
        usuario = User.objects.create_user('sin_contrasena', password='x')
        perfil = UserProfile.objects.create(user=usuario, tenant=self.tenant,
                                            role='staff')

        self.assertFalse(perfil.check_delete_password(''))
        self.assertFalse(perfil.check_delete_password('lo que sea'))

    def test_la_pantalla_de_usuarios_la_guarda_cifrada(self):
        self.client.force_login(self.admin)

        self.client.post('/users/', {'action': 'create', 'username': 'nuevo',
                                     'password': 'secreta123', 'role': 'staff',
                                     'delete_password': 'borrame'})

        perfil = UserProfile.objects.get(user__username='nuevo')
        self.assertNotEqual(perfil.delete_password, 'borrame')
        self.assertTrue(perfil.check_delete_password('borrame'))

    def test_la_pantalla_de_usuarios_no_la_muestra(self):
        self.client.force_login(self.admin)

        respuesta = self.client.get('/users/')

        self.assertNotContains(respuesta, self.perfil_admin.delete_password)
        self.assertContains(respuesta, 'configurada')


class AsignarLaContrasenaDeBorradoTests(TestCase):
    """
    El camino por el que la contrasena de borrado llega al perfil.

    No estaba cubierto por ninguna prueba, y es el que decide si alguien puede
    borrar: sin contrasena configurada, la pantalla rechaza el borrado. Se
    comprueba el caso que se dio en produccion -un superadmin asignandosela a si
    mismo desde su propia fila- y que el campo vacio no la borra.
    """

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')
        cls.jefe = User.objects.create_user('jefe', password='x')
        cls.perfil_jefe = UserProfile.objects.create(
            user=cls.jefe, tenant=cls.tenant, role='superadmin')
        cls.operador = User.objects.create_user('operador', password='x')
        cls.perfil_operador = UserProfile.objects.create(
            user=cls.operador, tenant=cls.tenant, role='staff')

    def setUp(self):
        self.client.force_login(self.jefe)

    def _editar(self, usuario, perfil, **extra):
        datos = {'action': 'update_role', 'user_id': usuario.pk,
                 'role': perfil.role, 'customer_id': ''}
        datos.update(extra)
        return self.client.post('/users/', datos)

    def test_se_la_puede_asignar_a_otro(self):
        self._editar(self.operador, self.perfil_operador,
                     delete_password='borrar123')

        self.perfil_operador.refresh_from_db()
        self.assertTrue(self.perfil_operador.check_delete_password('borrar123'))

    def test_se_la_puede_asignar_a_si_mismo(self):
        """
        Es lo primero que hay que hacer en una instalacion nueva: el
        administrador no puede borrar nada mientras no tenga la suya.
        """
        self._editar(self.jefe, self.perfil_jefe, delete_password='borrar123')

        self.perfil_jefe.refresh_from_db()
        self.assertTrue(self.perfil_jefe.check_delete_password('borrar123'))

    def test_queda_cifrada_y_no_en_claro(self):
        self._editar(self.operador, self.perfil_operador,
                     delete_password='borrar123')

        self.perfil_operador.refresh_from_db()
        self.assertNotEqual(self.perfil_operador.delete_password, 'borrar123')

    def test_el_campo_vacio_no_borra_la_que_ya_tenia(self):
        self.perfil_operador.set_delete_password('borrar123')
        self.perfil_operador.save(update_fields=['delete_password'])

        self._editar(self.operador, self.perfil_operador, delete_password='')

        self.perfil_operador.refresh_from_db()
        self.assertTrue(self.perfil_operador.check_delete_password('borrar123'))

    def _asignar(self, usuario, clave):
        return self.client.post('/users/', {
            'action': 'set_delete_password', 'user_id': usuario.pk,
            'delete_password': clave})

    def test_la_columna_propia_la_asigna_sin_tocar_nada_mas(self):
        """
        Tiene accion propia para que asignarla no arrastre el rol ni el cliente,
        y sobre todo para que no se confunda con la contrasena de acceso, que se
        pone en la columna de al lado.
        """
        self._asignar(self.operador, 'borrar123')

        self.perfil_operador.refresh_from_db()
        self.assertTrue(self.perfil_operador.check_delete_password('borrar123'))
        self.assertEqual(self.perfil_operador.role, 'staff')

    def test_asignarla_no_cambia_la_contrasena_de_acceso(self):
        self._asignar(self.operador, 'borrar123')

        self.assertTrue(self.client.login(username='operador', password='x'))

    def test_vaciarla_se_la_quita_y_lo_dice(self):
        self.perfil_operador.set_delete_password('borrar123')
        self.perfil_operador.save(update_fields=['delete_password'])

        respuesta = self._asignar(self.operador, '')

        self.perfil_operador.refresh_from_db()
        self.assertFalse(self.perfil_operador.delete_password)
        self.assertIn('no longer delete', respuesta.content.decode())

    def test_nadie_se_la_pone_a_quien_esta_por_encima(self):
        otro_jefe = User.objects.create_user('otro_jefe', password='x')
        perfil = UserProfile.objects.create(
            user=otro_jefe, tenant=self.tenant, role='superadmin')
        admin = User.objects.create_user('un_admin', password='x')
        UserProfile.objects.create(user=admin, tenant=self.tenant, role='admin')
        self.client.force_login(admin)

        self._asignar(otro_jefe, 'borrar123')

        perfil.refresh_from_db()
        self.assertFalse(perfil.delete_password)

    def test_el_alta_de_usuario_tambien_la_guarda(self):
        self.client.post('/users/', {
            'action': 'create', 'username': 'nuevo', 'password': 'acceso123',
            'role': 'staff', 'delete_password': 'borrar123'})

        perfil = UserProfile.objects.get(user__username='nuevo')
        self.assertTrue(perfil.check_delete_password('borrar123'))
