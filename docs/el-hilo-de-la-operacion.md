# El hilo de la operación

Cada operación puede tener un hilo de mensajes entre **la empresa y su
cliente**. No es un chat interno y no es un chat entre dos personas: es la
empresa hablando con el cliente sobre esa operación concreta.

## Por qué cuelga de la operación

Lo que se conversa sobre una carga —«manden la foto de la etiqueta», «el
pedimento va con este número», «¿ya llegó?»— vivía en el WhatsApp de alguien.
Cuando entra el turno siguiente, esa información no está en ninguna parte, y
sin embargo de ella dependen decisiones que después hay que poder consultar.

Colgar el hilo de la operación es lo que convierte la conversación en parte del
expediente: queda junto a las fotos y los documentos, y la lee quien tome el
caso mañana. Es la misma razón por la que el orden de las fotos importa (ver
`orden-de-los-archivos.md`): lo que el trabajo necesita no puede depender de
que alguien se acuerde.

**Un chat interno entre los usuarios de la empresa no se construyó a
propósito.** Compite con WhatsApp, que ya tienen abierto todo el día, y no deja
nada en el expediente.

## Quién participa

| Lado | Quiénes |
|---|---|
| **Empresa** (`TENANT`) | Cualquier operador con acceso a la operación: `superadmin`, `admin`, `manager` y `staff` |
| **Cliente** (`CUSTOMER`) | Todos los usuarios cuyo `profile.customer` sea el cliente de la operación |

Del lado de la empresa contesta **el del turno**, no el que empezó la
conversación: por eso el hilo no es de una pareja de usuarios. Del lado del
cliente pueden escribir varias personas si el cliente tiene varias dadas de
alta, y todas ven lo mismo.

**El lado lo pone el servidor**, nunca el formulario. Un cliente que mandara
`side=TENANT` firmaría como la empresa; la vista ignora ese campo y decide por
el rol de quien escribe.

Quien no puede abrir la operación no puede leer el hilo: el criterio es el
mismo `customer_can_access_op` que usa `operation_detail`. Quien tiene el rol en
blanco —un alta a medias— no escribe ni lee.

## Lo que el sistema no hace

- **No hay mensajes internos.** Todo lo que se escribe lo ve el cliente, y esa
  regla tiene que seguir siendo evidente para quien escribe: el aviso bajo el
  botón lo dice. El día que hagan falta notas internas serán mensajes marcados
  y pintados aparte, nunca un silencio que haya que recordar.
- **Los mensajes no se editan ni se borran.** Es la diferencia entre un chat y
  una nota: de lo que se dijo cuelgan decisiones, y hay que poder consultarlo
  tal como se escribió.
- **No hay adjuntos, todavía.** Lo que se manda va al expediente digital, que
  es donde el ZIP y los correos lo van a buscar. Un adjunto que viviera solo en
  el chat sería una segunda verdad, justo lo que este hilo vino a evitar.
- **No se ofrece el hilo si el cliente no está en el catálogo.** Una operación
  con el cliente capturado a mano y sin alta no tiene usuarios del otro lado;
  ofrecer el hilo sería ofrecer un buzón que nadie abre.
- **El campo `customer_notes` de la operación sigue donde estaba.** Se decidió
  no tocarlo: lo ya escrito no se pierde y no hay migración de datos que pueda
  salir mal. Por un tiempo hay dos sitios donde el cliente puede escribir.

## El aviso por correo

Sin aviso el chat no existe: nadie se queda mirando la pantalla esperando. Pero
un correo por mensaje convierte una conversación de diez líneas en diez
correos, y a la tercera vez el destinatario deja de abrirlos.

La regla, en `notifications.AVISO_ESPERA`: **se avisa del primer mensaje y se
callan los siguientes durante 15 minutos**. Cuando la conversación se enfría,
el siguiente mensaje vuelve a avisar. Cada lado lleva su propia espera, porque
son dos buzones distintos.

- Lo que escribe el cliente lo reciben **todos los operadores activos de la
  empresa** que tengan correo. Funciona como un buzón compartido: dirigir el
  aviso a una sola persona es la forma de que un mensaje espere a que esa
  persona vuelva de vacaciones.
- Lo que escribe la empresa lo recibe el cliente por los mismos correos a los
  que ya se le mandan los avisos de sus operaciones (`email_recipients`).

**El silencio también queda registrado.** Cuando la espera no se cumplió, se
escribe un renglón `SKIPPED` con `detail='aviso_reciente'` en la bitácora de
envíos. Es a propósito: la pregunta que se le hace a esa pantalla es «¿por qué
no me avisaron?», y un silencio sin registro no la responde.

Un fallo enviando el correo **nunca impide que el mensaje se escriba**. Lo
importante es lo que se dijo; el aviso es secundario, y va por el mismo
`_never_breaks` que el resto.

> El aviso depende de que el correo saliente funcione. Mientras Resend no esté
> verificado, los mensajes se escriben y se leen en pantalla, pero nadie recibe
> el correo. Ver `configurar-correo-resend.md`.

## La marca de lectura

Es **por persona, no por lado**: que un operador haya abierto el hilo no
significa que los demás se enteraron.

El hilo se da por leído **cada vez que se pide el panel**, que es lo mismo que
decir «mientras alguien lo está mirando»: esa vista es la que refresca el
contenido cada diez segundos. Cuando el modal se cierra deja de pedirse, y lo
que llegue después vuelve a contar como nuevo.

Cuando alguien escribe, el hilo se le da por leído **dos veces**: una explícita
al guardar el mensaje y otra al repintar el panel en la respuesta. La segunda
tapa a la primera, y por eso `sin_leer_para` excluye además los mensajes
propios por su cuenta: si mañana un mensaje entrara por un camino que no
repinta el panel —un comando, una API—, su autor lo vería como no leído.

En la tabla de operaciones, junto a *View*:

| Se ve | Significa |
|---|---|
| nada | La operación no tiene hilo |
| 💬 gris | Hay conversación y está al día |
| 💬 **n** en ámbar | Hay `n` mensajes que quien mira no ha visto |

El conteo se calcula en **dos consultas para todo el listado** (`anotar_hilos`),
no una por fila: la tabla trae hasta doscientas operaciones y con una consulta
por fila dejaría de cargar en cuanto la empresa lleve unos meses trabajando.

## Cómo está hecho

| Pieza | Dónde |
|---|---|
| `Conversation`, `Message`, `ConversationRead` | `warehouse/models.py` |
| `lado_en_el_hilo`, `anotar_hilos`, las dos vistas | `warehouse/views.py` |
| `avisar_mensaje_nuevo`, `correos_del_tenant` | `warehouse/notifications.py` |
| El panel y la lista de mensajes | `templates/warehouse/partials/chat_thread.html` y `chat_messages.html` |
| El correo | `templates/warehouse/email/chat_email.html` |
| Las pruebas | `warehouse/tests_chat.py` |

El panel y la lista van **en dos plantillas a propósito**: el refresco cada diez
segundos reemplaza solo la lista. Si reemplazara el panel entero, borraría lo
que el operador lleva escrito cada vez que se cumplen los diez segundos.

## Dos cosas que la pantalla tiene que hacer sola

Ambas se descubrieron probando en producción, y sin ellas el hilo parece roto
aunque el servidor esté haciendo lo correcto.

**El panel baja al último mensaje.** Mide 260 px: con once mensajes quedaban
590 px ocultos por debajo, así que lo que llegaba entraba fuera de la vista y
parecía que no llegaba nada. Baja solo cada vez que entra contenido — salvo
para quien subió a leer algo, que se queda donde estaba (`data-abajo` en
`chat_thread.html`).

**Los avisos de la tabla se refrescan sin recargar.** La tabla se pinta una vez
y se queda quieta, de modo que un mensaje nuevo no se veía hasta la siguiente
recarga y el aviso de un hilo ya leído seguía encendido. `chat_badges` devuelve
solo los números —recargar doscientas filas con su scroll horizontal a mano y
sus menús abiertos no es opción— y `refrescarAvisosDeChat` los reparte cada 30
segundos y al cerrar el modal, que es el momento exacto en que la tabla está
mintiendo. El botón existe en todas las filas, oculto: si solo se pintara donde
ya hay conversación, el primer mensaje de una operación no tendría dónde
aparecer.

## El rol de un usuario de cliente

Un usuario de cliente lleva rol `customer` **y** su cliente. La pantalla de
usuarios ofrecía los dos campos sueltos, así que se podía dar de alta a alguien
«para un cliente» dejándole el `staff` que viene por omisión: el perfil quedaba
con cliente y con rol de la casa, y como `customer_ops_filter` acota por rol,
esa cuenta **veía todas las operaciones de la empresa**, incluidas las de los
demás clientes, mientras quien la creó creía haber dado un acceso limitado.

Ahora el alta y la edición rechazan las dos incoherencias:

- **Cliente asignado con rol de la casa** — el caso peligroso.
- **Rol `customer` sin cliente** — no abre nada de más, pero entrega una cuenta
  que no alcanza ni una sola operación, y el «no veo mis operaciones» tarda días
  en llegar.

El filtro sigue acotando por rol y así debe seguir: el `customer` del perfil de
un operador no significa nada. La puerta se cierra en la pantalla, que es donde
se describe mal a una persona. Está en `warehouse/tests_rol_y_cliente.py`.
