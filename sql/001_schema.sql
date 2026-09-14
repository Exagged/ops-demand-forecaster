-- 001_schema.sql
-- Core tables for the 311 demand forecaster.
-- Safe to run more than once.

BEGIN;

-- One fetch job. Every row we load points back to the run that loaded it,
-- so a bad run can be traced and replayed.
CREATE TABLE IF NOT EXISTS ingestion_run (
    run_id          BIGSERIAL PRIMARY KEY,
    mode            TEXT        NOT NULL,          -- backfill | incremental | fixtures
    query           TEXT        NOT NULL,          -- the $where clause we sent
    partition_key   TEXT,                          -- e.g. 2025-03 for a monthly slice
    window_start    DATE,                          -- first created_date this run covers
    window_end      DATE,                          -- last created_date this run covers
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    fetched_at      TIMESTAMPTZ,
    rows_fetched    INTEGER     NOT NULL DEFAULT 0,
    rows_new        INTEGER     NOT NULL DEFAULT 0,
    rows_changed    INTEGER     NOT NULL DEFAULT 0,
    source_count    INTEGER,                       -- count query result for reconciliation
    checksum        TEXT,                          -- hash of the full page set
    status          TEXT        NOT NULL DEFAULT 'running',  -- running | ok | failed | mismatch
    error_message   TEXT
);

-- Request category. Source labels are strings that can be renamed, so we
-- keep a surrogate key and hold on to the label we actually saw.
CREATE TABLE IF NOT EXISTS category (
    category_id     SERIAL PRIMARY KEY,
    source_label    TEXT        NOT NULL UNIQUE,   -- complaint_type as delivered
    display_name    TEXT        NOT NULL,
    in_scope        BOOLEAN     NOT NULL DEFAULT FALSE,
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Geographic unit. Borough for the MVP; the level column leaves room for
-- community board later without a migration.
CREATE TABLE IF NOT EXISTS area (
    area_id         SERIAL PRIMARY KEY,
    level           TEXT        NOT NULL,          -- borough | community_board
    source_label    TEXT        NOT NULL,
    display_name    TEXT        NOT NULL,
    in_scope        BOOLEAN     NOT NULL DEFAULT FALSE,
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (level, source_label)
);

-- One source request, holding its current known state.
CREATE TABLE IF NOT EXISTS service_request (
    request_id      BIGINT      PRIMARY KEY,       -- unique_key from the source
    created_at      TIMESTAMP   NOT NULL,          -- local NYC wall clock, no conversion
    created_date    DATE        NOT NULL,          -- derived local day, the forecast grain
    closed_at       TIMESTAMP,
    agency          TEXT        NOT NULL,
    category_id     INTEGER     NOT NULL REFERENCES category (category_id),
    area_id         INTEGER     NOT NULL REFERENCES area (area_id),
    descriptor      TEXT,
    current_status  TEXT,
    resolution_updated_at TIMESTAMP,
    first_seen_run  BIGINT      NOT NULL REFERENCES ingestion_run (run_id),
    last_seen_run   BIGINT      NOT NULL REFERENCES ingestion_run (run_id),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_service_request_day
    ON service_request (created_date, category_id, area_id);

-- One distinct observed version of a request. We append a row only when the
-- payload hash changes, so a daily refetch of unchanged rows costs nothing.
CREATE TABLE IF NOT EXISTS request_version (
    version_id      BIGSERIAL PRIMARY KEY,
    request_id      BIGINT      NOT NULL REFERENCES service_request (request_id) ON DELETE CASCADE,
    run_id          BIGINT      NOT NULL REFERENCES ingestion_run (run_id),
    observed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    payload_hash    TEXT        NOT NULL,
    payload         JSONB       NOT NULL,
    UNIQUE (request_id, payload_hash)
);

CREATE INDEX IF NOT EXISTS idx_request_version_request
    ON request_version (request_id, observed_at);

-- One local date x category x area. Built by sql/002_daily_demand.sql.
-- is_complete is false when the day is not fully covered by a confirmed
-- ingestion run, which keeps a partial day out of training and evaluation.
CREATE TABLE IF NOT EXISTS daily_demand (
    demand_date     DATE        NOT NULL,
    category_id     INTEGER     NOT NULL REFERENCES category (category_id),
    area_id         INTEGER     NOT NULL REFERENCES area (area_id),
    request_count   INTEGER     NOT NULL,
    is_complete     BOOLEAN     NOT NULL DEFAULT FALSE,
    rebuilt_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (demand_date, category_id, area_id)
);

-- One trained artifact. Filled in during the modeling step.
CREATE TABLE IF NOT EXISTS model_run (
    model_run_id    BIGSERIAL PRIMARY KEY,
    model_name      TEXT        NOT NULL,
    training_cutoff DATE        NOT NULL,
    feature_hash    TEXT,
    config_hash     TEXT,
    artifact_path   TEXT,
    trained_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    notes           TEXT
);

-- One origin x target date x segment x model.
CREATE TABLE IF NOT EXISTS forecast (
    model_run_id    BIGINT      NOT NULL REFERENCES model_run (model_run_id) ON DELETE CASCADE,
    origin_date     DATE        NOT NULL,
    target_date     DATE        NOT NULL,
    category_id     INTEGER     NOT NULL REFERENCES category (category_id),
    area_id         INTEGER     NOT NULL REFERENCES area (area_id),
    horizon_days    SMALLINT    NOT NULL,
    point_forecast  NUMERIC(10, 3) NOT NULL,
    lower_80        NUMERIC(10, 3),
    upper_80        NUMERIC(10, 3),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (model_run_id, origin_date, target_date, category_id, area_id)
);

COMMIT;
