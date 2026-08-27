"""
Generador del documento de prueba — Fase 1
Laboratorio 3 - TeraTrip

Produce el PDF que dispara el pipeline de ingesta. NO se usa ninguno de los 5 PDFs
de ejemplo: la consigna pide un documento propio.

Genera tres archivos:
    teratrip_reservas_demo.pdf      -> lote A, usado durante el desarrollo
    teratrip_reservas_demo_v2.pdf   -> lote A con otro eTag, para probar idempotencia
    teratrip_reservas_walkthrough.pdf -> lote B, reservado para la demo en vivo

Uso:
    pip install reportlab
    python src/pdf/generate_demo_pdf.py

--- Por que el layout es como es -------------------------------------------------

Los 5 PDFs de ejemplo tienen los headers PARTIDOS EN DOS LINEAS (customer_i/d,
payment_/amount, payment_/method): fuente 6.1pt en columnas demasiado angostas.
Textract reconstruye la tabla mapeando TABLE -> CELL -> WORD, y un header partido
llega como dos WORD en la misma celda, o peor, se desalinea contra la fila de datos.
Es el riesgo alto que el plan marca para toda la Parte A.

Por eso aca: landscape A4, anchos de columna calculados a partir del contenido real
y un assert que falla si algun header no entra en una linea.
"""

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer,
)

OUT_DIR = Path(__file__).parent

# --- Paleta TeraTrip (misma de la consigna) ---
PURPLE_DARK = colors.HexColor("#3b0764")
PURPLE_MID = colors.HexColor("#5b21b6")
GRAY_ROW = colors.HexColor("#faf7fc")

# 14 columnas. airline y hotel_name se agregaron despues de detectar que los
# registros ingresados por documento quedaban sin proveedor mientras si tenian
# product_type: un "package" sin aerolinea ni hotel es un dato incoherente
# contra el resto de la tabla.
#
# La regla la fija el dataset base, sin excepciones en 500.000 filas:
#   flight  -> solo airline
#   hotel   -> solo hotel_name
#   package -> AMBOS
HEADERS = [
    "booking_id", "booking_date", "customer_id", "customer_name",
    "customer_country", "destination_city", "product_type", "status",
    "airline", "hotel_name",
    "total_amount", "payment_amount", "payment_status", "payment_method",
]

# --- Diseno de los datos ---------------------------------------------------------
#
# Cada fila tiene un destino DISTINTO. Siete existen en el dataset base; el octavo,
# "Salvador de Bahia", NO existe entre los 31 destinos catalogados. Esa fila es el
# ancla de la Prueba 4 end-to-end: antes de la ingesta el agente responde 0, despues 1.
#
# C000001, C003923, C012500 y C045000 son customer_id REALES del dataset, con su
# nombre y pais exactos. Importa: el merge no joinea contra customers, toma esos
# campos del documento. Un nombre inventado dejaria al mismo customer_id con dos
# nombres distintos en la tabla sabana.
#
# C900001..C900004 son clientes nuevos.
#
# booking_id en serie B9000xx: el dataset base llega hasta B500000, no colisionan.

ROWS_LOTE_A = [
    # id        fecha         cliente     nombre              pais         destino             producto   estado       aerolinea             hotel                        total    pagado   pago_estado  metodo
    ["B900001", "2026-08-25", "C000001", "Carlos Rodriguez", "Italy",     "Madrid",           "package", "confirmed", "Latamundo Air",      "Gran Vía Suites",           "1850.00", "1850.00", "approved", "credit_card"],
    ["B900002", "2026-08-25", "C003923", "Diego Gomez",      "Brazil",    "Salvador de Bahia", "flight",  "confirmed", "Rio Plata Airlines", "",                          "740.50",  "740.50", "approved", "debit_card"],
    ["B900003", "2026-08-26", "C012500", "Mateo Rivera",     "Peru",      "Cusco",            "hotel",   "confirmed", "",                   "Inti Valley Lodge",         "980.00",  "600.00", "approved", "bank_transfer"],
    ["B900004", "2026-08-26", "C045000", "Valentina Rojas",  "Argentina", "Bariloche",        "package", "cancelled", "Patagonian Fly",     "Nahuel Lake Resort",        "1320.00",    "0.00", "rejected", "credit_card"],
    ["B900005", "2026-08-27", "C900001", "Sofia Marchetti",  "Uruguay",   "Punta del Este",   "hotel",   "confirmed", "",                   "Brava Beach Hotel",         "560.75",  "560.75", "approved", "wallet"],
    ["B900006", "2026-08-27", "C900002", "Tomas Herrera",    "Chile",     "Valparaiso",       "flight",  "pending",   "Pacifico Air",       "",                          "430.00",    "0.00", "pending",  "debit_card"],
    ["B900007", "2026-08-28", "C900003", "Camila Duarte",    "Paraguay",  "Iguazu",           "package", "confirmed", "Andes Air",          "Cataratas Garden Inn",      "1590.90", "1590.90", "approved", "credit_card"],
    ["B900008", "2026-08-28", "C900004", "Bruno Salgado",    "Brazil",    "Florianopolis",    "hotel",   "confirmed", "",                   "Ilha Norte Suites",         "820.00",  "820.00", "approved", "cash"],
]

# Lote B -- reservado para la Prueba 4 del walkthrough.
#
# Existe porque el lote A ya se ingesto durante el desarrollo: su destino ancla
# (Salvador de Bahia) quedo en 1 y el "antes = 0" dejo de ser reproducible. Una
# demo end-to-end necesita un ancla virgen.
#
# "Puerto Madryn" no figura entre los destinos del dataset. Es la fila que
# sostiene la Prueba 4: antes de la ingesta el agente responde 0, despues 1.
# booking_id en serie B91xxxx para no colisionar ni con el dataset base
# (llega a B500000) ni con el lote A (B9000xx).
ROWS_LOTE_B = [
    ["B910001", "2026-08-29", "C007777", "Tomas Flores",   "Peru",      "Puerto Madryn", "package", "confirmed", "Patagonian Fly",  "Golfo Nuevo Lodge",  "2140.00", "2140.00", "approved", "credit_card"],
    ["B910002", "2026-08-29", "C021000", "Agustin Gomez",  "Colombia",  "Lima",          "flight",  "confirmed", "Andina Jet",      "",                   "615.30",  "615.30", "approved", "debit_card"],
    ["B910003", "2026-08-30", "C038450", "Sofia Sanchez",  "Colombia",  "Orlando",       "hotel",   "confirmed", "",                "Sunshine Bay Hotel", "1275.00",  "800.00", "approved", "bank_transfer"],
    ["B910004", "2026-08-30", "C910001", "Lucas Ferreyra", "Argentina", "Rome",          "package", "cancelled", "Nova Airlines",   "Trastevere Palace",  "1980.50",    "0.00", "rejected", "credit_card"],
    ["B910005", "2026-08-31", "C910002", "Julieta Ponce",  "Uruguay",   "Salta",         "flight",  "confirmed", "Altura Airlines", "",                   "395.00",  "395.00", "approved", "wallet"],
    ["B910006", "2026-08-31", "C910003", "Martin Aguirre", "Chile",     "Aruba",         "hotel",   "confirmed", "",                "Palm Coast Resort",  "890.75",  "890.75", "approved", "cash"],
]

HEADER_FONT, HEADER_SIZE = "Helvetica-Bold", 7.5
BODY_FONT, BODY_SIZE = "Helvetica", 7.5
CELL_PAD = 4  # padding izq + der que aplica el TableStyle


def column_widths(rows):
    """Ancho por columna = el contenido mas ancho de esa columna + padding.

    Calculado con las metricas reales de la fuente, no a ojo: es lo que garantiza
    que ningun header se parta en dos lineas.
    """
    widths = []
    for i, header in enumerate(HEADERS):
        w = stringWidth(header, HEADER_FONT, HEADER_SIZE)
        for row in rows:
            w = max(w, stringWidth(row[i], BODY_FONT, BODY_SIZE))
        widths.append(w + CELL_PAD * 2 + 3)
    return widths


def assert_headers_fit(widths):
    """Falla si un header no entra en una linea. Es el chequeo que los PDFs de
    ejemplo no pasan."""
    for header, w in zip(HEADERS, widths):
        needed = stringWidth(header, HEADER_FONT, HEADER_SIZE)
        if needed > w - CELL_PAD * 2:
            raise AssertionError(
                f"El header '{header}' no entra en una linea "
                f"({needed:.1f}pt necesarios, {w - CELL_PAD * 2:.1f}pt disponibles). "
                f"Textract lo va a partir."
            )


def build(path, subtitulo, rows):
    widths = column_widths(rows)
    assert_headers_fit(widths)

    # La pagina se ensancha si la tabla no entra en A4 apaisado. Con 14 columnas
    # el ancho necesario supera los 842pt de A4, y ReportLab NO avisa: dibuja la
    # tabla igual y las ultimas columnas quedan fuera del area visible. Textract
    # entonces devuelve una tabla incompleta, y el fallo se descubre recien al
    # mirar los datos en Athena.
    #
    # Se prefiere ensanchar la pagina antes que achicar la fuente: el documento
    # se lee por OCR, no se imprime, y una fuente mas chica degrada la extraccion.
    total_w = sum(widths)
    page_w = max(landscape(A4)[0], total_w + 20 * mm)
    page_h = landscape(A4)[1]
    margin = (page_w - total_w) / 2

    doc = SimpleDocTemplate(
        str(path),
        pagesize=(page_w, page_h),
        leftMargin=margin, rightMargin=margin,
        topMargin=14 * mm, bottomMargin=14 * mm,
        title="TeraTrip - Nuevas reservas",
    )

    # leading y spaceAfter explicitos: con el spaceAfter por defecto el subtitulo
    # queda pegado al titulo y Textract puede leerlos como un solo bloque de texto.
    h1 = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=16, leading=20,
                        textColor=PURPLE_DARK, spaceAfter=10)
    h2 = ParagraphStyle("h2", fontName="Helvetica", fontSize=9, leading=12,
                        textColor=colors.HexColor("#2f2437"), spaceAfter=0)
    foot = ParagraphStyle("foot", fontName="Helvetica", fontSize=7,
                          textColor=colors.HexColor("#66556f"))

    table = Table([HEADERS] + rows, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PURPLE_DARK),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), HEADER_FONT),
        ("FONTSIZE", (0, 0), (-1, 0), HEADER_SIZE),
        ("FONTNAME", (0, 1), (-1, -1), BODY_FONT),
        ("FONTSIZE", (0, 1), (-1, -1), BODY_SIZE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, GRAY_ROW]),
        ("GRID", (0, 0), (-1, -1), 0.4, PURPLE_MID),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), CELL_PAD),
        ("RIGHTPADDING", (0, 0), (-1, -1), CELL_PAD),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))

    doc.build([
        Paragraph("TeraTrip - Alta de nuevas reservas", h1),
        Paragraph(subtitulo, h2),
        Spacer(1, 14),
        table,
        Spacer(1, 8),
        Paragraph(
            "Documento generado para el Laboratorio Final. Datos ficticios. "
            "Una fila por reserva; los importes estan expresados en USD.", foot),
    ])
    print(f"OK  {path.name}  ({len(rows)} reservas, {len(HEADERS)} columnas, "
          f"pagina {page_w:.0f}x{page_h:.0f}pt)")


if __name__ == "__main__":
    build(OUT_DIR / "teratrip_reservas_demo.pdf",
          "Lote de alta - 8 reservas", ROWS_LOTE_A)
    # Mismos booking_id, distinto eTag: subirlo no debe cambiar el conteo.
    build(OUT_DIR / "teratrip_reservas_demo_v2.pdf",
          "Lote de alta - 8 reservas (reenvio del mismo lote)", ROWS_LOTE_A)
    # Lote B: NO ingestarlo antes del walkthrough. Su ancla debe estar en 0.
    build(OUT_DIR / "teratrip_reservas_walkthrough.pdf",
          "Lote de alta - 6 reservas", ROWS_LOTE_B)
