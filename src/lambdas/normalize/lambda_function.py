"""
Lambda 2 de la Parte A - Normalizacion al esquema de TeraTrip
Laboratorio 3 - TeraTrip

Toma la grilla de strings que devolvio la Lambda de Textract y produce registros
que cumplen el contrato de docs/schema_contract.md.

Limite de responsabilidad: esta Lambda conoce el ESQUEMA DE TERATRIP y nada de
Textract. Y NO calcula campos derivados (booking_year, confirmed_revenue,
payment_gap, is_confirmed, ...): esos los genera el Glue Job del merge, para que
exista una unica fuente de verdad y un registro que entra por documento sea
indistinguible de uno del dataset base.

Entrada: la salida de lambda-teratrip-textract-extract.
Salida:
    {
        "bucket", "run_id", "output_prefix",
        "record_count", "rejected_count", "booking_ids"
    }

Config: Python 3.12, timeout 60s, memoria 256 MB.
"""

import json
import os
import re
import unicodedata
from datetime import datetime

import boto3

OUTPUT_PREFIX = os.environ.get("INCOMING_DATA_PREFIX", "incoming-data/")

_clientes = {}


def _cliente(servicio):
    if servicio not in _clientes:
        _clientes[servicio] = boto3.client(servicio)
    return _clientes[servicio]


# --- Contrato ------------------------------------------------------------------

CAMPOS = [
    "booking_id", "booking_date", "customer_id", "customer_name",
    "customer_country", "destination_city", "product_type", "status",
    "total_amount", "payment_amount", "payment_status", "payment_method",
]

# Campos cuyo valor alimenta un campo derivado monetario o de estado. Si alguno
# viene mal, la fila se descarta: un status invalido no rompe nada visiblemente,
# pero produce un confirmed_revenue silenciosamente equivocado, que es peor.
OBLIGATORIOS = [
    "booking_id", "booking_date", "product_type", "status",
    "total_amount", "payment_amount", "payment_status",
]

DOMINIOS = {
    "status": {"confirmed", "cancelled", "pending"},
    "product_type": {"flight", "hotel", "package"},
    "payment_status": {"approved", "rejected", "pending", "refunded"},
    "payment_method": {"credit_card", "debit_card", "wallet", "bank_transfer", "cash"},
}

# Variantes de header que aceptamos ademas del nombre canonico. La normalizacion
# de _clave() ya resuelve mayusculas, espacios, guiones y acentos; este mapa
# cubre sinonimos que no son solo cuestion de formato.
ALIAS = {
    "id_reserva": "booking_id",
    "reserva": "booking_id",
    "fecha": "booking_date",
    "fecha_reserva": "booking_date",
    "id_cliente": "customer_id",
    "cliente": "customer_name",
    "nombre_cliente": "customer_name",
    "pais": "customer_country",
    "pais_cliente": "customer_country",
    "destino": "destination_city",
    "ciudad_destino": "destination_city",
    "tipo_producto": "product_type",
    "estado": "status",
    "monto_total": "total_amount",
    "importe": "total_amount",
    "monto_pagado": "payment_amount",
    "estado_pago": "payment_status",
    "metodo_pago": "payment_method",
    "amount": "total_amount",
    "paid_amount": "payment_amount",
}


class SinDatos(Exception):
    """El documento no trajo ninguna fila utilizable."""


# --- Saneamiento ---------------------------------------------------------------

def _clave(texto):
    """Normaliza un header a su forma comparable: sin acentos, minusculas,
    separadores unificados a guion bajo. 'Customer Name', 'CUSTOMER-NAME' y
    'customer_name' colapsan al mismo valor."""
    t = unicodedata.normalize("NFKD", texto or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.strip().lower()
    t = re.sub(r"[\s\-./]+", "_", t)
    t = re.sub(r"[^a-z0-9_]", "", t)
    return re.sub(r"_+", "_", t).strip("_")


def mapear_headers(header):
    """Devuelve {indice_de_columna: campo_canonico} y la lista de headers que no
    se pudieron mapear. El mapeo es POR NOMBRE, nunca por posicion: si Textract
    devuelve las columnas en otro orden o agrega una, esto sigue funcionando."""
    mapa, desconocidos = {}, []
    for i, h in enumerate(header):
        k = _clave(h)
        campo = k if k in CAMPOS else ALIAS.get(k)
        if campo:
            mapa[i] = campo
        elif k:
            desconocidos.append(h)
    return mapa, desconocidos


def parsear_fecha(valor):
    """Normaliza a ISO YYYY-MM-DD. Devuelve None si no es una fecha valida.

    Acepta las variantes que puede traer un documento, pero siempre emite ISO:
    el Glue Job hace CAST(booking_date AS DATE) y Spark solo entiende ISO.
    """
    v = (valor or "").strip()
    if not v:
        return None
    for formato in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(v, formato).date().isoformat()
        except ValueError:
            continue
    return None


def parsear_monto(valor):
    """Limpia simbolos de moneda y separadores, y devuelve float. None si no parsea.

    El caso ambiguo es '1.850,00' (formato es-AR) contra '1,850.00' (en-US):
    ambos valen 1850. Se resuelve mirando cual separador aparece ultimo, que es
    siempre el decimal.
    """
    v = (valor or "").strip()
    if not v:
        return None
    v = re.sub(r"[^\d,.\-]", "", v)  # saca $, USD, espacios
    if not v or v in ("-", ".", ","):
        return None

    ultima_coma, ultimo_punto = v.rfind(","), v.rfind(".")
    if ultima_coma > -1 and ultimo_punto > -1:
        if ultima_coma > ultimo_punto:      # 1.850,00 -> decimal es la coma
            v = v.replace(".", "").replace(",", ".")
        else:                                # 1,850.00 -> decimal es el punto
            v = v.replace(",", "")
    elif ultima_coma > -1:
        # Una sola coma: decimal si separa 1 o 2 digitos, si no es de miles.
        v = v.replace(",", "." if len(v) - ultima_coma - 1 <= 2 else "")

    try:
        return round(float(v), 2)
    except ValueError:
        return None


def parsear_dominio(valor, campo):
    """Minusculas y espacios a guion bajo, validado contra el dominio permitido.
    Devuelve None si el valor no pertenece al dominio."""
    v = _clave(valor)
    return v if v in DOMINIOS[campo] else None


def parsear_texto(valor):
    """Colapsa espacios. Textract separa los WORD con un espacio simple, pero un
    documento puede traer espacios dobles o saltos."""
    v = re.sub(r"\s+", " ", (valor or "").strip())
    return v or None


# --- Normalizacion de una fila ---------------------------------------------------

def normalizar_fila(celdas, mapa, numero_fila):
    """Convierte una fila de la grilla en un registro, o devuelve (None, motivo).

    Nunca falla en silencio: toda fila descartada sale con un motivo explicito
    que despues se loguea y se guarda en _rejected.json.
    """
    crudo = {}
    for i, campo in mapa.items():
        crudo[campo] = celdas[i] if i < len(celdas) else ""

    registro = {
        "booking_id": parsear_texto(crudo.get("booking_id")),
        "booking_date": parsear_fecha(crudo.get("booking_date")),
        "customer_id": parsear_texto(crudo.get("customer_id")),
        "customer_name": parsear_texto(crudo.get("customer_name")),
        "customer_country": parsear_texto(crudo.get("customer_country")),
        "destination_city": parsear_texto(crudo.get("destination_city")),
        "product_type": parsear_dominio(crudo.get("product_type"), "product_type"),
        "status": parsear_dominio(crudo.get("status"), "status"),
        "total_amount": parsear_monto(crudo.get("total_amount")),
        "payment_amount": parsear_monto(crudo.get("payment_amount")),
        "payment_status": parsear_dominio(crudo.get("payment_status"), "payment_status"),
        "payment_method": parsear_dominio(crudo.get("payment_method"), "payment_method"),
    }

    faltantes = [c for c in OBLIGATORIOS if registro[c] is None]
    if faltantes:
        return None, {
            "fila": numero_fila,
            "motivo": "campos obligatorios invalidos o ausentes: " + ", ".join(faltantes),
            "valores_crudos": crudo,
        }

    if registro["total_amount"] < 0 or registro["payment_amount"] < 0:
        return None, {
            "fila": numero_fila,
            "motivo": "montos negativos",
            "valores_crudos": crudo,
        }

    return registro, None


def normalizar(header, filas):
    """Nucleo de la fase. Funcion pura: no toca AWS, se testea en local."""
    mapa, desconocidos = mapear_headers(header)

    faltantes = [c for c in CAMPOS if c not in mapa.values()]
    if faltantes:
        print("ATENCION: headers no encontrados en el documento: " + str(faltantes))
    if desconocidos:
        print("ATENCION: headers no reconocidos, se ignoran: " + str(desconocidos))

    obligatorios_ausentes = [c for c in OBLIGATORIOS if c not in mapa.values()]
    if obligatorios_ausentes:
        raise SinDatos(
            "El documento no tiene los headers obligatorios " +
            str(obligatorios_ausentes) +
            ". Revisar el JSON crudo en textract-raw/: probablemente Textract "
            "partio un header en dos lineas."
        )

    registros, rechazos = [], []
    for n, celdas in enumerate(filas, start=1):
        if not any((c or "").strip() for c in celdas):
            continue  # fila totalmente vacia: no es un rechazo, es ruido de la tabla
        registro, rechazo = normalizar_fila(celdas, mapa, n)
        (registros if registro else rechazos).append(registro or rechazo)

    return registros, rechazos


def lambda_handler(event, context):
    bucket = event["bucket"]
    run_id = event["run_id"]
    header = event.get("header", [])
    filas = event.get("rows", [])

    print("run_id: " + run_id + " | filas recibidas: " + str(len(filas)))

    registros, rechazos = normalizar(header, filas)

    for r in rechazos:
        print("FILA DESCARTADA " + str(r["fila"]) + ": " + r["motivo"])

    # Duplicados dentro del propio documento. No se descartan aca: el
    # dropDuplicates del Glue Job los resuelve. Se loguea para que el dato
    # aparezca en el informe.
    ids = [r["booking_id"] for r in registros]
    repetidos = sorted({i for i in ids if ids.count(i) > 1})
    if repetidos:
        print("ATENCION: booking_id repetidos dentro del documento: " + str(repetidos))

    output_prefix = OUTPUT_PREFIX + run_id + "/"
    s3 = _cliente("s3")

    if registros:
        # JSON Lines: lo lee Spark directo con spark.read.json y ademas se puede
        # abrir a mano desde la consola de S3 para debuggear. Parquet obligaria a
        # empaquetar pyarrow como layer de la Lambda sin ganar nada a este volumen.
        cuerpo = "\n".join(json.dumps(r, ensure_ascii=False) for r in registros)
        s3.put_object(
            Bucket=bucket,
            Key=output_prefix + "records.jsonl",
            Body=cuerpo.encode("utf-8"),
            ContentType="application/x-ndjson",
        )
        print("Escritos " + str(len(registros)) + " registros en s3://" +
              bucket + "/" + output_prefix + "records.jsonl")

    if rechazos:
        # El guion bajo inicial no es decorativo: Spark ignora los archivos que
        # empiezan con _ o . al leer un directorio, asi que el archivo de
        # auditoria convive con los datos sin que el Glue Job intente parsearlo.
        s3.put_object(
            Bucket=bucket,
            Key=output_prefix + "_rejected.json",
            Body=json.dumps(rechazos, ensure_ascii=False, indent=2).encode("utf-8"),
            ContentType="application/json",
        )

    return {
        "bucket": bucket,
        "run_id": run_id,
        "output_prefix": output_prefix,
        "record_count": len(registros),
        "rejected_count": len(rechazos),
        "booking_ids": ids,
    }
