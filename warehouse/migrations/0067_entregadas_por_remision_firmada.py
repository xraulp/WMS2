"""
Las tareas que ya tienen la remision firmada subida pasan a entregadas.

Desde ahora la remision firmada marca la entrega sola; las que la subieron
antes se quedaron "en cruce". Se toma como momento de la entrega el de la
primera remision firmada, y como quien entrego, quien la subio.
"""
from django.db import migrations


def entregar(apps, schema_editor):
    CrossingTask = apps.get_model('warehouse', 'CrossingTask')
    DocumentoDeEntrega = apps.get_model('warehouse', 'DocumentoDeEntrega')
    pendientes = (CrossingTask.objects
                  .filter(entregada_en__isnull=True,
                          documentos_de_entrega__tipo='REMISION_FIRMADA')
                  .exclude(estado='CANCELADA').distinct())
    for tarea in pendientes:
        doc = (DocumentoDeEntrega.objects
               .filter(task=tarea, tipo='REMISION_FIRMADA')
               .order_by('uploaded_at').first())
        tarea.entregada_en = doc.uploaded_at
        tarea.entregada_por_id = doc.uploaded_by_id
        tarea.estado = 'CRUZADA'
        tarea.save(update_fields=['entregada_en', 'entregada_por', 'estado'])


class Migration(migrations.Migration):

    dependencies = [
        ('warehouse', '0066_salida_de_la_tarea_de_cruce'),
    ]

    operations = [
        migrations.RunPython(entregar, migrations.RunPython.noop),
    ]
