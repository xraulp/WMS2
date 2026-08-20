"""
Los tres flecos que dejo abierta la papelera.

El archivo archivado seguia en su ruta de siempre, de modo que quien ya tuviera
el enlace podia abrirlo aunque hubiera desaparecido de la pantalla; la papelera
no se vaciaba nunca, asi que crecia sin fin; y desde el movil se podia archivar
pero no devolver, que es justo donde se sube la foto equivocada.
"""
import tempfile
from datetime import date, timedelta
from io import StringIO
from unittest import mock

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import (PREFIJO_PAPELERA, Catalog, OperationDocument, Tenant,
                     UserProfile, WarehouseOperation)

STORAGE_LOCAL = override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage',
                'OPTIONS': {'location': tempfile.mkdtemp()}},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})


def _lista_de_paneles(html, variable):
    """
    Los nombres de panel que conoce el JavaScript de la pantalla.

    Hay que mirar dentro de la declaracion y no en el HTML entero: el nombre del
    panel aparece tambien en el onclick de su boton, asi que buscarlo suelto da
    por buena una lista que no lo incluye — que es exactamente la averia que se
    quiere detectar.
    """
    import re

    m = re.search(variable + r'\s*=\s*\[([^\]]*)\]', html)
    return m.group(1) if m else ''



class BasePapelera(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')
        cls.admin = User.objects.create_user('admin_tenant', password='x')
        perfil = UserProfile.objects.create(
            user=cls.admin, tenant=cls.tenant, role='admin')
        perfil.set_delete_password('borrar123')
        perfil.save(update_fields=['delete_password'])

        cls.cliente = Catalog.objects.create(
            tenant=cls.tenant, category='CUSTOMER', name='ACME')

    def _operacion(self, custom_id='ED260819-0001'):
        return WarehouseOperation.objects.create(
            tenant=self.tenant, operation_type='ENTRY', date=date.today(),
            custom_id=custom_id, customer=self.cliente, description='Mercancia',
            created_by=self.admin)

    _consecutivo = 0

    def _documento(self, op=None, nombre='guia.pdf'):
        # El directorio del almacen de pruebas se comparte entre metodos, asi
        # que los nombres se hacen unicos: si no, el segundo archivo con el
        # mismo nombre recibe sufijo y las rutas dejan de ser comparables.
        type(self)._consecutivo += 1
        n = type(self)._consecutivo
        raiz, _, ext = nombre.rpartition('.')
        unico = f'{raiz}_{n}.{ext}'
        return OperationDocument.objects.create(
            tenant=self.tenant, operation=op or self._operacion(f'ED260819-{n:04d}'),
            original_name=unico,
            file=SimpleUploadedFile(unico, b'contenido del archivo'))


@STORAGE_LOCAL
class ElArchivoCambiaDeSitioTests(BasePapelera):

    def test_al_archivar_se_va_bajo_el_prefijo_de_la_papelera(self):
        doc = self._documento()
        ruta_original = doc.file.name

        doc.archivar(self.admin, 'Subida equivocada')

        doc.refresh_from_db()
        self.assertTrue(doc.file.name.startswith(PREFIJO_PAPELERA))
        self.assertIn(ruta_original, doc.file.name)

    def test_la_ruta_anterior_deja_de_servir(self):
        """
        Es el motivo de moverlo: el bucket sirve por URL, sin preguntar quien
        mira, asi que un enlace repartido antes seguia abriendo el archivo.
        """
        doc = self._documento()
        ruta_original = doc.file.name
        almacen = doc.file.storage

        doc.archivar(self.admin, 'Subida equivocada')

        self.assertFalse(almacen.exists(ruta_original))

    def test_el_contenido_sigue_estando(self):
        doc = self._documento()

        doc.archivar(self.admin, 'Subida equivocada')

        doc.refresh_from_db()
        with doc.file.open('rb') as f:
            self.assertEqual(f.read(), b'contenido del archivo')

    def test_restaurar_lo_devuelve_a_su_ruta(self):
        doc = self._documento()
        ruta_original = doc.file.name

        doc.archivar(self.admin, 'Subida equivocada')
        doc.refresh_from_db()
        doc.restaurar()

        doc.refresh_from_db()
        self.assertEqual(doc.file.name, ruta_original)
        with doc.file.open('rb') as f:
            self.assertEqual(f.read(), b'contenido del archivo')

    def test_restaurar_no_pisa_lo_que_ocupo_la_ruta_mientras_tanto(self):
        """
        Si alguien subio otro archivo con el mismo nombre mientras este estaba
        en la papelera, el que vuelve se coloca al lado y no encima: se pierde
        la ruta bonita, no un archivo.
        """
        doc = self._documento()
        ruta_original = doc.file.name
        doc.archivar(self.admin, 'Subida equivocada')
        doc.refresh_from_db()

        almacen = doc.file.storage
        almacen.save(ruta_original, SimpleUploadedFile('x', b'el nuevo inquilino'))

        doc.restaurar()

        doc.refresh_from_db()
        self.assertNotEqual(doc.file.name, ruta_original)
        self.assertFalse(doc.file.name.startswith(PREFIJO_PAPELERA))
        with doc.file.open('rb') as f:
            self.assertEqual(f.read(), b'contenido del archivo')

    def test_un_almacen_caido_no_impide_archivar(self):
        """
        Quitar de la vista un archivo mal subido no puede depender de que R2
        conteste: el registro y la papelera son lo que no puede fallar.
        """
        doc = self._documento()
        ruta_original = doc.file.name

        with mock.patch('django.core.files.storage.FileSystemStorage.save',
                        side_effect=OSError('R2 no responde')):
            doc.archivar(self.admin, 'Subida equivocada')

        doc.refresh_from_db()
        self.assertTrue(doc.en_papelera)
        self.assertEqual(doc.file.name, ruta_original)

    def test_destruir_borra_el_archivo_movido(self):
        doc = self._documento()
        doc.archivar(self.admin, 'Subida equivocada')
        doc.refresh_from_db()
        ruta_en_papelera = doc.file.name
        almacen = doc.file.storage

        self.client.force_login(self.admin)
        self.client.post(f'/deletions/{doc.pk}/purge/',
                         {'confirm_password': 'borrar123'})

        self.assertFalse(almacen.exists(ruta_en_papelera))
        self.assertFalse(OperationDocument.todos.filter(pk=doc.pk).exists())


@STORAGE_LOCAL
class PurgaPorAntiguedadTests(BasePapelera):

    def _archivar_hace(self, dias, nombre='guia.pdf'):
        doc = self._documento(nombre=nombre)
        doc.archivar(self.admin, 'Subida equivocada')
        OperationDocument.todos.filter(pk=doc.pk).update(
            deleted_at=timezone.now() - timedelta(days=dias))
        doc.refresh_from_db()
        return doc

    def _correr(self, *args):
        salida = StringIO()
        call_command('purgar_papelera', *args, stdout=salida, stderr=salida)
        return salida.getvalue()

    def test_sin_confirmar_no_destruye_nada(self):
        doc = self._archivar_hace(200)

        salida = self._correr()

        self.assertTrue(OperationDocument.todos.filter(pk=doc.pk).exists())
        self.assertIn('--confirmar', salida)

    def test_destruye_lo_que_pasa_del_plazo(self):
        viejo = self._archivar_hace(200, nombre='viejo.pdf')

        self._correr('--confirmar')

        self.assertFalse(OperationDocument.todos.filter(pk=viejo.pk).exists())

    def test_respeta_lo_reciente(self):
        reciente = self._archivar_hace(3, nombre='reciente.pdf')

        self._correr('--confirmar')

        self.assertTrue(OperationDocument.todos.filter(pk=reciente.pk).exists())

    def test_no_toca_lo_que_sigue_en_el_expediente(self):
        vivo = self._documento(nombre='vivo.pdf')

        self._correr('--dias', '1', '--confirmar')

        self.assertTrue(OperationDocument.todos.filter(pk=vivo.pk).exists())

    def test_el_plazo_se_puede_acortar(self):
        doc = self._archivar_hace(10)

        self._correr('--dias', '5', '--confirmar')

        self.assertFalse(OperationDocument.todos.filter(pk=doc.pk).exists())

    def test_el_archivo_se_borra_del_almacen(self):
        doc = self._archivar_hace(200)
        ruta = doc.file.name
        almacen = doc.file.storage

        self._correr('--confirmar')

        self.assertFalse(almacen.exists(ruta))

    def test_se_puede_acotar_a_una_empresa(self):
        otro = Tenant.objects.create(
            name='Almacenes del Sur', type='organization', subdomain='sur')
        ajeno = self._archivar_hace(200, nombre='ajeno.pdf')
        OperationDocument.todos.filter(pk=ajeno.pk).update(tenant=otro)
        propio = self._archivar_hace(200, nombre='propio.pdf')

        self._correr('--empresa', 'norte', '--confirmar')

        self.assertTrue(OperationDocument.todos.filter(pk=ajeno.pk).exists())
        self.assertFalse(OperationDocument.todos.filter(pk=propio.pk).exists())

    def test_una_empresa_que_no_existe_es_un_error(self):
        with self.assertRaises(CommandError):
            self._correr('--empresa', 'no-existe', '--confirmar')

    def test_un_plazo_de_cero_dias_es_un_error(self):
        """
        Purgar la papelera del mismo dia la deja sin efecto: seria destruir en
        el acto lo que se acaba de archivar.
        """
        with self.assertRaises(CommandError):
            self._correr('--dias', '0', '--confirmar')


class LaPapeleraEnElMovilTests(BasePapelera):

    def test_el_administrador_la_tiene_en_la_barra(self):
        self.client.force_login(self.admin)

        cuerpo = self.client.get('/mobile/').content.decode()

        self.assertIn('nav-deletions', cuerpo)
        self.assertIn('deletions', _lista_de_paneles(cuerpo, 'MOB_PANELS'))

    def test_el_staff_no_la_ve(self):
        """
        El staff deja rastro, no lo audita: es la misma frontera que en el
        tablero de escritorio.
        """
        operador = User.objects.create_user('staff_norte', password='x')
        UserProfile.objects.create(user=operador, tenant=self.tenant, role='staff')
        self.client.force_login(operador)

        cuerpo = self.client.get('/mobile/').content.decode()

        self.assertNotIn('nav-deletions', cuerpo)

    def test_no_queda_ningun_comentario_de_plantilla_sin_cerrar(self):
        self.client.force_login(self.admin)

        cuerpo = self.client.get('/mobile/').content.decode()

        self.assertNotIn('{#', cuerpo)
        self.assertNotIn('#}', cuerpo)
