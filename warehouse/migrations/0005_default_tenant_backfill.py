from django.db import migrations


def crear_tenant_default_y_asignar(apps, schema_editor):
    Tenant = apps.get_model('warehouse', 'Tenant')
    UserProfile = apps.get_model('warehouse', 'UserProfile')

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

    UserProfile.objects.filter(tenant__isnull=True).update(tenant=tenant)


def revertir(apps, schema_editor):
    # No revertimos el tenant/asignaciones para no perder datos por accidente.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('warehouse', '0003_userprofile_created_at_userprofile_tenant'),
    ]

    operations = [
        migrations.RunPython(crear_tenant_default_y_asignar, revertir),
    ]
