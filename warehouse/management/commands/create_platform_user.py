"""Crea o actualiza un usuario del nivel de plataforma.

Hace falta un camino que no pase por la interfaz, porque hay un problema del
huevo y la gallina: la pantalla que reparte este acceso solo la ve quien ya lo
tiene. Hoy eso se resuelve porque `is_superuser` sigue contando como
administrador de plataforma, pero el objetivo es poder retirar ese flag, y
entonces este comando es la única forma de volver a entrar.

    python manage.py create_platform_user ana --role admin
    python manage.py create_platform_user soporte --role staff --password xxx

Si el usuario ya existe se le concede el acceso sin tocar su contraseña, salvo
que se pase `--password`.
"""

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from warehouse.models import PLATFORM_ROLE_CHOICES, PlatformUser, UserProfile


class Command(BaseCommand):
    help = 'Concede acceso de plataforma (admin o staff) a un usuario.'

    def add_arguments(self, parser):
        parser.add_argument('username')
        parser.add_argument('--role', default='staff',
                            choices=[r for r, _ in PLATFORM_ROLE_CHOICES])
        parser.add_argument('--password', default=None,
                            help='Obligatoria si el usuario no existe todavia.')

    @transaction.atomic
    def handle(self, *args, **options):
        username = options['username']
        rol      = options['role']
        password = options['password']

        usuario = User.objects.filter(username=username).first()

        if usuario is None:
            if not password:
                raise CommandError(
                    f'El usuario "{username}" no existe: hace falta --password '
                    f'para crearlo.'
                )
            usuario = User.objects.create_user(username=username, password=password)
            self.stdout.write(f'Usuario "{username}" creado.')
        elif password:
            usuario.set_password(password)
            usuario.save(update_fields=['password'])
            self.stdout.write(f'Contrasena de "{username}" actualizada.')

        acceso, creado = PlatformUser.objects.get_or_create(
            user=usuario, defaults={'role': rol})
        if not creado and acceso.role != rol:
            acceso.role = rol
            acceso.save(update_fields=['role'])

        # Aviso, no error: que alguien tenga los dos niveles es legitimo -es la
        # situacion de hoy- pero conviene saberlo, porque entonces entra al
        # tablero de su empresa y no a la pantalla de plataforma.
        if UserProfile.objects.filter(user=usuario, tenant__isnull=False).exists():
            self.stdout.write(self.style.WARNING(
                f'Ojo: "{username}" tambien pertenece a una empresa, asi que al '
                f'entrar ira al tablero de esa empresa. La plataforma le queda '
                f'en la pestana Platform, o en /platform/.'
            ))

        self.stdout.write(self.style.SUCCESS(
            f'"{username}" tiene acceso de plataforma como {rol}.'
        ))
