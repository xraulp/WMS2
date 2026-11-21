"""
Las listas enseñan lo reciente y lo que sigue en bodega; lo demas, en el historico.

Lo que se prueba: el corte de 4 meses, que una entrada vieja que sigue guardada
no se esconde nunca, que el buscador recorre todo, que `periodo=todo` lo quita,
que la tabla dice cuantas quedaron fuera, y lo mismo en Cruces realizados.
"""
from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from .models import Catalog, CrossingTask, Tenant, UserProfile, WarehouseOperation
from .periodo import fecha_de_corte


class FechaDeCorteTests(TestCase):

    def test_cuatro_meses_antes(self):
        self.assertEqual(fecha_de_corte(date(2026, 11, 21)), date(2026, 7, 21))

    def test_cruza_el_anio(self):
        self.assertEqual(fecha_de_corte(date(2026, 2, 15)), date(2025, 10, 15))

    def test_dia_que_no_existe_cae_en_el_ultimo_del_mes(self):
        self.assertEqual(fecha_de_corte(date(2026, 6, 30)), date(2026, 2, 28))
        self.assertEqual(fecha_de_corte(date(2026, 3, 31)), date(2025, 11, 30))


class BasePeriodo(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte')
        cls.jefa = User.objects.create_user('jefa_periodo', password='x')
        UserProfile.objects.create(user=cls.jefa, tenant=cls.tenant, role='admin')
        cls.acme = Catalog.objects.create(
            category='CUSTOMER', name='Acme', tenant=cls.tenant)

        hoy = timezone.localdate()
        viejo = hoy - timedelta(days=200)

        def op(custom_id, tipo, fecha, despachada=None):
            return WarehouseOperation.objects.create(
                tenant=cls.tenant, operation_type=tipo, custom_id=custom_id,
                customer=cls.acme, created_by=cls.jefa, date=fecha,
                entry_dispatched=despachada)

        op('NUEVA-SALIDA', 'EXIT', hoy)
        op('NUEVA-LIBERADA', 'ENTRY', hoy, 'SD-1')
        op('VIEJA-EN-BODEGA', 'ENTRY', viejo)                # sin folio: sigue guardada
        op('VIEJA-FOLIO-RARO', 'ENTRY', viejo, 'XSD-7')      # no empieza por SD: sigue guardada
        op('VIEJA-LIBERADA', 'ENTRY', viejo, 'PO1, sd-44')   # liberada
        op('VIEJA-SALIDA', 'EXIT', viejo)

    def setUp(self):
        self.client.force_login(self.jefa)

    def tabla(self, **parametros):
        r = self.client.get('/operations/search/', parametros)
        self.assertEqual(r.status_code, 200)
        return r, sorted(o.custom_id for o in r.context['operations'])


class TablaDeOperacionesTests(BasePeriodo):

    RECIENTES = ['NUEVA-LIBERADA', 'NUEVA-SALIDA', 'VIEJA-EN-BODEGA', 'VIEJA-FOLIO-RARO']

    def test_por_defecto_lo_reciente_y_lo_que_sigue_en_bodega(self):
        r, ids = self.tabla()
        self.assertEqual(ids, self.RECIENTES)
        self.assertEqual(r.context['en_historico'], 2)
        self.assertContains(r, 'View all history')

    def test_periodo_todo_trae_el_historico(self):
        r, ids = self.tabla(periodo='todo')
        self.assertEqual(len(ids), 6)
        self.assertIsNone(r.context['en_historico'])
        self.assertNotContains(r, 'View all history')

    def test_la_busqueda_recorre_todo(self):
        _, ids = self.tabla(q='VIEJA-SALIDA')
        self.assertEqual(ids, ['VIEJA-SALIDA'])

    def test_se_combina_con_los_demas_filtros(self):
        _, ids = self.tabla(type='EXIT')
        self.assertEqual(ids, ['NUEVA-SALIDA'])
        _, ids = self.tabla(type='EXIT', periodo='todo')
        self.assertEqual(ids, ['NUEVA-SALIDA', 'VIEJA-SALIDA'])

    def test_las_vencidas_no_se_acotan(self):
        r, _ = self.tabla(aging='1')
        self.assertIsNone(r.context['en_historico'])

    def test_el_tablero_arranca_acotado_aunque_traiga_q_del_qr(self):
        # En el tablero `?q=` abre el panel Digital; no es la busqueda de la tabla.
        r = self.client.get('/dashboard/', {'tab': 'digital', 'q': 'NUEVA-SALIDA'})
        self.assertEqual(sorted(o.custom_id for o in r.context['operations']), self.RECIENTES)
        self.assertEqual(r.context['en_historico'], 2)

    def test_el_movil_arranca_acotado(self):
        r = self.client.get('/mobile/')
        self.assertEqual(sorted(o.custom_id for o in r.context['operations']), self.RECIENTES)
        self.assertContains(r, 'name="periodo"')

    def test_en_espanol(self):
        from django.utils import translation
        with translation.override('es'):
            r = self.client.get('/operations/search/', HTTP_ACCEPT_LANGUAGE='es')
        self.assertContains(r, 'están en el histórico')


class CrucesRealizadosTests(BasePeriodo):

    def setUp(self):
        super().setUp()
        ahora = timezone.now()
        for nombre, dias in (('reciente', 10), ('vieja', 200)):
            t = CrossingTask.objects.create(
                tenant=self.tenant, customer=self.acme, created_by=self.jefa,
                fecha_de_cruce=timezone.localdate() - timedelta(days=dias))
            CrossingTask.objects.filter(pk=t.pk).update(
                entregada_en=ahora - timedelta(days=dias))
            setattr(self, nombre, t)

    def test_por_defecto_los_ultimos_4_meses(self):
        r = self.client.get('/cruces/done/')
        self.assertEqual([t.pk for t in r.context['tareas']], [self.reciente.pk])
        self.assertEqual(r.context['en_historico'], 1)

    def test_periodo_todo(self):
        r = self.client.get('/cruces/done/', {'periodo': 'todo'})
        self.assertEqual({t.pk for t in r.context['tareas']},
                         {self.reciente.pk, self.vieja.pk})

    def test_la_busqueda_recorre_todo(self):
        r = self.client.get('/cruces/done/', {'q': self.vieja.custom_id})
        self.assertEqual([t.pk for t in r.context['tareas']], [self.vieja.pk])
