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
- **Los adjuntos no viven en el chat.** Lo que se manda por el hilo entra al
  expediente digital como cualquier otro documento, que es donde el ZIP y los
  correos lo van a buscar; el mensaje solo guarda la marca de por dónde entró.
  Un archivo que viviera dentro de la conversación sería una segunda verdad,
  justo lo que este hilo vino a evitar. Ver **Los adjuntos**, más abajo.
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

## El contador global

Un aviso en una fila **solo sirve si esa fila está a la vista**. La tabla trae
doscientas operaciones ordenadas por fecha, así que un mensaje sobre una carga
de hace tres meses enciende un ámbar que nadie va a ver: está mil setecientos
píxeles por debajo del borde de la pantalla.

Por eso, a la derecha de las pestañas va un contador con **todo lo que le espera
a quien mira**, y al pulsarlo la tabla llega acotada a las operaciones que
esperan respuesta.

| Pieza | Qué hace |
|---|---|
| `resumen_sin_leer(user, tenant)` | El total, en una sola consulta agregada y **sin tope**: un contador que dice «3» cuando hay doce miente peor que no estar |
| `hilos_pendientes(user, tenant)` | Las operaciones con algo sin leer, la conversación más reciente primero |
| `operations_unread` | La tabla acotada, a donde lleva el contador |
| `verSinLeer()` en el tablero | Abre *Database*, limpia los demás filtros y pide esa tabla |

Detalles que no son casuales:

- **El total viaja con los avisos de fila**, en la misma respuesta de
  `chat_badges`. Los dos caducan en el mismo instante —cuando un aviso se apaga
  el total baja— y pedirlos por separado los deja discrepando entre peticiones.
- **Se ordena por el último mensaje**, no por la fecha de la operación: lo que
  se busca aquí es la conversación viva, no la carga reciente.
- **La consulta parte de los mensajes, no de las conversaciones.** Un `exclude`
  sobre los mensajes de un hilo descarta el hilo entero en cuanto uno haya
  escrito en él una vez, que es justo el caso normal: la empresa contesta y el
  cliente vuelve a escribir.
- **La fila leída no desaparece bajo el ratón.** La lista se pide una vez y se
  queda quieta, así que abrir un hilo desde ahí no reordena lo que se está
  mirando. El contador de arriba sí baja, que es donde se espera ver el efecto.
- **En cero no se pinta.** Un contador que siempre está ahí diciendo «0» deja de
  leerse a la semana. Va separado de las pestañas para que aparecer y
  desaparecer no las desplace.
- **La tabla acotada dice que lo está**, con el camino de vuelta al lado: una
  tabla que de pronto muestra dos filas de doscientas, sin explicación, se lee
  como que se perdieron las demás.

## Los adjuntos

Se puede mandar un archivo dentro del hilo, y esa es la mitad del valor de la
conversación: «mándenos la foto del pedimento» se contesta con la foto, no con
una explicación de dónde subirla.

**Un adjunto del chat es un documento del expediente.** No hay una segunda
colección: se crea un `OperationDocument` normal —con su nombre digital, su tipo
y su posición al final del expediente— y lo único que lo distingue es el campo
`mensaje`, que dice por dónde entró. Si fueran dos caminos, el ZIP y los correos
tendrían que aprender el segundo, y el día que se olvidaran el archivo estaría en
la conversación pero no en el expediente.

| Decisión | Por qué |
|---|---|
| El campo va en `OperationDocument`, no en `Message` | Así un mensaje lleva varios archivos. Quien manda tres fotos de la misma tarima está diciendo una sola cosa; tres mensajes con una foto cada uno la convierten en tres |
| Hasta **5 archivos** por mensaje | No es un límite técnico: es para que el hilo siga siendo una conversación. Quien sube veinte fotos está armando un expediente, y para eso está el panel Digital, que además deja ordenarlas |
| Hasta **25 MB** por archivo | Este sí es técnico: el archivo se lee para subirlo al bucket |
| Un archivo **solo**, sin texto, es un mensaje completo | Mandar la foto de la etiqueta sin escribir nada es una respuesta |
| Si la subida falla, **el mensaje se queda escrito** | El archivo viaja al bucket, que está al otro lado de la red. Un 500 ahí le borraría al operador lo que acababa de escribir sin decirle por qué; en su lugar se avisa de que el archivo no subió |
| Un archivo mandado a la papelera **desaparece del hilo** | El hilo no puede seguir ofreciendo algo que ya se retiró del expediente. Lo que se dijo se queda: los mensajes no se borran |

En el globo, las fotos se ven y lo demás se enlaza por su **nombre digital** —el
mismo que tendrá en el ZIP—. Una miniatura de un PDF no dice nada; el nombre sí.

## Con qué nombre se firma cada mensaje

Un mensaje firmado solo con el nombre de usuario —`custtes1`— no le dice nada a
quien lo lee del otro lado: lo primero que necesita saber es si le escribe su
almacén o su cliente, y solo después quién en concreto. Por eso la firma lleva
las dos cosas, la empresa primero:

```
DYSER · Ana Ruiz              Customer Test · Juan
```

La empresa **no se guarda en el mensaje**: sale del lado, que sí está guardado.
Si mañana la empresa se cambia el nombre, los mensajes viejos pasan a mostrar el
nuevo, que es lo correcto —es la misma empresa—. El nombre de la persona, en
cambio, queda congelado en el mensaje desde que se escribió, para que dar de baja
una cuenta no borre quién dijo qué.

`utils.nombre_corto` recorta el nombre para que quepa: corta en la primera coma
y quita las formas societarias del final, porque «Customer Test, SA. de CV» no
es una firma sino un dato del acta constitutiva, y ocupa media línea sin aportar
nada frente a «Customer Test». No pretende ser exacto —una empresa puede
llamarse «Ltd» de verdad— sino legible, y si al recortar no quedara nada
devuelve el nombre entero: es preferible una firma larga a una firma vacía.

## El aviso no se espera

El aviso por correo sale **fuera de la petición**, en su propio hilo.

Se midió con el correo tal como estaba: escribir un mensaje costaba **diez
segundos justos** —el `EMAIL_TIMEOUT`— porque el envío iba dentro de la petición
y el panel no se repintaba hasta que el servidor de correo terminaba de no
contestar. Y no era solo el primer mensaje: la espera de 15 minutos que calla
los avisos seguidos solo cuenta cuando el envío **salió bien**, así que cada
mensaje volvía a intentarlo y volvía a costar diez segundos. Escribir en el chat
era insoportable por una razón que no tenía nada que ver con el chat.

> **Esto no vale para todos los avisos.** El alta de una operación le dice al
> operador en pantalla si el correo salió o falló; mandarlo aparte convertiría
> ese aviso en una mentira. `notifications.en_segundo_plano` es para los envíos
> cuyo resultado no se está mirando —hoy, el del chat—, donde lo único que la
> espera consigue es dejar la pantalla quieta.

**En local no se usa.** Producción va sobre PostgreSQL, que aguanta varias
escrituras a la vez; SQLite no, y cortaba la petición con «database is locked»
cuando el hilo escribía su renglón en la bitácora mientras la vista todavía
guardaba el mensaje. Se vio en un navegador: el POST daba 500 en local y
funcionaba en el servidor. El camino real lo cubre la prueba
`LaPantallaNoEsperaAlCorreoTests`, que además **mide el tiempo**: sin medirlo, un
envío en línea de quince segundos también devuelve 200 y la prueba pasaría igual.

## Cómo está hecho

| Pieza | Dónde |
|---|---|
| `Conversation`, `Message`, `ConversationRead`, `OperationDocument.mensaje` | `warehouse/models.py` |
| `lado_en_el_hilo`, `anotar_hilos`, `resumen_sin_leer`, `hilos_pendientes` y las vistas | `warehouse/views.py` |
| `avisar_mensaje_nuevo`, `correos_del_tenant`, `en_segundo_plano` | `warehouse/notifications.py` |
| `nombre_corto` | `warehouse/utils.py` |
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
