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
