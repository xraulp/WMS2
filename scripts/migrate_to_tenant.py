# scripts/migrate_to_tenant.py
import os
import sys
import django

# Agrega la ruta base del proyecto al PYTHONPATH
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'warehouse_system.settings')
django.setup()

from warehouse.models import Tenant, WarehouseOperation, Catalog, OperationDocument, UserProfile
from django.contrib.auth.models import User

def migrate():
    # 1. Crear tenant por defecto
    default_tenant, created = Tenant.objects.get_or_create(
        subdomain='default',
        defaults={
            'name': 'Default Organization',
            'type': 'organization',
            'is_active': True,
            'billing_email': 'admin@dyser.com',
            'plan': 'pro'
        }
    )
    print(f"✅ Tenant default: {default_tenant.name} (ID: {default_tenant.id})")
    
    # 2. Asignar todos los registros existentes al tenant default
    ops_count = WarehouseOperation.objects.update(tenant=default_tenant)
    print(f"✅ {ops_count} operaciones asignadas a tenant default")
    
    catalog_count = Catalog.objects.update(tenant=default_tenant)
    print(f"✅ {catalog_count} entradas de catálogo asignadas a tenant default")
    
    docs_count = OperationDocument.objects.update(tenant=default_tenant)
    print(f"✅ {docs_count} documentos asignados a tenant default")
    
    # 3. Asignar tenant a los usuarios existentes
    for user in User.objects.all():
        profile, created = UserProfile.objects.get_or_create(user=user)
        profile.tenant = default_tenant
        profile.save()
    print(f"✅ {User.objects.count()} usuarios asignados a tenant default")
    
    print("🎉 Migración completada exitosamente.")

if __name__ == '__main__':
    migrate()