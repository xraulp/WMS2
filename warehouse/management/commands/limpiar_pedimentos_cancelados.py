"""Limpia, de una vez, los pedimentos de tareas de cruce canceladas antes de que
existiera la limpieza automatica (deploy de `8487a34`, 22 nov 2026).

Desde ese deploy, cancelar una tarea deja sus pedimentos en borrador y sin
documentos, para que al volver a un cruce se suban actualizados. Lo cancelado
antes se quedo con sus documentos viejos; esto hace con ellos lo mismo que hoy
hace cancelar (`Pedimento.limpiar_al_salir_del_cruce`):

* Toma los pedimentos que estuvieron en una tarea **cancelada**.
* Salta los **validados y pagados** (ya se pagaron en aduana) y los que siguen
  en una tarea **viva** (esos estan en uso).
* A los demas les borra los documentos y los regresa a borrador, sin la
  aprobacion del cliente ni su fecha de pago, y lo anota en su historial de
  cambios.

Lo que **no** puede encontrar: los pedimentos que se *sacaron* de una tarea con
"Quitar del cruce" antes del deploy. Al quitarlos no quedo registro de en que
tarea estuvieron; esos se limpian a mano.

Sin argumentos no toca nada: enseña que haria.

    python manage.py limpiar_pedimentos_cancelados              # informe
    python manage.py limpiar_pedimentos_cancelados --confirmar  # aplica
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from warehouse.models import CrossingTask, Pedimento


class Command(BaseCommand):
    help = ('Deja en borrador y sin documentos los pedimentos de tareas de cruce '
            'canceladas (salvo validados, pagados o en otra tarea viva).')

    def add_arguments(self, parser):
        parser.add_argument('--confirmar', action='store_true',
                            help='Sin esto solo informa.')

    def handle(self, *args, **options):
        confirmar = options['confirmar']
        candidatos = (Pedimento.objects
                      .filter(renglones__operation__cruces__task__estado=CrossingTask.CANCELADA)
                      .select_related('tenant', 'customer')
                      .distinct()
                      .order_by('tenant__name', 'customer__name', 'pk'))

        limpiar, saltados = [], []
        for p in candidatos:
            docs = p.documentos.count()
            if p.estado in (Pedimento.VALIDADO, Pedimento.PAGADO):
                saltados.append((p, 'validado o pagado'))
            elif p.tareas_de_cruce().exists():
                saltados.append((p, 'sigue en otra tarea'))
            elif not docs and p.estado == Pedimento.BORRADOR:
                saltados.append((p, 'ya esta limpio'))
            else:
                limpiar.append((p, docs))

        self.stdout.write(self.style.MIGRATE_HEADING('Se limpian'))
        if not limpiar:
            self.stdout.write('Ninguno.')
        for p, docs in limpiar:
            self.stdout.write(f'  {p.tenant} · {p.customer.abbreviation or p.customer.name} · '
                              f'{p.etiqueta} · {p.get_estado_display()} · {docs} documento(s)')

        self.stdout.write(self.style.MIGRATE_HEADING('No se tocan'))
        if not saltados:
            self.stdout.write('Ninguno.')
        for p, motivo in saltados:
            self.stdout.write(f'  {p.tenant} · {p.customer.abbreviation or p.customer.name} · '
                              f'{p.etiqueta} · {motivo}')

        if not confirmar:
            self.stdout.write(self.style.WARNING(
                f'\nInforme solamente: {len(limpiar)} pedimento(s) se limpiarian. '
                'Para aplicar, repite con --confirmar.'))
            return

        hechos = 0
        with transaction.atomic():
            for p, _docs in limpiar:
                if p.limpiar_al_salir_del_cruce(None):
                    hechos += 1
        self.stdout.write(self.style.SUCCESS(f'\n{hechos} pedimento(s) limpiados.'))
