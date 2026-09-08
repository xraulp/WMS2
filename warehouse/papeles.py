"""
Los tres papeles que salen de una tarea de cruce.

    Lista de preparacion   sale desde el minuto uno · no ampara nada
    Orden de carga         solo con los pedimentos pagados · es la que ampara
    Remision               solo si el escaneo cuadra · se la lleva el transfer

Los tres se leen **de pie**, en un anden, sujetos con una mano. Por eso van en
vertical: lo apaisado cabe mas columnas y no se lee. La forma de que quepa la
informacion en vertical no es encoger la letra, es dar dos renglones a cada
operacion -- arriba, en tamano legible, lo que se compara contra el bulto que
se esta subiendo; abajo, en gris y pequeno, lo que solo se consulta cuando hay
una duda.

Tres decisiones que no son de maquetacion aunque lo parezcan:

1. **El recuadro del QR de la orden es deliberadamente feo y grande.** Esta
   compitiendo por la atencion de alguien que lleva veinte anos surtiendo sin
   leer papeles, y perder esa competencia significa que el candado no sirve.

2. **La lista de preparacion lleva una banda que dice que no es documento de
   embarque**, y donde la orden pone su version ella pone "puede haber
   cambiado". Es un papel que nunca pretende ser la verdad, y tiene que
   decirlo el mismo -- porque en la bodega los dos papeles se parecen.

3. **La remision con diferencia lo dice en rojo y del mismo tamano**, en el
   papel que lleva el chofer y no escondido en una pantalla. Quien recibe la
   mercancia en la frontera tiene que poder ver que este embarque salio
   sabiendo que no cuadraba: si la excepcion no se ve, deja de ser una
   excepcion.
"""
from io import BytesIO

from django.utils import timezone
from django.utils.translation import gettext as _

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph,
                                SimpleDocTemplate, Spacer, Table, TableStyle)

from .utils import generate_qr_code, logo_de, tenant_public_url

# ── Paleta y medidas ────────────────────────────────────────────────────────
TINTA      = colors.HexColor('#0f172a')
GRIS       = colors.HexColor('#64748b')
GRIS_CLARO = colors.HexColor('#e2e8f0')
FONDO      = colors.HexColor('#f1f5f9')
ROJO       = colors.HexColor('#b91c1c')
ROJO_SUAVE = colors.HexColor('#fee2e2')

MARGEN = 0.5 * inch
ANCHO  = 8.5 * inch - 2 * MARGEN


def _estilo(nombre, **kw):
    return ParagraphStyle(nombre, parent=getSampleStyleSheet()['Normal'], **kw)


TITULO   = _estilo('t',  fontName='Helvetica-Bold', fontSize=17, textColor=TINTA)
SUBTITULO= _estilo('s',  fontName='Helvetica', fontSize=8,  textColor=GRIS)
NUMERO   = _estilo('n',  fontName='Helvetica-Bold', fontSize=15, textColor=TINTA,
                   alignment=TA_RIGHT)
DATO     = _estilo('d',  fontName='Helvetica', fontSize=9,  textColor=TINTA)
DATO_R   = _estilo('dr', fontName='Helvetica', fontSize=9,  textColor=TINTA,
                   alignment=TA_RIGHT)
ETIQUETA = _estilo('e',  fontName='Helvetica-Bold', fontSize=7, textColor=GRIS)
CELDA    = _estilo('c',  fontName='Helvetica', fontSize=9,  textColor=TINTA)
CELDA_B  = _estilo('cb', fontName='Helvetica-Bold', fontSize=9, textColor=TINTA)
CELDA_R  = _estilo('cr', fontName='Helvetica', fontSize=9,  textColor=TINTA,
                   alignment=TA_RIGHT)
# El segundo renglon de cada operacion: lo que solo se consulta cuando hay una
# duda. Va en gris y pequeno a proposito, para que no compita con lo de arriba.
SEGUNDO  = _estilo('g',  fontName='Helvetica', fontSize=7,  textColor=GRIS,
                   leading=9)
PIE      = _estilo('p',  fontName='Helvetica', fontSize=6.5, textColor=GRIS)
CABECERA = _estilo('h',  fontName='Helvetica-Bold', fontSize=7, textColor=colors.white)


def _documento(buffer, titulo):
    return SimpleDocTemplate(
        buffer, pagesize=letter, title=titulo,
        topMargin=MARGEN, bottomMargin=MARGEN,
        leftMargin=MARGEN, rightMargin=MARGEN)


def _cabecera(tenant, titulo, numero, lineas_derecha):
    """El logo y el titulo a la izquierda, el numero y sus datos a la derecha."""
    logo = logo_de(tenant, ancho=1.1 * inch, alto=0.55 * inch)
    izquierda = [
        [logo] if logo is not None else
        [Paragraph(f'<b>{(tenant.name if tenant else "WMS").upper()}</b>',
                   _estilo('lg', fontName='Helvetica-Bold', fontSize=13,
                           textColor=GRIS))],
        [Paragraph(titulo, TITULO)],
        [Paragraph((tenant.name if tenant else 'WMS').upper(), SUBTITULO)],
    ]
    derecha = [[Paragraph(numero, NUMERO)]]
    for linea in lineas_derecha:
        derecha.append([Paragraph(linea, DATO_R)])

    tabla_izq = Table(izquierda, colWidths=[ANCHO * 0.6])
    tabla_der = Table(derecha, colWidths=[ANCHO * 0.4])
    for t in (tabla_izq, tabla_der):
        t.setStyle(TableStyle([
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 1),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
    fuera = Table([[tabla_izq, tabla_der]],
                  colWidths=[ANCHO * 0.6, ANCHO * 0.4])
    fuera.setStyle(TableStyle([
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LINEBELOW', (0, 0), (-1, -1), 1.2, TINTA),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    return fuera


def _ficha(pares, columnas=2):
    """Un bloque de `etiqueta: valor`, en dos columnas."""
    filas, fila = [], []
    for etiqueta, valor in pares:
        celda = Table(
            [[Paragraph(str(etiqueta).upper(), ETIQUETA)],
             [Paragraph(str(valor or '—'), DATO)]],
            colWidths=[ANCHO / columnas - 6])
        celda.setStyle(TableStyle([
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        fila.append(celda)
        if len(fila) == columnas:
            filas.append(fila)
            fila = []
    if fila:
        while len(fila) < columnas:
            fila.append('')
        filas.append(fila)

    tabla = Table(filas, colWidths=[ANCHO / columnas] * columnas)
    tabla.setStyle(TableStyle([
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    return tabla


def _estilo_de_tabla(anchos, filas_segundas):
    """El estilo comun de las tablas de dos renglones por operacion."""
    estilo = [
        ('BACKGROUND',    (0, 0), (-1, 0), TINTA),
        ('TEXTCOLOR',     (0, 0), (-1, 0), colors.white),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING',   (0, 0), (-1, -1), 5),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 5),
        ('TOPPADDING',    (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('GRID',          (0, 0), (-1, -1), 0.4, GRIS_CLARO),
    ]
    # El segundo renglon de cada operacion se funde con el primero: son la
    # misma cosa vista con dos niveles de detalle, no dos renglones.
    for fila in filas_segundas:
        estilo += [
            ('SPAN',          (1, fila), (-1, fila)),
            ('TOPPADDING',    (0, fila), (-1, fila), 0),
            ('BOTTOMPADDING', (0, fila), (-1, fila), 5),
            ('LINEABOVE',     (0, fila), (-1, fila), 0, colors.white),
            ('BACKGROUND',    (0, fila), (-1, fila), FONDO),
        ]
    return TableStyle(estilo)


def _bultos_de(renglon):
    """`6` o `3 de 5` -- asi se ve en el papel el caso de los 19 de 20."""
    total = renglon.operation.bundle_qty or 0
    if total and renglon.bultos < total:
        return _('%(van)d of %(total)d') % {'van': renglon.bultos, 'total': total}
    return str(renglon.bultos)


def _bultos_de_la_tarea(renglon):
    """`6` o `3 de 5`, para el renglon de una tarea de cruce."""
    total = renglon.operation.bundle_qty or 0
    van = renglon.bultos_que_van
    if total and van < total:
        return _('%(van)d of %(total)d') % {'van': van, 'total': total}
    return str(van or total or '—')


def _sobran(renglon):
    """Los bultos que se quedan en bodega, si es un embarque partido."""
    total = renglon.operation.bundle_qty or 0
    return max(total - renglon.bultos, 0)


# ═══════════════════════════════════════════════════════════════════════════
#  LA LISTA DE PREPARACION
# ═══════════════════════════════════════════════════════════════════════════

def lista_de_preparacion(task, usuario=None):
    """
    El papel con el que bodega baja y arma el embarque.

    Sale desde el minuto uno, que es lo que hoy se hace con la orden de carga
    adelantada. Tres diferencias con la orden, y las tres importan: va ordenada
    por **ubicacion** -- que manda a quien surte por un recorrido y no en
    zigzag --, no lleva totales aduanales ni firmas, y lleva una banda que no
    deja lugar a dudas.

    Donde la orden pone su version, esta pone "puede haber cambiado": es un
    papel que nunca pretende ser la verdad, y tiene que decirlo el mismo.
    """
    buffer = BytesIO()
    doc = _documento(buffer, f'Lista de preparacion {task.custom_id}')
    tenant = task.tenant
    historia = []

    # La banda. Es lo primero que se ve y lo unico que hace falta leer para
    # saber que este papel no ampara nada.
    banda = Table([[Paragraph(
        _('PICKING — NOT A SHIPPING DOCUMENT — DO NOT HAND IT TO THE TRANSFER'),
        _estilo('banda', fontName='Helvetica-Bold', fontSize=10,
                textColor=colors.white, alignment=TA_CENTER))]],
        colWidths=[ANCHO])
    banda.setStyle(TableStyle([
        ('BACKGROUND',    (0, 0), (-1, -1), ROJO),
        ('TOPPADDING',    (0, 0), (-1, -1), 7),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
    ]))
    historia += [banda, Spacer(1, 10)]

    ahora = timezone.localtime()
    historia.append(_cabecera(
        tenant, _('PICKING LIST'), task.custom_id, [
            _('printed %(cuando)s') % {'cuando': ahora.strftime('%d/%m/%y %H:%M')},
            f'<b>{_("It may have changed")}</b>',
        ]))
    historia.append(Spacer(1, 8))
    historia.append(_ficha([
        (_('Customer'), task.customer.name),
        (_('Crossing day'), task.fecha_de_cruce.strftime('%d/%m/%Y')),
    ]))
    historia.append(Spacer(1, 6))

    anchos = [ANCHO * x for x in (0.17, 0.19, 0.15, 0.10, 0.13, 0.26)]
    datos = [[Paragraph(t, CABECERA) for t in (
        _('Location'), _('Custom ID'), _('PO / Order'), _('Bundles'),
        _('Type'), _('Description'))]]
    segundas = []

    # Por ubicacion, que es el recorrido de quien surte.
    renglones = sorted(
        task.renglones.select_related('operation', 'operation__location',
                                      'operation__bundle_type').all(),
        key=lambda r: ((r.operation.location.code if r.operation.location else 'zzz'),
                       r.operation.custom_id))
    for renglon in renglones:
        op = renglon.operation
        datos.append([
            Paragraph(op.location.code if op.location else '—', CELDA_B),
            Paragraph(op.custom_id, CELDA),
            # El pedido, que aqui sirve para lo mas practico de todo: cuando el
            # cliente llama preguntando por el suyo, quien esta en el anden le
            # contesta mirando el papel que ya tiene en la mano.
            Paragraph(op.po_order or '—', CELDA_B),
            Paragraph(_bultos_de_la_tarea(renglon), CELDA_R),
            Paragraph(op.get_bundle_type_display_name() or '—', CELDA),
            Paragraph((op.description or '—')[:70], CELDA),
        ])

    tabla = Table(datos, colWidths=anchos, repeatRows=1)
    tabla.setStyle(_estilo_de_tabla(anchos, segundas))
    historia.append(tabla)

    historia.append(Spacer(1, 14))
    historia.append(Paragraph(
        'WMS · %s · %s' % (task.custom_id, _('picking list — does not cover goods')),
        PIE))
    historia.append(Paragraph(
        _('Printed by %(quien)s') % {
            'quien': (usuario.username if usuario else '—')}, PIE))

    doc.build(historia)
    buffer.seek(0)
    return buffer.read()


# ═══════════════════════════════════════════════════════════════════════════
#  LA ORDEN DE CARGA
# ═══════════════════════════════════════════════════════════════════════════

def orden_de_carga(orden, usuario=None):
    """
    El papel que ampara. Ordenado por Custom ID, con totales y con firmas.

    Lleva su version impresa y el QR de vigencia al pie, en un recuadro grande
    y feo a proposito. Quien va a surtir lo pistolea antes de empezar y el
    telefono le dice si esta hoja sigue siendo la buena.
    """
    buffer = BytesIO()
    task = orden.task
    tenant = task.tenant
    doc = _documento(buffer, f'{orden.custom_id} v{orden.version}')
    historia = []

    emitida = timezone.localtime(orden.emitida_en)
    historia.append(_cabecera(
        tenant, _('LOAD ORDER'), orden.custom_id, [
            _('Task %(t)s') % {'t': task.custom_id},
            _('Version %(v)d · %(cuando)s') % {
                'v': orden.version, 'cuando': emitida.strftime('%d/%m/%y %H:%M')},
        ]))
    historia.append(Spacer(1, 8))

    numeros = ' · '.join(task.numeros_de_pedimento) or '—'
    historia.append(_ficha([
        (_('Customer'), task.customer.name),
        (_('Crossing day'), task.fecha_de_cruce.strftime('%d/%m/%Y')),
        (_('Customs'), task.aduana or '—'),
        (_('Broker license'), task.patente or '—'),
        (_('Pedimentos'), numeros),
        (_('Bundles'), orden.total_bultos),
    ]))
    historia.append(Spacer(1, 6))

    anchos = [ANCHO * x for x in (0.05, 0.20, 0.15, 0.10, 0.12, 0.12, 0.16, 0.10)]
    datos = [[Paragraph(t, CABECERA) for t in (
        '#', _('Custom ID'), _('PO / Order'), _('Bundles'), _('Type'),
        _('Kgs'), _('Location'), _('Ped.'))]]
    segundas = []

    for i, renglon in enumerate(
            orden.renglones.select_related('operation').all(), 1):
        datos.append([
            Paragraph(str(i), CELDA),
            Paragraph(renglon.operation.custom_id, CELDA_B),
            Paragraph(renglon.po_order or '—', CELDA_B),
            Paragraph(_bultos_de(renglon), CELDA_R),
            Paragraph(renglon.bundle_type or '—', CELDA),
            Paragraph(f'{renglon.weight_kgs:,.2f}' if renglon.weight_kgs else '—',
                      CELDA_R),
            Paragraph(renglon.ubicacion or '—', CELDA),
            Paragraph((renglon.pedimento or '—')[-9:], CELDA),
        ])
        # El segundo renglon: lo que solo se consulta cuando hay una duda.
        detalle = ' · '.join(x for x in (
            renglon.shipper, renglon.invoice, (renglon.descripcion or '')[:60],
            _('entered %(f)s') % {
                'f': renglon.operation.date.strftime('%d/%m/%y')}
            if renglon.operation.date else '') if x)
        sobran = _sobran(renglon)
        if sobran:
            detalle += ' · ' + str(_('partial: %(n)d bundles stay in the warehouse')
                                   % {'n': sobran})
        datos.append(['', Paragraph(detalle or '—', SEGUNDO)])
        segundas.append(len(datos) - 1)

    kilos = orden.total_kilos
    datos.append([
        '', Paragraph(_('TOTALS — %(n)d shipments')
                      % {'n': orden.renglones.count()}, CELDA_B),
        '', Paragraph(str(orden.total_bultos), CELDA_R), '',
        Paragraph(f'{kilos:,.2f}' if kilos else '—', CELDA_R), '', ''])

    tabla = Table(datos, colWidths=anchos, repeatRows=1)
    estilo = _estilo_de_tabla(anchos, segundas)
    estilo.add('BACKGROUND', (0, len(datos) - 1), (-1, len(datos) - 1), FONDO)
    estilo.add('LINEABOVE', (0, len(datos) - 1), (-1, len(datos) - 1), 1, TINTA)
    tabla.setStyle(estilo)
    historia.append(tabla)
    historia.append(Spacer(1, 12))

    # ── El recuadro del QR ──────────────────────────────────────────────────
    # Deliberadamente feo y grande: esta compitiendo por la atencion de alguien
    # que lleva veinte anos surtiendo sin leer papeles, y perder esa
    # competencia significa que el candado no sirve.
    url = '%s/orden/%d/v%d/' % (tenant_public_url(tenant), orden.pk, orden.version)
    aviso = Table([[
        generate_qr_code(url, size=68),
        Table([
            [Paragraph(_('SCAN THIS CODE BEFORE PICKING.'),
                       _estilo('av', fontName='Helvetica-Bold', fontSize=11,
                               textColor=ROJO))],
            [Paragraph(_('Your phone tells you whether this sheet is still the '
                         'good one. If it comes up red, there is a newer '
                         'version and this one no longer covers the load.'),
                       _estilo('av2', fontName='Helvetica', fontSize=8,
                               textColor=TINTA, leading=11))],
            [Paragraph(f'<b>{orden.custom_id} · v{orden.version}</b>',
                       _estilo('av3', fontName='Helvetica-Bold', fontSize=9,
                               textColor=TINTA))],
        ], colWidths=[ANCHO - 1.3 * inch]),
    ]], colWidths=[1.15 * inch, ANCHO - 1.15 * inch])
    aviso.setStyle(TableStyle([
        ('BOX',           (0, 0), (-1, -1), 2.5, ROJO),
        ('BACKGROUND',    (0, 0), (-1, -1), ROJO_SUAVE),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING',   (0, 0), (-1, -1), 8),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 8),
        ('TOPPADDING',    (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    historia.append(aviso)
    historia.append(Spacer(1, 16))
    historia.append(_firmas([
        _('Picked — warehouse'),
        _('Received — transfer name and signature'),
        _('Date and time of departure'),
    ]))

    historia.append(Spacer(1, 10))
    historia.append(Paragraph(
        'WMS · %s · %s · v%d' % (task.custom_id, orden.custom_id, orden.version),
        PIE))
    historia.append(Paragraph(
        _('Printed %(cuando)s by %(quien)s') % {
            'cuando': timezone.localtime().strftime('%d/%m/%y %H:%M'),
            'quien': (usuario.username if usuario else '—')}, PIE))

    doc.build(historia)
    buffer.seek(0)
    return buffer.read()


def _firmas(titulos):
    """Los recuadros de firma del pie."""
    ancho = ANCHO / len(titulos)
    fila = [Paragraph(t, ETIQUETA) for t in titulos]
    tabla = Table([[''] * len(titulos), fila], colWidths=[ancho] * len(titulos),
                  rowHeights=[0.42 * inch, None])
    tabla.setStyle(TableStyle([
        ('LINEBELOW',   (0, 0), (-1, 0), 0.8, TINTA),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING',(0, 0), (-1, -1), 10),
        ('TOPPADDING',  (0, 1), (-1, 1), 3),
        ('VALIGN',      (0, 0), (-1, -1), 'BOTTOM'),
    ]))
    return tabla


# ═══════════════════════════════════════════════════════════════════════════
#  LA REMISION
# ═══════════════════════════════════════════════════════════════════════════

def remision(rem, usuario=None):
    """
    El papel que se le entrega al transfer.

    Lleva lo mismo que la orden sobre la mercancia y encima toda la cadena de
    entrega: quien cruza, a quien le deja la carga en la frontera mexicana, a
    donde va despues y a quien se le factura. Los tres bloques van separados a
    proposito, porque en la practica son tres empresas distintas y hoy esa
    informacion viaja de boca en boca.

    La leyenda del QR es la frase que mas valor tiene de todo el documento:
    dice, por escrito y con nombre y hora, que alguien conto la carga bulto por
    bulto. Es lo que hoy no existe en ningun papel.
    """
    buffer = BytesIO()
    task = rem.task
    tenant = task.tenant
    doc = _documento(buffer, rem.custom_id)
    historia = []

    emitida = timezone.localtime(rem.emitida_en)
    historia.append(_cabecera(
        tenant, _('REMISION'), rem.custom_id, [
            _('Order %(o)s v%(v)d') % {'o': rem.order.custom_id,
                                       'v': rem.order.version},
            _('Task %(t)s') % {'t': task.custom_id},
            emitida.strftime('%d/%m/%y %H:%M'),
        ]))
    historia.append(Spacer(1, 8))

    historia.append(_bloque(_('Transfer crossing the merchandise'), [
        (_('Company'), rem.transfer_empresa),
        (_('Driver'), rem.transfer_chofer),
        (_('Unit / trailer'), rem.transfer_unidad),
        (_('Seal'), rem.sello),
    ], columnas=4))
    historia.append(Spacer(1, 4))
    historia.append(_bloque(_('Hand over at the Mexican border to'), [
        (_('Connecting carrier'), rem.linea_de_enlace),
        (_('Delivery address'), rem.domicilio_de_enlace),
    ]))
    historia.append(Spacer(1, 4))
    historia.append(_bloque(_('Final consignee — owner of the goods'), [
        (_('Company'), rem.destinatario),
        (_('Tax ID'), rem.destinatario_rfc),
        (_('Address'), rem.destinatario_domicilio),
    ], columnas=3))
    historia.append(Spacer(1, 6))

    anchos = [ANCHO * x for x in (0.05, 0.21, 0.15, 0.10, 0.12, 0.11, 0.11, 0.15)]
    datos = [[Paragraph(t, CABECERA) for t in (
        '#', _('Custom ID'), _('PO / Order'), _('Bundles'), _('Type'),
        _('Lbs'), _('Kgs'), _('Pedimento'))]]
    segundas = []
    for i, renglon in enumerate(
            rem.order.renglones.select_related('operation').all(), 1):
        datos.append([
            Paragraph(str(i), CELDA),
            Paragraph(renglon.operation.custom_id, CELDA_B),
            Paragraph(renglon.po_order or '—', CELDA_B),
            Paragraph(_bultos_de(renglon), CELDA_R),
            Paragraph(renglon.bundle_type or '—', CELDA),
            Paragraph(f'{renglon.weight_lbs:,.2f}' if renglon.weight_lbs else '—',
                      CELDA_R),
            Paragraph(f'{renglon.weight_kgs:,.2f}' if renglon.weight_kgs else '—',
                      CELDA_R),
            Paragraph(renglon.pedimento or '—', CELDA),
        ])
        detalle = ' · '.join(x for x in (
            (renglon.descripcion or '')[:60], renglon.shipper,
            renglon.invoice) if x)
        datos.append(['', Paragraph(detalle or '—', SEGUNDO)])
        segundas.append(len(datos) - 1)

    kilos = rem.order.total_kilos
    libras = [r.weight_lbs for r in rem.order.renglones.all() if r.weight_lbs]
    datos.append([
        '', Paragraph(_('TOTALS'), CELDA_B), '',
        Paragraph(str(rem.order.total_bultos), CELDA_R), '',
        Paragraph(f'{sum(libras):,.2f}' if libras else '—', CELDA_R),
        Paragraph(f'{kilos:,.2f}' if kilos else '—', CELDA_R), ''])

    tabla = Table(datos, colWidths=anchos, repeatRows=1)
    estilo = _estilo_de_tabla(anchos, segundas)
    estilo.add('BACKGROUND', (0, len(datos) - 1), (-1, len(datos) - 1), FONDO)
    estilo.add('LINEABOVE', (0, len(datos) - 1), (-1, len(datos) - 1), 1, TINTA)
    tabla.setStyle(estilo)
    historia.append(tabla)
    historia.append(Spacer(1, 6))

    # La instruccion de facturacion, que es lo que hace que este papel sirva
    # tambien para cobrar.
    historia.append(_bloque(_('Billing instruction'), [
        (_('Bill this crossing service to'),
         '%s%s' % (rem.destinatario,
                   f', {_("Tax ID")} {rem.destinatario_rfc}'
                   if rem.destinatario_rfc else '')),
        (_('Mandatory reference on the invoice'), rem.custom_id),
    ], columnas=2))
    historia.append(Spacer(1, 6))

    # ── La verificacion ─────────────────────────────────────────────────────
    verificada = (timezone.localtime(rem.verificada_en).strftime('%d/%m/%y %H:%M')
                  if rem.verificada_en else '—')
    if rem.con_discrepancia:
        # Va impreso en el papel que lleva el chofer, no escondido en una
        # pantalla: quien recibe la mercancia en la frontera tiene que poder
        # ver que este embarque salio sabiendo que no cuadraba. Si la excepcion
        # no se ve, deja de ser una excepcion.
        texto = _('ISSUED WITH A DIFFERENCE — %(v)d of %(t)d bundles verified.')
        cuerpo = [
            [Paragraph(str(texto) % {'v': rem.bultos_verificados,
                                     't': rem.bultos_de_la_orden},
                       _estilo('dif', fontName='Helvetica-Bold', fontSize=11,
                               textColor=ROJO, leading=14))],
            [Paragraph(rem.diferencia or '—',
                       _estilo('dif2', fontName='Helvetica-Bold', fontSize=9,
                               textColor=ROJO, leading=12))],
            [Paragraph(_('Authorised by %(quien)s. Reason: %(motivo)s')
                       % {'quien': (rem.autorizada_por.username
                                    if rem.autorizada_por else '—'),
                          'motivo': rem.motivo_discrepancia},
                       _estilo('dif3', fontName='Helvetica', fontSize=9,
                               textColor=TINTA, leading=12))],
        ]
        marco, fondo, borde = cuerpo, ROJO_SUAVE, ROJO
    else:
        marco = [
            [Paragraph(_('%(n)d of %(n)d bundles verified one by one with a '
                         'scanner.') % {'n': rem.bultos_de_la_orden},
                       _estilo('ver', fontName='Helvetica-Bold', fontSize=10,
                               textColor=TINTA, leading=13))],
            [Paragraph(_('Verified by %(quien)s on %(cuando)s. This document '
                         'could not be issued until the physical load matched '
                         'order %(o)s v%(v)d exactly.')
                       % {'quien': (rem.verificada_por.username
                                    if rem.verificada_por else '—'),
                          'cuando': verificada,
                          'o': rem.order.custom_id, 'v': rem.order.version},
                       _estilo('ver2', fontName='Helvetica', fontSize=8,
                               textColor=GRIS, leading=11))],
        ]
        fondo, borde = FONDO, GRIS

    url = '%s/orden/%d/v%d/' % (tenant_public_url(tenant), rem.order.pk,
                                rem.order.version)
    caja = Table([[generate_qr_code(url, size=58),
                   Table(marco, colWidths=[ANCHO - 1.3 * inch])]],
                 colWidths=[1.05 * inch, ANCHO - 1.05 * inch])
    caja.setStyle(TableStyle([
        ('BOX',           (0, 0), (-1, -1), 1.6, borde),
        ('BACKGROUND',    (0, 0), (-1, -1), fondo),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING',   (0, 0), (-1, -1), 8),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 8),
        ('TOPPADDING',    (0, 0), (-1, -1), 7),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
    ]))
    historia.append(caja)
    historia.append(Spacer(1, 10))
    historia.append(_firmas([
        _('Handed over — warehouse'),
        _('Received — name, signature and time of the transfer'),
    ]))
    historia.append(Spacer(1, 6))
    historia.append(Paragraph(
        _('WMS · %(rm)s · issued against %(o)s v%(v)d')
        % {'rm': rem.custom_id, 'o': rem.order.custom_id,
           'v': rem.order.version}, PIE))
    historia.append(Paragraph(
        _('Printed %(cuando)s by %(quien)s')
        % {'cuando': timezone.localtime().strftime('%d/%m/%y %H:%M'),
           'quien': (usuario.username if usuario else '—')}, PIE))

    doc.build(historia)
    buffer.seek(0)
    return buffer.read()


def _bloque(titulo, pares, columnas=2):
    """Un recuadro con titulo, de los tres de la cadena de custodia."""
    dentro = _ficha(pares, columnas=columnas)
    caja = Table([[Paragraph(str(titulo).upper(), ETIQUETA)], [dentro]],
                 colWidths=[ANCHO])
    caja.setStyle(TableStyle([
        ('BOX',           (0, 0), (-1, -1), 0.8, GRIS_CLARO),
        ('BACKGROUND',    (0, 0), (0, 0), FONDO),
        ('LEFTPADDING',   (0, 0), (-1, -1), 7),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 7),
        ('TOPPADDING',    (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    return caja
