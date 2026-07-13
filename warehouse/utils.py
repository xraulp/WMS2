from django.utils import timezone
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
import os
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


# ═══════════════════════════════════════════════════════════════════════════════
# REPORT PDF  —  Full operation report (sent by email)
# ═══════════════════════════════════════════════════════════════════════════════

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
    type_color = ENTRY_COL if operation.operation_type == 'ENTRY' else EXIT_COL
    type_label = operation.get_operation_type_display().upper()


    logo_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'media', 'Untitled-3.jpg')

    if os.path.exists(logo_path):
        logo_img = Image(logo_path, width=1.0*inch, height=0.6*inch)
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
        logo_cell = Paragraph('<b>DYSER GROUP</b>', style('lg', fontName='Helvetica-Bold',
                              fontSize=11, textColor=WHITE))

    # ── Columna central (WAREHOUSE MANAGEMENT SYSTEM + subtítulo) ──
    center_column = Table([
        [
            Paragraph('<b>WAREHOUSE MANAGEMENT SYSTEM</b>',
                      style('hd', fontName='Helvetica-Bold', fontSize=13, textColor=colors.HexColor("#5b5b5b"))),
        ],
        [
            Paragraph('Operation Report: Goods Receipt' if operation.operation_type == 'ENTRY' else 'Operation Report: Goods Dispatch',
                      S['subtitle']),
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
        field('Date:',             operation.date.strftime('%B %d, %Y')),
        field('Type:',             operation.get_operation_type_display()),
        field('Custom ID:',        operation.custom_id),
        field('Entries Dispatched:', operation.entry_dispatched or '—'),
        field('Customer:',         operation.get_customer_display()),
        field('Shipper:',          operation.get_shipper_display()),
        field('Invoice:',          operation.invoice or '—'),
        field('PO / Order:',       operation.po_order or '—'),
        field('Seal:',             operation.seal or '—'),
        field('Carrier:',          operation.get_carrier_display()),
        field('PRO:',              operation.pro or '—'),
        field('Trailer:',          operation.trailer or '—'),
        field('Bundle Type:',      operation.get_bundle_type_display_name()),
        field('Bundle Qty:',       operation.bundle_qty or '—'),
        field('Weight:',
              f"{operation.weight_lbs or '—'} LBS  /  {operation.weight_kgs or '—'} KGS"),
        field('Description:',      operation.description or '—'),
        field('Note:',             operation.note or '—'),
        field('Damage:',           '⚠ YES — ' + (operation.damage_description or '')
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
        story.append(Paragraph('ATTACHED FILES', S['section']))
        file_rows = [
            [Paragraph(h, style('fh', fontName='Helvetica-Bold', fontSize=8, textColor=WHITE))
             for h in ['#', 'Type', 'File Name', 'Uploaded']]
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
    qr_url = f"https://rdeluna.pythonanywhere.com/mobile/?tab=digital&q={operation.custom_id}"
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
    qr_url_pc = f"https://rdeluna.pythonanywhere.com/dashboard/?tab=digital&q={operation.custom_id}"

    # Solo enlace clickeable (sin QR)
    link_text = Paragraph(
        f'🔗 <link href="{qr_url_pc }">Haga clic aquí para acceder al expediente digital de esta operación</link>',
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
# LABEL PDF  —  Full width labels, 2 columns, 3 labels per page
# ═══════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════════
# LABEL PDF  —  Full width labels, 2 columns, 3 labels per page
# ═══════════════════════════════════════════════════════════════════════════════

def generate_label_pdf(operation):
    """
    Generate labels with bordered header ONLY (2 rows).
    2 columns for data.
    3 labels per page - FULL WIDTH.
    """
    buffer = BytesIO()

    # Calcular ancho disponible en la hoja carta (letter = 8.5 x 11 pulgadas)
    page_width = 8.5 * inch
    page_height = 11 * inch

    # Márgenes
    margin_left = 0.55 * inch #0.4
    margin_right = 0.75 * inch #0.4
    margin_top = 0.5 * inch
    margin_bottom = 0.1 * inch

    # Ancho total disponible para la etiqueta
    label_width = page_width - margin_left - margin_right

    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        topMargin=margin_top,
        bottomMargin=margin_bottom,
        leftMargin=margin_left,
        rightMargin=margin_right,
    )

    story = []

    # Estilos
    def style(name, **kw):
        base = getSampleStyleSheet()['Normal']
        return ParagraphStyle(name, parent=base, **kw)

    header_company_style = style('header_company', fontName='Helvetica-Bold', fontSize=15, textColor=colors.HexColor('#5b5b5b'), alignment=TA_LEFT)
    header_type_style = style('header_type', fontName='Helvetica-Bold', fontSize=12, textColor=colors.white, alignment=TA_CENTER)
    custom_id_style = style('custom_id', fontName='Helvetica-Bold', fontSize=18, textColor=colors.HexColor('#5b5b5b'), alignment=TA_LEFT)
    bundle_style = style('bundle', fontName='Helvetica-Bold', fontSize=18, textColor=colors.HexColor('#ffffff'), alignment=TA_RIGHT)

    label_style = style('label', fontName='Helvetica-Bold', fontSize=8, textColor=colors.HexColor('#64748b'))
    value_style = style('value', fontName='Helvetica', fontSize=11, textColor=colors.HexColor('#0f172a'))

    qty = operation.bundle_qty or 1
    type_color = ENTRY_COL if operation.operation_type == 'ENTRY' else EXIT_COL
    type_label = operation.get_operation_type_display().upper()

    # Anchos de columnas
    col_left_width = label_width * 0.50
    col_right_width = label_width * 0.50

    for bundle_num in range(1, qty + 1):

        # ── HEADER CON BORDE (SOLO ENCABEZADO) ──
        # Primera fila del header
        header_row1 = Table([
            [
                Paragraph('<b>DYSER GROUP LLC</b>', header_company_style),
                Paragraph(f'<b>{type_label}</b>', header_type_style),
            ]
        ], colWidths=[label_width * 0.80, label_width * 0.20],
           rowHeights=[0.25 * inch])

        header_row1.setStyle(TableStyle([
            # ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#cfcccc')),
            ('BACKGROUND', (0, 0), (0, 0), colors.HexColor('#ffffff')),
            ('BACKGROUND', (1, 0), (1, 0), type_color),
            ('TEXTCOLOR', (0, 0), (0, 0), colors.white),
            ('TEXTCOLOR', (1, 0), (1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('PADDING', (0, 0), (-1, -1), 0),
            ('LEFTPADDING', (0, 0), (-1, -1), 15),
            ('RIGHTPADDING', (0, 0), (-1, -1), 25),
            ('TOPPADDING', (0, 0), (-1, -1), 1),
            ('BOTTOMPADDING', (0, 0), (0, 0), 12),   # Solo columna 0
            ('BOTTOMPADDING', (1, 0), (1, 0), 0),
        ]))

        # ===== CONFIGURACIÓN DE MÁRGENES PARA LA SEGUNDA FILA =====
        MARGEN_IZQUIERDO_CUSTOM_ID_INCH = 0.3
        MARGEN_DERECHO_CUSTOM_ID_INCH = 0.03
        MARGEN_IZQUIERDO_BUNDLE_INCH = 0.05
        MARGEN_DERECHO_BUNDLE_INCH = 0.5   # Ajusta este valor

        margen_izquierdo_custom_pt = MARGEN_IZQUIERDO_CUSTOM_ID_INCH * 72
        margen_derecho_custom_pt = MARGEN_DERECHO_CUSTOM_ID_INCH * 72
        margen_izquierdo_bundle_pt = MARGEN_IZQUIERDO_BUNDLE_INCH * 72
        margen_derecho_pt = MARGEN_DERECHO_BUNDLE_INCH * 72

        header_row2 = Table([
            [
                Paragraph(f'<b> {operation.custom_id}</b>', custom_id_style),
                Paragraph(f'<b> {bundle_num} / {qty}</b>', bundle_style),
            ]
        ], colWidths=[label_width * 0.80, label_width * 0.20],
           rowHeights=[0.40 * inch])

        header_row2.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, 0), colors.HexColor('#ffffff')),
            ('BACKGROUND', (1, 0), (1, 0), type_color),
            ('ALIGN', (0, 0), (0, 0), 'LEFT'),
            ('ALIGN', (1, 0), (1, 0), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('PADDING', (0, 0), (-1, -1), 0),
            ('LEFTPADDING', (0, 0), (0, 0), 15),
            ('RIGHTPADDING', (0, 0), (0, 0), 1),
            ('LEFTPADDING', (1, 0), (1, 0), 0),
            ('RIGHTPADDING', (1, 0), (1, 0), margen_derecho_pt),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
        ]))

        # HEADER COMPLETO CON BORDE
        header_table = Table([
            [header_row1],
            [header_row2],
        ], colWidths=[label_width])

        header_table.setStyle(TableStyle([
            ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#cfcccc')),
            ('ROUNDEDCORNERS', [6, 6, 6, 6]),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))

        # ============================================
        # DATOS ÚNICOS EN TABLA DE 4 COLUMNAS (incluye DESCRIPTION y NOTE)
        # ============================================
        ANCHO_ETIQUETA = 1.0 * inch   # Ajusta según necesites
        ancho_valor_izquierdo = col_left_width - ANCHO_ETIQUETA - 0.05 * inch
        ancho_valor_derecho = col_right_width - ANCHO_ETIQUETA - 0.05 * inch

        note_text = getattr(operation, 'note', '—') or '—'
        desc_text = (operation.description or '—')[:60]

        rows = [
            [Paragraph('DATE:', label_style),
             Paragraph(operation.date.strftime('%Y-%m-%d'), value_style),
             Paragraph('PO/ORDER:', label_style),
             Paragraph(str(operation.po_order or '—'), value_style)],

            [Paragraph('CUSTOMER:', label_style),
             Paragraph(operation.get_customer_display(), value_style),
             Paragraph('PRO:', label_style),
             Paragraph(str(operation.pro or '—'), value_style)],

            [Paragraph('SHIPPER:', label_style),
             Paragraph(operation.get_shipper_display(), value_style),
             Paragraph('WEIGHT:', label_style),
             Paragraph(f"{operation.weight_lbs or '—'} LBS", value_style)],

            [Paragraph('CARRIER:', label_style),
             Paragraph(operation.get_carrier_display(), value_style),
             Paragraph('BUNDLE TYPE:', label_style),
             Paragraph(operation.get_bundle_type_display_name(), value_style)],

            [Paragraph('DESCRIPTION:', label_style),
             Paragraph(desc_text, value_style),
             Paragraph('NOTE:', label_style),
             Paragraph(note_text, value_style)],
        ]

        tabla_datos = Table(rows, colWidths=[ANCHO_ETIQUETA, ancho_valor_izquierdo, ANCHO_ETIQUETA, ancho_valor_derecho])
        tabla_datos.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, -1), 3.5),
            ('BOTTOMPADDING', (0, 0), (-1, -1),1),###3.5 061526
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('LINEBELOW', (0, 0), (-1, -2), 0.9, colors.HexColor('#e2e8f0')),
            ('ALIGN', (0, 0), (0, -1), 'RIGHT'),
            ('ALIGN', (1, 0), (1, -1), 'LEFT'),
            ('ALIGN', (2, 0), (2, -1), 'RIGHT'),
            ('ALIGN', (3, 0), (3, -1), 'LEFT'),
        ]))

        # ============================================
        # CONFIGURACIÓN DE LÍNEA DE CORTE (Tijera)
        # ============================================
        CUT_LINE_CHAR = '‐'                     # Carácter para la línea (‐, -, ., •)
        CUT_LINE_REPEAT = 328                    # Número de repeticiones a cada lado
        CUT_LINE_SYMBOL = ' ✂️ '                # Símbolo central
        CUT_LINE_FONTSIZE = 1                   # Tamaño de letra
        CUT_LINE_COLOR = colors.HexColor('#a0aec0')  # Gris suave
        CUT_LINE_SPACING = -0.20 * inch          # Espacio arriba y abajo
        ESPACIO_ANTES_CORTE = CUT_LINE_SPACING

        cut_line_text = (CUT_LINE_CHAR * CUT_LINE_REPEAT) + CUT_LINE_SYMBOL + (CUT_LINE_CHAR * CUT_LINE_REPEAT)
        cut_line = Paragraph(cut_line_text,
                             style('cutline',
                                   fontSize=CUT_LINE_FONTSIZE,
                                   alignment=TA_CENTER,
                                   textColor=CUT_LINE_COLOR))

        # ============================================
        # CONFIGURACIÓN DE ESPACIOS ALREDEDOR DE LA LÍNEA DE CORTE
        # ============================================
        ### agregado 0610262
                # ── CÓDIGO QR EN ETIQUETA ─────────────────────────────────────────────
        ### qr_url = f"https://rdeluna.pythonanywhere.com/dashboard/?tab=digital&q={operation.custom_id}" ### ELIMINADA 061026
        qr_url = f"https://rdeluna.pythonanywhere.com/?tab=digital&q={operation.custom_id}"  ### NUEVA 061026
        qr_image = generate_qr_code(qr_url, size=35)

        footer_qr_row = Table([
            [
                Paragraph(f'DYSER GROUP LLC · {operation.custom_id} · Bundle {bundle_num}/{qty}',
                          style('footer', fontSize=6, alignment=TA_LEFT, textColor=colors.HexColor('#94a3b8'))),
                qr_image,
            ]
        ], colWidths=[label_width - 0.6*inch, 0.5*inch])
        footer_qr_row.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('ALIGN', (0, 0), (0, 0), 'LEFT'),
            ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
        ]))

                # ── CÓDIGO QR EN ETIQUETA ─────────────────────────────────────────────
        qr_url = f"https://rdeluna.pythonanywhere.com/mobile/?tab=digital&q={operation.custom_id}"
        qr_image = generate_qr_code(qr_url, size=30)

        # Fila con footer + QR (reemplaza el footer simple)
        footer_qr_row = Table([
            [
                Paragraph(f'DYSER GROUP LLC · {operation.custom_id} · Bundle {bundle_num}/{qty}',
                          style('footer', fontSize=6, alignment=TA_LEFT, textColor=colors.HexColor('#94a3b8'))),
                qr_image,
            ]
        ], colWidths=[label_width - 0.6*inch, 0.5*inch])
        footer_qr_row.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('ALIGN', (0, 0), (0, 0), 'LEFT'),
            ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ]))

        label_container = Table([
            [header_table],
            [Spacer(1, 0.05*inch)],
            [tabla_datos],                          # Datos
            [Spacer(1, -0.10*inch)],                # Espacio entre datos y footer
            [footer_qr_row],                        # ← Footer con QR (reemplaza el footer simple)
            [Spacer(1, ESPACIO_ANTES_CORTE)],       # Espacio entre footer y línea de corte
            [cut_line],                             # Línea de corte con tijera
        ], colWidths=[label_width])

        label_container.setStyle(TableStyle([
            ('PADDING', (0, 0), (-1, -1), 0),
        ]))

        story.append(label_container)

        # 3 etiquetas por página
        if bundle_num % 3 == 0 and bundle_num < qty:
            story.append(PageBreak())
        elif bundle_num < qty:
            story.append(Spacer(1, -0.09*inch))


    # Construcción del PDF
    doc.build(story)
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

# aqui copeo el nuevo codigo def generate_operations_report_pdf
def generate_operations_report_pdf(operations, title='Operations Report'):
    from reportlab.platypus import Image  # importación necesaria para el logo
    buffer = BytesIO()
    import sys
    print("!!! NUEVA FUNCIÓN LANDSCAPE (2026-05-23) ejecutándose", file=sys.stderr)

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



    # --- Logo (izquierda) ---
    logo_path = '/home/rdeluna/DYSWMS/media/Untitled-3.jpg'
    logo = None
    try:
        logo = Image(logo_path, width=1.0*inch, height=0.7*inch)
    except:
        pass

    page_width = landscape(letter)[0]
    available_width = page_width - doc.leftMargin - doc.rightMargin
    logo_width = 1.0*inch

    # Ajuste fino para centrar el título sobre el subtítulo
    shift = -2.00 * inch   # ← cámbialo según necesites
    logo_width += shift
    title_width = available_width - logo_width

    if logo:
        title_para = Paragraph(title, S('title', fontName='Helvetica-Bold', fontSize=16,
                                        textColor=DARK, alignment=1))
        header_table = Table([[logo, title_para]], colWidths=[logo_width, title_width])
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
    subtitle_text = f'Generated: {timezone.now().strftime("%Y-%m-%d %H:%M")}  ·  {len(operations)} records'
    subtitle_para = Paragraph(subtitle_text, S('sub', fontName='Helvetica', fontSize=9,
                                               textColor=MID, alignment=1))
    if logo:
        subtitle_table = Table([['', subtitle_para]], colWidths=[logo_width, title_width])
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
        'Date', 'Custom ID', 'Type','Status', 'Customer', 'Shipper',
        'Invoice', 'PO/Order', 'PRO', 'Description', 'Bundle Type', 'Bundle Qty', 'Weight LBS'
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
            if status_val == 'In Warehouse':
                status_abbr = 'WHS'
            elif status_val == 'Released Goods':
                status_abbr = 'RLS'
            else:
                status_abbr = '—'
        else:
            status_abbr = '—'

        rows.append([
            Paragraph(op.date.strftime('%Y-%m-%d'), S('cell', fontSize=7, textColor=DARK)),
            Paragraph(op.custom_id, S('cell', fontSize=7, textColor=DARK, fontName='Helvetica-Bold')),
            Paragraph(op.get_operation_type_display(), S('cell', fontSize=7, textColor=ENTRY_COL if op.operation_type == 'ENTRY' else EXIT_COL, fontName='Helvetica-Bold')),
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

