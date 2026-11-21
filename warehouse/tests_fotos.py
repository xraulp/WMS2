"""
Las fotos se achican al subirse y lo demas llega intacto.
"""
import io
import tempfile
from datetime import date, timedelta

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image

from .fotos import LADO_MAXIMO
from .models import (Catalog, NotificationLog, OperationDocument, Tenant,
                     UserProfile, WarehouseOperation)

STORAGE_LOCAL = override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage',
                'OPTIONS': {'location': tempfile.mkdtemp()}},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})


def foto(ancho, alto, formato='JPEG', modo='RGB', exif_orientacion=None):
    """Una foto con ruido, para que no se comprima a casi nada como un color liso."""
    import os
    imagen = Image.frombytes(modo, (ancho, alto),
                             os.urandom(ancho * alto * len(modo)))
    salida = io.BytesIO()
    opciones = {}
    if exif_orientacion:
        exif = Image.Exif()
        exif[0x0112] = exif_orientacion
        exif[0x0132] = '2026:11:21 10:00:00'
        opciones['exif'] = exif.tobytes()
    imagen.save(salida, formato, quality=95, **opciones)
    return salida.getvalue()


@STORAGE_LOCAL
class CompresionDeFotosTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')
        cls.admin = User.objects.create_user('admin_fotos', password='x')
        UserProfile.objects.create(user=cls.admin, tenant=cls.tenant, role='admin')
        cls.cliente = Catalog.objects.create(
            tenant=cls.tenant, category='CUSTOMER', name='ACME')
        cls.op = WarehouseOperation.objects.create(
            tenant=cls.tenant, operation_type='ENTRY', date=date.today(),
            custom_id='ED261121-0001', customer=cls.cliente,
            description='Mercancia', created_by=cls.admin)

    def subir(self, nombre, contenido):
        return OperationDocument.objects.create(
            tenant=self.tenant, operation=self.op, original_name=nombre,
            file=SimpleUploadedFile(nombre, contenido))

    def abrir(self, doc):
        doc.file.open('rb')
        try:
            imagen = Image.open(io.BytesIO(doc.file.read()))
            imagen.load()
            return imagen
        finally:
            doc.file.close()

    def test_la_foto_grande_se_achica_y_pesa_menos(self):
        original = foto(3000, 2000)
        doc = self.subir('IMG_0001.jpg', original)
        imagen = self.abrir(doc)
        self.assertEqual(max(imagen.size), LADO_MAXIMO)
        self.assertEqual(imagen.size, (2000, 1333))
        self.assertLess(doc.file.size, len(original))
        self.assertEqual(doc.original_name, 'IMG_0001.jpg')

    def test_la_rotacion_se_aplica_y_la_fecha_se_conserva(self):
        # Orientacion 6: el celular la guardo acostada y pide girarla.
        doc = self.subir('acostada.jpg', foto(3000, 2000, exif_orientacion=6))
        imagen = self.abrir(doc)
        self.assertEqual(imagen.size, (1333, 2000))
        exif = imagen.getexif()
        self.assertEqual(exif.get(0x0132), '2026:11:21 10:00:00')
        self.assertNotEqual(exif.get(0x0112), 6)

    def test_la_foto_chica_no_se_toca(self):
        original = foto(400, 300)
        doc = self.subir('chica.jpg', original)
        doc.file.open('rb')
        self.assertEqual(doc.file.read(), original)
        doc.file.close()

    def test_el_png_sin_transparencia_pasa_a_jpg_y_el_nombre_lo_dice(self):
        doc = self.subir('captura.png', foto(2500, 2500, formato='PNG'))
        self.assertEqual(self.abrir(doc).format, 'JPEG')
        self.assertTrue(doc.file.name.endswith('.jpg'))
        self.assertEqual(doc.original_name, 'captura.jpg')

    def test_el_png_con_transparencia_sigue_siendo_png(self):
        doc = self.subir('logo.png', foto(2500, 2500, formato='PNG', modo='RGBA'))
        imagen = self.abrir(doc)
        self.assertEqual(imagen.format, 'PNG')
        self.assertEqual(imagen.mode, 'RGBA')
        self.assertEqual(doc.original_name, 'logo.png')

    def test_el_pdf_llega_intacto(self):
        contenido = b'%PDF-1.4 contenido del pdf'
        doc = self.subir('factura.pdf', contenido)
        doc.file.open('rb')
        self.assertEqual(doc.file.read(), contenido)
        doc.file.close()
        self.assertTrue(doc.file.name.endswith('.pdf'))

    def test_archivar_no_vuelve_a_comprimir(self):
        doc = self.subir('IMG_0002.jpg', foto(3000, 2000))
        peso = doc.file.size
        doc.archivar(self.admin, 'prueba')
        doc.restaurar()
        doc.refresh_from_db()
        self.assertEqual(doc.file.size, peso)


class MantenimientoTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')

    def aviso(self, dias, evento='OPERATION_CREATED'):
        log = NotificationLog.objects.create(
            tenant=self.tenant, channel='EMAIL', event=evento, status='SENT')
        NotificationLog.objects.filter(pk=log.pk).update(
            created_at=timezone.now() - timedelta(days=dias))
        return log

    def test_sin_confirmar_no_borra_nada(self):
        self.aviso(400)
        call_command('mantenimiento', stdout=io.StringIO())
        self.assertEqual(NotificationLog.objects.count(), 1)

    def test_borra_la_bitacora_vieja_pero_no_las_facturas(self):
        viejo = self.aviso(400)
        factura = self.aviso(400, evento='INVOICE_SENT')
        reciente = self.aviso(30)
        call_command('mantenimiento', '--confirmar', stdout=io.StringIO())
        restantes = set(NotificationLog.objects.values_list('pk', flat=True))
        self.assertEqual(restantes, {factura.pk, reciente.pk})
        self.assertNotIn(viejo.pk, restantes)
