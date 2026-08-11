from django.db import migrations


def asignar_tenant_default(apps, schema_editor):
    Tenant = apps.get_model('warehouse', 'Tenant')
    Catalog = apps.get_model('warehouse', 'Catalog')
    WarehouseOperation = apps.get_model('warehouse', 'WarehouseOperation')
    OperationDocument = apps.get_model('warehouse', 'OperationDocument')

    tenant, _ = Tenant.objects.get_or_create(
        subdomain='default',
        defaults={
            'name': 'DYSER Group LLC',
            'type': 'organization',
            'is_active': True,
            'billing_email': 'admin@example.com',
            'plan': 'pro',
        },
    )

    Catalog.objects.filter(tenant__isnull=True).update(tenant=tenant)
    WarehouseOperation.objects.filter(tenant__isnull=True).update(tenant=tenant)
    OperationDocument.objects.filter(tenant__isnull=True).update(tenant=tenant)


def revertir(apps, schema_editor):
    # No revertimos para no perder la asignacion por accidente.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('warehouse', '0006_deletionlog_tenant'),
    ]

    operations = [
        migrations.RunPython(asignar_tenant_default, revertir),
    ]
