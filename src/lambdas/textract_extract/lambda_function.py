"""
Lambda 1 de la Parte A - Extraccion con Textract
Laboratorio 3 - TeraTrip

Recibe el evento de un objeto nuevo en incoming-documents/, llama a Textract y
devuelve la tabla del documento como una grilla de strings.

Limite de responsabilidad: esta Lambda conoce el MODELO DE DATOS DE TEXTRACT
(Blocks, Relationships, CELL, WORD). No conoce el esquema de TeraTrip: no mapea
headers, no castea tipos, no valida dominios. Todo eso es la Lambda de
normalizacion (Fase 3). Si el dia de manana se cambia de motor de OCR, solo se
reescribe este archivo.

Entrada - acepta dos formas:
    1. Evento de EventBridge (S3 Object Created), que es lo que llega en produccion.
    2. {"bucket": "...", "key": "..."} para probar a mano desde la consola.

Salida:
    {
        "bucket", "document_key", "run_id", "raw_key",
        "page_count", "table_count",
        "header": [...], "rows": [[...], ...], "row_count"
    }

Config: Python 3.12, timeout 120s, memoria 512 MB.
"""

import json
import os
import urllib.parse

import boto3

RAW_PREFIX = os.environ.get("TEXTRACT_RAW_PREFIX", "textract-raw/")

# Los clientes se crean perezosamente y se cachean entre invocaciones (Lambda
# reutiliza el contenedor). Crearlos a nivel de modulo obligaria a tener
# credenciales para siquiera importar el archivo, y romperia los tests locales
# de reconstruct_tables(), que es una funcion pura y no deberia necesitar AWS.
_clientes = {}


def _cliente(servicio):
    if servicio not in _clientes:
        _clientes[servicio] = boto3.client(servicio)
    return _clientes[servicio]


# Textract sincronico acepta PDF de una sola pagina, PNG, JPEG y TIFF.
EXTENSIONES_VALIDAS = (".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif")


class DocumentoInvalido(Exception):
    """El documento no se puede procesar. No tiene sentido reintentar."""


def parse_event(event):
    """Extrae (bucket, key, etag) del evento, sea de EventBridge o de un test manual."""
    detail = event.get("detail")
    if detail and "bucket" in detail:  # EventBridge: S3 Object Created
        bucket = detail["bucket"]["name"]
        key = urllib.parse.unquote_plus(detail["object"]["key"])
        etag = detail["object"].get("etag", "")
    elif "bucket" in event and "key" in event:  # invocacion manual
        bucket = event["bucket"]
        key = urllib.parse.unquote_plus(event["key"])
        etag = event.get("etag", "")
    else:
        raise DocumentoInvalido(
            "No pude encontrar bucket/key en el evento: " + json.dumps(event)[:400]
        )
    return bucket, key, etag.strip('"')


def resolve_run_id(bucket, key, etag):
    """El run_id deriva del eTag del objeto, NO de un uuid ni del timestamp.

    Es la primera de las tres capas de idempotencia del pipeline: si S3 o
    EventBridge emiten el evento dos veces para el mismo archivo, ambas corridas
    comparten run_id, escriben sobre el mismo prefijo de incoming-data/ y el
    anti-join del Glue Job termina descartando todo. Un uuid haria que cada
    evento duplicado se viera como una corrida nueva.
    """
    if not etag:
        # La invocacion manual no trae eTag: se lo pedimos a S3.
        etag = _cliente("s3").head_object(Bucket=bucket, Key=key)["ETag"].strip('"')
    return etag


def _texto_de_celda(cell, block_map):
    """Concatena los WORD hijos de una CELL en el orden en que Textract los devuelve."""
    partes = []
    for rel in cell.get("Relationships", []):
        if rel["Type"] != "CHILD":
            continue
        for child_id in rel["Ids"]:
            child = block_map.get(child_id)
            if not child:
                continue
            if child["BlockType"] == "WORD":
                partes.append(child["Text"])
            elif child["BlockType"] == "SELECTION_ELEMENT":
                if child.get("SelectionStatus") == "SELECTED":
                    partes.append("X")
    return " ".join(partes).strip()


def reconstruct_tables(blocks):
    """Reconstruye las tablas a partir de la lista PLANA de Blocks de Textract.

    Textract no devuelve la tabla armada: devuelve bloques sueltos unidos por
    Relationships. Hay que bajar TABLE -> CELL -> WORD y reindexar por
    (RowIndex, ColumnIndex). Una celda vacia simplemente no trae hijos, asi que
    la grilla se rellena con cadena vacia en las posiciones faltantes: si se
    armara solo con las celdas presentes, una celda vacia correria toda la fila
    una posicion a la izquierda y desalinearia los datos contra los headers.

    Se ignoran los bloques MERGED_CELL, TABLE_TITLE y TABLE_FOOTER: las celdas
    base ya cubren todo el contenido y contarlos duplicaria texto.

    Funcion pura: no toca AWS. Es la que se testea en local contra el JSON
    guardado en textract-raw/ (ver tests/test_textract_reconstruct.py).
    """
    block_map = {b["Id"]: b for b in blocks}
    tablas = []

    for block in blocks:
        if block["BlockType"] != "TABLE":
            continue

        celdas = {}
        max_fila = max_col = 0
        for rel in block.get("Relationships", []):
            if rel["Type"] != "CHILD":
                continue
            for cell_id in rel["Ids"]:
                cell = block_map.get(cell_id)
                if not cell or cell["BlockType"] != "CELL":
                    continue
                fila, col = cell["RowIndex"], cell["ColumnIndex"]
                max_fila = max(max_fila, fila)
                max_col = max(max_col, col)
                celdas[(fila, col)] = _texto_de_celda(cell, block_map)

        grilla = [
            [celdas.get((f, c), "") for c in range(1, max_col + 1)]
            for f in range(1, max_fila + 1)
        ]
        tablas.append(grilla)

    return tablas


def elegir_tabla(tablas):
    """El documento del laboratorio trae una sola tabla, pero Textract puede
    detectar bloques espurios (un recuadro, el pie de pagina). Nos quedamos con
    la que tiene mas celdas: la tabla de reservas siempre es la mas grande."""
    if not tablas:
        raise DocumentoInvalido(
            "Textract no detecto ninguna tabla en el documento. "
            "Revisar el JSON crudo en textract-raw/ antes de tocar la normalizacion."
        )
    return max(tablas, key=lambda t: sum(len(f) for f in t))


def lambda_handler(event, context):
    bucket, key, etag = parse_event(event)
    print("Documento: s3://" + bucket + "/" + key)

    if not key.lower().endswith(EXTENSIONES_VALIDAS):
        raise DocumentoInvalido(
            "Extension no soportada por Textract sincronico: " + key
        )

    run_id = resolve_run_id(bucket, key, etag)
    print("run_id (derivado del eTag): " + run_id)

    # AnalyzeDocument sincronico. Valido porque el documento es de UNA pagina.
    # Para multipagina hay que migrar a StartDocumentAnalysis + polling; es la
    # pregunta de investigacion 1 de la consigna.
    respuesta = _cliente("textract").analyze_document(
        Document={"S3Object": {"Bucket": bucket, "Name": key}},
        FeatureTypes=["TABLES"],
    )

    blocks = respuesta.get("Blocks", [])
    page_count = respuesta.get("DocumentMetadata", {}).get("Pages", 0)

    # Guardar el JSON CRUDO antes de interpretarlo. Es lo que despues permite
    # distinguir "Textract leyo mal" de "la reconstruccion rompio", y reiterar
    # sobre la logica sin volver a pagar una llamada a Textract.
    raw_key = RAW_PREFIX + run_id + ".json"
    _cliente("s3").put_object(
        Bucket=bucket,
        Key=raw_key,
        Body=json.dumps(respuesta, default=str).encode("utf-8"),
        ContentType="application/json",
    )
    print("JSON crudo guardado en s3://" + bucket + "/" + raw_key +
          " (" + str(len(blocks)) + " blocks)")

    tablas = reconstruct_tables(blocks)
    tabla = elegir_tabla(tablas)

    header, filas = tabla[0], tabla[1:]
    print("Tabla reconstruida: " + str(len(filas)) + " filas x " +
          str(len(header)) + " columnas")
    print("Headers detectados: " + str(header))

    # La salida viaja por el payload de la Step Function (limite 256 KB). Un
    # documento de 5-10 reservas ocupa ~1 KB, asi que va inline; si el formato
    # creciera, habria que pasar solo el raw_key y que la normalizacion lo lea.
    return {
        "bucket": bucket,
        "document_key": key,
        "run_id": run_id,
        "raw_key": raw_key,
        "page_count": page_count,
        "table_count": len(tablas),
        "header": header,
        "rows": filas,
        "row_count": len(filas),
    }
