# Queries de CloudWatch Logs Insights — TeraTrip Lab 3

Consultas reutilizables para diagnosticar el pipeline. Las tres primeras están embebidas en el
dashboard `dash-teratrip-lab3`; el resto se corren a mano desde **CloudWatch → Logs Insights**.

Los log groups del laboratorio:

```
/aws/lambda/lambda-teratrip-textract-extract
/aws/lambda/lambda-teratrip-normalize
/aws/lambda/lambda-teratrip-athena-query
/aws-glue/jobs/output
/aws-glue/jobs/error
```

---

## 1. Errores en cualquier capa

El primer lugar donde mirar cuando una ejecución falla. Agrega las tres Lambdas y el Glue Job, así no
hay que adivinar en cuál rompió.

```
fields @timestamp, @log, @message
| filter @message like /(?i)(error|exception|traceback|rechazad|descartad|fallo)/
| sort @timestamp desc
| limit 50
```

El campo `@log` indica de qué log group vino cada línea, que es lo que convierte esta consulta en un
triage y no en una lista suelta.

---

## 2. Qué hizo el merge en cada corrida

```
fields @timestamp, @message
| filter @message like /(Tabla sabana|anti-join|A incorporar|Registros recibidos|Backup|Todos los booking_id)/
| sort @timestamp desc
| limit 60
```

Log group: `/aws-glue/jobs/output`.

Responde de un vistazo: cuántos registros llegaron, cuántos se descartaron por duplicados dentro del
lote, cuántos por el anti-join, y a cuántas filas quedó la tabla. Es la evidencia directa del criterio
*"los registros se incorporan sin duplicar reservas existentes"*.

---

## 3. Qué SQL generó el agente

```
fields @timestamp, @message
| filter @message like /(SQL ejecutado|RECHAZADO|QueryExecutionId)/
| sort @timestamp desc
| limit 60
```

Log group: `/aws/lambda/lambda-teratrip-athena-query`.

Sirve para dos cosas distintas: auditar las consultas que el modelo produce, y recuperar el
`QueryExecutionId` para contrastar contra Athena una respuesta sospechosa.

---

## 4. Seguir un `run_id` a través de las tres capas

Reemplazar `<RUN_ID>` por el eTag del documento. Es la consulta que reconstruye una ingesta completa.

```
fields @timestamp, @log, @message
| filter @message like /<RUN_ID>/
| sort @timestamp asc
```

Log groups: las tres Lambdas y `/aws-glue/jobs/output`.

Como el `run_id` deriva del eTag del objeto y se propaga por todo el pipeline, esta consulta muestra en
orden cronológico qué pasó con ese documento en cada paso.

---

## 5. Filas descartadas por la normalización

```
fields @timestamp, @message
| filter @message like /FILA DESCARTADA/
| sort @timestamp desc
| limit 50
```

Log group: `/aws/lambda/lambda-teratrip-normalize`.

Cada descarte sale con su motivo y sus valores crudos. Si un documento ingresó menos reservas de las
esperadas, esta consulta dice por qué antes de tener que abrir el `_rejected.json` en S3.

---

## 6. Consultas rechazadas por el guard de SQL

```
fields @timestamp, @message
| filter @message like /RECHAZADO/
| sort @timestamp desc
| limit 50
```

Log group: `/aws/lambda/lambda-teratrip-athena-query`.

Evidencia de la Prueba 3. Cada línea trae el motivo exacto del rechazo.

---

## 7. Costo de Athena por consulta

```
fields @timestamp, @message
| filter @message like /bytes escaneados/
| parse @message "bytes escaneados: *" as bytes
| sort @timestamp desc
| limit 50
```

Log group: `/aws/lambda/lambda-teratrip-athena-query`.

Athena se factura por bytes escaneados. Una consulta del agente sobre la tabla completa ronda los 2,4 MB
por columna leída; si alguna aparece muy por encima, conviene mirar qué SQL la produjo.

---

## 8. Cold starts de las Lambdas

```
filter @type = "REPORT"
| fields @timestamp, @log, @duration, @initDuration, @maxMemoryUsed / 1000000 as memoriaMB
| filter ispresent(@initDuration)
| sort @timestamp desc
| limit 30
```

Log groups: las tres Lambdas.

Útil antes del walkthrough: correr el pipeline una vez para calentar los contenedores, y saber cuánta
memoria usan realmente frente a la asignada.
