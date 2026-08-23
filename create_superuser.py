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

from warehouse.models import PlatformUser

# Si ya hay un administrador de plataforma, este script no tiene nada que hacer.
#
# Es la misma regla que ya aplica `platform_role`: la llave maestra existe para
# resolver el huevo y la gallina -- hace falta una llave para crear al primer
# administrador -- y deja de valer en cuanto ese sucesor existe.
#
# Sin esta comprobacion el script es una puerta que se vuelve a abrir sola.
# Basta con que el usuario llamado como `SUPERUSER_USERNAME` deje de existir
# -- porque se renombro, por ejemplo -- para que el siguiente deploy fabrique
# un superusuario nuevo, con el admin de Django y los datos de todas las
# empresas dentro, sin que nadie lo haya pedido.
if PlatformUser.objects.filter(role='admin').exists():
    print("Ya hay un administrador de plataforma; no se crea ningun "
          "superusuario. Si de verdad hiciera falta uno, se crea a mano con "
          "'python manage.py createsuperuser'.")
    sys.exit(0)

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
