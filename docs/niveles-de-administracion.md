# Los tres niveles de administración

El sistema tiene tres niveles, y cada uno tiene sus propios administradores y sus
propios usuarios. Conviene tener presente cuál es cuál antes de tocar permisos,
porque durante mucho tiempo los dos primeros fueron la misma persona por
construcción.

| Nivel | Quién es | Dónde vive en el código |
|---|---|---|
| **1. Plataforma** | Quien administra el SaaS: da de alta empresas, atiende soporte | `PlatformUser` |
| **2. Empresa (tenant)** | Quien administra y usa una empresa, por ejemplo DYSER | `UserProfile.role` con `tenant` |
| **3. Cliente de la empresa** | El cliente final, con permisos limitados | `UserProfile.role='customer'` |

## Nivel 1 — La plataforma

Sus usuarios **no pertenecen a ninguna empresa**: no tienen `UserProfile` con
tenant. Esa ausencia no es un descuido, es la garantía — todas las pantallas del
tenant pasan por `get_tenant_or_404`, así que un usuario de plataforma recibe un
404 si intenta abrir el tablero, el catálogo o los documentos de cualquiera.

Tiene su propia pantalla, en `/platform/`, y dos niveles:

**`admin` — lo crítico.** Dar de alta una empresa y nombrar a su administrador
(es fabricar la llave de su puerta), activarla o desactivarla (es cortarle el
servicio), y repartir este mismo acceso.

**`staff` — el día a día.** Consultar el estado de las empresas y la bitácora de
envíos, que es lo que hace falta para atender un «a este cliente no le llegan los
correos». Mira, no toca.

### Crear el primero

La pantalla que reparte este acceso solo la ve quien ya lo tiene, así que hay un
problema del huevo y la gallina. Se resuelve por línea de comandos:

```
python manage.py create_platform_user ana --role admin --password ...
```

Sobre un usuario que ya existe, el `--password` es opcional y no se le toca la
contraseña.

## El `is_superuser` de Django, y cómo retirarlo

**Hoy `is_superuser` sigue contando como administrador de plataforma.** Se
conserva a propósito: quitárselo al único superusuario que hay, en el mismo
cambio que introduce el nivel nuevo, dejaría el sistema sin nadie dentro si algo
saliera mal.

Ese flag es más de lo que parece. Da a la vez:

1. El panel de plataforma.
2. El admin de Django, con **todos los datos de todas las empresas**.
3. El rol más alto dentro de su propia empresa — porque
   `UserProfile.is_superadmin()` devuelve `True` para cualquier superusuario.

El objetivo es que ese flag deje de ser necesario. **El orden importa**, y hay
que hacerlo en este y no en otro:

1. Crear el administrador de plataforma con el comando de arriba.
2. **Entrar con él** y comprobar que ve `/platform/`, que puede crear una empresa
   de prueba y que la bitácora carga. Hasta aquí no se ha perdido nada: si algo
   falla, el superusuario sigue estando.
3. Crear, si hace falta, un usuario `admin` de la empresa para el trabajo diario
   —el que da de alta operaciones y clientes— y probarlo también.
4. Solo entonces, retirar `is_superuser` al usuario original, desde el admin de
   Django o por shell.

Saltarse el paso 2 es la forma de quedarse fuera.

## Lo que el nivel de plataforma **no** puede hacer

No abre las operaciones, los documentos ni el catálogo de ninguna empresa. Si
algún día soporte necesitara eso para diagnosticar un problema, la decisión hay
que tomarla explícitamente y dejar registro de quién miró qué — no conviene que
aparezca como efecto secundario de otra cosa.

La bitácora de envíos sí muestra el correo y el teléfono del destinatario, porque
sin eso no se puede diagnosticar una entrega fallida. Es el mínimo necesario, y
conviene saber que está ahí.

## Pendiente

`UserProfile.is_superadmin()` sigue devolviendo `True` para cualquier
superusuario de Django. Mientras exista un superusuario que además pertenezca a
una empresa, los niveles 1 y 2 se tocan en esa persona. Se arregla solo cuando se
complete la retirada descrita arriba; el código no puede forzarlo sin arriesgarse
a dejar a alguien fuera.
