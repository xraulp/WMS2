# Facturación de la plataforma

Quién ha pagado, quién debe y quién va tarde. Vive en la pestaña **Billing** de
`/platform/`, junto a las empresas y la bitácora de envíos, porque es del nivel
que administra el SaaS y no del que opera un almacén.

## Qué había antes

Tres campos sueltos dentro de `Subscription` — `invoice_number`,
`invoice_date` y `amount_usd` — y `Subscription` es **una fila por empresa**. Es
decir: cabía una sola factura por cliente, y emitir la de septiembre borraba la
de agosto. Sin historial, sin estado de pago y sin forma de saber quién debía.
En la práctica no se podía facturar.

Esos tres campos siguen en el modelo para no perder lo que hubiera capturado
alguien, marcados como obsoletos. Nada los escribe ya y ninguna pantalla los
lee.

## Cómo funciona

Una factura se emite a una empresa por un **periodo** (un mes), con un **monto
capturado a mano** y una **fecha de vencimiento**. Nace *pendiente*. Desde ahí
solo puede ir a dos sitios:

- **Pagada**, cuando se registra el cobro, con su fecha y una referencia
  opcional.
- **Cancelada**, con un motivo obligatorio.

No hay más caminos, y son de ida:

- **Una factura pagada no se cancela.** Hubo un cobro; borrarlo así dejaría el
  dinero sin explicación. Lo que corresponde es una nota de crédito, que hoy no
  existe — por eso el sistema se niega en vez de improvisar.
- **Una factura pagada no se vuelve a cobrar.** Pisaría la fecha del cobro real.
- **Una cancelada no se cobra.**

## «Vencida» no es un estado

Se deduce: pendiente y con la fecha de vencimiento pasada. Se decidió así a
propósito, porque guardarlo obligaría a un proceso diario que fuera marcándolas
una por una — y el día que ese proceso no corriera, la pantalla estaría
mintiendo sobre quién debe. Deducirlo no puede desincronizarse.

En el listado se ve como un estado más, y el filtro *Overdue* funciona igual que
los otros. Por dentro es una comparación de fechas.

## La numeración

`INV-2026-0001`, serie única para toda la plataforma y una por año. La lleva
`InvoiceSequence`, con la fila bloqueada al apartar el número donde el motor lo
permite: sin eso, dos personas emitiendo a la vez leen el mismo valor y se
llevan el mismo número, que en una serie de facturas es el peor error posible.

Dos consecuencias que conviene tener claras:

- **Cancelar no libera el número.** Ya salió al cliente; reutilizarlo sería peor
  que dejar el hueco.
- **El número se aparta al final**, cuando ya está validado todo lo demás. Si el
  formulario se rechaza — un monto en blanco, una empresa que no existe — no se
  gasta ningún número.

El 1 de enero la serie vuelve a empezar en 1 sin que nadie tenga que acordarse,
porque el año va dentro del número.

## Qué se congela al emitir

El **monto** y el **plan** se copian a la factura y ya no cambian. Si mañana
sube el precio del plan o el cliente se cambia de plan, las facturas ya emitidas
siguen diciendo lo que se cobró. Una factura es un registro de lo que pasó, no
una vista de la situación de hoy.

Por lo mismo, la empresa está enlazada con `PROTECT`: **no se puede borrar una
empresa que tenga facturas**. Dar de baja a un cliente se hace con `is_active`,
que no destruye nada.

## Quién puede qué

| | Soporte (staff) | Administrador |
|---|---|---|
| Ver el listado y el resumen | ✅ | ✅ |
| Emitir | — | ✅ |
| Registrar un cobro | — | ✅ |
| Cancelar | — | ✅ |

El soporte ve todo porque para atender una llamada necesita saber si esa empresa
está al corriente. Las tres acciones son del administrador.

La pantalla esconde los formularios al soporte, pero eso es cortesía, no
seguridad: **la vista vuelve a comprobar el rol en cada POST**. Esconder un
botón nunca es un permiso.

## Duplicar un mes

Emitir dos veces el mismo periodo a la misma empresa es el error caro — el
cliente recibe dos cobros por lo mismo —, así que la pantalla avisa y no lo hace
salvo que se confirme marcando la casilla. Una factura **cancelada** no cuenta
para ese aviso: si se canceló, ese mes sigue sin cobrarse.

## Lo que todavía no hace

- **No genera un PDF de la factura** ni la manda por correo. Hoy es un registro
  interno de cobro: sirve para saber quién debe, no para entregarle un documento
  al cliente.
- **No calcula el monto.** Se captura a mano. Las métricas de uso
  (`operations_count`, `storage_used_gb` en `Subscription`) están ahí y nadie
  las mira todavía.
- **No hay notas de crédito**, y por eso una factura pagada no se puede
  cancelar.
- **No hay impuestos**: un solo importe. Lo fiscal vive en el sistema contable.
- **No avisa de los vencimientos.** El resumen los muestra en rojo cuando se
  abre la pantalla; nadie recibe un correo.

Cubierto por `warehouse/tests_facturacion.py`.
