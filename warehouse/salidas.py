"""
La salida (SD) que nace sola de una tarea de cruce.

La mercancia sale de bodega cuando se sube la foto de la mercancia cargada en
el transporte: es el unico momento en que alguien la tiene delante ya arriba
del camion. Ahi se genera el reporte de salida con lo que va en la tarea, se le
pegan las fotos de la carga y se le manda al cliente, sin que nadie tenga que
capturar la SD a mano juntando las entradas.

Se arma igual que la SD capturada a mano (`exit_entry_totals`): los bultos y
los pesos sumados, los pedidos y las descripciones unidos con " / ", y
"VARIOUS" cuando las entradas traen mas de un shipper. La diferencia es el
caso de 19 de 20: la SD lleva solo los bultos que cruzan, con el peso en la
misma proporcion, y la entrada que deja bultos en bodega no se da por
liberada.

Una tarea genera una sola SD. Las fotos que se suban despues se agregan al
expediente de esa misma SD, pero no vuelven a mandar el correo: para eso esta
el envio de documentos de entrega.
"""
import logging
import os
from decimal import Decimal, ROUND_HALF_UP

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from .models import CrossingTask, DocumentoDeEntrega, OperationDocument, WarehouseOperation

logger = logging.getLogger(__name__)

DOS_DECIMALES = Decimal('0.01')


def _proporcion(valor, van, de):
    """El peso de los bultos que van, si no van todos."""
    if valor is None:
        return None
    if not de or van >= de:
        return valor
    return (Decimal(valor) * van / de).quantize(DOS_DECIMALES, ROUND_HALF_UP)


def _unidos(textos):
    vistos = []
    for t in textos:
        t = (t or '').strip()
        if t and t not in vistos:
            vistos.append(t)
    return ' / '.join(vistos)


def _transporte(tarea):
    """
    El transporte mexicano que se lleva la carga: el de las remisiones de la
    tarea, o el que quedo en el camion si la remision aun no se emite.
    """
    remisiones = list(tarea.remisiones.order_by('emitida_en', 'pk')) \
        if hasattr(tarea, 'remisiones') else []
    fuentes = remisiones or ([tarea.camion] if tarea.camion_id else [])
    return (_unidos(f.transfer_empresa for f in fuentes),
            _unidos(f.transfer_unidad for f in fuentes),
            _unidos(f.sello for f in fuentes))


def armar_salida(tarea, usuario):
    """
    Crea la SD de la tarea y devuelve `(salida, entradas_liberadas)`.

    Si la tarea ya tiene su SD no crea otra y devuelve `(None, [])`. Se bloquea
    la tarea mientras tanto: dos fotos subidas a la vez no hacen dos salidas.
    """
    with transaction.atomic():
        tarea = (CrossingTask.objects.select_for_update()
                 .get(pk=tarea.pk))
        if tarea.salida_id:
            return None, []

        renglones = list(tarea.renglones.select_related(
            'operation', 'operation__shipper', 'operation__bundle_type'))
        if not renglones:
            return None, []

        bultos = 0
        lbs = kgs = None
        shippers, tipos_de_bulto = set(), []
        for r in renglones:
            op = r.operation
            van = r.bultos_que_van
            de = op.bundle_qty or 0
            bultos += van
            pl = _proporcion(op.weight_lbs, van, de)
            pk_ = _proporcion(op.weight_kgs, van, de)
            if pl is not None:
                lbs = (lbs or Decimal('0')) + pl
            if pk_ is not None:
                kgs = (kgs or Decimal('0')) + pk_
            s = op.get_shipper_display()
            if s and s != '—':
                shippers.add(s)
            if op.bundle_type_id and op.bundle_type_id not in [b.pk for b in tipos_de_bulto]:
                tipos_de_bulto.append(op.bundle_type)

        unico_shipper = None
        if len(shippers) == 1:
            unico_shipper = next((r.operation.shipper for r in renglones
                                  if r.operation.shipper_id), None)
        if unico_shipper:
            shipper_texto = ''
        elif len(shippers) == 1:
            shipper_texto = next(iter(shippers))
        else:
            shipper_texto = 'VARIOUS' if shippers else ''

        empresa, unidad, sello = _transporte(tarea)
        numeros = [p.numero for p in tarea.pedimentos if p.numero]
        completas = [r.operation for r in renglones if not r.es_parcial]

        salida = WarehouseOperation(
            tenant=tarea.tenant,
            date=timezone.localdate(),
            operation_type='EXIT',
            customer=tarea.customer,
            entry_dispatched=', '.join(r.operation.custom_id for r in renglones
                                       if r.operation.custom_id)[:500],
            shipper=unico_shipper,
            shipper_name_manual=shipper_texto,
            carrier_name_manual=empresa[:200],
            trailer=unidad[:200],
            seal=sello[:200],
            bundle_type=tipos_de_bulto[0] if len(tipos_de_bulto) == 1 else None,
            bundle_type_manual=(', '.join(b.name for b in tipos_de_bulto)[:200]
                                if len(tipos_de_bulto) > 1 else ''),
            bundle_qty=bultos,
            weight_lbs=lbs,
            weight_kgs=kgs,
            po_order=_unidos(r.operation.po_order for r in renglones)[:200],
            description=_unidos(r.operation.description for r in renglones),
            pedimento=', '.join(numeros)[:100],
            note=f'Salida generada al cargar el cruce {tarea.custom_id}.',
            created_by=usuario,
        )
        salida.save()
        tarea.salida = salida
        tarea.save(update_fields=['salida', 'updated_at'])

        # La entrada que cruza completa queda liberada por esta SD, igual que
        # al capturarla a mano. La de 19 de 20 no: le quedan bultos en bodega.
        for op in completas:
            op.entry_dispatched = salida.custom_id
            op.save(update_fields=['entry_dispatched'])
    return salida, completas


def copiar_fotos(salida, documentos, usuario=None):
    """Pega las fotos de la carga al expediente de la SD."""
    for doc in documentos:
        try:
            doc.file.open('rb')
            try:
                contenido = doc.file.read()
            finally:
                doc.file.close()
        except Exception:
            logger.warning('No se pudo leer la foto de carga %s para %s',
                           doc.pk, salida.custom_id)
            continue
        nombre = doc.nombre or os.path.basename(doc.file.name or '') or 'foto.jpg'
        OperationDocument.objects.create(
            tenant=salida.tenant, operation=salida, file_type='PHOTO',
            file=ContentFile(contenido, name=nombre), original_name=nombre,
            uploaded_by=usuario or doc.uploaded_by)


def avisar_salida(salida, documentos, entradas, usuario=None):
    """
    Copia las fotos al expediente y le manda al cliente el reporte de la SD con
    ellas adjuntas. Corre en segundo plano: quien sube la foto esta en el anden
    con el telefono y no tiene por que esperar al correo.
    """
    from . import notifications
    copiar_fotos(salida, documentos, usuario)
    enviado, _error = notifications.notify_operation_created(
        salida, triggered_by=usuario)
    ya = (notifications.email_recipients(notifications.resolve_customer(salida))
          if enviado else [])
    for entrada in entradas:
        notifications.notify_goods_released(entrada, exit_op=salida,
                                            triggered_by=usuario,
                                            already_notified=ya)


def al_subir_fotos_de_carga(tarea, nuevos, usuario):
    """
    Lo que pasa al subir fotos de la mercancia cargada. La primera vez nace la
    SD y se manda; despues, las fotos solo se suman a su expediente.

    Devuelve la SD si se acaba de generar, o None.
    """
    from . import notifications
    if not nuevos:
        return None
    salida, entradas = armar_salida(tarea, usuario)
    if salida:
        fotos = list(DocumentoDeEntrega.objects.filter(
            task_id=tarea.pk, tipo=DocumentoDeEntrega.FOTOS_CARGA))
        notifications.en_segundo_plano(avisar_salida, salida, fotos,
                                       entradas, usuario)
        return salida
    tarea.refresh_from_db(fields=['salida'])
    if tarea.salida_id:
        notifications.en_segundo_plano(copiar_fotos, tarea.salida, nuevos,
                                       usuario)
    return None
