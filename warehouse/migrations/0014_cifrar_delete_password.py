"""
Cifra las contrasenas de borrado que estaban guardadas en claro.

`UserProfile.delete_password` se guardaba tal cual se tecleaba y ademas se
pintaba en la pantalla de gestion de usuarios. Mientras el borrado estuvo
reservado a los roles de casa era una molestia; desde que esa contrasena es lo
que autoriza al staff a borrar, es el control mismo, y un control no puede estar
a la vista de cualquiera que abra la tabla.

La migracion es de ida y no tiene vuelta posible: del hash no se recupera el
texto, asi que revertirla deja las contrasenas cifradas y hay que volver a
ponerlas. El codigo tolera los dos formatos, de modo que nadie se queda fuera
mientras tanto.
"""
from django.contrib.auth.hashers import identify_hasher, make_password
from django.db import migrations


def cifrar(apps, schema_editor):
    UserProfile = apps.get_model('warehouse', 'UserProfile')
    for perfil in UserProfile.objects.exclude(delete_password__isnull=True).exclude(delete_password=''):
        try:
            identify_hasher(perfil.delete_password)
        except ValueError:
            perfil.delete_password = make_password(perfil.delete_password)
            perfil.save(update_fields=['delete_password'])


def no_se_puede_volver(apps, schema_editor):
    """
    Del hash no sale el texto original. Se deja pasar para no bloquear un
    `migrate` hacia atras, pero las contrasenas hay que volver a escribirlas.
    """


class Migration(migrations.Migration):

    dependencies = [
        ('warehouse', '0013_alter_operationdocument_options_and_more'),
    ]

    operations = [
        migrations.RunPython(cifrar, no_se_puede_volver),
    ]
