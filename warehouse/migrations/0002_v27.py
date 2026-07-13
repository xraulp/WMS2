from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('warehouse', '0001_initial'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]
    operations = [
        migrations.AddField(model_name='warehouseoperation', name='created_by',
            field=models.ForeignKey(blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='created_operations', to='auth.user')),
        migrations.AddField(model_name='warehouseoperation', name='customer_notes',
            field=models.TextField(blank=True, null=True)),
        migrations.AlterField(model_name='userprofile', name='role',
            field=models.CharField(
                choices=[('superadmin','Super Administrator'),('manager','Manager'),
                         ('staff','Staff'),('customer','Customer')],
                default='manager', max_length=20)),
        migrations.AddField(model_name='userprofile', name='delete_password',
            field=models.CharField(blank=True, max_length=128, null=True)),
        migrations.CreateModel(name='DeletionLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('deleted_at', models.DateTimeField(auto_now_add=True)),
                ('custom_id', models.CharField(max_length=20)),
                ('operation_type', models.CharField(max_length=5)),
                ('operation_date', models.DateField(blank=True, null=True)),
                ('customer_name', models.CharField(blank=True, max_length=200)),
                ('description', models.TextField(blank=True)),
                ('reason', models.TextField(blank=True, null=True)),
                ('deleted_by', models.ForeignKey(null=True,
                    on_delete=django.db.models.deletion.SET_NULL, to='auth.user')),
            ],
            options={'ordering': ['-deleted_at']}),
    ]
