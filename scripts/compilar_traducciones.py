"""
Compila los catalogos .po a .mo sin depender de gettext.

Django trae `compilemessages`, pero llama a `msgfmt`, que es un binario de
GNU gettext: no esta en Windows y tampoco tiene por que estar en el contenedor
de Render. Antes que pedir que cada maquina lo instale --y descubrir que falta
en mitad de un despliegue-- el proyecto trae su propio compilador: el formato
.mo son dos tablas de offsets y un bloque de texto, y esto cabe en una pagina.

    python scripts/compilar_traducciones.py

Lee todos los `locale/<idioma>/LC_MESSAGES/*.po` y escribe el .mo de al lado.
Los .mo se versionan junto a los .po para que un despliegue no dependa de que
esto llegue a ejecutarse.
"""
import array
import os
import re
import struct
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAGICO = 0x950412de


def _sin_comillas(linea):
    """El contenido de una linea de .po entrecomillada, con sus escapes."""
    linea = linea.strip()
    if not (linea.startswith('"') and linea.endswith('"')):
        raise ValueError('linea .po mal formada: %r' % linea)
    cuerpo = linea[1:-1]
    return (cuerpo.replace('\\n', '\n').replace('\\t', '\t')
                  .replace('\\"', '"').replace('\\\\', '\\'))


def leer_po(ruta):
    """
    Las entradas de un .po, como {msgid: msgstr}.

    Los plurales se guardan como gettext los quiere en el .mo: el singular y el
    plural unidos por un nulo en la clave, y las formas unidas por nulos en el
    valor. Las entradas sin traducir se saltan, que es lo que hace que la
    cadena original se vea tal cual en vez de vacia.
    """
    entradas = {}
    campo = None          # msgid | msgid_plural | msgstr | msgstr[n]
    acumulado = {}
    difusa = False

    def cerrar():
        nonlocal difusa
        if not acumulado:
            return
        clave = acumulado.get('msgid')
        if clave is None:
            return
        if 'msgid_plural' in acumulado:
            clave = clave + '\x00' + acumulado['msgid_plural']
            formas = [acumulado[k] for k in sorted(acumulado)
                      if k.startswith('msgstr[')]
            valor = '\x00'.join(formas)
        else:
            valor = acumulado.get('msgstr', '')
        # Una entrada difusa es una propuesta automatica sin revisar; gettext
        # tampoco la usa. La cabecera (msgid vacio) entra siempre.
        if valor and not difusa or clave == '':
            entradas[clave] = valor
        acumulado.clear()
        difusa = False

    with open(ruta, encoding='utf-8') as f:
        for linea in f:
            cruda = linea.rstrip('\n')
            desnuda = cruda.strip()
            if not desnuda:
                cerrar()
                campo = None
                continue
            if desnuda.startswith('#'):
                if desnuda.startswith('#,') and 'fuzzy' in desnuda:
                    difusa = True
                continue
            m = re.match(r'^(msgid_plural|msgid|msgstr(?:\[\d+\])?)\s+(.*)$', desnuda)
            if m:
                campo = m.group(1)
                if campo == 'msgid' and 'msgid' in acumulado:
                    cerrar()
                acumulado[campo] = _sin_comillas(m.group(2))
            elif campo:
                acumulado[campo] += _sin_comillas(desnuda)
            else:
                raise ValueError('%s: linea suelta %r' % (ruta, cruda))
    cerrar()
    return entradas


def escribir_mo(entradas, ruta):
    """El .mo tal como lo lee el modulo `gettext` de Python."""
    claves = sorted(entradas)
    originales = [k.encode('utf-8') for k in claves]
    traducidas = [entradas[k].encode('utf-8') for k in claves]
    n = len(claves)

    # Cabecera de 7 enteros, y detras las dos tablas de (largo, offset).
    inicio_tabla_o = 7 * 4
    inicio_tabla_t = inicio_tabla_o + n * 8
    inicio_texto = inicio_tabla_t + n * 8

    tabla_o, tabla_t = [], []
    bloque = b''
    offset = inicio_texto
    for texto in originales:
        tabla_o += [len(texto), offset]
        bloque += texto + b'\x00'
        offset += len(texto) + 1
    for texto in traducidas:
        tabla_t += [len(texto), offset]
        bloque += texto + b'\x00'
        offset += len(texto) + 1

    salida = struct.pack('<7I', MAGICO, 0, n, inicio_tabla_o, inicio_tabla_t, 0, 0)
    salida += array.array('i', tabla_o + tabla_t).tobytes()
    salida += bloque
    with open(ruta, 'wb') as f:
        f.write(salida)


def main():
    base = os.path.join(RAIZ, 'locale')
    if not os.path.isdir(base):
        print('No hay carpeta locale/; nada que compilar.')
        return 0
    hechos = 0
    for carpeta, _, ficheros in os.walk(base):
        for nombre in ficheros:
            if not nombre.endswith('.po'):
                continue
            po = os.path.join(carpeta, nombre)
            mo = po[:-3] + '.mo'
            entradas = leer_po(po)
            escribir_mo(entradas, mo)
            # La cabecera no es una traduccion; no se cuenta.
            print('%s -> %s (%d cadenas)'
                  % (os.path.relpath(po, RAIZ), os.path.relpath(mo, RAIZ),
                     len([k for k in entradas if k])))
            hechos += 1
    if not hechos:
        print('No se encontro ningun .po.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
