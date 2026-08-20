"""
La pestana Digital dice de entrada que expedientes tienen archivos.

Solo sabia abrir uno si le tecleaban el Custom ID completo y exacto: quien no se
supiera el numero de memoria se quedaba mirando un recuadro vacio, sin manera de
averiguar donde hay algo guardado. Ahora arranca con el indice de expedientes
con archivos, y la busqueda acepta partes del identificador.

El indice pasa por los mismos filtros que todo lo demas -tenant y cliente-,
porque una lista es tan capaz de filtrar de mas como cualquier otra pantalla.
"""
import tempfile
from datetime import date, timedelta

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from .models import (Catalog, OperationDocument, Tenant, UserProfile,
                     WarehouseOperation)

STORAGE_LOCAL = override_settings(STORAGES={
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage',
                'OPTIONS': {'location': tempfile.mkdtemp()}},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})


@STORAGE_LOCAL
class IndiceDeExpedientesTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')
        cls.otro_tenant = Tenant.objects.create(
            name='Almacenes del Sur', type='organization', subdomain='sur')

        cls.manager = User.objects.create_user('manager', password='x')
        UserProfile.objects.create(user=cls.manager, tenant=cls.tenant, role='manager')

        cls.acme = Catalog.objects.create(
            tenant=cls.tenant, category='CUSTOMER', name='ACME')
        cls.otro_cliente = Catalog.objects.create(
            tenant=cls.tenant, category='CUSTOMER', name='Beta')

    def _operacion(self, custom_id, cliente=None, tenant=None, dias=0):
        return WarehouseOperation.objects.create(
            tenant=tenant or self.tenant, operation_type='ENTRY',
            date=date.today() - timedelta(days=dias), custom_id=custom_id,
            customer=cliente or self.acme, description='Mercancia',
            created_by=self.manager)

    def _con_archivo(self, op, nombre='guia.pdf'):
        OperationDocument.objects.create(
            operation=op, original_name=nombre,
            file=SimpleUploadedFile(nombre, b'contenido'))

    def _panel(self, q=None):
        self.client.force_login(self.manager)
        url = '/digital/search/' + (f'?q={q}' if q is not None else '')
        return self.client.get(url).content.decode()

    def test_de_entrada_lista_los_expedientes_que_tienen_archivos(self):
        con = self._operacion('ED260819-0001')
        self._con_archivo(con)
        self._operacion('ED260819-0002')  # sin archivos

        cuerpo = self._panel()

        self.assertIn('ED260819-0001', cuerpo)
        self.assertNotIn('ED260819-0002', cuerpo)

    def test_el_expediente_vaciado_desaparece_del_indice(self):
        """
        El conteo mira solo los documentos vivos: un expediente cuyo unico
        archivo esta en la papelera no tiene nada que ofrecer.
        """
        op = self._operacion('ED260819-0001')
        self._con_archivo(op)
        doc = OperationDocument.objects.get(operation=op)
        doc.archivar(self.manager, 'Subida equivocada')

        cuerpo = self._panel()

        self.assertNotIn('ED260819-0001', cuerpo)

    def test_no_asoma_lo_de_otra_empresa(self):
        ajena = self._operacion('SD260819-0001', tenant=self.otro_tenant)
        self._con_archivo(ajena)

        cuerpo = self._panel()

        self.assertNotIn('SD260819-0001', cuerpo)

    def test_el_cliente_solo_ve_los_suyos(self):
        mio = self._operacion('ED260819-0001', cliente=self.acme)
        ajeno = self._operacion('ED260819-0002', cliente=self.otro_cliente)
        self._con_archivo(mio)
        self._con_archivo(ajeno)

        usuario = User.objects.create_user('cliente_acme', password='x')
        UserProfile.objects.create(user=usuario, tenant=self.tenant,
                                   role='customer', customer=self.acme)
        self.client.force_login(usuario)

        cuerpo = self.client.get('/digital/search/').content.decode()

        self.assertIn('ED260819-0001', cuerpo)
        self.assertNotIn('ED260819-0002', cuerpo)


@STORAGE_LOCAL
class BusquedaPorPartesTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')
        cls.manager = User.objects.create_user('manager', password='x')
        UserProfile.objects.create(user=cls.manager, tenant=cls.tenant, role='manager')
        cls.acme = Catalog.objects.create(
            tenant=cls.tenant, category='CUSTOMER', name='ACME')

    def _operacion(self, custom_id):
        return WarehouseOperation.objects.create(
            tenant=self.tenant, operation_type='ENTRY', date=date.today(),
            custom_id=custom_id, customer=self.acme, description='Mercancia',
            created_by=self.manager)

    def _buscar(self, q):
        self.client.force_login(self.manager)
        return self.client.get(f'/digital/search/?q={q}').content.decode()

    def test_el_id_exacto_abre_el_expediente(self):
        self._operacion('ED260819-0001')

        cuerpo = self._buscar('ED260819-0001')

        self.assertIn('Download All', cuerpo)

    def test_una_sola_coincidencia_parcial_abre_el_expediente(self):
        self._operacion('ED260819-0001')

        cuerpo = self._buscar('0001')

        self.assertIn('Download All', cuerpo)

    def test_varias_coincidencias_se_ofrecen_todas(self):
        self._operacion('ED260819-0001')
        self._operacion('ED260819-0002')

        cuerpo = self._buscar('ED260819')

        self.assertIn('ED260819-0001', cuerpo)
        self.assertIn('ED260819-0002', cuerpo)
        self.assertNotIn('Download All', cuerpo)

    def test_lo_que_no_existe_sigue_diciendo_que_no_existe(self):
        self._operacion('ED260819-0001')

        cuerpo = self._buscar('XX999')

        self.assertIn('No operation found', cuerpo)

    def test_la_busqueda_parcial_no_cruza_empresas(self):
        otro = Tenant.objects.create(
            name='Almacenes del Sur', type='organization', subdomain='sur')
        WarehouseOperation.objects.create(
            tenant=otro, operation_type='ENTRY', date=date.today(),
            custom_id='ED260819-0009', description='Ajena',
            created_by=self.manager)

        cuerpo = self._buscar('ED260819')

        self.assertNotIn('ED260819-0009', cuerpo)


class LaPestanaDeletionsSeAbreTests(TestCase):
    """
    El panel existia y la vista respondia, pero `showTab` no conocia la pestana:
    su lista de paneles no incluia 'deletions', asi que al pulsarla no se
    encendia ninguno -la pantalla se quedaba en blanco- y su carga diferida,
    que colgaba de `intersect`, no llegaba a dispararse nunca porque el panel
    seguia oculto.

    Es una regresion de plantilla, invisible para el resto de la suite: aqui se
    comprueba en el HTML servido.
    """

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')
        cls.admin = User.objects.create_user('admin_tenant', password='x')
        UserProfile.objects.create(user=cls.admin, tenant=cls.tenant, role='admin')

    def test_el_tablero_sabe_encender_el_panel_de_borrados(self):
        self.client.force_login(self.admin)

        cuerpo = self.client.get('/dashboard/').content.decode()

        from .tests_papelera_remate import _lista_de_paneles
        self.assertIn('deletions', _lista_de_paneles(cuerpo, 'tabs'))
        self.assertIn('#deletions-content', cuerpo)   # y algo lo carga

    def test_la_vista_de_la_papelera_responde(self):
        self.client.force_login(self.admin)

        self.assertEqual(self.client.get('/deletions/').status_code, 200)

    def test_no_queda_ningun_comentario_de_plantilla_sin_cerrar(self):
        """
        `{# ... #}` solo comenta una linea: escrito en varias, Django lo imprime
        tal cual en la pantalla. Ya paso una vez y llego a produccion.
        """
        self.client.force_login(self.admin)

        cuerpo = self.client.get('/dashboard/').content.decode()

        self.assertNotIn('{#', cuerpo)
        self.assertNotIn('#}', cuerpo)


class ElPanelDigitalNoDependeDeUnSoloDisparadorTests(TestCase):
    """
    El panel se carga por htmx y su contenido inicial es un "Loading...". Si la
    unica llamada que lo llena depende de pulsar la pestana, cualquier camino
    que no pase por ahi deja el panel colgado para siempre — y antes ese mismo
    caso mostraba un texto estatico, asi que no se notaba.

    Aqui se comprueba en el HTML servido que hay mas de una entrada a la misma
    funcion de carga: la pestana y la inicializacion de la pagina.
    """

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')
        cls.manager = User.objects.create_user('manager_norte', password='x')
        UserProfile.objects.create(user=cls.manager, tenant=cls.tenant, role='manager')

    def _cuerpo(self, url):
        self.client.force_login(self.manager)
        return self.client.get(url).content.decode()

    def test_el_tablero_carga_el_panel_al_arrancar_y_al_abrir_la_pestana(self):
        cuerpo = self._cuerpo('/dashboard/')

        # Al menos: la definicion, la llamada de showTab y la de arranque.
        self.assertGreaterEqual(cuerpo.count('cargarPanelDigital()'), 3)

    def test_el_movil_tambien(self):
        cuerpo = self._cuerpo('/mobile/')

        self.assertGreaterEqual(cuerpo.count('cargarPanelDigital()'), 3)
