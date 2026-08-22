"""
Qué nombre lleva la pantalla de entrada.

`login.html` tenía «DYSER Group LLC» escrito a mano. Mientras hubo una sola
empresa eso solo era feo; desde que existe el nivel de plataforma es incorrecto,
porque quien administra el SaaS entra por esta misma pantalla, y el día que haya
una segunda empresa sus usuarios verían el nombre de la primera al entrar.

En el login no hay usuario todavía, así que la única forma de saber de qué
empresa se trata es el subdominio. Cuando lo dice, se nombra; cuando no —la
dirección general, que es por donde se entra hoy—, no se nombra ninguna.
"""
from django.test import TestCase, override_settings

from .models import Tenant


@override_settings(ALLOWED_HOSTS=['*'])
class LaPantallaDeEntradaTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')

    def test_por_la_direccion_general_no_se_nombra_ninguna_empresa(self):
        """Es la pantalla por la que entra el administrador de la plataforma."""
        respuesta = self.client.get('/', HTTP_HOST='wms2.example.com')

        self.assertEqual(respuesta.status_code, 200)
        self.assertNotContains(respuesta, 'Almacenes del Norte')
        self.assertNotContains(respuesta, 'DYSER')

    def test_por_el_subdominio_de_una_empresa_se_nombra_esa(self):
        respuesta = self.client.get('/', HTTP_HOST='norte.wms2.example.com')

        self.assertContains(respuesta, 'Almacenes del Norte')

    def test_el_subdominio_de_una_empresa_no_nombra_a_otra(self):
        Tenant.objects.create(name='Bodegas del Sur', type='organization',
                              subdomain='sur')

        respuesta = self.client.get('/', HTTP_HOST='norte.wms2.example.com')

        self.assertNotContains(respuesta, 'Bodegas del Sur')

    def test_una_empresa_dada_de_baja_no_se_nombra(self):
        """`is_active` decide si el subdominio resuelve; la pantalla lo hereda."""
        self.tenant.is_active = False
        self.tenant.save(update_fields=['is_active'])

        respuesta = self.client.get('/', HTTP_HOST='norte.wms2.example.com')

        self.assertNotContains(respuesta, 'Almacenes del Norte')
