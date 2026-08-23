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

Ese flag daba tres cosas a la vez, que no tienen por qué ir juntas:

1. El admin de Django, con **todos los datos de todas las empresas**.
2. El panel de plataforma.
3. **El rol más alto dentro de su propia empresa**, porque cada predicado de
   `UserProfile` llevaba pegado un `or self.user.is_superuser`.

**La tercera ya no existe.** Los permisos de empresa salen del rol escrito en el
perfil y de nada más: un superusuario cuyo perfil diga `staff` es staff, y punto.
La migración `0016` escribió `superadmin` en el perfil de los superusuarios que
lo tuvieran menor, para que nadie se degradara de golpe con el deploy — era lo
que ya ejercían.

**La segunda se retira sola.** `platform_role()` sigue aceptando el flag, pero
solo **mientras no exista ningún `PlatformUser` con rol `admin`**. En cuanto se
crea el primer administrador de plataforma, la llave maestra deja de abrir esa
puerta. Esa condición es la que resuelve el huevo y la gallina —hace falta una
llave para crear al primero— sin que la llave se quede puesta para siempre
esperando a que alguien se acuerde de quitarla. Si el sucesor se revoca, vuelve.

**La primera se retira a mano**, y es el último paso.

### El orden

Sigue importando, y hay un comando que acompaña cada paso:

```
python manage.py retirar_superusuario
```

Sin argumentos hace un informe y no toca nada: quién es superusuario, a qué
empresa pertenece, con qué rol se quedaría y si hay administradores de
plataforma. Conviene correrlo antes de empezar.

1. **Crear el administrador de plataforma:**
   ```
   python manage.py create_platform_user ana --role admin --password ...
   ```
2. **Entrar con él** y comprobar que ve `/platform/`, que crea una empresa de
   prueba y que la bitácora carga. Desde este momento el superusuario **ya no
   entra al panel de plataforma** —la llave cedió ante el sucesor—, pero sigue
   teniendo el admin de Django, así que si algo falla se revoca el acceso nuevo
   desde ahí y la llave vuelve.
3. **Crear, si hace falta, un usuario `admin` de la empresa** para el trabajo
   diario, y probarlo también.
4. **Retirar el flag:**
   ```
   python manage.py retirar_superusuario admin
   ```
   Se niega si no quedara ningún administrador de plataforma. Retira también el
   `is_staff`, porque sin superusuario el admin de Django no muestra nada útil y
   sí sigue siendo una puerta; para conservarlo, `--conservar-admin-django`.

Saltarse el paso 2 es la forma de quedarse fuera.

### Quien no tiene perfil, no tiene rol

`get_profile()` fabricaba el perfil que faltara: `superadmin` para el
superusuario y `manager` para cualquier otro. Lo segundo era lo grave — bastaba
existir en `auth_user` y abrir el subdominio de una empresa para quedar de
manager en ella, con una fila escrita en la base como si alguien lo hubiera
decidido. Ahora devuelve un perfil vacío y sin guardar: todos los predicados dan
`False` y cada pantalla lo rechaza por su cuenta. Dar de alta a alguien es un
acto explícito de la pestaña Users.

Consecuencia práctica: **un superusuario sin perfil de empresa no entra a ningún
tablero.** No es una pérdida —no tenía ningún rol que nadie le hubiera dado—,
pero conviene saberlo antes de retirar el flag. El informe del comando lo avisa
usuario por usuario.

## Lo que el nivel de plataforma **no** puede hacer

No abre las operaciones, los documentos ni el catálogo de ninguna empresa. Si
algún día soporte necesitara eso para diagnosticar un problema, la decisión hay
que tomarla explícitamente y dejar registro de quién miró qué — no conviene que
aparezca como efecto secundario de otra cosa.

La bitácora de envíos sí muestra el correo y el teléfono del destinatario, porque
sin eso no se puede diagnosticar una entrega fallida. Es el mínimo necesario, y
conviene saber que está ahí.

## Pendiente

Lo que queda es operativo, no de código: **retirar el flag al superusuario
original**, siguiendo el orden de arriba. Mientras exista un superusuario, sigue
abierto el admin de Django con los datos de todas las empresas — que es lo único
que ese flag conserva ya.

Del lado del código queda un detalle menor: la pestaña **Platform** del tablero
de una empresa muestra la lista de todas las empresas dentro de esa pantalla,
duplicando lo que ya vive en `/platform/`. Hoy cuelga del mismo permiso que el
panel, así que no es un hueco; es una mezcla de niveles que valdría la pena
deshacer.

## Cómo se da de alta a cada quien

Los tres niveles no se crean por el mismo camino, y eso es deliberado.

| Quién | Dónde | Rol |
|---|---|---|
| Usuario de plataforma | `create_platform_user` por línea de comandos, o `/platform/users/` | `admin` o `staff` de plataforma |
| Personal de la empresa | Pestaña Users → *Create New User* | `admin`, `manager` o `staff` |
| Primera persona de un cliente | Pestaña Users → *New Customer + Login* | `customer`, siempre |
| Otra persona de ese cliente | Pestaña Users → *Otro usuario para un cliente que ya existe* | `customer`, siempre |

**El rol `customer` ya no aparece en el desplegable del alta de personal.**
Ofrecerlo junto a los demás fue lo que permitió el error que apareció en
producción: se elegía un cliente en el formulario y se dejaba el `staff` que
viene por omisión, con lo que la cuenta quedaba con cliente **y** con rol de la
casa. Como `customer_ops_filter` acota por rol y no por tener cliente, esa
persona veía todas las operaciones de la empresa mientras quien la creó creía
haber dado un acceso limitado.

En la edición sí se ofrece, pero **solo a quien ya es `customer`**: sin la
opción en su propio desplegable, guardar cualquier otro cambio suyo lo
convertiría en personal de la empresa sin querer.

Y la comprobación de fondo no está en el HTML sino en la vista: un cliente
asignado obliga al rol `customer`, y el rol `customer` obliga a un cliente.

## El nombre de usuario es único en toda la plataforma

`User.username` es único en la base entera, no por empresa. Mientras exista un
usuario llamado `admin`, **ninguna otra empresa podrá tener el suyo**. Y el
login tampoco distingue empresa: se entra con usuario y contraseña, y el sistema
deduce la empresa del perfil.

Por eso el administrador de la primera empresa se renombró de `admin` a
`dyser`: la convención es **el nombre de la empresa**, adoptada mientras había
una sola y no cuando ya hubiera cinco. Renombrar no toca la contraseña ni el
histórico — las operaciones siguen siendo suyas, porque cuelgan del id y no del
nombre.

Que cada empresa pueda tener literalmente su propio `admin` exigiría autenticar
contra el subdominio, que es un cambio mayor: Django impone el usuario único, así
que habría que guardar un nombre de acceso por empresa y componer el username
real, o autenticar por correo.

### Cuidado con `create_superuser.py`

Ese script corre **en cada deploy** desde `build.sh` y crea el usuario que diga
`SUPERUSER_USERNAME`, que por omisión es `admin`. Renombrar al `admin` de una
empresa dejaba ese nombre libre, así que el siguiente deploy habría fabricado un
superusuario nuevo —con el admin de Django y los datos de todas las empresas
dentro— sin que nadie lo pidiera.

Ahora el script **no crea nada mientras exista un administrador de plataforma**.
Es la misma regla que ya aplica `platform_role`: la llave maestra existe para
resolver el huevo y la gallina, y deja de valer en cuanto el sucesor existe. Sin
esa condición, el script era una puerta que se volvía a abrir sola.
