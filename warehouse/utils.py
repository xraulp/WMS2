from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.formats import date_format
from reportlab.lib.pagesizes import letter, A5
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch, cm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether, PageBreak, Image
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from io import BytesIO
import logging
import os

from django.conf import settings

from . import bultos

logger = logging.getLogger(__name__)


# Lo que se le quita al final del nombre de una empresa para poder firmar con
# el. No es una lista de formas societarias validas: es lo que estorba cuando
# hay que poner un nombre en un renglon estrecho.
_SUFIJOS_SOCIETARIOS = [
    'sa de cv', 'sa. de cv', 's.a. de c.v.', 's. a. de c. v.', 'sapi de cv',
    's de rl de cv', 'srl de cv', 's.a.s.', 'sas', 'sa', 's.a.',
    'llc', 'l.l.c.', 'inc', 'inc.', 'corp', 'corp.', 'ltd', 'ltd.',
    'co', 'co.', 'sl', 's.l.', 'sc', 's.c.',
]


def nombre_corto(nombre, tope=22):
    """
    El nombre de una empresa como se firma un mensaje.

    «Customer Test, SA. de CV» no es una firma, es un dato del acta
    constitutiva: ocupa la mitad del ancho del globo y lo unico que aporta
    respecto de «Customer Test» es ruido. Se corta en la primera coma y se le
    quitan las formas societarias del final.

    No pretende ser exacto -- una empresa puede llamarse «Ltd» de verdad -- sino
    legible. Por eso, si al recortar no quedara nada, se devuelve el nombre tal
    como estaba: es preferible una firma larga a una firma vacia.
    """
    limpio = (nombre or '').strip()
    if not limpio:
        return ''

    corto = limpio.split(',')[0].strip()

    # Los sufijos se quitan uno tras otro: «Algo SA de CV» y «Algo, S.A.» caen
    # en la misma pasada, y un «Algo SA de CV.» con punto tambien.
    seguir = True
    while seguir:
        seguir = False
        sin_punto = corto.rstrip('.').strip()
        for sufijo in _SUFIJOS_SOCIETARIOS:
            if sin_punto.lower().endswith(' ' + sufijo):
                corto = sin_punto[:-len(sufijo)].strip().rstrip(',').strip()
                seguir = True
                break

    if not corto:
        return limpio
    # Un nombre que sigue siendo larguisimo se recorta con puntos suspensivos:
    # el globo del mensaje no crece, asi que sin esto la firma partiria el
    # renglon en dos y el mensaje empezaria mas abajo en unas filas que en otras.
    if len(corto) > tope:
        return corto[:tope - 1].rstrip() + '…'
    return corto


def tenant_public_url(tenant=None):
    """
    URL pública base del tenant, para los enlaces y QR que salen impresos.

    Si hay un dominio raíz configurado (TENANT_BASE_DOMAIN) y el tenant tiene
    subdominio propio, cada tenant apunta al suyo. Si no, todos comparten
    SITE_BASE_URL, que es como opera hoy: un solo host en Render.

    El subdominio 'default' se ignora a propósito: es el que reciben los tenants
    creados por el backfill, no un subdominio real que alguien haya registrado.
    """
    base_domain = getattr(settings, 'TENANT_BASE_DOMAIN', '')
    subdomain = getattr(tenant, 'subdomain', None)

    if base_domain and subdomain and subdomain != 'default':
        return f"https://{subdomain}.{base_domain}"

    return settings.SITE_BASE_URL


def operation_digital_url(operation, path='/'):
    """
    Enlace al expediente digital de una operación, ya resuelto contra el tenant
    dueño de la operación. `path` elige la vista destino: '/dashboard/' para
    escritorio, '/mobile/' para el QR que se escanea con el teléfono.
    """
    base = tenant_public_url(getattr(operation, 'tenant', None))
    return f"{base}{path}?tab=digital&q={operation.custom_id}"


def codigo_de_barras(texto, ancho_mm=48, alto_mm=9, llenar=False, letra=7):
    """
    Un Code 128 con el texto legible debajo.

    Cuesta unas lineas y compra tres cosas: que sirvan las pistolas que ya hay
    en la bodega sean del tipo que sean, que sirva cualquier lector barato que
    se compre despues, y que el dia que falle todo alguien pueda leer el numero
    con los ojos y teclearlo. Esa ultima es la que de verdad importa: un anden
    a las seis de la manana con una pistola sin bateria no puede quedarse
    parado.

    Devuelve una tabla con el codigo arriba y el texto debajo. El codigo de
    ReportLab es un flowable, no una figura, asi que se apila en vez de
    dibujarse: da igual para el resultado y evita montar un lienzo.

    Con `llenar` las barras tambien se ensanchan hasta ocupar todo el hueco:
    en la etiqueta de media hoja sobra sitio, y una barra mas ancha la lee la
    pistola desde mas lejos y con menos luz.
    """
    from reportlab.graphics.barcode import code128
    from reportlab.lib.units import mm

    ancho, alto = ancho_mm * mm, alto_mm * mm
    codigo = code128.Code128(texto, barHeight=alto, humanReadable=False,
                             quiet=False)
    # El ancho de barra se ajusta para que el codigo entre en el sitio que
    # tiene: un codigo cortado no lo lee ninguna pistola.
    if codigo.width > ancho or llenar:
        codigo = code128.Code128(
            texto, barHeight=alto, humanReadable=False, quiet=False,
            barWidth=codigo.barWidth * (ancho / codigo.width))

    fila = Table(
        [[codigo],
         [Paragraph(texto, ParagraphStyle(
             'codigo_legible', parent=getSampleStyleSheet()['Normal'],
             fontName='Helvetica', fontSize=letra, leading=letra + 1,
             textColor=colors.HexColor('#334155')))]],
        colWidths=[ancho])
    fila.setStyle(TableStyle([
        ('LEFTPADDING',   (0, 0), (-1, -1), 0),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
        ('TOPPADDING',    (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ('ALIGN',         (0, 0), (-1, -1), 'LEFT'),
    ]))
    return fila


###agregado 0610262
def generate_qr_code(data, size=50):
    """Genera un código QR y lo devuelve como objeto Image de ReportLab"""
    import qrcode
    from io import BytesIO
    from reportlab.platypus import Image

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=4,
        border=1,
    )
    qr.add_data(data)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white")

    buffer = BytesIO()
    qr_img.save(buffer, format='PNG')
    buffer.seek(0)

    return Image(buffer, width=size, height=size)

###agregado 0610262


# ── Color palette #10b981
DARK      = colors.HexColor('#0f172a')
ACCENT    = colors.HexColor('#0ea5e9')
ASOFT     = colors.HexColor('#e0f2fe')
MID       = colors.HexColor('#64748b')
FAINT     = colors.HexColor('#94a3b8')
LIGHT     = colors.HexColor('#f1f5f9')
BORDER    = colors.HexColor('#e2e8f0')
WHITE     = colors.white
ENTRY_COL = colors.HexColor('#03C04A')#ENTRY_COL
EXIT_COL  = colors.HexColor('#ef4444')
ENTRY_BG  = colors.HexColor('#d1fae5')
EXIT_BG   = colors.HexColor('#fee2e2')
TD_COL    = colors.HexColor('#d97706')   # trasbordo
RD_COL    = colors.HexColor('#7c3aed')   # revision

# El color y el titulo de cada tipo en los documentos. Antes se decidian con un
# `if` de dos ramas -- si no era ENTRY, se pintaba y se titulaba como una salida
# -- asi que una revision salia del PDF diciendo "Goods Dispatch".
COLOR_DEL_TIPO = {'ENTRY': ENTRY_COL, 'EXIT': EXIT_COL, 'TD': TD_COL, 'RD': RD_COL}
TITULO_DEL_TIPO = {
    'ENTRY': 'Operation Report: Goods Receipt',
    'EXIT':  'Operation Report: Goods Dispatch',
    'TD':    'Operation Report: Transfer',
    'RD':    'Operation Report: Inspection',
}


def color_del_tipo(operation_type):
    return COLOR_DEL_TIPO.get(operation_type, EXIT_COL)


def titulo_del_tipo(operation_type):
    # Se traduce al pedirlo, no al definir el diccionario: el idioma que manda
    # es el del cliente que recibe el documento, y ese no se sabe al importar.
    return _(TITULO_DEL_TIPO.get(operation_type, 'Operation Report'))


# ═══════════════════════════════════════════════════════════════════════════════
# REPORT PDF  —  Full operation report (sent by email)
# ═══════════════════════════════════════════════════════════════════════════════

def logo_de(tenant, ancho=1.0*inch, alto=0.6*inch):
    """
    El logo de una empresa como imagen de reportlab, o None si no tiene.

    Antes cada documento cargaba un archivo escrito en el codigo: el reporte de
    operacion, uno del repositorio -- el logo de la primera empresa, que asi
    firmaba los reportes de todas --, y el reporte de operaciones, una ruta
    absoluta de otra maquina (`/home/rdeluna/...`) que en produccion no existe,
    de modo que ese PDF nunca llevo logo y nadie se entero porque el error se
    lo tragaba un `except` mudo.

    Ahora sale del tenant y se lee del almacen. Nunca lanza: un logo que no se
    puede cargar no puede impedir que salga un reporte, y quien lo mira
    entiende igual un documento sin logo que uno que no llega.
    """
    archivo = getattr(tenant, 'logo', None)
    if not archivo or not getattr(archivo, 'name', ''):
        return None
    try:
        with archivo.open('rb') as contenido:
            datos = BytesIO(contenido.read())
        return Image(datos, width=ancho, height=alto)
    except Exception as e:
        logger.warning('No se pudo cargar el logo de %s: %s',
                       getattr(tenant, 'name', '—'), e)
        return None


def generate_pdf_report(operation):
    """Full A4 report: header, all operation fields, attached files list."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=letter,
        rightMargin=0.65*inch, leftMargin=0.65*inch,
        topMargin=0.65*inch, bottomMargin=0.65*inch,
    )

    # ── Styles
    def style(name, **kw):
        base = getSampleStyleSheet()['Normal']
        return ParagraphStyle(name, parent=base, **kw)

    S = {
        'company': style('co', fontName='Helvetica-Bold', fontSize=18,
                         textColor=WHITE, spaceAfter=2),
        'subtitle': style('su', fontName='Helvetica', fontSize=9,
                          textColor=colors.HexColor('#5b5b5b'), spaceAfter=0),##94a3b8
        'section':  style('sec', fontName='Helvetica-Bold', fontSize=9,
                          textColor=FAINT, spaceBefore=10, spaceAfter=4,
                          textTransform='uppercase', letterSpacing=1),
        'label':    style('lb', fontName='Helvetica', fontSize=9,
                          textColor=MID),
        'value':    style('vl', fontName='Helvetica', fontSize=11,
                          textColor=DARK),
        'footer':   style('ft', fontName='Helvetica', fontSize=7,
                          textColor=FAINT, alignment=TA_CENTER),
        'custom_id':style('ci', fontName='Helvetica-Bold', fontSize=20,
                          textColor=WHITE),
    }

    story = []
    type_color = color_del_tipo(operation.operation_type)
    type_label = operation.get_operation_type_display().upper()


    logo_img = logo_de(operation.tenant)

    if logo_img is not None:
        # Logo centrado verticalmente con espaciadores
        logo_cell = Table([
            [Spacer(1, -0.08*inch)],
            [logo_img],
        ], colWidths=1.2*inch)
        logo_cell.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
    else:
        tenant_name = operation.tenant.name if operation.tenant else 'WMS'
        logo_cell = Paragraph(f'<b>{tenant_name.upper()}</b>', style('lg', fontName='Helvetica-Bold',
                              fontSize=11, textColor=WHITE))

    # ── Columna central (WAREHOUSE MANAGEMENT SYSTEM + subtítulo) ──
    center_column = Table([
        [
            Paragraph('<b>WAREHOUSE MANAGEMENT SYSTEM</b>',
                      style('hd', fontName='Helvetica-Bold', fontSize=13, textColor=colors.HexColor("#5b5b5b"))),
        ],
        [
            Paragraph(titulo_del_tipo(operation.operation_type), S['subtitle']),
        ],
    ], colWidths=4.2*inch)

    center_column.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, 0), 'MIDDLE'),
        ('VALIGN', (0, 1), (-1, 1), 'MIDDLE'),
        ('PADDING', (0, 0), (-1, -1), 6),
    ]))

    # ── Columna derecha (type_label + custom_id) ──
    right_column = Table([
        [
            Paragraph(f'<b>{type_label}</b>',
                      style('tp', fontName='Helvetica-Bold', fontSize=14,
                            textColor=WHITE, alignment=TA_CENTER)),
        ],
        [
            Paragraph(operation.custom_id,
                      style('ci2', fontName='Helvetica-Bold', fontSize=11,
                            textColor=WHITE, alignment=TA_CENTER)),
        ],
    ], colWidths=1.6*inch)

    right_column.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), type_color),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, 0), 'MIDDLE'),
        ('VALIGN', (0, 1), (-1, 1), 'MIDDLE'),
        ('PADDING', (0, 0), (-1, -1), 3),
    ]))

    # ── Header principal ──
    header = Table([
        [logo_cell, center_column, right_column],
    ], colWidths=[1.4*inch, 4.2*inch, 1.6*inch], rowHeights=[0.9*inch])

    header.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, 0), colors.HexColor("#ffffff")),
        ('BACKGROUND', (1, 0), (1, 0), colors.HexColor("#ffffff")),
        ('BACKGROUND', (2, 0), (2, 0), type_color),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 0), (0, 0), 'CENTER'),
        ('ALIGN', (1, 0), (1, 0), 'CENTER'),
        ('ALIGN', (2, 0), (2, 0), 'CENTER'),
        ('PADDING', (0, 0), (-1, -1), 0),
        ('ROUNDEDCORNERS', [6, 6, 6, 6]),
        ('BOX', (0, 0), (-1, -1), .5, colors.HexColor("#cfcccc")),
    ]))

    story.append(header)
    story.append(Spacer(1, 16))
    story.append(HRFlowable(width='100%', thickness=0.5, color=BORDER, spaceAfter=8))

    # ── FIELD TABLE  (2-column grid)
    def field(label, value):
        return [
            Paragraph(label.upper(), S['label']),
            Paragraph(str(value) if value else '—', S['value']),
        ]

    rows_data = [
        field(_('Date:'),
              # `DATE_FORMAT` es el formato largo del idioma activo: "July 8,
              # 2026" en ingles y "8 de julio de 2026" en espanol. Escribir el
              # formato a mano daba "julio 08, 2026", que no lo escribe nadie.
              date_format(operation.date, 'DATE_FORMAT')),
        field(_('Type:'),             operation.get_operation_type_display()),
        field(_('Custom ID:'),        operation.custom_id),
        field(_('Entries Dispatched:'), operation.entry_dispatched or '—'),
        field(_('Customer:'),         operation.get_customer_display()),
        field(_('Shipper:'),          operation.get_shipper_display()),
        field(_('Invoice:'),          operation.invoice or '—'),
        field(_('PO / Order:'),       operation.po_order or '—'),
        field(_('Seal:'),             operation.seal or '—'),
        field(_('Carrier:'),          operation.get_carrier_display()),
        field(_('PRO:'),              operation.pro or '—'),
        field(_('Trailer:'),          operation.trailer or '—'),
        field(_('Bundle Type:'),      operation.get_bundle_type_display_name()),
        field(_('Bundle Qty:'),       operation.bundle_qty or '—'),
        field(_('Weight:'),
              f"{operation.weight_lbs or '—'} LBS  /  {operation.weight_kgs or '—'} KGS"),
        field(_('Description:'),      operation.description or '—'),
        field(_('Note:'),             operation.note or '—'),
        field(_('Damage:'),           '⚠ YES — ' + (operation.damage_description or '')
                                  if operation.damage else 'No'),
    ]

    # Pair into 2 columns per row
    grid_rows = []
    for i in range(0, len(rows_data), 2):
        left  = rows_data[i]
        right = rows_data[i+1] if i+1 < len(rows_data) else [Paragraph('&nbsp;', S['label']), Paragraph('&nbsp;', S['value'])]
        grid_rows.append([left[0], left[1], Paragraph('', S['label']), right[0], right[1]])

    grid = Table(grid_rows,
                 colWidths=[1.1*inch, 2.4*inch, 0.2*inch, 1.1*inch, 2.4*inch])
    grid.setStyle(TableStyle([
        ('VALIGN',      (0,0), (-1,-1), 'TOP'),
        ('PADDING',     (0,0), (-1,-1), 5),
        ('ROWBACKGROUNDS', (0,0), (-1,-1), [WHITE, LIGHT]),
        ('LINEBELOW',   (0,0), (1,-1), 0.3, BORDER),
        ('LINEBELOW',   (3,0), (4,-1), 0.3, BORDER),
    ]))
    story.append(grid)
    story.append(Spacer(1, 12))

    # ── ATTACHED FILES
    docs = operation.documents.all()
    if docs.exists():
        story.append(HRFlowable(width='100%', thickness=0.5, color=BORDER, spaceAfter=8))
        story.append(Paragraph(_('ATTACHED FILES'), S['section']))
        file_rows = [
            [Paragraph(h, style('fh', fontName='Helvetica-Bold', fontSize=8, textColor=WHITE))
             for h in ['#', _('Type'), _('File Name'), _('Uploaded')]]
        ]
        for i, d in enumerate(docs, 1):
            file_rows.append([
                str(i),
                d.get_file_type_display(),
                d.original_name or os.path.basename(d.file.name),
                d.uploaded_at.strftime('%Y-%m-%d'),
            ])
        ft = Table(file_rows, colWidths=[0.3*inch, 0.8*inch, 5.2*inch, 0.8*inch])
        ft.setStyle(TableStyle([
            ('BACKGROUND',  (0,0), (-1,0), colors.HexColor("#C62034")),
            ('FONTNAME',    (0,1), (-1,-1), 'Helvetica'),
            ('FONTSIZE',    (0,0), (-1,-1), 8),
            ('PADDING',     (0,0), (-1,-1), 5),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [WHITE, LIGHT]),
            ('GRID',        (0,0), (-1,-1), 0.3, BORDER),
        ]))
        story.append(ft)

    # ── FOOTER
    story.append(Spacer(1, 14))
    story.append(HRFlowable(width='100%', thickness=0.5, color=BORDER))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        f'Warehouse Management System · {operation.custom_id} · '
        f'Generated {operation.date.strftime("%Y-%m-%d")}',
        S['footer']
    ))

### agregado 0610262
    # ── CÓDIGO QR ──────────────────────────────────────────────────────────────
    qr_url = operation_digital_url(operation, '/mobile/')
    qr_image = generate_qr_code(qr_url, size=50)

    qr_text = Paragraph(
        'Escanee este código QR para acceder a los archivos digitales de esta operación.',
        style('qr_text', fontName='Helvetica', fontSize=8, textColor=MID)
    )

    qr_table = Table([[qr_image, qr_text]], colWidths=[0.7*inch, 4.6*inch])
    qr_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('PADDING', (0, 0), (-1, -1), 5),
    ]))

    story.append(Spacer(1, 12))
    story.append(HRFlowable(width='100%', thickness=0.5, color=BORDER))
    story.append(Spacer(1, 6))
    story.append(qr_table)
### agregado 0610262

    # ── ENLACE CLICKEABLE + QR (solo para Full Report) ────────────────────────
    qr_url_pc = operation_digital_url(operation, '/dashboard/')

    # Solo enlace clickeable (sin QR)
    link_text = Paragraph(
        f'🔗 <link href="{qr_url_pc}">Haga clic aquí para acceder al expediente digital de esta operación</link>',
        style('link', fontName='Helvetica', fontSize=9, textColor=colors.HexColor('#0ea5e9'))
    )

    story.append(Spacer(1, 12))
    story.append(HRFlowable(width='100%', thickness=0.5, color=BORDER))
    story.append(Spacer(1, 6))
    story.append(link_text)

    doc.build(story)
    buffer.seek(0)
    return buffer.read()




# ═══════════════════════════════════════════════════════════════════════════════
# LABEL PDF  —  dos etiquetas por hoja carta, una por bulto
# ═══════════════════════════════════════════════════════════════════════════════

# Medidas de la hoja y de cada etiqueta. La hoja carta se parte por la mitad:
# una etiqueta arriba, otra abajo, y la linea de corte justo en medio, para que
# un corte a ojo con la guillotina deje las dos del mismo tamano.
ETQ_MARGEN_LADO     = 0.40 * inch
ETQ_MARGEN_FUERA    = 0.35 * inch   # entre la etiqueta y el borde de la hoja
ETQ_MARGEN_CORTE    = 0.20 * inch   # entre la etiqueta y la linea de corte
ETQ_ALTO_ENCABEZADO = 1.30 * inch
ETQ_ANCHO_TIPO      = 0.30          # fraccion del encabezado para el bloque de color
ETQ_QR_MAXIMO       = 2.20 * inch
ETQ_QR_MINIMO       = 1.40 * inch
MM_PT               = 72 / 25.4     # puntos por milimetro


def _letra_que_cabe(texto, fuente, tamano, ancho, minimo=8):
    """El tamano mas grande, sin pasar de `tamano`, con el que `texto` cabe en
    `ancho`. Es lo que impide que el tipo o el numero salgan cortados: antes
    "ENTRADA (ED)" no cabia en su celda y se partia en dos renglones, y el
    segundo quedaba fuera de la caja."""
    from reportlab.pdfbase.pdfmetrics import stringWidth
    while tamano > minimo and stringWidth(texto, fuente, tamano) > ancho:
        tamano -= 0.5
    return tamano


def _qr_vectorial(datos, lado):
    """El QR dibujado con lineas y no como imagen: a 2 pulgadas una imagen de
    pocos pixeles sale con los bordes borrosos, y lo que se busca al agrandarlo
    es justo que la camara lo lea de lejos. Correccion de errores M: aguanta
    una esquina rayada o una arruga del plastico del pallet."""
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing
    qr = QrCodeWidget(datos, barLevel='M', barBorder=2)
    x0, y0, x1, y1 = qr.getBounds()
    dibujo = Drawing(lado, lado, transform=[lado / (x1 - x0), 0, 0,
                                            lado / (y1 - y0), 0, 0])
    dibujo.add(qr)
    return dibujo


def _dibujar_etiqueta(c, operation, bundle_num, qty, x, y_arriba, ancho, alto):
    """Una etiqueta, con su esquina superior izquierda en (x, y_arriba)."""
    from reportlab.graphics import renderPDF

    def style(name, **kw):
        base = getSampleStyleSheet()['Normal']
        return ParagraphStyle(name, parent=base, **kw)

    GRIS = colors.HexColor('#5b5b5b')
    tenant_name = (operation.tenant.name if operation.tenant else 'WMS').upper()
    type_color = color_del_tipo(operation.operation_type)
    type_label = operation.get_operation_type_display().upper()

    # ── ENCABEZADO ────────────────────────────────────────────────────────────
    # Dos partes: a la izquierda la empresa y el numero de entrada, grande y
    # centrado, que es lo que se busca con la vista en el anden; a la derecha
    # el bloque de color con el tipo y el bulto, centrados tambien. Cada texto
    # se achica lo necesario para caber en su sitio y no se corta nunca.
    alto_enc = ETQ_ALTO_ENCABEZADO
    y_enc = y_arriba - alto_enc
    ancho_tipo = ancho * ETQ_ANCHO_TIPO
    ancho_id = ancho - ancho_tipo
    radio = 8

    c.saveState()
    borde = c.beginPath()
    borde.roundRect(x, y_enc, ancho, alto_enc, radio)
    c.clipPath(borde, stroke=0, fill=0)
    c.setFillColor(colors.white)
    c.rect(x, y_enc, ancho_id, alto_enc, stroke=0, fill=1)
    c.setFillColor(type_color)
    c.rect(x + ancho_id, y_enc, ancho_tipo, alto_enc, stroke=0, fill=1)
    c.restoreState()
    c.setStrokeColor(colors.HexColor('#cfcccc'))
    c.setLineWidth(1)
    c.roundRect(x, y_enc, ancho, alto_enc, radio, stroke=1, fill=0)

    hueco = 16  # aire a cada lado del texto dentro de su parte
    centro_id = x + ancho_id / 2
    t = _letra_que_cabe(tenant_name, 'Helvetica-Bold', 15, ancho_id - 2 * hueco)
    c.setFillColor(GRIS)
    c.setFont('Helvetica-Bold', t)
    c.drawCentredString(centro_id, y_arriba - 0.36 * inch, tenant_name)
    t = _letra_que_cabe(operation.custom_id, 'Helvetica-Bold', 40, ancho_id - 2 * hueco)
    c.setFillColor(colors.HexColor('#0f172a'))
    c.setFont('Helvetica-Bold', t)
    c.drawCentredString(centro_id, y_enc + 0.28 * inch, operation.custom_id)

    centro_tipo = x + ancho_id + ancho_tipo / 2
    c.setFillColor(colors.white)
    t = _letra_que_cabe(type_label, 'Helvetica-Bold', 16, ancho_tipo - 2 * hueco)
    c.setFont('Helvetica-Bold', t)
    c.drawCentredString(centro_tipo, y_arriba - 0.40 * inch, type_label)
    bulto = f'{bundle_num} / {qty}'
    t = _letra_que_cabe(bulto, 'Helvetica-Bold', 38, ancho_tipo - 2 * hueco)
    c.setFont('Helvetica-Bold', t)
    c.drawCentredString(centro_tipo, y_enc + 0.28 * inch, bulto)

    # ── DATOS ─────────────────────────────────────────────────────────────────
    label_style = style('label', fontName='Helvetica-Bold', fontSize=8.5,
                        textColor=colors.HexColor('#64748b'))
    value_style = style('value', fontName='Helvetica', fontSize=12, leading=14,
                        textColor=colors.HexColor('#0f172a'))

    def corto(texto, n=70):
        texto = texto or '—'
        return texto if len(texto) <= n else texto[:n - 1] + '…'

    rows = [
        [Paragraph(_('DATE:'), label_style),
         Paragraph(operation.date.strftime('%Y-%m-%d'), value_style),
         Paragraph(_('PO/ORDER:'), label_style),
         Paragraph(str(operation.po_order or '—'), value_style)],
        [Paragraph(_('CUSTOMER:'), label_style),
         Paragraph(operation.get_customer_display(), value_style),
         Paragraph(_('PRO:'), label_style),
         Paragraph(str(operation.pro or '—'), value_style)],
        [Paragraph(_('SHIPPER:'), label_style),
         Paragraph(operation.get_shipper_display(), value_style),
         Paragraph(_('WEIGHT:'), label_style),
         Paragraph(f"{operation.weight_lbs or '—'} LBS", value_style)],
        [Paragraph(_('CARRIER:'), label_style),
         Paragraph(operation.get_carrier_display(), value_style),
         Paragraph(_('BUNDLE TYPE:'), label_style),
         Paragraph(operation.get_bundle_type_display_name(), value_style)],
        [Paragraph(_('DESCRIPTION:'), label_style),
         Paragraph(corto(operation.description), value_style),
         Paragraph(_('NOTE:'), label_style),
         Paragraph(corto(getattr(operation, 'note', '')), value_style)],
    ]
    ancho_etq = 1.25 * inch  # cabe TRANSPORTISTA: en un renglon
    ancho_val = ancho / 2 - ancho_etq
    tabla_datos = Table(rows, colWidths=[ancho_etq, ancho_val, ancho_etq, ancho_val])
    tabla_datos.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('LINEBELOW', (0, 0), (-1, -2), 0.8, colors.HexColor('#e2e8f0')),
    ]))
    _w, alto_datos = tabla_datos.wrapOn(c, ancho, alto)
    y_datos = y_enc - 0.10 * inch - alto_datos
    tabla_datos.drawOn(c, x, y_datos)

    # ── CODIGOS ───────────────────────────────────────────────────────────────
    # La etiqueta se escanea con el telefono, asi que el QR apunta a /mobile/.
    # Lleva el `&b=` del bulto: sin el, las seis etiquetas de una entrada de
    # seis pallets decian lo mismo y pistolear seis veces el mismo pallet
    # contaba como los seis. El QR ocupa todo el alto que queda: es lo que se
    # lee desde lejos.
    y_abajo = y_arriba - alto
    lado_qr = max(ETQ_QR_MINIMO, min(ETQ_QR_MAXIMO, y_datos - 0.10 * inch - y_abajo))
    qr_url = bultos.url_del_qr(
        operation_digital_url(operation, '/mobile/'),
        operation.custom_id, bundle_num)
    renderPDF.draw(_qr_vectorial(qr_url, lado_qr), c,
                   x + ancho - lado_qr, y_abajo)

    # Y a su lado, el mismo dato en un codigo de rayas. No es un respaldo del
    # QR: es lo que hace que sirvan las pistolas 1D que ya hay en la bodega, y
    # el texto de abajo es para el dia que alguien tenga que teclearlo.
    ancho_libre = ancho - lado_qr - 0.30 * inch
    codigo_de_rayas = codigo_de_barras(
        bultos.codigo(operation.custom_id, bundle_num),
        ancho_mm=min(100, ancho_libre / MM_PT), alto_mm=18, llenar=True, letra=11)
    _w, alto_rayas = codigo_de_rayas.wrapOn(c, ancho_libre, lado_qr)
    codigo_de_rayas.drawOn(c, x, y_abajo + (lado_qr - alto_rayas) / 2 + 6)

    c.setFillColor(colors.HexColor('#94a3b8'))
    c.setFont('Helvetica', 7)
    c.drawString(x, y_abajo + 2,
                 f'{tenant_name} · {operation.custom_id} · {_("Bundle")} {bundle_num}/{qty}')


def generate_label_pdf(operation):
    """
    Una etiqueta por bulto, dos por hoja carta.

    Se dibuja en posiciones fijas y no con un flujo de parrafos: con el flujo
    cada etiqueta media lo que le tocaba segun su contenido y la ultima de la
    hoja quedaba donde cayera; aqui cada una ocupa exactamente media hoja y la
    linea de corte va siempre en el mismo sitio.
    """
    from reportlab.pdfgen import canvas as lienzo

    buffer = BytesIO()
    c = lienzo.Canvas(buffer, pagesize=letter)
    c.setTitle(f'{operation.custom_id} - labels')
    ancho_hoja, alto_hoja = letter
    mitad = alto_hoja / 2
    ancho = ancho_hoja - 2 * ETQ_MARGEN_LADO
    alto = mitad - ETQ_MARGEN_FUERA - ETQ_MARGEN_CORTE
    qty = operation.bundle_qty or 1

    for bundle_num in range(1, qty + 1):
        arriba = bundle_num % 2 == 1
        y_arriba = (alto_hoja - ETQ_MARGEN_FUERA) if arriba else (mitad - ETQ_MARGEN_CORTE)
        _dibujar_etiqueta(c, operation, bundle_num, qty,
                          ETQ_MARGEN_LADO, y_arriba, ancho, alto)
        # La linea de corte solo cuando hay etiqueta abajo: en la ultima hoja
        # de un numero impar de bultos no hay nada que separar.
        if not arriba:
            c.saveState()
            c.setStrokeColor(colors.HexColor('#a0aec0'))
            c.setLineWidth(0.6)
            c.setDash(4, 3)
            c.line(ETQ_MARGEN_LADO, mitad, ancho_hoja - ETQ_MARGEN_LADO, mitad)
            c.restoreState()
        if not arriba or bundle_num == qty:
            c.showPage()

    c.save()
    buffer.seek(0)
    return buffer.read()


# ═══════════════════════════════════════════════════════════════════════════════
# OPERATIONS LIST REPORT PDF (Report Generator)
# ═══════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════════
# OPERATIONS LIST REPORT PDF (Report Generator)
# ═══════════════════════════════════════════════════════════════════════════════


from reportlab.lib.pagesizes import letter, landscape
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import inch
from io import BytesIO
from django.utils import timezone

# Definir colores constantes (ajústalos si ya los tienes definidos)
DARK   = colors.HexColor('#0f172a')
MID    = colors.HexColor('#475569')
LIGHT  = colors.HexColor('#f8fafc')
WHITE  = colors.white
BORDER = colors.HexColor('#cbd5e1')
ACCENT = colors.HexColor('#3b82f6')   # azul para líneas
ENTRY_COL = colors.HexColor('#008000')  # verde para ENTRY (si lo usas)
EXIT_COL  = colors.HexColor('#FF0000')  # rojo para EXIT ef4444
# Este archivo redefine la paleta a mitad de camino, asi que el mapa de colores
# hay que rehacerlo con los valores de aqui o el informe saldria con los de
# arriba.
COLOR_DEL_TIPO = {'ENTRY': ENTRY_COL, 'EXIT': EXIT_COL, 'TD': TD_COL, 'RD': RD_COL}

# aqui copeo el nuevo codigo def generate_operations_report_pdf
def generate_operations_report_pdf(operations, title=None,
                                   tenant=None):
    """El tenant decide el logo. Si no se pasa, se toma el de la primera
    operacion de la lista: todas son del mismo, porque las vistas ya filtran
    por empresa antes de llegar aqui."""
    from reportlab.platypus import Image  # importación necesaria para el logo
    # El titulo se traduce aqui y no en la firma: un valor por defecto se evalua
    # al importar el modulo, cuando todavia no hay idioma puesto.
    if title is None:
        title = _('Operations Report')
    buffer = BytesIO()
    # Márgenes
    doc = SimpleDocTemplate(buffer,
                            pagesize=landscape(letter),
                            rightMargin=0.3*inch,
                            leftMargin=0.3*inch,
                            topMargin=0.2*inch,###
                            bottomMargin=0.4*inch)

    SS = getSampleStyleSheet()
    story = []

## empieza codigo nuevo 052426 alineacion de logo, titulo y subtitulo
    story = []

    def S(name, **kw):
        return ParagraphStyle(name, parent=SS['Normal'], **kw)

    if tenant is None and operations:
        tenant = getattr(operations[0], 'tenant', None)



    # --- Logo (izquierda) ---
    # Salia de una ruta absoluta de otra maquina, asi que en produccion este
    # reporte nunca llevo logo: el archivo no existia y el `except` mudo lo
    # tapaba. Ahora es el de la empresa de las operaciones que se listan.
    logo = logo_de(tenant, ancho=1.0*inch, alto=0.7*inch)

    page_width = landscape(letter)[0]
    available_width = page_width - doc.leftMargin - doc.rightMargin
    logo_width = 1.3*inch

    # El titulo se centra respecto a la pagina, y para eso la fila lleva una
    # tercera columna vacia del ancho del logo: es el mismo patron que la
    # cabecera del reporte de operacion.
    #
    # Antes habia aqui un "ajuste fino" que le restaba dos pulgadas al ancho del
    # logo. Como el ancho era una, la columna quedaba en menos una pulgada y
    # reportlab lanza al calcular la tabla. Nunca se vio porque esta rama no
    # llego a ejecutarse jamas: el logo se cargaba de una ruta de otra maquina,
    # asi que `logo` siempre era None y el reporte salia por el `else`.
    title_width = available_width - (logo_width * 2)

    if logo:
        title_para = Paragraph(title, S('title', fontName='Helvetica-Bold', fontSize=16,
                                        textColor=DARK, alignment=1))
        header_table = Table([[logo, title_para, '']],
                             colWidths=[logo_width, title_width, logo_width])
        header_table.setStyle(TableStyle([
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('ALIGN', (0,0), (0,0), 'LEFT'),
            ('ALIGN', (1,0), (1,0), 'CENTER'),
            ('LEFTPADDING', (0,0), (-1,-1), 15),
            ('RIGHTPADDING', (0,0), (-1,-1), 0),
            ('TOPPADDING', (0,0), (-1,-1), 0),
            ('BOTTOMPADDING', (0,0), (-1,-1), -15),###aqui se modifica el espacio vertical entre la linea separadora y el  subtitlo
        ]))
        story.append(header_table)
    else:
        story.append(Paragraph(title, S('title', fontName='Helvetica-Bold', fontSize=16,
                                        textColor=DARK, alignment=1)))

    story.append(Spacer(1, 0.04*inch))

    # Subtítulo (con la misma alineación de columnas)
    subtitle_text = _('Generated: %(fecha)s  ·  %(n)s records') % {
        'fecha': timezone.now().strftime('%Y-%m-%d %H:%M'),
        'n': len(operations),
    }
    subtitle_para = Paragraph(subtitle_text, S('sub', fontName='Helvetica', fontSize=9,
                                               textColor=MID, alignment=1))
    if logo:
        subtitle_table = Table([['', subtitle_para, '']],
                               colWidths=[logo_width, title_width, logo_width])
        subtitle_table.setStyle(TableStyle([
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('ALIGN', (1,0), (1,0), 'CENTER'),
            ('LEFTPADDING', (0,0), (-1,-1), 25),###1:18
            ('RIGHTPADDING', (0,0), (-1,-1), 0),
            ('TOPPADDING', (0,0), (-1,-1), 0),
            ('BOTTOMPADDING', (0,0), (-1,-1), 0),
        ]))
        story.append(subtitle_table)
    else:
        story.append(subtitle_para)

    story.append(HRFlowable(width='100%', thickness=1, color=colors.HexColor('#C62034'), spaceAfter=8))


## termina codigo nuevo 052426 alineacion de logo, titulo y subtitulo

    # Cabeceras de la tabla (12 columnas)
    headers = [
        _('Date'), _('Custom ID'), _('Type'), _('Status'), _('Customer'), _('Shipper'),
        _('Invoice'), _('PO/Order'), _('PRO'), _('Description'), _('Bundle Type'),
        _('Bundle Qty'), _('Weight LBS'),
    ]
    header_cells = [Paragraph(h, S('h',
                                   fontName='Helvetica-Bold',
                                   fontSize=7,
                                   textColor=WHITE,
                                   alignment=1)) for h in headers]
    rows = [header_cells]

    for op in operations:
        description = (op.description or '—')
        if len(description) > 40:
            description = description[:37] + '...'

        # Calcular abreviatura del estado
        if op.operation_type == 'ENTRY':
            status_val = op.status
            # Abreviaturas de tres letras porque la columna mide media
            # pulgada. Se traducen: en la tabla no cabe la palabra entera en
            # ningun idioma, pero 'WHS' no le dice nada a quien lee en espanol.
            if status_val == 'In Warehouse':
                status_abbr = _('WHS')
            elif status_val == 'Released Goods':
                status_abbr = _('RLS')
            else:
                status_abbr = '—'
        else:
            status_abbr = '—'

        rows.append([
            Paragraph(op.date.strftime('%Y-%m-%d'), S('cell', fontSize=7, textColor=DARK)),
            Paragraph(op.custom_id, S('cell', fontSize=7, textColor=DARK, fontName='Helvetica-Bold')),
            Paragraph(op.get_operation_type_display(), S('cell', fontSize=7, textColor=color_del_tipo(op.operation_type), fontName='Helvetica-Bold')),
            Paragraph(status_abbr, S('cell', fontSize=7, textColor=DARK, alignment=1)),
            Paragraph(op.get_customer_display()[:44], S('cell', fontSize=7, textColor=DARK)),
            Paragraph(op.get_shipper_display()[:44], S('cell', fontSize=7, textColor=DARK)),
            Paragraph(str(op.invoice or '—'), S('cell', fontSize=7, textColor=DARK)),
            Paragraph(str(op.po_order or '—'), S('cell', fontSize=7, textColor=DARK, fontName='Helvetica-Bold')),
            Paragraph(str(op.pro or '—'), S('cell', fontSize=7, textColor=DARK, fontName='Helvetica-Bold')),
            Paragraph(description, S('cell', fontSize=7, textColor=DARK)),
            Paragraph(op.get_bundle_type_display_name() or '—', S('cell', fontSize=7, textColor=DARK)),
            Paragraph(str(op.bundle_qty or '—'), S('cell', fontSize=7, textColor=DARK, alignment=1)),
            Paragraph(str(op.weight_lbs or '—'), S('cell', fontSize=7, textColor=DARK, alignment=1)),
        ])

    # Anchos de columna ajustados
    col_widths = [
        0.6 * inch,   # Date
        0.8 * inch,   # Custom ID
        0.5 * inch,   # Type
        0.5 * inch,   # Status
        1.2 * inch,   # Customer
        1.0 * inch,   # Shipper
        0.7 * inch,   # Invoice
        0.7 * inch,   # PO/Order
        1.3 * inch,   # PRO
        1.0 * inch,   # Description
        0.7 * inch,   # Bundle Type
        0.5 * inch,   # Bundle Qty
        0.5 * inch,   # Weight LBS
    ]

    t = Table(rows, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), DARK),
        ('TEXTCOLOR',  (0,0), (-1,0), WHITE),
        ('FONTNAME',   (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE',   (0,0), (-1,0), 7),
        ('ALIGN',      (0,0), (-1,0), 'CENTER'),
        ('VALIGN',     (0,0), (-1,-1), 'MIDDLE'),
        ('PADDING',    (0,0), (-1,-1), 2),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [WHITE, LIGHT]),
        ('GRID',       (0,0), (-1,-1), 0.3, BORDER),
        ('FONTSIZE',   (0,1), (-1,-1), 7),
        # Alineaciones por columna
        ('ALIGN', (0,1), (0,-1), 'CENTER'),
        ('ALIGN', (1,1), (1,-1), 'LEFT'),
        ('ALIGN', (2,1), (2,-1), 'CENTER'),
        ('ALIGN', (3,1), (3,-1), 'CENTER'),
        ('ALIGN', (4,1), (4,-1), 'LEFT'),
        ('ALIGN', (5,1), (5,-1), 'LEFT'),
        ('ALIGN', (6,1), (6,-1), 'LEFT'),
        ('ALIGN', (7,1), (7,-1), 'LEFT'),
        ('ALIGN', (8,1), (8,-1), 'LEFT'),
        ('ALIGN', (9,1), (9,-1), 'LEFT'),
        ('ALIGN', (10,1), (10,-1), 'LEFT'),
        ('ALIGN', (11,1), (11,-1), 'CENTER'),
        ('ALIGN', (12,1), (12,-1), 'CENTER'),
    ]))

    story.append(t)
    doc.build(story)
    buffer.seek(0)
    return buffer.read()

# aqui termina el nuevo codigo def generate_operations_report_pdf

# ── FACTURA DE LA PLATAFORMA ─────────────────────────────────────────────────

def datos_del_emisor():
    """
    Quien factura, para la cabecera del PDF.

    Sale de la configuracion y no del codigo por la misma razon por la que el
    nombre de la empresa dejo de estar escrito a mano en la pantalla de
    entrada: quien opera esta plataforma puede no ser quien la escribio. Sin
    configurar nada sale un nombre neutro, que es preferible a inventar uno.

    Se pone en el entorno:

        PLATFORM_BILLING_NAME="RDL Systems LLC"
        PLATFORM_BILLING_EMAIL="billing@ejemplo.com"
        PLATFORM_BILLING_ADDRESS="1234 Example St, Laredo TX"
    """
    return {
        'nombre':    getattr(settings, 'PLATFORM_BILLING_NAME', '') or 'WMS Platform',
        'email':     getattr(settings, 'PLATFORM_BILLING_EMAIL', '') or '',
        'direccion': getattr(settings, 'PLATFORM_BILLING_ADDRESS', '') or '',
    }


def sello_de_estado(factura):
    """
    El renglon de estado que va impreso en el PDF: (texto, fondo, letra).

    Sale aparte del dibujo por dos razones. La primera es que se pueda
    comprobar sin abrir el PDF: una prueba que solo mire que el archivo empieza
    por %PDF no distingue un documento correcto de uno que le dice "pendiente"
    al cliente de algo que ya pago. La segunda es que esto no es decoracion --
    el mismo PDF se adjunta al emitir y se vuelve a descargar meses despues, y
    lo que dice aqui tiene que ser lo de hoy, no lo del dia que se emitio.
    """
    if factura.estado == factura.PAGADA:
        texto = 'PAID · {}'.format(factura.pagada_el.strftime('%Y-%m-%d'))
        if factura.referencia_de_pago:
            texto += ' · {}'.format(factura.referencia_de_pago)
        return texto, colors.HexColor('#dcfce7'), colors.HexColor('#166534')

    if factura.estado == factura.CANCELADA:
        return 'CANCELLED', colors.HexColor('#f1f5f9'), colors.HexColor('#64748b')

    # El orden de las tres guardas de arriba es lo que impide que una pagada
    # fuera de plazo salga marcada como vencida: para cuando se llega aqui, ya
    # se sabe que sigue pendiente. `esta_vencida` lo comprueba igual por su
    # cuenta, asi que son dos redes y no una.
    if factura.esta_vencida:
        return ('OVERDUE · {} day(s) past due'.format(factura.dias_de_atraso),
                colors.HexColor('#fee2e2'), colors.HexColor('#991b1b'))

    return ('PENDING · due {}'.format(factura.vence_el.strftime('%Y-%m-%d')),
            colors.HexColor('#e0f2fe'), colors.HexColor('#075985'))


def generar_pdf_factura(factura):
    """
    El PDF de una factura de la plataforma, listo para adjuntar o descargar.

    Es un documento de cobro, asi que lleva lo que hace falta para reclamarlo y
    para casarlo con un pago: quien cobra, a quien, que periodo cubre, cuanto y
    para cuando. El estado va impreso -pagada, vencida o pendiente- porque el
    mismo PDF se manda al emitir y se vuelve a descargar despues, y lo peor que
    puede hacer es decir "pendiente" de algo que ya se cobro.
    """
    emisor = datos_del_emisor()
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=letter,
        rightMargin=0.75*inch, leftMargin=0.75*inch,
        topMargin=0.75*inch, bottomMargin=0.75*inch,
        title=factura.numero,
    )

    base = getSampleStyleSheet()['Normal']

    def style(name, **kw):
        return ParagraphStyle(name, parent=base, **kw)

    S = {
        'emisor':   style('em', fontName='Helvetica-Bold', fontSize=15,
                          textColor=colors.HexColor('#0f172a')),
        'pequeno':  style('pq', fontName='Helvetica', fontSize=8.5,
                          textColor=colors.HexColor('#64748b'), leading=12),
        'titulo':   style('ti', fontName='Helvetica-Bold', fontSize=22,
                          textColor=colors.HexColor('#0f172a'), alignment=TA_RIGHT),
        'numero':   style('nu', fontName='Helvetica-Bold', fontSize=11,
                          textColor=colors.HexColor('#0ea5e9'), alignment=TA_RIGHT),
        'etiqueta': style('et', fontName='Helvetica-Bold', fontSize=7.5,
                          textColor=colors.HexColor('#94a3b8')),
        'valor':    style('va', fontName='Helvetica', fontSize=10,
                          textColor=colors.HexColor('#0f172a')),
        'pie':      style('pi', fontName='Helvetica', fontSize=7.5,
                          textColor=colors.HexColor('#94a3b8'), alignment=TA_CENTER),
    }

    story = []

    # ── Cabecera: quien cobra, y que documento es esto
    izquierda = [Paragraph(emisor['nombre'], S['emisor'])]
    detalle = [d for d in (emisor['direccion'], emisor['email']) if d]
    if detalle:
        izquierda.append(Spacer(1, 3))
        izquierda.append(Paragraph('<br/>'.join(detalle), S['pequeno']))

    cabecera = Table(
        [[izquierda,
          [Paragraph('INVOICE', S['titulo']), Paragraph(factura.numero, S['numero'])]]],
        colWidths=[3.6*inch, 3.4*inch])
    cabecera.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))
    story += [cabecera, Spacer(1, 6),
              HRFlowable(width='100%', thickness=1,
                         color=colors.HexColor('#e2e8f0')),
              Spacer(1, 14)]

    # ── A quien se cobra y las fechas que importan
    def bloque(etiqueta, valor):
        return [Paragraph(etiqueta.upper(), S['etiqueta']),
                Spacer(1, 2),
                Paragraph(valor or '—', S['valor'])]

    empresa = [Paragraph('BILL TO', S['etiqueta']), Spacer(1, 2),
               Paragraph(factura.tenant.name, S['valor'])]
    if factura.tenant.billing_email:
        empresa += [Spacer(1, 2),
                    Paragraph(factura.tenant.billing_email, S['pequeno'])]

    fechas = Table([
        [Paragraph('ISSUED', S['etiqueta']), Paragraph('DUE', S['etiqueta'])],
        [Paragraph(factura.emitida_el.strftime('%Y-%m-%d'), S['valor']),
         Paragraph(factura.vence_el.strftime('%Y-%m-%d'), S['valor'])],
    ], colWidths=[1.7*inch, 1.7*inch])
    fechas.setStyle(TableStyle([
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 2),
    ]))

    story += [Table([[empresa, fechas]], colWidths=[3.6*inch, 3.4*inch],
                    style=TableStyle([
                        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                        ('LEFTPADDING', (0, 0), (-1, -1), 0),
                        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                    ])),
              Spacer(1, 18)]

    # ── El concepto. Una sola linea: un periodo de servicio.
    periodo = '{} → {}'.format(factura.periodo_inicio.strftime('%Y-%m-%d'),
                               factura.periodo_fin.strftime('%Y-%m-%d'))
    concepto = 'Warehouse Management System — service period'
    if factura.plan:
        concepto += ' ({} plan)'.format(factura.plan)

    detalle = Table([
        ['DESCRIPTION', 'PERIOD', 'AMOUNT USD'],
        [Paragraph(concepto, S['valor']),
         Paragraph(periodo, S['pequeno']),
         Paragraph('{:,.2f}'.format(factura.monto_usd), S['valor'])],
    ], colWidths=[3.6*inch, 1.9*inch, 1.5*inch])
    detalle.setStyle(TableStyle([
        ('FONTNAME',   (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE',   (0, 0), (-1, 0), 7.5),
        ('TEXTCOLOR',  (0, 0), (-1, 0), colors.HexColor('#94a3b8')),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f8fafc')),
        ('ALIGN',      (2, 0), (2, -1), 'RIGHT'),
        ('VALIGN',     (0, 0), (-1, -1), 'TOP'),
        ('LINEBELOW',  (0, 0), (-1, 0), 0.5, colors.HexColor('#e2e8f0')),
        ('LINEBELOW',  (0, 1), (-1, 1), 0.5, colors.HexColor('#e2e8f0')),
        ('TOPPADDING',    (0, 0), (-1, -1), 7),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
    ]))
    story += [detalle, Spacer(1, 10)]

    # ── El total, que es lo que se busca al abrir el documento
    total = Table([['TOTAL', '{:,.2f} USD'.format(factura.monto_usd)]],
                  colWidths=[5.5*inch, 1.5*inch])
    total.setStyle(TableStyle([
        ('FONTNAME',  (0, 0), (-1, -1), 'Helvetica-Bold'),
        ('FONTSIZE',  (0, 0), (0, 0), 9),
        ('FONTSIZE',  (1, 0), (1, 0), 13),
        ('TEXTCOLOR', (0, 0), (0, 0), colors.HexColor('#64748b')),
        ('ALIGN',     (0, 0), (-1, -1), 'RIGHT'),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
    ]))
    story += [total, Spacer(1, 16)]

    # ── Estado, tal como esta hoy.
    texto, fondo, letra = sello_de_estado(factura)

    sello = Table([[Paragraph(texto, style('se', fontName='Helvetica-Bold',
                                           fontSize=9, textColor=letra))]],
                  colWidths=[7.0*inch])
    sello.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), fondo),
        ('TOPPADDING', (0, 0), (-1, -1), 7),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
    ]))
    story += [sello]

    if factura.notas:
        story += [Spacer(1, 14), Paragraph('NOTES', S['etiqueta']), Spacer(1, 3),
                  Paragraph(factura.notas, S['pequeno'])]

    story += [Spacer(1, 26),
              HRFlowable(width='100%', thickness=0.5,
                         color=colors.HexColor('#e2e8f0')),
              Spacer(1, 6),
              Paragraph('{} · {}'.format(factura.numero, emisor['nombre']), S['pie'])]

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


# ═══════════════════════════════════════════════════════════════════════════
#  LA HOJA DE IMPUESTOS EN PAPEL
# ═══════════════════════════════════════════════════════════════════════════
#
# El mismo documento que se ve en pantalla, con las mismas columnas y en el
# mismo orden. Se imprime y se manda adjunto, que son las dos cosas que hoy se
# hacen copiando el Excel a un correo.
#
# Los colores no son los de la casa: son los del papel que el cliente lleva
# meses leyendo -- el vino de la cabecera y el durazno de los renglones --,
# porque el dia que el sistema empiece a mandar esta hoja no es el dia de
# cambiarle el aspecto.

HOJA_FUERTE = colors.HexColor('#9c1f23')
HOJA_FILA   = colors.HexColor('#fbe3d7')
HOJA_ALT    = colors.HexColor('#fdefe8')
HOJA_INK    = colors.HexColor('#8d1a1e')


def _importe_de_hoja(valor):
    """`345500` y no `345,500.00`: los centavos salen solo cuando los hay."""
    if valor is None:
        return ''
    from decimal import Decimal
    n = Decimal(str(valor)).quantize(Decimal('0.01'))
    entero = n.to_integral_value()
    if n == entero:
        return '{:,}'.format(int(entero))
    return '{:,.2f}'.format(n)


def generar_pdf_hoja_de_impuestos(tenant, cliente, renglones, total, parametros):
    """
    La hoja de un cliente en PDF. Devuelve los bytes.

    `renglones` llega ya ordenado por la vista, y con `estimado` puesto: el
    papel y la pantalla salen de la misma lista a proposito, para que no puedan
    decir cosas distintas.

    Va en carta **vertical** (pedido por Diego el 12 nov 2026; antes salia
    apaisada). Las columnas son las mismas y en el mismo orden -- el cliente
    lleva meses leyendola asi --; lo que cambia es el reparto del ancho y que
    la letra baja medio punto para que el status quepa en dos lineas.
    """
    buffer = BytesIO()
    pagina = letter
    doc = SimpleDocTemplate(buffer, pagesize=pagina,
                            rightMargin=0.45 * inch, leftMargin=0.45 * inch,
                            topMargin=0.45 * inch, bottomMargin=0.5 * inch,
                            title=_('Shipment report and duty estimate'))
    SS = getSampleStyleSheet()

    def S(nombre, **kw):
        return ParagraphStyle(nombre, parent=SS['Normal'], **kw)

    story = []

    # ── El membrete ─────────────────────────────────────────────────────────
    titulo = Paragraph(_('Shipment report and duty estimate'),
                       S('t', fontName='Helvetica-Bold', fontSize=14,
                         textColor=HOJA_INK, alignment=TA_LEFT))
    quien = Paragraph(
        '%s &nbsp;·&nbsp; %s' % (cliente.name,
                                 (tenant.short_name or tenant.name)),
        S('q', fontName='Helvetica', fontSize=9.5, textColor=MID,
          alignment=TA_LEFT, spaceBefore=3))

    ahora = timezone.localtime(timezone.now())
    # Con plural: la cadena suelta "%(n)s shipments" no tenia traduccion y la
    # hoja en español decia "5 shipments". La del plural si la tiene.
    from django.utils.translation import ngettext
    cuantos = ngettext('%(n)s shipment', '%(n)s shipments',
                       len(renglones)) % {'n': len(renglones)}
    sello = Paragraph(
        '%s<br/>%s<br/>%s' % (ahora.strftime('%d/%m/%Y'),
                              ahora.strftime('%H:%M'), cuantos),
        S('s', fontName='Helvetica', fontSize=9, textColor=MID,
          alignment=TA_RIGHT, leading=12))

    logo = logo_de(tenant, ancho=1.1 * inch, alto=0.6 * inch)
    # El marco de la pagina tiene 6 pt de relleno por lado: sin descontarlos,
    # la tabla se salia un poco del ancho de la raya de arriba.
    ancho = pagina[0] - doc.leftMargin - doc.rightMargin - 12
    izquierda = [titulo, quien]
    if logo:
        cabecera = Table([[logo, izquierda, sello]],
                         colWidths=[1.3 * inch, ancho - 2.7 * inch, 1.4 * inch])
    else:
        cabecera = Table([[izquierda, sello]],
                         colWidths=[ancho - 1.4 * inch, 1.4 * inch])
    cabecera.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    story.append(cabecera)
    story.append(HRFlowable(width='100%', thickness=2, color=HOJA_FUERTE,
                            spaceBefore=2, spaceAfter=10))

    # ── La tabla, columna por columna y en su orden ─────────────────────────
    cab = S('th', fontName='Helvetica-Bold', fontSize=6.8, textColor=colors.white,
            alignment=TA_CENTER, leading=8.2)
    celda = S('td', fontName='Helvetica', fontSize=7.5, alignment=TA_CENTER,
              leading=9.2)
    celda_st = S('st', fontName='Helvetica-Bold', fontSize=7.5,
                 textColor=HOJA_INK, alignment=TA_CENTER, leading=9.2)
    celda_num = S('num', fontName='Helvetica-Bold', fontSize=8.5,
                  alignment=TA_RIGHT, leading=10)

    filas = [[Paragraph(t, cab) for t in (
        '#', _('Status'), _('Invoice'), _('Entry number'),
        _('Date sent to review and VM'), _('Date reported'),
        _('Order number'), _('Pedimento'), _('Approx. duty'))]]

    for i, r in enumerate(renglones, 1):
        enviado = r.fecha_enviado_a_rev_y_mv
        reportado = r.fecha_reportado
        pedimento = r.pedimento
        filas.append([
            Paragraph(str(i), celda),
            Paragraph(r.status_texto, celda_st),
            Paragraph(_('YES') if r.tiene_factura else _('NO'), celda),
            Paragraph(r.entrada or '', celda),
            Paragraph(enviado.strftime('%d-%b') if enviado else '', celda),
            Paragraph(reportado.strftime('%d-%b') if reportado else '', celda),
            Paragraph(r.pedido or '', celda),
            Paragraph((pedimento.ped_consecutivo or pedimento.etiqueta)
                      if pedimento else '', celda),
            Paragraph('$ %s' % _importe_de_hoja(r.estimado)
                      if r.estimado else '—', celda_num),
        ])

    filas.append([
        Paragraph('', celda), Paragraph('', celda), Paragraph('', celda),
        Paragraph('', celda), Paragraph('', celda), Paragraph('', celda),
        Paragraph('', celda),
        Paragraph(_('TOTAL'), S('tot', fontName='Helvetica-Bold', fontSize=9,
                                alignment=TA_RIGHT)),
        Paragraph('$ %s' % _importe_de_hoja(total),
                  S('totn', fontName='Helvetica-Bold', fontSize=10,
                    alignment=TA_RIGHT)),
    ])

    # Reparto para carta vertical (7.6" utiles). El status es lo mas largo
    # ("LISTO PARA VALIDACION Y PAGO · ACME") y va en dos lineas; el importe no
    # se parte nunca, asi que conserva su ancho.
    # El numero de entrada (ED261112-9001) no se parte nunca.
    anchos = [0.26, 1.7, 0.55, 1.0, 0.78, 0.68, 0.95, 0.85, 0.95]
    escala = ancho / (sum(anchos) * inch)
    tabla = Table(filas, colWidths=[a * inch * escala for a in anchos],
                  repeatRows=1)

    estilo = [
        ('BACKGROUND', (0, 0), (-1, 0), HOJA_FUERTE),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -2), 0.75, colors.white),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        # En vertical cada punto cuenta: con el relleno de 6 pt por lado el
        # numero de entrada y el encabezado "Factura" se partian en dos.
        ('LEFTPADDING', (0, 0), (-1, -1), 3),
        ('RIGHTPADDING', (0, 0), (-1, -1), 3),
        # La fila del total: sin rejilla y con la raya de arriba, como el papel.
        ('BACKGROUND', (0, -1), (-1, -1), HOJA_FILA),
        ('LINEABOVE', (0, -1), (-1, -1), 1.5, HOJA_FUERTE),
    ]
    for i in range(1, len(filas) - 1):
        estilo.append(('BACKGROUND', (0, i), (-1, i),
                       HOJA_ALT if i % 2 else HOJA_FILA))
    tabla.setStyle(TableStyle(estilo))
    story.append(tabla)

    # ── El pie, que dice que esto no es una liquidacion ─────────────────────
    story.append(Spacer(1, 0.12 * inch))
    story.append(Paragraph(
        _('Estimate at working exchange rate %(tc)s · this is not a settlement')
        % {'tc': parametros.tipo_de_cambio},
        S('pie', fontName='Helvetica', fontSize=7.5, textColor=MID,
          alignment=TA_LEFT)))

    doc.build(story)
    pdf = buffer.getvalue()
    buffer.close()
    return pdf
