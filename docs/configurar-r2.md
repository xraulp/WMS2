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

## El bucket es público, y lo que eso implica

En producción la variable está puesta, así que estamos en el segundo caso:
cualquiera que conozca la ruta de un archivo lo descarga sin pasar por el
sistema. Comprobado con un `GET` desde fuera, sin sesión y sin credenciales.

Dos cosas acotan el riesgo, y conviene tener claras las dos:

- **El bucket no se puede enumerar.** Pedir la raíz del dominio devuelve 404, de
  modo que hay que acertar la ruta, no listarla.
- **La ruta dejó de ser adivinable.** Era `operations/<fecha>/<nombre original>`,
  y con nombres como `report.pdf` eso se acierta probando. Ahora lleva un
  identificador aleatorio: ver `ruta_documento` en `warehouse/models.py`.

El dominio no es un secreto: sale en el HTML de cualquier pantalla que muestre un
archivo, así que lo tiene cualquier usuario, incluido un cliente. Lo único que
separa los documentos de una empresa de los de otra es que la ruta no se acierta.

**La salida limpia, cuando haga falta**, es servir los archivos por una vista de
Django que compruebe permisos y redirija a una URL firmada recién hecha: el
enlace del sistema no caduca, el bucket queda cerrado y queda registro de quién
abrió qué. El orden para eso es obligatorio — **primero la vista, después quitar
la variable, después despublicar el bucket**. Al revés, todos los enlaces se
rompen de golpe.

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
