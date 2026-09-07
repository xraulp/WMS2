"""
El numero de pedimento, por dentro.

Hasta ahora el pedimento era un campo de texto donde cabia cualquier cosa. No
lo es: son quince digitos con estructura, y la ley dice cual -- el Anexo 22 de
las Reglas Generales de Comercio Exterior. Leido por grupos:

    26   24     1515      6      005000
    |    |      |         |      |
    |    |      |         |      consecutivo del agente por aduana (6)
    |    |      |         ultimo digito del ano (1)
    |    |      patente del agente aduanal (4)
    |    aduana de despacho (2)
    ano de validacion (2)

De esos cinco grupos el sistema solo pone el primero, el ano de validacion, que
ya sabe. Los otros cuatro se teclean, y se teclean en **tres casillas**: la
aduana (2), la patente (4) y el ano-y-consecutivo (7 = 1 + 6). Escrito de
corrido queda `24-1780-6003555`, que es como lo dicta el agente aduanal.

Lo que compra este formato no es la validacion, es el candado. Con la aduana y
la patente **dentro** del numero, el sistema sabe de que aduana y de que agente
es cada operacion sin preguntarle a nadie y sin un campo nuevo que alguien
tenga que acordarse de rellenar. Un DODA solo puede llevar pedimentos de un
mismo agente aduanal -- eso es candado fijo, no recomendacion -- y de ahi
salen las tres comprobaciones de `choque`: al teclear el numero dentro de una
tarea, al juntar tareas en un mismo camion, y al emitir el DODA.

De regalo cae la comprobacion que mas veces va a saltar: **siete digitos, ni
seis ni ocho**. Un cero de mas tecleando de prisa es el error que se cuela, y
es el unico que se puede cazar antes de que el numero acabe en un documento
oficial.
"""
import re

from django.utils.translation import gettext_lazy as _

# Los largos fijos de cada casilla. No son configurables: los dicta el Anexo 22.
LARGO_ADUANA      = 2
LARGO_PATENTE     = 4
LARGO_CONSECUTIVO = 7   # el ultimo digito del ano (1) + el consecutivo (6)

# Las dos aduanas por las que cruza el area. La lista no es un candado -- se
# acepta cualquier aduana de dos digitos -- pero sirve para escribir el nombre
# al lado del numero, que es lo que hace legible un `24` suelto en una pantalla.
ADUANAS = {
    '24': _('Nuevo Laredo'),
    '80': _('Colombia'),
}

# Un numero pegado de cualquier sitio trae guiones, espacios o nada. Todo eso
# sobra: lo que importa son los digitos y en que orden vienen.
_SOLO_DIGITOS = re.compile(r'\D+')


def solo_digitos(texto):
    """Los digitos de `texto`, sin guiones, espacios ni puntos."""
    return _SOLO_DIGITOS.sub('', str(texto or ''))


def nombre_de_aduana(aduana):
    """El nombre de la aduana, o su numero si no es de las conocidas."""
    aduana = (aduana or '').strip()
    return ADUANAS.get(aduana, aduana)


def errores_de_casillas(aduana, patente, consecutivo):
    """
    Que le falta a las tres casillas para formar un pedimento valido.

    Devuelve una lista de mensajes ya escritos para la pantalla, vacia si todo
    cuadra. Las tres casillas vacias tambien cuadran: un pedimento nace sin
    numero -- primero se agrupa la mercancia y despues el agente aduanal da el
    numero -- y solo hace falta para mandarlo a revision.

    Lo que no se acepta es dejarlo a medias. Media captura es la que se queda
    guardada creyendo que esta completa.
    """
    aduana      = solo_digitos(aduana)
    patente     = solo_digitos(patente)
    consecutivo = solo_digitos(consecutivo)

    if not (aduana or patente or consecutivo):
        return []

    errores = []
    if len(aduana) != LARGO_ADUANA:
        errores.append(_('The customs office takes 2 digits (e.g. 24 for Nuevo Laredo).'))
    if len(patente) != LARGO_PATENTE:
        errores.append(_('The broker license takes 4 digits (e.g. 1780).'))
    if len(consecutivo) != LARGO_CONSECUTIVO:
        # El mensaje dice cuantos hay, no solo cuantos faltan: el error tipico
        # es un cero de mas, y verlo contado es lo que lo delata.
        errores.append(
            _('The year and sequence take 7 digits — you typed %(n)d.')
            % {'n': len(consecutivo)})
    return errores


def numero_corrido(aduana, patente, consecutivo):
    """
    El numero como lo dicta el agente aduanal: `24-1780-6003555`.

    Es lo que se guarda en el campo `pedimento` de la operacion y lo que sale
    en los nombres de archivo, en los reportes y en las busquedas. Cadena vacia
    si las casillas no estan completas -- media captura no se guarda.
    """
    aduana      = solo_digitos(aduana)
    patente     = solo_digitos(patente)
    consecutivo = solo_digitos(consecutivo)
    if (len(aduana), len(patente), len(consecutivo)) != (
            LARGO_ADUANA, LARGO_PATENTE, LARGO_CONSECUTIVO):
        return ''
    return f'{aduana}-{patente}-{consecutivo}'


def numero_completo(aduana, patente, consecutivo, anio_validacion=None):
    """
    Los quince digitos del Anexo 22, separados por grupos: `26 24 1780 6003555`.

    `anio_validacion` son los dos digitos del ano en que se valida el pedimento
    -- lo pone el sistema, no se teclea --. Sin el se devuelve el numero de
    cuatro grupos, que es lo que hay mientras el pedimento sigue en borrador.
    """
    corrido = numero_corrido(aduana, patente, consecutivo)
    if not corrido:
        return ''
    grupos = corrido.split('-')
    anio = solo_digitos(anio_validacion)[-2:]
    if anio:
        grupos.insert(0, anio.zfill(2))
    return ' '.join(grupos)


def desglosar(texto):
    """
    Las tres casillas de un numero ya escrito, para leer lo que hay guardado.

    Acepta las dos formas en que puede venir: los trece digitos que se teclean
    (`24-1780-6003555`) y los quince completos con el ano delante
    (`26 24 1780 6003555`). Cualquier otra cosa devuelve las tres casillas
    vacias, que es lo correcto para los ochenta y seis pedimentos viejos que se
    capturaron como texto libre: no se inventa una estructura que no tienen.

    Devuelve `(aduana, patente, consecutivo)`.
    """
    digitos = solo_digitos(texto)
    if len(digitos) == 15:
        digitos = digitos[2:]          # sobra el ano de validacion de delante
    if len(digitos) != LARGO_ADUANA + LARGO_PATENTE + LARGO_CONSECUTIVO:
        return ('', '', '')
    corte = LARGO_ADUANA + LARGO_PATENTE
    return (digitos[:LARGO_ADUANA], digitos[LARGO_ADUANA:corte], digitos[corte:])


def patente_de(texto):
    """La patente del agente aduanal que va dentro del numero, o cadena vacia."""
    return desglosar(texto)[1]


def aduana_de(texto):
    """La aduana de despacho que va dentro del numero, o cadena vacia."""
    return desglosar(texto)[0]


def choque(nuevo, existentes):
    """
    Si `nuevo` no puede convivir con `existentes` -- y por que.

    Es el candado del agente aduanal, y de paso el de la aduana: un DODA solo
    puede llevar pedimentos de un mismo agente, y nadie puede meter en un cruce
    por Nuevo Laredo un pedimento validado para Colombia. Las dos
    comprobaciones son la misma porque los dos datos viajan dentro del numero.

    `nuevo` es un numero de pedimento; `existentes` son los que ya estan
    dentro. Devuelve `None` si cabe, y si no un diccionario con el mensaje ya
    escrito y con **el numero contra el que choca**: un "valor invalido" no le
    sirve a quien tiene que arreglarlo, y saber cual es el otro pedimento es lo
    que convierte el aviso en una instruccion.

    Un numero sin estructura -- los viejos de texto libre -- no choca con
    nadie: no se puede afirmar de que agente es, y bloquear por una sospecha
    para el trabajo sin dar a cambio ninguna certeza.
    """
    aduana_nueva, patente_nueva, _consecutivo = desglosar(nuevo)
    if not patente_nueva:
        return None

    for otro in existentes:
        aduana_otra, patente_otra, _c = desglosar(otro)
        if not patente_otra:
            continue
        if patente_otra != patente_nueva:
            return {
                'motivo':  'PATENTE',
                'choca_con': otro,
                'mensaje': _(
                    'This pedimento is from broker license %(nueva)s and '
                    '%(otro)s is from %(otra)s. A single DODA cannot carry two '
                    'customs brokers.')
                    % {'nueva': patente_nueva, 'otro': otro, 'otra': patente_otra},
            }
        if aduana_otra != aduana_nueva:
            return {
                'motivo':  'ADUANA',
                'choca_con': otro,
                'mensaje': _(
                    'This pedimento clears through %(nueva)s and %(otro)s '
                    'clears through %(otra)s.')
                    % {'nueva': nombre_de_aduana(aduana_nueva),
                       'otro': otro,
                       'otra': nombre_de_aduana(aduana_otra)},
            }
    return None
