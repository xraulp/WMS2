"""Comprueba que las credenciales de R2 sirven y que el bucket responde.

Este diagnóstico vivía suelto al final de `settings.py`, marcado como TEMPORAL,
y por estar ahí se ejecutaba en **cada arranque**: cada `runserver`, cada worker
de gunicorn y cada corrida de las pruebas abrían una conexión de red a
Cloudflare antes de hacer nada. Aquí solo corre cuando se pide.

    python manage.py check_r2
"""

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Verifica la conexion con el bucket de Cloudflare R2.'

    def handle(self, *args, **options):
        import boto3
        from botocore.exceptions import ClientError, NoCredentialsError

        faltantes = [
            nombre for nombre, valor in (
                ('R2_ACCESS_KEY_ID', settings.AWS_ACCESS_KEY_ID),
                ('R2_SECRET_ACCESS_KEY', settings.AWS_SECRET_ACCESS_KEY),
                ('R2_BUCKET_NAME', settings.AWS_STORAGE_BUCKET_NAME),
                ('R2_ENDPOINT_URL', settings.AWS_S3_ENDPOINT_URL),
            ) if not valor
        ]
        if faltantes:
            self.stderr.write(self.style.ERROR(
                'Faltan variables de entorno: ' + ', '.join(faltantes)
            ))
            return

        self.stdout.write(f'Endpoint: {settings.AWS_S3_ENDPOINT_URL}')
        self.stdout.write(f'Bucket:   {settings.AWS_STORAGE_BUCKET_NAME}')
        self.stdout.write(f'Dominio publico: {settings.AWS_S3_CUSTOM_DOMAIN or "(sin definir: las URL se firman y caducan)"}')

        s3 = boto3.client(
            's3',
            endpoint_url=settings.AWS_S3_ENDPOINT_URL,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_S3_REGION_NAME,
        )

        try:
            respuesta = s3.list_objects_v2(
                Bucket=settings.AWS_STORAGE_BUCKET_NAME, MaxKeys=1
            )
        except NoCredentialsError:
            self.stderr.write(self.style.ERROR('boto3 no encontro credenciales.'))
            return
        except ClientError as e:
            codigo = e.response.get('Error', {}).get('Code', '?')
            self.stderr.write(self.style.ERROR(f'Error de R2 [{codigo}]: {e}'))
            return

        self.stdout.write(self.style.SUCCESS(
            f'Conexion correcta. El bucket devolvio {respuesta.get("KeyCount", 0)} objeto(s) '
            f'en la primera pagina.'
        ))
