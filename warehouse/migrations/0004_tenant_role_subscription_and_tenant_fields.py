"""
Migracion neutralizada a proposito. NO agregar operaciones aqui.

Historia: 0001_initial fue regenerada a partir del modelo ya completo, asi que
ya crea Tenant, Role y Subscription, y ya trae el campo tenant en Catalog,
WarehouseOperation y OperationDocument. Esta 0004 volvia a crear esos tres
modelos, de modo que el arbol de migraciones no se podia aplicar desde cero
("table warehouse_tenant already exists"). En la base de datos de produccion
nunca se noto porque 0004 se marco como aplicada sin ejecutarse.

Se deja el archivo (vaciado) en vez de borrarlo para no romper el historial:
produccion ya la tiene registrada como aplicada, y las migraciones siguientes
dependen de este nombre. El esquema que declaraba ya lo aporta 0001.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('warehouse', '0002_userprofile_customer_userprofile_delete_password_and_more'),
    ]

    operations = []
