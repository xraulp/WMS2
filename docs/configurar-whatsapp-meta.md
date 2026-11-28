# Configurar WhatsApp (Cloud API de Meta)

El WhatsApp del WMS va directo con Meta, sin Twilio. El código está en
`warehouse/whatsapp.py`.

## Variables de entorno (Render)

| Variable | De dónde sale |
|---|---|
| `WHATSAPP_TOKEN` | Para probar: el token temporal de **WhatsApp → Configuración de la API** (dura 24 h). Para producción: el de un **usuario del sistema** del portafolio, con `whatsapp_business_messaging` y `whatsapp_business_management`. |
| `WHATSAPP_PHONE_NUMBER_ID` | Misma pantalla, debajo del número que envía. |
| `WHATSAPP_APP_SECRET` | App → **Configuración de la app → Básica → Clave secreta de la app**. Firma los avisos del webhook; sin ella el webhook no acepta nada. |
| `WHATSAPP_WEBHOOK_VERIFY_TOKEN` | Una palabra cualquiera, igual aquí y en Meta. |

`WHATSAPP_TEST_TO` solo se usa en el `.env` local, para el comando de prueba.

## Webhook

En la app de Meta → **WhatsApp → Configuración → Webhook → Editar**:

1. URL de devolución de llamada: `https://wms2-s5yd.onrender.com/webhooks/whatsapp/`
2. Token de verificación: el mismo de `WHATSAPP_WEBHOOK_VERIFY_TOKEN`.
3. **Verificar y guardar**. Meta llama a la URL con un GET; si contesta, queda dada de alta.
4. En **Campos del webhook**, suscribir **messages**: ahí vienen los estados
   (enviado, entregado, leído, falló) y los mensajes que escriben los clientes.

Las variables tienen que estar en Render **antes** de verificar, o Meta recibe
un 403.

## Probar

```
python manage.py probar_whatsapp                       # texto libre a WHATSAPP_TEST_TO
python manage.py probar_whatsapp --plantilla hello_world --idioma en_US
python manage.py probar_whatsapp --ver                 # los últimos 10 y su estado
```

El comando deja un renglón en la bitácora con el `wamid`, y el webhook lo mueve
a Entregada, Leída o Fallida con el código de Meta. Con el `.env` local los
renglones van a la base de producción, que es donde escribe el webhook.

El texto libre solo se entrega si el destinatario le escribió al número en las
últimas 24 horas. Fuera de esa ventana hay que usar plantilla.

## Números

`52` + 10 dígitos para México (Meta agrega el `1` por dentro); `1` + 10 para
EE. UU. Sin `+` ni espacios; el código los quita si vienen.

## Códigos de fallo frecuentes

| Código | Qué quiere decir |
|---|---|
| 131030 | El número no está en la lista "Para" (solo con el número de prueba). |
| 131047 | Texto libre fuera de la ventana de 24 horas: hay que usar plantilla. |
| 131026 | El destinatario no puede recibirlo (no tiene WhatsApp, versión vieja…). |
| 131042 | Problema de pago de la cuenta: falta método de pago en WhatsApp Manager. |
| 131049 | Meta no lo entregó para cuidar la experiencia del usuario (marketing). |
| 130472 | El usuario está en un experimento de Meta y no recibe marketing. |
