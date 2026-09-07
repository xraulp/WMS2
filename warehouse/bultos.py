"""
El codigo que identifica un bulto concreto.

Hasta ahora la etiqueta llevaba un QR que apuntaba al expediente de la
operacion, y nada mas. Eso alcanza para consultar, pero no para cargar: si las
seis etiquetas de una entrada de seis pallets dicen exactamente lo mismo,
pistolear el mismo pallet seis veces da el mismo resultado que pistolear los
seis. Y "nada se carga sin pistolear" se sostiene justo sobre esa diferencia --
es lo que permite saber no solo que son 19 pallets, sino **cuales** 19.

Asi que la etiqueta pasa a identificar el bulto de dos formas, con el mismo
dato en las dos:

    QR        .../mobile/?tab=digital&q=ED260901-0001&b=3
    Code 128  ED260901-0001-3

El codigo de barras de rayas no es un adorno ni un respaldo del QR: compra tres
cosas a la vez. Que sirvan las pistolas que ya hay en la bodega sean 1D o 2D --
las 1D no leen un QR y hoy no leerian nada de lo que esta pegado --, que sirva
cualquier pistola barata que se compre despues, y que el dia que falle todo
alguien pueda leer el numero con los ojos y teclearlo.

Y esa ultima forma importa mas de lo que parece. Las pistolas BT433 se
comportan como un teclado: se emparejan por Bluetooth y cada disparo llega como
si alguien hubiera tecleado el codigo y pulsado Enter. Por eso la pantalla de
escaneo no necesita camara, ni permisos, ni libreria: es una caja de texto que
siempre tiene el foco. Y por eso `leer` acepta las tres formas -- el QR
completo, el codigo de rayas y lo tecleado a mano --, porque las tres llegan
por el mismo sitio.
"""
import re

# `ED260901-0001-3`: el identificador de la operacion, un guion, y el numero de
# bulto. El identificador ya lleva guiones dentro, asi que el numero se lee del
# final y no partiendo por guiones.
_CODIGO = re.compile(r'^\s*([A-Za-z]{2}\d{6}-\d{4})-(\d{1,5})\s*$')

# El QR de la etiqueta. Lo que importa son los dos parametros; el resto de la
# URL cambia con el dominio del tenant y no se compara.
_DEL_QR_OPERACION = re.compile(r'[?&]q=([^&\s]+)')
_DEL_QR_BULTO     = re.compile(r'[?&]b=(\d{1,5})')


def codigo(operation_custom_id, numero_de_bulto):
    """El texto del codigo de barras de un bulto: `ED260901-0001-3`."""
    return f'{operation_custom_id}-{int(numero_de_bulto)}'


def leer(texto):
    """
    `(custom_id, numero_de_bulto)` de lo que haya entrado por el lector.

    Acepta las tres formas en que puede llegar:

    - el QR entero, con `q=` y `b=`;
    - el codigo de rayas, `ED260901-0001-3`;
    - el identificador solo, `ED260901-0001`, que es lo que se teclea a mano
      cuando la etiqueta esta rota y solo se lee la parte de arriba. En ese
      caso el numero de bulto es `None`: el sistema sabe de que operacion es
      pero no de que bulto, y quien lo esta usando tiene que decidir.

    Devuelve `(None, None)` si no reconoce nada. No adivina: un codigo que no
    se entiende y se interpreta a medias es peor que uno rechazado, porque lo
    segundo se ve en el momento y lo primero aparece en la aduana.
    """
    texto = (texto or '').strip()
    if not texto:
        return (None, None)

    if 'q=' in texto:
        op = _DEL_QR_OPERACION.search(texto)
        if op:
            bulto = _DEL_QR_BULTO.search(texto)
            return (op.group(1).upper(),
                    int(bulto.group(1)) if bulto else None)

    con_bulto = _CODIGO.match(texto)
    if con_bulto:
        return (con_bulto.group(1).upper(), int(con_bulto.group(2)))

    # El identificador suelto, tecleado a mano.
    if re.match(r'^[A-Za-z]{2}\d{6}-\d{4}$', texto):
        return (texto.upper(), None)

    return (None, None)


def url_del_qr(base_url, operation_custom_id, numero_de_bulto):
    """
    La URL que va dentro del QR de la etiqueta de un bulto.

    Es la de siempre mas `&b=`. Se añade y no se sustituye a proposito: una
    etiqueta vieja -- sin `b` -- sigue abriendo el expediente igual que antes,
    que es para lo que se pegaba.
    """
    return f'{base_url}&b={int(numero_de_bulto)}'
