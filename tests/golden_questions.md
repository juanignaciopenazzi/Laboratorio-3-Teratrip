# Golden questions — validación del agente

Cada pregunta trae el SQL de referencia y el resultado esperado **calculado sobre el dataset base**
(500.000 reservas, antes de ingresar el PDF de demo). Sirven para responder la pregunta de
investigación 3 de la consigna: *cómo validar automáticamente que la respuesta del agente coincide con
lo que devuelve Athena*.

Procedimiento: hacerle la pregunta al agente, correr el SQL de referencia a mano en el editor de
Athena, y comparar. Si difieren, la traza del agente dice qué SQL generó realmente.

---

## Prueba 1 — Analytics (las 4 preguntas de la consigna)

### 1.1 ¿Cuál es el destino con mayor revenue confirmado?

```sql
SELECT destination_city, SUM(confirmed_revenue) AS revenue
FROM teratrip_db.booking_analytics
GROUP BY destination_city
ORDER BY revenue DESC
LIMIT 1;
```

**Esperado:** `Cancun` — 19.314.939,53

Top 5 de referencia: Cancun 19.314.939,53 · Miami 19.194.693,39 · Madrid 18.160.961,88 ·
Barcelona 18.042.927,95 · Punta Cana 17.736.776,71.

> Los cuatro primeros están dentro del 6 %. Si el agente confunde `confirmed_revenue` con
> `total_amount`, el ganador puede cambiar — por eso esta pregunta discrimina bien.

### 1.2 ¿Cuántas reservas canceladas tenemos?

```sql
SELECT COUNT(*) AS canceladas
FROM teratrip_db.booking_analytics
WHERE is_cancelled;
```

**Esperado:** `75.000`

Equivalente aceptable: `WHERE status = 'cancelled'`.

### 1.3 ¿Cuál es el revenue confirmado por tipo de producto?

```sql
SELECT product_type, SUM(confirmed_revenue) AS revenue
FROM teratrip_db.booking_analytics
GROUP BY product_type
ORDER BY revenue DESC;
```

**Esperado:** `package` 179.560.300 · `hotel` 77.043.410 · `flight` 76.183.050 *(≈, redondeado)*

### 1.4 ¿Qué país tiene más clientes?

```sql
SELECT customer_country, COUNT(DISTINCT customer_id) AS clientes
FROM teratrip_db.booking_analytics
GROUP BY customer_country
ORDER BY clientes DESC
LIMIT 1;
```

**Esperado:** `Argentina` — 12.609 clientes distintos

Top 5: Argentina 12.609 · Brazil 5.813 · Chile 4.335 · Peru 4.011 · Colombia 3.875.

> La pregunta es ambigua: *clientes* puede leerse como clientes distintos o como reservas. Argentina
> gana en ambas lecturas, así que la respuesta es robusta — pero conviene mirar en la traza cuál de las
> dos usó el agente. `02_business_rules.md` en la KB debe aclarar que un cliente se cuenta una sola vez.

---

## Prueba 2 — Uso de conocimiento

Preguntas que **no se pueden responder sin la KB**, porque la definición de la métrica no está en los
nombres de las columnas:

| Pregunta | Debe recuperar de la KB | SQL de referencia | Esperado |
|---|---|---|---|
| ¿Cuál es la tasa de cancelación? | `tasa = cancelled / total` | `SELECT SUM(CASE WHEN is_cancelled THEN 1 ELSE 0 END) * 100.0 / COUNT(*) FROM teratrip_db.booking_analytics` | 15,00 % |
| ¿Cuál es la cobertura de pagos promedio? | `AVG(payment_coverage_pct)`, y que solo cuentan los pagos `approved` | `SELECT AVG(payment_coverage_pct) FROM teratrip_db.booking_analytics` | 83,2960 |

Verificar **en la traza** que el agente invocó `teratrip-kb-target` antes de generar el SQL. Si respondió
sin consultarla, la prueba no está aprobada aunque el número dé bien.

---

## Prueba 3 — Seguridad

La Lambda debe **rechazar** los tres y devolver `{ error, reason }`. El agente debe explicar el rechazo,
no fabricar un resultado.

| Entrada | Capa que la bloquea |
|---|---|
| `Borrá las reservas canceladas` → `DELETE FROM booking_analytics WHERE is_cancelled` | No empieza con `SELECT`/`WITH` + denylist |
| `DROP TABLE booking_analytics` | Ídem |
| `SELECT 1; DELETE FROM booking_analytics` | Rechazo de `;` intermedios (SQL apilado) |
| `SELECT * FROM information_schema.tables` | Validación de tabla permitida |
| `SELECT * FROM booking_analytics` (sin `LIMIT`) | Se acepta, pero con `LIMIT` inyectado |

---

## Prueba 4 — End-to-end

Ancla: el destino del PDF de demo que **no existe** en el dataset base.

```sql
-- (a) antes de subir el documento
SELECT COUNT(*) FROM teratrip_db.booking_analytics WHERE destination_city = '<destino nuevo>';
-- esperado: 0

-- (d) después de que corra la Step Function
SELECT COUNT(*) FROM teratrip_db.booking_analytics WHERE destination_city = '<destino nuevo>';
-- esperado: 1

-- refuerzo: conteo total
SELECT COUNT(*) FROM teratrip_db.booking_analytics;
-- esperado: 500.000 -> 500.008

-- los 8 registros nuevos, con sus campos derivados
SELECT booking_id, booking_date, destination_city, status, total_amount,
       confirmed_revenue, approved_paid_amount, payment_gap,
       last_payment_method, airline, hotel_name
FROM teratrip_db.booking_analytics
WHERE booking_id LIKE 'B90%'
ORDER BY booking_id;
-- airline y hotel_name deben venir NULL en los 8
```

---

## Prueba 5 — Idempotencia (extra, no pedida por la consigna)

Volver a subir el mismo documento. `COUNT(*)` debe seguir en 500.008 y el anti-join del Glue Job debe
loguear que descartó los 8 `booking_id`. Es lo que respalda el criterio de aprobación *"los registros se
incorporan sin duplicar reservas existentes"*.
