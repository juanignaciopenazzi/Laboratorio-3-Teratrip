# Definición de métricas de TeraTrip

Cada métrica se define de forma operacional: qué significa y con qué expresión SQL se calcula sobre la
tabla `booking_analytics` de la database `teratrip_db`. Cuando una pregunta usa alguno de estos
nombres, hay que usar la fórmula de acá y no improvisar una.

---

## Revenue confirmado

Ingreso real de TeraTrip. Solo cuentan las reservas con `status = 'confirmed'`; las canceladas y las
pendientes valen cero.

```sql
SELECT SUM(confirmed_revenue) AS revenue_confirmado
FROM teratrip_db.booking_analytics;
```

**No** se calcula con `SUM(total_amount)`. `total_amount` incluye reservas canceladas y pendientes, así
que sobrestima el ingreso. La columna `confirmed_revenue` ya vale `0.0` en esas filas, por lo que no
hace falta agregar un `WHERE status = 'confirmed'`: sumarla sobre toda la tabla ya da el resultado
correcto.

Por destino, por tipo de producto o por cualquier otra dimensión, es la misma suma agrupada:

```sql
SELECT destination_city, SUM(confirmed_revenue) AS revenue_confirmado
FROM teratrip_db.booking_analytics
GROUP BY destination_city
ORDER BY revenue_confirmado DESC;
```

---

## Tasa de cancelación

Porcentaje de reservas canceladas sobre el total de reservas.

```sql
SELECT SUM(CASE WHEN is_cancelled THEN 1 ELSE 0 END) * 100.0 / COUNT(*) AS tasa_cancelacion
FROM teratrip_db.booking_analytics;
```

El denominador es **todas** las reservas, incluidas las `pending`, no solo las confirmadas más las
canceladas. El resultado se expresa en porcentaje.

---

## Cobertura de pagos

Qué porcentaje del valor de una reserva ya fue cobrado con pagos aprobados. La métrica a nivel fila es
la columna `payment_coverage_pct`; a nivel agregado es su promedio.

```sql
SELECT AVG(payment_coverage_pct) AS cobertura_promedio
FROM teratrip_db.booking_analytics;
```

Es un promedio de porcentajes por reserva, no un porcentaje del total. Si se quisiera la proporción
global del dinero cobrado sobre el facturado, es otra métrica y otra fórmula:

```sql
SELECT SUM(approved_paid_amount) * 100.0 / SUM(total_amount) AS cobertura_global
FROM teratrip_db.booking_analytics;
```

Las dos son legítimas y dan números distintos. "Cobertura de pagos promedio" es la primera.

---

## Monto pendiente de cobro

Cuánto dinero falta cobrar. Es la suma de `payment_gap`, restringida a las reservas confirmadas: lo que
falta cobrar de una reserva cancelada no es una cuenta por cobrar.

```sql
SELECT SUM(payment_gap) AS pendiente_de_cobro
FROM teratrip_db.booking_analytics
WHERE is_confirmed;
```

---

## Ticket promedio

Valor promedio de una reserva confirmada.

```sql
SELECT AVG(total_amount) AS ticket_promedio
FROM teratrip_db.booking_analytics
WHERE is_confirmed;
```

Se usa `total_amount` con el filtro `WHERE is_confirmed`, y no `AVG(confirmed_revenue)` sobre toda la
tabla: esa otra expresión metería los ceros de las reservas canceladas y pendientes en el promedio,
hundiéndolo artificialmente.

---

## Cantidad de clientes

Clientes únicos, no reservas. Requiere `DISTINCT` porque un cliente aparece en tantas filas como
reservas tenga.

```sql
SELECT COUNT(DISTINCT customer_id) AS clientes
FROM teratrip_db.booking_analytics;
```

Por país:

```sql
SELECT customer_country, COUNT(DISTINCT customer_id) AS clientes
FROM teratrip_db.booking_analytics
GROUP BY customer_country
ORDER BY clientes DESC;
```

"Qué país tiene más clientes" se responde con esta fórmula. Contar filas en lugar de clientes distintos
responde otra pregunta: cuál es el país con más reservas.

---

## Cantidad de reservas

```sql
SELECT COUNT(*) AS reservas
FROM teratrip_db.booking_analytics;
```

Para un subconjunto, se agrega el filtro correspondiente. Reservas canceladas:

```sql
SELECT COUNT(*) AS canceladas
FROM teratrip_db.booking_analytics
WHERE is_cancelled;
```

Reservas de un destino:

```sql
SELECT COUNT(*) AS reservas
FROM teratrip_db.booking_analytics
WHERE destination_city = 'Madrid';
```

---

## Resumen de qué columna usar

| Si la pregunta es sobre… | Usar |
|---|---|
| ingreso, revenue, facturación, ventas | `SUM(confirmed_revenue)` |
| valor total contratado, incluido lo cancelado | `SUM(total_amount)` |
| dinero efectivamente cobrado | `SUM(approved_paid_amount)` |
| dinero pendiente de cobro | `SUM(payment_gap)` con `WHERE is_confirmed` |
| cantidad de reservas | `COUNT(*)` |
| cantidad de clientes | `COUNT(DISTINCT customer_id)` |
| proporción de cancelaciones | tasa de cancelación |
