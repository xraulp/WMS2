"""
El camion compartido cambia de prefijo: de `CM` a `TCC` (tarea de cruce
compartida, o cruce consolidado). Los que ya existen se renombran conservando
su fecha y su consecutivo: CM261116-0001 pasa a TCC261116-0001.
"""
from django.db import migrations


def a_tcc(apps, schema_editor):
    Camion = apps.get_model('warehouse', 'Camion')
    for c in Camion.objects.filter(custom_id__startswith='CM'):
        c.custom_id = 'TCC' + c.custom_id[2:]
        c.save(update_fields=['custom_id'])


def a_cm(apps, schema_editor):
    Camion = apps.get_model('warehouse', 'Camion')
    for c in Camion.objects.filter(custom_id__startswith='TCC'):
        c.custom_id = 'CM' + c.custom_id[3:]
        c.save(update_fields=['custom_id'])


class Migration(migrations.Migration):

    dependencies = [
        ('warehouse', '0067_entregadas_por_remision_firmada'),
    ]

    operations = [
        migrations.RunPython(a_tcc, a_cm),
    ]
