# Diccionario de datos — tabla `booking_analytics`

## Identificación de la tabla

La única tabla consultable es **`booking_analytics`**, en la database **`teratrip_db`** del catálogo de
Amazon Athena. El nombre completamente calificado es **`teratrip_db.booking_analytics`**.

Ambas formas son válidas en una consulta:

```sql
SELECT COUNT(*) FROM booking_analytics;
SELECT COUNT(*) FROM teratrip_db.booking_analytics;
```

No existe ninguna otra tabla disponible. No hay tablas `customers`, `bookings`, `payments`, `flights`
ni `hotels`: toda la información está desnormalizada en `booking_analytics`. Cualquier consulta que
referencie otra tabla va a ser rechazada.

## Columnas de `booking_analytics`

La tabla tiene exactamente 20 columnas. Estos son sus nombres literales; no existe ninguna otra.

### Identificación y fecha

| Columna | Tipo | Significado |
|---|---|---|
| `booking_id` | `string` | Identificador único de la reserva. Es la clave primaria: hay exactamente una fila por `booking_id`. |
| `booking_date` | `date` | Fecha en que se realizó la reserva. |
| `booking_year` | `int` | Año de `booking_date`. Existe como columna propia para evitar tener que extraerlo con una función. |
| `booking_month` | `int` | Mes de `booking_date`, de 1 a 12. Es el número de mes, sin el año: `booking_month = 3` agrupa todos los marzos de todos los años. |

### Producto y estado

| Columna | Tipo | Significado |
|---|---|---|
| `destination_city` | `string` | Ciudad de destino del viaje. |
| `product_type` | `string` | Tipo de producto contratado. Valores posibles: `flight`, `hotel`, `package`. |
| `status` | `string` | Estado de la reserva. Valores posibles: `confirmed`, `cancelled`, `pending`. |

### Cliente

| Columna | Tipo | Significado |
|---|---|---|
| `customer_id` | `string` | Identificador del cliente. **Se repite entre filas**: un mismo cliente puede tener muchas reservas. |
| `customer_name` | `string` | Nombre del cliente. |
| `customer_country` | `string` | País del cliente. Es el país de residencia del cliente, **no** el país del destino del viaje. |

### Proveedor

| Columna | Tipo | Significado |
|---|---|---|
| `airline` | `string` | Aerolínea del vuelo asociado. Es `NULL` cuando la reserva no incluye vuelo. |
| `hotel_name` | `string` | Nombre del hotel asociado. Es `NULL` cuando la reserva no incluye hotel. |

### Montos y pagos

| Columna | Tipo | Significado |
|---|---|---|
| `total_amount` | `double` | Monto total de la reserva, en dólares. Es lo que la reserva vale, independientemente de su estado y de si se cobró. |
| `confirmed_revenue` | `double` | Igual a `total_amount` si `status = 'confirmed'`; en cualquier otro caso vale `0.0`. Es la columna que hay que sumar para medir ingresos. |
| `approved_paid_amount` | `double` | Suma de los pagos **aprobados** de la reserva. Vale `0.0` si la reserva no tiene ningún pago aprobado. |
| `payment_gap` | `double` | `total_amount - approved_paid_amount`. Cuánto falta cobrar de esa reserva. Puede ser `0` si está totalmente cobrada. |
| `payment_coverage_pct` | `double` | Porcentaje del total que ya fue cobrado con pagos aprobados, de 0 a 100. Vale `0.0` si `total_amount` es `0` o negativo. |

### Banderas derivadas

| Columna | Tipo | Significado |
|---|---|---|
| `is_confirmed` | `boolean` | `true` si `status = 'confirmed'`. |
| `is_cancelled` | `boolean` | `true` si `status = 'cancelled'`. |
| `last_payment_method` | `string` | Método del pago aprobado de la reserva. Es `NULL` si la reserva no tiene ningún pago aprobado. Valores posibles: `credit_card`, `debit_card`, `wallet`, `bank_transfer`, `cash`. |

Una reserva `pending` tiene `is_confirmed = false` **y** `is_cancelled = false`. Las dos banderas no son
complementarias: son tres estados, no dos.

## Valores posibles de las columnas categóricas

Estos dominios son cerrados y estables:

- `status`: `confirmed`, `cancelled`, `pending`
- `product_type`: `flight`, `hotel`, `package`
- `last_payment_method`: `credit_card`, `debit_card`, `wallet`, `bank_transfer`, `cash`, o `NULL`

Todos los valores se guardan en **minúsculas**. Una comparación como `WHERE status = 'Confirmed'` no
devuelve nada.

`destination_city`, `customer_country`, `airline` y `hotel_name` son dominios abiertos: sus valores
cambian a medida que se ingresan reservas nuevas. Para conocerlos hay que consultarlos, por ejemplo con
`SELECT DISTINCT destination_city FROM booking_analytics`. Estos valores sí conservan mayúsculas
iniciales, por ejemplo `Cancun`, `Buenos Aires`, `Argentina`.

### Los valores de texto se guardan SIN tildes ni diacríticos

Esto es la causa más frecuente de que una consulta devuelva 0 filas siendo correcta. Las ciudades y los
países están almacenados sin acentos, aunque su ortografía correcta en español los lleve:

| Escrito así en la base | **No** buscar como |
|---|---|
| `Cancun` | `Cancún` |
| `Bogota` | `Bogotá` |
| `Sao Paulo` | `São Paulo` |
| `Cordoba` | `Córdoba` |
| `Asuncion` | `Asunción` |
| `Valparaiso` | `Valparaíso` |
| `Iguazu` | `Iguazú` |

Al filtrar por un valor de texto hay que usar la forma **sin diacríticos**, aunque el usuario haya
escrito la palabra con tilde en su pregunta. `WHERE destination_city = 'Cancún'` devuelve cero filas;
`WHERE destination_city = 'Cancun'` devuelve las correctas.

Una alternativa robusta cuando hay dudas sobre la ortografía exacta es filtrar de forma aproximada:

```sql
SELECT COUNT(*) FROM booking_analytics WHERE destination_city LIKE 'Canc%';
```

Y si un filtro por un valor de texto devuelve **0 filas**, no hay que concluir que no existen datos:
primero conviene verificar cómo está escrito realmente el valor.

```sql
SELECT DISTINCT destination_city FROM booking_analytics ORDER BY destination_city;
```

## Cuándo `airline` y `hotel_name` están vacíos

Que estos dos campos sean `NULL` **no significa que falte el dato**: significa que ese producto no
incluye ese servicio. La regla depende de `product_type` y se cumple en toda la tabla:

| `product_type` | `airline` | `hotel_name` |
|---|---|---|
| `flight` | tiene valor | `NULL` |
| `hotel` | `NULL` | tiene valor |
| `package` | tiene valor | tiene valor |

Un paquete combina vuelo y alojamiento, así que trae los dos. Un vuelo suelto no tiene hotel asociado, y
una reserva de alojamiento no tiene aerolínea.

La consecuencia al responder: un análisis agrupado por `airline` deja afuera las reservas de tipo
`hotel`, y uno por `hotel_name` deja afuera las de tipo `flight`. Eso es correcto y esperable, pero
conviene aclararlo, porque el total de esos agrupamientos no coincide con el total de reservas.

```sql
-- Reservas que incluyen vuelo (flight + package)
SELECT COUNT(*) FROM booking_analytics WHERE airline IS NOT NULL;
```

Esto vale para **todas** las reservas por igual, vengan del sistema operacional o de un documento
procesado automáticamente: ambas vías producen filas con la misma estructura y las mismas reglas.
