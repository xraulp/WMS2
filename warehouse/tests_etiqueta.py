"""
La etiqueta identifica el bulto, no solo la operacion.

Hasta ahora las seis etiquetas de una entrada de seis pallets decian
exactamente lo mismo, asi que pistolear seis veces el mismo pallet daba el
mismo resultado que pistolear los seis. Sobre esa diferencia se sostiene "nada
se carga sin pistolear": es lo que permite saber no solo que son 19 pallets,
sino cuales 19.

Y el codigo de rayas no es un respaldo del QR: las pistolas 1D no leen un QR, y
hoy no leerian nada de lo que esta pegado en la bodega.
"""
from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase

from . import bultos
from .models import Catalog, Tenant, UserProfile, WarehouseOperation
from .utils import codigo_de_barras, generate_label_pdf, operation_digital_url


class ElCodigoDeUnBultoTests(SimpleTestCase):

    def test_el_codigo_lleva_la_operacion_y_el_bulto(self):
        self.assertEqual(bultos.codigo('ED260901-0001', 3), 'ED260901-0001-3')

    def test_se_lee_del_codigo_de_rayas(self):
        self.assertEqual(bultos.leer('ED260901-0001-3'),
                         ('ED260901-0001', 3))

    def test_se_lee_del_qr_entero(self):
        url = 'https://dyser.example.com/mobile/?tab=digital&q=ED260901-0001&b=3'
        self.assertEqual(bultos.leer(url), ('ED260901-0001', 3))

    def test_un_qr_viejo_sigue_diciendo_de_que_operacion_es(self):
        # Las etiquetas pegadas antes de esto no llevan `b=`. Siguen abriendo
        # el expediente, que es para lo que se pegaron.
        url = 'https://dyser.example.com/mobile/?tab=digital&q=ED260901-0001'
        self.assertEqual(bultos.leer(url), ('ED260901-0001', None))

    def test_el_identificador_tecleado_a_mano(self):
        # Cuando la etiqueta esta rota y solo se lee la parte de arriba.
        self.assertEqual(bultos.leer('ed260901-0001'), ('ED260901-0001', None))

    def test_lo_que_no_se_entiende_se_rechaza(self):
        # No se adivina: un codigo interpretado a medias es peor que uno
        # rechazado, porque lo segundo se ve en el momento y lo primero aparece
        # en la aduana.
        for basura in ('', '   ', 'hola', '12345', 'ED260901', 'ED260901-0001-'):
            self.assertEqual(bultos.leer(basura), (None, None), basura)

    def test_los_espacios_de_alrededor_no_estorban(self):
        # La pistola manda el codigo y un Enter; a veces llega con espacios.
        self.assertEqual(bultos.leer('  ED260901-0001-7  '),
                         ('ED260901-0001', 7))


class ElCodigoDeBarrasTests(SimpleTestCase):

    def test_lleva_el_texto_legible_debajo(self):
        # Para el dia que falle todo y alguien tenga que leerlo con los ojos.
        tabla = codigo_de_barras('ED260901-0001-3')
        self.assertEqual(tabla._cellvalues[0][0].value, 'ED260901-0001-3')
        self.assertEqual(tabla._cellvalues[1][0].text, 'ED260901-0001-3')

    def test_no_desborda_el_hueco_que_tiene(self):
        # Un codigo cortado no lo lee ninguna pistola.
        hueco = 48 * 72 / 25.4
        for texto in ('ED260901-0001-1', 'ED260901-0001-12345'):
            self.assertLessEqual(codigo_de_barras(texto)._cellvalues[0][0].width,
                                 hueco, texto)


class LaEtiquetaImpresaTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Dyser Group', type='organization', subdomain='dyser')
        cls.jefa = User.objects.create_user('jefa', password='x')
        UserProfile.objects.create(user=cls.jefa, tenant=cls.tenant, role='admin')
        cls.cliente = Catalog.objects.create(
            category='CUSTOMER', name='Acme', tenant=cls.tenant)
        cls.op = WarehouseOperation.objects.create(
            tenant=cls.tenant, operation_type='ENTRY', custom_id='ED260901-0001',
            customer=cls.cliente, bundle_qty=3)

    def test_cada_bulto_tiene_su_propio_qr(self):
        base = operation_digital_url(self.op, '/mobile/')
        urls = {bultos.url_del_qr(base, self.op.custom_id, n) for n in (1, 2, 3)}
        self.assertEqual(len(urls), 3)
        for n in (1, 2, 3):
            self.assertEqual(
                bultos.leer(bultos.url_del_qr(base, self.op.custom_id, n)),
                ('ED260901-0001', n))

    def test_las_etiquetas_se_siguen_imprimiendo(self):
        pdf = generate_label_pdf(self.op)
        datos = pdf.read() if hasattr(pdf, 'read') else pdf
        self.assertTrue(datos.startswith(b'%PDF'))
        self.assertGreater(len(datos), 2000)
