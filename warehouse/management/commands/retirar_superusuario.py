"""Informa sobre los superusuarios de Django y retira el flag cuando toca.

El `is_superuser` da a la vez tres cosas que no tienen por qué ir juntas: el
admin de Django con los datos de todas las empresas, el panel de plataforma, y
—hasta que se retiró el atajo— el rol más alto dentro de la propia empresa.
Sustituirlo tiene un orden, y el paso peligroso es el último: quitarle el flag
al único que lo tiene sin haber comprobado que alguien más puede entrar.

Sin argumentos hace un informe y no toca nada:

    python manage.py retirar_superusuario

Con un nombre de usuario, retira el flag después de comprobar que queda un
administrador de plataforma:

    python manage.py retirar_superusuario admin

El acceso al admin de Django (`is_staff`) se retira junto con el flag, porque
sin superusuario ese panel no muestra nada útil y sí sigue siendo una puerta.
Para conservarlo, `--conservar-admin-django`.
"""

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from warehouse.models import PlatformUser, UserProfile


class Command(BaseCommand):
    help = 'Informa sobre los superusuarios de Django, o retira el flag a uno.'

    def add_arguments(self, parser):
        parser.add_argument('username', nargs='?', default=None,
                            help='Sin nombre, solo informa.')
        parser.add_argument('--conservar-admin-django', action='store_true',
                            dest='conservar_staff',
                            help='Deja is_staff puesto: sigue abriendo /admin/.')
        parser.add_argument('--force', action='store_true',
                            help='Retira el flag aunque no quede ningun '
                                 'administrador de plataforma. Es quedarse fuera.')

    def handle(self, *args, **options):
        if options['username'] is None:
            return self._informe()
        return self._retirar(options)

    # ── Informe ──────────────────────────────────────────────────────────────

    def _informe(self):
        admins = PlatformUser.objects.filter(role='admin').select_related('user')
        supers = User.objects.filter(is_superuser=True).order_by('username')

        self.stdout.write('Administradores de plataforma:')
        if admins:
            for a in admins:
                self.stdout.write(f'  - {a.user.username}')
        else:
            self.stdout.write(self.style.WARNING(
                '  ninguno. Mientras no haya uno, el is_superuser sigue '
                'abriendo el panel de plataforma; en cuanto exista el primero, '
                'deja de abrirlo.'))

        self.stdout.write('')
        self.stdout.write('Superusuarios de Django:')
        if not supers:
            self.stdout.write('  ninguno.')
            return

        for u in supers:
            perfil = UserProfile.objects.filter(user=u).select_related('tenant').first()
            if perfil is None:
                empresa = self.style.WARNING(
                    'sin perfil de empresa: al retirarle el flag no entra a '
                    'ningun tablero. Si trabaja en una empresa, hay que darle '
                    'de alta en ella desde la pestana Users.')
            else:
                nombre = perfil.tenant.name if perfil.tenant else 'sin empresa asignada'
                empresa = f'{nombre}, rol "{perfil.role}"'
            plataforma = 'si' if PlatformUser.objects.filter(user=u).exists() else 'no'
            self.stdout.write(f'  - {u.username}: {empresa}')
            self.stdout.write(f'      acceso de plataforma propio: {plataforma}')

    # ── Retirada ─────────────────────────────────────────────────────────────

    @transaction.atomic
    def _retirar(self, options):
        username = options['username']
        usuario = User.objects.filter(username=username).first()
        if usuario is None:
            raise CommandError(f'No existe el usuario "{username}".')
        if not usuario.is_superuser:
            raise CommandError(f'"{username}" no es superusuario: no hay nada que retirar.')

        # Lo que importa no es que el usuario conserve algo, sino que despues de
        # esto siga habiendo alguien capaz de administrar la plataforma.
        quedan = PlatformUser.objects.filter(role='admin').exists()
        if not quedan and not options['force']:
            raise CommandError(
                'No hay ningun administrador de plataforma, asi que retirar este '
                'flag deja el producto sin quien lo administre. Crea uno primero:\n'
                '    python manage.py create_platform_user <nombre> --role admin --password ...\n'
                'y comprueba que entra a /platform/ antes de volver aqui.'
            )

        perfil = UserProfile.objects.filter(user=usuario).select_related('tenant').first()

        usuario.is_superuser = False
        campos = ['is_superuser']
        if not options['conservar_staff']:
            usuario.is_staff = False
            campos.append('is_staff')
        usuario.save(update_fields=campos)

        self.stdout.write(self.style.SUCCESS(
            f'"{username}" ya no es superusuario de Django.'))

        if perfil is None:
            self.stdout.write(self.style.WARNING(
                'No tiene perfil de empresa, asi que ahora mismo no entra a '
                'ningun tablero. Si tiene que operar en una, dale de alta desde '
                'la pestana Users de esa empresa.'))
        else:
            empresa = perfil.tenant.name if perfil.tenant else 'sin empresa asignada'
            self.stdout.write(
                f'En {empresa} se queda con el rol "{perfil.role}", que es el '
                f'que dice su perfil.')

        if PlatformUser.objects.filter(user=usuario).exists():
            self.stdout.write('Conserva su acceso de plataforma por PlatformUser.')
        else:
            self.stdout.write('Ya no entra a /platform/.')
