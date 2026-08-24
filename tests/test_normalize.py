"""
Tests de la normalizacion al esquema de TeraTrip - Fase 3
Laboratorio 3 - TeraTrip

normalizar() es pura: no toca AWS. Los casos cubren lo que efectivamente puede
llegar desde un documento leido por OCR, no casos inventados.

Uso:
    python tests/test_normalize.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "lambdas" / "normalize"))

from lambda_function import (  # noqa: E402
    normalizar, parsear_monto, parsear_fecha, mapear_headers, SinDatos,
)

HEADERS = [
    "booking_id", "booking_date", "customer_id", "customer_name",
    "customer_country", "destination_city", "product_type", "status",
    "total_amount", "payment_amount", "payment_status", "payment_method",
]

# Las 8 filas del PDF de demo, tal como las devuelve la reconstruccion de Fase 2.
FILAS_DEMO = [
    ["B900001", "2026-08-25", "C000001", "Carlos Rodriguez", "Italy", "Madrid", "package", "confirmed", "1850.00", "1850.00", "approved", "credit_card"],
    ["B900002", "2026-08-25", "C003923", "Diego Gomez", "Brazil", "Salvador de Bahia", "flight", "confirmed", "740.50", "740.50", "approved", "debit_card"],
    ["B900003", "2026-08-26", "C012500", "Mateo Rivera", "Peru", "Cusco", "hotel", "confirmed", "980.00", "600.00", "approved", "bank_transfer"],
    ["B900004", "2026-08-26", "C045000", "Valentina Rojas", "Argentina", "Bariloche", "package", "cancelled", "1320.00", "0.00", "rejected", "credit_card"],
    ["B900005", "2026-08-27", "C900001", "Sofia Marchetti", "Uruguay", "Punta del Este", "hotel", "confirmed", "560.75", "560.75", "approved", "wallet"],
    ["B900006", "2026-08-27", "C900002", "Tomas Herrera", "Chile", "Valparaiso", "flight", "pending", "430.00", "0.00", "pending", "debit_card"],
    ["B900007", "2026-08-28", "C900003", "Camila Duarte", "Paraguay", "Iguazu", "package", "confirmed", "1590.90", "1590.90", "approved", "credit_card"],
    ["B900008", "2026-08-28", "C900004", "Bruno Salgado", "Brazil", "Florianopolis", "hotel", "confirmed", "820.00", "820.00", "approved", "cash"],
]


def test_documento_de_demo_completo():
    registros, rechazos = normalizar(HEADERS, FILAS_DEMO)
    assert len(registros) == 8, len(registros)
    assert rechazos == [], rechazos
    assert [r["booking_id"] for r in registros] == [f"B90000{i}" for i in range(1, 9)]
    assert registros[0]["total_amount"] == 1850.00
    assert registros[3]["status"] == "cancelled"
    assert registros[3]["payment_status"] == "rejected"
    # La normalizacion SI conserva payment_method aunque el pago no este aprobado.
    # Anularlo es responsabilidad del Glue Job, que es donde vive la regla.
    assert registros[3]["payment_method"] == "credit_card"


def test_no_calcula_campos_derivados():
    """El contrato dice que los derivados son del Glue Job. Si aparecieran aca,
    habria dos fuentes de verdad para confirmed_revenue."""
    registros, _ = normalizar(HEADERS, FILAS_DEMO)
    derivados = {"booking_year", "confirmed_revenue", "payment_gap", "is_confirmed",
                 "is_cancelled", "approved_paid_amount", "last_payment_method"}
    assert not (derivados & set(registros[0])), set(registros[0]) & derivados


def test_headers_con_otro_formato():
    """Textract puede devolver el header con mayusculas o espacios."""
    raros = ["Booking ID", "BOOKING-DATE", "Customer_Id", "Customer Name",
             "Customer Country", "Destination City", "Product Type", "Status",
             "Total Amount", "Payment Amount", "Payment Status", "Payment Method"]
    registros, rechazos = normalizar(raros, FILAS_DEMO[:1])
    assert len(registros) == 1 and not rechazos
    assert registros[0]["booking_id"] == "B900001"


def test_columnas_en_otro_orden():
    """El mapeo es por nombre, no por posicion."""
    orden = [11, 0, 5, 1, 7, 8, 9, 10, 2, 3, 4, 6]
    header = [HEADERS[i] for i in orden]
    fila = [FILAS_DEMO[0][i] for i in orden]
    registros, rechazos = normalizar(header, [fila])
    assert not rechazos
    assert registros[0]["booking_id"] == "B900001"
    assert registros[0]["destination_city"] == "Madrid"
    assert registros[0]["payment_method"] == "credit_card"


def test_columna_extra_se_ignora():
    registros, rechazos = normalizar(
        HEADERS + ["observaciones"], [FILAS_DEMO[0] + ["cliente VIP"]])
    assert not rechazos
    assert "observaciones" not in registros[0]


def test_montos_con_simbolos_y_separadores():
    assert parsear_monto("$1,850.00") == 1850.00     # en-US
    assert parsear_monto("1.850,00") == 1850.00      # es-AR
    assert parsear_monto("USD 740.50") == 740.50
    assert parsear_monto("1850") == 1850.00
    assert parsear_monto("0.00") == 0.00
    assert parsear_monto("  980,5 ") == 980.50
    assert parsear_monto("") is None
    assert parsear_monto("N/A") is None


def test_fechas_en_varios_formatos_salen_iso():
    assert parsear_fecha("2026-08-25") == "2026-08-25"
    assert parsear_fecha("25/08/2026") == "2026-08-25"
    assert parsear_fecha("25-08-2026") == "2026-08-25"
    assert parsear_fecha("agosto") is None
    assert parsear_fecha("") is None


def test_fila_sin_booking_id_se_descarta_con_motivo():
    mala = list(FILAS_DEMO[0])
    mala[0] = ""
    registros, rechazos = normalizar(HEADERS, [mala])
    assert registros == []
    assert len(rechazos) == 1
    assert "booking_id" in rechazos[0]["motivo"]


def test_status_fuera_de_dominio_se_descarta():
    """Un status invalido no rompe nada visiblemente, pero produce un
    confirmed_revenue silenciosamente equivocado. Por eso se descarta."""
    mala = list(FILAS_DEMO[0])
    mala[7] = "aprobada"
    registros, rechazos = normalizar(HEADERS, [mala])
    assert registros == []
    assert "status" in rechazos[0]["motivo"]


def test_payment_method_desconocido_queda_nulo_sin_descartar():
    """Solo alimenta last_payment_method: no justifica perder la reserva."""
    fila = list(FILAS_DEMO[0])
    fila[11] = "cripto"
    registros, rechazos = normalizar(HEADERS, [fila])
    assert not rechazos
    assert registros[0]["payment_method"] is None


def test_fila_vacia_no_cuenta_como_rechazo():
    registros, rechazos = normalizar(HEADERS, [FILAS_DEMO[0], [""] * 12])
    assert len(registros) == 1
    assert rechazos == []


def test_celda_faltante_no_rompe():
    """Si la fila viene mas corta que el header, los campos que sobran son None."""
    corta = FILAS_DEMO[0][:8]
    registros, rechazos = normalizar(HEADERS, [corta])
    assert registros == []
    assert "total_amount" in rechazos[0]["motivo"]


def test_headers_partidos_fallan_con_mensaje_util():
    """Es el modo de falla de los PDFs de ejemplo: 'customer_i' + 'd'."""
    partidos = ["booking_id", "booking_date", "customer_i", "customer_name",
                "customer_", "destination_", "product_type", "status",
                "total_amount", "payment_", "payment_", "payment_"]
    try:
        normalizar(partidos, FILAS_DEMO[:1])
    except SinDatos as e:
        assert "textract-raw" in str(e)
        assert "payment_status" in str(e)
    else:
        raise AssertionError("Deberia haber fallado con SinDatos")


def test_mapeo_de_alias_en_espanol():
    header = ["id_reserva", "fecha", "id_cliente", "cliente", "pais", "destino",
              "tipo_producto", "estado", "monto_total", "monto_pagado",
              "estado_pago", "metodo_pago"]
    mapa, desconocidos = mapear_headers(header)
    assert not desconocidos
    assert sorted(mapa.values()) == sorted(HEADERS)


if __name__ == "__main__":
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
