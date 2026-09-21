"""
Cada quien puede cambiar su contrasena, y quien la pierde la recupera.

Antes no existia ninguna de las dos cosas: la contrasena solo se cambiaba desde
la pantalla de usuarios de la empresa, que administra a los demas, y el
administrador de plataforma no tenia ni eso -- por encima de el no hay nadie
que se la reasigne, asi que un olvido se resolvia entrando al servidor.

Lo que se prueba aqui es lo que decide el servidor. Lo que se ve -- que el
enlace quepa en el menu del telefono, que los campos se escriban a dedo -- se
miro en un navegador.
"""
from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase

from .models import Catalog, PlatformUser, Tenant, UserProfile


VIEJA = 'la-de-siempre-9'
NUEVA = 'trepidante-quilate-42'


class BaseDeLaCuenta(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')
        cls.operador = User.objects.create_user('operador', password=VIEJA)
        UserProfile.objects.create(user=cls.operador, tenant=cls.tenant, role='staff')

    def setUp(self):
        self.client.force_login(self.operador)

    def _cambiar(self, actual=VIEJA, nueva=NUEVA, repetida=None):
        return self.client.post('/cuenta/', {
            'action': 'password',
            'current_password': actual,
            'new_password': nueva,
            'new_password2': nueva if repetida is None else repetida,
        })


class CambiarLaPropiaTests(BaseDeLaCuenta):

    def test_con_la_actual_correcta_la_contrasena_cambia(self):
        self._cambiar()

        self.operador.refresh_from_db()
        self.assertTrue(self.operador.check_password(NUEVA))

    def test_la_sesion_sigue_abierta_despues_de_cambiarla(self):
        """
        Django invalida la sesion al cambiar la contrasena. Sin
        `update_session_auth_hash`, quien acaba de cambiarla con exito seria
        expulsado al login, que parece un fallo justo cuando no lo hubo.
        """
        self._cambiar()

        respuesta = self.client.get('/cuenta/')
        self.assertEqual(respuesta.status_code, 200)

    def test_sin_la_actual_no_cambia_nada(self):
        """
        La regla que distingue esta pantalla de la de usuarios: un
        administrador reasigna sin saber la anterior, el dueno de la cuenta la
        sabe. Exigirla es lo que impide que una sesion abierta y sin vigilancia
        se convierta en una cuenta robada.
        """
        respuesta = self._cambiar(actual='me-la-invento')

        self.operador.refresh_from_db()
        self.assertTrue(self.operador.check_password(VIEJA))
        self.assertIn('not correct', respuesta.content.decode())

    def test_si_las_dos_copias_no_coinciden_no_cambia(self):
        respuesta = self._cambiar(repetida='otra-cosa-distinta-7')

        self.operador.refresh_from_db()
        self.assertTrue(self.operador.check_password(VIEJA))
        self.assertIn('do not match', respuesta.content.decode())

    def test_una_contrasena_debil_se_rechaza(self):
        """
        `AUTH_PASSWORD_VALIDATORS` estaba configurado y no lo llamaba nadie.
        Aqui si: la contrasena la elige quien la va a escribir todos los dias.
        """
        self._cambiar(nueva='12345')

        self.operador.refresh_from_db()
        self.assertTrue(self.operador.check_password(VIEJA))

    def test_repetir_la_misma_no_cuenta_como_cambio(self):
        respuesta = self._cambiar(nueva=VIEJA)

        self.assertIn('same as the current one', respuesta.content.decode())


class ElDePlataformaTambienTests(TestCase):
    """
    El administrador del SaaS no pertenece a ninguna empresa, asi que cualquier
    pantalla que exija tenant le responde 404. Esta no puede ser una de ellas:
    es el unico usuario al que nadie mas puede reasignarle la contrasena.
    """

    @classmethod
    def setUpTestData(cls):
        cls.jefe = User.objects.create_user('plataforma', password=VIEJA)
        PlatformUser.objects.create(user=cls.jefe, role='admin')

    def test_abre_su_cuenta_sin_pertenecer_a_ninguna_empresa(self):
        self.client.force_login(self.jefe)

        self.assertEqual(self.client.get('/cuenta/').status_code, 200)

    def test_cambia_su_contrasena(self):
        self.client.force_login(self.jefe)

        self.client.post('/cuenta/', {
            'action': 'password', 'current_password': VIEJA,
            'new_password': NUEVA, 'new_password2': NUEVA})

        self.jefe.refresh_from_db()
        self.assertTrue(self.jefe.check_password(NUEVA))


class ElCorreoDeRecuperacionTests(BaseDeLaCuenta):

    def test_se_guarda_en_la_cuenta(self):
        self.client.post('/cuenta/', {'action': 'email',
                                      'email': 'operador@almacen.com'})

        self.operador.refresh_from_db()
        self.assertEqual(self.operador.email, 'operador@almacen.com')

    def test_se_rechaza_el_que_ya_usa_otra_cuenta(self):
        """
        Django permite repetirlo; el olvido no. Pedir la recuperacion con un
        correo compartido manda un mensaje por cada cuenta, y quien lo recibe no
        sabe cual de los dos enlaces es el suyo.
        """
        otro = User.objects.create_user('otro', password=VIEJA,
                                        email='compartido@almacen.com')
        UserProfile.objects.create(user=otro, tenant=self.tenant, role='staff')

        self.client.post('/cuenta/', {'action': 'email',
                                      'email': 'compartido@almacen.com'})

        self.operador.refresh_from_db()
        self.assertEqual(self.operador.email, '')

    def test_se_rechaza_lo_que_no_es_una_direccion(self):
        self.client.post('/cuenta/', {'action': 'email', 'email': 'arroba-nada'})

        self.operador.refresh_from_db()
        self.assertEqual(self.operador.email, '')

    def test_se_puede_vaciar(self):
        self.operador.email = 'operador@almacen.com'
        self.operador.save(update_fields=['email'])

        self.client.post('/cuenta/', {'action': 'email', 'email': ''})

        self.operador.refresh_from_db()
        self.assertEqual(self.operador.email, '')


class LaRecuperacionPorCorreoTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')
        cls.olvidadizo = User.objects.create_user(
            'olvidadizo', password=VIEJA, email='olvidadizo@almacen.com')
        UserProfile.objects.create(user=cls.olvidadizo, tenant=cls.tenant, role='staff')

    def test_el_login_ofrece_la_salida(self):
        respuesta = self.client.get('/')

        self.assertIn('/password/reset/', respuesta.content.decode())

    def test_pedirlo_manda_un_correo_con_el_enlace(self):
        self.client.post('/password/reset/', {'email': 'olvidadizo@almacen.com'})

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('/password/reset/', mail.outbox[0].body)

    def test_una_direccion_desconocida_no_manda_nada_pero_contesta_igual(self):
        """
        No se dice nunca si un correo existe: contestar "ese correo no esta
        registrado" le confirma a cualquiera quien tiene cuenta aqui.
        """
        respuesta = self.client.post('/password/reset/',
                                     {'email': 'nadie@ninguna-parte.com'},
                                     follow=True)

        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(respuesta.status_code, 200)

    def test_el_enlace_deja_poner_una_contrasena_nueva(self):
        self.client.post('/password/reset/', {'email': 'olvidadizo@almacen.com'})
        enlace = [renglon for renglon in mail.outbox[0].body.splitlines()
                  if '/password/reset/' in renglon][0].strip()
        camino = enlace[enlace.index('/password/reset/'):]

        # La primera visita cambia el token por uno de sesion y redirige; es el
        # destino de esa redireccion el que acepta el POST.
        respuesta = self.client.get(camino)
        self.client.post(respuesta.url, {'new_password1': NUEVA,
                                         'new_password2': NUEVA})

        self.olvidadizo.refresh_from_db()
        self.assertTrue(self.olvidadizo.check_password(NUEVA))

    def test_el_enlace_no_sirve_dos_veces(self):
        self.client.post('/password/reset/', {'email': 'olvidadizo@almacen.com'})
        enlace = [renglon for renglon in mail.outbox[0].body.splitlines()
                  if '/password/reset/' in renglon][0].strip()
        camino = enlace[enlace.index('/password/reset/'):]
        respuesta = self.client.get(camino)
        self.client.post(respuesta.url, {'new_password1': NUEVA,
                                         'new_password2': NUEVA})

        # El mismo enlace, otra vez: el token ya no vale porque la contrasena
        # con la que se firmo ya no es la de la cuenta.
        segunda = self.client.get(camino, follow=True)

        self.assertIn('no longer works', segunda.content.decode())


class ElEnlaceEstaDondeSeBuscaTests(TestCase):
    """
    El menu de la cuenta es un solo parcial para las dos pantallas, asi que el
    enlace tiene que salir en las dos. Se comprueban las dos de todos modos:
    que hoy compartan parcial no es una garantia para manana, y el movil es la
    pantalla que mas se usa.
    """

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')
        Catalog.objects.create(tenant=cls.tenant, category='CUSTOMER', name='ACME')
        cls.jefa = User.objects.create_user('jefa', password=VIEJA)
        UserProfile.objects.create(user=cls.jefa, tenant=cls.tenant, role='superadmin')

    def setUp(self):
        self.client.force_login(self.jefa)

    def test_en_el_tablero(self):
        respuesta = self.client.get('/dashboard/')

        self.assertIn('/cuenta/', respuesta.content.decode())

    def test_en_el_movil(self):
        respuesta = self.client.get('/mobile/')

        self.assertIn('/cuenta/', respuesta.content.decode())


class SeLeenEnEspanolTests(TestCase):
    """
    Las pantallas nuevas estan escritas en ingles, como el resto, y el espanol
    sale del catalogo. Sin esta prueba, un `msgid` mal copiado -- una coma de
    mas, un guion corto donde va uno largo -- deja la cadena en ingles sin que
    nada falle, y el resultado es la pantalla mitad en un idioma y mitad en
    otro que Diego no quiere ver.
    """

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')
        cls.operador = User.objects.create_user(
            'operador', password=VIEJA, email='operador@almacen.com')
        UserProfile.objects.create(user=cls.operador, tenant=cls.tenant, role='staff')

    def setUp(self):
        self.client.post('/preferencias/idioma/', {'idioma': 'es'})

    def test_la_pantalla_de_la_cuenta(self):
        self.client.force_login(self.operador)

        respuesta = self.client.get('/cuenta/')

        self.assertContains(respuesta, 'Cambiar mi contraseña')
        self.assertContains(respuesta, 'Contraseña actual')
        self.assertContains(respuesta, 'Correo de recuperación')

    def test_la_pantalla_de_recuperacion(self):
        respuesta = self.client.get('/password/reset/')

        self.assertContains(respuesta, 'Recupera tu contraseña')
        self.assertContains(respuesta, 'Enviar el enlace')

    def test_el_correo_que_se_manda(self):
        self.client.post('/password/reset/', {'email': 'operador@almacen.com'})

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('contraseña nueva', mail.outbox[0].subject)
        self.assertIn('Abre este enlace', mail.outbox[0].body)
