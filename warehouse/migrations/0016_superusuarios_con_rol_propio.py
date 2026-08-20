"""
Escribe en el perfil el rol que el superusuario venia ejerciendo.

Hasta ahora cada predicado de `UserProfile` llevaba pegado un
`or self.user.is_superuser`, de modo que un superusuario de Django mandaba en su
empresa aunque su perfil dijera 'manager' o 'staff'. Al retirar ese atajo, el
rol escrito pasa a ser el unico que cuenta, y quien tuviera uno menor se
degradaria de golpe en el deploy sin que nadie lo hubiera decidido.

Esta migracion conserva el estado de hecho: a cada superusuario con perfil de
empresa se le escribe 'superadmin', que es lo que era en la practica. No toca a
los de rol 'customer' -un cliente que ademas sea superusuario es un error de
datos, no un permiso que valga la pena conservar- ni crea perfiles a quien no
tenga: a que empresa pertenece no lo sabe nadie desde aqui, y darle uno seria
inventarlo.

No tiene vuelta: el rol anterior no queda guardado en ningun sitio. Si alguien
debia ser manager y no superadmin, se corrige desde la pestana Users, que es
donde se decide.
"""
from django.db import migrations


def escribir_el_rol_que_ya_ejercian(apps, schema_editor):
    UserProfile = apps.get_model('warehouse', 'UserProfile')
    (UserProfile.objects
     .filter(user__is_superuser=True)
     .exclude(role='customer')
     .exclude(role='superadmin')
     .update(role='superadmin'))


def no_se_puede_volver(apps, schema_editor):
    """
    Deshacer pediria saber que rol tenia cada quien antes, y eso no se guardo.
    Revertir no rompe nada: deja los roles como quedaron.
    """


class Migration(migrations.Migration):

    dependencies = [
        ('warehouse', '0015_quitar_plain_password'),
    ]

    operations = [
        migrations.RunPython(escribir_el_rol_que_ya_ejercian, no_se_puede_volver),
    ]
