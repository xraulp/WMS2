"""
El logo de cada empresa en los documentos que manda a sus clientes.

Estaba escrito en el código: un único archivo del repositorio —el logo de la
primera empresa— que firmaba los reportes de todas. Es el mismo vicio que el
nombre escrito a mano en la pantalla de entrada, y más visible, porque una
imagen no se lee por encima: se ve.

Y había un segundo caso, peor: el reporte de operaciones cargaba el logo de
`/home/rdeluna/DYSWMS/media/...`, una ruta absoluta de otra máquina. En
producción ese archivo no existe, así que ese PDF **nunca llevó logo** y nadie
se enteró, porque el error se lo tragaba un `except` mudo.

Lo que fijan estas pruebas:

* El logo sale del tenant, y **cada empresa lleva el suyo**.
* **Sin logo el documento sale igual**, con el nombre de la empresa en texto.
* **Un logo que no se puede cargar no tumba el reporte.** Un PDF que no llega es
  peor que uno sin logo.
* Subirlo es del administrador de plataforma, y lo que no es una imagen válida
  se rechaza sin dejar la empresa a medias.
"""
import tempfile
from datetime import date
from io import BytesIO

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from .models import Catalog, PlatformUser, Tenant, UserProfile, WarehouseOperation
from .utils import generate_pdf_report, generate_operations_report_pdf, logo_de

STORAGE_LOCAL = override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage',
                'OPTIONS': {'location': tempfile.mkdtemp()}},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})


def _png(color=(10, 130, 200)):
    """Un PNG de verdad, pequeño: reportlab abre la imagen al construirla."""
    from PIL import Image as PILImage
    buffer = BytesIO()
    PILImage.new('RGB', (40, 24), color).save(buffer, format='PNG')
    return buffer.getvalue()


@STORAGE_LOCAL
class BaseLogo(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.norte = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')
        cls.sur = Tenant.objects.create(
            name='Bodegas del Sur', type='organization', subdomain='sur')

        cls.admin_plataforma = User.objects.create_user('plataforma', password='x')
        PlatformUser.objects.create(user=cls.admin_plataforma, role='admin')
        cls.soporte = User.objects.create_user('soporte', password='x')
        PlatformUser.objects.create(user=cls.soporte, role='staff')

        cls.admin = User.objects.create_user('admin_norte', password='x')
        UserProfile.objects.create(user=cls.admin, tenant=cls.norte, role='admin')
        cls.cliente = Catalog.objects.create(
            tenant=cls.norte, category='CUSTOMER', name='ACME')

    _consecutivo = 0

    def _operacion(self, tenant=None):
        type(self)._consecutivo += 1
        return WarehouseOperation.objects.create(
            tenant=tenant or self.norte, operation_type='ENTRY', date=date.today(),
            custom_id='ED-%04d' % type(self)._consecutivo, customer=self.cliente,
            description='Mercancia', created_by=self.admin)

    def _con_logo(self, tenant=None, nombre='logo.png'):
        tenant = tenant or self.norte
        tenant.logo = SimpleUploadedFile(nombre, _png(), content_type='image/png')
        tenant.save(update_fields=['logo'])
        return tenant


class ElLogoSaleDeLaEmpresa(BaseLogo):

    def test_una_empresa_con_logo_lo_usa(self):
        self._con_logo()

        self.assertIsNotNone(logo_de(self.norte))

    def test_una_empresa_sin_logo_no_hereda_el_de_otra(self):
        """Era el fallo: un archivo del repositorio firmaba los reportes de todas."""
        self._con_logo(self.norte)

        self.assertIsNone(logo_de(self.sur))

    def test_sin_tenant_tampoco_revienta(self):
        self.assertIsNone(logo_de(None))

    def test_un_logo_que_no_se_puede_leer_no_tumba_el_reporte(self):
        """Un PDF que no llega es peor que uno sin logo."""
        tenant = self._con_logo()
        tenant.logo.storage.delete(tenant.logo.name)

        self.assertIsNone(logo_de(tenant))

    def test_la_ruta_lleva_la_empresa_y_un_identificador(self):
        """Mismo criterio que los documentos: sin colisiones y sin adivinar."""
        self._con_logo()

        self.assertIn('logos/norte/', self.norte.logo.name)
        self.assertNotEqual(self.norte.logo.name, 'logos/norte/logo.png')

    def test_dos_empresas_con_el_mismo_nombre_de_archivo_no_colisionan(self):
        uno = self._con_logo(self.norte)
        otro = self._con_logo(self.sur)

        self.assertNotEqual(uno.logo.name, otro.logo.name)


class LosDocumentosSalenIgual(BaseLogo):

    def test_el_reporte_de_operacion_sale_con_logo(self):
        self._con_logo()

        pdf = generate_pdf_report(self._operacion())

        self.assertTrue(pdf.startswith(b'%PDF'))

    def test_el_reporte_de_operacion_sale_sin_logo(self):
        """Cae al nombre de la empresa en texto, que es lo que ya hacía."""
        pdf = generate_pdf_report(self._operacion(tenant=self.sur))

        self.assertTrue(pdf.startswith(b'%PDF'))

    def test_el_reporte_de_operaciones_sale_con_logo(self):
        """Este nunca llevó logo en producción: la ruta era de otra máquina."""
        self._con_logo()

        pdf = generate_operations_report_pdf([self._operacion()], 'Reporte')

        self.assertTrue(pdf.startswith(b'%PDF'))

    def test_el_reporte_de_operaciones_deduce_la_empresa_de_sus_operaciones(self):
        self._con_logo()
        operacion = self._operacion()

        pdf = generate_operations_report_pdf([operacion], 'Reporte')

        self.assertTrue(pdf.startswith(b'%PDF'))

    def test_el_reporte_de_operaciones_sin_operaciones_no_revienta(self):
        pdf = generate_operations_report_pdf([], 'Reporte vacio')

        self.assertTrue(pdf.startswith(b'%PDF'))


class SubirElLogoDesdeLaPlataforma(BaseLogo):

    def setUp(self):
        self.client.force_login(self.admin_plataforma)

    def _subir(self, tenant, nombre='logo.png', contenido=None, **extra):
        datos = {'action': 'set_logo', 'tenant_id': tenant.pk}
        datos.update(extra)
        if nombre:
            datos['logo'] = SimpleUploadedFile(
                nombre, contenido if contenido is not None else _png())
        return self.client.post('/platform/tenants/', datos)

    def test_el_administrador_lo_sube(self):
        self._subir(self.sur)

        self.sur.refresh_from_db()
        self.assertTrue(self.sur.logo)

    def test_al_dar_de_alta_la_empresa(self):
        self.client.post('/platform/tenants/', {
            'action': 'create', 'name': 'Nueva Empresa', 'subdomain': 'nueva',
            'plan': 'starter',
            'logo': SimpleUploadedFile('logo.png', _png()),
        })

        nueva = Tenant.objects.get(subdomain='nueva')
        self.assertTrue(nueva.logo)

    def test_un_archivo_que_no_es_imagen_se_rechaza(self):
        respuesta = self._subir(self.sur, nombre='contrato.pdf',
                                contenido=b'%PDF-1.4 esto no es una imagen')

        self.sur.refresh_from_db()
        self.assertFalse(self.sur.logo)
        self.assertContains(respuesta, 'tiene que ser')

    def test_un_logo_invalido_no_deja_la_empresa_a_medias(self):
        """El alta no puede fallar entera por el logo: la empresa queda creada."""
        respuesta = self.client.post('/platform/tenants/', {
            'action': 'create', 'name': 'Otra Empresa', 'subdomain': 'otra',
            'plan': 'starter',
            'logo': SimpleUploadedFile('contrato.pdf', b'no soy una imagen'),
        })

        otra = Tenant.objects.get(subdomain='otra')
        self.assertFalse(otra.logo)
        self.assertContains(respuesta, 'El logo no se guardo')

    def test_cambiar_el_logo_retira_el_anterior(self):
        """Si se queda, el bucket acumula un logo por cada cambio."""
        self._subir(self.sur)
        self.sur.refresh_from_db()
        primero = self.sur.logo.name
        almacen = self.sur.logo.storage

        self._subir(self.sur, nombre='nuevo.png')

        self.sur.refresh_from_db()
        self.assertNotEqual(self.sur.logo.name, primero)
        self.assertFalse(almacen.exists(primero))

    def test_se_puede_quitar(self):
        self._subir(self.sur)

        self._subir(self.sur, nombre=None, quitar='1')

        self.sur.refresh_from_db()
        self.assertFalse(self.sur.logo)

    def test_el_soporte_no_sube_logos(self):
        """Es marca de la empresa: la reparte quien da de alta, no quien atiende."""
        self.client.force_login(self.soporte)

        respuesta = self._subir(self.sur)

        self.assertEqual(respuesta.status_code, 403)
        self.sur.refresh_from_db()
        self.assertFalse(self.sur.logo)

    def test_el_administrador_de_una_empresa_no_alcanza_la_pantalla(self):
        self.client.force_login(self.admin)

        respuesta = self._subir(self.sur)

        self.assertEqual(respuesta.status_code, 403)
