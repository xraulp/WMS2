# El orden de los archivos del expediente

El orden de las fotos es **información**, no presentación. En una entrada se
fotografía la misma pieza varias veces —el número de serie o de lote, el peso en
kilos o libras, la tabla nutrimental— y la documentación aduanal se arma
siguiendo esa secuencia. Si el ZIP las entrega en otro orden, lo que llega al
agente aduanal está mal aunque no falte ningún archivo.

Este documento existe porque esa regla no se deduce del código: quien lea
`operation_download_all` sin saberlo puede tomar el orden por un detalle
estético y romperlo sin enterarse.

## Cómo se decide el orden

Por este orden, y en este orden:

1. **`orden`** — la posición puesta a mano desde el panel Digital. Vale cero
   mientras nadie toque las flechas.
2. **`uploaded_at`** — cuándo se subió.
3. **`pk`** — desempata la subida múltiple, donde varios archivos comparten el
   instante.

Está en `Meta.ordering` de `OperationDocument`, así que lo respetan la pantalla,
el expediente y cualquier consulta que no pida otra cosa. **El ZIP lo repite
explícito**, porque de ese orden sale la numeración de los archivos que entrega
y no debe quedar a merced de que alguien edite el `Meta`.

> **Cuidado con ese `order_by` explícito.** Cuando se añadió el reordenado
> manual, el ZIP seguía ordenando solo por fecha de subida: la pantalla mostraba
> las fotos reordenadas y el ZIP salía como antes. Es decir, la función entera no
> servía para nada, que es justamente para lo que se hizo. Si algún día se toca
> esa línea, `orden` va primero.

## Reordenar a mano

Cada miniatura del panel Digital lleva dos flechas y su número de posición. El
número está a la vista porque es el mismo consecutivo con el que la foto sale en
el ZIP: quien ordena tiene que poder comprobar el resultado sin descargar nada.

Se mueve de una en una y no arrastrando. Con muchas fotos arrastrar sería más
cómodo, pero esta pantalla se usa sobre todo desde el móvil, donde el arrastre es
frágil; las flechas funcionan igual en los dos sitios.

Al mover, **se renumera el expediente entero** a 1..N. Eso es lo que arregla de
una vez los expedientes anteriores a este cambio, donde todos los documentos
valen cero y el orden lo lleva la fecha: la primera vez que alguien mueve algo,
ese expediente queda numerado. No hizo falta migrar datos.

**Lo que se sube después va al final.** Un archivo nuevo toma la posición
siguiente a la mayor que haya. Sin eso se colaría el primero, porque «sin
posición» es cero y cero va antes que uno.

**Quien puede subir puede ordenar**, incluido un cliente en sus propias
operaciones. Es el mismo criterio que `digital_upload`, para no tener dos reglas
distintas sobre la misma pantalla.

Los archivos en la papelera no se reordenan: fuera del expediente no hay posición
que ocupar.

## Cómo salen en el ZIP

```
CLA PO123 ED-0001 001.jpg     serie / lote
CLA PO123 ED-0001 002.jpg     peso
CLA PO123 ED-0001 003.jpg     tabla nutrimental
CLA PO123 ED-0001 001.pdf     factura
```

Tres detalles, y cada uno arregla un fallo real:

- **Cada tipo lleva su propio consecutivo** —fotos por un lado, documentos por
  otro—, que es como se pidió en su día.
- **Se agrupa por tipo, no por extensión.** Agrupando por extensión, una foto
  `.jpeg` entre `.jpg` abría una segunda serie y aparecían dos «foto 1» en la
  misma carpeta.
- **Tres cifras como mínimo.** Sin los ceros, el explorador de Windows ordena
  1, 10, 11, 2, 3… y la secuencia se lee mal aunque dentro del ZIP vaya bien.
  Son tres y no dos porque una operación puede pasar de cien fotos. Si alguna
  llegara a mil, el ancho crece solo.

## De dónde viene el orden inicial

Del orden en que el navegador manda los archivos:

- **Desde el móvil**, tomando y subiendo foto a foto, es el orden de captura. Sin
  nada más que hacer.
- **Desde la PC**, seleccionando varias de un tirón, es el que decida el
  explorador —normalmente alfabético—, que no tiene por qué ser el de captura.
  Para ese caso están las flechas.

Cubierto por `warehouse/tests_orden_de_archivos.py` y
`warehouse/tests_reordenar_archivos.py`.
