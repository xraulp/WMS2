"""
La barra de arriba, el menu de la cuenta y la barra de abajo del telefono.

Tres cosas que Diego vio en el telefono y que ninguna prueba veia:

* Al cliente se le borraba la pantalla entera al cargar Inicio. El panel vivia
  dentro del bloque de la captura, el cliente no captura, y htmx -- sin
  contenedor donde pintar -- pegaba las cifras en el <body>.
* La barra de abajo "a veces estaba y a veces no": nada mandaba al telefono al
  movil, y pedimentos, cruces e impuestos volvian al tablero de escritorio.
* En la barra de arriba no cabia "Cerrar sesion".

Lo que se ve -- que el menu quepa, que la barra no se salga -- se comprobo en
Chromium con un iPhone simulado. Aqui queda lo que el servidor decide.
"""
from django.contrib.auth.models import User
from django.test import TestCase

from .models import Catalog, Tenant, UserProfile

UA_IPHONE = ('Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 '
             '(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1')
UA_ANDROID = ('Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/126.0 Mobile Safari/537.36')
UA_PC = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
         '(KHTML, like Gecko) Chrome/126.0 Safari/537.36')


class SiglaDelTenantTests(TestCase):

    def test_manda_el_nombre_corto(self):
        t = Tenant(name='DYSER Group LLC', short_name='dyser')
        self.assertEqual(t.sigla, 'DYSER')

    def test_sin_nombre_corto_las_iniciales_sin_la_forma_juridica(self):
        self.assertEqual(Tenant(name='Logistics Laredo LLC').sigla, 'LL')
        self.assertEqual(Tenant(name='Centro de Distribucion Industrial SA de CV').sigla, 'CDI')
        self.assertEqual(Tenant(name='Fisair American Manufacturing S.A.P.I. de C.V.').sigla, 'FAM')


class BaseDeLaBarra(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name='Warehouse Uno', type='organization',
                                           subdomain='uno', short_name='WUNO')
        cls.cliente = Catalog.objects.create(tenant=cls.tenant, category='CUSTOMER',
                                             name='Cliente de Uno')
        cls.jefe = User.objects.create_user('jefe', password='x')
        UserProfile.objects.create(user=cls.jefe, tenant=cls.tenant, role='superadmin')
        cls.de_cliente = User.objects.create_user('de_cliente', password='x')
        UserProfile.objects.create(user=cls.de_cliente, tenant=cls.tenant, role='customer',
                                   customer=cls.cliente)


class ElTelefonoEntraAlMovilTests(BaseDeLaBarra):

    def test_el_login_desde_un_iphone_lleva_al_movil(self):
        r = self.client.post('/', {'username': 'jefe', 'password': 'x'},
                             HTTP_USER_AGENT=UA_IPHONE)
        self.assertRedirects(r, '/mobile/', fetch_redirect_response=False)

    def test_el_login_por_htmx_desde_android_tambien(self):
        r = self.client.post('/', {'username': 'jefe', 'password': 'x'},
                             HTTP_USER_AGENT=UA_ANDROID, HTTP_HX_REQUEST='true')
        self.assertEqual(r.headers['HX-Redirect'], '/mobile/')

    def test_el_login_desde_una_pc_sigue_llevando_al_tablero(self):
        r = self.client.post('/', {'username': 'jefe', 'password': 'x'},
                             HTTP_USER_AGENT=UA_PC)
        self.assertRedirects(r, '/dashboard/', fetch_redirect_response=False)

    def test_el_tablero_abierto_desde_el_telefono_manda_al_movil_con_su_direccion(self):
        # La direccion se conserva: los QR viejos llevan ?tab=digital&q=...
        self.client.force_login(self.jefe)
        r = self.client.get('/dashboard/?tab=digital&q=ED1', HTTP_USER_AGENT=UA_IPHONE)
        self.assertRedirects(r, '/mobile/?tab=digital&q=ED1', fetch_redirect_response=False)

    def test_quien_pide_el_escritorio_lo_tiene_toda_la_sesion(self):
        self.client.force_login(self.jefe)
        r = self.client.get('/dashboard/?vista=escritorio', HTTP_USER_AGENT=UA_IPHONE)
        self.assertEqual(r.status_code, 200)
        r = self.client.get('/dashboard/', HTTP_USER_AGENT=UA_IPHONE)
        self.assertEqual(r.status_code, 200)

    def test_y_vuelve_al_movil_cuando_lo_pide(self):
        self.client.force_login(self.jefe)
        self.client.get('/dashboard/?vista=escritorio', HTTP_USER_AGENT=UA_IPHONE)
        self.client.get('/mobile/?vista=movil', HTTP_USER_AGENT=UA_IPHONE)
        r = self.client.get('/dashboard/', HTTP_USER_AGENT=UA_IPHONE)
        self.assertRedirects(r, '/mobile/', fetch_redirect_response=False)


class ElInicioDelClienteTests(BaseDeLaBarra):
    """El contenedor de Inicio tiene que llegarle a quien no captura."""

    def test_en_el_tablero(self):
        self.client.force_login(self.de_cliente)
        html = self.client.get('/dashboard/').content.decode()
        self.assertIn('id="inicio-content"', html)
        self.assertNotIn('id="panel-form"', html)

    def test_en_el_movil(self):
        self.client.force_login(self.de_cliente)
        html = self.client.get('/mobile/').content.decode()
        self.assertIn('id="mob-inicio-content"', html)
        self.assertNotIn('id="panel-new"', html)


class LaCuentaTests(BaseDeLaBarra):

    def test_el_movil_lleva_la_sigla_y_el_menu_con_la_salida(self):
        self.client.force_login(self.jefe)
        html = self.client.get('/mobile/').content.decode()
        self.assertIn('WMS · WUNO', html)
        self.assertIn('id="cuenta-btn"', html)
        self.assertIn('data-siempre="1"', html)
        self.assertIn('href="/logout/"', html)
        self.assertIn('/dashboard/?vista=escritorio', html)

    def test_el_tablero_lleva_las_dos_marcas_y_la_puerta_al_movil(self):
        self.client.force_login(self.jefe)
        html = self.client.get('/dashboard/').content.decode()
        self.assertIn('WMS - WAREHOUSE UNO', html)
        self.assertIn('WMS · WUNO', html)
        self.assertIn('/mobile/?vista=movil', html)


class LasPantallasAparteTests(BaseDeLaBarra):
    """Pedimentos, cruces e impuestos: la barra de abajo solo en el telefono."""

    PANTALLAS = ('/pedimentos/', '/cruces/', '/impuestos/')

    def test_en_el_telefono_llevan_la_barra_y_vuelven_al_movil(self):
        self.client.force_login(self.jefe)
        for url in self.PANTALLAS:
            with self.subTest(url=url):
                html = self.client.get(url, HTTP_USER_AGENT=UA_IPHONE).content.decode()
                self.assertIn('class="bottom-nav"', html)
                self.assertIn('/mobile/?tab=database', html)
                self.assertIn('<a href="/mobile/">', html)

    def test_en_la_pc_no_llevan_barra_y_vuelven_al_tablero(self):
        self.client.force_login(self.jefe)
        for url in self.PANTALLAS:
            with self.subTest(url=url):
                html = self.client.get(url, HTTP_USER_AGENT=UA_PC).content.decode()
                self.assertNotIn('class="bottom-nav"', html)
                self.assertIn('<a href="/dashboard/">', html)
