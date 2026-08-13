from django.db import migrations


def asignar_tenant_a_deletionlog(apps, schema_editor):
    """
    Los registros de DeletionLog anteriores a la migracion 0006 quedaron con
    tenant NULL porque el modelo no tenia ese campo cuando se crearon. La
    operacion original ya fue borrada, asi que no hay forma de reconstruir el
    tenant real: se asignan al tenant 'default', igual que en 0007. En la
    practica es correcto porque en ese momento solo operaba un tenant.
    """
    Tenant = apps.get_model('warehouse', 'Tenant')
    DeletionLog = apps.get_model('warehouse', 'DeletionLog')

    tenant = Tenant.objects.filter(subdomain='default').first()
    if tenant is None:
        return

    DeletionLog.objects.filter(tenant__isnull=True).update(tenant=tenant)


def revertir(apps, schema_editor):
    # No revertimos para no perder la asignacion por accidente.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('warehouse', '0008_clean_subscription_leftover_fields'),
    ]

    operations = [
        migrations.RunPython(asignar_tenant_a_deletionlog, revertir),
    ]
