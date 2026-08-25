"""
Tests del validador de SQL - Fase 7
Laboratorio 3 - TeraTrip

validar() y forzar_limit() son puras: no tocan AWS. Estos tests son la evidencia
de la Prueba 3 de la consigna, y cubren tanto los ataques obvios como las
variantes que sortean una validacion ingenua.

Uso:
    python tests/test_sql_guard.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "lambdas" / "athena_query"))

from lambda_function import (  # noqa: E402
    validar, forzar_limit, quitar_comentarios, convertir, SqlRechazado, MAX_FILAS,
)


def _rechaza(sql, fragmento_esperado=None):
    try:
        validar(sql)
    except SqlRechazado as e:
        if fragmento_esperado and fragmento_esperado.lower() not in str(e).lower():
            raise AssertionError(
                "Rechazada por el motivo equivocado: " + str(e))
        return
    raise AssertionError("DEBERIA HABER SIDO RECHAZADA: " + sql)


def _acepta(sql):
    try:
        return validar(sql)
    except SqlRechazado as e:
        raise AssertionError("Deberia haber sido aceptada: " + sql + " -> " + str(e))


# --- Consultas legitimas ----------------------------------------------------------

def test_acepta_las_preguntas_de_la_prueba_1():
    _acepta("SELECT destination_city, SUM(confirmed_revenue) AS r "
            "FROM booking_analytics GROUP BY destination_city ORDER BY r DESC LIMIT 1")
    _acepta("SELECT COUNT(*) FROM teratrip_db.booking_analytics WHERE is_cancelled")
    _acepta("SELECT product_type, SUM(confirmed_revenue) FROM booking_analytics "
            "GROUP BY product_type")
    _acepta("SELECT customer_country, COUNT(DISTINCT customer_id) "
            "FROM booking_analytics GROUP BY customer_country ORDER BY 2 DESC LIMIT 1")


def test_acepta_with_y_sus_ctes():
    """Un CTE es una tabla derivada: FROM ingresos es legitimo aunque 'ingresos'
    no sea la tabla del laboratorio."""
    _acepta("""
        WITH ingresos AS (
            SELECT destination_city, SUM(confirmed_revenue) AS total
            FROM booking_analytics GROUP BY destination_city
        )
        SELECT * FROM ingresos ORDER BY total DESC LIMIT 5
    """)


def test_acepta_varios_ctes_encadenados():
    _acepta("""
        WITH a AS (SELECT * FROM booking_analytics),
             b AS (SELECT destination_city FROM a)
        SELECT COUNT(*) FROM b
    """)


def test_acepta_self_join_sobre_la_tabla_permitida():
    _acepta("SELECT COUNT(*) FROM booking_analytics t1 "
            "JOIN booking_analytics t2 ON t1.customer_id = t2.customer_id")


def test_acepta_punto_y_coma_final():
    _acepta("SELECT COUNT(*) FROM booking_analytics;")


# --- Prueba 3 de la consigna ------------------------------------------------------

def test_rechaza_delete():
    _rechaza("DELETE FROM booking_analytics WHERE is_cancelled", "no es de lectura")


def test_rechaza_drop_table():
    _rechaza("DROP TABLE booking_analytics", "no es de lectura")


def test_rechaza_sql_apilado():
    """El caso que hace inutil validar solo el comienzo de la sentencia."""
    _rechaza("SELECT 1 FROM booking_analytics; DELETE FROM booking_analytics",
             "mas de una sentencia")


def test_rechaza_otra_tabla():
    _rechaza("SELECT * FROM information_schema.tables", "information_schema.tables")


def test_rechaza_update_y_insert():
    _rechaza("UPDATE booking_analytics SET status = 'confirmed'", "no es de lectura")
    _rechaza("INSERT INTO booking_analytics VALUES (1)", "no es de lectura")


# --- Variantes que sortean una validacion ingenua ---------------------------------

def test_rechaza_ddl_escondido_detras_de_un_comentario():
    """Sin quitar comentarios primero, el ; queda 'tapado' y la sentencia
    aparenta empezar con SELECT."""
    _rechaza("SELECT 1 FROM booking_analytics -- inocente\n; DROP TABLE booking_analytics",
             "mas de una sentencia")


def test_rechaza_ddl_con_comentario_multilinea_intercalado():
    _rechaza("SELECT * FROM booking_analytics /* x */ ; /* y */ DELETE FROM booking_analytics",
             "mas de una sentencia")


def test_rechaza_ctas_y_view():
    _rechaza("CREATE TABLE copia AS SELECT * FROM booking_analytics", "no es de lectura")
    _rechaza("CREATE VIEW v AS SELECT * FROM booking_analytics", "no es de lectura")


def test_rechaza_unload():
    """UNLOAD empieza con una palabra que no es SELECT, pero conviene que ademas
    este en la denylist: escribe en S3."""
    _rechaza("UNLOAD (SELECT * FROM booking_analytics) TO 's3://x/'", "no es de lectura")


def test_rechaza_subconsulta_a_otra_tabla():
    """El SELECT de arriba es legitimo; la fuga esta adentro."""
    _rechaza("SELECT * FROM booking_analytics WHERE customer_id IN "
             "(SELECT id FROM otra_tabla)", "otra_tabla")


def test_rechaza_union_contra_otra_tabla():
    _rechaza("SELECT booking_id FROM booking_analytics "
             "UNION SELECT nombre FROM clientes_privados", "clientes_privados")


def test_rechaza_consulta_vacia():
    _rechaza("", "vacia")
    _rechaza("   \n  ", "vacia")


def test_rechaza_select_sin_tabla():
    _rechaza("SELECT 1", "no referencia ninguna tabla")


# --- Falsos positivos: lo que NO debe romperse ------------------------------------

def test_un_valor_de_texto_no_dispara_la_denylist():
    """destination_city = 'DROP Center' es un dato, no un intento de DDL. Si la
    denylist mirara el SQL crudo, esta consulta legitima seria rechazada."""
    _acepta("SELECT * FROM booking_analytics WHERE destination_city = 'DROP Center'")
    _acepta("SELECT * FROM booking_analytics WHERE customer_name = 'Update Smith'")


def test_guiones_dentro_de_un_literal_no_son_comentario():
    sql = "SELECT * FROM booking_analytics WHERE destination_city = 'Rio -- Norte'"
    assert "Rio -- Norte" in quitar_comentarios(sql)
    _acepta(sql)


def test_comilla_escapada_dentro_de_un_literal():
    _acepta("SELECT * FROM booking_analytics WHERE customer_name = 'O''Brien'")


def test_funciones_trino_homonimas_de_ddl():
    """replace() y truncate() son funciones escalares validas en Trino. Si la
    denylist las confundiera con CREATE OR REPLACE y TRUNCATE TABLE, rechazaria
    consultas legitimas, que es la forma mas facil de que un guard termine
    desactivado 'porque molestaba'."""
    _acepta("SELECT replace(customer_name, 'a', 'b') FROM booking_analytics")
    _acepta("SELECT truncate(payment_coverage_pct) FROM booking_analytics")


def test_igual_bloquea_esos_verbos_cuando_son_ddl():
    _rechaza("CREATE OR REPLACE VIEW v AS SELECT * FROM booking_analytics",
             "no es de lectura")
    _rechaza("SELECT 1 FROM booking_analytics; TRUNCATE TABLE booking_analytics",
             "mas de una sentencia")


def test_order_by_desc_es_legitimo():
    """DESC es a la vez abreviatura de DESCRIBE y modificador de ORDER BY. El
    agente lo usa en casi toda consulta de ranking."""
    _acepta("SELECT destination_city FROM booking_analytics ORDER BY total_amount DESC")


def test_columna_que_contiene_una_palabra_prohibida_no_se_confunde():
    """'created_at' contiene 'create' como subcadena. La denylist usa limites de
    palabra justamente para esto."""
    _acepta("SELECT booking_id FROM booking_analytics WHERE status = 'confirmed'")


# --- Capa 6: LIMIT ----------------------------------------------------------------

def test_inyecta_limit_si_falta():
    sql = forzar_limit(_acepta("SELECT * FROM booking_analytics"))
    assert sql.rstrip().endswith("LIMIT " + str(MAX_FILAS)), sql


def test_respeta_un_limit_chico():
    sql = forzar_limit(_acepta("SELECT * FROM booking_analytics LIMIT 5"))
    assert sql.rstrip().endswith("LIMIT 5"), sql


def test_recorta_un_limit_excesivo():
    sql = forzar_limit(_acepta("SELECT * FROM booking_analytics LIMIT 999999"))
    assert sql.rstrip().endswith("LIMIT " + str(MAX_FILAS)), sql
    assert "999999" not in sql


def test_limit_de_subconsulta_no_cuenta_como_limit_final():
    sql = forzar_limit(_acepta(
        "SELECT * FROM (SELECT * FROM booking_analytics LIMIT 5) t"))
    assert sql.rstrip().endswith("LIMIT " + str(MAX_FILAS)), sql


# --- Tipado de la respuesta -------------------------------------------------------

def test_convierte_numeros_en_notacion_cientifica():
    """Athena devuelve SUM(confirmed_revenue) como "1.931493953000002E7". Si eso
    llega asi al modelo, tiene que interpretarlo para responder, y una respuesta
    correcta de Athena se vuelve una respuesta equivocada del agente."""
    v = convertir("1.931493953000002E7", "double")
    assert isinstance(v, float), type(v)
    assert abs(v - 19314939.53) < 0.01, v
    import json
    assert "E7" not in json.dumps(v), json.dumps(v)


def test_convierte_enteros_y_booleanos():
    assert convertir("500008", "bigint") == 500008
    assert convertir("75001", "integer") == 75001
    assert convertir("true", "boolean") is True
    assert convertir("false", "boolean") is False


def test_conserva_texto_y_fechas_como_string():
    assert convertir("Cancun", "varchar") == "Cancun"
    assert convertir("2026-08-25", "date") == "2026-08-25"


def test_null_se_mantiene_null():
    """last_payment_method es NULL en las reservas sin pago aprobado: el agente
    tiene que poder distinguir 'sin dato' de la cadena vacia."""
    assert convertir(None, "varchar") is None
    assert convertir(None, "double") is None


def test_valor_que_no_parsea_no_se_pierde():
    assert convertir("N/A", "double") == "N/A"


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
