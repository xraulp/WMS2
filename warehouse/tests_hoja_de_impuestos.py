"""
La hoja de impuestos: los seis status, los tres estados del valor y la casilla
que suma.

La hoja no se abre "al llegar a un paso": esta siempre, una por cliente, viva
desde que le llega el primer embarque hasta que cruza el ultimo. De toda la
tabla, la unica columna que se teclea es la del valor -- el status, las fechas,
la entrada, el pedido y el pedimento ya estan en el sistema por haber hecho el
trabajo.

La prueba que mas vale es `test_el_ejemplo_del_excel_de_punta_a_punta`: la
misma cifra del archivo de Diego, pero pasando por el formulario. Si esa falla,
lo que se le manda al cliente esta mal.
"""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase

from . import impuestos
from .models import (Catalog, OperationDocument, ParametrosDeImpuestos,
                     Pedimento, PedimentoBundle, RenglonDeImpuestos, Tenant,
                     UserProfile, WarehouseOperation)


class BaseDeAlmacen(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name='Dyser Group', type='organization', subdomain='dyser')
        cls.jefa = User.objects.create_user('jefa', password='x')
        UserProfile.objects.create(user=cls.jefa, tenant=cls.tenant, role='admin')
        cls.cliente = Catalog.objects.create(
            category='CUSTOMER', name='Acme', tenant=cls.tenant)

    def operacion(self, custom_id, **extra):
        return WarehouseOperation.objects.create(
            tenant=self.tenant, operation_type='ENTRY', custom_id=custom_id,
            customer=self.cliente, created_by=self.jefa, **extra)


# ── La hoja de impuestos ─────────────────────────────────────────────────────

class LaCasillaQueSumaTests(SimpleTestCase):
    """
    `500+700` en la casilla del valor, para el embarque con dos facturas.

    Lo importante no es la suma: es que se guarda lo que se tecleo. Quien
    vuelva a ese renglon en octubre tiene que ver `500+700` y no un `1,200`
    huerfano del que ya nadie se acuerda de donde salio.
    """

    def test_suma_resta_y_multiplica(self):
        self.assertEqual(str(impuestos.resolver_expresion('500+700')), '1200.00')
        self.assertEqual(str(impuestos.resolver_expresion('100-25')), '75.00')
        self.assertEqual(str(impuestos.resolver_expresion('3*250.50')), '751.50')

    def test_las_comas_son_separador_de_miles(self):
        # Es como llegan escritos los valores: 162,300.00
        self.assertEqual(str(impuestos.resolver_expresion('162,300.00')),
                         '162300.00')

    def test_la_casilla_vacia_no_es_un_cero(self):
        # Un renglon sin factura ni proforma va vacio, no en cero: la columna
        # del impuesto se queda en blanco y el renglon pide el documento.
        self.assertIsNone(impuestos.resolver_expresion(''))
        self.assertIsNone(impuestos.resolver_expresion('   '))

    def test_un_dedazo_se_rechaza(self):
        for malo in ('abc', '500++', '1/0', '500+'):
            with self.assertRaises(impuestos.ExpresionInvalida, msg=malo):
                impuestos.resolver_expresion(malo)

    def test_no_se_puede_colar_codigo(self):
        # Aqui entra texto de un formulario. El filtro no deja pasar ni un
        # nombre, asi que no hay nada que llamar.
        for intento in ('__import__("os")', 'open("x")', '[].__class__'):
            with self.assertRaises(impuestos.ExpresionInvalida):
                impuestos.resolver_expresion(intento)

    def test_ni_colgar_la_maquina(self):
        # `9**9**9` cabe en una casilla y se come el proceso antes de devolver
        # nada. Aqui nadie eleva nada.
        with self.assertRaises(impuestos.ExpresionInvalida):
            impuestos.resolver_expresion('9**9**9')
        with self.assertRaises(impuestos.ExpresionInvalida):
            impuestos.resolver_expresion('1' * 70)


class LosSeisStatusTests(BaseDeAlmacen):
    """
    Ninguno se teclea: los seis salen de lo que ya paso en el sistema.

    Y los seis contestan la misma pregunta -- de quien es la pelota -- dicha
    con las palabras del papel que el cliente ya lee.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.tenant.short_name = 'DYSER'
        cls.tenant.save()
        cls.cliente.abbreviation = 'LBO'
        cls.cliente.save()

    def renglon(self, **extra):
        return RenglonDeImpuestos.objects.create(
            tenant=self.tenant, customer=self.cliente, **extra)

    def con_pedimento(self, estado):
        op = self.operacion('ED-%s' % estado, bundle_qty=1)
        ped = Pedimento.objects.create(tenant=self.tenant, customer=self.cliente,
                                       orden=1, estado=estado)
        PedimentoBundle.objects.create(pedimento=ped, operation=op, bultos=1)
        return self.renglon(operation=op)

    def test_sin_entrada_y_sin_fecha(self):
        r = self.renglon(po_order='MD07/26')
        self.assertEqual(r.status, RenglonDeImpuestos.NO_HA_LLEGADO)
        self.assertEqual(r.status_texto, 'NO HA LLEGADO')

    def test_con_guia_y_transportista_hay_fecha(self):
        transportista = Catalog.objects.create(category='CARRIER', name='XPO',
                                               tenant=self.tenant)
        r = self.renglon(po_order='MD07/26', carrier=transportista, guia='123',
                         eta=date(2026, 10, 9))
        self.assertEqual(r.status, RenglonDeImpuestos.ETA)
        self.assertEqual(r.status_texto, 'ETA 09/10')

    def test_llego_y_se_elabora_el_pedimento(self):
        # Es el hueco largo, y el momento en que mas falta hace reclamar la
        # factura. Sin este status esos dias se verian como "no ha llegado".
        r = self.renglon(operation=self.operacion('ED-1', bundle_qty=1))
        self.assertEqual(r.status, RenglonDeImpuestos.ELAB_PED)
        self.assertEqual(r.status_texto, 'ELAB PED · DYSER')

    def test_salio_a_revision(self):
        r = self.con_pedimento(Pedimento.EN_REVISION)
        self.assertEqual(r.status_texto, 'ENVIADO A REVISION Y MV · LBO')
        self.assertTrue(r.la_pelota_es_del_cliente)

    def test_el_cliente_aprobo(self):
        r = self.con_pedimento(Pedimento.APROBADO)
        self.assertEqual(r.status_texto, 'LISTO PARA VALIDACION Y PAGO · LBO')
        self.assertFalse(r.la_pelota_es_del_cliente)

    def test_validado_y_pagado(self):
        self.assertEqual(self.con_pedimento(Pedimento.PAGADO).status_texto,
                         'VALIDADOS Y PAGADOS')
        self.assertEqual(self.con_pedimento(Pedimento.VALIDADO).status_texto,
                         'VALIDADOS Y PAGADOS')

    def test_sin_abreviacion_el_status_va_solo(self):
        # El nombre completo recortado a doce letras da cosas como
        # "CUSTOMER TES", que en un papel que el cliente lee parece un error.
        self.cliente.abbreviation = ''
        self.cliente.save()
        r = self.con_pedimento(Pedimento.EN_REVISION)
        self.assertEqual(r.status_texto, 'ENVIADO A REVISION Y MV')


class LosTresEstadosDelValorTests(BaseDeAlmacen):
    """
    Sin nada, con proforma, con factura.

    El sistema no adivina cuando el valor deja de ser provisional: lo sabe por
    el archivo. Mientras el renglon no tenga colgada una factura comercial el
    valor es provisional venga de donde venga, y en el momento en que se carga
    el documento deja de serlo. Sin clic de confirmacion.
    """

    def setUp(self):
        self.op = self.operacion('ED-V1', bundle_qty=1)
        self.r = RenglonDeImpuestos.objects.create(
            tenant=self.tenant, customer=self.cliente, operation=self.op)

    def test_sin_nada(self):
        self.assertEqual(self.r.estado_del_valor, 'SIN_NADA')
        # La columna del impuesto va vacia: el renglon existe para pedir el
        # documento, no para dar una cifra.
        self.assertIsNone(self.r.impuesto_estimado)

    def test_con_proforma_el_valor_es_provisional(self):
        self.r.valor_mercancia = Decimal('28400')
        self.r.save()
        self.assertEqual(self.r.estado_del_valor, 'PROFORMA')
        self.assertTrue(self.r.valor_es_provisional)
        self.assertIsNotNone(self.r.impuesto_estimado)

    def test_al_cargar_la_factura_deja_de_ser_provisional(self):
        self.r.valor_mercancia = Decimal('28400')
        self.r.save()
        OperationDocument.objects.create(
            tenant=self.tenant, operation=self.op,
            file=SimpleUploadedFile('factura.pdf', b'x'),
            original_name='factura.pdf',
            ranura=OperationDocument.RANURA_FACTURA_COMERCIAL)
        self.assertEqual(self.r.estado_del_valor, 'FACTURA')
        self.assertFalse(self.r.valor_es_provisional)


class LaHojaEnPantallaTests(BaseDeAlmacen):
    """La pantalla de la hoja y lo que se puede hacer en ella."""

    def setUp(self):
        self.client.force_login(self.jefa)
        self.op = self.operacion('ED-H1', bundle_qty=1)
        self.r = RenglonDeImpuestos.objects.create(
            tenant=self.tenant, customer=self.cliente, operation=self.op)

    def hoja(self):
        return self.client.get('/impuestos/', {'customer': self.cliente.pk})

    def guardar(self, **datos):
        return self.client.post('/impuestos/%d/save/' % self.r.pk, datos)

    def test_sin_cliente_no_se_pinta_ninguna_hoja(self):
        respuesta = self.client.get('/impuestos/')
        self.assertEqual(respuesta.status_code, 200)
        self.assertIsNone(respuesta.context['cliente'])

    def test_el_ejemplo_del_excel_de_punta_a_punta(self):
        # La misma cifra del archivo, pero pasando por el formulario.
        self.guardar(valor='10395.00', incrementables='200.00', tasa_igi='0')
        self.r.refresh_from_db()
        self.assertEqual(str(self.r.impuesto_estimado), '34376.91')

    def test_se_guarda_la_cuenta_y_no_solo_el_total(self):
        self.guardar(valor='6000+4395')
        self.r.refresh_from_db()
        self.assertEqual(self.r.valor_expresion, '6000+4395')
        self.assertEqual(str(self.r.valor_mercancia), '10395.00')

    def test_un_dedazo_no_borra_el_valor_que_habia(self):
        self.guardar(valor='10395.00')
        self.guardar(valor='500++')
        self.r.refresh_from_db()
        self.assertEqual(str(self.r.valor_mercancia), '10395.00')

    def test_al_corregir_el_valor_se_guarda_el_anterior(self):
        # En tres meses eso dice cuanto se despegan las proformas de cada
        # proveedor de la factura de verdad.
        self.guardar(valor='28400')
        self.guardar(valor='29150')
        self.r.refresh_from_db()
        self.assertEqual(str(self.r.valor_anterior), '28400.00')
        self.assertEqual(str(self.r.valor_mercancia), '29150.00')

    def test_el_redondeo_es_de_la_casa(self):
        # El sistema calcula y propone; nunca decide ni redondea por su cuenta.
        self.guardar(valor='10395.00', incrementables='200.00',
                     impuesto_reportado='35000')
        self.r.refresh_from_db()
        self.assertEqual(str(self.r.impuesto_estimado), '35000.00')
        # Y la cifra exacta sigue disponible, para ver de cuanto fue el salto.
        self.assertEqual(str(self.r.calcular()['T_total_pedimento']), '34376.91')

    def test_el_total_de_la_hoja_suma_los_renglones(self):
        self.guardar(valor='10395.00', incrementables='200.00')
        self.r.refresh_from_db()
        otra = self.operacion('ED-H2', bundle_qty=1)
        segundo = RenglonDeImpuestos.objects.create(
            tenant=self.tenant, customer=self.cliente, operation=otra,
            valor_mercancia=Decimal('1000'))
        respuesta = self.hoja()
        esperado = (self.r.impuesto_estimado + segundo.impuesto_estimado)
        self.assertEqual(respuesta.context['resumen']['total'], esperado)

    def test_un_renglon_de_otra_empresa_no_existe(self):
        otro = Tenant.objects.create(name='Bodegas del Sur', type='organization',
                                     subdomain='sur')
        ajeno_cliente = Catalog.objects.create(category='CUSTOMER', name='Zeta',
                                               tenant=otro)
        ajeno = RenglonDeImpuestos.objects.create(tenant=otro,
                                                  customer=ajeno_cliente)
        respuesta = self.client.post('/impuestos/%d/save/' % ajeno.pk,
                                     {'valor': '999'})
        self.assertEqual(respuesta.status_code, 404)


class ElRenglonEntraSoloTests(BaseDeAlmacen):
    """
    Todo nace del embarque: se captura la entrada y entra sola a la hoja.

    Nadie tiene que acordarse de dar de alta el renglon, que es justo lo que
    hoy se hace a mano en el Excel.
    """

    def setUp(self):
        self.client.force_login(self.jefa)
        self.shipper = Catalog.objects.create(category='SHIPPER', name='ABC LLC',
                                              tenant=self.tenant)
        self.carrier = Catalog.objects.create(category='CARRIER', name='XPO',
                                              tenant=self.tenant)
        self.bundle = Catalog.objects.create(category='BUNDLE_TYPE',
                                             name='Pallet', tenant=self.tenant)

    def capturar(self, **extra):
        datos = {'date': '2026-09-07', 'operation_type': 'ENTRY',
                 'customer_id': self.cliente.pk, 'shipper_id': self.shipper.pk,
                 'carrier_id': self.carrier.pk, 'bundle_type_id': self.bundle.pk,
                 'bundle_qty': '2', 'weight_lbs': '100', 'description': 'Cajas'}
        datos.update(extra)
        return self.client.post('/operations/create/', datos)

    def test_capturar_una_entrada_crea_su_renglon(self):
        self.capturar(po_order='MD07/26')
        op = WarehouseOperation.objects.latest('id')
        self.assertTrue(RenglonDeImpuestos.objects.filter(operation=op).exists())

    def test_una_salida_no_entra_en_la_hoja(self):
        # Una salida no se le reporta al cliente como impuesto por pagar.
        antes = RenglonDeImpuestos.objects.count()
        self.capturar(operation_type='EXIT')
        self.assertEqual(RenglonDeImpuestos.objects.count(), antes)

    def test_la_factura_que_se_adelanta_se_enlaza_al_llegar_la_mercancia(self):
        # Se crea el renglon suelto con el pedido, y al capturar la entrada con
        # ese mismo pedido se enlaza en vez de crear otro: partir en dos la
        # historia del mismo embarque es peor que no tenerla.
        suelto = RenglonDeImpuestos.objects.create(
            tenant=self.tenant, customer=self.cliente, po_order='MF15/26',
            valor_mercancia=Decimal('28400'))
        self.capturar(po_order='MF15/26')
        op = WarehouseOperation.objects.latest('id')
        suelto.refresh_from_db()
        self.assertEqual(suelto.operation_id, op.pk)
        self.assertEqual(RenglonDeImpuestos.objects.filter(
            customer=self.cliente).count(), 1)
        # Y el valor que ya se habia estimado no se pierde.
        self.assertEqual(str(suelto.valor_mercancia), '28400.00')

    def test_un_pedido_distinto_no_se_enlaza_por_error(self):
        RenglonDeImpuestos.objects.create(
            tenant=self.tenant, customer=self.cliente, po_order='MF15/26')
        self.capturar(po_order='OTRO/26')
        self.assertEqual(RenglonDeImpuestos.objects.filter(
            customer=self.cliente).count(), 2)


class LosParametrosDelDiaTests(BaseDeAlmacen):
    """
    Un estimado dado en marzo con la cuota de marzo la sigue enseñando en agosto.

    Sin esto, cambiar la cuota de prevalidacion una vez al ano reescribiria
    hacia atras todos los estimados ya dados a los clientes.
    """

    def test_se_usan_los_parametros_del_dia_del_embarque(self):
        ParametrosDeImpuestos.objects.create(
            tenant=self.tenant, vigente_desde=date(2026, 1, 1),
            prevalidacion=Decimal('300'))
        ParametrosDeImpuestos.objects.create(
            tenant=self.tenant, vigente_desde=date(2026, 6, 1),
            prevalidacion=Decimal('450'))

        de_marzo = RenglonDeImpuestos.objects.create(
            tenant=self.tenant, customer=self.cliente,
            operation=self.operacion('ED-M', date=date(2026, 3, 15), bundle_qty=1),
            valor_mercancia=Decimal('1000'))
        de_agosto = RenglonDeImpuestos.objects.create(
            tenant=self.tenant, customer=self.cliente,
            operation=self.operacion('ED-A', date=date(2026, 8, 15), bundle_qty=1),
            valor_mercancia=Decimal('1000'))

        self.assertEqual(de_marzo.calcular()['S_prevalidacion'], Decimal('300.00'))
        self.assertEqual(de_agosto.calcular()['S_prevalidacion'], Decimal('450.00'))
