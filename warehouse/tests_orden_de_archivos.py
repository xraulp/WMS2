"""
El orden de los archivos del expediente, y cómo sale en el ZIP.

En una entrada se fotografía la misma pieza varias veces —el número de serie o
de lote, el peso en kilos o libras, la tabla nutrimental— y la documentación
aduanal se arma siguiendo esa secuencia. Si el ZIP entrega las fotos en otro
orden, lo que llega al agente aduanal está mal aunque no falte ningún archivo.
El orden es información, no presentación.

Nada lo garantizaba. Estas pruebas fijan las tres cosas que lo rompían:

* **Ninguna consulta pedía orden**, así que PostgreSQL devolvía las filas como
  le convenía. Coincidía con el de inserción por casualidad y se rompía en
  cuanto una fila se actualizaba: archivar y restaurar mandaba el documento al
  final de la lista.
* **La numeración no llevaba ceros**, de modo que con diez archivos o más el
  explorador los mostraba 1, 10, 11, 2, 3… aunque dentro del ZIP fueran en
  orden. Van a tres cifras porque una operación puede pasar de cien fotos.
* **Agrupaba por extensión y no por tipo**, así que una foto `.jpeg` y otra
  `.jpg` abrían dos series y aparecían dos «foto 1» distintas.
"""
import tempfile
import zipfile
from datetime import date
from io import BytesIO

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from .models import Catalog, OperationDocument, Tenant, UserProfile, WarehouseOperation
from .views import _ancho_de_numeracion

STORAGE_LOCAL = override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage',
                'OPTIONS': {'location': tempfile.mkdtemp()}},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})


@STORAGE_LOCAL
class BaseOrden(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')
        cls.admin = User.objects.create_user('admin_orden', password='x')
        UserProfile.objects.create(user=cls.admin, tenant=cls.tenant, role='admin')
        cls.cliente = Catalog.objects.create(
            tenant=cls.tenant, category='CUSTOMER', name='ACME', abbreviation='ACM')

    def setUp(self):
        self.op = WarehouseOperation.objects.create(
            tenant=self.tenant, operation_type='ENTRY', date=date.today(),
            custom_id='ED-0001', customer=self.cliente, po_order='PO123',
            description='Mercancia', created_by=self.admin)
        self.client.force_login(self.admin)

    def _subir(self, nombre, tipo='PHOTO'):
        return OperationDocument.objects.create(
            tenant=self.tenant, operation=self.op, file_type=tipo,
            original_name=nombre, file=SimpleUploadedFile(nombre, b'x'))

    def _nombres_del_zip(self):
        respuesta = self.client.get('/operations/%s/download-all/' % self.op.pk)
        self.assertEqual(respuesta.status_code, 200)
        with zipfile.ZipFile(BytesIO(respuesta.content)) as zf:
            return zf.namelist()


class ElZipRespetaElOrdenDeSubida(BaseOrden):

    def test_las_fotos_salen_en_el_orden_en_que_se_tomaron(self):
        """Serie, peso y tabla nutrimental: ese orden es el que vale."""
        self._subir('serie.jpg')
        self._subir('peso.jpg')
        self._subir('nutrimental.jpg')

        nombres = self._nombres_del_zip()

        self.assertEqual([n[-7:] for n in nombres],
                         ['001.jpg', '002.jpg', '003.jpg'])

    def test_un_documento_restaurado_no_se_va_al_final(self):
        """Actualizar una fila la movia al final; archivar y restaurar lo hace."""
        primera = self._subir('serie.jpg')
        self._subir('peso.jpg')
        self._subir('nutrimental.jpg')

        primera.archivar(self.admin, 'me equivoque')
        primera.restaurar()

        orden = list(self.op.documents.values_list('original_name', flat=True))
        self.assertEqual(orden, ['serie.jpg', 'peso.jpg', 'nutrimental.jpg'])

    def test_lo_que_esta_en_la_papelera_no_entra_al_zip(self):
        self._subir('serie.jpg')
        descartada = self._subir('borrosa.jpg')
        descartada.archivar(self.admin, 'salio movida')

        self.assertEqual(len(self._nombres_del_zip()), 1)


class LaNumeracionSeLeeEnOrden(BaseOrden):

    def test_con_diez_o_mas_los_numeros_llevan_ceros_delante(self):
        """Sin los ceros, el explorador ordena 1, 10, 11, 2, 3... y rompe la secuencia."""
        for i in range(1, 13):
            self._subir('foto%02d.jpg' % i)

        nombres = sorted(self._nombres_del_zip())

        self.assertTrue(nombres[0].endswith('001.jpg'), nombres[0])
        self.assertTrue(nombres[1].endswith('002.jpg'), nombres[1])
        self.assertTrue(nombres[-1].endswith('012.jpg'), nombres[-1])

    def test_el_orden_alfabetico_del_zip_coincide_con_el_de_subida(self):
        """Es la propiedad que de verdad importa: la carpeta se ve en orden."""
        subidas = ['serie.jpg', 'peso.jpg', 'nutrimental.jpg', 'etiqueta.jpg',
                   'sello.jpg', 'tarima.jpg', 'caja.jpg', 'lote.jpg',
                   'danio.jpg', 'general.jpg', 'extra.jpg']
        for nombre in subidas:
            self._subir(nombre)

        nombres = self._nombres_del_zip()

        self.assertEqual(nombres, sorted(nombres))

    def test_con_pocos_archivos_igual_llevan_tres_cifras(self):
        """Un ancho fijo mantiene los nombres comparables entre operaciones."""
        self._subir('serie.jpg')

        self.assertTrue(self._nombres_del_zip()[0].endswith('001.jpg'))


class CadaTipoLLevaSuPropiaSerie(BaseOrden):

    def test_jpg_y_jpeg_no_abren_dos_series(self):
        """Agrupar por extension daba dos "foto 1" en la misma carpeta."""
        self._subir('serie.jpg')
        self._subir('peso.jpeg')
        self._subir('nutrimental.jpg')

        nombres = self._nombres_del_zip()

        self.assertEqual(len(set(nombres)), 3)
        self.assertEqual(sorted(nombres), [
            'ACM PO123 ED-0001 001.jpg',
            'ACM PO123 ED-0001 002.jpeg',
            'ACM PO123 ED-0001 003.jpg',
        ])

    def test_las_fotos_y_los_pdf_cuentan_por_separado(self):
        """Es lo que se pidio: cada tipo con su consecutivo."""
        self._subir('serie.jpg')
        self._subir('peso.jpg')
        self._subir('factura.pdf', tipo='DOCUMENT')

        nombres = self._nombres_del_zip()

        self.assertIn('ACM PO123 ED-0001 001.pdf', nombres)
        self.assertEqual(sorted(n for n in nombres if n.endswith('.jpg')),
                         ['ACM PO123 ED-0001 001.jpg', 'ACM PO123 ED-0001 002.jpg'])

    def test_cada_archivo_conserva_su_contenido(self):
        """Renombrar dentro del ZIP no puede mezclar los contenidos."""
        OperationDocument.objects.create(
            tenant=self.tenant, operation=self.op, file_type='PHOTO',
            original_name='serie.jpg', file=SimpleUploadedFile('serie.jpg', b'la serie'))
        OperationDocument.objects.create(
            tenant=self.tenant, operation=self.op, file_type='PHOTO',
            original_name='peso.jpg', file=SimpleUploadedFile('peso.jpg', b'el peso'))

        respuesta = self.client.get('/operations/%s/download-all/' % self.op.pk)
        with zipfile.ZipFile(BytesIO(respuesta.content)) as zf:
            nombres = zf.namelist()
            self.assertEqual(zf.read(nombres[0]), b'la serie')
            self.assertEqual(zf.read(nombres[1]), b'el peso')


class ElAnchoDeLaNumeracion(TestCase):
    """
    Cuantas cifras lleva el consecutivo. Se prueba sobre la funcion y no
    subiendo archivos porque los casos que importan -cien fotos, mil- no se
    pueden montar en una prueba sin volverla lentisima, y son justo los que
    decidieron el minimo.
    """

    def test_el_minimo_son_tres_cifras(self):
        """Una operacion puede pasar de cien fotos; con dos, volvia el desorden."""
        for cantidad in (1, 5, 99, 100, 999):
            with self.subTest(cantidad=cantidad):
                self.assertEqual(_ancho_de_numeracion(cantidad), 3)

    def test_si_hicieran_falta_mas_el_ancho_crece(self):
        self.assertEqual(_ancho_de_numeracion(1000), 4)
        self.assertEqual(_ancho_de_numeracion(12345), 5)
