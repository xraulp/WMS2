# Configurar Cloudflare R2

Los archivos del expediente digital (fotos, PDF, video) no viven en el disco de
Render — ese disco se borra en cada deploy — sino en un bucket de Cloudflare R2.
R2 habla el protocolo de S3, así que del lado de Django se usa `django-storages`
con el backend `s3boto3` apuntando al endpoint de Cloudflare.

## Variables de entorno

| Variable | Para qué |
|---|---|
| `R2_ACCESS_KEY_ID` | Credencial del token de API de R2 |
| `R2_SECRET_ACCESS_KEY` | La otra mitad de la credencial |
| `R2_BUCKET_NAME` | Nombre del bucket |
| `R2_ENDPOINT_URL` | `https://<account_id>.r2.cloudflarestorage.com` |
| `AWS_S3_CUSTOM_DOMAIN` | Dominio público desde el que se sirven los archivos, **sin** `https://` ni barra final |

`AWS_S3_CUSTOM_DOMAIN` es la que decide la URL que devuelve `FieldFile.url`, o
sea el enlace que el operador ve y abre, y `django-storages` toma uno de dos
caminos según esté definida o no (`storages/backends/s3.py`, método `url()`):

- **Sin ella**, firma una URL contra el endpoint privado de R2. El bucket puede
  quedar cerrado, pero el enlace **caduca**: a falta de `AWS_QUERYSTRING_EXPIRE`
  se toma el valor por omisión de la librería, **una hora**. Un enlace guardado
  o pegado en un correo deja de servir.
- **Con ella**, construye `https://<dominio>/<ruta>` sin firma ninguna. El
  enlace no caduca nunca — y por lo mismo no lleva credencial, así que **el
  bucket tiene que estar publicado** para que funcione.

## Los archivos se sirven por una vista, no por el bucket

La pantalla ya no enlaza a R2. Cada archivo del expediente se pide a
`/documents/<id>/file/` — la vista `document_file` en `warehouse/views.py` —, que
hace tres cosas antes de entregar nada:

1. **Comprueba quién pide.** Hay que estar dentro, el documento tiene que ser del
   tenant del request y, si quien mira es un cliente, de sus propias operaciones.
   Es el mismo criterio que `operation_detail`, a propósito: lo que se puede
   abrir coincide con lo que se puede ver.
2. **Firma una URL nueva y de vida corta** — cinco minutos, en
   `warehouse/almacen.py` — y redirige a ella. El enlace que guarda el navegador
   es el del sistema y no caduca nunca; el que llega al bucket caduca en minutos.
   La respuesta va con `Cache-Control: private, no-store` para que el navegador
   no se quede con un redirect que va a dejar de servir.
3. **Deja constancia** de quién abrió qué, en el log de la aplicación.

Un archivo en la papelera solo lo abre quien puede ver la papelera. Para los
demás deja de existir, que es lo que significa archivarlo.

Firmar hace falta *aunque* `AWS_S3_CUSTOM_DOMAIN` siga puesta: mientras lo esté,
`FieldFile.url` devuelve el enlace público sin firma. Por eso `url_firmada`
trabaja sobre una copia del almacén con `custom_domain` en `None`. Cuando la
variable se retire, esa copia dejará de tener trabajo y el código seguirá siendo
correcto — no hay nada que tocar el día del cambio.

Cuando el almacén no sabe firmar —el sistema de archivos local, en desarrollo y
en las pruebas— la vista sirve el archivo ella misma. Ese camino no es un parche:
es lo que mantiene la pantalla igual de funcional sin R2 detrás.

### El bucket sigue siendo público, y qué falta para cerrarlo

Servir por la vista quita el enlace público del HTML, que era lo que ponía la
ruta al alcance de cualquier usuario, cliente incluido. Lo que **no** hace por sí
solo es cerrar el bucket: quien ya tenga apuntada una ruta de las de antes sigue
descargándola sin pasar por el sistema. Dos cosas acotan lo que queda:

- **El bucket no se puede enumerar.** Pedir la raíz del dominio devuelve 404, de
  modo que hay que acertar la ruta, no listarla.
- **La ruta dejó de ser adivinable.** Era `operations/<fecha>/<nombre original>`,
  y con nombres como `report.pdf` eso se acierta probando. Ahora lleva un
  identificador aleatorio: ver `ruta_documento` en `warehouse/models.py`.

El orden es obligatorio, y el primer paso ya está hecho:

1. ~~Servir los archivos por una vista de Django.~~ **Hecho.**
2. **Quitar `AWS_S3_CUSTOM_DOMAIN` del entorno de Render.** A partir de ahí
   `FieldFile.url` firma por su cuenta y nada del sistema depende del dominio
   público. Antes de hacerlo, comprobar que no queda ninguna plantilla usando
   `doc.file.url` (`grep -rn "file.url" templates/`): hoy no queda ninguna.
3. **Despublicar el bucket** en el panel de Cloudflare.

Al revés, todos los enlaces se rompen de golpe — es exactamente lo que le pasa
hoy al dominio viejo `pub-7aa64bbc...`, que devuelve 401.

## Dónde se guarda cada archivo

La ruta la arma `ruta_documento` y tiene esta forma:

```
operations/<empresa>/<año>/<mes>/<día>/<12 hex>-<nombre-saneado>.<ext>
```

Cada tramo está por una razón que costó un incidente:

- **La empresa**, porque sin ella el `report.pdf` de una podía pisar el de otra.
- **El identificador aleatorio**, porque dos archivos con el mismo nombre subidos
  el mismo día daban la misma ruta y **el segundo destruía al primero** — el
  backend de S3 sobrescribe mientras no se le diga otra cosa. Pasó dos veces con
  datos reales, sin ningún error visible: la base conservaba las dos filas
  apuntando al mismo objeto, así que la pantalla mostraba el archivo equivocado.
  De ahí también `AWS_S3_FILE_OVERWRITE = False` en `settings.py`, que es la red
  de abajo.
- **El nombre saneado y recortado**, para reconocer el archivo al mirar el bucket
  sin arrastrar espacios ni tildes a la URL. El nombre completo, tal como lo puso
  quien lo subió, vive en `original_name` y es lo que se le enseña al usuario.

Las rutas de antes del cambio siguen como estaban: nada las reescribe. Las cubre
`warehouse/tests_rutas_de_archivos.py`.

Para comprobar que todo responde:

```
python manage.py check_r2
```

Imprime el endpoint, el bucket, el dominio público y el resultado de listar un
objeto. Ese diagnóstico vivía suelto al final de `settings.py` y se ejecutaba en
cada arranque del servidor y en cada corrida de las pruebas; ahora solo corre
cuando se pide.

## Política CORS del bucket

Se configura **en el panel de Cloudflare** (R2 → el bucket → *Settings* → *CORS
Policy*), no en el código. Sin ella el navegador bloquea la carga de imágenes y
la descarga de archivos desde el dominio de la aplicación.

```json
[
  {
    "AllowedOrigins": ["*"],
    "AllowedMethods": ["GET", "HEAD"],
    "AllowedHeaders": ["*"],
    "ExposeHeaders": ["ETag"],
    "MaxAgeSeconds": 3600
  }
]
```

Este JSON estaba pegado como un literal suelto dentro de `settings.py`: Python lo
evaluaba como una expresión y tiraba el resultado en cada arranque. No hacía
daño, pero tampoco configuraba nada — de ahí que viva aquí.

`AllowedOrigins: ["*"]` es lo que hay hoy. Cuando la aplicación tenga dominio
propio conviene acotarlo a ese dominio.

## Cuidado con `FieldFile.path`

Con los archivos en R2 **no existe una ruta en disco**: `documento.file.path`
lanza `NotImplementedError`. Ese error ya rompió tres cosas en silencio (los
adjuntos del correo, el borrado de archivos y el ZIP de descarga completa).

Al tocar código que maneje archivos, usar siempre la interfaz del storage:

| En vez de | Usar |
|---|---|
| `open(f.path)` | `f.open()` / `f.read()` |
| `os.remove(f.path)` | `f.delete(save=False)` |
| `zf.write(f.path, nombre)` | `zf.writestr(nombre, f.read())` |


## El bucket es uno solo: `MEDIA_LOCAL=1` para probar

`STORAGES['default']` es S3/R2 **siempre**, y no depende de `DEBUG`. Una prueba
lanzada en el portátil, contra la base de datos local, sube igualmente al mismo
bucket que producción.

Pasó: probando los adjuntos del hilo en un navegador quedaron nueve archivos de
prueba en `django-wms`, y hubo que borrarlos a mano. La ruta lleva el subdominio
del tenant —`operations/pruebalocal/…`—, así que no se mezclan con los de
ninguna empresa de verdad ni aparecen en ninguna pantalla, pero siguen siendo
basura en un bucket que se paga.

Para que lo que se suba probando se quede en la máquina:

```bash
DATABASE_URL="" MEDIA_LOCAL=1 python manage.py runserver
```

Con esa variable los archivos van a `media/` en el propio proyecto. Las pruebas
de Django ya lo hacen por su cuenta —cada módulo que sube archivos redefine
`STORAGES` a un directorio temporal—; esto es para cuando se prueba **a mano o
con un navegador**, que es donde no hay nada que lo impida.

> Al terminar, comprobar que no quedó nada:
>
> ```python
> from warehouse.models import OperationDocument
> OperationDocument.todos.filter(operation__custom_id='LO-QUE-SEA')
> ```
