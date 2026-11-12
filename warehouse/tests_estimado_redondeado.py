"""
El estimado que se le da al cliente: la exacta mas un margen, a numero cerrado.

Diego lo pidio el 12 nov 2026: la cifra de la hoja del cliente es la exacta
+15 %, subida al siguiente multiplo de 500. Margen y paso son parametros del
tenant, con fecha. Lo que fijan estas pruebas:

* El redondeo **siempre sube**, al siguiente multiplo del paso, y una cifra que
  ya es multiplo se queda donde esta.
* La casilla del cliente **sigue a la propuesta** cuando estaba vacia o tenia
  justo la propuesta anterior, y **no se pisa** si la casa tecleo otra cifra.
* Los parametros se cambian creando una version nueva; nunca hacia atras.
* El cajon trae lo que el guion necesita para la cuenta en vivo, y el telefono
  ya lo puede abrir.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from . import impuestos
from .models import (Catalog, ParametrosDeImpuestos, RenglonDeImpuestos,
                     Tenant, UserProfile, WarehouseOperation)
from .views import enlazar_renglon_de_impuestos


class RedondeoTests(TestCase):

    def test_sube_al_siguiente_500(self):
        # 12,340 x 1.15 = 14,191 -> 14,500
        self.assertEqual(impuestos.estimado_redondeado(Decimal('12340')), Decimal('14500.00'))

    def test_cae_en_el_mil_cuando_toca(self):
        # 12,700 x 1.15 = 14,605 -> 15,000
        self.assertEqual(impuestos.estimado_redondeado(Decimal('12700')), Decimal('15000.00'))

    def test_un_multiplo_exacto_no_sube(self):
        # 10,000 x 1.15 = 11,500 justos: ya es multiplo de 500 y no sube
        self.assertEqual(impuestos.estimado_redondeado(Decimal('10000')), Decimal('11500.00'))

    def test_cero_es_cero(self):
        self.assertEqual(impuestos.estimado_redondeado(0), Decimal('0.00'))

    def test_paso_mil_y_margen_cero(self):
        self.assertEqual(impuestos.estimado_redondeado(Decimal('14191'), Decimal('0'), Decimal('1000')),
                         Decimal('15000.00'))

    def test_paso_cero_no_redondea(self):
        self.assertEqual(impuestos.estimado_redondeado(Decimal('100'), Decimal('0.15'), 0),
                         Decimal('115.00'))

    def test_calcular_devuelve_la_propuesta(self):
        cuenta = impuestos.calcular(Decimal('10395'), incrementables=Decimal('200'),
                                    tipo_de_cambio=Decimal('19'))
        self.assertEqual(cuenta['T_total_pedimento'], Decimal('34376.91'))
        # 34,376.91 x 1.15 = 39,533.45 -> 40,000
        self.assertEqual(cuenta['U_estimado'], Decimal('40000.00'))


class BaseEstimado(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name='Dyser Group', type='organization',
                                           subdomain='dyser')
        cls.jefa = User.objects.create_user('jefa', password='x')
        UserProfile.objects.create(user=cls.jefa, tenant=cls.tenant, role='admin')
        cls.cliente = Catalog.objects.create(category='CUSTOMER', name='Acme',
                                             tenant=cls.tenant)
        cls.comprador = User.objects.create_user('comprador', password='x')
        UserProfile.objects.create(user=cls.comprador, tenant=cls.tenant,
                                   role='customer', customer=cls.cliente)
        ParametrosDeImpuestos.objects.create(
            tenant=cls.tenant, vigente_desde=date(2020, 1, 1),
            tipo_de_cambio=Decimal('19.0000'), prevalidacion=Decimal('300.00'))

    _n = 0

    def _op(self):
        type(self)._n += 1
        return WarehouseOperation.objects.create(
            tenant=self.tenant, operation_type='ENTRY', custom_id='ED-E%d' % self._n,
            customer=self.cliente, created_by=self.jefa, bundle_qty=1)

    def _renglon(self, **campos):
        datos = dict(tenant=self.tenant, customer=self.cliente, operation=self._op(),
                     valor_mercancia=Decimal('10395.00'), incrementables=Decimal('200.00'))
        datos.update(campos)
        return RenglonDeImpuestos.objects.create(**datos)


class LaCasillaSigueALaPropuestaTests(BaseEstimado):

    def test_vacia_toma_la_propuesta(self):
        r = self._renglon()
        r.seguir_la_propuesta(None)
        self.assertEqual(r.impuesto_reportado, Decimal('40000.00'))

    def test_si_seguia_la_propuesta_la_actualiza(self):
        r = self._renglon(impuesto_reportado=Decimal('40000.00'))
        anterior = r.propuesta()
        r.valor_mercancia = Decimal('20000')
        r.seguir_la_propuesta(anterior)
        self.assertEqual(r.impuesto_reportado, r.propuesta())
        self.assertNotEqual(r.impuesto_reportado, Decimal('40000.00'))

    def test_una_cifra_puesta_a_mano_no_se_pisa(self):
        r = self._renglon(impuesto_reportado=Decimal('45000.00'))
        anterior = r.propuesta()
        r.valor_mercancia = Decimal('20000')
        r.seguir_la_propuesta(anterior)
        self.assertEqual(r.impuesto_reportado, Decimal('45000.00'))

    def test_la_entrada_que_nace_con_valor_trae_la_propuesta(self):
        op = self._op()
        r = enlazar_renglon_de_impuestos(op, self.jefa, valor=Decimal('10395'),
                                         expresion='10395')
        r.refresh_from_db()
        # Sin incrementables: 10,395 x 19 = 197,505; T = 33,733.65 -> x1.15 = 38,793.70 -> 39,000
        self.assertEqual(r.impuesto_reportado, Decimal('39000.00'))

    def test_la_entrada_sin_valor_no_inventa_cifra(self):
        r = enlazar_renglon_de_impuestos(self._op(), self.jefa)
        self.assertIsNone(r.impuesto_reportado)

    def test_los_parametros_del_tenant_mandan(self):
        ParametrosDeImpuestos.objects.create(
            tenant=self.tenant, vigente_desde=date(2021, 1, 1),
            tipo_de_cambio=Decimal('19.0000'), prevalidacion=Decimal('300.00'),
            margen_estimado=Decimal('0'), redondeo_estimado=Decimal('1000'))
        r = self._renglon()
        # 34,376.91 sin margen, al siguiente mil
        self.assertEqual(r.propuesta(), Decimal('35000.00'))


class CajonTests(BaseEstimado):

    def test_el_cajon_trae_los_parametros_para_el_guion(self):
        r = self._renglon()
        self.client.force_login(self.jefa)
        resp = self.client.get('/impuestos/%d/calculo/' % r.pk,
                               HTTP_X_REQUESTED_WITH='fetch')
        self.assertContains(resp, 'data-margen="0.1500"')
        self.assertContains(resp, 'data-paso="500.00"')
        self.assertContains(resp, 'data-cifra="U"')
        self.assertContains(resp, '40,000')

    def test_sin_valor_tambien_pinta_las_casillas_escondidas(self):
        r = self._renglon(valor_mercancia=None, incrementables=None)
        self.client.force_login(self.jefa)
        resp = self.client.get('/impuestos/%d/calculo/' % r.pk,
                               HTTP_X_REQUESTED_WITH='fetch')
        self.assertContains(resp, 'data-papel="salida" hidden')
        self.assertContains(resp, 'data-cifra="T"')

    def test_el_telefono_puede_abrir_el_desglose(self):
        r = self._renglon()
        self.client.force_login(self.jefa)
        resp = self.client.get('/impuestos/', {'customer': self.cliente.pk})
        self.assertContains(resp, 'aria-controls="cajon-f-%d"' % r.pk)
        self.assertNotContains(resp, 'aria-disabled="true"')

    def test_guardar_desde_el_cajon_deja_la_cifra(self):
        r = self._renglon()
        self.client.force_login(self.jefa)
        self.client.post('/impuestos/%d/save/' % r.pk, {
            'valor': '10395', 'incrementables': '200', 'tasa_igi': '0',
            'tmec_present': '1', 'impuesto_reportado': '40000.00'})
        r.refresh_from_db()
        self.assertEqual(r.impuesto_reportado, Decimal('40000.00'))
        self.assertEqual(r.impuesto_estimado, Decimal('40000.00'))


class ParametrosTests(BaseEstimado):

    def _datos(self, **cambios):
        datos = {'tipo_de_cambio': '20.5', 'prevalidacion': '320',
                 'dta_sin_tmec': '0.008', 'dta_con_tmec': '0',
                 'margen_pct': '12.5', 'redondeo_estimado': '1000',
                 'vigente_desde': date.today().isoformat()}
        datos.update(cambios)
        return datos

    def test_guardar_crea_una_version_nueva(self):
        self.client.force_login(self.jefa)
        antes = ParametrosDeImpuestos.objects.filter(tenant=self.tenant).count()
        resp = self.client.post('/impuestos/parametros/', self._datos())
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(ParametrosDeImpuestos.objects.filter(tenant=self.tenant).count(), antes + 1)
        v = ParametrosDeImpuestos.vigentes(self.tenant)
        self.assertEqual(v.margen_estimado, Decimal('0.1250'))
        self.assertEqual(v.redondeo_estimado, Decimal('1000'))
        self.assertEqual(v.tipo_de_cambio, Decimal('20.5'))

    def test_no_acepta_una_fecha_pasada(self):
        self.client.force_login(self.jefa)
        ayer = (date.today() - timedelta(days=1)).isoformat()
        resp = self.client.post('/impuestos/parametros/', self._datos(vigente_desde=ayer))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(ParametrosDeImpuestos.objects.filter(tenant=self.tenant).count(), 1)

    def test_rechaza_un_margen_imposible(self):
        self.client.force_login(self.jefa)
        self.client.post('/impuestos/parametros/', self._datos(margen_pct='150'))
        self.assertEqual(ParametrosDeImpuestos.objects.filter(tenant=self.tenant).count(), 1)

    def test_el_cliente_no_entra(self):
        self.client.force_login(self.comprador)
        self.assertEqual(self.client.get('/impuestos/parametros/').status_code, 404)

    def test_la_pantalla_abre(self):
        self.client.force_login(self.jefa)
        resp = self.client.get('/impuestos/parametros/')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'name="margen_pct"')
