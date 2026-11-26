"""
Genera el par de llaves VAPID de los avisos push.

Se corre **una sola vez** y lo que imprime se pega en el entorno (Render →
Environment). No se vuelve a generar en produccion: cada dispositivo quedo
suscrito con la llave publica, y con otra todos tendrian que volver a activar
los avisos.

    python manage.py generar_llaves_push
"""
import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.core.management.base import BaseCommand


def _b64url(datos):
    return base64.urlsafe_b64encode(datos).rstrip(b'=').decode('ascii')


class Command(BaseCommand):
    help = 'Genera las llaves VAPID para los avisos push (se corre una sola vez).'

    def handle(self, *args, **options):
        privada = ec.generate_private_key(ec.SECP256R1())
        publica = privada.public_key().public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint)
        cruda = privada.private_numbers().private_value.to_bytes(32, 'big')

        self.stdout.write('Pega estas dos variables en el entorno (Render > Environment):\n')
        self.stdout.write(f'VAPID_PUBLIC_KEY={_b64url(publica)}')
        self.stdout.write(f'VAPID_PRIVATE_KEY={_b64url(cruda)}')
        self.stdout.write(
            '\nOpcional: VAPID_SUBJECT=mailto:tu-correo@tu-dominio.com '
            '(si no, se usa DEFAULT_FROM_EMAIL).\n'
            'La privada no se comparte ni se sube a git. No las vuelvas a '
            'generar: cada telefono tendria que activar los avisos otra vez.')
