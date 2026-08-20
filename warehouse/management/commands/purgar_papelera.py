"""Destruye los archivos que llevan demasiado tiempo en la papelera.

La papelera no se vaciaba sola. El administrador destruye de uno en uno desde la
pestaña Deletions, que está bien para el archivo concreto que estorba, pero con
el tiempo se acumula todo lo demás: sigue ocupando el bucket y sigue siendo
recuperable mucho después de que a nadie le importe.

Sin argumentos no borra nada: enseña qué se llevaría.

    python manage.py purgar_papelera                 # informe, no toca nada
    python manage.py purgar_papelera --confirmar     # destruye lo que pase de 90 dias
    python manage.py purgar_papelera --dias 30 --confirmar
    python manage.py purgar_papelera --empresa norte --confirmar

Lo que destruye es irreversible y no vuelve a registrarse: el renglón de la
bitácora se escribió cuando el archivo entró en la papelera, con quién lo quitó y
por qué, y ese renglón se queda.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from warehouse.models import OperationDocument, Tenant

DIAS_POR_DEFECTO = 90


class Command(BaseCommand):
    help = 'Destruye los archivos que llevan mas de N dias en la papelera.'

    def add_arguments(self, parser):
        parser.add_argument('--dias', type=int, default=DIAS_POR_DEFECTO,
                            help=f'Antiguedad minima en la papelera (por defecto {DIAS_POR_DEFECTO}).')
        parser.add_argument('--empresa', default=None,
                            help='Subdominio del tenant. Sin esto, todas.')
        parser.add_argument('--confirmar', action='store_true',
                            help='Sin esto solo informa.')

    def handle(self, *args, **options):
        dias = options['dias']
        if dias < 1:
            raise CommandError('--dias tiene que ser al menos 1: purgar la papelera '
                               'del mismo dia deja sin efecto la papelera.')

        corte = timezone.now() - timedelta(days=dias)
        candidatos = (OperationDocument.todos
                      .filter(deleted_at__isnull=False, deleted_at__lte=corte)
                      .select_related('operation', 'tenant'))

        if options['empresa']:
            tenant = Tenant.objects.filter(subdomain=options['empresa']).first()
            if tenant is None:
                raise CommandError(f'No hay ninguna empresa con subdominio '
                                   f'"{options["empresa"]}".')
            candidatos = candidatos.filter(tenant=tenant)

        candidatos = list(candidatos)
        if not candidatos:
            self.stdout.write(f'No hay nada en la papelera con mas de {dias} dias.')
            return

        self.stdout.write(f'{len(candidatos)} archivo(s) llevan mas de {dias} dias '
                          f'en la papelera:')
        for doc in candidatos:
            nombre = doc.digital_name or doc.original_name or '(sin nombre)'
            empresa = doc.tenant.name if doc.tenant else 'sin empresa'
            self.stdout.write(f'  - {nombre} · {doc.operation.custom_id} · {empresa} '
                              f'· en la papelera desde {doc.deleted_at:%Y-%m-%d}')

        if not options['confirmar']:
            self.stdout.write(self.style.WARNING(
                'Informe solamente. Para destruirlos, repite con --confirmar.'))
            return

        destruidos, fallidos = 0, 0
        for doc in candidatos:
            try:
                if doc.file:
                    doc.file.delete(save=False)
            except Exception as e:
                # El archivo puede haberse borrado ya por otro camino. La fila
                # se va igual: dejarla seria mantener una papelera que promete
                # un archivo que no esta.
                fallidos += 1
                self.stdout.write(self.style.WARNING(
                    f'  No se pudo borrar del almacen {doc.file.name}: {e}'))
            doc.delete()
            destruidos += 1

        self.stdout.write(self.style.SUCCESS(
            f'{destruidos} archivo(s) destruidos definitivamente.'))
        if fallidos:
            self.stdout.write(self.style.WARNING(
                f'{fallidos} seguian sin poder borrarse del almacen; su registro '
                f'se retiro igualmente.'))
