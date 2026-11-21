"""
Achica las fotos antes de que lleguen al almacen.

Una foto de celular pesa de 3 a 5 MB y casi todo es resolucion que nadie usa:
en el expediente se ve en una pantalla y en el ZIP se manda al agente aduanal,
y a los dos les sobra con 2000 px del lado largo. Con eso y calidad 82 la foto
queda en unos cientos de kB, legible aunque sea la foto de una factura, y el
bucket crece diez veces mas despacio.

Se hace en el servidor y no en el navegador porque asi no depende de por donde
entro el archivo: el expediente, el chat, el enlace de carga, el pedimento y
los papeles de entrega guardan por el mismo `save()`.

Reglas:

* Solo se toca lo que Pillow reconoce como JPEG, PNG o WEBP. PDF, video, HEIC,
  GIF y lo demas pasan tal cual.
* Lo que ya es chico no se recomprime: volver a codificar un JPEG pequeño solo
  le quita calidad.
* Si el resultado no pesa menos que el original, se queda el original.
* Cualquier error deja el archivo como llego. Que una foto rara se guarde
  grande es aceptable; que no se guarde, no.
* Se conserva el EXIF (fecha y lugar de la toma), que es parte de la
  evidencia, y se aplica antes la rotacion para que la foto no salga de lado.
"""
import io
import logging
import os

from django.core.files.base import ContentFile

logger = logging.getLogger(__name__)

LADO_MAXIMO = 2000
CALIDAD_JPEG = 82
# Por debajo de esto, y sin pasar del lado maximo, la foto se deja como esta.
PESO_QUE_NO_SE_TOCA = 600 * 1024

FORMATOS = {'JPEG', 'MPO', 'PNG', 'WEBP'}


def _peso(archivo):
    try:
        return archivo.size
    except Exception:
        return None


def comprimir_foto(archivo, nombre):
    """
    Devuelve `(contenido, nombre_nuevo)` si vale la pena cambiar el archivo, o
    `None` si hay que guardarlo como llego.
    """
    try:
        from PIL import Image, ImageOps
    except ImportError:  # pragma: no cover - Pillow esta en requirements
        return None

    try:
        archivo.seek(0)
        imagen = Image.open(archivo)
    except Exception:
        # PDF, video, Excel...: lo normal, no un error.
        try:
            archivo.seek(0)
        except Exception:
            pass
        return None

    try:
        formato = imagen.format
        if formato not in FORMATOS:
            return None
        if getattr(imagen, 'n_frames', 1) > 1 and formato != 'MPO':
            return None  # WEBP animado

        peso = _peso(archivo)
        if (max(imagen.size) <= LADO_MAXIMO and peso is not None
                and peso <= PESO_QUE_NO_SE_TOCA):
            return None

        imagen = ImageOps.exif_transpose(imagen)
        exif = imagen.getexif()

        transparente = imagen.mode in ('RGBA', 'LA') or (
            imagen.mode == 'P' and 'transparency' in imagen.info)
        if formato == 'PNG' and transparente:
            # Un PNG con transparencia es un logo o un recorte, no una foto: se
            # achica pero sigue siendo PNG para no ponerle fondo.
            salida_formato, extension = 'PNG', 'png'
        else:
            salida_formato, extension = 'JPEG', 'jpg'
            if imagen.mode != 'RGB':
                imagen = imagen.convert('RGB')

        imagen.thumbnail((LADO_MAXIMO, LADO_MAXIMO), Image.LANCZOS)

        salida = io.BytesIO()
        if salida_formato == 'JPEG':
            imagen.save(salida, 'JPEG', quality=CALIDAD_JPEG, optimize=True,
                        progressive=True, exif=exif.tobytes() if exif else b'')
        else:
            imagen.save(salida, 'PNG', optimize=True)
    except Exception as e:
        logger.warning('No se pudo comprimir %s; se guarda tal cual: %s', nombre, e)
        return None
    finally:
        try:
            archivo.seek(0)
        except Exception:
            pass

    if peso is not None and salida.tell() >= peso:
        return None

    base = os.path.splitext(os.path.basename(nombre or ''))[0] or 'foto'
    nombre_nuevo = f'{base}.{extension}'
    return ContentFile(salida.getvalue(), name=nombre_nuevo), nombre_nuevo


class ComprimeFotosAlGuardar:
    """
    Mixin de los modelos de documento: achica la foto recien subida.

    Solo actua sobre un archivo que todavia no esta en el almacen
    (`_committed` falso). Lo que ya se guardo -- un documento que se edita, se
    archiva o se restaura -- no se vuelve a abrir.
    """

    def save(self, *args, **kwargs):
        campo = getattr(self, 'file', None)
        if campo and not getattr(campo, '_committed', True):
            resultado = comprimir_foto(campo.file, campo.name)
            if resultado is not None:
                contenido, nombre_nuevo = resultado
                extension_vieja = os.path.splitext(campo.name or '')[1].lower()
                campo.file = contenido
                campo.name = nombre_nuevo
                # Si cambio el formato, el nombre que ve el usuario y con el
                # que se descarga tiene que decirlo: un .png con JPEG dentro
                # confunde al que lo abre.
                nueva = os.path.splitext(nombre_nuevo)[1].lower()
                jpeg = {'.jpg', '.jpeg', '.jfif'}
                mismo_formato = extension_vieja == nueva or (
                    extension_vieja in jpeg and nueva in jpeg)
                original = getattr(self, 'original_name', '')
                if original and not mismo_formato:
                    self.original_name = os.path.splitext(original)[0] + nueva
        super().save(*args, **kwargs)
