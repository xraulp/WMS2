"""
Los papeles de entrega de una tarea de cruce, juntos.

Al final del cruce el cliente recibe de una vez:

- los **pedimentos pagados**, uno por pedimento, cada uno en su archivo. Se
  suben en la ranura de cada pedimento -- es lo que deja marcarlo pagado -- y
  aqui solo se juntan;
- la **remision original**, la que genera el sistema;
- el **shipper**, uno o varios PDF;
- las **fotos de la mercancia cargada** en el camion;
- la **foto de la remision firmada** de recibido por el transporte mexicano.

Este modulo es la lista unica de esos archivos, con una clave estable por
archivo (`pag:12`, `rem:3`, `ent:40`). La usan la caja de la tarea, el ZIP y
el correo, para que los tres hablen siempre de los mismos archivos y una
seleccion hecha en pantalla signifique lo mismo al descargar que al mandar.
"""
import logging
import os

from django.urls import reverse
from django.utils.translation import gettext as _, pgettext

from .models import DocumentoDeEntrega, PedimentoDocument

logger = logging.getLogger(__name__)


# El orden en que se enseñan y en que van en el ZIP y en el correo: primero lo
# que el cliente necesita para la aduana, despues la prueba de la entrega.
PAGADOS   = 'PAGADOS'
REMISION  = 'REMISION'
GRUPOS = (PAGADOS, REMISION, DocumentoDeEntrega.SHIPPER,
          DocumentoDeEntrega.FOTOS_CARGA, DocumentoDeEntrega.REMISION_FIRMADA)


def titulo_del_grupo(grupo):
    return {
        PAGADOS:  _('Paid pedimentos'),
        REMISION: _('Original remision'),
        DocumentoDeEntrega.SHIPPER:          pgettext('delivery document', 'Shipper'),
        DocumentoDeEntrega.FOTOS_CARGA:      _('Photos of the loaded goods'),
        DocumentoDeEntrega.REMISION_FIRMADA: _('Signed remision'),
    }[grupo]


class Archivo:
    """Uno de los archivos de la entrega, venga de donde venga."""

    def __init__(self, clave, grupo, nombre, url, leer, quitable=None,
                 campo=None):
        self.clave = clave
        self.grupo = grupo
        self.nombre = nombre
        self.url = url
        self._leer = leer
        # El `DocumentoDeEntrega` si se puede quitar desde la tarea; los
        # pagados se quitan en su pedimento y la remision no se quita.
        self.quitable = quitable
        self._campo = campo

    @property
    def tamano(self):
        """
        El tamaño, solo cuando se pide -- al armar el correo --. Con los
        archivos en R2 cada consulta es una vuelta por internet, y pedirlo
        para cada archivo al pintar la tarea hacia que la caja tardara en
        salir.
        """
        if self._campo is None:
            return None
        try:
            return self._campo.size
        except Exception:
            return None

    def leer(self):
        return self._leer()


def _leer_campo(campo):
    def leer():
        campo.open('rb')
        try:
            return campo.read()
        finally:
            campo.close()
    return leer


def remisiones_vigentes(tarea):
    orden = tarea.ordenes.order_by('-version').first()
    return list(orden.remisiones.order_by('emitida_en')) if orden else []


def archivos(tarea, usuario=None, pedimentos=None):
    """Todos los archivos de la entrega de `tarea`, en el orden de `GRUPOS`."""
    from . import papeles

    salida = []
    for ped in (tarea.pedimentos if pedimentos is None else pedimentos):
        etiqueta = ped.numero or ped.etiqueta
        docs = list(ped.documentos.filter(
            ranura=PedimentoDocument.PEDIMENTO_PAGADO
        ).select_related('documento_de_operacion'))
        varios = len(docs) > 1
        for i, doc in enumerate(docs, 1):
            archivo = doc.archivo
            if not archivo or not archivo.name:
                continue
            ext = os.path.splitext(doc.nombre or archivo.name)[1].lower() or '.pdf'
            nombre = '%s %s%s%s' % (_('Paid pedimento'), etiqueta,
                                    ' (%d)' % i if varios else '', ext)
            salida.append(Archivo(
                f'pag:{doc.pk}', PAGADOS, nombre,
                reverse('pedimento_archivo', args=[doc.pk]),
                _leer_campo(archivo), campo=archivo))

    for rem in remisiones_vigentes(tarea):
        def leer(rem=rem):
            return papeles.remision(rem, usuario)
        salida.append(Archivo(
            f'rem:{rem.pk}', REMISION, f'{rem.custom_id}.pdf',
            reverse('remision_pdf', args=[rem.pk]), leer))

    for doc in tarea.documentos_de_entrega.all():
        salida.append(Archivo(
            f'ent:{doc.pk}', doc.tipo, doc.nombre,
            reverse('cruce_entrega_archivo', args=[doc.pk]),
            _leer_campo(doc.file), quitable=doc, campo=doc.file))

    orden = {g: i for i, g in enumerate(GRUPOS)}
    salida.sort(key=lambda a: orden[a.grupo])
    return salida


def pedimentos_sin_pagado(tarea, lista=None, pedimentos=None):
    """Los pedimentos de la tarea a los que todavia no se les sube el pagado."""
    pedimentos = tarea.pedimentos if pedimentos is None else pedimentos
    if lista is not None:
        # Con la lista ya armada no hace falta volver a preguntar a la base.
        con_pagado = {int(a.clave.split(':')[1]) for a in lista if a.grupo == PAGADOS}
        con_pagado = set(PedimentoDocument.objects.filter(
            pk__in=con_pagado).values_list('pedimento_id', flat=True))
        return [p for p in pedimentos if p.pk not in con_pagado]
    return [p for p in pedimentos
            if not p.documentos.filter(
                ranura=PedimentoDocument.PEDIMENTO_PAGADO).exists()]


def por_grupo(lista):
    """`[(grupo, titulo, [archivos])]` con todos los grupos, vacios incluidos."""
    cajones = {g: [] for g in GRUPOS}
    for a in lista:
        cajones[a.grupo].append(a)
    return [(g, titulo_del_grupo(g), cajones[g]) for g in GRUPOS]


def elegidos(lista, claves):
    """Los de `lista` cuyas claves vienen en `claves`; todos si no viene ninguna."""
    claves = [c for c in (claves or []) if c]
    if not claves:
        return list(lista)
    quiero = set(claves)
    return [a for a in lista if a.clave in quiero]


def nombre_en_el_zip(tarea, archivo, usados):
    """
    El nombre con que va un archivo dentro del ZIP o del correo.

    La tarea delante para que, guardados en una carpeta con los de otros
    cruces, se sepa de cual es cada uno. Dos fotos que se llamen igual no se
    pisan: la segunda lleva numero.
    """
    base, ext = os.path.splitext(archivo.nombre)
    if archivo.grupo in (DocumentoDeEntrega.SHIPPER,
                         DocumentoDeEntrega.FOTOS_CARGA,
                         DocumentoDeEntrega.REMISION_FIRMADA):
        base = f'{titulo_del_grupo(archivo.grupo)} - {base}'
    # Guion normal y no raya: los descompresores viejos de Windows leen los
    # nombres en cp437 y una raya sale como basura.
    nombre = f'{tarea.custom_id} - {base}'.replace('/', '_')
    candidato, n = f'{nombre}{ext}', 2
    while candidato.lower() in usados:
        candidato = f'{nombre} ({n}){ext}'
        n += 1
    usados.add(candidato.lower())
    return candidato
