# Borrar en el WMS: quién, con qué y qué queda

Hasta ahora borrar era una frontera de rol: podían el superadmin, el admin y el
manager, y el staff no. En la práctica eso paraba el trabajo del día —un archivo
mal subido obligaba a buscar a alguien más— y la frontera además no estaba donde
parecía: la regla «cada quien borra solo lo que capturó» dejaba **incluso al
administrador** sin poder borrar lo capturado en otro turno, así que hacía falta
un superusuario de Django.

Ahora **todo el que opera puede borrar**, y lo que sustituye al permiso denegado
es el rastro.

## Las cuatro condiciones

Ninguna es decorativa; quitar una deja el permiso sin contrapeso.

1. **Contraseña de borrado.** Es la que se configura por usuario en la pestaña
   Users. Se exige siempre: quien no la tenga puesta no borra. Ya no vale la
   contraseña de sesión —la que el navegador tiene escrita— ni la contraseña de
   borrado de otro usuario.
2. **Motivo escrito.** Obligatorio, de una línea. Sin motivo la bitácora dice
   quién borró pero no por qué, que es justo lo que se pregunta cuando falta un
   expediente.
3. **Registro.** Cada borrado escribe un renglón en `DeletionLog` con qué, quién,
   cuándo y por qué. Se ve en la pestaña **Deletions**.
4. **Papelera, en el expediente digital.** Los archivos no se destruyen: salen de
   la vista y el administrador puede devolverlos.

## Qué puede cada rol

| | Operaciones | Archivos del expediente | Papelera y bitácora | Destruir un archivo | Clientes |
|---|---|---|---|---|---|
| superadmin | ✔ | ✔ | ✔ | ✔ | ✔ |
| admin | ✔ | ✔ | ✔ | ✔ | ✔ |
| manager | ✔ | ✔ | ✔ | ✕ | ✕ |
| staff | ✔ | ✔ | ✕ | ✕ | ✕ |
| cliente (nivel 3) | ✕ | ✕ | ✕ | ✕ | ✕ |

El staff **deja rastro, no lo audita**: la pantalla de bitácora es vigilancia
sobre el trabajo ajeno y se queda en los roles de casa. Y **destruir** un archivo
ya archivado es lo único irreversible, así que se queda en el administrador: una
papelera que cualquiera puede vaciar no es una papelera.

Los **clientes del catálogo** siguen siendo cosa del administrador de la empresa;
esa frontera no se movió, y es la única que le queda al staff.

## Las operaciones no tienen papelera

Se borran de verdad. Lo que queda es el renglón de `DeletionLog`, que guarda el
custom ID, el tipo, la fecha, el cliente, la descripción y el motivo: suficiente
para saber qué había y volver a capturarlo, no para recuperarlo con sus archivos.
Los documentos de una operación borrada se van con ella, por la cascada.

## Cómo funciona la papelera

Un archivo archivado conserva su registro con `deleted_at`, `deleted_by` y
`delete_reason`. Deja de aparecer en el panel Digital, en el ZIP de descarga, en
los adjuntos de los correos y en el PDF del reporte, porque el manager por
defecto de `OperationDocument` los esconde. Para verlos hay que pedirlos
expresamente con `OperationDocument.todos`.

**El número no se libera.** El consecutivo `DDMMAA-N` se siembra contando también
la papelera: un nombre que ya salió impreso o adjunto en un correo no puede
reasignarse, y menos cuando el archivo puede volver.

### El archivo cambia de sitio

Al archivarlo se mueve bajo el prefijo `papelera/` del bucket, y al restaurarlo
vuelve a su ruta. Mientras estuvo en su sitio de siempre, quien ya tuviera el
enlace podía seguir abriéndolo aunque el archivo hubiera desaparecido de la
pantalla: R2 sirve por URL, sin preguntar quién mira. Al cambiarlo de sitio, esa
URL deja de servir.

> **Lo que esto no hace.** Invalida el enlace que alguien tuviera, y nada más. El
> archivo sigue existiendo y su ruta nueva es igual de pública que la anterior
> —comprobado con un `GET` desde fuera—, porque el bucket se sirve por un dominio
> abierto. Archivar retira el archivo de la vista y del expediente; **no retira
> el acceso a quien sepa dónde buscar**. Lo único que lo destruye es la purga.
> Ver `docs/configurar-r2.md`.

Dos detalles que conviene conocer:

- **Si el almacén no responde, el archivado ocurre igual** y el archivo se queda
  donde estaba. Quitar de la vista una foto mal subida no puede depender de que
  R2 conteste; lo que no puede fallar es el registro y la papelera.
- **Al restaurar no se pisa nada.** Si alguien subió otro archivo con ese nombre
  mientras este estaba en la papelera, el que vuelve se coloca al lado con un
  sufijo. Se pierde la ruta bonita, no un archivo.

### La papelera se vacía sola si se le pide

No hay purga automática: hay un comando, pensado para un cron.

```
python manage.py purgar_papelera                 # informe, no toca nada
python manage.py purgar_papelera --confirmar     # destruye lo que pase de 90 días
python manage.py purgar_papelera --dias 30 --confirmar
python manage.py purgar_papelera --empresa norte --confirmar
```

Sin `--confirmar` solo enseña qué se llevaría, con nombre, expediente, empresa y
fecha de entrada en la papelera. Lo que destruye es irreversible y no se vuelve a
registrar: el renglón de la bitácora se escribió cuando el archivo entró, con
quién lo quitó y por qué, y ese renglón se queda.

### En el móvil también

La papelera está en la barra de abajo, con el mismo permiso que en el tablero:
la ven los roles de casa, no el staff. Antes desde el móvil se podía archivar
pero no devolver, que es justo donde se sube la foto equivocada — en el andén,
sin el escritorio a mano.

## La contraseña de borrado ya no se ve

Se guardaba en claro en la base y se pintaba en la pantalla de usuarios, en un
campo de texto y en una columna de la tabla. Mientras borrar estuvo reservado a
tres roles de confianza era una fealdad; desde que es lo que autoriza la acción,
es el control mismo.

Ahora se guarda cifrada. La pantalla dice si el usuario **tiene** contraseña de
borrado, nunca cuál es, y el campo de edición sirve para asignarla o cambiarla.
La migración `0014_cifrar_delete_password` convierte las que ya estaban
guardadas; el código acepta además un valor sin cifrar y lo cifra en el acto, por
si alguna base se queda sin migrar.

La contraseña de acceso siguió el mismo camino poco después: ver
[`contrasenas.md`](contrasenas.md).

> **Al desplegar:** revisa que los usuarios que van a borrar tengan contraseña de
> borrado configurada. Quien no la tenga verá «No tienes contraseña de borrado
> configurada» y no podrá borrar nada. El alta de usuario ya la guarda —antes
> pintaba el campo y lo tiraba—, pero los usuarios que ya existen hay que
> revisarlos uno por uno en la pestaña Users, donde la columna dice
> «configurada» o «sin configurar».

## La puerta que se cerró

`/operations/<pk>/delete/` borraba con un POST pelado, sin contraseña ni motivo,
mientras la pantalla usaba `delete-confirm`. Nadie la llamaba desde la interfaz,
pero existía. Con el staff pudiendo borrar, esa ruta volvía decorativo todo lo
demás, así que se retiró.
