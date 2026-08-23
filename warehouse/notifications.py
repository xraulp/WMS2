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

from django.conf import settings
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from django.utils import timezone

from .models import Catalog, NotificationLog, UserProfile
from .utils import (datos_del_emisor, generar_pdf_factura,
                    generate_pdf_report, operation_digital_url)

logger = logging.getLogger(__name__)

# Canales
EMAIL    = 'EMAIL'
WHATSAPP = 'WHATSAPP'

# Eventos
EVENT_CREATED   = 'OPERATION_CREATED'
EVENT_RELEASED  = 'GOODS_RELEASED'
EVENT_DOCUMENTS = 'DOCUMENTS_ADDED'
EVENT_MANUAL    = 'MANUAL'

# Estados
SENT    = 'SENT'
FAILED  = 'FAILED'
SKIPPED = 'SKIPPED'


# ── RESOLUCIÓN DE CLIENTE Y DESTINATARIOS ─────────────────────────────────────

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
    op_type = 'Recepcion de Mercancias' if operation.operation_type == 'ENTRY' else 'Salida de Mercancias'
    parts.append(op_type)
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
        titulo = 'Mercancia liberada'
    elif event == EVENT_DOCUMENTS:
        titulo = 'Documentos nuevos'
    else:
        titulo = ('Recep de Mercancias' if operation.operation_type == 'ENTRY'
                  else 'Salida de Mercancias')

    return (f"*{tenant_name.upper()} — {titulo}*\n"
            f"ID: {operation.custom_id}\n"
            f"Date: {operation.date}\n"
            f"Customer: {operation.get_customer_display()}\n"
            f"Shipper: {operation.get_shipper_display()}\n"
            f"Po/Order: {operation.po_order or '—'}\n"
            f"Description: {operation.description or '—'}\n"
            f"Bundles: {operation.bundle_qty or '—'}\n"
            f"Weight: {operation.weight_lbs or '—'} LBS")


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
        html_body = render_to_string('warehouse/email/report_email.html',
                                     {'operation': operation, 'message_body': message_body})
        pdf = None
        try:
            pdf = generate_pdf_report(operation)
        except Exception as e:
            logger.warning('No se pudo generar el PDF de %s: %s', operation.custom_id, e)
        email_sent, email_error = _deliver_email(
            operation, customer, EVENT_CREATED, email_recipients(customer),
            subject, html_body, pdf=pdf, attach_documents=True,
            triggered_by=triggered_by)
        if email_sent:
            mark_email_sent(operation)

    wants_wa = bool(customer and customer.wants_notification(WHATSAPP, EVENT_CREATED))
    if force_whatsapp or wants_wa:
        _deliver_whatsapp(operation, customer, EVENT_CREATED,
                          whatsapp_number(customer),
                          _whatsapp_body(operation, EVENT_CREATED),
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
    subject = f"{customer.name} | {entry_op.tenant.name if entry_op.tenant else 'WMS'} | Mercancia liberada | {entry_op.custom_id}"

    sent = False
    if customer.wants_notification(EMAIL, EVENT_RELEASED):
        recipients = [a for a in email_recipients(customer) if a.lower() not in already]
        if not recipients and already:
            log_notification(entry_op, customer, EMAIL, EVENT_RELEASED, SKIPPED,
                             subject=subject, detail='already_notified',
                             triggered_by=triggered_by)
        else:
            sent, _ = _deliver_email(
                entry_op, customer, EVENT_RELEASED, recipients, subject,
                _event_email_body(entry_op, EVENT_RELEASED, extra),
                triggered_by=triggered_by)

    if customer.wants_notification(WHATSAPP, EVENT_RELEASED):
        _deliver_whatsapp(entry_op, customer, EVENT_RELEASED,
                          whatsapp_number(customer),
                          _whatsapp_body(entry_op, EVENT_RELEASED),
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
    subject = (f"{customer.name} | {operation.tenant.name if operation.tenant else 'WMS'} | "
               f"Documentos nuevos | {operation.custom_id}")

    sent = False
    if customer.wants_notification(EMAIL, EVENT_DOCUMENTS):
        sent, _ = _deliver_email(
            operation, customer, EVENT_DOCUMENTS, email_recipients(customer),
            subject, _event_email_body(operation, EVENT_DOCUMENTS, extra),
            triggered_by=triggered_by)

    if customer.wants_notification(WHATSAPP, EVENT_DOCUMENTS):
        _deliver_whatsapp(operation, customer, EVENT_DOCUMENTS,
                          whatsapp_number(customer),
                          _whatsapp_body(operation, EVENT_DOCUMENTS),
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
    return _deliver_whatsapp(operation, customer, EVENT_MANUAL,
                             whatsapp_number(customer),
                             _whatsapp_body(operation, EVENT_CREATED),
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
        return False, ('%s no tiene correo de facturación. Se pone al crear la '
                       'empresa o en su ficha.' % factura.tenant.name)

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
        return False, 'No se pudo enviar: %s' % e

    log_notification(None, None, EMAIL, 'INVOICE_SENT', SENT,
                     recipient=destino, subject=asunto,
                     triggered_by=triggered_by, tenant=factura.tenant)

    factura.enviada_el = timezone.now()
    factura.save(update_fields=['enviada_el'])
    return True, destino
