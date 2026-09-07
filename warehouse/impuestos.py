"""
La estimacion de impuestos, leida del Excel de Diego.

No es una aproximacion ni una reconstruccion: son las formulas del archivo
`CALCULO IMPUESTOS123.xls`, comprobadas numero a numero contra el ejemplo que
trae dentro. Las letras de cada funcion son las columnas de esa hoja, para que
quien abra el Excel y quien lea esto esten mirando lo mismo.

    Se teclea                          Sale solo
    ---------------------------------  ------------------------------------
    B  valor de mercancia (USD)        E  suma global    = B + C + D
    C  fletes y embalajes (USD)        G  factor ajuste  = E / B
    D  incrementables (USD)            H  precio pagado  = B * F
    F  tipo de cambio                  I  valor en aduana= E * F
    J  aplica T-MEC (si/no)            K  DTA            = I * tasa
    L  tasa de IGI                     M  IGI            = I * L
    S  prevalidacion (MXN)             N  DTA + IGI      = K + M
                                       O  base del IVA   = I + N
                                       Q  IVA            = O * 0.16
                                       R  total impuestos= N + Q
                                       T  total pedimento= R + S

Tres cosas que no son evidentes y que valen mas que las formulas:

1. **El T-MEC descuenta el derecho de tramite, no el IGI.** Se palomea solo
   cuando la mercancia es de origen Mexico, Estados Unidos o Canada, y lo que
   cambia es el DTA -- que pasa a ser cero. La tasa del IGI se sigue tecleando
   aparte, segun la fraccion arancelaria.

2. **El factor de ajuste no es un paso del calculo.** `E * F` y `H * G` dan el
   mismo numero, asi que como paso sobra. Sirve para otra cosa: repartir el
   valor en aduana entre las facturas de una misma partida del pedimento. Por
   eso se devuelve, y por eso se enseña solo del lado del tenant.

3. **El sistema calcula y propone, nunca decide.** El redondeo es de Diego:
   siempre hacia arriba y a numero cerrado, a su criterio, porque depende de
   cosas que el sistema no sabe. Aqui se devuelve la cifra exacta; quien la
   mande puede subirla, y la pantalla enseña la exacta al lado para que se vea
   de cuanto es el salto.

Los cuatro numeros que no salen de ninguna formula -- el tipo de cambio de
trabajo, la prevalidacion, y las dos tasas del DTA -- son parametros del
tenant, con la fecha desde la que valen, y no viven aqui.
"""
from decimal import Decimal, ROUND_HALF_UP

# El IVA. Va como constante y no como parametro porque no es una decision de
# nadie de la casa: es la tasa general, y el dia que cambie cambia para todos.
TASA_DE_IVA = Decimal('0.16')

# Los valores con los que nace un tenant que no ha tocado sus ajustes. El del
# tipo de cambio es deliberadamente alto -- va alto a proposito, para no
# quedarse corto -- y no sale nunca del DOF.
DTA_SIN_TMEC_POR_OMISION = Decimal('0.008')   # 8 al millar sobre el valor en aduana
DTA_CON_TMEC_POR_OMISION = Decimal('0')       # con T-MEC no hay derecho de tramite
PREVALIDACION_POR_OMISION = Decimal('300')    # lo cobra el prevalidador, cambia cada ano

CENTAVO = Decimal('0.01')


def _dec(valor, por_omision=Decimal('0')):
    """Un `Decimal` de lo que venga, o `por_omision` si no se puede."""
    if valor is None or valor == '':
        return por_omision
    if isinstance(valor, Decimal):
        return valor
    try:
        return Decimal(str(valor))
    except Exception:
        return por_omision


def _centavos(valor):
    """Redondeado a dos decimales, como cualquier importe."""
    return valor.quantize(CENTAVO, rounding=ROUND_HALF_UP)


def calcular(valor_mercancia, fletes=0, incrementables=0, tipo_de_cambio=0,
             aplica_tmec=False, tasa_igi=0,
             prevalidacion=PREVALIDACION_POR_OMISION,
             dta_sin_tmec=DTA_SIN_TMEC_POR_OMISION,
             dta_con_tmec=DTA_CON_TMEC_POR_OMISION):
    """
    La hoja entera, de los cuatro numeros que se teclean a los once que salen.

    Los tres primeros y la prevalidacion van en las unidades del Excel: los
    valores de mercancia, fletes e incrementables en dolares, la prevalidacion
    en pesos. `tasa_igi` es una fraccion, no un porcentaje: 0.15 y no 15.

    Devuelve un diccionario con la letra de cada columna del Excel, para que
    una cifra que no cuadre se pueda perseguir hasta su celda.

    Con el valor de mercancia en cero -- que es el caso normal el dia que llega
    la mercancia sin proforma -- todo sale en ceros y el factor de ajuste en
    uno, en vez de reventar dividiendo. Ese renglon existe para avisar de la
    llegada y pedir la factura, no para dar una cifra.
    """
    B = _dec(valor_mercancia)
    C = _dec(fletes)
    D = _dec(incrementables)
    F = _dec(tipo_de_cambio)
    L = _dec(tasa_igi)
    S = _dec(prevalidacion, PREVALIDACION_POR_OMISION)

    E = B + C + D                                  # suma global USD
    G = (E / B) if B else Decimal('1')             # factor de ajuste
    H = B * F                                      # precio pagado MXN
    I = E * F                                      # valor en aduana MXN

    tasa_dta = _dec(dta_con_tmec, DTA_CON_TMEC_POR_OMISION) if aplica_tmec \
        else _dec(dta_sin_tmec, DTA_SIN_TMEC_POR_OMISION)
    K = I * tasa_dta                               # DTA
    M = I * L                                      # IGI
    N = K + M                                      # DTA + IGI
    O = I + N                                      # base del IVA
    Q = O * TASA_DE_IVA                            # IVA
    R = N + Q                                      # total de impuestos
    T = R + S                                      # total del pedimento

    return {
        # Lo que se teclea, devuelto tal cual para que la pantalla pueda
        # enseñar de que numeros salio el resultado.
        'B_valor_mercancia': _centavos(B),
        'C_fletes':          _centavos(C),
        'D_incrementables':  _centavos(D),
        'F_tipo_de_cambio':  F,
        'J_aplica_tmec':     bool(aplica_tmec),
        'L_tasa_igi':        L,
        'S_prevalidacion':   _centavos(S),
        # Lo que sale solo.
        'E_suma_global':     _centavos(E),
        # Ocho decimales y no dos: es un factor, no un importe, y con dos
        # decimales el reparto entre facturas de una partida deja de cuadrar.
        'G_factor_ajuste':   G.quantize(Decimal('0.00000001'), rounding=ROUND_HALF_UP),
        'H_precio_pagado':   _centavos(H),
        'I_valor_en_aduana': _centavos(I),
        'K_dta':             _centavos(K),
        'M_igi':             _centavos(M),
        'N_dta_mas_igi':     _centavos(N),
        'O_base_del_iva':    _centavos(O),
        'Q_iva':             _centavos(Q),
        'R_total_impuestos': _centavos(R),
        'T_total_pedimento': _centavos(T),
        # La tasa que se acabo aplicando al DTA, para que se vea por que el
        # derecho de tramite salio en cero cuando salio en cero.
        'tasa_dta_aplicada': tasa_dta,
    }


def reparto_por_factura(valores_de_facturas, factor_ajuste, tipo_de_cambio):
    """
    El valor en aduana que le toca a cada factura de una misma partida.

    Es para lo que sirve de verdad el factor de ajuste. Cuando en una partida
    del pedimento va mercancia de dos o tres facturas, el valor en aduana de
    cada una es su valor por el factor por el tipo de cambio, y eso es lo que
    va al identificador PO, primer complemento.

    Con una sola factura no hace falta, porque el sistema del agente ya lo trae
    hecho; con varias a veces falla y hoy se saca en un Excel nuevo desde cero.
    Aqui el factor y el tipo de cambio no se teclean: salen del pedimento.

    Devuelve `(renglones, total)`, donde cada renglon es
    `(valor_de_la_factura, valor_en_aduana)`.
    """
    G = _dec(factor_ajuste, Decimal('1'))
    F = _dec(tipo_de_cambio)
    renglones = []
    total = Decimal('0')
    for valor in valores_de_facturas:
        v = _dec(valor)
        en_aduana = _centavos(v * G * F)
        renglones.append((_centavos(v), en_aduana))
        total += en_aduana
    return renglones, _centavos(total)


# ── La casilla que suma, como en Excel ───────────────────────────────────────
#
# Un embarque puede traer dos o tres facturas comerciales, y hoy eso se suma
# con la calculadora antes de teclear el total. La casilla del valor acepta la
# suma escrita -- `500+700` -- y la resuelve ahi mismo.
#
# Lo que importa no es la suma: es que se guarda **lo que se tecleo** y no solo
# el resultado. Quien vuelva a ese renglon en octubre ve `500+700` y no un
# `1,200` huerfano del que ya nadie se acuerda de donde salio.

import re as _re

# Lo unico que se acepta: digitos, punto, coma de miles y los cuatro
# operadores, mas parentesis. No es una restriccion de estilo -- es que aqui
# entra texto de un formulario, y evaluar texto de un formulario sin acotarlo
# es abrir una puerta que no hace falta abrir.
_EXPRESION_VALIDA = _re.compile(r'^[0-9+\-*/().,\s]+$')


class ExpresionInvalida(ValueError):
    """Lo tecleado en la casilla del valor no es una cuenta."""


def resolver_expresion(texto):
    """
    El numero que sale de lo tecleado en la casilla del valor.

    Acepta un numero suelto (`1200`, `1,200.00`) y una cuenta con sumas,
    restas, multiplicaciones y divisiones (`500+700`, `3*250.50`). Devuelve un
    `Decimal`, o `None` si la casilla esta vacia -- que es el caso normal
    mientras no hay ni factura ni proforma.

    Levanta `ExpresionInvalida` cuando lo tecleado no es una cuenta. La casilla
    avisa mientras se escribe, pero el aviso de la pantalla no es un candado:
    esto es lo que impide que un dedazo acabe guardado como valor de una
    factura.
    """
    texto = (texto or '').strip()
    if not texto:
        return None
    if not _EXPRESION_VALIDA.match(texto):
        raise ExpresionInvalida(texto)
    # El asterisco doble es la potencia, y `9**9**9` cabe en una casilla y se
    # come la maquina antes de devolver nada. Aqui nadie eleva nada: una casilla
    # de valor de factura suma, resta y multiplica.
    if '**' in texto.replace(' ', ''):
        raise ExpresionInvalida(texto)
    # Y un largo razonable, por la misma razon: lo que se teclea de verdad son
    # dos o tres numeros sumados.
    if len(texto) > 60:
        raise ExpresionInvalida(texto)

    # Las comas son separador de miles, no decimal: es como llegan los valores
    # escritos, `162,300.00`. Se quitan antes de evaluar.
    limpio = texto.replace(',', '').replace(' ', '')
    if not limpio:
        return None
    try:
        # `eval` acotado: sin nombres, sin builtins y sobre un texto que ya
        # paso el filtro de arriba. Los numeros se convierten a Decimal para no
        # arrastrar el error del punto flotante en cifras de dinero.
        limpio_decimal = _re.sub(r'(\d+\.?\d*)', r"Decimal('\1')", limpio)
        valor = eval(limpio_decimal, {'__builtins__': {}, 'Decimal': Decimal}, {})
    except Exception as e:
        raise ExpresionInvalida(texto) from e
    if not isinstance(valor, Decimal):
        valor = _dec(valor)
    return _centavos(valor)
