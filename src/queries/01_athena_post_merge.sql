-- =============================================================================
-- Validacion del merge -- Fase 4
-- Laboratorio 3 - TeraTrip
--
-- Correr DESPUES de que el Glue Job de merge incorpore el documento de demo.
-- Los valores esperados salen de tests/golden_questions.md.
--
-- No hace falta volver a correr el Crawler: el merge reescribe el mismo archivo
-- con el mismo esquema, y la tabla del Data Catalog apunta al prefijo, no al
-- contenido. Si alguna de estas queries falla, el problema es de datos, no de
-- catalogo.
-- =============================================================================


-- 1. Conteo total. Esperado: 500.008 (baseline 500.000 + 8 del documento).
SELECT COUNT(*) AS total_reservas
FROM teratrip_db.booking_analytics;


-- 2. La PK sigue siendo unica. No debe devolver ninguna fila.
--    Es la verificacion directa del criterio de aprobacion
--    "los registros se incorporan sin duplicar reservas existentes".
SELECT booking_id, COUNT(*) AS repeticiones
FROM teratrip_db.booking_analytics
GROUP BY booking_id
HAVING COUNT(*) > 1;


-- 3. Los 8 registros nuevos con todos sus campos derivados.
--    Contrastar fila por fila contra la tabla de tests/golden_questions.md.
SELECT
    booking_id,
    booking_date,
    booking_year,
    booking_month,
    destination_city,
    product_type,
    status,
    customer_id,
    customer_name,
    customer_country,
    airline,
    hotel_name,
    total_amount,
    confirmed_revenue,
    approved_paid_amount,
    payment_gap,
    ROUND(payment_coverage_pct, 2) AS payment_coverage_pct,
    is_confirmed,
    is_cancelled,
    last_payment_method
FROM teratrip_db.booking_analytics
WHERE booking_id LIKE 'B90%'
ORDER BY booking_id;


-- 4. Totales del lote nuevo.
--    Esperado: 8 filas | total 8292.15 | confirmed 6542.15 | aprobado 6162.15 | gap 2130.00
SELECT
    COUNT(*)                              AS filas,
    ROUND(SUM(total_amount), 2)           AS total_amount,
    ROUND(SUM(confirmed_revenue), 2)      AS confirmed_revenue,
    ROUND(SUM(approved_paid_amount), 2)   AS approved_paid_amount,
    ROUND(SUM(payment_gap), 2)            AS payment_gap
FROM teratrip_db.booking_analytics
WHERE booking_id LIKE 'B90%';


-- 5. La regla que mas facil se rompe al portar la logica del Lab 2.
--    B900004 (pago rejected) y B900006 (pago pending) deben tener
--    last_payment_method NULL, aunque el documento SI traia payment_method.
--    Esperado: exactamente esas dos filas.
SELECT booking_id, status, approved_paid_amount, last_payment_method
FROM teratrip_db.booking_analytics
WHERE booking_id LIKE 'B90%'
  AND last_payment_method IS NULL
ORDER BY booking_id;


-- 6. airline y hotel_name deben venir NULL en los 8: el documento no trae
--    flight_id ni hotel_id contra los que joinear. Limitacion conocida,
--    declarada en la Knowledge Base. Esperado: 8.
SELECT COUNT(*) AS con_ambos_nulos
FROM teratrip_db.booking_analytics
WHERE booking_id LIKE 'B90%'
  AND airline IS NULL
  AND hotel_name IS NULL;


-- 7. Ancla de la Prueba 4 end-to-end.
--    Antes del merge: 0. Despues: 1.
SELECT COUNT(*) AS reservas_salvador
FROM teratrip_db.booking_analytics
WHERE destination_city = 'Salvador de Bahia';


-- 8. El dataset base quedo intacto: los agregados globales solo se movieron
--    en el delta del lote nuevo.
--    Esperado: total 443.707.689,72 | confirmado 332.793.336,98
SELECT
    ROUND(SUM(total_amount), 2)       AS total_facturado,
    ROUND(SUM(confirmed_revenue), 2)  AS revenue_confirmado
FROM teratrip_db.booking_analytics;


-- 9. Distribucion por status. Esperado: confirmed 375.036 | cancelled 75.001 | pending 49.971
SELECT status, COUNT(*) AS reservas
FROM teratrip_db.booking_analytics
GROUP BY status
ORDER BY reservas DESC;
