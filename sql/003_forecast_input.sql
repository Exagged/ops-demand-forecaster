-- Read-only forecasting view: recover the latest successful LIVE observation.
-- A fixture or failed run must never override the analytical input.
BEGIN;
-- Record each new run's observation, including a return to an earlier hash.
-- Legacy request_version stores only distinct hashes and loses that chronology.
CREATE TABLE IF NOT EXISTS request_observation (
    request_id BIGINT NOT NULL REFERENCES service_request(request_id) ON DELETE CASCADE,
    run_id BIGINT NOT NULL REFERENCES ingestion_run(run_id),
    payload JSONB NOT NULL,
    PRIMARY KEY (request_id, run_id)
);
CREATE OR REPLACE VIEW forecast_live_request AS
WITH candidates AS (
    SELECT o.request_id, r.started_at, 9223372036854775806::bigint AS tie_break, o.payload
    FROM request_observation o JOIN ingestion_run r USING (run_id)
    WHERE r.mode IN ('backfill', 'incremental') AND r.status = 'ok'
      AND r.source_count = r.rows_fetched
    UNION ALL
    SELECT v.request_id, r.started_at, v.version_id AS tie_break, v.payload
    FROM request_version v JOIN ingestion_run r USING (run_id)
    WHERE r.mode IN ('backfill', 'incremental') AND r.status = 'ok'
      AND r.source_count = r.rows_fetched
    UNION ALL
    -- If the same hash was first seen in fixtures, no live version was appended.
    -- The latest live service_request is still an attested live observation.
    SELECT s.request_id, r.started_at, 9223372036854775807::bigint,
           jsonb_build_object('created_date', s.created_at, 'agency', s.agency,
                              'complaint_type', c.source_label, 'borough', a.source_label)
    FROM service_request s
    JOIN ingestion_run r ON r.run_id = s.last_seen_run
    JOIN category c USING (category_id) JOIN area a USING (area_id)
    WHERE r.mode IN ('backfill', 'incremental') AND r.status = 'ok'
      AND r.source_count = r.rows_fetched
), latest AS (
    SELECT DISTINCT ON (request_id) request_id, payload
    FROM candidates ORDER BY request_id, started_at DESC, tie_break DESC
)
SELECT request_id, (payload->>'created_date')::timestamp::date AS demand_date,
       trim(payload->>'agency') AS agency,
       trim(payload->>'complaint_type') AS category,
       upper(trim(payload->>'borough')) AS area
FROM latest;
COMMIT;

-- Legacy display grid excludes fixtures too. Forecasting applies stricter scope coverage in demand.py.
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
            fl.demand_date AS demand_date,
            cc.category_id,
            aa.area_id,
            COUNT(DISTINCT fl.request_id) AS request_count
        FROM forecast_live_request fl
        JOIN category cc ON cc.source_label = fl.category
        JOIN area aa ON aa.level = 'borough' AND aa.source_label = fl.area
        WHERE fl.demand_date BETWEEN p_start AND p_end
        GROUP BY 1, 2, 3
    ),
    covered AS (
        SELECT c.demand_date
        FROM calendar c
        WHERE EXISTS (
            SELECT 1
            FROM ingestion_run r
            WHERE r.status = 'ok'
              AND r.mode IN ('backfill', 'incremental')
              AND r.source_count = r.rows_fetched
              AND r.window_start IS NOT NULL
              AND r.window_end IS NOT NULL
              AND c.demand_date BETWEEN r.window_start AND r.window_end
              AND r.fetched_at >= ((c.demand_date + INTERVAL '1 day') AT TIME ZONE 'America/New_York')
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

-- Refresh any existing materialized display grid so old fixture flags vanish.
DO $$
DECLARE lo DATE; hi DATE;
BEGIN
    SELECT min(demand_date), max(demand_date) INTO lo, hi FROM daily_demand;
    IF lo IS NOT NULL THEN PERFORM rebuild_daily_demand(lo, hi); END IF;
END $$;
