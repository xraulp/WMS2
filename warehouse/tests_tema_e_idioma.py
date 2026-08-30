"""
Como quiere ver la pantalla cada quien: el tema y el idioma.

Las dos preferencias se guardan en el perfil y no solo en el navegador, porque
quien las cambia espera encontrarlas puestas al entrar desde otra computadora.
"""
from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase

from .models import Tenant, UserProfile


class BaseDePreferencias(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')
        cls.usuario = User.objects.create_user('operador', password='x')
        cls.perfil = UserProfile.objects.create(
            user=cls.usuario, tenant=cls.tenant, role='staff')

    def setUp(self):
        self.client.force_login(self.usuario)


class TemaTests(BaseDePreferencias):

    def test_se_guarda_en_el_perfil(self):
        self.client.post('/preferencias/tema/', {'tema': 'dark'})

        self.perfil.refresh_from_db()
        self.assertEqual(self.perfil.theme, 'dark')

    def test_se_puede_volver_al_del_sistema(self):
        """Vacio no es 'claro': es 'el que tenga el sistema operativo'."""
        self.perfil.theme = 'dark'
        self.perfil.save()

        self.client.post('/preferencias/tema/', {'tema': ''})

        self.perfil.refresh_from_db()
        self.assertEqual(self.perfil.theme, '')

    def test_un_tema_inventado_se_rechaza(self):
        respuesta = self.client.post('/preferencias/tema/', {'tema': 'neon'})

        self.assertEqual(respuesta.status_code, 400)
        self.perfil.refresh_from_db()
        self.assertEqual(self.perfil.theme, '')

    def test_la_pantalla_llega_con_el_tema_puesto(self):
        """Server-side, para que no haya fogonazo blanco ni dependa del
        navegador en el que se entre."""
        self.perfil.theme = 'dark'
        self.perfil.save()

        self.assertContains(self.client.get('/dashboard/'), 'data-theme="dark"')

    def test_sin_tema_elegido_manda_el_sistema(self):
        """El <html> sale limpio, que es lo que deja mandar a
        `prefers-color-scheme`. Se mira la etiqueta y no la cadena suelta:
        `data-theme` aparece tambien dentro del CSS del tema."""
        self.assertContains(self.client.get('/dashboard/'), '<html lang="en">')

    def test_quien_no_ha_entrado_no_cambia_nada(self):
        self.client.logout()

        respuesta = self.client.post('/preferencias/tema/', {'tema': 'dark'})

        self.assertIn(respuesta.status_code, (302, 403))
        self.perfil.refresh_from_db()
        self.assertEqual(self.perfil.theme, '')


class IdiomaTests(BaseDePreferencias):

    def test_se_guarda_en_el_perfil_y_en_la_cookie(self):
        respuesta = self.client.post('/preferencias/idioma/', {'idioma': 'en'})

        self.perfil.refresh_from_db()
        self.assertEqual(self.perfil.language, 'en')
        self.assertEqual(respuesta.cookies[settings.LANGUAGE_COOKIE_NAME].value, 'en')

    def test_un_idioma_que_no_se_habla_se_rechaza(self):
        respuesta = self.client.post('/preferencias/idioma/', {'idioma': 'fr'})

        self.assertEqual(respuesta.status_code, 400)
        self.perfil.refresh_from_db()
        self.assertEqual(self.perfil.language, '')

    def test_volver_a_automatico_borra_la_cookie(self):
        self.client.post('/preferencias/idioma/', {'idioma': 'en'})

        respuesta = self.client.post('/preferencias/idioma/', {'idioma': ''})

        self.perfil.refresh_from_db()
        self.assertEqual(self.perfil.language, '')
        self.assertEqual(respuesta.cookies[settings.LANGUAGE_COOKIE_NAME].value, '')


class IdiomaEnElLoginTests(BaseDePreferencias):
    """
    El login es anterior a saber quien eres: ahi el idioma no puede salir del
    perfil, y sin selector quien no habla ingles se encuentra la primera
    pantalla del sistema en un idioma que no eligio.
    """

    def setUp(self):
        # A diferencia del resto de la clase base, aqui nadie ha entrado.
        self.client.logout()

    def test_la_pantalla_ofrece_los_dos_idiomas(self):
        respuesta = self.client.get('/')

        self.assertContains(respuesta, 'id="sel-idioma"')
        for codigo, _nombre in settings.LANGUAGES:
            self.assertContains(respuesta, 'value="%s"' % codigo)

    def test_quien_no_ha_entrado_puede_cambiarlo(self):
        respuesta = self.client.post('/preferencias/idioma/', {'idioma': 'es'})

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.cookies[settings.LANGUAGE_COOKIE_NAME].value, 'es')

    def test_un_idioma_que_no_se_habla_se_rechaza_tambien_sin_entrar(self):
        respuesta = self.client.post('/preferencias/idioma/', {'idioma': 'fr'})

        self.assertEqual(respuesta.status_code, 400)

    def test_la_pantalla_se_lee_en_el_idioma_elegido(self):
        self.client.post('/preferencias/idioma/', {'idioma': 'es'})

        respuesta = self.client.get('/')

        self.assertContains(respuesta, 'Usuario')
        self.assertContains(respuesta, 'Entrar')
        self.assertContains(respuesta, '<html lang="es">')

    def test_el_error_de_credenciales_tambien_se_traduce(self):
        self.client.post('/preferencias/idioma/', {'idioma': 'es'})

        respuesta = self.client.post('/', {'username': 'operador',
                                           'password': 'la-que-no-es'},
                                     headers={'hx-request': 'true'})

        self.assertContains(respuesta, 'Usuario o contraseña incorrectos.')

    def test_el_idioma_del_login_sigue_puesto_al_entrar(self):
        """El que se elige para leer el login manda tambien en la pantalla de
        despues: quien lo cambio ahi no queria leer solo esa pantalla."""
        self.client.post('/preferencias/idioma/', {'idioma': 'es'})

        self.client.force_login(self.usuario)
        respuesta = self.client.get('/dashboard/')

        self.assertContains(respuesta, '<html lang="es"')


class PantallasDeAccionesTests(TestCase):
    """
    Las pantallas que se abren desde Operations: el detalle de una operacion,
    su tabla y los avisos que devuelven las acciones.

    Estaban escritas a mano en ingles, asi que se quedaban en ingles con la
    pantalla de alrededor ya traducida. Se comprueba con el idioma puesto
    porque el defecto solo se ve asi: con el idioma de la casa, un texto sin
    traducir y uno traducido se leen igual.
    """

    @classmethod
    def setUpTestData(cls):
        from datetime import date
        from .models import Catalog, WarehouseOperation

        cls.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')
        cls.usuario = User.objects.create_user('capturista', password='x')
        cls.perfil = UserProfile.objects.create(
            user=cls.usuario, tenant=cls.tenant, role='manager')
        cls.cliente = Catalog.objects.create(
            tenant=cls.tenant, category='CUSTOMER', name='Cliente del Norte')
        cls.op = WarehouseOperation.objects.create(
            tenant=cls.tenant, operation_type='ENTRY', date=date.today(),
            custom_id='ED260819-0001', customer=cls.cliente,
            description='Mercancia de prueba', created_by=cls.usuario)

    def setUp(self):
        self.client.force_login(self.usuario)
        self.client.post('/preferencias/idioma/', {'idioma': 'es'})

    def test_el_detalle_se_lee_en_el_idioma_del_usuario(self):
        respuesta = self.client.get('/operations/%s/' % self.op.pk)

        # Un rotulo que arma la vista y uno que vive en la plantilla: los dos
        # estaban en ingles y cada uno se arregla por su lado.
        self.assertContains(respuesta, 'Capturada por')
        self.assertContains(respuesta, 'Descargar PDF')

    def test_el_estado_se_ensena_traducido_sin_cambiar_su_valor(self):
        """Lo que se filtra sigue siendo el texto en ingles: el formulario de
        busqueda manda 'In Warehouse' se lea como se lea la pantalla."""
        self.assertEqual(self.op.status, 'In Warehouse')

        respuesta = self.client.get('/operations/%s/' % self.op.pk)

        self.assertContains(respuesta, 'En almacén')

    def test_los_botones_de_la_tabla_se_traducen(self):
        respuesta = self.client.get('/operations/search/')

        self.assertContains(respuesta, '>Ver<')
        self.assertContains(respuesta, '>Borrar<')

    def test_el_pdf_del_informe_va_en_el_idioma_de_quien_lo_pide(self):
        """A diferencia del PDF de una operacion, que va en el idioma del
        cliente, este lo descarga el operador y puede abarcar varios clientes:
        no hay una ficha de la que sacar el idioma."""
        respuesta = self.client.get('/reports/pdf/', {'ids': str(self.op.pk)})

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta['Content-Type'], 'application/pdf')
        # El texto de un PDF va comprimido; lo que se comprueba es que se
        # genero con el idioma puesto y no revento al traducir.
        self.assertGreater(len(respuesta.content), 500)


class PlataformaYDigitalTests(TestCase):
    """
    Las dos pantallas que quedaban fuera del selector: la de plataforma --con
    sus cuatro pestanas-- y el panel del expediente digital.

    Estaban escritas a mano, y ademas mezclando los dos idiomas dentro de la
    misma pantalla: los recuadros de facturacion en espanol y sus filtros en
    ingles, los avisos del panel digital en espanol y sus botones en ingles.
    """

    @classmethod
    def setUpTestData(cls):
        from datetime import date
        from .models import Catalog, NotificationLog, PlatformUser, WarehouseOperation

        cls.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')
        cls.usuario = User.objects.create_user('platadmin', password='x')
        UserProfile.objects.create(user=cls.usuario, tenant=cls.tenant, role='admin')
        PlatformUser.objects.create(user=cls.usuario, role='admin')
        cls.cliente = Catalog.objects.create(
            tenant=cls.tenant, category='CUSTOMER', name='Cliente del Norte')
        cls.op = WarehouseOperation.objects.create(
            tenant=cls.tenant, operation_type='ENTRY', date=date.today(),
            custom_id='ED260819-0001', customer=cls.cliente,
            description='Mercancia de prueba', created_by=cls.usuario)
        NotificationLog.objects.create(
            tenant=cls.tenant, operation=cls.op, operation_custom_id=cls.op.custom_id,
            channel='EMAIL', event='OPERATION_CREATED', status='SENT',
            recipient='cliente@ejemplo.local')

    def setUp(self):
        self.client.force_login(self.usuario)
        self.client.post('/preferencias/idioma/', {'idioma': 'es'})

    def test_la_pantalla_de_plataforma_se_lee_en_espanol(self):
        respuesta = self.client.get('/platform/')

        self.assertContains(respuesta, 'Administración del SaaS')
        self.assertContains(respuesta, 'Cerrar sesión')

    def test_las_cuatro_pestanas_se_traducen(self):
        respuesta = self.client.get('/platform/tenants/')

        for pestana in ('Empresas', 'Bitácora de envíos', 'Facturación',
                        'Usuarios de plataforma'):
            self.assertContains(respuesta, pestana)

    def test_el_evento_y_el_estado_de_la_bitacora_salen_traducidos(self):
        """Las etiquetas viven en los choices del modelo, que estaban escritos
        en espanol a mano: en la pantalla en ingles salian en espanol."""
        respuesta = self.client.get('/platform/notifications/')

        self.assertContains(respuesta, 'Operación registrada')
        self.assertContains(respuesta, 'Enviada')

    def test_la_facturacion_no_mezcla_los_dos_idiomas(self):
        respuesta = self.client.get('/platform/invoices/')

        self.assertContains(respuesta, 'Por cobrar')
        # El filtro de al lado seguia en ingles con el resumen ya en espanol.
        self.assertContains(respuesta, 'Todas las empresas')
        self.assertNotContains(respuesta, '>Company</label>')

    def test_el_panel_digital_se_lee_en_espanol(self):
        respuesta = self.client.get('/digital/search/', {'q': self.op.custom_id})

        self.assertContains(respuesta, 'Elegir archivos')
        self.assertContains(respuesta, 'Tomar una foto')

    def test_los_avisos_del_panel_digital_llevan_su_marcador(self):
        """`{% trans %}` no traduce un literal con %(...)s -- Django lo toma por
        una cadena con formato y devuelve el original --, asi que el marcador
        que interpola el guion va entre llaves."""
        respuesta = self.client.get('/digital/search/', {'q': self.op.custom_id})

        self.assertContains(respuesta, '{archivo}')
        self.assertContains(respuesta, 'a la papelera')
        self.assertNotContains(respuesta, 'to the trash')


class GestionYClientesTests(TestCase):
    """
    Las dos pantallas que quedaban a medias: la gestion de usuarios de la
    empresa y la tabla del catalogo de clientes.

    La de usuarios mezclaba los dos idiomas dentro de la misma linea -- el
    titulo decia "Create New User - personal de la empresa" --, y era la unica
    pantalla donde eso se leia de un vistazo.
    """

    @classmethod
    def setUpTestData(cls):
        from .models import Catalog

        cls.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')
        cls.usuario = User.objects.create_user('jefa', password='x')
        UserProfile.objects.create(user=cls.usuario, tenant=cls.tenant, role='admin')
        cls.cliente = Catalog.objects.create(
            tenant=cls.tenant, category='CUSTOMER', name='Cliente del Norte')

    def setUp(self):
        self.client.force_login(self.usuario)
        self.client.post('/preferencias/idioma/', {'idioma': 'es'})

    def test_la_gestion_de_usuarios_no_mezcla_los_dos_idiomas(self):
        respuesta = self.client.get('/users/')

        self.assertContains(respuesta, 'personal de la empresa')
        self.assertNotContains(respuesta, 'Create New User')
        # Las columnas de la tabla, que seguian en ingles.
        self.assertContains(respuesta, 'Contraseña de acceso')
        self.assertContains(respuesta, 'Última entrada')

    def test_el_estado_de_la_contrasena_de_borrado_se_traduce(self):
        respuesta = self.client.get('/users/')

        self.assertContains(respuesta, 'sin configurar')
        self.assertNotContains(respuesta, 'cannot delete')

    def test_la_tabla_de_clientes_se_lee_en_espanol(self):
        respuesta = self.client.get('/catalog/list/', {'scope': 'customers'})

        self.assertContains(respuesta, 'sin acceso')
        self.assertNotContains(respuesta, '>no access<')


class CambiarElIdiomaSinPerderElSitioTests(TestCase):
    """
    Cambiar el idioma vuelve a pedir la pantalla, porque la traduccion la hace
    el servidor. Eso te devolvia a capturar aunque estuvieras revisando la
    tabla, y se llevaba por delante lo que hubiera en el formulario.

    Lo que se comprueba aqui es que la pantalla trae con que recuperar el sitio
    y con que avisar. El comportamiento en si es del navegador.
    """

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')
        cls.usuario = User.objects.create_user('capturista', password='x')
        UserProfile.objects.create(
            user=cls.usuario, tenant=cls.tenant, role='manager')

    def setUp(self):
        self.client.force_login(self.usuario)

    def test_la_pestana_se_guarda_y_se_vuelve_a_leer(self):
        """Se guardaba desde hace tiempo y no la leia nadie: la mitad del
        trabajo estaba hecha y no servia para nada."""
        respuesta = self.client.get('/dashboard/')

        self.assertContains(respuesta, "sessionStorage.setItem('activeTab'")
        self.assertContains(respuesta, "sessionStorage.getItem('activeTab')")

    def test_la_pestana_no_sobrevive_al_cierre_del_navegador(self):
        """`sessionStorage` y no `localStorage`: manana se arranca capturando,
        no en la pantalla que quedo abierta ayer."""
        respuesta = self.client.get('/dashboard/')

        self.assertNotContains(respuesta, "localStorage.setItem('activeTab'")

    def test_el_movil_tambien_recupera_su_pestana(self):
        respuesta = self.client.get('/mobile/')

        self.assertContains(respuesta, "sessionStorage.getItem('mobileActiveTab')")

    def test_se_avisa_antes_de_perder_lo_capturado(self):
        respuesta = self.client.get('/dashboard/')

        self.assertContains(respuesta, 'hayCapturaAMedias')
        self.assertContains(respuesta, 'what you have captured is lost')

    def test_el_aviso_no_salta_por_lo_que_rellena_la_propia_pantalla(self):
        """
        La fecha de hoy la pone un `load` de la pantalla, asi que la foto del
        formulario tiene que tomarse despues de todos ellos -- de ahi el
        `setTimeout` -- y solo cuenta como capturado lo que hizo la persona,
        que es lo que dice `isTrusted`.

        Comparando contra el HTML, el formulario nacia modificado y el aviso
        saltaba siempre; un confirm que aparece cuando no toca bloquea la
        pantalla entera hasta que alguien lo cierra a mano.
        """
        respuesta = self.client.get('/dashboard/')

        self.assertContains(respuesta, 'CAPTURA_TOCADA')
        self.assertContains(respuesta, 'e.isTrusted')
        self.assertContains(respuesta, 'setTimeout(function () {')

    def test_el_aviso_tambien_se_traduce(self):
        self.client.post('/preferencias/idioma/', {'idioma': 'es'})

        respuesta = self.client.get('/dashboard/')

        self.assertContains(respuesta, 'se pierde lo que llevas capturado')
