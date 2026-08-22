"""
Quien puede abrir un archivo del expediente, y por donde sale.

Hasta ahora la pantalla enlazaba al bucket de R2 directamente. Ese enlace no
lleva credencial —`AWS_S3_CUSTOM_DOMAIN` hace que `FieldFile.url` devuelva
`https://<dominio>/<ruta>` sin firma— y para que funcione el bucket tiene que
estar publicado. De ahí las tres propiedades que fijan estas pruebas, y que
antes no se cumplía ninguna:

* **Hay que estar dentro y tener permiso.** El aislamiento es exactamente el de
  `operation_detail`: mismo tenant, y el cliente solo lo suyo.
* **El enlace que llega al bucket caduca.** La vista firma una URL de vida
  corta en cada visita; el enlace que guarda el navegador es el del sistema.
* **El dominio del bucket deja de salir en el HTML.** Era lo que ponía la ruta
  al alcance de cualquier usuario, cliente incluido.

Las pruebas corren sobre el sistema de archivos local, que no sabe firmar: ahí
la vista sirve el archivo ella misma. Lo específico de R2 se prueba aparte
contra `url_firmada`, con un almacén de mentira, porque no hay bucket en las
pruebas.
"""
import tempfile
from datetime import date
from unittest import mock

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from .almacen import url_firmada
from .models import Catalog, OperationDocument, Tenant, UserProfile, WarehouseOperation

STORAGE_LOCAL = override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage',
                'OPTIONS': {'location': tempfile.mkdtemp()}},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})


class BaseArchivos(TestCase):
    """Un tenant con dos clientes, y otro tenant enfrente."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')
        cls.otro_tenant = Tenant.objects.create(
            name='Bodegas del Sur', type='organization', subdomain='sur')

        cls.cliente_a = Catalog.objects.create(
            tenant=cls.tenant, category='CUSTOMER', name='Cliente A')
        cls.cliente_b = Catalog.objects.create(
            tenant=cls.tenant, category='CUSTOMER', name='Cliente B')

        cls.admin = User.objects.create_user('admin_archivos', password='x')
        UserProfile.objects.create(user=cls.admin, tenant=cls.tenant, role='admin')

        cls.operador = User.objects.create_user('staff_archivos', password='x')
        UserProfile.objects.create(user=cls.operador, tenant=cls.tenant, role='staff')

        cls.usuario_a = User.objects.create_user('cliente_a', password='x')
        UserProfile.objects.create(user=cls.usuario_a, tenant=cls.tenant,
                                   role='customer', customer=cls.cliente_a)

        cls.usuario_b = User.objects.create_user('cliente_b', password='x')
        UserProfile.objects.create(user=cls.usuario_b, tenant=cls.tenant,
                                   role='customer', customer=cls.cliente_b)

        cls.ajeno = User.objects.create_user('admin_del_sur', password='x')
        UserProfile.objects.create(user=cls.ajeno, tenant=cls.otro_tenant, role='admin')

    _consecutivo = 0

    def _operacion(self, tenant=None, cliente=None):
        type(self)._consecutivo += 1
        return WarehouseOperation.objects.create(
            tenant=tenant or self.tenant, operation_type='ENTRY',
            date=date.today(), custom_id='ED-%04d' % type(self)._consecutivo,
            customer=cliente if cliente is not None else self.cliente_a,
            description='Mercancia', created_by=self.admin)

    def _documento(self, operacion=None, nombre='Reporte Final.pdf',
                   contenido=b'el contenido del archivo'):
        operacion = operacion or self._operacion()
        return OperationDocument.objects.create(
            tenant=operacion.tenant, operation=operacion, original_name=nombre,
            file=SimpleUploadedFile(nombre, contenido))

    def _url(self, doc, descarga=False):
        return '/documents/%s/file/%s' % (doc.pk, '?download=1' if descarga else '')

    def _leer(self, respuesta):
        return b''.join(respuesta.streaming_content)


@STORAGE_LOCAL
class ElAccesoSeComprueba(BaseArchivos):

    def test_quien_no_ha_entrado_no_recibe_el_archivo(self):
        """Era el agujero de fondo: el enlace público servía a cualquiera."""
        doc = self._documento()

        respuesta = self.client.get(self._url(doc))

        self.assertEqual(respuesta.status_code, 302)
        self.assertIn('next=', respuesta['Location'])

    def test_un_usuario_del_tenant_abre_el_archivo(self):
        doc = self._documento()
        self.client.force_login(self.operador)

        respuesta = self.client.get(self._url(doc))

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(self._leer(respuesta), b'el contenido del archivo')

    def test_un_usuario_de_otra_empresa_no_lo_encuentra(self):
        """404 y no 403: para el de enfrente ese documento no existe."""
        doc = self._documento()
        self.client.force_login(self.ajeno)

        self.assertEqual(self.client.get(self._url(doc)).status_code, 404)

    def test_el_cliente_abre_los_archivos_de_sus_operaciones(self):
        doc = self._documento(self._operacion(cliente=self.cliente_a))
        self.client.force_login(self.usuario_a)

        self.assertEqual(self.client.get(self._url(doc)).status_code, 200)

    def test_el_cliente_no_abre_los_de_otro_cliente(self):
        """Mismo criterio que operation_detail, para que no se separen."""
        doc = self._documento(self._operacion(cliente=self.cliente_b))
        self.client.force_login(self.usuario_a)

        self.assertEqual(self.client.get(self._url(doc)).status_code, 403)

    def test_un_documento_sin_archivo_no_revienta(self):
        """Hay al menos una fila así en producción, sin objeto en el bucket."""
        doc = self._documento()
        doc.file = ''
        doc.save(update_fields=['file'])
        self.client.force_login(self.operador)

        self.assertEqual(self.client.get(self._url(doc)).status_code, 404)

    def test_el_pk_de_otro_tenant_no_se_alcanza_ni_existiendo(self):
        """El tenant sale de la operación, no del documento, y tiene que filtrar."""
        doc = self._documento(self._operacion(tenant=self.otro_tenant, cliente=None))
        self.client.force_login(self.admin)

        self.assertEqual(self.client.get(self._url(doc)).status_code, 404)

    def test_un_documento_viejo_sin_tenant_propio_sigue_abriendose(self):
        """Cinco filas quedaron con el campo en NULL; no deben quedar mudas."""
        doc = self._documento()
        doc.tenant = None
        doc.save(update_fields=['tenant'])
        self.client.force_login(self.operador)

        self.assertEqual(self.client.get(self._url(doc)).status_code, 200)


@STORAGE_LOCAL
class LaPapeleraSacaDeLaVista(BaseArchivos):

    def test_archivado_deja_de_abrirse_para_quien_opera(self):
        """Archivar significa que ya no está; si el enlace sigue sirviendo, no lo hace."""
        doc = self._documento()
        doc.archivar(self.admin, 'subido por error')
        self.client.force_login(self.operador)

        self.assertEqual(self.client.get(self._url(doc)).status_code, 404)

    def test_quien_ve_la_papelera_si_lo_abre(self):
        """Restaurar a ciegas no es una decisión; hay que poder mirar antes."""
        doc = self._documento()
        doc.archivar(self.admin, 'subido por error')
        self.client.force_login(self.admin)

        self.assertEqual(self.client.get(self._url(doc)).status_code, 200)

    def test_el_cliente_no_abre_lo_archivado_aunque_sea_suyo(self):
        doc = self._documento(self._operacion(cliente=self.cliente_a))
        doc.archivar(self.admin, 'subido por error')
        self.client.force_login(self.usuario_a)

        self.assertEqual(self.client.get(self._url(doc)).status_code, 404)


@STORAGE_LOCAL
class LaDescargaLlevaElNombreDeVerdad(BaseArchivos):

    def test_con_download_el_archivo_se_baja_con_su_nombre_original(self):
        """La ruta lleva un identificador aleatorio delante; el usuario no lo vio nunca."""
        doc = self._documento(nombre='Reporte Final.pdf')
        self.client.force_login(self.operador)

        respuesta = self.client.get(self._url(doc, descarga=True))

        self.assertIn('attachment', respuesta['Content-Disposition'])
        self.assertIn('Reporte Final.pdf', respuesta['Content-Disposition'])

    def test_sin_download_se_abre_en_el_navegador(self):
        doc = self._documento()
        self.client.force_login(self.operador)

        respuesta = self.client.get(self._url(doc))

        self.assertNotIn('attachment', respuesta.get('Content-Disposition', ''))


@STORAGE_LOCAL
class LaPantallaYaNoEnlazaAlBucket(BaseArchivos):

    def test_el_expediente_enlaza_a_la_vista_y_no_al_almacen(self):
        """El dominio del bucket salía en el HTML, así que lo tenía cualquiera."""
        doc = self._documento()
        self.client.force_login(self.operador)

        respuesta = self.client.get('/operations/%s/' % doc.operation.pk)

        self.assertContains(respuesta, '/documents/%s/file/' % doc.pk)
        self.assertNotContains(respuesta, doc.file.url)


class AlmacenFalso:
    """Un almacén que se comporta como el de R2 en lo único que importa aquí."""

    def __init__(self, custom_domain='pub-ejemplo.r2.dev'):
        self.bucket_name = 'wms2'
        self.custom_domain = custom_domain
        self.querystring_auth = True

    def url(self, name, parameters=None, expire=None, http_method=None):
        if self.custom_domain:
            return 'https://%s/%s' % (self.custom_domain, name)
        return 'https://cuenta.r2.cloudflarestorage.com/wms2/%s?X-Amz-Signature=abc' % name


class ArchivoFalso:
    def __init__(self, almacen, name='operations/norte/2026/08/22/abc123-report.pdf'):
        self.storage = almacen
        self.name = name


class LaFirmaEsquivaElDominioPublico(TestCase):
    """
    Lo específico de R2, contra un almacén de mentira: en las pruebas no hay
    bucket, y estas propiedades son justo las que deciden si el enlace
    entregado caduca o no.
    """

    def _espiar(self, capturado):
        original = AlmacenFalso.url

        def espia(self, name, parameters=None, expire=None, http_method=None):
            capturado['parameters'] = parameters
            capturado['expire'] = expire
            return original(self, name, parameters, expire, http_method)

        return mock.patch.object(AlmacenFalso, 'url', espia)

    def test_la_url_no_sale_por_el_dominio_publico(self):
        """Mientras `custom_domain` esté puesto, `url()` devuelve el enlace sin firma."""
        almacen = AlmacenFalso()

        firmada = url_firmada(ArchivoFalso(almacen))

        self.assertNotIn('pub-ejemplo.r2.dev', firmada)
        self.assertIn('X-Amz-Signature', firmada)

    def test_el_almacen_original_no_se_toca(self):
        """La copia es superficial y compartida; mutar el original sería una bomba."""
        almacen = AlmacenFalso()

        url_firmada(ArchivoFalso(almacen))

        self.assertEqual(almacen.custom_domain, 'pub-ejemplo.r2.dev')

    def test_el_enlace_dura_poco(self):
        capturado = {}

        with self._espiar(capturado):
            url_firmada(ArchivoFalso(AlmacenFalso()))

        self.assertEqual(capturado['expire'], 300)

    def test_la_descarga_pide_al_bucket_el_nombre_original(self):
        """El atributo `download` del enlace no sirve: el archivo viene de otro dominio."""
        capturado = {}

        with self._espiar(capturado):
            url_firmada(ArchivoFalso(AlmacenFalso()), descargar_como='Reporte Final.pdf')

        disposicion = capturado['parameters']['ResponseContentDisposition']
        self.assertIn('attachment', disposicion)
        self.assertIn('Reporte Final.pdf', disposicion)

    def test_sin_descarga_no_se_le_pide_nada_al_bucket(self):
        capturado = {}

        with self._espiar(capturado):
            url_firmada(ArchivoFalso(AlmacenFalso()))

        self.assertIsNone(capturado['parameters'])

    def test_sin_almacen_de_s3_no_inventa_una_firma(self):
        """El sistema de archivos local no firma; la vista tiene que enterarse."""
        class Local:
            pass

        self.assertIsNone(url_firmada(ArchivoFalso(Local())))

    def test_un_fallo_al_firmar_no_tumba_la_pantalla(self):
        """Firmar habla con boto3; si no puede, el archivo sale por el servidor."""
        def revienta(self, *a, **kw):
            raise RuntimeError('sin credenciales')

        with mock.patch.object(AlmacenFalso, 'url', revienta):
            self.assertIsNone(url_firmada(ArchivoFalso(AlmacenFalso())))

    def test_sin_archivo_no_hay_enlace(self):
        self.assertIsNone(url_firmada(ArchivoFalso(AlmacenFalso(), name='')))
        self.assertIsNone(url_firmada(None))


@STORAGE_LOCAL
class LaVistaEntregaLaFirmaYNoLaGuarda(BaseArchivos):
    """
    Con R2 detrás la vista redirige en vez de servir el archivo. Se comprueba
    aquí con la firma simulada, porque el almacén de las pruebas es local.
    """

    def test_redirige_a_la_url_firmada(self):
        doc = self._documento()
        self.client.force_login(self.operador)
        firmada = 'https://cuenta.r2.cloudflarestorage.com/wms2/x?X-Amz-Signature=abc'

        with mock.patch('warehouse.views.url_firmada', return_value=firmada):
            respuesta = self.client.get(self._url(doc))

        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(respuesta['Location'], firmada)

    def test_el_navegador_no_se_queda_con_el_redirect(self):
        """La firma caduca y el enlace del sistema no: hay que volver a preguntar."""
        doc = self._documento()
        self.client.force_login(self.operador)

        with mock.patch('warehouse.views.url_firmada', return_value='https://x/y'):
            respuesta = self.client.get(self._url(doc))

        self.assertIn('no-store', respuesta['Cache-Control'])

    def test_el_permiso_se_comprueba_antes_de_firmar(self):
        """Firmar es entregar la llave; no puede pasar antes del control."""
        doc = self._documento(self._operacion(cliente=self.cliente_b))
        self.client.force_login(self.usuario_a)

        with mock.patch('warehouse.views.url_firmada') as firma:
            respuesta = self.client.get(self._url(doc))

        self.assertEqual(respuesta.status_code, 403)
        firma.assert_not_called()
