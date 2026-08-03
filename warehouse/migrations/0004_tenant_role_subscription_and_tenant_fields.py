import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('warehouse', '0002_userprofile_customer_userprofile_delete_password_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='Tenant',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200, verbose_name='Nombre')),
                ('type', models.CharField(choices=[('organization', 'Corporativo / Empresa Matriz'), ('branch', 'Sucursal / Franquicia')], max_length=20, verbose_name='Tipo')),
                ('subdomain', models.CharField(max_length=50, unique=True, verbose_name='Subdominio')),
                ('is_active', models.BooleanField(default=True, verbose_name='Activo')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Creado el')),
                ('config', models.JSONField(blank=True, default=dict, verbose_name='Configuración')),
                ('billing_email', models.EmailField(blank=True, max_length=254, null=True, verbose_name='Email de Facturación')),
                ('plan', models.CharField(choices=[('starter', 'Starter'), ('pro', 'Pro'), ('enterprise', 'Enterprise')], default='starter', max_length=50, verbose_name='Plan')),
                ('parent', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='children', to='warehouse.tenant', verbose_name='Tenant Padre')),
            ],
            options={
                'verbose_name': 'Tenant',
                'verbose_name_plural': 'Tenants',
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='Subscription',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('start_date', models.DateField(auto_now_add=True, verbose_name='Fecha de Inicio')),
                ('end_date', models.DateField(blank=True, null=True, verbose_name='Fecha de Fin')),
                ('storage_used_gb', models.FloatField(default=0, verbose_name='Almacenamiento usado (GB)')),
                ('operations_count', models.IntegerField(default=0, verbose_name='Número de Operaciones')),
                ('invoice_number', models.CharField(blank=True, max_length=50, null=True, verbose_name='Número de Factura')),
                ('invoice_date', models.DateField(blank=True, null=True, verbose_name='Fecha de Factura')),
                ('amount_usd', models.DecimalField(decimal_places=2, default=0, max_digits=10, verbose_name='Monto (USD)')),
                ('is_active', models.BooleanField(default=True, verbose_name='Activo')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Creado el')),
                ('config', models.JSONField(blank=True, default=dict, verbose_name='Configuración')),
                ('billing_email', models.EmailField(blank=True, max_length=254, null=True, verbose_name='Email de Facturación')),
                ('plan', models.CharField(choices=[('starter', 'Starter'), ('pro', 'Pro'), ('enterprise', 'Enterprise')], default='starter', max_length=50, verbose_name='Plan')),
                ('tenant', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='subscription', to='warehouse.tenant', verbose_name='Tenant')),
            ],
            options={
                'verbose_name': 'Tenant',
                'verbose_name_plural': 'Tenants',
                'ordering': ['tenant__name'],
            },
        ),
        migrations.CreateModel(
            name='Role',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=50, verbose_name='Nombre del Rol')),
                ('description', models.TextField(blank=True, null=True, verbose_name='Descripción')),
                ('parent', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='children_roles', to='warehouse.role', verbose_name='Rol Padre')),
                ('permissions', models.ManyToManyField(blank=True, to='auth.permission', verbose_name='Permisos')),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='roles', to='warehouse.tenant', verbose_name='Tenant')),
            ],
            options={
                'verbose_name': 'Rol',
                'verbose_name_plural': 'Roles',
                'unique_together': {('name', 'tenant')},
            },
        ),
        migrations.AddField(
            model_name='catalog',
            name='tenant',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='catalog_entries', to='warehouse.tenant'),
        ),
        migrations.AddField(
            model_name='operationdocument',
            name='tenant',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='documents', to='warehouse.tenant'),
        ),
        migrations.AddField(
            model_name='warehouseoperation',
            name='tenant',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='operations', to='warehouse.tenant'),
        ),
    ]
