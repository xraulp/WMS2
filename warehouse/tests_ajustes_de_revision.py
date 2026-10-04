"""
Los ajustes que Diego pidio al revisar la aplicacion el 4 de octubre de 2026.

Cada clase es un punto de su lista. Lo que se prueba aqui es lo que el
servidor decide -- que se pinta, a quien, y que se guarda --; el aspecto se
revisa en un navegador.
"""
from django.contrib.auth.models import User
from django.test import TestCase

from .models import (Catalog, CrossingTask, CrossingTaskItem, Pedimento,
                     PedimentoBundle, RenglonDeImpuestos, Tenant, UserProfile,
                     WarehouseOperation)
from .tests_cruces import BaseDeCruces


class BaseDeRevision(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Dyser Group', type='organization', subdomain='dyser')
        cls.jefa = User.objects.create_user('jefa', password='x')
        UserProfile.objects.create(user=cls.jefa, tenant=cls.tenant, role='admin')
        cls.cliente = Catalog.objects.create(
            category='CUSTOMER', name='Acme', tenant=cls.tenant, language='en')
        cls.duenio = User.objects.create_user('acme', password='x')
        UserProfile.objects.create(user=cls.duenio, tenant=cls.tenant,
                                   role='customer', customer=cls.cliente)

    def operacion(self, custom_id, **extra):
        return WarehouseOperation.objects.create(
            tenant=self.tenant, operation_type='ENTRY', custom_id=custom_id,
            customer=self.cliente, created_by=self.jefa, **extra)


# ── El menu de arriba en las pantallas aparte ───────────────────────────────

class ElMenuDeArribaTests(BaseDeRevision):

    def setUp(self):
        self.client.force_login(self.jefa)

    def test_las_tres_pantallas_lo_pintan_con_la_suya_marcada(self):
        for url, activa in (('/pedimentos/', '/pedimentos/'),
                            ('/cruces/', '/cruces/'),
                            ('/impuestos/', '/impuestos/')):
            html = self.client.get(url, {'customer': self.cliente.pk}).content.decode()
            self.assertIn('class="menu-sup"', html, url)
            self.assertIn('href="%s" class="on"' % activa, html, url)
            self.assertIn('?tab=database', html, url)

    def test_en_el_telefono_no_se_pinta(self):
        movil = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) Mobile/15E148'
        html = self.client.get('/cruces/', {'customer': self.cliente.pk},
                               HTTP_USER_AGENT=movil).content.decode()
        self.assertNotIn('class="menu-sup"', html)

    def test_al_cliente_no_le_ofrece_lo_que_no_puede_ver(self):
        self.client.force_login(self.duenio)
        html = self.client.get('/cruces/').content.decode()
        self.assertIn('class="menu-sup"', html)
        self.assertNotIn('?tab=locations', html)
        self.assertNotIn('?tab=users', html)


# ── El alta de operacion ya no pide ETA ─────────────────────────────────────

class SinEtaEnElAltaTests(BaseDeRevision):

    def test_ni_el_tablero_ni_el_movil_lo_piden(self):
        self.client.force_login(self.jefa)
        for url in ('/dashboard/', '/mobile/'):
            html = self.client.get(url).content.decode()
            self.assertNotIn('name="eta"', html, url)


# ── La hoja de impuestos del cliente ────────────────────────────────────────

class LaHojaDelClienteTests(BaseDeRevision):

    def setUp(self):
        self.op = self.operacion('ED-H1', bundle_qty=1)
        RenglonDeImpuestos.objects.create(
            tenant=self.tenant, customer=self.cliente, operation=self.op)

    def hoja(self, usuario):
        self.client.force_login(usuario)
        return self.client.get('/impuestos/', {'customer': self.cliente.pk}).content.decode()

    def test_el_cliente_no_ve_el_tipo_de_cambio(self):
        html = self.hoja(self.duenio)
        self.assertNotIn('id="sim-tc"', html)
        # Ni los numeros con que se simula, que llevan el colchon dentro.
        self.assertNotIn('id="datos-sim"', html)

    def test_la_casa_lo_sigue_teniendo(self):
        html = self.hoja(self.jefa)
        self.assertIn('id="sim-tc"', html)

    def test_actualizar_va_antes_que_el_pdf(self):
        html = self.hoja(self.duenio)
        actualizar = html.index('href="/impuestos/?customer=%d"' % self.cliente.pk)
        pdf = html.index('/impuestos/pdf/')
        self.assertLess(actualizar, pdf)


# ── Los cruces vistos por el cliente ────────────────────────────────────────

class LosCrucesDelClienteTests(BaseDeCruces):

    def setUp(self):
        super().setUp()
        self.t = self.tarea()
        self.libre = self.operacion('ED-L1', bundle_qty=2)

    def pantalla(self, usuario):
        self.client.force_login(usuario)
        return self.client.get('/cruces/', {'customer': self.cliente.pk}).content.decode()

    def test_el_cliente_no_ve_los_papeles_de_bodega(self):
        html = self.pantalla(self.duenio)
        self.assertNotIn('class="papeles"', html)

    def test_la_casa_los_sigue_viendo(self):
        html = self.pantalla(self.jefa)
        self.assertIn('class="papeles"', html)

    def test_agregar_y_mover_el_dia_van_en_el_titulo_y_cerrados(self):
        html = self.pantalla(self.duenio)
        titulo = html.index('class="tarea-acc"')
        self.assertLess(titulo, html.index('<table>', html.index('id="tarea-%d"' % self.t.pk)))
        self.assertIn('id="meter-%d" hidden' % self.t.pk, html)
        self.assertIn('id="dia-%d" hidden' % self.t.pk, html)

    def test_sin_nada_libre_no_se_ofrece_agregar(self):
        CrossingTaskItem.objects.create(task=self.t, operation=self.libre,
                                        added_by=self.jefa)
        html = self.pantalla(self.duenio)
        self.assertNotIn('id="meter-%d"' % self.t.pk, html)


# ── El formulario de clientes del tenant ───────────────────────────────────

class ElFormularioDeClientesTests(BaseDeRevision):

    def setUp(self):
        self.client.force_login(self.jefa)

    def test_el_alta_no_pide_idioma(self):
        html = self.client.get('/dashboard/').content.decode()
        self.assertNotIn('name="language"', html)

    def test_la_edicion_tampoco_y_guardar_no_lo_borra(self):
        html = self.client.get('/catalog/%d/edit/' % self.cliente.pk).content.decode()
        self.assertNotIn('name="language"', html)
        self.client.post('/catalog/%d/edit/' % self.cliente.pk, {'phone': '123'})
        self.cliente.refresh_from_db()
        self.assertEqual(self.cliente.language, 'en')
        self.assertEqual(self.cliente.phone, '123')

    def test_editar_un_cliente_repinta_la_tabla_de_clientes(self):
        html = self.client.get('/catalog/%d/edit/' % self.cliente.pk).content.decode()
        self.assertIn('hx-target="#customer-table"', html)
        transportista = Catalog.objects.create(
            category='CARRIER', name='Fletes', tenant=self.tenant)
        html = self.client.get('/catalog/%d/edit/' % transportista.pk).content.decode()
        self.assertIn('hx-target="#catalog-table"', html)

    def test_cancelar_cierra_el_modal_del_tablero(self):
        html = self.client.get('/catalog/%d/edit/' % self.cliente.pk).content.decode()
        self.assertIn("'cat-edit-modal'", html)


# ── El numero de pedimento que ya trae la entrada ───────────────────────────

class ElNumeroDeLaEntradaTests(BaseDeRevision):

    def setUp(self):
        self.client.force_login(self.jefa)
        self.ped = Pedimento.objects.create(
            tenant=self.tenant, customer=self.cliente, orden=1, created_by=self.jefa)

    def asignar(self, op, bultos=1):
        return self.client.post('/pedimentos/%d/assign/' % self.ped.pk,
                                {'operation': op.pk, 'bultos': bultos})

    def test_el_pedimento_sin_numero_se_queda_con_el_de_la_entrada(self):
        op = self.operacion('ED-P1', bundle_qty=2, ped_aduana='24',
                            ped_patente='1780', ped_consecutivo='6003555')
        self.asignar(op)
        self.ped.refresh_from_db()
        self.assertEqual(self.ped.numero, '24-1780-6003555')

    def test_tambien_con_el_numero_escrito_de_antes(self):
        op = self.operacion('ED-P2', bundle_qty=2, pedimento='24-1780-6003555')
        self.asignar(op)
        self.ped.refresh_from_db()
        self.assertEqual(self.ped.numero, '24-1780-6003555')

    def test_uno_que_ya_tiene_numero_no_se_toca(self):
        self.ped.ped_aduana, self.ped.ped_patente = '24', '1780'
        self.ped.ped_consecutivo = '6000001'
        self.ped.save()
        op = self.operacion('ED-P3', bundle_qty=2, ped_aduana='24',
                            ped_patente='1780', ped_consecutivo='6003555')
        self.asignar(op)
        self.ped.refresh_from_db()
        self.assertEqual(self.ped.numero, '24-1780-6000001')

    def test_si_choca_la_patente_no_se_mete_nada(self):
        Pedimento.objects.create(
            tenant=self.tenant, customer=self.cliente, orden=2,
            ped_aduana='24', ped_patente='1515', ped_consecutivo='6005000')
        op = self.operacion('ED-P4', bundle_qty=2, ped_aduana='24',
                            ped_patente='1780', ped_consecutivo='6003555')
        self.asignar(op)
        self.ped.refresh_from_db()
        self.assertEqual(self.ped.numero, '')
        self.assertFalse(PedimentoBundle.objects.filter(pedimento=self.ped).exists())

    def test_sin_numero_en_la_entrada_todo_sigue_igual(self):
        op = self.operacion('ED-P5', bundle_qty=2)
        self.asignar(op)
        self.ped.refresh_from_db()
        self.assertEqual(self.ped.numero, '')
        self.assertTrue(PedimentoBundle.objects.filter(pedimento=self.ped).exists())
