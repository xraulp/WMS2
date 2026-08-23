"""
Facturación de la plataforma.

Antes esto eran tres campos sueltos dentro de `Subscription` —número de
factura, fecha e importe— y `Subscription` es **una fila por empresa**. O sea
que cabía una sola factura por cliente: emitir la de septiembre pisaba la de
agosto. No había historial, ni estado de pago, ni forma de saber quién debía.

Lo que fijan estas pruebas es lo que hace que una serie de facturas sirva para
cobrar:

* **La numeración no se repite ni deja huecos**, y cancelar no libera un número
  que ya salió al cliente.
* **El monto y el plan se congelan** al emitir: si mañana sube el precio, la
  factura sigue diciendo lo que se cobró.
* **«Vencida» se deduce de la fecha**, no se guarda. Guardarlo obligaría a un
  proceso diario que fuera marcándolas, y el día que no corriera la pantalla
  mentiría.
* **Solo el administrador de plataforma emite, cobra y cancela.** El soporte ve
  el listado entero, porque para atender una llamada necesita saber si la
  empresa está al corriente.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from .models import Invoice, InvoiceSequence, PlatformUser, Tenant, UserProfile


class BaseFacturacion(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Tenant.objects.create(
            name='Almacenes del Norte', type='organization', subdomain='norte',
            plan='pro')
        cls.otra = Tenant.objects.create(
            name='Bodegas del Sur', type='organization', subdomain='sur',
            plan='starter')

        cls.admin_plataforma = User.objects.create_user('plataforma', password='x')
        PlatformUser.objects.create(user=cls.admin_plataforma, role='admin')

        cls.soporte = User.objects.create_user('soporte', password='x')
        PlatformUser.objects.create(user=cls.soporte, role='staff')

        # Un administrador de empresa: manda dentro de la suya y no pinta nada
        # en la plataforma.
        cls.admin_empresa = User.objects.create_user('admin_norte', password='x')
        UserProfile.objects.create(user=cls.admin_empresa, tenant=cls.empresa,
                                   role='admin')

    def _factura(self, empresa=None, monto='250.00', vence=None, mes=None):
        hoy = timezone.localdate()
        inicio = mes or date(hoy.year, hoy.month, 1)
        empresa = empresa or self.empresa
        return Invoice.objects.create(
            tenant=empresa, numero=Invoice.siguiente_numero(),
            periodo_inicio=inicio, periodo_fin=inicio + timedelta(days=27),
            emitida_el=hoy, vence_el=vence or (hoy + timedelta(days=15)),
            plan=empresa.plan, monto_usd=Decimal(monto))

    def _emitir(self, **extra):
        datos = {
            'action': 'emitir',
            'tenant_id': self.empresa.pk,
            'periodo': timezone.localdate().strftime('%Y-%m'),
            'monto': '250.00',
            'vence_el': (timezone.localdate() + timedelta(days=15)).strftime('%Y-%m-%d'),
        }
        datos.update(extra)
        return self.client.post('/platform/invoices/', datos)


class LaNumeracionAguanta(BaseFacturacion):

    def test_dos_facturas_no_comparten_numero(self):
        una = self._factura()
        otra = self._factura()

        self.assertNotEqual(una.numero, otra.numero)

    def test_la_serie_no_deja_huecos(self):
        numeros = [self._factura().numero for _ in range(3)]

        self.assertEqual([n[-4:] for n in numeros], ['0001', '0002', '0003'])

    def test_el_numero_lleva_el_ano(self):
        anio = timezone.localdate().year

        self.assertTrue(self._factura().numero.startswith(f'INV-{anio}-'))

    def test_cancelar_no_libera_el_numero(self):
        """El número ya salió al cliente: reutilizarlo sería peor que el hueco."""
        una = self._factura()
        una.cancelar('emitida por error')

        siguiente = self._factura()

        self.assertNotEqual(siguiente.numero, una.numero)
        self.assertEqual(siguiente.numero[-4:], '0002')

    def test_cada_ano_empieza_su_propia_serie(self):
        Invoice.siguiente_numero(2025)
        Invoice.siguiente_numero(2025)

        self.assertEqual(Invoice.siguiente_numero(2026), 'INV-2026-0001')
        self.assertEqual(Invoice.siguiente_numero(2025), 'INV-2025-0003')

    def test_el_contador_sube_aunque_no_se_cree_la_factura(self):
        """Apartar es apartar: si luego no se usa, el hueco es preferible al repetido."""
        Invoice.siguiente_numero(2026)

        self.assertEqual(InvoiceSequence.objects.get(year=2026).last_value, 1)


class ElEstadoDeCadaFactura(BaseFacturacion):

    def test_nace_pendiente(self):
        self.assertEqual(self._factura().estado, Invoice.PENDIENTE)

    def test_vencida_se_deduce_de_la_fecha(self):
        """No es un estado guardado: nadie tiene que ir marcándolas cada día."""
        atrasada = self._factura(vence=timezone.localdate() - timedelta(days=3))

        self.assertTrue(atrasada.esta_vencida)
        self.assertEqual(atrasada.dias_de_atraso, 3)
        self.assertEqual(atrasada.estado, Invoice.PENDIENTE)

    def test_una_pagada_no_esta_vencida_aunque_la_fecha_pasara(self):
        pagada = self._factura(vence=timezone.localdate() - timedelta(days=30))
        pagada.marcar_pagada()

        self.assertFalse(pagada.esta_vencida)

    def test_marcar_pagada_guarda_cuando_y_la_referencia(self):
        factura = self._factura()

        factura.marcar_pagada(referencia='transferencia 8891')

        self.assertEqual(factura.estado, Invoice.PAGADA)
        self.assertEqual(factura.pagada_el, timezone.localdate())
        self.assertEqual(factura.referencia_de_pago, 'transferencia 8891')

    def test_una_pagada_no_se_vuelve_a_cobrar(self):
        """Cobrar dos veces pisaría la fecha del cobro real."""
        factura = self._factura()
        factura.marcar_pagada(cuando=date(2026, 1, 10))

        with self.assertRaises(ValueError):
            factura.marcar_pagada()

        factura.refresh_from_db()
        self.assertEqual(factura.pagada_el, date(2026, 1, 10))

    def test_una_pagada_no_se_cancela(self):
        """Hubo un cobro; borrarlo así dejaría el dinero sin explicación."""
        factura = self._factura()
        factura.marcar_pagada()

        with self.assertRaises(ValueError):
            factura.cancelar('me equivoque')

        factura.refresh_from_db()
        self.assertEqual(factura.estado, Invoice.PAGADA)

    def test_una_cancelada_no_se_cobra(self):
        factura = self._factura()
        factura.cancelar('duplicada')

        with self.assertRaises(ValueError):
            factura.marcar_pagada()

    def test_cancelar_deja_escrito_el_motivo(self):
        factura = self._factura()

        factura.cancelar('se facturó dos veces el mismo mes')

        self.assertEqual(factura.estado, Invoice.CANCELADA)
        self.assertEqual(factura.cancelada_el, timezone.localdate())
        self.assertIn('dos veces', factura.motivo_de_cancelacion)


class LoQueSeFacturaSeCongela(BaseFacturacion):

    def test_el_plan_de_la_factura_no_cambia_con_el_de_la_empresa(self):
        """La factura dice qué se cobró, no qué plan tiene hoy el cliente."""
        self.client.force_login(self.admin_plataforma)
        self._emitir()
        factura = Invoice.objects.get()

        self.empresa.plan = 'enterprise'
        self.empresa.save(update_fields=['plan'])

        factura.refresh_from_db()
        self.assertEqual(factura.plan, 'pro')

    def test_el_monto_se_guarda_tal_cual_se_captura(self):
        self.client.force_login(self.admin_plataforma)

        self._emitir(monto='1234.56')

        self.assertEqual(Invoice.objects.get().monto_usd, Decimal('1234.56'))

    def test_la_empresa_no_se_puede_borrar_con_facturas(self):
        """Una factura es registro de cobro: no desaparece con la empresa."""
        self._factura()

        from django.db.models import ProtectedError
        with self.assertRaises(ProtectedError):
            self.empresa.delete()


class SoloElAdministradorFactura(BaseFacturacion):

    def test_el_soporte_ve_el_listado(self):
        """Para atender una llamada necesita saber si la empresa está al corriente."""
        self._factura()
        self.client.force_login(self.soporte)

        respuesta = self.client.get('/platform/invoices/')

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'INV-')

    def test_el_soporte_no_emite(self):
        self.client.force_login(self.soporte)

        respuesta = self._emitir()

        self.assertEqual(respuesta.status_code, 403)
        self.assertEqual(Invoice.objects.count(), 0)

    def test_el_soporte_no_cobra(self):
        """Esconder el botón no es un permiso: el POST se comprueba igual."""
        factura = self._factura()
        self.client.force_login(self.soporte)

        respuesta = self.client.post('/platform/invoices/', {
            'action': 'pagar', 'invoice_id': factura.pk})

        self.assertEqual(respuesta.status_code, 403)
        factura.refresh_from_db()
        self.assertEqual(factura.estado, Invoice.PENDIENTE)

    def test_el_soporte_no_cancela(self):
        factura = self._factura()
        self.client.force_login(self.soporte)

        respuesta = self.client.post('/platform/invoices/', {
            'action': 'cancelar', 'invoice_id': factura.pk, 'motivo': 'porque si'})

        self.assertEqual(respuesta.status_code, 403)
        factura.refresh_from_db()
        self.assertEqual(factura.estado, Invoice.PENDIENTE)

    def test_el_administrador_de_una_empresa_no_entra(self):
        """Manda en su empresa; la facturación de la plataforma no es suya."""
        self.client.force_login(self.admin_empresa)

        self.assertEqual(self.client.get('/platform/invoices/').status_code, 403)

    def test_quien_no_ha_entrado_no_ve_nada(self):
        respuesta = self.client.get('/platform/invoices/')

        self.assertEqual(respuesta.status_code, 302)


class EmitirDesdeLaPantalla(BaseFacturacion):

    def setUp(self):
        self.client.force_login(self.admin_plataforma)

    def test_emitir_crea_la_factura_con_su_periodo(self):
        self._emitir(periodo='2026-02')

        factura = Invoice.objects.get()
        self.assertEqual(factura.periodo_inicio, date(2026, 2, 1))
        self.assertEqual(factura.periodo_fin, date(2026, 2, 28))

    def test_el_periodo_cubre_el_mes_entero_en_bisiesto(self):
        """Febrero de 2028 tiene 29 días, y el último día importa en un periodo."""
        self._emitir(periodo='2028-02')

        self.assertEqual(Invoice.objects.get().periodo_fin, date(2028, 2, 29))

    def test_queda_registrado_quien_la_emitio(self):
        self._emitir()

        self.assertEqual(Invoice.objects.get().emitida_por, self.admin_plataforma)

    def test_un_monto_de_cero_se_rechaza(self):
        respuesta = self._emitir(monto='0')

        self.assertEqual(Invoice.objects.count(), 0)
        self.assertContains(respuesta, 'mayor que cero')

    def test_un_monto_que_no_es_numero_se_rechaza(self):
        respuesta = self._emitir(monto='doscientos')

        self.assertEqual(Invoice.objects.count(), 0)
        self.assertContains(respuesta, 'mayor que cero')

    def test_un_monto_rechazado_no_gasta_numero(self):
        """El número se aparta al final, ya validado todo lo demás."""
        self._emitir(monto='0')

        self.assertFalse(InvoiceSequence.objects.exists())

    def test_sin_empresa_no_se_emite(self):
        respuesta = self._emitir(tenant_id='')

        self.assertEqual(Invoice.objects.count(), 0)
        self.assertContains(respuesta, 'Elige la empresa')

    def test_avisa_si_el_periodo_ya_estaba_facturado(self):
        """Duplicar el mes es el error caro: el cliente recibe dos cobros."""
        self._emitir()

        respuesta = self._emitir()

        self.assertEqual(Invoice.objects.count(), 1)
        self.assertContains(respuesta, 'ya tiene la factura')

    def test_se_puede_emitir_otra_del_mismo_mes_confirmando(self):
        self._emitir()

        self._emitir(confirmar_duplicado='1')

        self.assertEqual(Invoice.objects.count(), 2)

    def test_una_cancelada_no_bloquea_volver_a_facturar_el_mes(self):
        """Si se canceló, ese mes sigue sin cobrarse."""
        self._emitir()
        Invoice.objects.get().cancelar('numero equivocado')

        self._emitir()

        self.assertEqual(Invoice.objects.filter(estado=Invoice.PENDIENTE).count(), 1)

    def test_dos_empresas_pueden_facturar_el_mismo_mes(self):
        self._emitir()

        self._emitir(tenant_id=self.otra.pk)

        self.assertEqual(Invoice.objects.count(), 2)

    def test_cancelar_sin_motivo_se_rechaza(self):
        self._emitir()
        factura = Invoice.objects.get()

        respuesta = self.client.post('/platform/invoices/', {
            'action': 'cancelar', 'invoice_id': factura.pk, 'motivo': '   '})

        factura.refresh_from_db()
        self.assertEqual(factura.estado, Invoice.PENDIENTE)
        self.assertContains(respuesta, 'motivo')

    def test_cobrar_desde_la_pantalla(self):
        self._emitir()
        factura = Invoice.objects.get()

        self.client.post('/platform/invoices/', {
            'action': 'pagar', 'invoice_id': factura.pk,
            'referencia': 'spei 4471'})

        factura.refresh_from_db()
        self.assertEqual(factura.estado, Invoice.PAGADA)
        self.assertEqual(factura.referencia_de_pago, 'spei 4471')


class ElResumenYLosFiltros(BaseFacturacion):

    def setUp(self):
        hoy = timezone.localdate()
        self.pendiente = self._factura(monto='100.00')
        self.vencida = self._factura(monto='200.00',
                                     vence=hoy - timedelta(days=5))
        self.pagada = self._factura(monto='300.00')
        self.pagada.marcar_pagada()
        self.cancelada = self._factura(monto='999.00')
        self.cancelada.cancelar('duplicada')
        self.client.force_login(self.admin_plataforma)

    def test_el_resumen_suma_lo_que_toca(self):
        respuesta = self.client.get('/platform/invoices/')
        resumen = respuesta.context['resumen']

        # Pendiente incluye la vencida; la cancelada no cuenta en nada.
        self.assertEqual(resumen['pendiente_total'], Decimal('300.00'))
        self.assertEqual(resumen['vencido_total'], Decimal('200.00'))
        self.assertEqual(resumen['cobrado_total'], Decimal('300.00'))

    def test_el_filtro_de_vencidas_no_trae_las_que_estan_en_plazo(self):
        respuesta = self.client.get('/platform/invoices/?status=vencida')

        numeros = [f.numero for f in respuesta.context['facturas']]
        self.assertEqual(numeros, [self.vencida.numero])

    def test_el_filtro_por_empresa(self):
        self._factura(empresa=self.otra, monto='50.00')

        respuesta = self.client.get('/platform/invoices/?tenant=%s' % self.otra.pk)

        self.assertEqual(len(respuesta.context['facturas']), 1)

    def test_las_mas_recientes_van_arriba(self):
        respuesta = self.client.get('/platform/invoices/')

        facturas = list(respuesta.context['facturas'])
        self.assertEqual(facturas[0].numero, self.cancelada.numero)
