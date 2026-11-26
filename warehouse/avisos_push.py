"""
Avisos push: lo que suena en el telefono de los operadores.

Es Web Push, el estandar de los navegadores, y no una app de tienda ni un
servicio contratado. El navegador del operador se suscribe una vez (pantalla
«Mi cuenta» → «Activar avisos») y entrega una direccion del servicio de push de
su fabricante -- Google, Apple o Mozilla --; el servidor le manda ahi el aviso
cifrado y firmado con las llaves VAPID de `settings`. Sin llaves no se manda
nada y todo lo demas sigue igual.

Hoy hay tres eventos. Dos van a los operadores de la empresa:

- **Mensaje nuevo** de un cliente en el hilo de una operacion.
- **Operacion nueva** de un cliente.

Y uno a los usuarios del propio cliente:

- **Operacion registrada**: la suya, igual que el correo de alta.

Cada empresa decide en la ficha de cada cliente cuales suenan
(`Catalog.push_on_message`, `push_on_create` y `push_cliente_on_create`),
porque es algo que acuerda con cada uno: al que paga atencion inmediata se le
prenden.

Push es **un canal mas**, no el que reemplaza al correo: el servicio del
fabricante no garantiza la entrega, y un telefono con ahorro de bateria puede
recibirlo tarde. Por eso el correo de esos eventos sigue saliendo igual.
"""
import json
import logging

from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import PushSubscription, UserProfile

logger = logging.getLogger(__name__)

PUSH = 'PUSH'

# Cuanto se guarda el aviso en el servicio del fabricante si el telefono esta
# apagado. Un mensaje o una operacion de hace dos dias ya no es un aviso, es
# historia: para eso esta la pantalla.
TTL_SEGUNDOS = 12 * 60 * 60

# Lo que cabe en la notificacion sin que el telefono lo corte a media palabra.
EXTRACTO = 140

# Lo que los servicios contestan cuando la suscripcion ya no existe: el usuario
# quito el permiso, borro los datos del navegador o desinstalo la app.
YA_NO_EXISTE = (404, 410)


def push_disponible():
    """Si hay llaves VAPID puestas. Sin ellas no se manda nada."""
    return bool(getattr(settings, 'VAPID_PUBLIC_KEY', '')
                and getattr(settings, 'VAPID_PRIVATE_KEY', ''))


def contacto_vapid():
    """
    El contacto que exigen los servicios de push en cada envio, por si un dia
    tienen que avisar de un problema. Si no se puso uno, el remitente del
    sistema o la URL del sitio.
    """
    puesto = getattr(settings, 'VAPID_SUBJECT', '')
    if puesto:
        return puesto
    correo = getattr(settings, 'DEFAULT_FROM_EMAIL', '') or ''
    if '<' in correo:
        correo = correo.split('<', 1)[1].rstrip('>').strip()
    if '@' in correo:
        return 'mailto:' + correo
    sitio = getattr(settings, 'SITE_BASE_URL', '') or ''
    return sitio if sitio.startswith('https://') else 'mailto:soporte@example.com'


def operadores_de(tenant, excepto=None):
    """
    Los operadores activos de la empresa, con el idioma de su perfil.

    `excepto` es quien provoco el aviso: el que registra la operacion ya sabe
    que la registro, y que le suene su propio telefono es ruido.
    """
    if tenant is None:
        return []
    perfiles = (UserProfile.objects
                .filter(tenant=tenant, user__is_active=True)
                .exclude(role='customer')
                .select_related('user'))
    if excepto is not None and getattr(excepto, 'pk', None):
        perfiles = perfiles.exclude(user=excepto)
    return [(p.user, p.language or '') for p in perfiles if p.is_operator()]


def ruta_de_la_operacion(operation, es_movil):
    """A donde lleva el aviso al tocarlo: el expediente de esa operacion."""
    base = '/mobile/' if es_movil else '/dashboard/'
    return f'{base}?tab=digital&q={operation.custom_id}'


def _enviar_uno(suscripcion, datos):
    """
    Manda un aviso a un dispositivo. Devuelve `None` si salio, o el texto del
    error. Si el servicio dice que la suscripcion ya no existe, se borra.
    """
    from pywebpush import WebPushException, webpush

    try:
        webpush(
            subscription_info={
                'endpoint': suscripcion.endpoint,
                'keys': {'p256dh': suscripcion.p256dh, 'auth': suscripcion.auth},
            },
            data=json.dumps(datos),
            vapid_private_key=settings.VAPID_PRIVATE_KEY,
            # Un dict nuevo en cada envio: `webpush` le agrega `aud` y `exp`, y
            # con uno compartido el segundo dispositivo heredaria el `aud` del
            # primero, que puede ser de otro fabricante.
            vapid_claims={'sub': contacto_vapid()},
            ttl=TTL_SEGUNDOS,
            timeout=10,
        )
    except WebPushException as e:
        codigo = getattr(getattr(e, 'response', None), 'status_code', None)
        if codigo in YA_NO_EXISTE:
            suscripcion.delete()
            return None
        return f'{codigo or ""} {e}'.strip()
    except Exception as e:
        return str(e)

    suscripcion.last_ok_at = timezone.now()
    suscripcion.save(update_fields=['last_ok_at'])
    return None


def mandar(destinatarios, componer):
    """
    Manda el aviso a todos los dispositivos de `destinatarios`.

    `destinatarios` es una lista de `(usuario, idioma)`, y `componer(idioma,
    es_movil)` devuelve el dict del aviso: `title`, `body`, `tag` y `url`.
    Se compone por dispositivo porque el idioma es de la persona y la pantalla
    a la que lleva depende de si es el telefono o la computadora.

    Devuelve `(a_quien_llego, errores)`: los nombres de usuario a los que les
    salio al menos uno, y los errores que hubo.
    """
    from .notifications import en_idioma

    idioma_de = {u.pk: idioma for u, idioma in destinatarios}
    suscripciones = (PushSubscription.objects
                     .filter(user_id__in=list(idioma_de))
                     .select_related('user'))

    llego, errores = [], []
    for s in suscripciones:
        with en_idioma(idioma_de.get(s.user_id, '')):
            datos = componer(idioma_de.get(s.user_id, ''), s.es_movil)
        error = _enviar_uno(s, datos)
        if error:
            errores.append(error)
            logger.warning('Push a %s fallo: %s', s.user, error)
        elif s.user.username not in llego and s.pk is not None:
            llego.append(s.user.username)
    return llego, errores


def _avisar_y_anotar(operation, customer, evento, destinatarios, componer,
                     titulo_para_la_bitacora, triggered_by=None,
                     sin_nadie='no_operators'):
    """Manda y deja su renglon en la bitacora de avisos, salga o no."""
    from .notifications import FAILED, SENT, SKIPPED, log_notification

    if not destinatarios:
        log_notification(operation, customer, PUSH, evento, SKIPPED,
                         subject=titulo_para_la_bitacora, detail=sin_nadie,
                         triggered_by=triggered_by)
        return False
    usuarios = [u.pk for u, _idioma in destinatarios]
    if not PushSubscription.objects.filter(user_id__in=usuarios).exists():
        log_notification(operation, customer, PUSH, evento, SKIPPED,
                         recipient=', '.join(u.username for u, _i in destinatarios),
                         subject=titulo_para_la_bitacora, detail='no_devices',
                         triggered_by=triggered_by)
        return False

    llego, errores = mandar(destinatarios, componer)
    if llego:
        log_notification(operation, customer, PUSH, evento, SENT,
                         recipient=', '.join(llego),
                         subject=titulo_para_la_bitacora,
                         detail='; '.join(errores[:3]), triggered_by=triggered_by)
        return True
    log_notification(operation, customer, PUSH, evento,
                     FAILED if errores else SKIPPED,
                     subject=titulo_para_la_bitacora,
                     detail='; '.join(errores[:3]) or 'no_devices',
                     triggered_by=triggered_by)
    return False


def avisar_operacion_nueva(operation, triggered_by=None):
    """Push a los operadores de que un cliente tiene una operacion nueva."""
    from .notifications import (EVENT_CREATED, build_subject,
                                resolve_customer)
    from .utils import nombre_corto

    if not push_disponible():
        return False
    customer = resolve_customer(operation)
    if customer is None or not customer.push_on_create:
        return False

    quien = nombre_corto(customer.name)

    def componer(_idioma, es_movil):
        return {
            'title': f"{_('New operation')} · {quien}",
            'body':  build_subject(operation),
            'tag':   f'op-{operation.pk}',
            'url':   ruta_de_la_operacion(operation, es_movil),
        }

    return _avisar_y_anotar(
        operation, customer, EVENT_CREATED,
        operadores_de(operation.tenant, excepto=triggered_by), componer,
        f'{quien} · {operation.custom_id}', triggered_by=triggered_by)


def usuarios_del_cliente(customer):
    """
    Los usuarios activos de ese cliente, con su idioma: el de su perfil, y si
    no eligio, el de la ficha del cliente, que es en el que le llegan los
    correos.
    """
    if customer is None:
        return []
    perfiles = (UserProfile.objects
                .filter(customer=customer, role='customer', user__is_active=True)
                .select_related('user'))
    idioma_de_la_ficha = getattr(customer, 'language', '') or ''
    return [(p.user, p.language or idioma_de_la_ficha) for p in perfiles]


def avisar_al_cliente_operacion(operation, triggered_by=None):
    """
    Push a los usuarios del cliente de que se registro una operacion suya.

    Es el mismo aviso que ya le llega por correo, en corto. Sale solo si la
    empresa se lo prendio en su ficha (`push_cliente_on_create`).
    """
    from .notifications import EVENT_CREATED, nombre_del_tipo, resolve_customer
    from .utils import nombre_corto

    if not push_disponible():
        return False
    customer = resolve_customer(operation)
    if customer is None or not customer.push_cliente_on_create:
        return False

    empresa = nombre_corto(operation.tenant.name) if operation.tenant else 'WMS'

    def componer(_idioma, es_movil):
        partes = [nombre_del_tipo(operation.operation_type), operation.custom_id]
        if operation.po_order:
            partes.append(f'PO {operation.po_order}')
        return {
            'title': f"{_('Operation registered')} · {empresa}",
            'body':  ' · '.join(p for p in partes if p),
            'tag':   f'op-{operation.pk}',
            'url':   ruta_de_la_operacion(operation, es_movil),
        }

    return _avisar_y_anotar(
        operation, customer, EVENT_CREATED, usuarios_del_cliente(customer),
        componer, f'{_("Customer")}: {nombre_corto(customer.name)} · {operation.custom_id}',
        triggered_by=triggered_by, sin_nadie='no_customer_users')


def avisar_mensaje_del_cliente(conversation, mensaje=None, triggered_by=None):
    """
    Push a los operadores de que un cliente escribio en el hilo.

    No espera los 15 minutos del correo (`AVISO_ESPERA`): esa espera esta para
    no llenar el buzon con un correo por mensaje, y aqui no hace falta porque
    los avisos de un mismo hilo comparten `tag` y el telefono reemplaza el
    anterior en vez de apilarlos.
    """
    from .notifications import EVENT_MESSAGE, resolve_customer
    from .utils import nombre_corto

    if not push_disponible():
        return False
    operation = conversation.operation
    customer = resolve_customer(operation)
    if customer is None or not customer.push_on_message:
        return False

    quien = nombre_corto(customer.name)
    texto = (mensaje.body or '').strip() if mensaje else ''
    if len(texto) > EXTRACTO:
        texto = texto[:EXTRACTO].rstrip() + '…'

    def componer(_idioma, es_movil):
        cuerpo = texto
        if not cuerpo and mensaje is not None:
            nombres = [a.digital_name or a.original_name
                       for a in mensaje.adjuntos.all()]
            cuerpo = ('📎 ' + ', '.join(nombres)) if nombres else ''
        return {
            'title': f"{_('New message')} · {quien}",
            'body':  f'{operation.custom_id}: {cuerpo}' if cuerpo else operation.custom_id,
            'tag':   f'chat-{operation.pk}',
            'url':   ruta_de_la_operacion(operation, es_movil),
        }

    return _avisar_y_anotar(
        operation, customer, EVENT_MESSAGE,
        operadores_de(operation.tenant, excepto=triggered_by), componer,
        f'{quien} · {operation.custom_id}', triggered_by=triggered_by)


def mandar_prueba(usuario):
    """
    Un aviso de prueba a los dispositivos de quien lo pide, para comprobar que
    su telefono los recibe. Devuelve `(a_quien_llego, errores)`.
    """
    perfil = UserProfile.objects.filter(user=usuario).first()

    def componer(_idioma, es_movil):
        return {
            'title': _('Test notification'),
            'body':  _('If you can read this, notifications work on this device.'),
            'tag':   'prueba',
            'url':   '/mobile/' if es_movil else '/dashboard/',
        }

    return mandar([(usuario, (perfil.language if perfil else '') or '')], componer)
