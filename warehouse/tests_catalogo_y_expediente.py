"""
Tres defectos chicos que llevaban tiempo anotados sin arreglar.

No tienen nada que ver entre si salvo que los tres se manifiestan como datos mal
escritos: una abreviatura que el alta tiraba, un nombre de documento repetido y
el nombre de una empresa ajena firmando el correo de otra.
"""
import tempfile

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.template.loader import render_to_string
from django.test import TestCase, override_settings

from .models import (Catalog, OperationDocument, Tenant, UserProfile,
                     WarehouseOperation)

STORAGE_LOCAL = override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage',
                'OPTIONS': {'location': tempfile.mkdtemp()}},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})


class BaseTenant(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')
        cls.manager = User.objects.create_user('manager', password='x')
        UserProfile.objects.create(user=cls.manager, tenant=cls.tenant,
                                   role='manager', delete_password='borrar123')

    def setUp(self):
        self.client.force_login(self.manager)


class AbreviaturaDelCatalogoTests(BaseTenant):
    """
    El alta de catalogo no guardaba `abbreviation`.

    Los dos formularios que llegan a esa vista -el del escritorio y el del
    movil- pintan el campo, asi que el operador lo escribia y se perdia sin
    ningun aviso. La edicion si lo guardaba, de modo que la unica forma de tener
    abreviatura era dar de alta y volver a entrar a editar.

    Importa porque la abreviatura del cliente es lo que
    `get_customer_abbreviation` usa para nombrar lo que sale impreso.
    """

    def _alta(self, **extra):
        datos = {'category': 'CUSTOMER', 'name': 'Ferretera del Bajio',
                 'abbreviation': 'fbj'}
        datos.update(extra)
        return self.client.post('/catalog/create/', datos)

    def test_el_alta_guarda_la_abreviatura(self):
        self._alta()

        entrada = Catalog.objects.get(name='Ferretera del Bajio')
        self.assertEqual(entrada.abbreviation, 'FBJ')

    def test_sin_abreviatura_queda_en_nulo_y_no_en_cadena_vacia(self):
        """
        Cadena vacia y None se comportan igual al consultarlos, pero el alta de
        cliente con usuario ya guardaba None; que dos vistas dejen valores
        distintos para lo mismo ensucia la base sin motivo.
        """
        self._alta(abbreviation='')

        self.assertIsNone(Catalog.objects.get().abbreviation)

    def test_la_edicion_sigue_guardando_la_abreviatura(self):
        entrada = Catalog.objects.create(
            tenant=self.tenant, category='CUSTOMER', name='Ferretera')

        self.client.post('/catalog/%d/edit/' % entrada.pk,
                         {'name': 'Ferretera', 'abbreviation': 'fer'})

        entrada.refresh_from_db()
        self.assertEqual(entrada.abbreviation, 'FER')


@STORAGE_LOCAL
class ConsecutivoDelExpedienteTests(BaseTenant):
    """
    El consecutivo de `digital_name` se calculaba contando los documentos del
    dia, y contar no es lo mismo que continuar: al borrar uno el contador
    retrocedia y la siguiente subida repetia un nombre ya usado.
    """

    def setUp(self):
        super().setUp()
        self.op = WarehouseOperation.objects.create(
            tenant=self.tenant, operation_type='ENTRY', custom_id='ED260817-0001',
            description='Mercancia de prueba')

    def _subir(self, nombre='foto.jpg'):
        return self.client.post(
            '/digital/%d/upload/' % self.op.pk,
            {'files': SimpleUploadedFile(nombre, b'contenido', 'image/jpeg')})

    def test_numera_en_orden(self):
        self._subir('uno.jpg')
        self._subir('dos.jpg')

        nombres = list(OperationDocument.objects.order_by('pk')
                       .values_list('digital_name', flat=True))
        self.assertEqual(len(set(nombres)), 2)
        self.assertTrue(nombres[1].endswith('-2'), nombres)

    def test_no_reutiliza_el_numero_de_un_documento_borrado(self):
        self._subir('uno.jpg')
        self._subir('dos.jpg')
        segundo = OperationDocument.objects.order_by('pk').last()
        nombre_liberado = segundo.digital_name
        segundo.delete()

        self._subir('tres.jpg')

        nuevo = OperationDocument.objects.order_by('pk').last()
        self.assertNotEqual(nuevo.digital_name, nombre_liberado)
        self.assertTrue(nuevo.digital_name.endswith('-3'), nuevo.digital_name)

    def test_varios_archivos_de_una_sola_subida_no_chocan(self):
        self.client.post('/digital/%d/upload/' % self.op.pk, {'files': [
            SimpleUploadedFile('a.jpg', b'a', 'image/jpeg'),
            SimpleUploadedFile('b.jpg', b'b', 'image/jpeg'),
            SimpleUploadedFile('c.jpg', b'c', 'image/jpeg'),
        ]})

        nombres = list(OperationDocument.objects.values_list('digital_name', flat=True))
        self.assertEqual(len(set(nombres)), 3)

    def test_continua_donde_se_quedaron_los_expedientes_anteriores(self):
        """
        El caso del propio deploy: la base ya trae documentos numerados y el
        contador todavia no existe para ese dia.

        Si arrancara en cero, la primera subida despues de subir el cambio se
        llamaria como un documento que ya esta en el expediente. Por eso la fila
        se siembra con el mayor que ya hubiera.
        """
        from datetime import date

        dia = date.today().strftime('%d%m%y')
        for n in (1, 2, 3):
            OperationDocument.objects.create(
                tenant=self.tenant, operation=self.op, file_type='PHOTO',
                file=SimpleUploadedFile('v%d.jpg' % n, b'x', 'image/jpeg'),
                original_name='v%d.jpg' % n, digital_name='%s-%d' % (dia, n))

        self._subir('nueva.jpg')

        nuevo = OperationDocument.objects.order_by('pk').last()
        self.assertEqual(nuevo.digital_name, '%s-4' % dia)

    def test_ignora_los_nombres_que_no_llevan_consecutivo(self):
        """
        Un documento cargado a mano con otro formato no puede tumbar el calculo
        ni hacerlo empezar de cero.
        """
        self._subir('uno.jpg')
        primero = OperationDocument.objects.get()
        prefijo = primero.digital_name.split('-')[0]
        OperationDocument.objects.create(
            tenant=self.tenant, operation=self.op, file_type='OTHER',
            file=SimpleUploadedFile('viejo.pdf', b'x', 'application/pdf'),
            original_name='viejo.pdf', digital_name=prefijo + '-antiguo')

        self._subir('dos.jpg')

        ultimo = OperationDocument.objects.order_by('pk').last()
        self.assertEqual(ultimo.digital_name, prefijo + '-2')


class PieDelCorreoTests(TestCase):
    """
    El pie de `report_email.html` decia "DYSER Group LLC" con el nombre escrito
    a mano, asi que el correo de cualquier otra empresa salia firmado con el
    nombre de una ajena - justo lo contrario de lo que persigue el resto del
    sistema, que es multiempresa de arriba abajo.
    """

    def _pie(self, tenant):
        op = WarehouseOperation.objects.create(
            tenant=tenant, operation_type='ENTRY', custom_id='ED260817-0007',
            description='Mercancia de prueba')
        return render_to_string('warehouse/email/report_email.html',
                                {'operation': op, 'message_body': ''})

    def test_firma_con_el_nombre_de_la_empresa_de_la_operacion(self):
        otra = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')

        html = self._pie(otra)

        self.assertIn('Almacenes del Norte', html)
        self.assertNotIn('DYSER', html)

    def test_la_leyenda_extra_sale_de_la_configuracion_de_la_empresa(self):
        tenant = Tenant.objects.create(
            name='DYSER Group LLC', type='organization', subdomain='dyser',
            config={'email_footer_note': 'Provider for RDL Systems LLC.'})

        html = self._pie(tenant)

        self.assertIn('DYSER Group LLC', html)
        self.assertIn('Provider for RDL Systems LLC.', html)

    def test_sin_leyenda_configurada_el_pie_no_la_inventa(self):
        tenant = Tenant.objects.create(
            name='Almacenes del Sur', type='organization', subdomain='sur')

        html = self._pie(tenant)

        self.assertIn('Almacenes del Sur', html)
        self.assertNotIn('Provider for', html)
