"""
Migracion neutralizada a proposito. NO agregar operaciones aqui.

Historia: 0001_initial fue regenerada a partir del modelo ya completo, asi que
crea UserProfile con created_at y tenant incluidos. Esta 0003 volvia a agregar
esos dos campos, de modo que el arbol de migraciones no se podia aplicar desde
cero ("duplicate column"). En la base de datos de produccion nunca se noto
porque 0003 se marco como aplicada sin ejecutarse.

Se deja el archivo (vaciado) en vez de borrarlo para no romper el historial:
produccion ya la tiene registrada como aplicada, y las migraciones siguientes
dependen de este nombre. El esquema que declaraba ya lo aporta 0001.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('warehouse', '0004_tenant_role_subscription_and_tenant_fields'),
    ]

    operations = []
