# Las contraseñas del sistema

En la base de datos no queda ninguna contraseña legible. Son tres, y conviene no
confundirlas.

| Cuál | Dónde vive | Para qué |
|---|---|---|
| La de acceso | `auth_user.password`, cifrada por Django | entrar al sistema |
| La de borrado | `UserProfile.delete_password`, cifrada desde la `0014` | autorizar un borrado |
| La de la sesión abierta | en ningún sitio | ya no vale para borrar |

## La de acceso ya no se puede consultar

`UserProfile` guardaba `plain_password`: una copia legible de la contraseña de
cada usuario, escrita al crearlo y cada vez que se le cambiaba. Existía por una
razón práctica —que la columna **Password** de la pestaña Users pudiera volver a
mostrarla cuando alguien la olvidara— y el precio era que abrir esa pestaña, o
esa columna de la base, era ver de golpe las contraseñas de toda la empresa.

La migración `0015_quitar_plain_password` retira el campo. Lo que cambia en la
pantalla:

- La columna se llama ahora **Set Password** y llega **vacía**: sirve para
  asignar una nueva, no para leer la que hay.
- El alta de usuario y el alta de cliente con su login avisan, en el mensaje de
  confirmación, de que la contraseña no queda guardada en ninguna parte.
- Quien olvide la suya **recibe una nueva** desde esa misma columna. No hay
  manera de recuperar la anterior, y es lo correcto.

> **Al desplegar:** la migración destruye la columna con su contenido. Si tienes
> contraseñas ahí que no conoces por otra vía, expórtalas **antes** del deploy:
> ```
> python manage.py shell -c "from warehouse.models import UserProfile; [print(p.user.username, p.plain_password) for p in UserProfile.objects.all()]"
> ```
> Después del deploy ese campo ya no existe. En condiciones normales no hace
> falta: el usuario conoce su contraseña, y si no, se le asigna otra.

## La de borrado

Se cifró en la `0014` por la misma razón, con el agravante de que autoriza una
acción irreversible. La pantalla dice si el usuario **la tiene**, nunca cuál es.
El detalle está en [`borrado-y-papelera.md`](borrado-y-papelera.md).

## Lo que sigue abierto

- **La pestaña Users no distingue «contraseña sin estrenar» de «contraseña que el
  usuario ya cambió»**, porque no hay forma de saberlo sin guardar algo. Si
  llegara a importar, la salida limpia es un campo de «debe cambiarla al entrar».
- **No hay recuperación por correo.** Restablecer la contraseña sigue siendo cosa
  del administrador de la empresa desde la pestaña Users.
