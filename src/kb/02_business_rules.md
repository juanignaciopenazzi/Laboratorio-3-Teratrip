# Reglas de negocio de TeraTrip

TeraTrip es una agencia de viajes online. Vende vuelos, alojamiento y paquetes que combinan ambos. Toda
la actividad comercial está reflejada en la tabla `booking_analytics` de la database `teratrip_db`.

## Estados de una reserva

La columna `status` de `booking_analytics` tiene tres valores posibles y son mutuamente excluyentes.

**`confirmed`** — La reserva está cerrada y es válida. Es la única que genera ingreso para TeraTrip.
Solo estas reservas cuentan como venta efectiva.

**`cancelled`** — La reserva se dio de baja. **No genera ingreso**, aunque tenga un `total_amount`
distinto de cero y aunque haya recibido pagos. El `total_amount` de una reserva cancelada representa lo
que la reserva habría valido, no lo que TeraTrip facturó.

**`pending`** — La reserva está iniciada pero todavía no se confirmó. Todavía no genera ingreso. No es
lo mismo que una cancelación: una reserva `pending` puede terminar confirmándose.

La consecuencia más importante: **`total_amount` no es ingreso**. Sumar `total_amount` sobre toda la
tabla da el valor de todo lo que pasó por el sistema, incluyendo lo cancelado y lo pendiente. Para medir
ingresos hay que usar `confirmed_revenue`, que ya vale `0.0` en las reservas que no están confirmadas.

## Pagos

Una reserva puede tener pagos asociados, y esos pagos pueden estar en distintos estados. **Solo los
pagos aprobados cuentan.** Un pago rechazado, pendiente o reembolsado no representa dinero cobrado.

Esta regla ya está aplicada en las columnas de `booking_analytics`:

- `approved_paid_amount` suma únicamente los pagos aprobados. Si una reserva solo tuvo pagos rechazados,
  vale `0.0`.
- `last_payment_method` es `NULL` si la reserva no tiene ningún pago aprobado. No es el método del pago
  que falló: si nadie pagó efectivamente, no hay método que informar.

No hace falta filtrar por estado de pago al consultar: las columnas ya lo contemplan.

## Confirmación y cobro son cosas distintas

Una reserva puede estar confirmada y no estar cobrada, o estar cobrada solo parcialmente. Son dos ejes
independientes:

- `status` / `confirmed_revenue` responden **¿se vendió?**
- `approved_paid_amount` / `payment_gap` / `payment_coverage_pct` responden **¿se cobró?**

Una reserva `confirmed` con `payment_gap > 0` es una venta real con saldo pendiente de cobro. Una
reserva `cancelled` con `approved_paid_amount > 0` es dinero cobrado sobre algo que después se dio de
baja, y suele ser candidata a reembolso.

Al responder una pregunta hay que identificar cuál de los dos ejes está preguntando. "¿Cuánto
facturamos?" es el eje de venta. "¿Cuánto nos deben?" es el eje de cobro.

## Granularidad: una fila por reserva

`booking_analytics` tiene **exactamente una fila por `booking_id`**. No hay filas repetidas ni versiones
históricas de una misma reserva.

Esto determina cómo se cuenta cada cosa:

- **Reservas**: `COUNT(*)` o `COUNT(booking_id)`. No hace falta `DISTINCT`.
- **Clientes**: `COUNT(DISTINCT customer_id)`. El `DISTINCT` **sí** es necesario, porque un cliente con
  cinco reservas aparece en cinco filas. Contar filas para responder "cuántos clientes" da mal.

Esta es la confusión más común al consultar `booking_analytics`: contar reservas cuando la pregunta era
por clientes, o al revés.

## Relación entre las entidades

Cada fila de `booking_analytics` combina, ya desnormalizada, la información de la reserva, su cliente,
sus pagos agregados y sus proveedores.

- **Reserva ↔ cliente**: muchas reservas por cliente. `customer_id` se repite entre filas.
- **Reserva ↔ pagos**: una reserva puede tener varios pagos, pero ya vienen agregados en
  `approved_paid_amount` y `last_payment_method`. Los pagos individuales no son consultables.
- **Reserva ↔ proveedor**: `airline` y `hotel_name` corresponden al vuelo y al hotel de esa reserva.
  Están vacíos cuando el producto no los incluye.

Como todo está en una sola tabla, **ninguna pregunta de negocio requiere un JOIN entre tablas
distintas**. Si una consulta parece necesitar otra tabla, el dato ya está en una columna de
`booking_analytics`.

## País del cliente vs. ciudad de destino

`customer_country` es el país donde vive el cliente. `destination_city` es la ciudad a la que viaja. Son
independientes: un cliente de Argentina puede viajar a Madrid.

Las preguntas sobre "clientes por país" usan `customer_country`. Las preguntas sobre "destinos" usan
`destination_city`. No existe una columna con el país del destino.
