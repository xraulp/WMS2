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
