"""
Glue Job — Bootstrap del dataset base (raw/ -> curated/)
Laboratorio 3 - TeraTrip

Regenera la tabla sabana del Lab 2 leyendo los 5 CSV crudos desde raw/ y aplicando
EXACTAMENTE la misma transformacion SQL del Lab 2 (02_landing_to_curated.py). Fusiona
lo que antes hacian los Jobs 01 (RDS->landing) y 02 (landing->curated) en un solo paso,
sin RDS ni VPC: solo habla con S3.

Este job es DESCARTABLE despues de la Fase 0. No forma parte de la arquitectura del Lab 3;
solo reconstruye el punto de partida que la consigna daba por existente.

Parametros del Job (en la consola de Glue, seccion "Job parameters"):
    --JOB_NAME          (lo inyecta Glue)
    --S3_BUCKET_NAME    teratrip-data-lake-955030484229

Config: Glue 4.0, 2 workers G.1X. Sin VPC connection.
"""

import sys
import boto3
from pyspark.context import SparkContext
from awsglue.context import GlueContext
from awsglue.job import Job
from awsglue.utils import getResolvedOptions
from pyspark.sql.types import (
    StructType, StructField, StringType, IntegerType, DoubleType,
)

# 1. Inicializacion
args = getResolvedOptions(sys.argv, ["JOB_NAME", "S3_BUCKET_NAME"])
sc = SparkContext()
glueContext = GlueContext(sc)
spark = glueContext.spark_session
job = Job(glueContext)
job.init(args["JOB_NAME"], args)

bucket = args["S3_BUCKET_NAME"]

# 2. Esquemas EXPLICITOS -- nunca inferSchema.
#    inferSchema puede tipar total_amount como int si el sample no trae decimales, o
#    stars como string si hay un vacio. El parquet resultante divergiria del esquema que
#    espera el merge de la Fase 4 y el union fallaria (o castearia en silencio).
schema_customers = StructType([
    StructField("customer_id", StringType(), True),
    StructField("customer_name", StringType(), True),
    StructField("email", StringType(), True),
    StructField("country", StringType(), True),
    StructField("created_at", StringType(), True),
])

schema_flights = StructType([
    StructField("flight_id", StringType(), True),
    StructField("origin_city", StringType(), True),
    StructField("destination_city", StringType(), True),
    StructField("airline", StringType(), True),
    StructField("price", DoubleType(), True),
])

schema_hotels = StructType([
    StructField("hotel_id", StringType(), True),
    StructField("hotel_name", StringType(), True),
    StructField("city", StringType(), True),
    StructField("stars", IntegerType(), True),
    StructField("price_per_night", DoubleType(), True),
])

schema_bookings = StructType([
    StructField("booking_id", StringType(), True),
    StructField("customer_id", StringType(), True),
    StructField("booking_date", StringType(), True),
    StructField("destination_city", StringType(), True),
    StructField("product_type", StringType(), True),
    StructField("flight_id", StringType(), True),
    StructField("hotel_id", StringType(), True),
    StructField("status", StringType(), True),
    StructField("total_amount", DoubleType(), True),
])

schema_payments = StructType([
    StructField("payment_id", StringType(), True),
    StructField("booking_id", StringType(), True),
    StructField("payment_method", StringType(), True),
    StructField("amount", DoubleType(), True),
    StructField("payment_status", StringType(), True),
])


def read_csv(table, schema):
    """Lee todos los CSV bajo raw/<table>/ con esquema explicito y header."""
    path = f"s3://{bucket}/raw/{table}/"
    print(f"Leyendo {path} ...")
    return (
        spark.read
        .option("header", "true")
        .option("mode", "PERMISSIVE")
        .schema(schema)
        .csv(path)
    )


print("=== Fase 0: bootstrap raw/ -> curated/ ===")

df_customers = read_csv("customers", schema_customers)
df_flights = read_csv("flights", schema_flights)
df_hotels = read_csv("hotels", schema_hotels)
df_bookings = read_csv("bookings", schema_bookings)
df_payments = read_csv("payments", schema_payments)

# 3. Deduplicar por PK y filtrar nulos -- los CSV son la fuente cruda y no pasaron por
#    las constraints de PostgreSQL, asi que replicamos la limpieza del Job 02 del Lab 2.
df_customers = df_customers.dropDuplicates(["customer_id"]).filter("customer_id IS NOT NULL")
df_flights = df_flights.dropDuplicates(["flight_id"]).filter("flight_id IS NOT NULL")
df_hotels = df_hotels.dropDuplicates(["hotel_id"]).filter("hotel_id IS NOT NULL")
df_bookings = df_bookings.dropDuplicates(["booking_id"]).filter("booking_id IS NOT NULL")
df_payments = df_payments.dropDuplicates(["payment_id"]).filter("payment_id IS NOT NULL")

df_customers.createOrReplaceTempView("customers")
df_flights.createOrReplaceTempView("flights")
df_hotels.createOrReplaceTempView("hotels")
df_bookings.createOrReplaceTempView("bookings")
df_payments.createOrReplaceTempView("payments")

# Diagnostico: bookings huerfanos (sin customer). Dato para el informe, no un error.
orphans = spark.sql("""
    SELECT COUNT(*) AS n
    FROM bookings b
    LEFT JOIN customers c ON b.customer_id = c.customer_id
    WHERE c.customer_id IS NULL
""").collect()[0]["n"]
print(f"Bookings huerfanos (sin customer): {orphans}")

# 4. Transformacion SQL EXACTA del Lab 2 (02_landing_to_curated.py lineas 45-100).
#    Sin modificaciones: es el contrato del esquema. Garantiza que un registro que
#    despues entre por Textract sea indistinguible de uno del dataset base.
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

        CASE
            WHEN b.status = 'confirmed' THEN CAST(b.total_amount AS DOUBLE)
            ELSE 0.0
        END AS confirmed_revenue,

        COALESCE(p.approved_paid_amount, 0.0) AS approved_paid_amount,

        (CAST(b.total_amount AS DOUBLE) - COALESCE(p.approved_paid_amount, 0.0)) AS payment_gap,

        CASE
            WHEN CAST(b.total_amount AS DOUBLE) > 0 THEN (COALESCE(p.approved_paid_amount, 0.0) / CAST(b.total_amount AS DOUBLE)) * 100.0
            ELSE 0.0
        END AS payment_coverage_pct,

        CASE WHEN b.status = 'confirmed' THEN true ELSE false END AS is_confirmed,
        CASE WHEN b.status = 'cancelled' THEN true ELSE false END AS is_cancelled,

        p.last_payment_method

    FROM bookings b
    LEFT JOIN customers c ON b.customer_id = c.customer_id
    LEFT JOIN flights f ON b.flight_id = f.flight_id
    LEFT JOIN hotels h ON b.hotel_id = h.hotel_id
    LEFT JOIN (
        SELECT
            booking_id,
            SUM(CAST(amount AS DOUBLE)) AS approved_paid_amount,
            MAX(payment_method) AS last_payment_method
        FROM payments
        WHERE payment_status = 'approved'
        GROUP BY booking_id
    ) p ON b.booking_id = p.booking_id
"""

# cache() para que el count() y el write() no recomputen el join completo dos veces.
df_curated = spark.sql(query).cache()

total = df_curated.count()
print(f"Filas en la tabla sabana: {total}")  # baseline para la Prueba 4

# 5. Escribir UN solo archivo (coalesce 1) a un temporal, luego renombrar al nombre final.
#    El Lab 3 asume un archivo unico para el read-modify-write de la Fase 4.
temp_path = f"s3://{bucket}/curated/temp_booking_analytics/"
df_curated.coalesce(1).write.mode("overwrite").parquet(temp_path)

s3 = boto3.client("s3")
prefix_temp = "curated/temp_booking_analytics/"
final_key = "curated/booking_analytics/teratrip_booking_analytics.parquet"

resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix_temp)
for obj in resp.get("Contents", []):
    if obj["Key"].endswith(".parquet"):
        s3.copy_object(
            CopySource={"Bucket": bucket, "Key": obj["Key"]},
            Bucket=bucket,
            Key=final_key,
        )
        break

# Limpiar el temporal
for obj in resp.get("Contents", []):
    s3.delete_object(Bucket=bucket, Key=obj["Key"])

print(f"Tabla sabana escrita en s3://{bucket}/{final_key}")
print(f"BASELINE = {total} filas. Anotarlo en docs/schema_contract.md")

job.commit()
print("Job completado con exito.")
