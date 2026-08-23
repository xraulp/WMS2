"""
Reordenar los archivos del expediente a mano.

El orden de las fotos es información: en una entrada se fotografía la misma
pieza varias veces —el número de serie o de lote, el peso, la tabla
nutrimental— y la documentación aduanal se arma siguiendo esa secuencia.

El orden de subida la conserva cuando las fotos se toman y se suben una a una
desde el móvil. No la conserva cuando el operador las selecciona de un tirón
desde la PC: ahí el navegador las manda en el orden que le parece —normalmente
alfabético— y la secuencia nace mal. Hasta ahora la única salida era borrarlas
y volver a subirlas una por una.

Lo que fijan estas pruebas:

* Mover un archivo cambia su sitio **y el del ZIP**, que es donde el orden
  acaba importando.
* Los expedientes que ya existían, con la posición en cero, siguen exactamente
  donde estaban y se reordenan sin sorpresas la primera vez que alguien toca
  una flecha.
* Un archivo subido después de reordenar cae **al final**, no al principio.
* Quien no puede ver la operación tampoco puede reordenarla.
"""
import tempfile
import zipfile
from datetime import date
from io import BytesIO

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from .models import Catalog, OperationDocument, Tenant, UserProfile, WarehouseOperation

STORAGE_LOCAL = override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage',
                'OPTIONS': {'location': tempfile.mkdtemp()}},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})


@STORAGE_LOCAL
class BaseReordenar(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')
        cls.otro_tenant = Tenant.objects.create(
            name='Bodegas del Sur', type='organization', subdomain='sur')

        cls.cliente_a = Catalog.objects.create(
            tenant=cls.tenant, category='CUSTOMER', name='Cliente A',
            abbreviation='CLA')
        cls.cliente_b = Catalog.objects.create(
            tenant=cls.tenant, category='CUSTOMER', name='Cliente B')

        cls.operador = User.objects.create_user('staff_orden', password='x')
        UserProfile.objects.create(user=cls.operador, tenant=cls.tenant, role='staff')

        cls.usuario_a = User.objects.create_user('cliente_a', password='x')
        UserProfile.objects.create(user=cls.usuario_a, tenant=cls.tenant,
                                   role='customer', customer=cls.cliente_a)

        cls.ajeno = User.objects.create_user('admin_del_sur', password='x')
        UserProfile.objects.create(user=cls.ajeno, tenant=cls.otro_tenant, role='admin')

    _consecutivo = 0

    def setUp(self):
        type(self)._consecutivo += 1
        self.op = WarehouseOperation.objects.create(
            tenant=self.tenant, operation_type='ENTRY', date=date.today(),
            custom_id='ED-%04d' % type(self)._consecutivo, customer=self.cliente_a,
            po_order='PO1', description='Mercancia', created_by=self.operador)
        self.client.force_login(self.operador)

    def _doc(self, nombre, orden=0, operacion=None):
        return OperationDocument.objects.create(
            tenant=self.tenant, operation=operacion or self.op, file_type='PHOTO',
            original_name=nombre, orden=orden,
            file=SimpleUploadedFile(nombre, nombre.encode()))

    def _mover(self, doc, direccion):
        return self.client.post('/digital/file/%s/reorder/' % doc.pk,
                                {'direccion': direccion})

    def _nombres(self, operacion=None):
        op = operacion or self.op
        return list(op.documents.values_list('original_name', flat=True))


class MoverUnArchivo(BaseReordenar):

    def test_subir_una_posicion(self):
        self._doc('serie.jpg')
        peso = self._doc('peso.jpg')
        self._doc('nutrimental.jpg')

        self._mover(peso, 'arriba')

        self.assertEqual(self._nombres(),
                         ['peso.jpg', 'serie.jpg', 'nutrimental.jpg'])

    def test_bajar_una_posicion(self):
        serie = self._doc('serie.jpg')
        self._doc('peso.jpg')
        self._doc('nutrimental.jpg')

        self._mover(serie, 'abajo')

        self.assertEqual(self._nombres(),
                         ['peso.jpg', 'serie.jpg', 'nutrimental.jpg'])

    def test_el_primero_no_puede_subir_mas(self):
        """No es un error: es el operador dándole a una flecha que no lleva a nada."""
        serie = self._doc('serie.jpg')
        self._doc('peso.jpg')

        respuesta = self._mover(serie, 'arriba')

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(self._nombres(), ['serie.jpg', 'peso.jpg'])

    def test_el_ultimo_no_puede_bajar_mas(self):
        self._doc('serie.jpg')
        peso = self._doc('peso.jpg')

        respuesta = self._mover(peso, 'abajo')

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(self._nombres(), ['serie.jpg', 'peso.jpg'])

    def test_varios_movimientos_llevan_una_foto_de_abajo_arriba(self):
        """El caso de verdad: la foto que debía ir primera llegó la última."""
        self._doc('peso.jpg')
        self._doc('nutrimental.jpg')
        serie = self._doc('serie.jpg')

        self._mover(serie, 'arriba')
        self._mover(serie, 'arriba')

        self.assertEqual(self._nombres(),
                         ['serie.jpg', 'peso.jpg', 'nutrimental.jpg'])

    def test_las_posiciones_quedan_sin_huecos(self):
        self._doc('a.jpg')
        b = self._doc('b.jpg')
        self._doc('c.jpg')

        self._mover(b, 'arriba')

        self.assertEqual(
            sorted(self.op.documents.values_list('orden', flat=True)), [1, 2, 3])


class LosExpedientesViejosNoSeRompen(BaseReordenar):

    def test_sin_reordenar_manda_la_fecha_de_subida(self):
        """Todo vale cero mientras nadie toque una flecha: el orden es el de siempre."""
        self._doc('serie.jpg')
        self._doc('peso.jpg')
        self._doc('nutrimental.jpg')

        self.assertEqual(self._nombres(),
                         ['serie.jpg', 'peso.jpg', 'nutrimental.jpg'])
        self.assertEqual(list(self.op.documents.values_list('orden', flat=True)),
                         [0, 0, 0])

    def test_el_primer_movimiento_numera_todo_el_expediente(self):
        """Renumerar entero es lo que arregla de una vez los expedientes viejos."""
        self._doc('serie.jpg')
        self._doc('peso.jpg')
        nutrimental = self._doc('nutrimental.jpg')

        self._mover(nutrimental, 'arriba')

        self.assertEqual(self._nombres(),
                         ['serie.jpg', 'nutrimental.jpg', 'peso.jpg'])
        self.assertEqual(
            sorted(self.op.documents.values_list('orden', flat=True)), [1, 2, 3])


class LoQueSeSubeDespuesVaAlFinal(BaseReordenar):

    def test_un_archivo_nuevo_no_se_cuela_al_principio(self):
        """Con la posición en cero se colaría delante de todo lo ya ordenado."""
        self._doc('serie.jpg')
        peso = self._doc('peso.jpg')
        self._mover(peso, 'arriba')

        self.client.post('/digital/%s/upload/' % self.op.pk,
                         {'files': SimpleUploadedFile('tardia.jpg', b'x')})

        self.assertEqual(self._nombres()[-1], 'tardia.jpg')

    def test_una_subida_multiple_conserva_su_propio_orden(self):
        self.client.post('/digital/%s/upload/' % self.op.pk, {'files': [
            SimpleUploadedFile('uno.jpg', b'1'),
            SimpleUploadedFile('dos.jpg', b'2'),
            SimpleUploadedFile('tres.jpg', b'3'),
        ]})

        self.assertEqual(self._nombres(), ['uno.jpg', 'dos.jpg', 'tres.jpg'])


class ElZipSigueElOrdenPuestoAMano(BaseReordenar):

    def test_el_consecutivo_del_zip_respeta_el_reordenado(self):
        """Es donde el orden acaba importando: el nombre que ve el agente aduanal."""
        self._doc('peso.jpg')
        serie = self._doc('serie.jpg')

        self._mover(serie, 'arriba')

        respuesta = self.client.get('/operations/%s/download-all/' % self.op.pk)
        with zipfile.ZipFile(BytesIO(respuesta.content)) as zf:
            nombres = zf.namelist()
            self.assertTrue(nombres[0].endswith('001.jpg'))
            self.assertEqual(zf.read(nombres[0]), b'serie.jpg')
            self.assertEqual(zf.read(nombres[1]), b'peso.jpg')


class QuienPuedeReordenar(BaseReordenar):

    def test_un_usuario_de_otra_empresa_no_alcanza_el_documento(self):
        doc = self._doc('serie.jpg')
        self._doc('peso.jpg')
        self.client.force_login(self.ajeno)

        respuesta = self._mover(doc, 'abajo')

        self.assertEqual(respuesta.status_code, 404)
        self.assertEqual(self._nombres(), ['serie.jpg', 'peso.jpg'])

    def test_el_cliente_ordena_lo_suyo(self):
        """Mismo criterio que subir: quien puede aportar archivos puede ordenarlos."""
        self._doc('serie.jpg')
        peso = self._doc('peso.jpg')
        self.client.force_login(self.usuario_a)

        self._mover(peso, 'arriba')

        self.assertEqual(self._nombres(), ['peso.jpg', 'serie.jpg'])

    def test_el_cliente_no_ordena_lo_de_otro_cliente(self):
        ajena = WarehouseOperation.objects.create(
            tenant=self.tenant, operation_type='ENTRY', date=date.today(),
            custom_id='ED-OTRO', customer=self.cliente_b, created_by=self.operador)
        doc = self._doc('serie.jpg', operacion=ajena)
        self._doc('peso.jpg', operacion=ajena)
        self.client.force_login(self.usuario_a)

        respuesta = self._mover(doc, 'abajo')

        self.assertEqual(respuesta.status_code, 403)
        self.assertEqual(self._nombres(ajena), ['serie.jpg', 'peso.jpg'])

    def test_quien_no_ha_entrado_no_reordena(self):
        doc = self._doc('serie.jpg')
        self.client.logout()

        respuesta = self._mover(doc, 'arriba')

        self.assertEqual(respuesta.status_code, 302)

    def test_un_archivo_en_la_papelera_no_se_reordena(self):
        """Fuera del expediente no hay posición que ocupar."""
        doc = self._doc('serie.jpg')
        self._doc('peso.jpg')
        doc.archivar(self.operador, 'salio movida')

        respuesta = self._mover(doc, 'abajo')

        self.assertEqual(respuesta.status_code, 404)
