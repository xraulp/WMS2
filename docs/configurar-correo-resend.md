# Configurar el correo en producción (Resend)

## Por qué no basta el SMTP propio

Desde septiembre de 2025, **Render bloquea el tráfico saliente a los puertos SMTP
(25, 465 y 587) en los servicios web del plan gratuito.** La conexión no se
rechaza: se queda esperando, gunicorn mata al worker a los 30 segundos y el
operador ve un "Internal Server Error".

Se comprobó cada pieza por separado:

| Prueba | Resultado |
|---|---|
| `vmail.globalpc.net:465` SSL desde la red local | conecta, autentica y el correo llega |
| `vmail.globalpc.net:587` STARTTLS desde la red local | timeout — ese servidor no atiende el 587 |
| `vmail.globalpc.net:465` desde Render | se queda colgado sin respuesta |

O sea: las credenciales están bien y el servidor solo atiende el 465; lo que no
funciona es salir por SMTP desde Render.

**Resend recibe el correo por HTTPS**, así que el bloqueo de puertos no le
afecta. El plan gratuito da 3 000 correos al mes y 100 al día.

## Lo que ya está programado

- `warehouse/email_backends.py` — backend de Django que manda por la API de
  Resend. Traduce el `EmailMessage` completo: cuerpo HTML, CC, BCC, reply-to y
  los adjuntos en base64 (el PDF del reporte y los archivos del expediente).
- `warehouse_system/settings.py` — elige el backend según el entorno.
- `warehouse/tests_email_backend.py` — 22 pruebas, sin tocar la red.

El sistema de notificaciones no se tocó: todo el envío pasaba ya por
`EmailMessage.send()`, así que cambiar el backend fue suficiente.

## Pasos manuales

### 1. Crear la cuenta y verificar el dominio

1. Crear cuenta en [resend.com](https://resend.com).
2. **Domains → Add Domain** → `dysergroup.com`.
3. Resend muestra unos registros DNS (un TXT de verificación, un CNAME o TXT de
   DKIM y opcionalmente el de DMARC). Hay que darlos de alta donde estén los DNS
   del dominio. La verificación suele tardar minutos, a veces horas.

Sin dominio verificado la API responde **403** y el motivo queda anotado en la
bitácora de notificaciones (`Resend HTTP 403: ... domain is not verified`).

### 2. Sacar la API key

**API Keys → Create API Key**, permiso de envío. Se copia una sola vez.

### 3. Variables en Render

En el servicio web, *Environment*:

| Variable | Valor |
|---|---|
| `EMAIL_PROVIDER` | `resend` |
| `RESEND_API_KEY` | `re_...` |
| `DEFAULT_FROM_EMAIL` | una dirección del dominio verificado, p. ej. `avisos@dysergroup.com` |

Las variables `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER` y
`EMAIL_HOST_PASSWORD` pueden quedarse: con `EMAIL_PROVIDER=resend` no se usan.
Conviene dejarlas para poder volver al SMTP cambiando una sola variable.

> Si `EMAIL_PROVIDER` no se define, el sistema usa Resend cuando hay
> `RESEND_API_KEY` y SMTP cuando no. En la máquina local, donde el SMTP sí sale,
> basta con no definir `RESEND_API_KEY` para seguir probando contra
> `vmail.globalpc.net`.

### 4. Comprobar que quedó

En el log de arranque del deploy sale la línea:

```
[INFO] Correo: proveedor=resend backend=warehouse.email_backends.ResendBackend
```

Después, registrar una operación de un cliente con correo y revisar
`/admin/warehouse/notificationlog/`:

- **Enviada** — llegó a Resend. La entrega se puede seguir en el panel de Resend
  (*Emails*), que muestra entregados, rebotados y marcados como spam.
- **Fallida** — el motivo va en `detail` con el código HTTP de la API.
- **Omitida** — no se intentó; el motivo va en `detail` (`preference_off`,
  `no_recipient`, `customer_not_in_catalog`, `already_notified`).

## Qué falta verificar cuando el correo salga

1. Que el renglón de la bitácora diga **Enviada**.
2. Que el usuario del cliente nivel 2 reciba el correo en su `User.email`,
   además del `contact_email` del catálogo. Antes solo se miraba el catálogo.
3. Que los documentos del expediente lleguen **adjuntos**. Dejaron de adjuntarse
   con la mudanza a R2 y se arregló en `5de4428`.
4. Activar `notify_on_release` a un cliente de prueba y registrar una salida que
   despache una de sus entradas.

## Detalles del backend que conviene saber

- **Tope de adjuntos:** 5 MB por archivo (`EMAIL_MAX_ATTACHMENT_MB`). Resend
  rechaza los envíos que pasan de 40 MB en total y el base64 infla el tamaño un
  33%.
- **Límite de peticiones:** el plan gratuito permite 2 por segundo. Un 429 se
  reintenta dos veces con espera creciente antes de darse por fallido.
- **Timeout:** `EMAIL_TIMEOUT`, 10 segundos por omisión. Sin él, un servicio que
  no contesta cuelga el request hasta que gunicorn mata al worker — que es
  exactamente lo que pasó con el SMTP.
- **Los avisos son sincrónicos.** Si una salida despacha diez entradas de diez
  clientes distintos, son diez llamadas a la API antes de responderle al
  operador. Hoy no muerde porque `notify_on_release` nace apagado, pero si se
  activa en varios clientes conviene mover el envío a una cola.
