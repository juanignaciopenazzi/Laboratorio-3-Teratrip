-- =============================================================================
-- Validacion de la Fase 0 -- tabla sabana regenerada desde raw/
-- Laboratorio 3 - TeraTrip
--
-- Correr en Athena con WorkGroup wg-teratrip, database teratrip_db.
-- El resultado de la query 1 es el BASELINE contra el que se contrasta la Prueba 4.
-- =============================================================================


-- 1. BASELINE: cantidad total de reservas.
--    Anotar este numero en docs/schema_contract.md.
SELECT COUNT(*) AS total_reservas
FROM teratrip_db.booking_analytics;


-- 2. Integridad de la PK: no debe devolver ninguna fila.
--    Si devuelve algo, el dropDuplicates del job no cumplio y el anti-join
--    de la Fase 4 opera sobre una tabla ya corrupta.
SELECT booking_id, COUNT(*) AS repeticiones
FROM teratrip_db.booking_analytics
GROUP BY booking_id
HAVING COUNT(*) > 1;


-- 3. Sanity check financiero.
SELECT
    ROUND(SUM(total_amount), 2)          AS total_facturado,
    ROUND(SUM(confirmed_revenue), 2)     AS revenue_confirmado,
    ROUND(SUM(approved_paid_amount), 2)  AS cobrado_aprobado,
    ROUND(AVG(payment_coverage_pct), 2)  AS cobertura_promedio_pct
FROM teratrip_db.booking_analytics;


-- 4. Distribucion por status. Esperado segun los CSV de origen:
--    confirmed 375030 | cancelled 75000 | pending 49970
SELECT status, COUNT(*) AS reservas
FROM teratrip_db.booking_analytics
GROUP BY status
ORDER BY reservas DESC;


-- 5. Rango de fechas. Esperado: 2024-01-01 a 2026-12-31.
--    Valida que el CAST AS DATE no haya nuleado filas.
SELECT
    MIN(booking_date) AS fecha_min,
    MAX(booking_date) AS fecha_max,
    COUNT(*) FILTER (WHERE booking_date IS NULL) AS fechas_nulas
FROM teratrip_db.booking_analytics;


-- 6. Coherencia de los flags derivados: no debe devolver ninguna fila.
SELECT COUNT(*) AS flags_inconsistentes
FROM teratrip_db.booking_analytics
WHERE (status = 'confirmed'  AND NOT is_confirmed)
   OR (status <> 'confirmed' AND is_confirmed)
   OR (status = 'cancelled'  AND NOT is_cancelled)
   OR (status <> 'cancelled' AND is_cancelled);


-- 7. Nulos esperados vs inesperados.
--    airline es NULL en reservas de tipo 'hotel'; hotel_name en las de tipo 'flight'.
--    customer_name NULL indicaria bookings huerfanos (sin customer en el LEFT JOIN).
SELECT
    COUNT(*) FILTER (WHERE airline IS NULL)             AS sin_airline,
    COUNT(*) FILTER (WHERE hotel_name IS NULL)          AS sin_hotel,
    COUNT(*) FILTER (WHERE customer_name IS NULL)       AS bookings_huerfanos,
    COUNT(*) FILTER (WHERE last_payment_method IS NULL) AS sin_pago_aprobado
FROM teratrip_db.booking_analytics;


-- 8. Destinos disponibles. Esperado: 31 valores, de Cancun (23192) a Valparaiso (7068).
--    El destino elegido para el PDF de demo NO debe aparecer en esta lista.
SELECT destination_city, COUNT(*) AS reservas
FROM teratrip_db.booking_analytics
GROUP BY destination_city
ORDER BY reservas DESC;
