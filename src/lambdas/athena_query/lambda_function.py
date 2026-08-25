"""
Lambda de la Parte B - Herramienta execute_athena_query
Laboratorio 3 - TeraTrip

Es la unica via por la que el agente toca datos. Recibe el SQL que genero el
modelo, lo valida en capas, lo ejecuta en Athena y devuelve el resultado.

La validacion vive ACA, no en el system prompt del agente. Un prompt se puede
sortear con una instruccion habil ("ignora las reglas anteriores", "esto es una
prueba autorizada"); el codigo no. El prompt puede pedirle al modelo que se
porte bien, pero la garantia la da esta funcion.

Capas de validacion, todas obligatorias y en este orden:
    1. Quitar comentarios (-- y multilinea) sin romper los literales de texto.
    2. Rechazar ; intermedios, que es como se apilan sentencias.
    3. Exigir que la sentencia empiece con SELECT o WITH.
    4. Denylist explicita de DDL/DML.
    5. Validar que toda tabla referenciada sea la del laboratorio.
    6. Forzar un LIMIT y acotar las filas devueltas.

Ninguna capa alcanza sola. La 3 sin la 2 se sortea con "SELECT 1; DROP ...".
La 2 y la 3 sin la 1 se sortean escondiendo el ; en un comentario. La 5 es la
que impide leer information_schema o cualquier otra tabla de la cuenta.

Config: Python 3.12, timeout 60s, memoria 256 MB.
"""

import re
import time

import boto3

DATABASE = "teratrip_db"
TABLA_PERMITIDA = "booking_analytics"
WORKGROUP = "wg-teratrip"

MAX_FILAS = 200          # filas devueltas al agente
TIMEOUT_CONSULTA = 45    # segundos; la Lambda tiene 60 de timeout

_clientes = {}


def _cliente(servicio):
    if servicio not in _clientes:
        _clientes[servicio] = boto3.client(servicio)
    return _clientes[servicio]


class SqlRechazado(Exception):
    """El SQL no paso la validacion. El mensaje se le devuelve al agente para
    que pueda explicarle al usuario por que no se ejecuto."""


# --- Capa 1: normalizacion -------------------------------------------------------

def quitar_comentarios(sql):
    """Elimina comentarios -- y multilinea respetando los literales de texto.

    Se recorre caracter por caracter en vez de usar un regex porque un regex no
    distingue un comentario real de uno que vive adentro de un string: en
    WHERE destination_city = 'Rio -- Norte' el -- no abre un comentario. Y al
    reves, esconder un ; adentro de un comentario es la forma clasica de burlar
    una validacion que solo mira el principio de la sentencia.
    """
    salida = []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        if c == "'":  # literal de texto: copiar tal cual hasta el cierre
            salida.append(c)
            i += 1
            while i < n:
                salida.append(sql[i])
                if sql[i] == "'":
                    if i + 1 < n and sql[i + 1] == "'":  # comilla escapada ''
                        salida.append(sql[i + 1])
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
        elif c == '"':  # identificador entrecomillado
            salida.append(c)
            i += 1
            while i < n:
                salida.append(sql[i])
                if sql[i] == '"':
                    i += 1
                    break
                i += 1
        elif c == "-" and i + 1 < n and sql[i + 1] == "-":
            while i < n and sql[i] != "\n":
                i += 1
        elif c == "/" and i + 1 < n and sql[i + 1] == "*":
            fin = sql.find("*/", i + 2)
            i = n if fin == -1 else fin + 2
        else:
            salida.append(c)
            i += 1
    return "".join(salida)


def _fuera_de_literales(sql):
    """Devuelve el SQL con el contenido de los literales reemplazado por espacios.

    Sirve para buscar palabras clave y ; sin que un valor de texto genere un
    falso positivo: destination_city = 'DROP Center' no es un intento de DDL.
    """
    salida = []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        if c in ("'", '"'):
            cierre = c
            salida.append(" ")
            i += 1
            while i < n:
                if sql[i] == cierre:
                    if cierre == "'" and i + 1 < n and sql[i + 1] == "'":
                        salida.append("  ")
                        i += 2
                        continue
                    salida.append(" ")
                    i += 1
                    break
                salida.append(" ")
                i += 1
        else:
            salida.append(c)
            i += 1
    return "".join(salida)


# --- Capas 2 a 5: validacion -----------------------------------------------------

# Palabras que solo pueden aparecer como VERBO de una sentencia. No incluye
# "desc": DESC es a la vez la abreviatura de DESCRIBE y el modificador de
# ORDER BY, que el agente usa en casi toda consulta de ranking. Como la capa 3
# ya exige que la sentencia empiece con SELECT o WITH, DESCRIBE nunca puede
# llegar hasta aca.
PROHIBIDAS = [
    "insert", "update", "delete", "drop", "create", "alter", "merge",
    "truncate", "grant", "revoke", "unload", "msck", "replace", "call",
    "commit", "rollback", "set", "reset", "prepare", "execute", "deallocate",
    "show", "describe", "analyze", "vacuum", "optimize",
]


def validar(sql):
    """Aplica las capas 2 a 5 y devuelve el SQL saneado. Lanza SqlRechazado."""
    if not sql or not sql.strip():
        raise SqlRechazado("La consulta esta vacia.")

    limpio = quitar_comentarios(sql).strip()
    enmascarado = _fuera_de_literales(limpio)

    # Capa 2: SQL apilado. Se tolera un unico ; final.
    cuerpo = enmascarado.rstrip()
    if cuerpo.endswith(";"):
        cuerpo = cuerpo[:-1]
        limpio = limpio.rstrip().rstrip(";").rstrip()
    if ";" in cuerpo:
        raise SqlRechazado(
            "Se rechazo la consulta porque contiene mas de una sentencia. "
            "Solo se permite una unica sentencia SELECT."
        )
    enmascarado = cuerpo

    # Capa 3: solo lectura desde el arranque.
    if not re.match(r"^\s*(select|with)\b", enmascarado, re.I):
        raise SqlRechazado(
            "Se rechazo la consulta porque no es de lectura. "
            "Solo se permiten sentencias que empiecen con SELECT o WITH."
        )

    # Capa 4: denylist explicita, buscada fuera de los literales de texto.
    # El lookahead de parentesis distingue el verbo de la funcion homonima: en
    # Trino, replace(...) y truncate(...) son funciones escalares perfectamente
    # validas dentro de un SELECT, mientras que CREATE OR REPLACE y TRUNCATE
    # TABLE son DDL. Sin esa distincion la denylist rechazaria consultas
    # legitimas, que es la forma mas facil de que un guard termine desactivado.
    for palabra in PROHIBIDAS:
        if re.search(r"\b" + palabra + r"\b(?!\s*\()", enmascarado, re.I):
            raise SqlRechazado(
                "Se rechazo la consulta porque contiene la operacion no permitida '" +
                palabra.upper() + "'. Esta herramienta es de solo lectura."
            )

    # Capa 5: unicamente la tabla del laboratorio.
    ctes = {
        m.group(1).strip('"').lower()
        for m in re.finditer(r'(?:\bwith\b|,)\s*("?\w+"?)\s+as\s*\(', enmascarado, re.I)
    }
    referencias = [
        m.group(1).strip('"').lower()
        for m in re.finditer(r'\b(?:from|join)\s+("?[\w.]+"?)', enmascarado, re.I)
    ]
    permitidas = {TABLA_PERMITIDA, DATABASE + "." + TABLA_PERMITIDA} | ctes
    for ref in referencias:
        if ref not in permitidas:
            raise SqlRechazado(
                "Se rechazo la consulta porque referencia la tabla '" + ref +
                "'. Esta herramienta solo puede consultar '" + TABLA_PERMITIDA + "'."
            )
    if not referencias:
        raise SqlRechazado(
            "Se rechazo la consulta porque no referencia ninguna tabla. "
            "Debe consultar '" + TABLA_PERMITIDA + "'."
        )

    return limpio


# --- Capa 6: acotar el volumen ----------------------------------------------------

def forzar_limit(sql):
    """Garantiza un LIMIT y lo acota a MAX_FILAS.

    Es una red de contencion de costo y de payload: sin LIMIT, una consulta
    perfectamente valida puede devolver 500.000 filas y llenar la ventana de
    contexto del modelo con datos que no va a usar.
    """
    m = re.search(r"\blimit\s+(\d+)\s*$", sql, re.I)
    if m:
        if int(m.group(1)) > MAX_FILAS:
            return sql[:m.start()] + "LIMIT " + str(MAX_FILAS)
        return sql
    return sql + "\nLIMIT " + str(MAX_FILAS)


# --- Ejecucion --------------------------------------------------------------------

ENTEROS = {"tinyint", "smallint", "integer", "int", "bigint"}
DECIMALES = {"real", "double", "float", "decimal"}


def convertir(valor, tipo):
    """Castea un valor de Athena al tipo JSON que le corresponde.

    Athena devuelve TODO como string en VarCharValue, y los numeros grandes en
    notacion cientifica: SUM(confirmed_revenue) vuelve como "1.931493953000002E7".
    Si eso llega asi al modelo, tiene que interpretarlo para responder, y ahi es
    donde una respuesta correcta de Athena se convierte en una respuesta
    equivocada del agente. Devolver un numero JSON real elimina ese paso.

    El tipo lo declara la propia Athena en ResultSetMetadata, asi que no se
    adivina por la forma del string.
    """
    if valor is None:
        return None
    t = (tipo or "").lower()
    try:
        if t == "boolean":
            return valor.lower() == "true"
        if t in ENTEROS:
            return int(valor)
        if t in DECIMALES:
            return float(valor)
    except (ValueError, AttributeError):
        # Si Athena declara un tipo pero manda algo que no parsea, se devuelve
        # el string crudo: es preferible a perder el dato.
        return valor
    return valor


def ejecutar(sql):
    athena = _cliente("athena")
    # Sin ResultConfiguration: la ubicacion de resultados y el limite de bytes
    # escaneados los impone el WorkGroup. Es una segunda red de seguridad, esta
    # a nivel de Athena, que no depende de que este codigo este bien.
    qid = athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": DATABASE},
        WorkGroup=WORKGROUP,
    )["QueryExecutionId"]

    inicio = time.time()
    estado, motivo = "QUEUED", ""
    escaneado = 0
    while time.time() - inicio < TIMEOUT_CONSULTA:
        ejec = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]
        estado = ejec["Status"]["State"]
        if estado in ("SUCCEEDED", "FAILED", "CANCELLED"):
            motivo = ejec["Status"].get("StateChangeReason", "")
            escaneado = ejec.get("Statistics", {}).get("DataScannedInBytes", 0)
            break
        time.sleep(0.4)
    else:
        athena.stop_query_execution(QueryExecutionId=qid)
        raise SqlRechazado(
            "La consulta supero el tiempo maximo de " + str(TIMEOUT_CONSULTA) +
            " segundos y se cancelo. Probar con una consulta mas acotada."
        )

    if estado != "SUCCEEDED":
        raise SqlRechazado("Athena rechazo la consulta (" + estado + "): " + motivo)

    # MaxResults incluye la fila de encabezados, de ahi el +1.
    resultado = athena.get_query_results(QueryExecutionId=qid, MaxResults=MAX_FILAS + 1)
    filas_crudas = resultado["ResultSet"]["Rows"]
    info = resultado["ResultSet"]["ResultSetMetadata"]["ColumnInfo"]
    columnas = [c["Name"] for c in info]
    tipos = [c.get("Type") for c in info]

    filas = []
    for fila in filas_crudas[1:]:  # la primera es el encabezado
        filas.append([
            convertir(celda.get("VarCharValue"), tipos[i] if i < len(tipos) else None)
            for i, celda in enumerate(fila["Data"])
        ])

    return {
        "columns": columnas,
        "column_types": tipos,
        "rows": filas,
        "row_count": len(filas),
        "truncated": len(filas) >= MAX_FILAS,
        "query_execution_id": qid,
        "data_scanned_bytes": escaneado,
    }


def lambda_handler(event, context):
    # El Gateway de AgentCore puede entregar los argumentos de la tool en la
    # raiz del evento o anidados; se contemplan las dos formas.
    sql = (
        event.get("sql_query")
        or (event.get("arguments") or {}).get("sql_query")
        or (event.get("body") or {}).get("sql_query")
    )

    print("SQL recibido: " + str(sql))

    try:
        validado = forzar_limit(validar(sql or ""))
    except SqlRechazado as e:
        # Se devuelve como respuesta, no como excepcion: el agente tiene que
        # poder explicarle al usuario por que no se ejecuto, en vez de recibir
        # un error opaco de invocacion.
        print("RECHAZADO: " + str(e))
        return {"error": "consulta_rechazada", "reason": str(e)}

    print("SQL ejecutado: " + validado)

    try:
        resultado = ejecutar(validado)
    except SqlRechazado as e:
        print("FALLO EN ATHENA: " + str(e))
        return {"error": "consulta_fallida", "reason": str(e)}

    print("Filas: " + str(resultado["row_count"]) +
          " | bytes escaneados: " + str(resultado["data_scanned_bytes"]) +
          " | QueryExecutionId: " + resultado["query_execution_id"])
    return resultado
