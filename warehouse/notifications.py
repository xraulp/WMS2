"""
Envío de avisos al cliente (Tenant nivel 2) con bitácora de lo que se envió.

Antes esto vivía suelto dentro de `views.py`: el correo del alta salía siempre,
el WhatsApp dependía de un checkbox y ninguno de los dos dejaba constancia —
`_send_whatsapp` incluso se tragaba la excepción entera, así que un envío
fallido era indistinguible de uno exitoso. Aquí todo pasa por el mismo camino:
se resuelve el cliente, se consultan sus preferencias, se envía, y salga bien o
mal queda un renglón en `NotificationLog`.
"""
import functools
import logging
import threading
from datetime import timedelta

from django import db
from django.conf import settings
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils import translation
from django.utils.translation import gettext as _

from .models import (Catalog, NotificationLog, UserProfile,
                     LADO_TENANT, LADO_CLIENTE)
from .utils import (datos_del_emisor, generar_pdf_factura,
                    generate_pdf_report, nombre_corto, operation_digital_url)

logger = logging.getLogger(__name__)

# Canales
EMAIL    = 'EMAIL'
WHATSAPP = 'WHATSAPP'

# Eventos
EVENT_CREATED   = 'OPERATION_CREATED'
EVENT_RELEASED  = 'GOODS_RELEASED'
EVENT_DOCUMENTS = 'DOCUMENTS_ADDED'
EVENT_MANUAL    = 'MANUAL'
EVENT_MESSAGE   = 'CHAT_MESSAGE'

# Estados
SENT    = 'SENT'
FAILED  = 'FAILED'
SKIPPED = 'SKIPPED'


# ── EL IDIOMA DE QUIEN LO RECIBE ──────────────────────────────────────────────
# Un correo y un PDF no se escriben en el idioma de quien los manda, sino en el
# del que los lee. El operador que captura una entrada a las tres de la manana
# no tiene por que acordarse de en que idioma habla cada cliente, asi que el
# idioma vive en la ficha del cliente y esto lo aplica al componer.


def idioma_del_cliente(customer):
    """El idioma de la ficha, o cadena vacia para el de la casa."""
    return getattr(customer, 'language', '') or ''


def en_idioma(codigo):
    """
    Context manager que compone un texto en el idioma pedido.

    Vacio significa el idioma de la casa, no "el que este puesto": si se deja
    correr el de la peticion, un correo escrito por un operador que trabaja en
    ingles le llegaria en ingles a un cliente que no lo eligio.
    """
    return translation.override(codigo or settings.LANGUAGE_CODE)


def en_el_idioma_de(customer):
    """
    Lo mismo, para el cliente al que se le escribe.

    Envuelve la composicion --el asunto, el cuerpo y el PDF adjunto--, no el
    envio: para cuando el envio ocurre, el idioma puesto es el del usuario que
    apreto el boton, y el correo saldria mitad en uno y mitad en otro.
    """
    return en_idioma(idioma_del_cliente(customer))


# ── RESOLUCIÓN DE CLIENTE Y DESTINATARIOS ─────────────────────────────────────

# Como se llama cada tipo de operacion en un correo o un WhatsApp, que es donde
# lo lee el cliente. Antes era un `if` de dos ramas: cualquier tipo que no fuera
# ENTRY se anunciaba como "Salida de Mercancias", de modo que una revision --que
# no saca nada de la bodega-- le habria dicho al cliente que su carga se fue.
NOMBRE_DEL_TIPO = {
    'ENTRY': ('Goods Receipt', 'Goods Receipt'),
    'EXIT':  ('Goods Dispatch', 'Goods Dispatch'),
    'TD':    ('Transfer', 'Transfer'),
    'RD':    ('Goods Inspection', 'Inspection'),
}


def nombre_del_tipo(operation_type, corto=False):
    # Se traduce al llamar y no al definir el diccionario: el idioma que manda
    # es el del cliente al que se le escribe, y ese no se sabe al importar.
    largo, breve = NOMBRE_DEL_TIPO.get(operation_type, ('Operation', 'Operation'))
    return _(breve) if corto else _(largo)


def resolve_customer(operation):
    """
    Entrada de catálogo del cliente de la operación.

    La operación puede apuntar al catálogo por FK o traer solo el nombre escrito
    a mano; en ese segundo caso se busca por nombre **dentro del tenant**. El
    `get_customer_email_raw()` del modelo hace esa misma búsqueda sin filtrar por
    tenant, lo que podía sacar el correo de un cliente homónimo de otra empresa.
    """
    if operation.customer_id:
        return operation.customer
    name = (operation.customer_name_manual or '').strip()
    if not name:
        return None
    return Catalog.objects.filter(
        tenant=operation.tenant, category='CUSTOMER',
        name__iexact=name, active=True).first()


def email_recipients(customer):
    """
    Correos del cliente: los del catálogo más los de sus usuarios con login.

    El alta de cliente nivel 2 (`create_customer`) crea un `User` con su propio
    correo; hasta ahora ese correo no recibía nada porque el envío miraba solo
    `contact_email`. Se devuelven en orden y sin repetidos, comparando sin
    distinguir mayúsculas.
    """
    if not customer:
        return []

    recipients, seen = [], set()

    def add(addr):
        addr = (addr or '').strip()
        if addr and addr.lower() not in seen:
            seen.add(addr.lower())
            recipients.append(addr)

    for addr in (customer.contact_email or '').split(','):
        add(addr)

    profiles = UserProfile.objects.filter(
        customer=customer, role='customer', user__is_active=True
    ).select_related('user')
    for profile in profiles:
        add(profile.user.email)

    return recipients


def whatsapp_number(customer):
    return (customer.whatsapp or '').strip() if customer else ''


def get_cc_emails(tenant):
    cc = []
    for e in Catalog.objects.filter(category='CC_EMAIL', active=True, tenant=tenant).exclude(
            contact_email__isnull=True).exclude(contact_email=''):
        for addr in e.contact_email.split(','):
            addr = addr.strip()
            if addr:
                cc.append(addr)
    return cc


def build_subject(operation):
    parts = []
    customer = operation.get_customer_display()
    if customer and customer != '—':
        parts.append(customer)
    if operation.po_order:
        parts.append(f"PO: {operation.po_order}")
    parts.append(operation.tenant.name if operation.tenant else 'WMS')
    parts.append(nombre_del_tipo(operation.operation_type))
    if operation.custom_id:
        parts.append(str(operation.custom_id))
    return ' | '.join(parts)


# ── BLINDAJE ──────────────────────────────────────────────────────────────────

def _never_breaks(default):
    """
    Un fallo avisando no puede tumbar lo que se estaba registrando.

    Guardar la operación es lo importante; el aviso es secundario. El error se
    loguea entero (con traza) y se devuelve como resultado para que la vista lo
    pinte, pero nunca sube hasta convertirse en un 500 que le borre el trabajo
    al operador.

    `default` puede ser un valor o una función que recibe la excepción.
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as e:
                logger.exception('Fallo inesperado notificando en %s', fn.__name__)
                return default(e) if callable(default) else default
        return wrapper
    return decorator


# ── FUERA DE LA PETICIÓN ──────────────────────────────────────────────────────

def en_segundo_plano(fn, *args, **kwargs):
    """
    Manda un aviso sin que la pantalla lo espere.

    Se midió con el correo tal como está hoy: escribir un mensaje del chat
    costaba **diez segundos justos** —el `EMAIL_TIMEOUT`— porque el envío iba
    dentro de la petición y el hilo no se repintaba hasta que el servidor de
    correo terminaba de no contestar. Y no era solo el primer mensaje: la espera
    de 15 minutos que calla los avisos seguidos solo cuenta cuando el envío salió
    bien, así que cada mensaje volvía a intentarlo y volvía a costar diez
    segundos.

    **No sirve para todos los avisos.** El alta de una operación le dice al
    operador en pantalla si el correo salió o falló; mandarlo aparte convertiría
    ese aviso en una mentira. Esto es para los envíos cuyo resultado no se está
    mirando —hoy, el del chat—, donde lo único que la espera consigue es que la
    pantalla se quede quieta.

    El hilo **no** es `daemon`: si el servidor se está apagando, conviene que
    espere a que el aviso salga en vez de matarlo a medias. La conexión a la
    base se cierra al terminar porque cada hilo abre la suya, y dejarlas
    abiertas es quedarse sin cupo en el servidor de base de datos.
    """
    if not getattr(settings, 'AVISOS_EN_HILO', True):
        return fn(*args, **kwargs)

    # No todo lo que se puede llamar tiene nombre: un `partial` no lo tiene, y
    # un doble de prueba tampoco. El nombre es para leer un log y un volcado de
    # hilos, asi que no puede ser lo que reviente el aviso.
    como_se_llama = getattr(fn, '__name__', repr(fn))

    def tarea():
        try:
            fn(*args, **kwargs)
        except Exception:
            logger.exception('Fallo avisando en segundo plano desde %s', como_se_llama)
        finally:
            db.connections.close_all()

    hilo = threading.Thread(target=tarea, name='aviso-%s' % como_se_llama)
    hilo.start()
    return hilo


# ── BITÁCORA ──────────────────────────────────────────────────────────────────

def log_notification(operation, customer, channel, event, status,
                     recipient='', subject='', detail='', triggered_by=None,
                     tenant=None):
    """
    Deja constancia de un envío. Nunca propaga: una bitácora rota no puede
    impedir que se registre la operación que la originó.

    `tenant` se pasa a mano en lo que no nace de una operación —hoy, la factura
    que la plataforma le manda a una empresa—. Sin él, ese renglón quedaría sin
    empresa y no se podría filtrar en la bitácora de envíos, que es para lo que
    se mira.
    """
    try:
        return NotificationLog.objects.create(
            tenant=(getattr(operation, 'tenant', None) if operation else None) or tenant,
            operation=operation,
            operation_custom_id=(operation.custom_id or '') if operation else '',
            customer=customer,
            channel=channel,
            event=event,
            status=status,
            recipient=(recipient or '')[:500],
            subject=(subject or '')[:300],
            detail=detail or '',
            triggered_by=triggered_by if (triggered_by and triggered_by.pk) else None,
        )
    except Exception:
        logger.exception('No se pudo registrar la notificacion en NotificationLog')
        return None


# ── ENVÍO ─────────────────────────────────────────────────────────────────────

# Tope por archivo adjunto. Los expedientes traen fotos y hasta video, y un
# correo de 40 MB lo rechaza cualquier servidor; además leerlo entero en memoria
# en un plan chico es la forma rápida de quedarse sin RAM.
MAX_ATTACHMENT_MB = 5


def _attach_documents(email, operation):
    """
    Adjunta los archivos del expediente leyéndolos del storage.

    La versión anterior usaba `attach_file(doc.file.path)`, y `path` **no existe
    cuando los archivos viven en R2**: lanzaba NotImplementedError que un
    `except: pass` se tragaba. Resultado: desde la mudanza a R2 los correos
    salían sin los documentos y nadie se enteró. Hay que leer el contenido, que
    es lo que funciona con cualquier backend de almacenamiento.
    """
    limite = getattr(settings, 'EMAIL_MAX_ATTACHMENT_MB', MAX_ATTACHMENT_MB) * 1024 * 1024

    for doc in operation.documents.all():
        nombre = doc.original_name or (doc.file.name or '').rsplit('/', 1)[-1]
        try:
            if doc.file.size > limite:
                logger.info('Documento %s omitido del correo por tamaño (%s bytes)',
                            doc.pk, doc.file.size)
                continue
            doc.file.open('rb')
            try:
                contenido = doc.file.read()
            finally:
                doc.file.close()
            email.attach(nombre, contenido)
        except Exception:
            logger.warning('No se pudo adjuntar el documento %s (%s)', doc.pk, nombre)

def _deliver_email(operation, customer, event, recipients, subject, html_body,
                   pdf=None, attach_documents=False, triggered_by=None):
    """Manda el correo y registra el resultado. Devuelve (enviado, error)."""
    if not recipients:
        log_notification(operation, customer, EMAIL, event, SKIPPED,
                         subject=subject, detail='no_recipient', triggered_by=triggered_by)
        return False, 'no_email'

    joined = ', '.join(recipients)
    try:
        email = EmailMessage(
            subject=subject,
            body=html_body,
            to=recipients,
            cc=get_cc_emails(operation.tenant) if operation else [],
        )
        email.content_subtype = 'html'
        if pdf is not None:
            email.attach(f'{operation.custom_id}.pdf', pdf, 'application/pdf')
        if attach_documents and operation:
            _attach_documents(email, operation)
        email.send()
    except Exception as e:
        log_notification(operation, customer, EMAIL, event, FAILED,
                         recipient=joined, subject=subject, detail=str(e),
                         triggered_by=triggered_by)
        logger.warning('Fallo el envio de correo de %s: %s',
                       getattr(operation, 'custom_id', '—'), e)
        return False, str(e)

    log_notification(operation, customer, EMAIL, event, SENT,
                     recipient=joined, subject=subject, triggered_by=triggered_by)
    return True, None


def _deliver_whatsapp(operation, customer, event, number, body, triggered_by=None):
    """Manda el WhatsApp y registra el resultado. Devuelve (enviado, error)."""
    if not number:
        log_notification(operation, customer, WHATSAPP, event, SKIPPED,
                         detail='no_recipient', triggered_by=triggered_by)
        return False, 'no_number'

    sid   = getattr(settings, 'TWILIO_ACCOUNT_SID', '')
    token = getattr(settings, 'TWILIO_AUTH_TOKEN', '')
    from_ = getattr(settings, 'TWILIO_WHATSAPP_FROM', '')
    if not (sid and token and from_):
        # Antes esto era un `return` silencioso: quien miraba la pantalla creía
        # que el mensaje había salido.
        log_notification(operation, customer, WHATSAPP, event, SKIPPED,
                         recipient=number, detail='twilio_not_configured',
                         triggered_by=triggered_by)
        return False, 'twilio_not_configured'

    try:
        from twilio.rest import Client
        Client(sid, token).messages.create(
            body=body, from_=from_, to=f'whatsapp:{number}')
    except Exception as e:
        log_notification(operation, customer, WHATSAPP, event, FAILED,
                         recipient=number, detail=str(e), triggered_by=triggered_by)
        # `twilio` faltó meses en producción y el `except: pass` de la versión
        # anterior escondió el ModuleNotFoundError todo ese tiempo.
        logger.warning('Fallo el envio de WhatsApp de %s: %s',
                       getattr(operation, 'custom_id', '—'), e)
        return False, str(e)

    log_notification(operation, customer, WHATSAPP, event, SENT,
                     recipient=number, triggered_by=triggered_by)
    return True, None


# ── CUERPOS DE MENSAJE ────────────────────────────────────────────────────────

def _whatsapp_body(operation, event):
    tenant_name = operation.tenant.name if operation.tenant else 'WMS'
    if event == EVENT_RELEASED:
        titulo = _('Goods released')
    elif event == EVENT_DOCUMENTS:
        titulo = _('New documents')
    else:
        titulo = nombre_del_tipo(operation.operation_type, corto=True)

    return (f"*{tenant_name.upper()} — {titulo}*\n"
            f"{_('ID')}: {operation.custom_id}\n"
            f"{_('Date')}: {operation.date}\n"
            f"{_('Customer')}: {operation.get_customer_display()}\n"
            f"{_('Shipper')}: {operation.get_shipper_display()}\n"
            f"{_('PO / Order')}: {operation.po_order or '—'}\n"
            f"{_('Description')}: {operation.description or '—'}\n"
            f"{_('Bundles')}: {operation.bundle_qty or '—'}\n"
            f"{_('Weight')}: {operation.weight_lbs or '—'} LBS")


def _event_email_body(operation, event, extra=None):
    return render_to_string('warehouse/email/event_email.html', {
        'operation': operation,
        'event': event,
        'tenant_name': operation.tenant.name if operation.tenant else 'WMS',
        'digital_url': operation_digital_url(operation, '/dashboard/'),
        'extra': extra or {},
    })


# ── EVENTOS ───────────────────────────────────────────────────────────────────

@_never_breaks(lambda e: (False, str(e)))
def notify_operation_created(operation, triggered_by=None, message_body='',
                             force_whatsapp=False):
    """
    Aviso de que la operación quedó registrada, con el reporte en PDF adjunto.

    Devuelve `(email_enviado, error)` con el mismo contrato que tenía
    `_send_operation_email`, porque la vista de alta pinta el resultado.

    `force_whatsapp` es el checkbox del formulario: una orden explícita del
    operador, así que se manda aunque el cliente no tenga el canal activado en
    sus preferencias. Sin el checkbox, el WhatsApp sale solo si el cliente lo
    pidió.
    """
    customer = resolve_customer(operation)
    with en_el_idioma_de(customer):
        subject = build_subject(operation)

    email_sent, email_error = False, 'no_email'
    if customer is None:
        log_notification(operation, None, EMAIL, EVENT_CREATED, SKIPPED,
                         subject=subject, detail='customer_not_in_catalog',
                         triggered_by=triggered_by)
    elif not customer.wants_notification(EMAIL, EVENT_CREATED):
        log_notification(operation, customer, EMAIL, EVENT_CREATED, SKIPPED,
                         subject=subject, detail='preference_off',
                         triggered_by=triggered_by)
        email_error = 'preference_off'
    else:
        with en_el_idioma_de(customer):
            html_body = render_to_string(
                'warehouse/email/report_email.html',
                {'operation': operation, 'message_body': message_body})
            pdf = None
            try:
                pdf = generate_pdf_report(operation)
            except Exception as e:
                logger.warning('No se pudo generar el PDF de %s: %s',
                               operation.custom_id, e)
        email_sent, email_error = _deliver_email(
            operation, customer, EVENT_CREATED, email_recipients(customer),
            subject, html_body, pdf=pdf, attach_documents=True,
            triggered_by=triggered_by)
        if email_sent:
            mark_email_sent(operation)

    wants_wa = bool(customer and customer.wants_notification(WHATSAPP, EVENT_CREATED))
    if force_whatsapp or wants_wa:
        with en_el_idioma_de(customer):
            cuerpo_wa = _whatsapp_body(operation, EVENT_CREATED)
        _deliver_whatsapp(operation, customer, EVENT_CREATED,
                          whatsapp_number(customer),
                          cuerpo_wa,
                          triggered_by=triggered_by)

    return email_sent, email_error


@_never_breaks(False)
def notify_goods_released(entry_op, exit_op=None, triggered_by=None,
                          already_notified=None):
    """
    Aviso de que una entrada en almacén salió despachada.

    El cliente de la entrada liberada no siempre es el mismo que el de la salida
    que la despacha, así que este aviso se dirige al de la entrada. Cuando sí
    coinciden, `already_notified` evita mandarle dos correos por lo mismo: trae
    las direcciones a las que ya se escribió en esta misma operación.

    Nace apagado (`notify_on_release` es False por omisión) para no empezar a
    mandar correos que hoy nadie recibe sin que el cliente lo pida.
    """
    customer = resolve_customer(entry_op)
    if customer is None:
        return False

    already = {a.lower() for a in (already_notified or [])}
    extra = {'exit_op': exit_op}
    with en_el_idioma_de(customer):
        subject = (f"{customer.name} | "
                   f"{entry_op.tenant.name if entry_op.tenant else 'WMS'} | "
                   f"{_('Goods released')} | {entry_op.custom_id}")

    sent = False
    if customer.wants_notification(EMAIL, EVENT_RELEASED):
        recipients = [a for a in email_recipients(customer) if a.lower() not in already]
        if not recipients and already:
            log_notification(entry_op, customer, EMAIL, EVENT_RELEASED, SKIPPED,
                             subject=subject, detail='already_notified',
                             triggered_by=triggered_by)
        else:
            with en_el_idioma_de(customer):
                cuerpo = _event_email_body(entry_op, EVENT_RELEASED, extra)
            sent, _error = _deliver_email(
                entry_op, customer, EVENT_RELEASED, recipients, subject,
                cuerpo, triggered_by=triggered_by)

    if customer.wants_notification(WHATSAPP, EVENT_RELEASED):
        with en_el_idioma_de(customer):
            cuerpo_wa = _whatsapp_body(entry_op, EVENT_RELEASED)
        _deliver_whatsapp(entry_op, customer, EVENT_RELEASED,
                          whatsapp_number(customer),
                          cuerpo_wa,
                          triggered_by=triggered_by)
    return sent


@_never_breaks(False)
def notify_documents_added(operation, documents, triggered_by=None):
    """
    Aviso de que se subieron documentos nuevos al expediente de una operación.

    No se dispara cuando quien sube es el propio cliente: no tiene sentido
    avisarle de sus propios archivos.
    """
    if not documents:
        return False

    customer = resolve_customer(operation)
    if customer is None:
        return False

    names = [d.original_name or d.digital_name or '' for d in documents]
    extra = {'documents': names, 'count': len(names)}
    with en_el_idioma_de(customer):
        subject = (f"{customer.name} | "
                   f"{operation.tenant.name if operation.tenant else 'WMS'} | "
                   f"{_('New documents')} | {operation.custom_id}")

    sent = False
    if customer.wants_notification(EMAIL, EVENT_DOCUMENTS):
        with en_el_idioma_de(customer):
            cuerpo = _event_email_body(operation, EVENT_DOCUMENTS, extra)
        sent, _error = _deliver_email(
            operation, customer, EVENT_DOCUMENTS, email_recipients(customer),
            subject, cuerpo, triggered_by=triggered_by)

    if customer.wants_notification(WHATSAPP, EVENT_DOCUMENTS):
        with en_el_idioma_de(customer):
            cuerpo_wa = _whatsapp_body(operation, EVENT_DOCUMENTS)
        _deliver_whatsapp(operation, customer, EVENT_DOCUMENTS,
                          whatsapp_number(customer),
                          cuerpo_wa,
                          triggered_by=triggered_by)
    return sent


# ── ENVÍOS MANUALES (botones del detalle de la operación) ─────────────────────

@_never_breaks(lambda e: (False, str(e)))
def send_manual_email(operation, recipient, subject, message_body='', triggered_by=None):
    """
    Reenvío a mano desde el detalle. Es una orden explícita del operador, así que
    no consulta preferencias; sí queda registrada como cualquier otro envío.
    """
    html_body = render_to_string('warehouse/email/report_email.html',
                                 {'operation': operation, 'message_body': message_body})
    pdf = None
    try:
        pdf = generate_pdf_report(operation)
    except Exception as e:
        logger.warning('No se pudo generar el PDF de %s: %s', operation.custom_id, e)

    sent, error = _deliver_email(
        operation, resolve_customer(operation), EVENT_MANUAL, [recipient],
        subject, html_body, pdf=pdf, attach_documents=True,
        triggered_by=triggered_by)
    if sent:
        mark_email_sent(operation)
    return sent, error


@_never_breaks(lambda e: (False, str(e)))
def send_manual_whatsapp(operation, triggered_by=None):
    customer = resolve_customer(operation)
    with en_el_idioma_de(customer):
        cuerpo = _whatsapp_body(operation, EVENT_CREATED)
    return _deliver_whatsapp(operation, customer, EVENT_MANUAL,
                             whatsapp_number(customer), cuerpo,
                             triggered_by=triggered_by)


def mark_email_sent(operation):
    operation.email_sent = True
    operation.email_sent_at = timezone.now()
    operation.save(update_fields=['email_sent', 'email_sent_at'])


# ── FACTURACIÓN DE LA PLATAFORMA ──────────────────────────────────────────────

def enviar_factura(factura, triggered_by=None):
    """
    Manda la factura al correo de facturación de la empresa, con el PDF adjunto.

    Devuelve (enviado, motivo). El motivo es lo que se le enseña a quien pulsó
    el botón, así que dice qué falta en vez de "error".

    Queda registrado en la misma bitácora de envíos que los avisos a clientes.
    Es de otro nivel —lo manda la plataforma, no una empresa— pero la pregunta
    que se le hace a esa pantalla es la misma: «¿le llegó o no?». Tener dos
    sitios donde mirar es lo que hace que nadie mire en ninguno.
    """
    destino = (factura.tenant.billing_email or '').strip()
    if not destino:
        log_notification(None, None, EMAIL, 'INVOICE_SENT', SKIPPED,
                         subject=factura.numero, detail='no_billing_email',
                         triggered_by=triggered_by, tenant=factura.tenant)
        # Este no lo lee el destinatario sino quien pulso el boton, asi que va
        # en el idioma del usuario y no en el de la empresa facturada.
        return False, _('%(empresa)s has no billing email. It is set when the '
                        'company is created, or in its record.') % {
                            'empresa': factura.tenant.name}

    asunto = 'Invoice %s · %s' % (factura.numero, factura.tenant.name)
    cuerpo = render_to_string('warehouse/email/invoice_email.html', {
        'factura': factura,
        'emisor': datos_del_emisor(),
    })

    try:
        correo = EmailMessage(subject=asunto, body=cuerpo, to=[destino])
        correo.content_subtype = 'html'
        correo.attach('%s.pdf' % factura.numero,
                      generar_pdf_factura(factura), 'application/pdf')
        correo.send()
    except Exception as e:
        log_notification(None, None, EMAIL, 'INVOICE_SENT', FAILED,
                         recipient=destino, subject=asunto, detail=str(e),
                         triggered_by=triggered_by, tenant=factura.tenant)
        logger.warning('Fallo el envio de la factura %s: %s', factura.numero, e)
        return False, _('Could not send: %(error)s') % {'error': e}

    log_notification(None, None, EMAIL, 'INVOICE_SENT', SENT,
                     recipient=destino, subject=asunto,
                     triggered_by=triggered_by, tenant=factura.tenant)

    factura.enviada_el = timezone.now()
    factura.save(update_fields=['enviada_el'])
    return True, destino


# ── EL HILO DE LA OPERACIÓN ───────────────────────────────────────────────────

# Cuánto se calla el aviso después de haber avisado a ese lado. El correo es lo
# que hace que el chat exista —nadie se queda mirando la pantalla esperando—,
# pero un correo por mensaje convierte una conversación de diez líneas en diez
# correos y a la tercera vez el destinatario deja de abrirlos. Con esta espera
# se avisa del primer mensaje y se callan los siguientes mientras la
# conversación sigue viva; cuando se enfría, el siguiente mensaje vuelve a
# avisar.
AVISO_ESPERA = timedelta(minutes=15)

# Cuánto del mensaje se copia en el correo. Lo suficiente para saber si hay que
# atenderlo ahora; el resto se lee en el expediente, que es donde vive.
AVISO_EXTRACTO = 300


def correos_del_tenant(tenant):
    """
    A quién se avisa dentro de la empresa cuando escribe un cliente.

    A todos los operadores activos que tengan correo, no solo a los
    administradores: del lado de la empresa contesta quien esté en el turno, y
    dirigir el aviso a una sola persona es la forma de que un mensaje se quede
    esperando a que esa persona vuelva de vacaciones. Funciona como un buzón
    compartido, y por eso importa la espera de `AVISO_ESPERA`.
    """
    if not tenant:
        return []

    correos, vistos = [], set()
    perfiles = UserProfile.objects.filter(
        tenant=tenant, user__is_active=True
    ).exclude(role='customer').select_related('user')
    for perfil in perfiles:
        if not perfil.is_operator():
            continue
        addr = (perfil.user.email or '').strip()
        if addr and addr.lower() not in vistos:
            vistos.add(addr.lower())
            correos.append(addr)
    return correos


def _hay_que_avisar(conversation, campo, ahora):
    """Si toca avisar a ese lado o si todavía está dentro de la espera."""
    ultimo = getattr(conversation, campo, None)
    return not ultimo or (ahora - ultimo) >= AVISO_ESPERA


@_never_breaks(lambda e: (False, str(e)))
def avisar_mensaje_nuevo(conversation, lado, mensaje=None, triggered_by=None):
    """
    Avisa al otro lado de que hay un mensaje nuevo en el hilo.

    `lado` es de quién viene el mensaje, así que el aviso va al contrario: lo
    que escribe el cliente lo reciben los operadores de la empresa, y lo que
    escribe la empresa lo recibe el cliente por los mismos correos a los que ya
    se le mandan los avisos de sus operaciones.

    Cuando la espera todavía no se cumplió no se manda nada, pero **sí queda el
    renglón** en la bitácora con el motivo. Es a propósito: la pregunta que se
    le hace a esa pantalla es «¿por qué no me avisaron?», y un silencio sin
    registro no la responde.

    Devuelve `(enviado, error)`, igual que el resto de avisos.
    """
    operation = conversation.operation
    customer  = resolve_customer(operation)
    ahora     = timezone.now()

    if lado == LADO_CLIENTE:
        destinatarios = correos_del_tenant(operation.tenant)
        campo         = 'avisado_al_tenant_at'
        # El nombre de la empresa como se firma, no como esta en el acta: en el
        # asunto y en la primera linea del correo, «Customer Test, SA. de CV»
        # ocupa el ancho util sin decir mas que «Customer Test».
        quien         = nombre_corto(
            customer.name if customer else operation.get_customer_display())
    else:
        destinatarios = email_recipients(customer)
        campo         = 'avisado_al_cliente_at'
        quien         = nombre_corto(
            operation.tenant.name if operation.tenant else 'WMS')

    # El aviso va en dos direcciones y el idioma es el del que lee: si escribio
    # el cliente, lo recibe la empresa y manda el idioma de la casa; si escribio
    # la empresa, lo recibe el cliente y manda el suyo.
    lee_el_cliente = (lado != LADO_CLIENTE)
    idioma_aviso = idioma_del_cliente(customer) if lee_el_cliente else ''

    with en_idioma(idioma_aviso):
        subject = f"{_('New message')} | {build_subject(operation)}"

    if not _hay_que_avisar(conversation, campo, ahora):
        log_notification(operation, customer, EMAIL, EVENT_MESSAGE, SKIPPED,
                         recipient=', '.join(destinatarios), subject=subject,
                         detail='aviso_reciente', triggered_by=triggered_by)
        return False, 'aviso_reciente'

    with en_idioma(idioma_aviso):
        cuerpo = render_to_string('warehouse/email/chat_email.html', {
            'operation':   operation,
            'tenant_name': operation.tenant.name if operation.tenant else 'WMS',
            'de_quien':    quien,
            'extracto':    (mensaje.body[:AVISO_EXTRACTO] if mensaje else ''),
            'recortado':   bool(mensaje and len(mensaje.body) > AVISO_EXTRACTO),
            # La firma del correo lleva las dos: arriba ya se dijo que empresa
            # escribio, y aqui quien de esa empresa lo hizo.
            'firma':       ('%s · %s' % (quien, mensaje.author_name)
                            if mensaje and mensaje.author_name else ''),
            'digital_url': operation_digital_url(operation, '/dashboard/'),
            # Un mensaje puede ser solo un archivo, y entonces el extracto va
            # vacio: sin esto el aviso diria que alguien escribio y no ensenaria
            # nada. Van los nombres digitales, que son los que ese archivo va a
            # tener en el expediente y en el ZIP.
            'adjuntos':    ([a.digital_name or a.original_name
                             for a in mensaje.adjuntos.all()] if mensaje else []),
        })

    enviado, error = _deliver_email(
        operation, customer, EVENT_MESSAGE, destinatarios, subject, cuerpo,
        triggered_by=triggered_by)

    if enviado:
        setattr(conversation, campo, ahora)
        conversation.save(update_fields=[campo])

    return enviado, error
