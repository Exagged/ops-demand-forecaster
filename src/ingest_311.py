"""Ingestion job for NYC 311 service requests.

Three modes:

  backfill      walk a date range in monthly partitions
  incremental   refetch a rolling window ending today
  fixtures      load the committed sample file, no network and no credentials

The load is the same in all three: stage the page, then merge in SQL.
Request identity is upserted, and a new version row is appended only when the
tracked payload hash changes. That means a daily refetch of unchanged rows
writes nothing, and an edit to a two-year-old row is still captured.

Usage:
    python -m src.ingest_311 --mode fixtures
    python -m src.ingest_311 --mode backfill --start 2024-09-01 --end 2024-10-31
    python -m src.ingest_311 --mode incremental
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Iterator

import psycopg

from src.config import Config, load_config
from src.db import connect
from src.normalize import SkippedRecord, normalize
from src.socrata import SocrataClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ingest")

STAGE_DDL = """
CREATE TEMP TABLE stage_request (
    ordinal               BIGINT,
    request_id            BIGINT,
    created_at            TIMESTAMP,
    created_date          DATE,
    closed_at             TIMESTAMP,
    agency                TEXT,
    complaint_type        TEXT,
    descriptor            TEXT,
    borough               TEXT,
    status                TEXT,
    resolution_updated_at TIMESTAMP,
    payload_hash          TEXT,
    payload               JSONB
) ON COMMIT DROP;
"""

COPY_SQL = """
COPY stage_request (
    ordinal, request_id, created_at, created_date, closed_at, agency,
    complaint_type, descriptor, borough, status, resolution_updated_at,
    payload_hash, payload
) FROM STDIN
"""


# --------------------------------------------------------------------------
# date helpers
# --------------------------------------------------------------------------

def month_partitions(start: date, end: date) -> Iterator[tuple[date, date]]:
    """Split a range into whole months. End of each slice is inclusive."""
    cursor = start.replace(day=1)
    while cursor <= end:
        if cursor.month == 12:
            next_month = date(cursor.year + 1, 1, 1)
        else:
            next_month = date(cursor.year, cursor.month + 1, 1)
        slice_start = max(cursor, start)
        slice_end = min(next_month - timedelta(days=1), end)
        yield slice_start, slice_end
        cursor = next_month


def where_clause(cfg: Config, start: date, end: date) -> str:
    """Half-open on the upper bound so a request at 23:59 on the last day is
    included and nothing is double counted at a partition boundary."""
    upper = end + timedelta(days=1)
    return (
        f"agency='{cfg.scope.agency}'"
        f" AND borough='{cfg.scope.borough}'"
        f" AND created_date >= '{start.isoformat()}T00:00:00'"
        f" AND created_date < '{upper.isoformat()}T00:00:00'"
    )


# --------------------------------------------------------------------------
# scope sync
# --------------------------------------------------------------------------

def sync_scope_flags(conn: psycopg.Connection, cfg: Config) -> None:
    """Make sure the in_scope flags match the config file.

    The config is the single source of truth. If a category is added to
    configs/mvp.yaml later, this picks it up without a manual update.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO area (level, source_label, display_name, in_scope)
            VALUES ('borough', %s, %s, TRUE)
            ON CONFLICT (level, source_label) DO UPDATE SET in_scope = TRUE
            """,
            (cfg.scope.borough, cfg.scope.borough.title()),
        )
        for label in cfg.scope.categories:
            cur.execute(
                """
                INSERT INTO category (source_label, display_name, in_scope)
                VALUES (%s, %s, TRUE)
                ON CONFLICT (source_label) DO UPDATE SET in_scope = TRUE
                """,
                (label, label),
            )
        cur.execute(
            "UPDATE category SET in_scope = FALSE WHERE NOT (source_label = ANY(%s))",
            (cfg.scope.categories,),
        )
        cur.execute(
            "UPDATE area SET in_scope = FALSE WHERE NOT (level = 'borough' AND source_label = %s)",
            (cfg.scope.borough,),
        )


# --------------------------------------------------------------------------
# run bookkeeping
# --------------------------------------------------------------------------

def start_run(
    conn: psycopg.Connection,
    mode: str,
    query: str,
    partition_key: str | None,
    window_start: date | None,
    window_end: date | None,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ingestion_run (mode, query, partition_key, window_start, window_end)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING run_id
            """,
            (mode, query, partition_key, window_start, window_end),
        )
        return cur.fetchone()[0]


def finish_run(
    conn: psycopg.Connection,
    run_id: int,
    status: str,
    rows_fetched: int = 0,
    rows_new: int = 0,
    rows_changed: int = 0,
    source_count: int | None = None,
    checksum: str | None = None,
    error_message: str | None = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE ingestion_run
               SET status = %s,
                   fetched_at = now(),
                   rows_fetched = %s,
                   rows_new = %s,
                   rows_changed = %s,
                   source_count = %s,
                   checksum = %s,
                   error_message = %s
             WHERE run_id = %s
            """,
            (
                status,
                rows_fetched,
                rows_new,
                rows_changed,
                source_count,
                checksum,
                error_message,
                run_id,
            ),
        )


# --------------------------------------------------------------------------
# staging and merge
# --------------------------------------------------------------------------

def stage_records(conn: psycopg.Connection, records: Iterable[dict]) -> tuple[int, int, str]:
    """COPY normalized records into a temp table. Returns (staged, skipped, checksum)."""
    with conn.cursor() as cur:
        cur.execute(STAGE_DDL)

    staged = 0
    skipped = 0
    digest = hashlib.sha256()

    with conn.cursor() as cur:
        with cur.copy(COPY_SQL) as copy:
            for ordinal, raw in enumerate(records):
                try:
                    row = normalize(raw)
                except SkippedRecord as exc:
                    skipped += 1
                    log.debug("Skipped record: %s", exc)
                    continue
                digest.update(row["payload_hash"].encode("utf-8"))
                copy.write_row(
                    [
                        ordinal,
                        row["request_id"],
                        row["created_at"],
                        row["created_date"],
                        row["closed_at"],
                        row["agency"],
                        row["complaint_type"],
                        row["descriptor"],
                        row["borough"],
                        row["status"],
                        row["resolution_updated_at"],
                        row["payload_hash"],
                        json.dumps(row["payload"], sort_keys=True),
                    ]
                )
                staged += 1

    return staged, skipped, digest.hexdigest()


def merge_stage(conn: psycopg.Connection, run_id: int, in_scope: list[str]) -> tuple[int, int]:
    """Merge the staging table into the real tables. Returns (new, changed)."""
    with conn.cursor() as cur:
        # Categories and areas we have not seen before. New ones default to
        # out of scope; sync_scope_flags owns the in_scope decision.
        cur.execute(
            """
            INSERT INTO category (source_label, display_name, in_scope)
            SELECT DISTINCT s.complaint_type, s.complaint_type, s.complaint_type = ANY(%s)
              FROM stage_request s
             WHERE s.complaint_type IS NOT NULL
            ON CONFLICT (source_label) DO NOTHING
            """,
            (in_scope,),
        )
        cur.execute(
            """
            INSERT INTO area (level, source_label, display_name, in_scope)
            SELECT DISTINCT 'borough', s.borough, initcap(s.borough), FALSE
              FROM stage_request s
             WHERE s.borough IS NOT NULL
            ON CONFLICT (level, source_label) DO NOTHING
            """
        )

        # One row per request. If the same id shows up twice in a run, the
        # later page wins.
        cur.execute(
            """
            WITH deduped AS (
                SELECT DISTINCT ON (request_id) *
                  FROM stage_request
                 ORDER BY request_id, ordinal DESC
            ),
            merged AS (
                INSERT INTO service_request (
                    request_id, created_at, created_date, closed_at, agency,
                    category_id, area_id, descriptor, current_status,
                    resolution_updated_at, first_seen_run, last_seen_run
                )
                SELECT d.request_id, d.created_at, d.created_date, d.closed_at, d.agency,
                       c.category_id, a.area_id, d.descriptor, d.status,
                       d.resolution_updated_at, %(run_id)s, %(run_id)s
                  FROM deduped d
                  JOIN category c ON c.source_label = d.complaint_type
                  JOIN area a ON a.level = 'borough' AND a.source_label = d.borough
                ON CONFLICT (request_id) DO UPDATE
                   SET created_at = EXCLUDED.created_at,
                       created_date = EXCLUDED.created_date,
                       agency = EXCLUDED.agency,
                       closed_at = EXCLUDED.closed_at,
                       category_id = EXCLUDED.category_id,
                       area_id = EXCLUDED.area_id,
                       descriptor = EXCLUDED.descriptor,
                       current_status = EXCLUDED.current_status,
                       resolution_updated_at = EXCLUDED.resolution_updated_at,
                       last_seen_run = EXCLUDED.last_seen_run,
                       updated_at = now()
                RETURNING (xmax = 0) AS is_new
            )
            SELECT count(*) FILTER (WHERE is_new) AS new_rows FROM merged
            """,
            {"run_id": run_id},
        )
        rows_new = cur.fetchone()[0] or 0

        # Append a version row only when the tracked payload actually differs
        # from every version we already hold for that request.
        cur.execute(
            """
            WITH deduped AS (
                SELECT DISTINCT ON (request_id, payload_hash)
                       request_id, payload_hash, payload
                  FROM stage_request
                 ORDER BY request_id, payload_hash, ordinal DESC
            ),
            inserted AS (
                INSERT INTO request_version (request_id, run_id, payload_hash, payload)
                SELECT d.request_id, %(run_id)s, d.payload_hash, d.payload
                  FROM deduped d
                  JOIN service_request sr ON sr.request_id = d.request_id
                ON CONFLICT (request_id, payload_hash) DO NOTHING
                RETURNING 1
            )
            SELECT count(*) FROM inserted
            """,
            {"run_id": run_id},
        )
        versions_written = cur.fetchone()[0] or 0
        # Keep run-level provenance even when the payload returns to an old hash.
        cur.execute(
            """INSERT INTO request_observation(request_id, run_id, payload)
               SELECT DISTINCT ON (request_id) request_id, %s, payload
               FROM stage_request ORDER BY request_id, ordinal DESC
               ON CONFLICT (request_id, run_id) DO UPDATE SET payload=EXCLUDED.payload""",
            (run_id,),
        )


    # Every brand new request writes its first version, so anything above that
    # count is an edit to a request we already had.
    rows_changed = max(versions_written - rows_new, 0)
    return rows_new, rows_changed


# --------------------------------------------------------------------------
# raw archive
# --------------------------------------------------------------------------

def archive_page(cfg: Config, run_id: int, page_number: int, page: list[dict]) -> None:
    """Keep the untouched response. Archives stay out of Git."""
    cfg.raw_archive.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    path = cfg.raw_archive / f"run{run_id:06d}_{stamp}_p{page_number:04d}.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for record in page:
            handle.write(json.dumps(record) + "\n")


# --------------------------------------------------------------------------
# modes
# --------------------------------------------------------------------------

def ingest_window(
    cfg: Config,
    client: SocrataClient,
    mode: str,
    start: date,
    end: date,
    partition_key: str | None = None,
    archive: bool = True,
) -> dict:
    where = where_clause(cfg, start, end)
    with connect() as conn:
        sync_scope_flags(conn, cfg)
        conn.commit()
        run_id = start_run(conn, mode, where, partition_key, start, end)
        conn.commit()

    log.info("Run %d: %s to %s", run_id, start, end)

    try:
        source_count = client.count(where)
        log.info("Run %d: source reports %d rows", run_id, source_count)

        pages: list[dict] = []
        for page_number, page in enumerate(client.iter_pages(where), start=1):
            if archive:
                archive_page(cfg, run_id, page_number, page)
            pages.extend(page)
            log.info("Run %d: page %d, %d rows", run_id, page_number, len(page))

        with connect() as conn:
            staged, skipped, checksum = stage_records(conn, pages)
            rows_new, rows_changed = merge_stage(conn, run_id, cfg.scope.categories)
            conn.commit()

        status = "ok"
        if skipped or staged != source_count or len({r.get("unique_key") for r in pages}) != source_count:
            status = "mismatch"
            log.warning(
                "Run %d: fetched %d rows but source counted %d",
                run_id,
                staged + skipped,
                source_count,
            )

        with connect() as conn:
            finish_run(
                conn,
                run_id,
                status,
                rows_fetched=staged + skipped,
                rows_new=rows_new,
                rows_changed=rows_changed,
                source_count=source_count,
                checksum=checksum,
            )
            conn.commit()

        log.info(
            "Run %d: %s. staged=%d skipped=%d new=%d changed=%d",
            run_id,
            status,
            staged,
            skipped,
            rows_new,
            rows_changed,
        )
        return {
            "run_id": run_id,
            "status": status,
            "staged": staged,
            "skipped": skipped,
            "new": rows_new,
            "changed": rows_changed,
            "source_count": source_count,
        }

    except Exception as exc:  # noqa: BLE001 - we want the reason on the run row
        with connect() as conn:
            finish_run(conn, run_id, "failed", error_message=str(exc)[:2000])
            conn.commit()
        log.error("Run %d failed: %s", run_id, exc)
        raise


def ingest_fixtures(cfg: Config) -> dict:
    """Load the committed sample. No network, no credentials, no archive."""
    with connect() as guard:
        if guard.execute("SELECT EXISTS (SELECT 1 FROM ingestion_run WHERE mode IN ('backfill','incremental'))").fetchone()[0]:
            raise ValueError('Fixture loading is blocked in a database with live runs. Use a separate fixture database.')

    records = []
    with open(cfg.fixtures, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    dates = [normalize(r)["created_date"] for r in records if _is_usable(r)]
    window_start = min(dates) if dates else None
    window_end = max(dates) if dates else None

    with connect() as conn:
        sync_scope_flags(conn, cfg)
        conn.commit()
        run_id = start_run(conn, "fixtures", "fixtures file", "fixtures", window_start, window_end)
        conn.commit()

        staged, skipped, checksum = stage_records(conn, records)
        rows_new, rows_changed = merge_stage(conn, run_id, cfg.scope.categories)
        finish_run(
            conn,
            run_id,
            "ok",
            rows_fetched=staged + skipped,
            rows_new=rows_new,
            rows_changed=rows_changed,
            source_count=staged + skipped,
            checksum=checksum,
        )
        conn.commit()

    log.info(
        "Fixtures loaded: staged=%d skipped=%d new=%d changed=%d",
        staged,
        skipped,
        rows_new,
        rows_changed,
    )
    return {
        "run_id": run_id,
        "status": "ok",
        "staged": staged,
        "skipped": skipped,
        "new": rows_new,
        "changed": rows_changed,
    }


def _is_usable(raw: dict) -> bool:
    try:
        normalize(raw)
        return True
    except SkippedRecord:
        return False


def rebuild_demand(start: date, end: date) -> int:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT rebuild_daily_demand(%s, %s)", (start, end))
            written = cur.fetchone()[0]
        conn.commit()
    log.info("daily_demand rebuilt: %d rows for %s to %s", written, start, end)
    return written


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest NYC 311 service requests")
    parser.add_argument("--mode", choices=["backfill", "incremental", "fixtures"], required=True)
    parser.add_argument("--config", default=None)
    parser.add_argument("--start", help="YYYY-MM-DD, overrides the config for backfill")
    parser.add_argument("--end", help="YYYY-MM-DD, overrides the config for backfill")
    parser.add_argument("--no-rebuild", action="store_true", help="skip the daily_demand rebuild")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)

    if args.mode == "fixtures":
        ingest_fixtures(cfg)
        if not args.no_rebuild:
            with connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT min(created_date), max(created_date) FROM service_request")
                    lo, hi = cur.fetchone()
            if lo and hi:
                rebuild_demand(lo, hi)
        return 0

    client = SocrataClient(
        json_url=cfg.socrata.json_url,
        page_size=cfg.socrata.page_size,
        requests_per_second=cfg.socrata.requests_per_second,
        max_retries=cfg.socrata.max_retries,
        app_token=cfg.socrata.app_token,
    )

    if args.mode == "backfill":
        start = date.fromisoformat(args.start) if args.start else cfg.backfill_start
        end = date.fromisoformat(args.end) if args.end else cfg.backfill_end
        for slice_start, slice_end in month_partitions(start, end):
            ingest_window(
                cfg,
                client,
                "backfill",
                slice_start,
                slice_end,
                partition_key=slice_start.strftime("%Y-%m"),
            )
    else:
        end = date.today()
        start = end - timedelta(days=cfg.rolling_days)
        ingest_window(cfg, client, "incremental", start, end, partition_key=end.isoformat())

    if not args.no_rebuild:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT min(created_date), max(created_date) FROM service_request")
                lo, hi = cur.fetchone()
        if lo and hi:
            rebuild_demand(lo, hi)

    return 0


if __name__ == "__main__":
    sys.exit(main())
