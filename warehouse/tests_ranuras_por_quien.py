"""
Las ranuras del pedimento, separadas por quien las sube.

Lo que se prueba:

1. Que una ranura propia se crea para la empresa o para el cliente, y que se
   puede cambiar despues.
2. Que la del cliente no frena el envio a revision, sino la validacion.
3. Que el cliente puede subir a la suya aunque el pedimento ya este con el, y
   no a una de la empresa.
4. Que la pantalla pone la empresa a la izquierda y el cliente a la derecha.
"""
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile

from .models import (Pedimento, PedimentoBundle, PedimentoDocument,
                     RanuraDePedimento, UserProfile)
from .tests_pedimentos import BaseDeAlmacen


def pdf(nombre='x.pdf'):
    return SimpleUploadedFile(nombre, b'%PDF-1.4 x', content_type='application/pdf')


class Base(BaseDeAlmacen):

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.lopez = User.objects.create_user('lopez', password='x')
        UserProfile.objects.create(user=cls.lopez, tenant=cls.tenant,
                                   role='customer', customer=cls.cliente)

    def setUp(self):
        self.client.force_login(self.jefa)
        self.entrada = self.operacion('ED261122-0001', bundle_qty=2)
        self.ped = Pedimento.objects.create(
            tenant=self.tenant, customer=self.cliente, orden=1,
            ped_aduana='24', ped_patente='1780', ped_consecutivo='6003555',
            created_by=self.jefa)
        PedimentoBundle.objects.create(pedimento=self.ped, operation=self.entrada,
                                       bultos=2)
        RanuraDePedimento.de_tenant(self.tenant)   # siembra las de fabrica

    def agregar(self, nombre, quien, obligatoria=True):
        datos = {'nueva': nombre, 'nueva_quien': quien, 'agregar': '1'}
        if obligatoria:
            datos['nueva_obligatoria'] = 'on'
        # Las casillas de las que ya hay, como las manda el formulario.
        for r in RanuraDePedimento.objects.filter(tenant=self.tenant):
            if r.activa:
                datos[f'activa_{r.pk}'] = 'on'
            if r.obligatoria:
                datos[f'obligatoria_{r.pk}'] = 'on'
        self.client.post('/pedimentos/slots/', datos)
        return RanuraDePedimento.objects.get(tenant=self.tenant, nombre=nombre)

    def subir(self, ranura, quien=None):
        if quien:
            self.client.force_login(quien)
        return self.client.post(f'/pedimentos/{self.ped.pk}/upload/',
                                {'ranura': ranura, 'archivo': pdf()})

    def subir_mv(self):
        for ranura in PedimentoDocument.RANURAS_DEL_CLIENTE:
            PedimentoDocument.objects.create(pedimento=self.ped, ranura=ranura,
                                             original_name='mv.pdf', file=pdf('mv.pdf'))

    def pantalla(self):
        return self.client.get('/pedimentos/', {'customer': self.cliente.pk,
                                                'solo': self.ped.pk})


class CrearLaRanuraTests(Base):

    def test_se_elige_quien_la_sube(self):
        self.assertFalse(self.agregar('Conocimiento de embarque', 'empresa').de_cliente)
        self.assertTrue(self.agregar('Packing list del cliente', 'cliente').de_cliente)

    def test_sin_elegir_es_de_la_empresa(self):
        self.client.post('/pedimentos/slots/', {'nueva': 'Otra', 'agregar': '1'})
        self.assertFalse(RanuraDePedimento.objects.get(nombre='Otra').de_cliente)

    def test_se_puede_cambiar_despues(self):
        r = self.agregar('Packing list', 'empresa')
        datos = {f'activa_{r.pk}': 'on', f'quien_{r.pk}': 'cliente'}
        self.client.post('/pedimentos/slots/', datos)
        r.refresh_from_db()
        self.assertTrue(r.de_cliente)

    def test_la_pantalla_ofrece_empresa_y_cliente(self):
        respuesta = self.client.get('/pedimentos/slots/')
        self.assertContains(respuesta, 'name="nueva_quien" value="empresa" checked')
        self.assertContains(respuesta, 'name="nueva_quien" value="cliente"')


class LoQueFrenaCadaUnaTests(Base):

    def test_la_del_cliente_no_frena_la_revision(self):
        r = self.agregar('Packing list', 'cliente')
        self.assertNotIn(r.etiqueta, self.ped.faltantes_para_revision)
        self.assertNotIn(r.clave, [x.clave for x in RanuraDePedimento.de_envio(self.tenant)])

    def test_la_de_la_empresa_si(self):
        r = self.agregar('Conocimiento de embarque', 'empresa')
        self.assertIn(r.etiqueta, self.ped.faltantes_para_revision)

    def test_la_del_cliente_obligatoria_frena_la_validacion(self):
        r = self.agregar('Packing list', 'cliente')
        Pedimento.objects.filter(pk=self.ped.pk).update(estado=Pedimento.APROBADO)
        self.ped.refresh_from_db()
        self.subir_mv()
        self.assertEqual(self.ped.faltan_del_cliente, ['Packing list'])
        self.assertFalse(self.ped.tiene_mv)
        respuesta = self.client.post(f'/pedimentos/{self.ped.pk}/validated/')
        self.assertEqual(respuesta.status_code, 422)
        self.assertContains(respuesta, 'Packing list', status_code=422)

        PedimentoDocument.objects.create(pedimento=self.ped, ranura=r.clave,
                                         original_name='pl.pdf', file=pdf())
        self.assertTrue(self.ped.tiene_mv)
        self.client.post(f'/pedimentos/{self.ped.pk}/validated/')
        self.ped.refresh_from_db()
        self.assertEqual(self.ped.estado, Pedimento.VALIDADO)

    def test_la_del_cliente_no_obligatoria_no_frena(self):
        self.agregar('Fotos de la carga', 'cliente', obligatoria=False)
        self.subir_mv()
        self.assertEqual(self.ped.faltan_del_cliente, [])

    def test_sin_ranuras_propias_sigue_siendo_la_mv(self):
        self.assertEqual(len(self.ped.faltan_del_cliente), 2)
        self.subir_mv()
        self.assertTrue(self.ped.tiene_mv)


class QuienSubeQueTests(Base):

    def test_el_cliente_sube_a_la_suya_con_el_pedimento_en_revision(self):
        r = self.agregar('Packing list', 'cliente')
        Pedimento.objects.filter(pk=self.ped.pk).update(estado=Pedimento.EN_REVISION)
        respuesta = self.subir(r.clave, quien=self.lopez)
        self.assertEqual(respuesta.status_code, 302)
        self.assertTrue(PedimentoDocument.objects.filter(
            pedimento=self.ped, ranura=r.clave).exists())

    def test_el_cliente_no_sube_a_una_de_la_empresa(self):
        r = self.agregar('Conocimiento de embarque', 'empresa')
        self.assertEqual(self.subir(r.clave, quien=self.lopez).status_code, 404)
        self.assertEqual(self.subir(PedimentoDocument.COVE, quien=self.lopez).status_code, 404)

    def test_la_empresa_tambien_puede_subir_la_del_cliente(self):
        r = self.agregar('Packing list', 'cliente')
        Pedimento.objects.filter(pk=self.ped.pk).update(estado=Pedimento.EN_REVISION)
        self.assertEqual(self.subir(r.clave).status_code, 302)

    def test_la_de_la_empresa_se_cierra_al_salir_de_borrador(self):
        r = self.agregar('Conocimiento de embarque', 'empresa')
        Pedimento.objects.filter(pk=self.ped.pk).update(estado=Pedimento.EN_REVISION)
        self.assertEqual(self.subir(r.clave).status_code, 422)


class DosColumnasTests(Base):

    def test_empresa_a_la_izquierda_y_cliente_a_la_derecha(self):
        self.agregar('Packing list del cliente', 'cliente')
        self.agregar('Conocimiento de embarque', 'empresa')
        html = self.pantalla().content.decode()
        izquierda = html.index('Company documents')
        derecha = html.index('Customer documents')
        self.assertLess(izquierda, derecha)
        # Cada ranura en su columna.
        self.assertLess(izquierda, html.index('Conocimiento de embarque'))
        self.assertLess(html.index('Conocimiento de embarque'), derecha)
        self.assertLess(derecha, html.index('Packing list del cliente'))

    def test_en_borrador_la_columna_del_cliente_explica_la_mv(self):
        respuesta = self.pantalla()
        self.assertContains(respuesta, 'class="ranuras-col col-cliente"')
        self.assertContains(respuesta, 'once the pedimento is under review')

    def test_el_cliente_ve_su_boton_de_subir(self):
        r = self.agregar('Packing list', 'cliente')
        self.client.force_login(self.lopez)
        respuesta = self.client.get('/pedimentos/', {'customer': self.cliente.pk})
        self.assertContains(respuesta, f'name="ranura" value="{r.clave}"')

    def test_en_espanol(self):
        from django.utils import translation
        with translation.override('es'):
            respuesta = self.client.get(
                '/pedimentos/', {'customer': self.cliente.pk, 'solo': self.ped.pk},
                HTTP_ACCEPT_LANGUAGE='es')
        self.assertContains(respuesta, 'Documentos de la empresa')
        self.assertContains(respuesta, 'Documentos del cliente')
