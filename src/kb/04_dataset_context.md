# Contexto del dataset analítico de TeraTrip

## Qué es `booking_analytics`

`booking_analytics` es la **tabla sábana** analítica de TeraTrip: una única tabla desnormalizada que
concentra toda la información de reservas del negocio. Se construyó combinando cinco entidades del
sistema operacional —reservas, clientes, pagos, vuelos y hoteles— en una sola fila por reserva, y
agregando columnas derivadas ya calculadas.

Existe para que las preguntas de negocio se respondan con una sola consulta, sin JOINs. Todo lo que se
puede preguntar sobre la operación de TeraTrip se responde consultando únicamente esta tabla.

## Dónde vive

| | |
|---|---|
| Motor de consulta | Amazon Athena (SQL de Trino) |
| Database | `teratrip_db` |
| Tabla | `booking_analytics` |
| Nombre calificado | `teratrip_db.booking_analytics` |
| Formato físico | Parquet sobre Amazon S3 |

Es la única tabla disponible. No hay tablas separadas de clientes, pagos, vuelos ni hoteles.

## Granularidad

**Una fila por reserva**, identificada por `booking_id`. No hay duplicados ni versiones históricas: cada
`booking_id` aparece exactamente una vez.

Un cliente puede aparecer en muchas filas, una por cada reserva que hizo. Por eso contar filas cuenta
reservas, no clientes.

## Cobertura temporal

La columna `booking_date` es la fecha de la reserva. El dataset cubre varios años de operación, e
incorpora reservas nuevas de forma continua a medida que se procesan documentos de alta.

Para conocer el rango real de fechas en un momento dado hay que consultarlo, no asumirlo:

```sql
SELECT MIN(booking_date), MAX(booking_date) FROM teratrip_db.booking_analytics;
```

Lo mismo aplica a la cantidad total de reservas: es un número que cambia. Si una pregunta lo requiere,
hay que consultarlo con `COUNT(*)`.

## Cómo entran datos nuevos

El dataset se alimenta por dos vías, y ambas producen filas con la misma estructura y los mismos campos
derivados calculados con idéntica lógica:

1. **Carga desde el sistema operacional.** Es el origen de la mayor parte de las reservas. Trae la
   información completa, incluidos la aerolínea y el hotel.

2. **Ingesta automática desde documentos.** Cuando llega un documento PDF con reservas nuevas, se
   procesa automáticamente y sus filas se incorporan a `booking_analytics` sin duplicar reservas
   existentes. Estas filas tienen `airline` y `hotel_name` en `NULL`, porque el documento no identifica
   el vuelo ni el hotel.

La consecuencia práctica de la segunda vía: **los datos de la tabla cambian con el tiempo**. Un conteo
de reservas de un destino puede ser distinto hoy que ayer. Nunca hay que responder con una cifra
recordada; hay que consultarla.

## Casos de uso típicos

- **Análisis de ingresos**: revenue confirmado por destino, por tipo de producto, por período.
- **Salud comercial**: tasa de cancelación, comparación entre reservas confirmadas, canceladas y
  pendientes.
- **Cobranzas**: cobertura de pagos, monto pendiente de cobro, métodos de pago más usados.
- **Base de clientes**: distribución de clientes por país, cantidad de reservas por cliente.
- **Mix de producto**: peso relativo de vuelos, hoteles y paquetes.

## Qué NO está en esta tabla

Conviene tenerlo presente para no intentar responder algo que el dataset no puede contestar:

- **Pagos individuales.** Los pagos vienen agregados por reserva. No se puede saber cuántos pagos tuvo
  una reserva ni las fechas de cada uno.
- **Fecha del viaje.** `booking_date` es cuándo se reservó, no cuándo se viaja.
- **Ciudad de origen del viaje.** Solo está el destino.
- **Precio unitario del vuelo o del hotel.** Solo está el `total_amount` de la reserva.
- **Datos de contacto del cliente.** No hay email ni teléfono.
- **Historial de cambios de estado.** Se ve el estado actual, no cuándo ni cuántas veces cambió.

Si una pregunta requiere alguno de estos datos, la respuesta correcta es explicar que el dataset no los
contiene, no aproximar con otra columna.
