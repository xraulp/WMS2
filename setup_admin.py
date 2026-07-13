"""
Run this once after migrations to create the admin superuser.
Usage: python setup_admin.py
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'warehouse_system.settings')
django.setup()

from django.contrib.auth import get_user_model

User = get_user_model()

username = 'admin'
password = 'admin123'
email = 'admin@warehouse.com'

if not User.objects.filter(username=username).exists():
    User.objects.create_superuser(username=username, password=password, email=email)
    print(f"✓ Superuser created: username='{username}' password='{password}'")
    print("  → Change this password immediately in production!")
else:
    print(f"✓ Superuser '{username}' already exists.")
