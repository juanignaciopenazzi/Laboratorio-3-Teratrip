"""
Tests de la reconstruccion de tablas de Textract - Fase 2
Laboratorio 3 - TeraTrip

reconstruct_tables() es una funcion pura: no toca AWS. Eso permite iterar sobre
la logica sin volver a pagar una llamada a Textract ni volver a subir el PDF,
que es exactamente el motivo por el que la Lambda guarda el JSON crudo en
textract-raw/.

Uso:
    python tests/test_textract_reconstruct.py                    # casos sinteticos
    python tests/test_textract_reconstruct.py <archivo.json>     # JSON real de textract-raw/
"""

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "lambdas" / "textract_extract"))

from lambda_function import reconstruct_tables, elegir_tabla, DocumentoInvalido  # noqa: E402


# --- Constructores de Blocks sinteticos con la forma que devuelve Textract ------

def _word(texto):
    return {"Id": str(uuid.uuid4()), "BlockType": "WORD", "Text": texto}


def _cell(fila, col, palabras):
    bloques = [_word(p) for p in palabras]
    cell = {
        "Id": str(uuid.uuid4()),
        "BlockType": "CELL",
        "RowIndex": fila,
        "ColumnIndex": col,
    }
    if bloques:
        cell["Relationships"] = [{"Type": "CHILD", "Ids": [b["Id"] for b in bloques]}]
    return cell, bloques


def tabla_sintetica(grilla, extras=None):
    """Arma la lista plana de Blocks que Textract devolveria para esa grilla.

    `grilla` es una lista de filas; cada celda es un string (se parte en WORDs por
    espacios, igual que hace Textract). Una cadena vacia = celda sin hijos.
    """
    blocks = []
    ids_celdas = []
    for f, fila in enumerate(grilla, start=1):
        for c, texto in enumerate(fila, start=1):
            cell, palabras = _cell(f, c, texto.split() if texto else [])
            ids_celdas.append(cell["Id"])
            blocks.append(cell)
            blocks.extend(palabras)

    tabla = {
        "Id": str(uuid.uuid4()),
        "BlockType": "TABLE",
        "Relationships": [{"Type": "CHILD", "Ids": ids_celdas + (extras or [])}],
    }
    return [tabla] + blocks


# --- Casos ----------------------------------------------------------------------

HEADERS = [
    "booking_id", "booking_date", "customer_id", "customer_name",
    "customer_country", "destination_city", "product_type", "status",
    "total_amount", "payment_amount", "payment_status", "payment_method",
]

FILA_1 = [
    "B900001", "2026-08-25", "C000001", "Carlos Rodriguez", "Italy",
    "Madrid", "package", "confirmed", "1850.00", "1850.00", "approved", "credit_card",
]

FILA_CANCELADA = [
    "B900004", "2026-08-26", "C045000", "Valentina Rojas", "Argentina",
    "Bariloche", "package", "cancelled", "1320.00", "0.00", "rejected", "credit_card",
]


def test_tabla_completa():
    blocks = tabla_sintetica([HEADERS, FILA_1, FILA_CANCELADA])
    tabla = elegir_tabla(reconstruct_tables(blocks))
    assert tabla[0] == HEADERS, tabla[0]
    assert tabla[1] == FILA_1, tabla[1]
    assert tabla[2] == FILA_CANCELADA, tabla[2]
    assert len(tabla) == 3


def test_nombre_con_espacios_no_se_parte_en_columnas():
    """'Carlos Rodriguez' son dos WORD dentro de UNA celda. Si la reconstruccion
    los tratara como dos celdas, correria toda la fila una posicion."""
    blocks = tabla_sintetica([HEADERS, FILA_1])
    tabla = elegir_tabla(reconstruct_tables(blocks))
    assert tabla[1][3] == "Carlos Rodriguez"
    assert len(tabla[1]) == 12


def test_celda_vacia_no_desalinea_la_fila():
    """El caso que mas dano hace: una celda sin WORDs. Si se armara la fila solo
    con las celdas presentes, todo lo que viene despues se correria a la
    izquierda y payment_method terminaria en la columna de payment_status."""
    fila = list(FILA_1)
    fila[4] = ""  # customer_country vacio
    blocks = tabla_sintetica([HEADERS, fila])
    tabla = elegir_tabla(reconstruct_tables(blocks))
    assert len(tabla[1]) == 12
    assert tabla[1][4] == ""
    assert tabla[1][5] == "Madrid"          # destination_city sigue en su lugar
    assert tabla[1][11] == "credit_card"    # payment_method tambien


def test_ignora_bloques_que_no_son_celdas():
    """TABLE_TITLE y TABLE_FOOTER cuelgan del TABLE igual que las CELL. Si no se
    filtraran por BlockType, su texto entraria como una fila fantasma."""
    titulo = {"Id": str(uuid.uuid4()), "BlockType": "TABLE_TITLE"}
    pie = {"Id": str(uuid.uuid4()), "BlockType": "TABLE_FOOTER"}
    blocks = tabla_sintetica([HEADERS, FILA_1], extras=[titulo["Id"], pie["Id"]])
    blocks.extend([titulo, pie])
    tabla = elegir_tabla(reconstruct_tables(blocks))
    assert len(tabla) == 2


def test_elige_la_tabla_mas_grande():
    """Textract a veces detecta un recuadro decorativo como TABLE. La de reservas
    siempre es la que mas celdas tiene."""
    grande = tabla_sintetica([HEADERS, FILA_1, FILA_CANCELADA])
    chica = tabla_sintetica([["nota"]])
    tabla = elegir_tabla(reconstruct_tables(chica + grande))
    assert tabla[0] == HEADERS


def test_sin_tablas_falla_con_mensaje_claro():
    try:
        elegir_tabla(reconstruct_tables([{"Id": "x", "BlockType": "PAGE"}]))
    except DocumentoInvalido as e:
        assert "textract-raw" in str(e)
    else:
        raise AssertionError("Deberia haber fallado con DocumentoInvalido")


def inspeccionar_json_real(path):
    """Corre la reconstruccion contra un JSON descargado de textract-raw/.
    Es el paso de verificacion de la Fase 2 con datos reales."""
    respuesta = json.loads(Path(path).read_text(encoding="utf-8"))
    blocks = respuesta.get("Blocks", [])
    tablas = reconstruct_tables(blocks)
    print("Blocks: " + str(len(blocks)) + " | tablas detectadas: " + str(len(tablas)))
    tabla = elegir_tabla(tablas)
    header, filas = tabla[0], tabla[1:]
    print("\nHeaders (" + str(len(header)) + "):")
    for i, h in enumerate(header):
        marca = "  " if h in HEADERS else " <-- NO ESPERADO"
        print("  [" + str(i) + "] " + repr(h) + marca)
    faltan = [h for h in HEADERS if h not in header]
    if faltan:
        print("\nFALTAN headers: " + str(faltan))
    print("\nFilas (" + str(len(filas)) + "):")
    for f in filas:
        print("  " + str(f))
    anchos = {len(f) for f in filas}
    print("\nAnchos de fila: " + str(anchos) +
          (" OK" if anchos == {len(header)} else " <-- DESALINEADO"))


if __name__ == "__main__":
    if len(sys.argv) > 1:
        inspeccionar_json_real(sys.argv[1])
        sys.exit(0)

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    fallos = 0
    for t in tests:
        try:
            t()
            print("PASS  " + t.__name__)
        except AssertionError as e:
            fallos += 1
            print("FAIL  " + t.__name__ + ": " + str(e))
    print("\n" + str(len(tests) - fallos) + "/" + str(len(tests)) + " tests OK")
    sys.exit(1 if fallos else 0)
