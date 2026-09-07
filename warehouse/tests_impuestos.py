"""
La estimacion de impuestos, contra el Excel del que salio.

La prueba que manda es la primera: el ejemplo que trae dentro el archivo
`CALCULO IMPUESTOS123.xls`, comprobado cifra a cifra. Si esa falla, el calculo
esta mal aunque todo lo demas pase, porque es el unico numero del que se sabe
con certeza cual es el resultado bueno.

Las otras cubren las tres cosas que no son evidentes: que el T-MEC descuenta el
derecho de tramite y no el IGI, que el factor de ajuste no es un paso del
calculo sino una herramienta para repartir el valor en aduana entre facturas, y
que un embarque sin valor todavia -- el caso normal el dia que llega la
mercancia sin proforma -- sale en ceros en vez de reventar.
"""
from decimal import Decimal

from django.test import SimpleTestCase

from . import impuestos


class ElEjemploDelExcelTests(SimpleTestCase):
    """
    El ejemplo real, hasta el centimo.

    Valor 10,395.00 USD, sin fletes, 200.00 de incrementables, tipo de cambio
    19.0000, sin T-MEC, IGI al 0 %, prevalidacion 300.00.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.r = impuestos.calcular(
            valor_mercancia='10395.00', fletes=0, incrementables='200.00',
            tipo_de_cambio='19.0000', aplica_tmec=False, tasa_igi=0,
            prevalidacion='300.00')

    def test_las_once_cifras_que_salen_solas(self):
        esperado = {
            'E_suma_global':     '10595.00',
            'G_factor_ajuste':   '1.01924002',
            'H_precio_pagado':   '197505.00',
            'I_valor_en_aduana': '201305.00',
            'K_dta':             '1610.44',
            'M_igi':             '0.00',
            'N_dta_mas_igi':     '1610.44',
            'O_base_del_iva':    '202915.44',
            'Q_iva':             '32466.47',
            'R_total_impuestos': '34076.91',
            'T_total_pedimento': '34376.91',
        }
        for clave, valor in esperado.items():
            self.assertEqual(str(self.r[clave]), valor, clave)

    def test_el_valor_en_aduana_se_puede_llegar_por_los_dos_caminos(self):
        # E x F y H x G dan el mismo numero. Por eso el factor de ajuste no es
        # un paso del calculo: es una comprobacion que sale gratis.
        por_un_lado = self.r['E_suma_global'] * self.r['F_tipo_de_cambio']
        por_el_otro = self.r['H_precio_pagado'] * self.r['G_factor_ajuste']
        self.assertEqual(round(por_un_lado, 2), self.r['I_valor_en_aduana'])
        self.assertEqual(round(por_el_otro, 2), self.r['I_valor_en_aduana'])


class ElTmecTests(SimpleTestCase):
    """El T-MEC descuenta el derecho de tramite, no el IGI."""

    def calcular(self, tmec, igi=0):
        return impuestos.calcular(
            valor_mercancia='10395.00', incrementables='200.00',
            tipo_de_cambio='19.0000', aplica_tmec=tmec, tasa_igi=igi,
            prevalidacion='300.00')

    def test_con_tmec_el_dta_es_cero(self):
        self.assertEqual(str(self.calcular(True)['K_dta']), '0.00')

    def test_sin_tmec_el_dta_es_ocho_al_millar(self):
        r = self.calcular(False)
        self.assertEqual(r['tasa_dta_aplicada'], Decimal('0.008'))
        self.assertEqual(str(r['K_dta']), '1610.44')

    def test_el_tmec_no_toca_el_igi(self):
        # La tasa del IGI se teclea aparte, segun la fraccion arancelaria: el
        # T-MEC no la cambia. Es el error que habia que quitarse de encima.
        con = self.calcular(True, igi='0.15')
        sin = self.calcular(False, igi='0.15')
        self.assertEqual(con['M_igi'], sin['M_igi'])
        self.assertNotEqual(con['K_dta'], sin['K_dta'])

    def test_con_tmec_el_total_baja_justo_el_dta_y_su_iva(self):
        con = self.calcular(True)
        sin = self.calcular(False)
        diferencia = sin['R_total_impuestos'] - con['R_total_impuestos']
        # El DTA que se deja de pagar, mas el IVA que ese DTA arrastraba.
        esperado = sin['K_dta'] * (1 + impuestos.TASA_DE_IVA)
        self.assertEqual(diferencia, round(esperado, 2))


class SinValorTodaviaTests(SimpleTestCase):
    """
    El renglon del dia que llega la mercancia sin proforma.

    Existe para avisar de la llegada y pedir la factura, no para dar una cifra.
    Asi que sale en ceros y no revienta dividiendo entre cero al sacar el
    factor de ajuste.
    """

    def test_todo_en_ceros(self):
        r = impuestos.calcular(valor_mercancia=0, tipo_de_cambio='19.0000')
        self.assertEqual(str(r['I_valor_en_aduana']), '0.00')
        self.assertEqual(str(r['R_total_impuestos']), '0.00')
        # La prevalidacion se cobra igual, asi que el total del pedimento no es
        # cero: es la cuota.
        self.assertEqual(str(r['T_total_pedimento']), '300.00')

    def test_el_factor_de_ajuste_es_uno_y_no_una_division_entre_cero(self):
        r = impuestos.calcular(valor_mercancia=0, tipo_de_cambio='19.0000')
        self.assertEqual(r['G_factor_ajuste'], Decimal('1.00000000'))

    def test_una_casilla_vacia_se_lee_como_cero(self):
        # Los formularios mandan cadenas vacias, no ceros.
        r = impuestos.calcular(valor_mercancia='10395.00', fletes='',
                               incrementables=None, tipo_de_cambio='19.0000')
        self.assertEqual(str(r['E_suma_global']), '10395.00')


class RepartoEntreFacturasTests(SimpleTestCase):
    """
    Para lo que sirve de verdad el factor de ajuste.

    Cuando en una partida del pedimento va mercancia de varias facturas, el
    valor en aduana de cada una es su valor por el factor por el tipo de
    cambio. Hoy eso se saca en un Excel nuevo desde cero, y equivocarse ahi
    sale caro.
    """

    def test_el_reparto_cuadra_contra_el_valor_en_aduana_del_pedimento(self):
        r = impuestos.calcular(valor_mercancia='10395.00',
                               incrementables='200.00',
                               tipo_de_cambio='19.0000')
        # Dos facturas que suman el valor de mercancia del pedimento.
        renglones, total = impuestos.reparto_por_factura(
            ['6000.00', '4395.00'], r['G_factor_ajuste'], r['F_tipo_de_cambio'])
        self.assertEqual(len(renglones), 2)
        # El total del reparto es el valor en aduana, salvo el centimo que se
        # pueda perder redondeando cada renglon.
        self.assertLessEqual(abs(total - r['I_valor_en_aduana']),
                             Decimal('0.02'))

    def test_una_sola_factura_da_el_valor_en_aduana_entero(self):
        r = impuestos.calcular(valor_mercancia='10395.00',
                               incrementables='200.00',
                               tipo_de_cambio='19.0000')
        _renglones, total = impuestos.reparto_por_factura(
            ['10395.00'], r['G_factor_ajuste'], r['F_tipo_de_cambio'])
        self.assertLessEqual(abs(total - r['I_valor_en_aduana']),
                             Decimal('0.02'))


class LosCuatroNumerosDelTenantTests(SimpleTestCase):
    """
    Los que no salen de ninguna formula: los pone Diego y cambian con el tiempo.

    Se pasan como parametros y no estan clavados en el codigo, para que un
    calculo del ano pasado pueda volver a hacerse con la cuota de entonces.
    """

    def test_la_prevalidacion_se_puede_cambiar(self):
        r = impuestos.calcular(valor_mercancia='10395.00',
                               tipo_de_cambio='19.0000', prevalidacion='450')
        self.assertEqual(r['T_total_pedimento'] - r['R_total_impuestos'],
                         Decimal('450.00'))

    def test_la_tasa_del_dta_se_puede_cambiar(self):
        r = impuestos.calcular(valor_mercancia='10395.00',
                               tipo_de_cambio='19.0000',
                               dta_sin_tmec='0.01')
        self.assertEqual(r['tasa_dta_aplicada'], Decimal('0.01'))

    def test_el_dta_con_tmec_podria_dejar_de_ser_cero(self):
        # Hoy es cero. Si algun ano pasa a ser una cuota, se cambia en los
        # ajustes del tenant y no hay que tocar el codigo.
        r = impuestos.calcular(valor_mercancia='10395.00',
                               tipo_de_cambio='19.0000', aplica_tmec=True,
                               dta_con_tmec='0.004')
        self.assertEqual(r['tasa_dta_aplicada'], Decimal('0.004'))
        self.assertNotEqual(str(r['K_dta']), '0.00')
