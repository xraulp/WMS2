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
| `DEFAULT_FROM_EMAIL` | una dirección del dominio verificado, p. ej. `no-reply@dysergroup.com` |
| `BILLING_FROM_EMAIL` | opcional: de dónde salen las facturas, p. ej. `billing@dysergroup.com` |
| `NOTIFICATIONS_FROM_EMAIL` | opcional: de dónde salen los avisos a los clientes, p. ej. `reportes@dysergroup.com` |
| `PLATFORM_BILLING_EMAIL` | el contacto de cobranza: sale impreso en el PDF y va como Reply-To de la factura |

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

## Quién firma cada correo

Lo que Resend verifica es el **dominio**, no la dirección, así que tener varias
direcciones del mismo dominio no cuesta nada ni requiere más DNS. El sistema
usa tres, y cada una dice de qué va el correo antes de abrirlo:

| Clase de correo | Sale de | Contesta a |
|---|---|---|
| Recuperación de contraseña, en los tres niveles | `DEFAULT_FROM_EMAIL` | nadie |
| Factura de la plataforma a una empresa | `BILLING_FROM_EMAIL` | `PLATFORM_BILLING_EMAIL` |
| Avisos, informes y hoja de impuestos que una empresa manda a sus clientes | `NOTIFICATIONS_FROM_EMAIL`, con el nombre de la empresa a la vista | `Tenant.reply_to_email` |

Las dos últimas caen en `DEFAULT_FROM_EMAIL` si no se definen, así que una
instalación que no quiera separarlas no tiene que tocar nada.

### Por qué no se manda desde el dominio de cada empresa

Es lo primero que se pide —que el cliente de un almacén reciba el correo de
`reportes@sualmacen.com`— y para los avisos tendría sentido. Pero:

* Un `From:` de un dominio ajeno **no sale** sin verificar antes su SPF y su
  DKIM. Resend responde 403 y el motivo queda en la bitácora. O sea: no es una
  casilla en el alta, es un trámite de DNS con la gente de sistemas de cada
  empresa.
* En el correo de **recuperación de contraseña** sería además peligroso. Lleva
  un enlace a esta plataforma, y un dominio que no controla ese enlace
  avalándolo es exactamente la forma de un fraude; los filtros lo tratan como
  tal. Y una empresa recién dada de alta se quedaría sin recuperación hasta
  terminar el trámite, justo la semana en que más gente se equivoca al entrar.

Lo que sí viaja por empresa son dos cabeceras que no necesitan DNS de nadie: el
**nombre visible** del remitente —el cliente lee «Almacenes del Norte», no el
dominio— y el **Reply-To**, que hace que una respuesta llegue a su proveedor y
no a un buzón de la plataforma que nadie abre. Se captura en la ficha de la
empresa, en el panel de plataforma, y la tabla marca en ámbar a las que no lo
tienen.

Si algún día hace falta el dominio propio de verdad, el sitio es un campo más
en la ficha de la empresa que solo afecte a los **avisos** —nunca a la
recuperación ni a las facturas— y el plan de Resend correspondiente: el
gratuito admite tres dominios, el Pro diez, y hay un complemento de cien.

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
