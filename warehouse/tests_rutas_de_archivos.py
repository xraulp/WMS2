"""Dónde se guarda el archivo de un documento del expediente.

La ruta era `operations/%Y/%m/%d/` más el nombre original tal cual, y eso falló
de tres maneras distintas en producción, las tres en silencio:

* **Se perdieron dos archivos.** Dos documentos llamados `report.pdf` subidos el
  mismo día daban la misma ruta, y `AWS_S3_FILE_OVERWRITE` vale `True` mientras
  nadie diga lo contrario, así que el segundo pisaba al primero. La base
  conservaba las dos filas apuntando al mismo objeto: la pantalla no mostraba un
  error, mostraba el archivo equivocado.
* **No aislaba las empresas.** La ruta no llevaba el tenant, de modo que el
  `report.pdf` de una podía pisar el de otra.
* **Era adivinable**, y el bucket se sirve por un dominio público, así que
  cualquiera que conociera el dominio —que sale en el HTML de cualquier pantalla
  con archivos— podía sondear documentos ajenos sin pasar por el sistema.

Estas pruebas fijan las tres propiedades de la ruta nueva. La del sobrescribir
mira el valor de la configuración porque es un caso que ninguna prueba funcional
detecta: el almacén de pruebas es el sistema de archivos local, que se comporta
distinto que R2.
"""
import re
import tempfile
from datetime import date

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import (PREFIJO_PAPELERA, Catalog, OperationDocument, Tenant,
                     UserProfile, WarehouseOperation, ruta_documento)

STORAGE_LOCAL = override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage',
                'OPTIONS': {'location': tempfile.mkdtemp()}},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})


class BaseRutas(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')
        cls.otro = Tenant.objects.create(
            name='Bodegas del Sur', type='organization', subdomain='sur')
        cls.admin = User.objects.create_user('admin_rutas', password='x')
        UserProfile.objects.create(user=cls.admin, tenant=cls.tenant, role='admin')
        cls.cliente = Catalog.objects.create(
            tenant=cls.tenant, category='CUSTOMER', name='ACME')

    _consecutivo = 0

    def _operacion(self, tenant=None):
        type(self)._consecutivo += 1
        return WarehouseOperation.objects.create(
            tenant=tenant or self.tenant, operation_type='ENTRY',
            date=date.today(), custom_id='ED260822-%04d' % type(self)._consecutivo,
            customer=self.cliente, description='Mercancia', created_by=self.admin)

    def _documento(self, nombre='report.pdf', tenant=None, operacion=None):
        empresa = tenant if tenant is not None else self.tenant
        return OperationDocument.objects.create(
            tenant=empresa, operation=operacion or self._operacion(empresa),
            original_name=nombre,
            file=SimpleUploadedFile(nombre, b'contenido del archivo'))


@STORAGE_LOCAL
class LaRutaNoColisionaTests(BaseRutas):

    def test_dos_archivos_con_el_mismo_nombre_no_comparten_ruta(self):
        """Es el fallo que destruyó dos archivos: misma ruta, y el segundo pisa.

        Se comprueba sobre el mismo tenant y el mismo día, que es exactamente la
        situación en la que la ruta vieja colisionaba.
        """
        uno = self._documento('report.pdf')
        dos = self._documento('report.pdf')

        self.assertNotEqual(uno.file.name, dos.file.name)

    def test_cada_archivo_conserva_su_contenido(self):
        """La colisión no se veía porque la pantalla seguía abriendo un PDF."""
        uno = OperationDocument.objects.create(
            tenant=self.tenant, operation=self._operacion(),
            original_name='report.pdf',
            file=SimpleUploadedFile('report.pdf', b'el primero'))
        dos = OperationDocument.objects.create(
            tenant=self.tenant, operation=self._operacion(),
            original_name='report.pdf',
            file=SimpleUploadedFile('report.pdf', b'el segundo'))

        self.assertEqual(uno.file.read(), b'el primero')
        self.assertEqual(dos.file.read(), b'el segundo')

    def test_el_nombre_original_se_conserva_aunque_la_ruta_cambie(self):
        """La ruta deja de ser el nombre, así que el nombre tiene que vivir aparte."""
        doc = self._documento('Reporte Final.pdf')

        self.assertEqual(doc.original_name, 'Reporte Final.pdf')


@STORAGE_LOCAL
class LaRutaSeparaLasEmpresasTests(BaseRutas):

    def test_la_ruta_lleva_el_subdominio_de_la_empresa(self):
        doc = self._documento('report.pdf')

        self.assertIn('/norte/', doc.file.name)

    def test_dos_empresas_no_comparten_carpeta(self):
        """Sin el tenant en la ruta, un archivo de una podía pisar el de la otra."""
        una = self._documento('report.pdf', tenant=self.tenant)
        otra = self._documento('report.pdf', tenant=self.otro)

        self.assertIn('/norte/', una.file.name)
        self.assertIn('/sur/', otra.file.name)

    def test_sin_tenant_propio_se_toma_el_de_la_operacion(self):
        """El documento puede llegar sin tenant; el de su operación es el mismo.

        Cinco de los nueve documentos que había en producción tenían el campo en
        NULL, porque la vista que crea la operación no lo pasaba.
        """
        doc = OperationDocument.objects.create(
            operation=self._operacion(), original_name='report.pdf',
            file=SimpleUploadedFile('report.pdf', b'contenido'))

        self.assertIn('/norte/', doc.file.name)

    def test_sin_empresa_por_ningun_lado_no_revienta(self):
        """Un huérfano de verdad va a su propio cajón, no a la raíz."""
        operacion = WarehouseOperation.objects.create(
            tenant=None, operation_type='ENTRY', date=date.today(),
            custom_id='ED260822-9999', description='Sin empresa',
            created_by=self.admin)
        doc = OperationDocument.objects.create(
            operation=operacion, original_name='report.pdf',
            file=SimpleUploadedFile('report.pdf', b'contenido'))

        self.assertIn('/sin-empresa/', doc.file.name)


@STORAGE_LOCAL
class LaRutaNoSeAdivinaTests(BaseRutas):

    def test_lleva_un_identificador_que_no_se_puede_predecir(self):
        """El bucket se sirve por un dominio público: la ruta es la cerradura."""
        doc = self._documento('report.pdf')

        ultimo = doc.file.name.rsplit('/', 1)[-1]
        self.assertRegex(ultimo, r'^[0-9a-f]{12}-report\.pdf$')

    def test_el_identificador_cambia_en_cada_archivo(self):
        rutas = {self._documento('report.pdf').file.name for _ in range(5)}

        self.assertEqual(len(rutas), 5)


@STORAGE_LOCAL
class ElNombreEnLaRutaEsSeguroTests(BaseRutas):

    def test_los_espacios_y_las_tildes_no_llegan_a_la_ruta(self):
        """Un carácter fuera del ASCII sobrevive, pero llega percent-encoded a
        la URL y estorba en cada diagnóstico sobre el bucket."""
        doc = self._documento('Guía de Remisión.pdf')

        self.assertRegex(doc.file.name, r'^[A-Za-z0-9/._-]+$')
        self.assertTrue(doc.file.name.endswith('guia-de-remision.pdf'))

    def test_un_archivo_sin_extension_no_deja_la_ruta_terminada_en_punto(self):
        doc = self._documento('CONTRATO')

        self.assertFalse(doc.file.name.endswith('.'))
        self.assertTrue(doc.file.name.endswith('-contrato'))

    def test_un_nombre_que_es_todo_signos_no_deja_la_ruta_sin_nombre(self):
        doc = self._documento('¿¡...!?.pdf')

        self.assertTrue(doc.file.name.endswith('-archivo.pdf'))

    def test_la_extension_tambien_se_sanea(self):
        """La extensión es parte del nombre que pone quien sube el archivo:
        `imagen.jpg;v=2` metería un `;` y un `=` en la URL pública.

        Se mira la función y no el documento guardado porque Django vuelve a
        limpiar el último tramo con `get_valid_filename` al pasar por el
        almacén, y eso taparía el resultado. Que hoy haya dos redes no es razón
        para que esta función devuelva una ruta en la que no se puede confiar:
        se la llama para construir la ruta, no solo para dársela al almacén.
        """
        ruta = ruta_documento(None, 'imagen.jpg;v=2')

        self.assertRegex(ruta, r'^[A-Za-z0-9/._-]+$')

    def test_la_extension_se_guarda_en_minusculas(self):
        """Dos rutas que solo difieren en mayúsculas son la misma para unos
        sistemas y distintas para otros; conviene no depender de eso."""
        doc = self._documento('CONTRATO.PDF')

        self.assertTrue(doc.file.name.endswith('.pdf'))

    def test_la_extension_no_se_usa_para_colar_una_carpeta(self):
        ruta = ruta_documento(None, 'informe.pdf/../../secreto')

        self.assertNotIn('..', ruta)

    def test_un_nombre_larguisimo_cabe_con_el_prefijo_de_la_papelera(self):
        """Se le añade `papelera/` al archivar, y el campo tiene un tope."""
        largo = 'a' * 300 + '.pdf'
        ruta = ruta_documento(None, largo)

        tope = OperationDocument._meta.get_field('file').max_length
        self.assertLessEqual(len(PREFIJO_PAPELERA + ruta), tope)


@STORAGE_LOCAL
class LaFechaSigueEnLaRutaTests(BaseRutas):

    def test_la_ruta_lleva_el_dia_en_que_se_subio(self):
        """Es lo que permite purgar y diagnosticar mirando el bucket."""
        doc = self._documento('report.pdf')

        self.assertIn(timezone.localtime().strftime('%Y/%m/%d'), doc.file.name)


@STORAGE_LOCAL
class ElAltaAsignaLaEmpresaAlDocumentoTests(BaseRutas):
    """De dónde salieron los cinco documentos con el tenant en NULL.

    `digital_upload` pasaba `tenant=tenant` al crear el documento; la vista que
    crea la operación, no. Así que todo archivo adjuntado en el momento del alta
    quedaba sin empresa, y en la ruta nueva eso lo mandaría al cajón de los
    huérfanos aunque su operación tenga dueño.
    """

    def setUp(self):
        self.client.force_login(self.admin)
        self.shipper = Catalog.objects.create(
            tenant=self.tenant, category='SHIPPER', name='Shipper')
        self.carrier = Catalog.objects.create(
            tenant=self.tenant, category='CARRIER', name='Carrier')
        self.bulto = Catalog.objects.create(
            tenant=self.tenant, category='BUNDLE_TYPE', name='Tarima')

    def _alta(self, archivos):
        return self.client.post('/operations/create/', {
            'operation_type': 'ENTRY', 'date': '2026-08-22',
            'customer_id': str(self.cliente.pk),
            'shipper_id': str(self.shipper.pk),
            'carrier_id': str(self.carrier.pk),
            'bundle_type_id': str(self.bulto.pk),
            'bundle_qty': '3', 'weight_lbs': '150',
            'description': 'Mercancia de prueba',
            **archivos,
        })

    def test_una_foto_adjuntada_al_crear_queda_con_su_empresa(self):
        self._alta({'photos': SimpleUploadedFile('foto.jpg', b'bytes')})

        doc = OperationDocument.objects.get(file_type='PHOTO')
        self.assertEqual(doc.tenant, self.tenant)
        self.assertIn('/norte/', doc.file.name)

    def test_un_documento_adjuntado_al_crear_queda_con_su_empresa(self):
        self._alta({'documents': SimpleUploadedFile('guia.pdf', b'bytes')})

        doc = OperationDocument.objects.get(file_type='DOCUMENT')
        self.assertEqual(doc.tenant, self.tenant)
        self.assertIn('/norte/', doc.file.name)


class ElSobrescribirEstaApagadoTests(TestCase):
    """La red debajo de `ruta_documento`, y no se puede probar funcionalmente.

    Las pruebas corren contra el sistema de archivos local, que añade un sufijo
    por su cuenta; el que pisaba el objeto era el backend de S3 con su valor por
    omisión. Así que se mira la configuración directamente.
    """

    def test_aws_s3_file_overwrite_es_falso(self):
        self.assertIs(getattr(settings, 'AWS_S3_FILE_OVERWRITE', True), False)
