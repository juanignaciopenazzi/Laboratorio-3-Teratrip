"""
Laboratorio 3 - Fase 0: Bootstrap del dataset base (raw/ -> curated/)

Regenera la tabla sabana del Laboratorio 2 leyendo los CSV originales desde
s3://<bucket>/raw/<tabla>/<tabla>.csv y aplicando la MISMA transformacion SQL
que Lab2/src/glue_jobs/02_landing_to_curated.py.

Fusiona lo que antes hacian los Jobs 01 (RDS -> landing) y 02 (landing -> curated):
los recursos operacionales (RDS, Bastion, VPC) fueron eliminados y no se
reconstruyen, porque solo servian para simular una fuente operacional.

Este job es DESCARTABLE despues de la Fase 0: no forma parte de la arquitectura
del Lab 3 ni se muestra en el walkthrough. Igual se incluye en TEARDOWN.md.

Parametros del job:
    --JOB_NAME
    --S3_BUCKET_NAME

Configuracion recomendada: Glue 4.0, 2 workers G.1X, SIN VPC connection
(el job solo habla con S3).
"""

import sys

import boto3
from awsglue.context import GlueContext
from awsglue.job import Job
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from pyspark.sql.types import (
    DoubleType,
    IntegerType,
    StringType,
    StructField,
    StructType,
)

# ---------------------------------------------------------------------------
# 1. Inicializacion
# ---------------------------------------------------------------------------
args = getResolvedOptions(sys.argv, ["JOB_NAME", "S3_BUCKET_NAME"])
sc = SparkContext()
glueContext = GlueContext(sc)
spark = glueContext.spark_session
job = Job(glueContext)
job.init(args["JOB_NAME"], args)

bucket_name = args["S3_BUCKET_NAME"]

# ---------------------------------------------------------------------------
# 2. Esquemas explicitos
# ---------------------------------------------------------------------------
# NO se usa inferSchema. Con inferencia, total_amount podria tiparse como int si
# las primeras filas del sample no traen decimales, o stars como string si hay un
# vacio. El Parquet resultante quedaria con un esquema distinto al que espera el
# Glue Job de merge de la Fase 4 y el union fallaria -- o peor, castearia en
# silencio. El esquema de origen es conocido y estable, asi que se declara.
CORRUPT_COL = "_corrupt_record"

SCHEMAS = {
    "customers": StructType(
        [
            StructField("customer_id", StringType(), True),
            StructField("customer_name", StringType(), True),
            StructField("email", StringType(), True),
            StructField("country", StringType(), True),
            StructField("created_at", StringType(), True),
            StructField(CORRUPT_COL, StringType(), True),
        ]
    ),
    "flights": StructType(
        [
            StructField("flight_id", StringType(), True),
            StructField("origin_city", StringType(), True),
            StructField("destination_city", StringType(), True),
            StructField("airline", StringType(), True),
            StructField("price", DoubleType(), True),
            StructField(CORRUPT_COL, StringType(), True),
        ]
    ),
    "hotels": StructType(
        [
            StructField("hotel_id", StringType(), True),
            StructField("hotel_name", StringType(), True),
            StructField("city", StringType(), True),
            StructField("stars", IntegerType(), True),
            StructField("price_per_night", DoubleType(), True),
            StructField(CORRUPT_COL, StringType(), True),
        ]
    ),
    "bookings": StructType(
        [
            StructField("booking_id", StringType(), True),
            StructField("customer_id", StringType(), True),
            StructField("booking_date", StringType(), True),
            StructField("destination_city", StringType(), True),
            StructField("product_type", StringType(), True),
            StructField("flight_id", StringType(), True),
            StructField("hotel_id", StringType(), True),
            StructField("status", StringType(), True),
            StructField("total_amount", DoubleType(), True),
            StructField(CORRUPT_COL, StringType(), True),
        ]
    ),
    "payments": StructType(
        [
            StructField("payment_id", StringType(), True),
            StructField("booking_id", StringType(), True),
            StructField("payment_method", StringType(), True),
            StructField("amount", DoubleType(), True),
            StructField("payment_status", StringType(), True),
            StructField(CORRUPT_COL, StringType(), True),
        ]
    ),
}

PRIMARY_KEYS = {
    "customers": "customer_id",
    "flights": "flight_id",
    "hotels": "hotel_id",
    "bookings": "booking_id",
    "payments": "payment_id",
}


def read_entity(table):
    """Lee un CSV de raw/ con esquema explicito, deduplica por PK y filtra nulos."""
    path = "s3://{}/raw/{}/".format(bucket_name, table)
    print("[{}] leyendo {}".format(table, path))

    df = (
        spark.read.option("header", "true")
        .option("mode", "PERMISSIVE")
        .option("columnNameOfCorruptRecord", CORRUPT_COL)
        .schema(SCHEMAS[table])
        .csv(path)
    )

    corrupt_count = df.filter("{} IS NOT NULL".format(CORRUPT_COL)).count()
    if corrupt_count > 0:
        print("[{}] ADVERTENCIA: {} filas corruptas".format(table, corrupt_count))
        df.filter("{} IS NOT NULL".format(CORRUPT_COL)).select(CORRUPT_COL).show(5, False)
    df = df.drop(CORRUPT_COL)

    pk = PRIMARY_KEYS[table]
    raw_count = df.count()
    df = df.dropDuplicates([pk]).filter("{} IS NOT NULL".format(pk))
    clean_count = df.count()
    print(
        "[{}] filas: {} -> {} (dedup por {} + no nulos)".format(
            table, raw_count, clean_count, pk
        )
    )

    return df


# ---------------------------------------------------------------------------
# 3. Lectura y limpieza
# ---------------------------------------------------------------------------
print("Leyendo los CSV desde la zona raw/...")

df_customers = read_entity("customers")
df_flights = read_entity("flights")
df_hotels = read_entity("hotels")
df_bookings = read_entity("bookings")
df_payments = read_entity("payments")

df_customers.createOrReplaceTempView("customers")
df_flights.createOrReplaceTempView("flights")
df_hotels.createOrReplaceTempView("hotels")
df_bookings.createOrReplaceTempView("bookings")
df_payments.createOrReplaceTempView("payments")

# Leyendo los CSV directo no existen las FKs que en el Lab 2 garantizaba
# PostgreSQL. Se loguean los huerfanos: es un dato para el informe, no un error.
orphans = spark.sql(
    """
    SELECT
        SUM(CASE WHEN c.customer_id IS NULL THEN 1 ELSE 0 END) AS sin_customer,
        SUM(CASE WHEN b.flight_id IS NOT NULL AND b.flight_id <> ''
                  AND f.flight_id IS NULL THEN 1 ELSE 0 END) AS sin_flight,
        SUM(CASE WHEN b.hotel_id IS NOT NULL AND b.hotel_id <> ''
                  AND h.hotel_id IS NULL THEN 1 ELSE 0 END) AS sin_hotel
    FROM bookings b
    LEFT JOIN customers c ON b.customer_id = c.customer_id
    LEFT JOIN flights   f ON b.flight_id   = f.flight_id
    LEFT JOIN hotels    h ON b.hotel_id    = h.hotel_id
"""
).collect()[0]
print(
    "Integridad referencial -- bookings huerfanos: "
    "sin_customer={}, sin_flight={}, sin_hotel={}".format(
        orphans["sin_customer"], orphans["sin_flight"], orphans["sin_hotel"]
    )
)

# ---------------------------------------------------------------------------
# 4. Transformacion SQL -- IDENTICA a Lab2/src/glue_jobs/02_landing_to_curated.py
# ---------------------------------------------------------------------------
# No modificar. Este SQL es el contrato del esquema: es lo que garantiza que un
# registro que despues entre por Textract sea indistinguible de uno del dataset
# base. Cualquier cambio aca hay que replicarlo en merge_curated.py (Fase 4).
print("Ejecutando transformacion (Calculo del Diccionario de Datos)...")

query = """
    SELECT
        b.booking_id,
        CAST(b.booking_date AS DATE) AS booking_date,
        YEAR(CAST(b.booking_date AS DATE)) AS booking_year,
        MONTH(CAST(b.booking_date AS DATE)) AS booking_month,
        b.destination_city,
        b.product_type,
        b.status,
        c.customer_id,
        c.customer_name,
        c.country AS customer_country,
        f.airline,
        h.hotel_name,
        CAST(b.total_amount AS DOUBLE) AS total_amount,

        -- confirmed_revenue
        CASE
            WHEN b.status = 'confirmed' THEN CAST(b.total_amount AS DOUBLE)
            ELSE 0.0
        END AS confirmed_revenue,

        -- approved_paid_amount
        COALESCE(p.approved_paid_amount, 0.0) AS approved_paid_amount,

        -- payment_gap
        (CAST(b.total_amount AS DOUBLE) - COALESCE(p.approved_paid_amount, 0.0)) AS payment_gap,

        -- payment_coverage_pct
        CASE
            WHEN CAST(b.total_amount AS DOUBLE) > 0 THEN (COALESCE(p.approved_paid_amount, 0.0) / CAST(b.total_amount AS DOUBLE)) * 100.0
            ELSE 0.0
        END AS payment_coverage_pct,

        -- is_confirmed e is_cancelled
        CASE WHEN b.status = 'confirmed' THEN true ELSE false END AS is_confirmed,
        CASE WHEN b.status = 'cancelled' THEN true ELSE false END AS is_cancelled,

        -- last_payment_method
        p.last_payment_method

    FROM bookings b
    LEFT JOIN customers c ON b.customer_id = c.customer_id
    LEFT JOIN flights f ON b.flight_id = f.flight_id
    LEFT JOIN hotels h ON b.hotel_id = h.hotel_id
    LEFT JOIN (
        -- Subquery para agregar pagos antes del JOIN
        SELECT
            booking_id,
            SUM(CAST(amount AS DOUBLE)) AS approved_paid_amount,
            MAX(payment_method) AS last_payment_method
        FROM payments
        WHERE payment_status = 'approved'
        GROUP BY booking_id
    ) p ON b.booking_id = p.booking_id
"""

df_curated = spark.sql(query)

# ---------------------------------------------------------------------------
# 5. Escritura: un unico archivo Parquet
# ---------------------------------------------------------------------------
# coalesce(1) sobre 500k filas fuerza todo a un solo executor. Con este volumen
# es aceptable (el Parquet final ronda decenas de MB) y es lo que permite el
# read-modify-write de un archivo unico en la Fase 4.
print("Guardando datos en S3...")

temp_prefix = "curated/temp_booking_analytics/"
temp_s3_path = "s3://{}/{}".format(bucket_name, temp_prefix)
final_key = "curated/booking_analytics/teratrip_booking_analytics.parquet"

df_curated.coalesce(1).write.mode("overwrite").parquet(temp_s3_path)

s3_client = boto3.client("s3")
response = s3_client.list_objects_v2(Bucket=bucket_name, Prefix=temp_prefix)

for obj in response.get("Contents", []):
    key = obj["Key"]
    if key.endswith(".parquet"):
        s3_client.copy_object(
            CopySource={"Bucket": bucket_name, "Key": key},
            Bucket=bucket_name,
            Key=final_key,
        )
        break

for obj in response.get("Contents", []):
    s3_client.delete_object(Bucket=bucket_name, Key=obj["Key"])

print("Archivo exacto creado exitosamente: s3://{}/{}".format(bucket_name, final_key))

# ---------------------------------------------------------------------------
# 6. Baseline
# ---------------------------------------------------------------------------
total_rows = df_curated.count()
print("=" * 70)
print("BASELINE - filas en la tabla sabana: {}".format(total_rows))
print("Registrar este numero en docs/schema_contract.md")
print("=" * 70)

job.commit()
print("Job completado con exito.")
