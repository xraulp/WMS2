"""
Manda un WhatsApp de prueba por la Cloud API y lo deja en la bitácora, para que
el webhook tenga a qué renglón pegarle lo que pase después.

    python manage.py probar_whatsapp 52XXXXXXXXXX 1XXXXXXXXXX
    python manage.py probar_whatsapp 52XXXXXXXXXX --plantilla hello_world --idioma en_US
    python manage.py probar_whatsapp --ver          # los últimos renglones y su estado

Sin número toma los de `WHATSAPP_TEST_TO` (separados por coma).
"""
import os

from django.core.management.base import BaseCommand

from warehouse import whatsapp
from warehouse.models import NotificationLog


class Command(BaseCommand):
    help = 'Manda un WhatsApp de prueba por la Cloud API de Meta, o muestra los últimos.'

    def add_arguments(self, parser):
        parser.add_argument('numeros', nargs='*')
        parser.add_argument('--texto', default='Hola, prueba de texto libre desde el WMS')
        parser.add_argument('--plantilla', default='')
        parser.add_argument('--idioma', default='en_US')
        parser.add_argument('--ver', action='store_true',
                            help='Mostrar los últimos 10 WhatsApp y su estado')

    def handle(self, *args, **o):
        if o['ver']:
            for r in NotificationLog.objects.filter(channel='WHATSAPP')[:10]:
                self.stdout.write(f'{r.created_at:%d/%m %H:%M}  {self._oculto(r.recipient):>15}  '
                                  f'{r.status:<9} {r.subject}  {r.detail}')
            return

        numeros = o['numeros'] or [
            n for n in os.getenv('WHATSAPP_TEST_TO', '').split(',') if n.strip()]
        if not numeros:
            self.stderr.write('Falta el número (o WHATSAPP_TEST_TO).')
            return

        asunto = f"plantilla {o['plantilla']}" if o['plantilla'] else 'texto libre'
        for numero in numeros:
            wamid, error = whatsapp.enviar(
                numero, texto=o['texto'], plantilla=o['plantilla'] or None,
                idioma=o['idioma'])
            NotificationLog.objects.create(
                channel='WHATSAPP', event='MANUAL',
                status='FAILED' if error else 'SENT',
                recipient=whatsapp.solo_digitos(numero), subject=f'Prueba: {asunto}',
                detail=error or '', provider_id=wamid or '')
            self.stdout.write(f'{self._oculto(numero)}: ' + (
                f'rechazado — {error}' if error else 'aceptado por Meta'))

    @staticmethod
    def _oculto(numero):
        d = whatsapp.solo_digitos(numero)
        return f'{d[:3]}…{d[-2:]}' if len(d) > 5 else d
