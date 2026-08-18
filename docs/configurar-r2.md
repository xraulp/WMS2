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
sea el enlace que el operador ve y abre. Si no está definida, `django-storages`
firma URLs contra el endpoint privado de R2: funcionan, pero **caducan**, así que
un enlace guardado o mandado por correo deja de servir al rato. En producción
tiene que estar puesta.

> **Pendiente de verificar en Render.** `settings.py` traía el dominio de
> desarrollo `pub-7aa64bbc50bd414e93e88ea59d6561a7.r2.dev` escrito a mano, pero
> un segundo bloque más abajo lo pisaba con `os.environ.get('AWS_S3_CUSTOM_DOMAIN')`,
> así que ese valor fijo llevaba tiempo sin usarse. En la máquina de desarrollo
> la variable no está definida y `python manage.py check_r2` lo dice con todas
> sus letras. **Hay que confirmar que en Render sí está**; si no lo está, los
> enlaces a los archivos del expediente caducan al rato de generarse.

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
