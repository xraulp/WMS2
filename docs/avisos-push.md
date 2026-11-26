# Avisos push a los operadores

Los avisos push suenan en el teléfono de los operadores de una empresa. Usan **Web Push**, el estándar de los navegadores: no hay app de tienda, ni servicio contratado, ni costo.

## Qué avisa hoy

| Evento | A quién | Se enciende en |
|---|---|---|
| Un cliente escribe en el hilo de una operación | Todos los operadores activos de la empresa | Ficha del cliente → «Push a nuestros operadores» → «Cuando este cliente escribe un mensaje» |
| Se registra una operación de un cliente | Todos los operadores menos quien la registró | Ficha del cliente → «Cuando se registra una operación de este cliente» |
| Se registra una operación de un cliente | Los usuarios de **ese cliente** | Ficha del cliente → «Push al cliente» → «Cuando se registra una operación suya» |

- **Por cliente.** Cada empresa decide cliente por cliente, según lo que acuerde con cada uno. Las dos casillas nacen apagadas.
- **El correo sigue saliendo igual.** Push es un canal más, porque el servicio del fabricante no garantiza la entrega.
- **Mensajes seguidos del mismo hilo** se reemplazan en el teléfono en lugar de apilarse. No esperan los 15 minutos del correo.
- **Al tocar el aviso** se abre el expediente de la operación: la pantalla móvil en el teléfono y el tablero en la computadora.
- **Cada envío deja un renglón** en la bitácora de avisos, con canal `PUSH`. Si no salió, el renglón dice por qué: `no_devices` (nadie tiene avisos activos) o `no_operators`.

## Configurarlo (una sola vez)

1. Genera las llaves:
   ```
   python manage.py generar_llaves_push
   ```
2. En Render → Environment, pega `VAPID_PUBLIC_KEY` y `VAPID_PRIVATE_KEY`. Si quieres, agrega también `VAPID_SUBJECT=mailto:...`; si no, se usa `DEFAULT_FROM_EMAIL`.
3. Guarda. Render vuelve a desplegar.

**No vuelvas a generar las llaves** una vez en uso: cada dispositivo quedó suscrito con la pública, y con otra todos tendrían que activar los avisos de nuevo. La privada no va en git.

Sin llaves, nada cambia: no se manda push, y «Mi cuenta» dice que falta configurarlo.

## Activarlos en un teléfono

Cada operador, y cada usuario de un cliente, lo hace en su teléfono: **Mi cuenta → Avisos en este dispositivo → Activar avisos**, y luego acepta el permiso. Ahí mismo está «Mandar un aviso de prueba».

- **Android:** funciona en Chrome tal cual.
- **iPhone:** necesita iOS 16.4 o posterior, y antes hay que **agregar el WMS a la pantalla de inicio** (Compartir → Agregar a pantalla de inicio). Después se abre el WMS desde ese ícono y se activan los avisos ahí. Desde Safari normal no se puede.
- **Si alguien bloqueó el permiso**, se desbloquea en los ajustes del navegador o del teléfono.
- **Si cambia de teléfono o borra los datos del navegador**, tiene que volver a activarlos. La suscripción vieja se borra sola en cuanto el servicio dice que ya no existe.

## Dónde está el código

| Qué | Dónde |
|---|---|
| A quién y qué se manda | `warehouse/avisos_push.py` |
| Service worker, manifest, alta y baja | `warehouse/vistas_push.py`, `templates/warehouse/push/sw.js` |
| Dónde se dispara | `notify_operation_created` y `avisar_mensaje_nuevo` en `warehouse/notifications.py` |
| Dispositivos | modelo `PushSubscription` |
| Casillas por cliente | `Catalog.push_on_message`, `Catalog.push_on_create` |
| Pruebas | `warehouse/tests_avisos_push.py` |
