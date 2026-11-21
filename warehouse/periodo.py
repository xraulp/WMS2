"""
Las listas enseñan lo reciente; lo viejo queda en el historico.

La tabla de Operaciones y la de Cruces realizados traian todo desde el primer
dia. Con meses de uso eso es una tabla que hay que recorrer para llegar a lo de
esta semana, y el corte duro a 200 filas escondia lo viejo sin avisar.

La regla:

* **Lo de los ultimos 4 meses** (`MESES_RECIENTES`), por la fecha de la
  operacion o la de entrega del cruce.
* **Mas las entradas que siguen en bodega**, tengan la fecha que tengan. Son
  inventario vivo: una tarima que lleva cinco meses guardada es justo la que
  hay que ver, no la que hay que esconder.
* **El buscador recorre todo el historico.** Quien teclea un ID o un pedido
  viejo lo esta buscando; que no apareciera por el periodo seria un "no existe"
  falso.
* `periodo=todo` lo quita.

Nada se borra ni se mueve: es solo lo que la lista enseña por defecto.
"""
from datetime import date

from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

MESES_RECIENTES = 4

TODO = 'todo'
OPCIONES = [
    ('', _('Last 4 months')),
    (TODO, _('All history')),
]

# Una entrada se libera cuando `entry_dispatched` lleva un folio que empieza por
# SD (ver `WarehouseOperation.status`). Es la misma lectura, hecha en SQL para
# poder acotar antes del corte de filas.
LIBERADA = Q(entry_dispatched__iregex=r'(^|[\s,])sd')


def fecha_de_corte(hoy=None):
    """Hoy hace 4 meses. Si ese dia no existe (31 de junio), el ultimo del mes."""
    hoy = hoy or timezone.localdate()
    mes = hoy.month - MESES_RECIENTES
    anio = hoy.year
    while mes < 1:
        mes += 12
        anio -= 1
    for dia in (hoy.day, 30, 29, 28):
        try:
            return date(anio, mes, dia)
        except ValueError:
            continue


def pide_todo(request, con_busqueda=True):
    """
    Si la peticion quiere el historico completo, o busca algo.

    `con_busqueda=False` donde `q` no es la busqueda de la lista: en el tablero
    y en el movil, `?q=` es lo que abre el panel Digital desde un QR.
    """
    if request.GET.get('periodo') == TODO:
        return True
    return con_busqueda and bool((request.GET.get('q') or '').strip())


def operaciones_recientes(qs, hoy=None):
    """Las operaciones recientes y las entradas que siguen en bodega."""
    return qs.filter(
        Q(date__gte=fecha_de_corte(hoy))
        | (Q(operation_type='ENTRY') & ~LIBERADA))


def acotar_operaciones(request, qs, con_busqueda=True):
    """
    Aplica el periodo a la tabla de Operaciones.

    Devuelve `(qs, en_historico)`: `en_historico` es cuantas quedaron fuera, o
    `None` si no se acoto, para que la tabla diga que hay mas y como verlas.
    """
    if pide_todo(request, con_busqueda):
        return qs, None
    recientes = operaciones_recientes(qs)
    return recientes, qs.count() - recientes.count()


def acotar_cruces(request, qs):
    """Lo mismo para Cruces realizados, por la fecha de entrega."""
    if pide_todo(request):
        return qs, None
    recientes = qs.filter(entregada_en__date__gte=fecha_de_corte())
    return recientes, qs.count() - recientes.count()
