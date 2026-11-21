"""La limpieza periodica: lo que caduca solo y nadie va a volver a buscar.

Es lo que corre el Cron Job de Render una vez por semana. Junta tres cosas:

* **La papelera.** Los archivos que llevan mas de 90 dias en ella se destruyen
  (es `purgar_papelera`, que sigue existiendo por si se quiere correr a mano).
* **Las sesiones vencidas.** Django no las borra solo; `clearsessions` las
  quita de `django_session`.
* **La bitacora de avisos vieja.** Los renglones de `NotificationLog` con mas
  de un año. Sirven para contestar "¿le llego el correo?", y esa pregunta nadie
  la hace un año despues. Las facturas enviadas se quedan: son la constancia de
  que la empresa recibio su cobro.

Lo que **no** toca: operaciones, pedimentos, tareas ni documentos vivos del
expediente. Esos son el historial del cliente y la evidencia ante la aduana, y
no caducan a los cuatro meses.

Sin argumentos no borra nada: enseña que haria.

    python manage.py mantenimiento                  # informe
    python manage.py mantenimiento --confirmar      # lo que corre el cron
"""
from datetime import timedelta

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

from warehouse.models import NotificationLog

DIAS_DE_BITACORA = 365


class Command(BaseCommand):
    help = 'Limpieza periodica: papelera, sesiones vencidas y bitacora de avisos vieja.'

    def add_arguments(self, parser):
        parser.add_argument('--confirmar', action='store_true',
                            help='Sin esto solo informa.')
        parser.add_argument('--dias-papelera', type=int, default=90)
        parser.add_argument('--dias-bitacora', type=int, default=DIAS_DE_BITACORA)

    def handle(self, *args, **options):
        confirmar = options['confirmar']

        self.stdout.write(self.style.MIGRATE_HEADING('Papelera'))
        argumentos = ['--dias', str(options['dias_papelera'])]
        if confirmar:
            argumentos.append('--confirmar')
        call_command('purgar_papelera', *argumentos, stdout=self.stdout)

        self.stdout.write(self.style.MIGRATE_HEADING('Sesiones vencidas'))
        if confirmar:
            call_command('clearsessions')
            self.stdout.write('Sesiones vencidas borradas.')
        else:
            self.stdout.write('Se borrarian con --confirmar.')

        self.stdout.write(self.style.MIGRATE_HEADING('Bitacora de avisos'))
        corte = timezone.now() - timedelta(days=options['dias_bitacora'])
        viejos = (NotificationLog.objects
                  .filter(created_at__lt=corte)
                  .exclude(event='INVOICE_SENT'))
        cuantos = viejos.count()
        if not cuantos:
            self.stdout.write(f'Nada con mas de {options["dias_bitacora"]} dias.')
        elif confirmar:
            viejos.delete()
            self.stdout.write(f'{cuantos} renglon(es) borrados.')
        else:
            self.stdout.write(f'{cuantos} renglon(es) con mas de '
                              f'{options["dias_bitacora"]} dias se borrarian con --confirmar.')

        if not confirmar:
            self.stdout.write(self.style.WARNING(
                '\nInforme solamente. Para aplicar, repite con --confirmar.'))
