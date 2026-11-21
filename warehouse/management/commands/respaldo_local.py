"""Copia a un disco local todo lo que vive en la nube: los archivos y la base.

Es un **respaldo**, no una mudanza: el original se queda en R2 y en Postgres, y
la aplicacion sigue funcionando igual. Sirve para el dia en que Cloudflare o
Render fallen, o alguien borre algo que no debia.

Se corre desde la PC de la oficina, donde el `.env` apunta a produccion:

    python manage.py respaldo_local --destino "D:\\Respaldos WMS"

Que deja en el destino:

    archivos\\operations\\...     copia del bucket, con las mismas rutas
    base\\wms_2026-11-21.dump     la base (pg_dump, formato custom), o
    base\\wms_2026-11-21.json.gz  si pg_dump no esta instalado (dumpdata)

Los archivos se copian solo si faltan o cambiaron de tamaño, asi que despues de
la primera vez cada corrida baja nada mas lo nuevo. Lo que se borra del bucket
**no** se borra del respaldo: para eso es el respaldo.

De la base se guardan las ultimas `--copias` (8 por defecto: dos meses si se
corre cada semana).

pg_dump es mejor -- se restaura con `pg_restore` tal cual -- pero tiene que ser
de la misma version que el servidor o mas nueva (Render usa la 17). Se busca en
el PATH y en `C:\\Program Files\\PostgreSQL\\*\\bin`. Sin el, se usa `dumpdata`,
que se restaura con `loaddata` sobre una base recien migrada.
"""
import glob
import gzip
import os
import shutil
import subprocess
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

DESTINO_POR_DEFECTO = os.path.join(os.path.expanduser('~'), 'Respaldos WMS')


def buscar_pg_dump():
    encontrado = shutil.which('pg_dump')
    if encontrado:
        return encontrado
    candidatos = sorted(glob.glob(r'C:\Program Files\PostgreSQL\*\bin\pg_dump.exe'),
                        key=lambda p: int(Path(p).parts[-3]) if Path(p).parts[-3].isdigit() else 0)
    return candidatos[-1] if candidatos else None


class Command(BaseCommand):
    help = 'Respalda en un disco local los archivos de R2 y la base de datos.'

    def add_arguments(self, parser):
        parser.add_argument('--destino', default=DESTINO_POR_DEFECTO,
                            help=f'Carpeta del respaldo (por defecto "{DESTINO_POR_DEFECTO}").')
        parser.add_argument('--copias', type=int, default=8,
                            help='Cuantos respaldos de la base se conservan.')
        parser.add_argument('--solo-archivos', action='store_true')
        parser.add_argument('--solo-base', action='store_true')

    def handle(self, *args, **options):
        destino = Path(options['destino'])
        destino.mkdir(parents=True, exist_ok=True)
        self.stdout.write(f'Respaldo en {destino}')

        errores = 0
        if not options['solo_base']:
            errores += self._archivos(destino / 'archivos')
        if not options['solo_archivos']:
            errores += self._base(destino / 'base', options['copias'])

        if errores:
            raise CommandError(f'El respaldo termino con {errores} error(es); revisa arriba.')
        self.stdout.write(self.style.SUCCESS('Respaldo completo.'))

    # ── Archivos ─────────────────────────────────────────────────────────────

    def _archivos(self, carpeta):
        self.stdout.write(self.style.MIGRATE_HEADING('Archivos de R2'))
        if not getattr(settings, 'AWS_STORAGE_BUCKET_NAME', None):
            self.stdout.write(self.style.WARNING(
                'No hay bucket configurado (R2_BUCKET_NAME): se omiten los archivos.'))
            return 1

        import boto3
        s3 = boto3.client(
            's3', endpoint_url=settings.AWS_S3_ENDPOINT_URL,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_S3_REGION_NAME)
        bucket = settings.AWS_STORAGE_BUCKET_NAME

        total = bajados = bytes_bajados = errores = 0
        for pagina in s3.get_paginator('list_objects_v2').paginate(Bucket=bucket):
            for objeto in pagina.get('Contents', []):
                clave = objeto['Key']
                if clave.endswith('/'):
                    continue
                total += 1
                local = carpeta.joinpath(*clave.split('/'))
                if local.exists() and local.stat().st_size == objeto['Size']:
                    continue
                local.parent.mkdir(parents=True, exist_ok=True)
                temporal = local.with_name(local.name + '.parcial')
                try:
                    s3.download_file(bucket, clave, str(temporal))
                    os.replace(temporal, local)
                except Exception as e:
                    errores += 1
                    self.stdout.write(self.style.WARNING(f'  No se pudo bajar {clave}: {e}'))
                    temporal.unlink(missing_ok=True)
                    continue
                bajados += 1
                bytes_bajados += objeto['Size']

        self.stdout.write(f'{total} archivo(s) en el bucket; {bajados} nuevo(s) o cambiado(s) '
                          f'bajados ({bytes_bajados / 1e6:.1f} MB).')
        return errores

    # ── Base de datos ────────────────────────────────────────────────────────

    def _base(self, carpeta, copias):
        self.stdout.write(self.style.MIGRATE_HEADING('Base de datos'))
        carpeta.mkdir(parents=True, exist_ok=True)
        fecha = timezone.localtime().strftime('%Y-%m-%d')

        pg_dump = buscar_pg_dump() if connection.vendor == 'postgresql' else None
        url = os.environ.get('DATABASE_URL')
        try:
            if pg_dump and url:
                archivo = carpeta / f'wms_{fecha}.dump'
                resultado = subprocess.run(
                    [pg_dump, '--format=custom', '--no-owner', '--no-privileges',
                     f'--file={archivo}', f'--dbname={url}'],
                    capture_output=True, text=True)
                if resultado.returncode != 0:
                    archivo.unlink(missing_ok=True)
                    raise RuntimeError(resultado.stderr.strip())
            else:
                if connection.vendor == 'postgresql':
                    self.stdout.write('pg_dump no esta instalado: se usa dumpdata.')
                archivo = carpeta / f'wms_{fecha}.json.gz'
                # UTF-8 explicito: `--output` escribe con la codificacion del
                # sistema, que en Windows es cp1252, y `loaddata` lee UTF-8.
                # Con `--output` las eñes y los acentos rompian la restauracion.
                # `--all` para que entren tambien los documentos de la papelera,
                # que el manager por defecto esconde.
                try:
                    with gzip.open(archivo, 'wt', encoding='utf-8') as salida:
                        call_command('dumpdata', '--all', '--natural-foreign', '--natural-primary',
                                     '--exclude=contenttypes', '--exclude=auth.permission',
                                     '--exclude=sessions', '--exclude=admin.logentry',
                                     stdout=salida, verbosity=0)
                except Exception:
                    archivo.unlink(missing_ok=True)
                    raise
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'No se pudo respaldar la base: {e}'))
            return 1

        self.stdout.write(f'{archivo.name} ({archivo.stat().st_size / 1024:,.0f} kB)')

        respaldos = sorted(
            [p for p in carpeta.iterdir() if p.name.startswith('wms_')
             and p.suffix in ('.dump', '.gz')],
            key=lambda p: p.stat().st_mtime)
        for viejo in respaldos[:-copias] if copias > 0 else []:
            viejo.unlink()
            self.stdout.write(f'  Se quito el respaldo viejo {viejo.name}')
        return 0
