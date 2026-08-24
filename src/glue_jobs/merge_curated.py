"""
Glue Job - Merge incremental a la tabla sabana
Laboratorio 3 - TeraTrip

Incorpora los registros normalizados de incoming-data/<run_id>/ a la tabla
sabana, sin duplicar reservas existentes y generando los campos derivados.

Por que Glue y no Lambda (la consigna pide fundamentar la eleccion):
reutiliza el SQL de campos derivados ya validado en el Lab 2, de modo que un
registro que entra por Textract produce exactamente los mismos campos que uno
que vino del dataset base. Con Lambda habria que reimplementar esa logica en
Python y se abriria un riesgo permanente de divergencia de esquema entre las
dos vias de ingreso. El costo es el arranque del job (1-2 min); a cambio, la
solucion no se rediseña cuando el volumen crezca.

Parametros del Job:
    --JOB_NAME          (lo inyecta Glue)
    --S3_BUCKET_NAME    teratrip-data-lake-955030484229
    --INCOMING_PREFIX   incoming-data/<run_id>/   (lo pasa la Step Function)

Config: Glue 5.1, 2 workers G.1X. Sin VPC connection.
"""

import sys
from datetime import datetime, timezone

import boto3
from awsglue.context import GlueContext
from awsglue.job import Job
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from pyspark.sql.types import StringType, StructField, StructType, DoubleType

# --- Inicializacion --------------------------------------------------------------

args = getResolvedOptions(sys.argv, ["JOB_NAME", "S3_BUCKET_NAME", "INCOMING_PREFIX"])
sc = SparkContext()
glueContext = GlueContext(sc)
spark = glueContext.spark_session
job = Job(glueContext)
job.init(args["JOB_NAME"], args)

bucket = args["S3_BUCKET_NAME"]
incoming_prefix = args["INCOMING_PREFIX"].strip("/") + "/"

s3 = boto3.client("s3")

CURATED_DIR = "curated/booking_analytics/"
FINAL_KEY = CURATED_DIR + "teratrip_booking_analytics.parquet"
TEMP_PREFIX = "curated/temp_merge/"
BACKUP_DIR = "curated/_backup/"

# Orden canonico de docs/schema_contract.md. El union se hace con un select
# explicito sobre esta lista en AMBOS lados: un union posicional a ciegas
# alinearia columnas distintas si alguna vez cambia el orden de una de las dos
# fuentes, y el error seria silencioso (los tipos coinciden).
COLUMNAS = [
    "booking_id", "booking_date", "booking_year", "booking_month",
    "destination_city", "product_type", "status",
    "customer_id", "customer_name", "customer_country",
    "airline", "hotel_name",
    "total_amount", "confirmed_revenue", "approved_paid_amount",
    "payment_gap", "payment_coverage_pct",
    "is_confirmed", "is_cancelled", "last_payment_method",
]

# Esquema explicito del JSON Lines que escribe la Lambda de normalizacion.
# Mismo criterio que en el bootstrap: con inferSchema, un documento donde todos
# los montos vinieran sin decimales tiparia total_amount como bigint y el union
# fallaria contra el double del parquet curado.
SCHEMA_NUEVOS = StructType([
    StructField("booking_id", StringType(), True),
    StructField("booking_date", StringType(), True),
    StructField("customer_id", StringType(), True),
    StructField("customer_name", StringType(), True),
    StructField("customer_country", StringType(), True),
    StructField("destination_city", StringType(), True),
    StructField("product_type", StringType(), True),
    StructField("status", StringType(), True),
    StructField("total_amount", DoubleType(), True),
    StructField("payment_amount", DoubleType(), True),
    StructField("payment_status", StringType(), True),
    StructField("payment_method", StringType(), True),
])

# Campos derivados. Replica el SQL del Lab 2 con las dos adaptaciones que impone
# el ingreso por documento, ambas documentadas en docs/schema_contract.md:
#
#   1. airline y hotel_name van NULL: el PDF no trae flight_id ni hotel_id, asi
#      que no hay contra que joinear. Limitacion conocida, declarada en la KB.
#   2. El documento trae UN pago por reserva, no la tabla payments. En el Lab 2
#      approved_paid_amount y last_payment_method salen de un subquery filtrado
#      por payment_status = 'approved'; aca esa condicion se aplica fila a fila.
#      last_payment_method queda NULL si el pago no esta aprobado -- NO es el
#      metodo del pago rechazado.
SQL_DERIVADOS = """
    WITH base AS (
        SELECT
            booking_id,
            CAST(booking_date AS DATE)                AS fecha,
            destination_city,
            product_type,
            status,
            customer_id,
            customer_name,
            customer_country,
            CAST(total_amount AS DOUBLE)              AS total,
            payment_status,
            payment_method,
            CASE WHEN payment_status = 'approved'
                 THEN CAST(payment_amount AS DOUBLE)
                 ELSE 0.0 END                        AS aprobado
        FROM nuevos
    )
    SELECT
        booking_id,
        fecha                                        AS booking_date,
        YEAR(fecha)                                  AS booking_year,
        MONTH(fecha)                                 AS booking_month,
        destination_city,
        product_type,
        status,
        customer_id,
        customer_name,
        customer_country,
        CAST(NULL AS STRING)                         AS airline,
        CAST(NULL AS STRING)                         AS hotel_name,
        total                                        AS total_amount,
        CASE WHEN status = 'confirmed' THEN total ELSE 0.0 END AS confirmed_revenue,
        aprobado                                     AS approved_paid_amount,
        (total - aprobado)                           AS payment_gap,
        CASE WHEN total > 0 THEN (aprobado / total) * 100.0 ELSE 0.0 END AS payment_coverage_pct,
        CASE WHEN status = 'confirmed' THEN true ELSE false END AS is_confirmed,
        CASE WHEN status = 'cancelled' THEN true ELSE false END AS is_cancelled,
        CASE WHEN payment_status = 'approved' THEN payment_method ELSE NULL END AS last_payment_method
    FROM base
"""


def terminar(mensaje):
    """Sale con exito sin reescribir nada. Reescribir la tabla entera para dejarla
    igual seria trabajo y riesgo gratuitos."""
    print(mensaje)
    job.commit()
    sys.exit(0)


# --- 1. Verificar que exista la tabla actual --------------------------------------

existe = s3.list_objects_v2(Bucket=bucket, Prefix=FINAL_KEY, MaxKeys=1).get("KeyCount", 0)
if not existe:
    # Falla ruidosa a proposito. Si la tabla curada no esta, algo se borro o el
    # bootstrap no corrio: escribir aca dejaria una tabla sabana de 8 filas
    # pisando el dataset de 500.000, y el fallo recien se veria en Athena.
    raise RuntimeError(
        "No existe s3://" + bucket + "/" + FINAL_KEY + ". "
        "Correr primero el Job de bootstrap (Fase 0). El merge no crea la tabla."
    )

# --- 2. Backup antes de tocar nada ------------------------------------------------

timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
backup_key = BACKUP_DIR + timestamp + "/teratrip_booking_analytics.parquet"
s3.copy_object(
    CopySource={"Bucket": bucket, "Key": FINAL_KEY},
    Bucket=bucket,
    Key=backup_key,
)
print("Backup: s3://" + bucket + "/" + backup_key)

# --- 3. Leer ambos lados ----------------------------------------------------------

df_current = spark.read.parquet("s3://" + bucket + "/" + CURATED_DIR)
total_antes = df_current.count()
print("Tabla sabana actual: " + str(total_antes) + " filas")

ruta_nuevos = "s3://" + bucket + "/" + incoming_prefix
print("Leyendo registros nuevos desde " + ruta_nuevos)
# Spark ignora los archivos que empiezan con _ o . al leer un directorio, asi
# que _rejected.json no entra como dato.
df_new = spark.read.schema(SCHEMA_NUEVOS).json(ruta_nuevos)

recibidos = df_new.count()
print("Registros recibidos: " + str(recibidos))
if recibidos == 0:
    terminar("No hay registros nuevos. La tabla queda intacta.")

# --- 4. Deduplicar dentro del batch -----------------------------------------------

df_new = df_new.dropDuplicates(["booking_id"])
tras_dedup = df_new.count()
if tras_dedup < recibidos:
    print("Duplicados dentro del documento descartados: " + str(recibidos - tras_dedup))

# --- 5. Anti-join: descartar los booking_id que ya existen ------------------------

df_inedito = df_new.join(df_current.select("booking_id"), on="booking_id", how="left_anti")
inedito = df_inedito.count()
print("Ya existian (descartados por anti-join): " + str(tras_dedup - inedito))
print("A incorporar: " + str(inedito))

if inedito == 0:
    # Este es el camino que recorre la prueba de idempotencia: subir dos veces el
    # mismo documento no cambia el conteo.
    terminar("Todos los booking_id ya estaban en la tabla. Nada que incorporar.")

# --- 6. Campos derivados ----------------------------------------------------------

df_inedito.createOrReplaceTempView("nuevos")
df_derivado = spark.sql(SQL_DERIVADOS)

# --- 7. Union con select explicito en ambos lados ---------------------------------

df_final = df_current.select(*COLUMNAS).unionByName(df_derivado.select(*COLUMNAS)).cache()
total_despues = df_final.count()
print("Tabla sabana resultante: " + str(total_antes) + " -> " + str(total_despues))

if total_despues != total_antes + inedito:
    raise RuntimeError(
        "Conteo inconsistente: esperaba " + str(total_antes + inedito) +
        " y obtuve " + str(total_despues) + ". No se reescribe la tabla."
    )

# --- 8. Escribir a temporal y renombrar al nombre final ---------------------------

temp_path = "s3://" + bucket + "/" + TEMP_PREFIX
df_final.coalesce(1).write.mode("overwrite").parquet(temp_path)

resp = s3.list_objects_v2(Bucket=bucket, Prefix=TEMP_PREFIX)
escrito = False
for obj in resp.get("Contents", []):
    if obj["Key"].endswith(".parquet"):
        s3.copy_object(
            CopySource={"Bucket": bucket, "Key": obj["Key"]},
            Bucket=bucket,
            Key=FINAL_KEY,
        )
        escrito = True
        break

if not escrito:
    raise RuntimeError(
        "Spark no dejo ningun .parquet en " + TEMP_PREFIX +
        ". La tabla NO fue modificada; el backup esta en " + backup_key
    )

for obj in resp.get("Contents", []):
    s3.delete_object(Bucket=bucket, Key=obj["Key"])

print("Tabla sabana actualizada: s3://" + bucket + "/" + FINAL_KEY)
print("Filas: " + str(total_despues) + " (+" + str(inedito) + ")")

job.commit()
print("Job completado con exito.")
