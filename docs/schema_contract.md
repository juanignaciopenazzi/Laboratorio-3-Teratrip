# Contrato de esquema — `teratrip_db.booking_analytics`

Fuente única de verdad del esquema de la tabla sábana. Lo consumen:

- `src/glue_jobs/00_raw_to_curated.py` (Fase 0 — bootstrap)
- `src/lambdas/normalize/` (Fase 3 — normalización de los registros del PDF)
- `src/glue_jobs/merge_curated.py` (Fase 4 — merge incremental)
- `src/kb/01_data_dictionary.md` (Fase 6 — diccionario de datos de la Knowledge Base)

Si estos cuatro divergen, el pipeline falla o —peor— castea en silencio.

---

## Nombres exactos

| Qué | Valor |
|---|---|
| Database (Glue Data Catalog) | `teratrip_db` |
| **Tabla (Athena)** | **`booking_analytics`** |
| Archivo en S3 | `s3://teratrip-data-lake-955030484229/curated/booking_analytics/teratrip_booking_analytics.parquet` |
| Región | `us-east-1` |
| WorkGroup de Athena | `wg-teratrip` |

> El nombre de la **tabla** y el del **archivo** son distintos. El Crawler nombra la tabla según la
> carpeta (`booking_analytics`), que es además el nombre que usa la consigna en su ejemplo de SQL.
> El diccionario de datos de la KB debe decir `booking_analytics`, nunca `teratrip_booking_analytics`.

---

## Columnas (20)

Este es el **orden canónico**. El merge de la Fase 4 debe hacer un `select` explícito sobre esta lista
antes del `union` — nunca un union posicional a ciegas.

| # | Columna | Tipo | Significado |
|---|---|---|---|
| 1 | `booking_id` | `STRING` | PK. Una fila por reserva. Formato `B%06d` en el dataset base; los ingresados por documento usan la serie `B90xxxx`. |
| 2 | `booking_date` | `DATE` | Fecha de la reserva. |
| 3 | `booking_year` | `INT` | Derivado: `YEAR(booking_date)`. |
| 4 | `booking_month` | `INT` | Derivado: `MONTH(booking_date)`. |
| 5 | `destination_city` | `STRING` | Ciudad de destino. |
| 6 | `product_type` | `STRING` | `flight` \| `hotel` \| `package`. |
| 7 | `status` | `STRING` | `confirmed` \| `cancelled` \| `pending`. |
| 8 | `customer_id` | `STRING` | Formato `C%06d`. `NULL` si la reserva quedó huérfana. |
| 9 | `customer_name` | `STRING` | Nombre del cliente. |
| 10 | `customer_country` | `STRING` | País del cliente (viene de `customers.country`). |
| 11 | `airline` | `STRING` | Aerolínea del vuelo asociado. **`NULL` cuando `product_type = 'hotel'`**, porque esa reserva no incluye vuelo. Presente en `flight` y en `package`. |
| 12 | `hotel_name` | `STRING` | Hotel asociado. **`NULL` cuando `product_type = 'flight'`**, porque esa reserva no incluye alojamiento. Presente en `hotel` y en `package`. |
| 13 | `total_amount` | `DOUBLE` | Monto total de la reserva. |
| 14 | `confirmed_revenue` | `DOUBLE` | `total_amount` si `status = 'confirmed'`, si no `0.0`. |
| 15 | `approved_paid_amount` | `DOUBLE` | Suma de los pagos con `payment_status = 'approved'`. `0.0` si no hay ninguno. |
| 16 | `payment_gap` | `DOUBLE` | `total_amount - approved_paid_amount`. |
| 17 | `payment_coverage_pct` | `DOUBLE` | `approved_paid_amount / total_amount * 100`, o `0.0` si `total_amount <= 0`. |
| 18 | `is_confirmed` | `BOOLEAN` | `status = 'confirmed'`. |
| 19 | `is_cancelled` | `BOOLEAN` | `status = 'cancelled'`. |
| 20 | `last_payment_method` | `STRING` | Método del pago aprobado (`MAX(payment_method)` sobre los aprobados). **`NULL` si la reserva no tiene ningún pago aprobado** — no es el método del pago rechazado. |

### Regla crítica para la Fase 4

El SQL del Lab 2 calcula `approved_paid_amount` y `last_payment_method` dentro de un subquery filtrado
por `payment_status = 'approved'`. Al ingresar una reserva desde un PDF, que trae un solo pago:

```
approved_paid_amount = payment_amount  si payment_status = 'approved', si no 0.0
last_payment_method  = payment_method  si payment_status = 'approved', si no NULL
```

Copiar `payment_method` a ciegas rompería la semántica del campo.

---

## Dominios de valores

| Columna | Valores | Frecuencia en el dataset base |
|---|---|---|
| `status` | `confirmed` / `cancelled` / `pending` | 375.030 / 75.000 / 49.970 |
| `product_type` | `flight` / `hotel` / `package` | 200.289 / 149.964 / 149.747 |
| `payment_status` *(origen)* | `approved` / `rejected` / `pending` / `refunded` | 416.480 / 100.578 / 42.213 / 40.729 |
| `last_payment_method` | `credit_card` / `debit_card` / `wallet` / `bank_transfer` / `cash` / `NULL` | — |
| `destination_city` | 31 ciudades | de `Cancun` (23.192) a `Valparaiso` (7.068) |
| `booking_date` | rango | 2024-01-01 → 2026-12-31 |

Los 31 destinos: Cancun, Buenos Aires, Rio de Janeiro, Miami, Punta Cana, Bariloche, Cartagena, Cusco,
Madrid, Orlando, Barcelona, Florianopolis, Lima, Mendoza, Sao Paulo, Bogota, Aruba, Iguazu, New York,
Punta del Este, Mexico City, Santiago, Paris, San Andres, Montevideo, Rome, Ushuaia, Salta, Cordoba,
Asuncion, Valparaiso.

---

## Baseline

Calculado localmente replicando el SQL del job sobre los CSV de origen
(`../Laboratorio 2/src/teratrip_dataset/`), **antes** de correr el Glue Job. Sirve para verificar que
el job produjo lo esperado: si Athena devuelve otro número, el job hizo algo distinto.

> Las mutaciones del notebook del Lab 2 (`B999999`, `P999999`, 3 reservas pasadas a `cancelled`)
> **se omitieron** deliberadamente. El baseline sale del dataset regenerado limpio.

Estado: **validado**. El Glue Job corrió el 2026-08-24 en Glue 5.1 y las 8 queries de
`src/queries/00_athena_validation.sql` devolvieron exactamente estos valores en Athena.

| Métrica | Valor esperado | Confirmado |
|---|---|---|
| `COUNT(*)` | **500.000** | 500.000 |
| `booking_id` duplicados | 0 | 0 |
| `SUM(total_amount)` | 443.699.397,57 | 4.4369939757E8 |
| `SUM(confirmed_revenue)` | 332.786.794,83 | 3.3278679483E8 |
| `AVG(payment_coverage_pct)` | 83,2960 | 83.3 |
| `MIN(booking_date)` / `MAX(booking_date)` | 2024-01-01 / 2026-12-31 | ✅ |
| Reservas huérfanas (sin `customer`) | 0 | 0 |
| `airline` nulos | 149.964 (= reservas `hotel`) | 149964 |
| `hotel_name` nulos | 200.289 (= reservas `flight`) | 200289 |
| `last_payment_method` nulos | 108.232 (reservas sin ningún pago aprobado) | 108232 |

### Queries de validación en Athena

El set completo está en `src/queries/00_athena_validation.sql`. Resumen:

```sql
-- Conteo y duplicados
SELECT COUNT(*) AS filas, COUNT(DISTINCT booking_id) AS ids_unicos
FROM teratrip_db.booking_analytics;

-- Sanity checks financieros
SELECT SUM(total_amount)          AS total,
       SUM(confirmed_revenue)     AS revenue_confirmado,
       AVG(payment_coverage_pct)  AS cobertura_promedio,
       MIN(booking_date)          AS fecha_min,
       MAX(booking_date)          AS fecha_max
FROM teratrip_db.booking_analytics;

-- Distribución por status
SELECT status, COUNT(*) AS reservas
FROM teratrip_db.booking_analytics
GROUP BY status ORDER BY reservas DESC;
```

---

## Tipos en el Glue Data Catalog

Después de correr el Crawler, verificar que la tabla haya quedado con:

- `booking_date` → `date` (no `string`, no `timestamp`)
- `booking_year`, `booking_month` → `int`
- `is_confirmed`, `is_cancelled` → `boolean`
- `total_amount` y los cuatro campos monetarios derivados → `double`

Si alguno quedó como `string`, el agente va a generar SQL que falla al agregar.

---

## Esquema de origen (`raw/`)

Leído con esquema **explícito** en `src/glue_jobs/00_raw_to_curated.py`, nunca `inferSchema`: si el
sample no trae decimales, `total_amount` se tipa `int` y el parquet resultante diverge del que espera
el merge de la Fase 4.

```
bookings   booking_id STRING, customer_id STRING, booking_date STRING, destination_city STRING,
           product_type STRING, flight_id STRING, hotel_id STRING, status STRING, total_amount DOUBLE
payments   payment_id STRING, booking_id STRING, payment_method STRING, amount DOUBLE,
           payment_status STRING
customers  customer_id STRING, customer_name STRING, email STRING, country STRING, created_at STRING
flights    flight_id STRING, origin_city STRING, destination_city STRING, airline STRING, price DOUBLE
hotels     hotel_id STRING, hotel_name STRING, city STRING, stars INT, price_per_night DOUBLE
```

Ruta en S3: `raw/<tabla>/<tabla>.csv`.

`email`, `created_at`, `flight_id`, `hotel_id`, `origin_city`, `price`, `city`, `stars`,
`price_per_night` y `payment_id` se usan solo para los joins o se descartan: **no llegan a la tabla
sábana**.

---

## Mapeo del documento PDF (Fases 1 y 3)

Los 14 campos del documento y su destino. Es el contrato que implementa la Lambda de normalización.

| Campo en el PDF | Destino |
|---|---|
| `booking_id` | `booking_id` |
| `booking_date` | `booking_date` |
| `customer_id` | `customer_id` |
| `customer_name` | `customer_name` |
| `customer_country` | `customer_country` |
| `destination_city` | `destination_city` |
| `product_type` | `product_type` |
| `status` | `status` |
| `airline` | `airline` |
| `hotel_name` | `hotel_name` |
| `total_amount` | `total_amount` |
| `payment_amount` | insumo de `approved_paid_amount` |
| `payment_status` | insumo de `approved_paid_amount` y `last_payment_method` |
| `payment_method` | insumo de `last_payment_method` |

Los campos derivados (3, 4, 14–20) **no** los calcula la normalización: los calcula el Glue Job del
merge, para que exista una única fuente de verdad.

### Coherencia entre producto y proveedor

`airline` y `hotel_name` vienen del propio documento, y el merge les aplica la misma regla que rige en
el dataset base, verificada sin excepciones sobre sus 500.000 filas:

```
flight  -> airline,  hotel_name NULL
hotel   -> hotel_name, airline NULL
package -> AMBOS
```

La regla se impone **en el Glue Job**, no en la normalización: si el documento trajera una aerolínea en
una reserva de tipo `hotel`, el campo se anula igual. Es el mismo criterio que `last_payment_method` —
el merge es el único lugar donde viven las reglas de coherencia del esquema.

La Lambda de normalización sí registra un aviso en el log cuando el proveedor esperado falta, pero **no
descarta la fila**: `airline` y `hotel_name` no alimentan ningún campo derivado, y perder una reserva
por un nombre de hotel ausente sería peor que dejarlo en `NULL`.
