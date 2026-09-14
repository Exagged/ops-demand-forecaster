-- 002_daily_demand.sql
-- Rebuilds the daily_demand table for a date range.
--
-- Two rules matter here:
--   1. Count distinct requests, not version rows. A request that was edited
--      five times is still one request.
--   2. A day only gets is_complete = TRUE when a successful ingestion run
--      covered that whole day AND ran after the day had ended. Otherwise the
--      count stays but the flag stays FALSE, so a missing day is never
--      silently treated as a real zero.

BEGIN;

CREATE OR REPLACE FUNCTION rebuild_daily_demand(p_start DATE, p_end DATE)
RETURNS INTEGER AS $$
DECLARE
    rows_written INTEGER;
BEGIN
    DELETE FROM daily_demand
    WHERE demand_date BETWEEN p_start AND p_end;

    WITH scope_category AS (
        SELECT category_id FROM category WHERE in_scope
    ),
    scope_area AS (
        SELECT area_id FROM area WHERE in_scope
    ),
    calendar AS (
        SELECT d::DATE AS demand_date
        FROM generate_series(p_start, p_end, INTERVAL '1 day') AS d
    ),
    grid AS (
        SELECT c.demand_date, sc.category_id, sa.area_id
        FROM calendar c
        CROSS JOIN scope_category sc
        CROSS JOIN scope_area sa
    ),
    counted AS (
        SELECT
            sr.created_date AS demand_date,
            sr.category_id,
            sr.area_id,
            COUNT(DISTINCT sr.request_id) AS request_count
        FROM service_request sr
        WHERE sr.created_date BETWEEN p_start AND p_end
        GROUP BY 1, 2, 3
    ),
    covered AS (
        SELECT c.demand_date
        FROM calendar c
        WHERE EXISTS (
            SELECT 1
            FROM ingestion_run r
            WHERE r.status = 'ok'
              AND r.window_start IS NOT NULL
              AND r.window_end IS NOT NULL
              AND c.demand_date BETWEEN r.window_start AND r.window_end
              AND r.fetched_at >= (c.demand_date + INTERVAL '1 day')
        )
    )
    INSERT INTO daily_demand (demand_date, category_id, area_id, request_count, is_complete, rebuilt_at)
    SELECT
        g.demand_date,
        g.category_id,
        g.area_id,
        COALESCE(cnt.request_count, 0),
        (cov.demand_date IS NOT NULL),
        now()
    FROM grid g
    LEFT JOIN counted cnt
        ON cnt.demand_date = g.demand_date
       AND cnt.category_id = g.category_id
       AND cnt.area_id = g.area_id
    LEFT JOIN covered cov
        ON cov.demand_date = g.demand_date;

    GET DIAGNOSTICS rows_written = ROW_COUNT;
    RETURN rows_written;
END;
$$ LANGUAGE plpgsql;

COMMIT;
