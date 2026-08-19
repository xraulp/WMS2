"""
Lo que puede y lo que no puede el `staff` de una empresa.

El `staff` es el operador que captura todos los dias, no un usuario de solo
lectura: crea operaciones, las corrige, reenvia avisos, saca reportes, mantiene
el catalogo operativo, sube archivos al expediente y tambien borra. Su unica
frontera es **no dar de alta clientes**; el borrado dejo de ser una frontera de
rol para volverse una accion con contrasena, motivo y rastro.

`operation_edit` exigia `is_home()`, que deja fuera al staff, mientras la tabla
le pintaba igual el boton Edit: quien se equivocaba tecleando un peso tenia que
pedirle la correccion a un manager.
"""
import tempfile
from datetime import date

from django.contrib.auth.models import User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from .models import (Catalog, DeletionLog, OperationDocument, Tenant,
                     UserProfile, WarehouseOperation)

STORAGE_LOCAL = override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage',
                'OPTIONS': {'location': tempfile.mkdtemp()}},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})


class BaseStaff(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')

        def usuario(nombre, rol):
            u = User.objects.create_user(nombre, password='x')
            UserProfile.objects.create(user=u, tenant=cls.tenant, role=rol,
                                       delete_password='borrar123')
            return u

        cls.staff   = usuario('staff', 'staff')
        cls.manager = usuario('manager', 'manager')

        cls.cliente = Catalog.objects.create(
            tenant=cls.tenant, category='CUSTOMER', name='Cliente del Norte',
            contact_email='avisos@cliente.com')

    def setUp(self):
        # `date` explicita: el default del modelo es `timezone.now`, que deja
        # un datetime en el objeto en memoria y el formulario de edicion espera
        # YYYY-MM-DD.
        self.op = WarehouseOperation.objects.create(
            tenant=self.tenant, operation_type='ENTRY', date=date.today(),
            custom_id='ED260818-0001', customer=self.cliente,
            weight_lbs=100, description='Mercancia de prueba')
        self.client.force_login(self.staff)


class StaffOperaTests(BaseStaff):
    """
    Capturar y corregir son el mismo trabajo: quien captura es quien se
    equivoca al capturar.
    """

    def test_abre_el_formulario_de_edicion(self):
        respuesta = self.client.get('/operations/%d/edit/' % self.op.pk)

        self.assertEqual(respuesta.status_code, 200)

    def test_corrige_el_peso_de_una_operacion(self):
        self.client.post('/operations/%d/edit/' % self.op.pk, {
            'date': str(self.op.date), 'weight_lbs': '250',
            'description': 'Mercancia de prueba'})

        self.op.refresh_from_db()
        self.assertEqual(int(self.op.weight_lbs), 250)

    def test_crea_operaciones(self):
        respuesta = self.client.post('/operations/create/', {
            'operation_type': 'ENTRY', 'date': str(date.today()),
            'customer_id': str(self.cliente.pk), 'shipper_text': 'Remitente',
            'carrier_text': 'Transportes', 'bundle_type_text': 'Tarima',
            'bundle_qty': '3', 'weight_lbs': '500',
            'description': 'Entrada capturada por el operador'})

        self.assertNotEqual(respuesta.status_code, 403)
        self.assertTrue(WarehouseOperation.objects.filter(
            description='Entrada capturada por el operador').exists())

    def test_reenvia_el_reporte_por_correo(self):
        respuesta = self.client.post('/operations/%d/email/' % self.op.pk,
                                     {'recipient_email': 'avisos@cliente.com',
                                      'subject': 'Reporte', 'message': ''})

        # La vista contesta 200 aunque el envio falle -el mensaje va en el
        # cuerpo-, asi que lo que se mira es que el correo salio.
        self.assertEqual(respuesta.status_code, 200)
        self.assertNotContains(respuesta, 'Permission denied')
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('avisos@cliente.com', mail.outbox[0].to)

    def test_el_detalle_le_ofrece_el_boton_de_whatsapp(self):
        """
        Estaba escondido tras `is_home`, asi que el operador veia el correo
        pero no el WhatsApp aunque la vista de envio nunca le negó el paso.
        """
        respuesta = self.client.get('/operations/%d/' % self.op.pk)

        self.assertContains(respuesta, 'Send WhatsApp Notification')

    def test_el_cliente_no_edita_los_campos_operativos(self):
        """
        Abrir la puerta al staff no la abre al cliente: el nivel 3 solo toca
        sus propias notas.
        """
        cliente_usuario = User.objects.create_user('cliente_final', password='x')
        UserProfile.objects.create(user=cliente_usuario, tenant=self.tenant,
                                   role='customer', customer=self.cliente)
        self.client.force_login(cliente_usuario)

        self.client.post('/operations/%d/edit/' % self.op.pk, {
            'date': str(self.op.date), 'weight_lbs': '999',
            'customer_notes': 'Traen dos bultos mas'})

        self.op.refresh_from_db()
        self.assertEqual(int(self.op.weight_lbs), 100)
        self.assertEqual(self.op.customer_notes, 'Traen dos bultos mas')


class StaffCatalogoTests(BaseStaff):
    """
    En el catalogo puede con todo menos con los clientes. La mitad de esto ya
    lo fija `tests_permisos`; aqui queda el lado positivo, que es el que se
    rompe sin querer al cerrar permisos.
    """

    def test_da_de_alta_el_catalogo_operativo(self):
        for categoria in ('SHIPPER', 'CARRIER', 'BUNDLE_TYPE', 'TYPE_OP'):
            with self.subTest(categoria=categoria):
                self.client.post('/catalog/create/',
                                 {'category': categoria,
                                  'name': 'Alta %s' % categoria})
                self.assertTrue(Catalog.objects.filter(
                    name='Alta %s' % categoria).exists())

    def test_no_da_de_alta_clientes(self):
        respuesta = self.client.post('/catalog/create/',
                                     {'category': 'CUSTOMER',
                                      'name': 'Cliente colado'})

        self.assertEqual(respuesta.status_code, 403)
        self.assertFalse(Catalog.objects.filter(name='Cliente colado').exists())


@STORAGE_LOCAL
class StaffExpedienteTests(BaseStaff):
    """
    Sube documentos e imagenes, y tambien los quita: buscar al administrador
    para eliminar un archivo mal subido paraba el trabajo del dia. Lo que
    sustituye al permiso denegado es el rastro -contrasena, motivo y bitacora- y
    el hecho de que el archivo se pueda devolver.
    """

    def _documento(self):
        return OperationDocument.objects.create(
            tenant=self.tenant, operation=self.op, file_type='DOCUMENT',
            file=SimpleUploadedFile('guia.pdf', b'%PDF-1.4 guia'),
            original_name='guia.pdf', digital_name='180826-1')

    def test_sube_archivos_al_expediente(self):
        respuesta = self.client.post(
            '/digital/%d/upload/' % self.op.pk,
            {'files': SimpleUploadedFile('foto.jpg', b'contenido', 'image/jpeg')})

        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(
            OperationDocument.objects.filter(original_name='foto.jpg').exists())

    def test_manda_un_archivo_a_la_papelera(self):
        doc = self._documento()

        respuesta = self.client.post('/digital/file/%d/delete/' % doc.pk,
                                     {'confirm_password': 'borrar123',
                                      'delete_reason': 'Foto del camion equivocado'})

        self.assertEqual(respuesta.status_code, 200)
        # Deja de estar en el expediente...
        self.assertFalse(OperationDocument.objects.filter(pk=doc.pk).exists())
        # ...pero no se destruyo, y se sabe quien lo quito y por que.
        doc.refresh_from_db()
        self.assertEqual(doc.deleted_by, self.staff)
        self.assertEqual(doc.delete_reason, 'Foto del camion equivocado')

    def test_el_borrado_deja_renglon_en_la_bitacora(self):
        doc = self._documento()

        self.client.post('/digital/file/%d/delete/' % doc.pk,
                         {'confirm_password': 'borrar123',
                          'delete_reason': 'Duplicado'})

        registro = DeletionLog.objects.get(kind='DOCUMENT')
        self.assertEqual(registro.deleted_by, self.staff)
        self.assertEqual(registro.custom_id, self.op.custom_id)
        self.assertEqual(registro.document_name, doc.digital_name)
        self.assertEqual(registro.reason, 'Duplicado')

    def test_sin_motivo_no_borra(self):
        doc = self._documento()

        self.client.post('/digital/file/%d/delete/' % doc.pk,
                         {'confirm_password': 'borrar123', 'delete_reason': '  '})

        self.assertTrue(OperationDocument.objects.filter(pk=doc.pk).exists())
        self.assertFalse(DeletionLog.objects.exists())

    def test_sin_contrasena_de_borrado_configurada_no_borra(self):
        """
        La contrasena de borrado es lo que autoriza la accion. Quien no la
        tenga puesta no borra, y no hay salida por la contrasena de sesion.
        """
        perfil = self.staff.profile
        perfil.delete_password = None
        perfil.save(update_fields=['delete_password'])
        doc = self._documento()

        self.client.post('/digital/file/%d/delete/' % doc.pk,
                         {'confirm_password': 'x', 'delete_reason': 'Duplicado'})

        self.assertTrue(OperationDocument.objects.filter(pk=doc.pk).exists())

    def test_manda_varios_archivos_a_la_papelera_de_una_vez(self):
        doc = self._documento()

        respuesta = self.client.post('/digital/delete-multiple/',
                                     {'ids': str(doc.pk),
                                      'confirm_password': 'borrar123',
                                      'delete_reason': 'Se subieron dos veces'})

        self.assertEqual(respuesta.status_code, 200)
        self.assertFalse(OperationDocument.objects.filter(pk=doc.pk).exists())

    def test_el_panel_le_ofrece_el_borrado(self):
        """
        La seleccion masiva y la papelera colgaban de `is_home`, que deja fuera
        al staff; se preguntan ahora por el permiso mismo, para que la pantalla
        y la vista no puedan separarse.
        """
        self._documento()

        respuesta = self.client.get('/digital/search/', {'q': self.op.custom_id})

        # Se buscan las marcas del bloque, no el texto del boton: las
        # funciones de borrado viven en el <script> del partial y estan
        # siempre, sea quien sea el que mira.
        self.assertContains(respuesta, 'id="select-all-files"')
        self.assertContains(respuesta, 'onclick="confirmDeleteFile(')

    def test_el_cliente_no_ve_el_borrado(self):
        self._documento()
        cliente_usuario = User.objects.create_user('cliente_lector', password='x')
        UserProfile.objects.create(user=cliente_usuario, tenant=self.tenant,
                                   role='customer', customer=self.cliente)
        self.client.force_login(cliente_usuario)

        respuesta = self.client.get('/digital/search/', {'q': self.op.custom_id})

        self.assertNotContains(respuesta, 'id="select-all-files"')
        self.assertNotContains(respuesta, 'onclick="confirmDeleteFile(')
