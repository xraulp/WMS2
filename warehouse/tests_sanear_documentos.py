"""
El comando que revisa los documentos del expediente.

Quedaron dos residuos de errores ya corregidos, porque arreglar la causa no
reescribe las filas que se crearon antes:

* **Documentos sin empresa**, de cuando la vista que crea la operación no pasaba
  el `tenant` al adjuntar archivos.
* **Filas cuyo archivo no está en el almacén**, de cuando el borrado usaba
  `file.path`, que con R2 no existe.

Lo que fijan estas pruebas es la línea entre lo que un comando de mantenimiento
puede arreglar solo y lo que no:

* **La empresa se repara sin riesgo**, porque el tenant de un documento es
  forzosamente el de su operación: no hay nada que adivinar.
* **Una fila sin archivo solo se informa.** Que falte el objeto no dice si la
  fila sobra, y destruir el registro de un documento es decisión de una persona.
* **Sin `--confirmar` no se toca nada**, igual que en la purga de la papelera.
"""
import tempfile
from datetime import date
from io import StringIO

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings

from .models import Catalog, OperationDocument, Tenant, UserProfile, WarehouseOperation

STORAGE_LOCAL = override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage',
                'OPTIONS': {'location': tempfile.mkdtemp()}},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})


@STORAGE_LOCAL
class BaseSanear(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')
        cls.admin = User.objects.create_user('admin_sanear', password='x')
        UserProfile.objects.create(user=cls.admin, tenant=cls.tenant, role='admin')
        cls.cliente = Catalog.objects.create(
            tenant=cls.tenant, category='CUSTOMER', name='ACME')

    _consecutivo = 0

    def _operacion(self, tenant='usa el de la clase'):
        type(self)._consecutivo += 1
        return WarehouseOperation.objects.create(
            tenant=self.tenant if tenant == 'usa el de la clase' else tenant,
            operation_type='ENTRY', date=date.today(),
            custom_id='ED-%04d' % type(self)._consecutivo,
            customer=self.cliente, created_by=self.admin)

    def _documento(self, tenant='usa el de la clase', operacion=None,
                   nombre='report.pdf'):
        return OperationDocument.objects.create(
            tenant=self.tenant if tenant == 'usa el de la clase' else tenant,
            operation=operacion or self._operacion(), original_name=nombre,
            file=SimpleUploadedFile(nombre, b'contenido'))

    def _correr(self, *argumentos):
        salida = StringIO()
        call_command('sanear_documentos', *argumentos, stdout=salida)
        return salida.getvalue()


class LaEmpresaQueFaltaSeRepara(BaseSanear):

    def test_sin_confirmar_solo_informa(self):
        doc = self._documento(tenant=None)

        salida = self._correr()

        self.assertIn('1 documento(s) sin empresa', salida)
        self.assertIn('--confirmar', salida)
        doc.refresh_from_db()
        self.assertIsNone(doc.tenant)

    def test_con_confirmar_toma_la_empresa_de_su_operacion(self):
        """No hay nada que adivinar: el documento es de la empresa de su operación."""
        doc = self._documento(tenant=None)

        salida = self._correr('--confirmar')

        doc.refresh_from_db()
        self.assertEqual(doc.tenant, self.tenant)
        self.assertIn('1 documento(s) quedaron con su empresa', salida)

    def test_no_toca_los_que_ya_tienen_empresa(self):
        doc = self._documento()

        self._correr('--confirmar')

        doc.refresh_from_db()
        self.assertEqual(doc.tenant, self.tenant)

    def test_si_la_operacion_tampoco_tiene_empresa_se_deja_como_esta(self):
        """Ponerle una a ojo sería mover un documento de compañía."""
        huerfana = self._operacion(tenant=None)
        doc = self._documento(tenant=None, operacion=huerfana)

        salida = self._correr('--confirmar')

        doc.refresh_from_db()
        self.assertIsNone(doc.tenant)
        self.assertIn('su operacion tampoco tiene empresa', salida)

    def test_tambien_repara_los_que_estan_en_la_papelera(self):
        """Un documento archivado puede restaurarse: su empresa importa igual."""
        doc = self._documento(tenant=None)
        doc.archivar(self.admin, 'subido por error')

        self._correr('--confirmar')

        doc.refresh_from_db()
        self.assertEqual(doc.tenant, self.tenant)

    def test_sin_nada_que_reparar_lo_dice(self):
        self._documento()

        self.assertIn('Todos los documentos tienen empresa asignada', self._correr())


class LasFilasSinArchivoSoloSeInforman(BaseSanear):

    def test_una_fila_sin_ruta_sale_en_el_informe(self):
        doc = self._documento()
        doc.file = ''
        doc.save(update_fields=['file'])

        salida = self._correr('--confirmar')

        self.assertIn('sin ninguna ruta de archivo', salida)
        self.assertIn('id %s' % doc.pk, salida)

    def test_no_se_borra_ni_con_confirmar(self):
        """Que falte el archivo no dice si la fila sobra."""
        doc = self._documento()
        doc.file = ''
        doc.save(update_fields=['file'])

        self._correr('--confirmar')

        self.assertTrue(OperationDocument.todos.filter(pk=doc.pk).exists())


class ElContrasteConElAlmacen(BaseSanear):

    def test_no_se_hace_si_no_se_pide(self):
        """Pregunta al almacén una vez por documento: con el expediente entero
        son muchas llamadas de red."""
        self._documento()

        salida = self._correr()

        self.assertIn('--sin-almacen', salida)
        self.assertNotIn('Contrastando', salida)

    def test_con_todo_en_su_sitio_lo_dice(self):
        self._documento()

        salida = self._correr('--sin-almacen')

        self.assertIn('estan en el almacen', salida)

    def test_una_fila_cuyo_archivo_desaparecio_sale_listada(self):
        """Es el caso del documento id 9 de producción."""
        doc = self._documento()
        doc.file.storage.delete(doc.file.name)

        salida = self._correr('--sin-almacen')

        self.assertIn('con fila pero sin archivo', salida)
        self.assertIn('id %s' % doc.pk, salida)
        self.assertIn('en el expediente', salida)

    def test_dice_si_el_ausente_estaba_en_la_papelera(self):
        """Cambia quién lo resuelve: a los de la papelera se los lleva la purga."""
        doc = self._documento()
        doc.archivar(self.admin, 'subido por error')
        doc.file.storage.delete(doc.file.name)

        salida = self._correr('--sin-almacen')

        self.assertIn('en la papelera', salida)

    def test_no_borra_lo_que_no_encuentra(self):
        doc = self._documento()
        doc.file.storage.delete(doc.file.name)

        self._correr('--sin-almacen', '--confirmar')

        self.assertTrue(OperationDocument.todos.filter(pk=doc.pk).exists())
