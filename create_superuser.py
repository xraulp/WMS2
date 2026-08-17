# create_superuser.py
#
# Corre en cada deploy desde build.sh. Solo crea el superusuario si no existe;
# nunca cambia la contraseña de uno que ya este creado.
#
# La contraseña **no tiene valor por omision**. Antes caia en 'admin123' cuando
# SUPERUSER_PASSWORD no estaba definida, asi que el admin de produccion podia
# haber quedado creado con una contraseña publicada en el repositorio. Sin la
# variable el script no crea nada y avisa: es preferible quedarse sin
# superusuario que tener uno con contraseña conocida.
import os
import sys

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'warehouse_system.settings')
django.setup()

from django.contrib.auth.models import User

username = os.environ.get('SUPERUSER_USERNAME', 'admin')
email = os.environ.get('SUPERUSER_EMAIL', '')
password = os.environ.get('SUPERUSER_PASSWORD', '')

if User.objects.filter(username=username).exists():
    print(f"El superusuario '{username}' ya existe; no se toca.")
    sys.exit(0)

if not password:
    # Se sale con 0 a proposito: un deploy no debe fallar por esto, y el caso
    # normal es que el superusuario ya exista y la variable no haga falta.
    print(f"[WARN] SUPERUSER_PASSWORD no esta definida: no se crea el "
          f"superusuario '{username}'. Definela en el entorno si hace falta "
          f"crearlo, o crealo a mano con 'python manage.py createsuperuser'.")
    sys.exit(0)

User.objects.create_superuser(username, email, password)
print(f"Superusuario '{username}' creado.")
