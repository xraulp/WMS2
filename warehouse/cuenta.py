"""
La contrasena propia: cambiarla desde dentro, recuperarla desde fuera.

Hasta ahora una contrasena solo se podia cambiar desde arriba: la pantalla de
usuarios de la empresa se la reasigna a quien la haya perdido, y la de
plataforma ni siquiera eso -- solo crea, cambia el rol y revoca --. El efecto
era doble y ninguno bueno:

* Nadie podia cambiar la suya. Ni siquiera para dejar de usar la que le dieron
  el primer dia, que paso por un mensaje, por un papel o por los dos.
* Quien la perdia dependia de que otra persona se la reasignara, y el
  administrador de plataforma no tenia esa persona: por encima de el no hay
  nadie. Se resolvia entrando al servidor a correr un comando.

Aqui estan las dos piezas que faltaban. La de dentro es propia porque tiene una
regla que la de la pantalla de usuarios no tiene: se pide la contrasena actual.
Un administrador que reasigna no puede saberla; el dueno de la cuenta si, y
exigirla es lo que impide que una sesion abierta y sin vigilancia se convierta
en una cuenta robada.

La de fuera es la de Django, con sus plantillas puestas. No se reescribe: el
enlace de un solo uso, el `uidb64`, el token que caduca y el detalle de no
decir nunca si un correo existe estan resueltos ahi desde hace anos, y
cualquier version propia seria peor.
"""
from django.conf import settings
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth import views as auth_views
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils.translation import gettext as _

from .context_processors import es_telefono
from .models import PlatformUser, UserProfile


def _de_donde_viene(user, request):
    """
    La pantalla a la que devuelve el boton de volver.

    Es la misma decision que toma el login, y por el mismo motivo: un usuario de
    plataforma no tiene empresa y el tablero le responderia 404, y quien entra
    desde un telefono espera volver al movil y no al tablero ancho.
    """
    tiene_empresa = UserProfile.objects.filter(user=user, tenant__isnull=False).exists()
    if not tiene_empresa and PlatformUser.objects.filter(user=user).exists():
        return 'platform_dashboard'
    if es_telefono(request):
        return 'mobile_dashboard'
    return 'dashboard'


def _revisar_la_nueva(usuario, actual, nueva, repetida):
    """
    Devuelve el primer motivo por el que la nueva contrasena no sirve, o None.

    El orden importa: primero la identidad -- quien pide el cambio es el dueno
    de la cuenta --, despues que las dos copias coincidan, y solo al final la
    calidad. Al reves, alguien que se equivoco al repetirla recibiria una
    leccion sobre contrasenas comunes antes de enterarse de su erratas.
    """
    if not usuario.check_password(actual):
        return _('The current password is not correct.')
    if not nueva:
        return _('Type the new password.')
    if nueva != repetida:
        return _('The two copies of the new password do not match.')
    if nueva == actual:
        return _('The new password is the same as the current one.')
    try:
        # Los validadores de `AUTH_PASSWORD_VALIDATORS` estaban configurados y
        # no los usaba nadie: la pantalla de usuarios llama a `set_password` a
        # secas. Aqui si, porque aqui la elige quien la va a escribir todos los
        # dias, que es justo quien tiende a elegir su propio nombre.
        validate_password(nueva, usuario)
    except ValidationError as e:
        return ' '.join(e.messages)
    return None


def _revisar_el_correo(usuario, correo):
    """
    Devuelve el motivo por el que el correo no sirve, o None.

    Se rechaza el que ya tenga otra cuenta. Django no lo exige -- su modelo
    permite repetirlo -- pero el olvido si: pedir la recuperacion con un correo
    compartido manda un mensaje por cada cuenta, y quien lo recibe no sabe cual
    de los dos enlaces es el suyo.
    """
    if not correo:
        # Vaciarlo es legitimo: es renunciar a la recuperacion, no un error.
        return None
    try:
        validate_email(correo)
    except ValidationError:
        return _('That does not look like an email address.')
    if User.objects.filter(email__iexact=correo).exclude(pk=usuario.pk).exists():
        return _('Another account already uses that email address.')
    return None


@login_required
def mi_cuenta(request):
    """
    La cuenta de quien entro: su contrasena y su correo de recuperacion.

    Las dos cosas viven en la misma pantalla a proposito. Una contrasena sin
    correo detras no se puede recuperar, y hoy casi nadie tiene correo puesto:
    de los catorce usuarios de produccion lo tiene uno. Separarlas en dos
    pantallas seria repartir el problema en dos sitios a los que hay que llegar
    por separado.

    No usa `get_tenant_or_404`: el administrador de plataforma no pertenece a
    ninguna empresa y tambien tiene contrasena que cambiar.
    """
    usuario = request.user
    perfil  = UserProfile.objects.filter(user=usuario).select_related('tenant').first()

    msg, msg_error, seccion = '', False, ''

    if request.method == 'POST':
        accion = request.POST.get('action')

        if accion == 'password':
            seccion = 'password'
            actual   = request.POST.get('current_password', '')
            nueva    = request.POST.get('new_password', '')
            repetida = request.POST.get('new_password2', '')
            problema = _revisar_la_nueva(usuario, actual, nueva, repetida)
            if problema:
                msg, msg_error = problema, True
            else:
                usuario.set_password(nueva)
                usuario.save(update_fields=['password'])
                # Sin esto la sesion se invalida con el cambio y la pantalla
                # siguiente seria el login: quien acaba de cambiar su contrasena
                # con exito no tiene por que volver a escribirla.
                update_session_auth_hash(request, usuario)
                msg = _('Password changed. It is not stored anywhere — the only '
                        'copy is the one you remember.')

        elif accion == 'email':
            seccion = 'email'
            correo   = request.POST.get('email', '').strip()
            problema = _revisar_el_correo(usuario, correo)
            if problema:
                msg, msg_error = problema, True
            else:
                usuario.email = correo
                usuario.save(update_fields=['email'])
                msg = (_('Recovery email saved.') if correo
                       else _('Recovery email removed. Without one, only an '
                              'administrator can reset your password.'))

    return render(request, 'warehouse/cuenta.html', {
        'perfil': perfil,
        'tenant': perfil.tenant if perfil else None,
        'rol_de_plataforma': PlatformUser.objects.filter(user=usuario).first(),
        'volver_a': _de_donde_viene(usuario, request),
        'msg': msg, 'msg_error': msg_error, 'seccion': seccion,
        # El aviso de que la recuperacion no funcionara sin correo se pinta
        # aqui, en la pantalla donde se arregla, y no solo cuando ya es tarde.
        'correo_del_sistema': bool(settings.DEFAULT_FROM_EMAIL),
    })


# ── LA RECUPERACION DESDE FUERA ───────────────────────────────────────────────
#
# Las cuatro pantallas de Django con sus plantillas puestas. Se declaran como
# clases y no con `as_view(...)` en las rutas para que el fichero de rutas siga
# leyendose de un vistazo.

class PedirElEnlace(auth_views.PasswordResetView):
    template_name = 'warehouse/password/reset_form.html'
    email_template_name = 'warehouse/password/reset_email.txt'
    html_email_template_name = 'warehouse/password/reset_email.html'
    subject_template_name = 'warehouse/password/reset_subject.txt'
    success_url = reverse_lazy('password_reset_done')

    @property
    def from_email(self):
        return settings.DEFAULT_FROM_EMAIL

    def form_valid(self, form):
        """
        Igual que el de Django, pero sin morir si el correo no sale.

        El envio va dentro de la peticion, y el servidor de correo es lo que mas
        se cae de todo esto: con `EMAIL_TIMEOUT` a diez segundos, un servidor
        que no contesta le daria a quien perdio su contrasena un "Internal
        Server Error". Es la misma leccion que dejo el chat, y aqui pesa mas
        porque quien esta en esta pantalla ya no puede entrar.
        """
        try:
            return super().form_valid(form)
        except Exception:
            import logging
            logging.getLogger(__name__).exception(
                'No se pudo enviar el correo de recuperacion')
            return self.render_to_response(self.get_context_data(
                form=form,
                error_de_envio=_('The recovery email could not be sent right '
                                 'now. Try again in a few minutes, or ask an '
                                 'administrator to reset your password.')))


class EnlaceEnviado(auth_views.PasswordResetDoneView):
    template_name = 'warehouse/password/reset_done.html'


class EscribirLaNueva(auth_views.PasswordResetConfirmView):
    template_name = 'warehouse/password/reset_confirm.html'
    success_url = reverse_lazy('password_reset_complete')


class YaEstaCambiada(auth_views.PasswordResetCompleteView):
    template_name = 'warehouse/password/reset_complete.html'
