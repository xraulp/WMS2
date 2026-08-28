# Alertas: qué tipos existen y cuáles tiene hoy el WMS

Diego pidió ver «qué tipos de alertas existen» antes de decidir cuáles quiere.
Esto es el catálogo, agrupado por lo que vigila cada una, con el estado real en
el sistema y lo que costaría cada una. La única implementada hoy es la primera.

Una nota que atraviesa toda la lista: **el plan de Render no permite Cron Jobs**,
así que cualquier alerta que tenga que *salir sola* (un correo a las 8 de la
mañana) está bloqueada hasta resolver eso. Las que se calculan **al abrir la
pantalla** funcionan ya, y son las que están marcadas como listas o baratas. Al
final hay una salida para el cron que no cuesta dinero.

---

## 1. Alertas de tiempo

| Alerta | Qué vigila | Estado |
|---|---|---|
| **Permanencia en bodega** | Días que lleva guardada una entrada sin liberar | ✅ **Implementada** |
| Fecha comprometida de salida | La carga tenía fecha prometida y se pasó | Falta el campo «fecha comprometida» en la operación |
| Caducidad de producto | Lotes perecederos que vencen | Falta fecha de caducidad; solo aplica a algunos giros |
| Demora de equipo (detention) | Días que un trailer o contenedor lleva parado | El dato del trailer ya se captura; falta la fecha de llegada del equipo |
| Expediente incompleto | Entradas que a los N días siguen sin documentos | Barato: la papelería ya se cuenta por operación |

La primera es la que pediste y ya está funcionando: cada cliente puede tener su
propio plazo en su ficha, la empresa tiene el suyo por defecto (7 días), se
avisa al cumplirse el plazo y sube de tono al doble.

## 2. Alertas de operación

| Alerta | Qué vigila | Estado |
|---|---|---|
| Descuadre de piezas | Lo que salió no cuadra con lo que entró | Barato: entrada y salidas ya están enlazadas por Custom ID |
| Mercancía dañada sin resolver | `damage` marcado y sin nota de cierre | Barato |
| Operación sin fotos | Se registró una entrada y nadie subió una imagen | Barato |
| Falta referencia aduanal | Sin pedimento o REF AA en tráfico internacional | Barato, pero hay que decidir cuándo es obligatorio |
| Salida parcial olvidada | Una entrada con salidas parciales que lleva meses parada | Medio |

## 3. Alertas de ubicación *(nuevas, ahora que hay posiciones)*

| Alerta | Qué vigila | Estado |
|---|---|---|
| **Mercancía sin ubicar** | Entró y nadie dijo dónde quedó | Muy barato — **la recomiendo primero** |
| Posición ocupada por dos cargas | Dos entradas vivas en el mismo hueco | Barato |
| Zona de andén congestionada | Más de N operaciones paradas en STAGING o RECEIVING | Barato |
| Capacidad de la posición | Se pasó del peso o los bultos que admite | Falta el campo capacidad en la ubicación |

## 4. Alertas comerciales

| Alerta | Qué vigila | Estado |
|---|---|---|
| Cliente sin usuario dado de alta | No puede entrar al portal | Ya se ve el conteo en Customers; falta convertirlo en aviso |
| Factura vencida / sin cobrar | Facturación de la plataforma | El modelo ya tiene estado de cobro y vencimiento |
| Cliente sobre su espacio contratado | Más bultos de los acordados | Falta el acuerdo por cliente |

## 5. Alertas de sistema

| Alerta | Qué vigila | Estado |
|---|---|---|
| Avisos que no salieron | Renglones `FAILED` en la bitácora de notificaciones | Muy barato — la bitácora ya existe, hoy hay que ir a mirarla |
| Papelera por purgar | Documentos borrados hace más de N días | Ya existe la papelera |
| **Mensajes sin leer** | Conversaciones esperando respuesta | ✅ Ya implementada |

---

## Los tres canales, y cuál conviene

1. **En pantalla, calculada al mirar** — es lo que hacen hoy el contador de
   mensajes y el de permanencia. No necesita cron, no puede quedarse
   desactualizada y no cuesta nada. **Para casi todo, es suficiente.**
2. **Correo o WhatsApp automático** — es lo que necesita cron, y además exige
   decidir a quién y con qué frecuencia, o se convierte en ruido que nadie abre.
3. **Correo bajo demanda** — un botón «mandarme el listado de vencidas». Sin
   cron, y a menudo es lo que la gente realmente quiere.

### La salida para el cron, sin cambiar de plan

Render no da Cron Jobs en el plan actual, pero un **cron externo gratuito**
(GitHub Actions con `schedule`, o cron-job.org) puede llamar cada mañana a una
URL del sistema protegida por un token secreto, y esa URL hace el trabajo: manda
el resumen de vencidas, purga la papelera, cierra lo que toque. Es la forma de
desbloquear de una vez todos los pendientes que hoy dicen «falta cron», sin
pagar más. El único cuidado es que la URL exija el token y no haga nada
destructivo si la llaman dos veces.

---

## Lo que recomiendo, por orden

1. **Mercancía sin ubicar.** Ahora que la ubicación existe, lo que la hace fiable
   es notar cuándo falta. Es una tarde de trabajo y evita que el dato nazca
   opcional para siempre.
2. **Avisos que no salieron.** Hoy hay que acordarse de ir a mirar la bitácora;
   un correo que falla en silencio es peor que no tener correo.
3. **El resumen diario por correo**, con el cron externo. Es lo que convierte las
   alertas de «lo veo si entro» en «me entero aunque no entre».
4. **Descuadre de piezas.** Es la alerta que encuentra errores de captura, y esos
   son los que acaban en una discusión con el cliente.
