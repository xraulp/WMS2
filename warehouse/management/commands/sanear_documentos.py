"""Revisa los documentos del expediente y arregla lo que se puede arreglar solo.

Quedaron dos residuos de errores ya corregidos, y las filas viejas no se
reescribieron cuando se arreglaron las causas:

* **Documentos sin empresa.** La vista que crea la operación no pasaba el
  `tenant` al adjuntar archivos —`digital_upload` sí—, así que esas filas
  quedaron con el campo en NULL. La causa está arreglada desde entonces. Se
  puede reparar sin riesgo: el tenant de un documento es forzosamente el de su
  operación, no hay nada que adivinar.
* **Filas cuyo archivo no está en el almacén.** Vienen de cuando el borrado
  usaba `file.path`, que con R2 no existe. Aquí solo se informa: que falte el
  objeto no dice si la fila sobra —puede ser un archivo que alguien quiere
  reponer— y destruir el registro de un documento es decisión de una persona,
  no de un comando de mantenimiento.

Sin argumentos no toca nada: enseña qué haría.

    python manage.py sanear_documentos                 # informe
    python manage.py sanear_documentos --confirmar     # asigna las empresas
    python manage.py sanear_documentos --sin-almacen   # ademas contrasta el bucket

`--sin-almacen` va aparte porque pregunta al almacén una vez por documento: con
el expediente entero eso son muchas llamadas de red y varios minutos, mientras
que lo demás son dos consultas.
"""
from django.core.management.base import BaseCommand

from warehouse.models import OperationDocument


class Command(BaseCommand):
    help = 'Informa y repara los documentos del expediente con datos incompletos.'

    def add_arguments(self, parser):
        parser.add_argument('--confirmar', action='store_true',
                            help='Sin esto solo informa.')
        parser.add_argument('--sin-almacen', action='store_true',
                            help='Comprueba ademas si el archivo esta en el almacen '
                                 '(lento: una llamada por documento).')

    def handle(self, *args, **options):
        self._empresas_que_faltan(options['confirmar'])
        self._sin_archivo_registrado()
        if options['sin_almacen']:
            self._contrastar_con_el_almacen()
        else:
            self.stdout.write(
                '\nPara contrastar la base con el bucket, repite con --sin-almacen.')

    # ── Documentos sin empresa ────────────────────────────────────────────────

    def _empresas_que_faltan(self, confirmar):
        huerfanos = list(OperationDocument.todos
                         .filter(tenant__isnull=True)
                         .select_related('operation', 'operation__tenant'))

        if not huerfanos:
            self.stdout.write('Todos los documentos tienen empresa asignada.')
            return

        self.stdout.write(f'{len(huerfanos)} documento(s) sin empresa:')
        reparables = []
        for doc in huerfanos:
            empresa = getattr(doc.operation, 'tenant', None)
            nombre = doc.original_name or doc.file.name or '(sin nombre)'
            if empresa is None:
                # Su operacion tampoco tiene empresa. No hay de donde sacarla,
                # y ponerle una a ojo seria mover un documento de compañia.
                self.stdout.write(self.style.WARNING(
                    f'  - {nombre} · {doc.operation.custom_id} · su operacion '
                    f'tampoco tiene empresa: se queda como esta'))
                continue
            self.stdout.write(f'  - {nombre} · {doc.operation.custom_id} '
                              f'→ {empresa.name}')
            reparables.append((doc, empresa))

        if not reparables:
            return

        if not confirmar:
            self.stdout.write(self.style.WARNING(
                f'Informe solamente. Para asignar {len(reparables)} empresa(s), '
                f'repite con --confirmar.'))
            return

        for doc, empresa in reparables:
            doc.tenant = empresa
            doc.save(update_fields=['tenant'])

        self.stdout.write(self.style.SUCCESS(
            f'{len(reparables)} documento(s) quedaron con su empresa asignada.'))

    # ── Filas sin archivo ─────────────────────────────────────────────────────

    def _sin_archivo_registrado(self):
        """Ni siquiera tienen ruta guardada: no hay nada que buscar en el bucket."""
        vacios = list(OperationDocument.todos
                      .filter(file='')
                      .select_related('operation'))
        if not vacios:
            return

        self.stdout.write(f'\n{len(vacios)} documento(s) sin ninguna ruta de archivo:')
        for doc in vacios:
            self.stdout.write(f'  - id {doc.pk} · {doc.operation.custom_id} · '
                              f'{doc.original_name or "(sin nombre)"}')
        self.stdout.write(self.style.WARNING(
            'Estos no se tocan: que falte el archivo no dice si la fila sobra.'))

    # ── Contraste con el almacen ──────────────────────────────────────────────

    def _contrastar_con_el_almacen(self):
        documentos = list(OperationDocument.todos
                          .exclude(file='')
                          .select_related('operation', 'tenant'))
        self.stdout.write(f'\nContrastando {len(documentos)} documento(s) con el '
                          f'almacen...')

        ausentes = []
        for doc in documentos:
            try:
                if not doc.file.storage.exists(doc.file.name):
                    ausentes.append(doc)
            except Exception as e:
                self.stdout.write(self.style.WARNING(
                    f'  No se pudo comprobar {doc.file.name}: {e}'))

        if not ausentes:
            self.stdout.write(self.style.SUCCESS(
                'Todos los archivos registrados estan en el almacen.'))
            return

        self.stdout.write(self.style.WARNING(
            f'{len(ausentes)} documento(s) con fila pero sin archivo:'))
        for doc in ausentes:
            estado = 'en la papelera' if doc.en_papelera else 'en el expediente'
            self.stdout.write(f'  - id {doc.pk} · {doc.operation.custom_id} · '
                              f'{doc.original_name or "(sin nombre)"} · {estado}')
        self.stdout.write(
            'No se retiran aqui. Los de la papelera se los lleva `purgar_papelera` '
            'cuando cumplan su plazo; los del expediente los decide una persona.')
