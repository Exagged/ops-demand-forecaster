"""Ingestion behavior against a real Postgres.

Covers the three failure modes that would quietly corrupt the forecast:
repeat runs inflating counts, edits to old rows being lost, and a partial
fetch being treated as a complete day.
"""

from __future__ import annotations

from datetime import date

from src.ingest_311 import finish_run, merge_stage, stage_records, start_run, sync_scope_flags

RECORD = {
    "unique_key": "70341016",
    "created_date": "2026-09-09T01:49:19.000",
    "agency": "NYPD",
    "complaint_type": "Noise - Residential",
    "descriptor": "Loud Music/Party",
    "status": "In Progress",
    "borough": "BROOKLYN",
}


def load(conn, cfg, records, mode="backfill", window=(date(2026, 9, 9), date(2026, 9, 9)), status="ok"):
    """Run one full ingest cycle inside a single transaction, like the job does."""
    sync_scope_flags(conn, cfg)
    run_id = start_run(conn, mode, "test", "test", window[0], window[1])
    staged, skipped, checksum = stage_records(conn, records)
    new, changed = merge_stage(conn, run_id, cfg.scope.categories)
    finish_run(
        conn,
        run_id,
        status,
        rows_fetched=staged + skipped,
        rows_new=new,
        rows_changed=changed,
        source_count=staged + skipped,
        checksum=checksum,
    )
    conn.commit()
    return {"run_id": run_id, "staged": staged, "new": new, "changed": changed}


def counts(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM service_request")
        requests = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM request_version")
        versions = cur.fetchone()[0]
    return requests, versions


def test_repeat_run_is_idempotent(db, cfg):
    records = [RECORD, dict(RECORD, unique_key="70337971", descriptor="Loud Talking")]

    first = load(db, cfg, records)
    assert first["new"] == 2
    assert counts(db) == (2, 2)

    second = load(db, cfg, records)
    assert second["new"] == 0
    assert second["changed"] == 0
    # Same rows fetched again means no new request and no new version.
    assert counts(db) == (2, 2)


def test_edit_to_an_old_record_appends_a_version(db, cfg):
    load(db, cfg, [RECORD])

    closed = dict(RECORD, status="Closed", closed_date="2026-09-09T02:10:00.000")
    result = load(db, cfg, [closed])

    assert result["new"] == 0
    assert result["changed"] == 1

    requests, versions = counts(db)
    assert requests == 1, "an edit must not create a second request"
    assert versions == 2, "the earlier version must still be there"

    with db.cursor() as cur:
        cur.execute("SELECT current_status, closed_at FROM service_request")
        status, closed_at = cur.fetchone()
    assert status == "Closed"
    assert closed_at is not None


def test_duplicate_ids_inside_one_page_collapse_to_one_request(db, cfg):
    # An unordered page walk over a live dataset can hand back the same row
    # twice. That must not become two requests or blow up the upsert.
    result = load(db, cfg, [RECORD, dict(RECORD, status="Closed")])
    assert result["new"] == 1
    requests, versions = counts(db)
    assert requests == 1
    assert versions == 2


def test_out_of_scope_rows_load_but_do_not_reach_daily_demand(db, cfg):
    records = [
        RECORD,
        dict(RECORD, unique_key="70341834", borough="QUEENS"),
        dict(RECORD, unique_key="70342778", complaint_type="Illegal Fireworks"),
    ]
    load(db, cfg, records)

    with db.cursor() as cur:
        cur.execute("SELECT rebuild_daily_demand(%s, %s)", (date(2026, 9, 9), date(2026, 9, 9)))
        cur.execute("SELECT sum(request_count) FROM daily_demand")
        total = cur.fetchone()[0]
    # All three are stored, but only the in-scope Brooklyn noise request counts.
    assert counts(db)[0] == 3
    assert total == 1


def test_failed_run_does_not_mark_a_day_complete(db, cfg):
    load(db, cfg, [RECORD], status="failed")

    with db.cursor() as cur:
        cur.execute("SELECT rebuild_daily_demand(%s, %s)", (date(2026, 9, 9), date(2026, 9, 9)))
        cur.execute(
            "SELECT bool_or(is_complete) FROM daily_demand WHERE demand_date = %s",
            (date(2026, 9, 9),),
        )
        complete = cur.fetchone()[0]
    assert complete is False, "a partial fetch must not look like a finished day"


def test_rerun_after_a_partial_fetch_recovers_the_full_day(db, cfg):
    half = [RECORD]
    full = [RECORD, dict(RECORD, unique_key="70344266", descriptor="Banging/Pounding")]

    load(db, cfg, half, status="failed")
    result = load(db, cfg, full, status="ok")

    assert result["new"] == 1, "only the row we had not seen is new"
    assert counts(db)[0] == 2

    with db.cursor() as cur:
        cur.execute("SELECT rebuild_daily_demand(%s, %s)", (date(2026, 9, 9), date(2026, 9, 9)))
        cur.execute(
            "SELECT request_count, is_complete FROM daily_demand "
            "WHERE demand_date = %s AND request_count > 0",
            (date(2026, 9, 9),),
        )
        count, complete = cur.fetchone()
    assert count == 2
    assert complete is True


def test_missing_day_is_flagged_not_zeroed(db, cfg):
    load(db, cfg, [RECORD], window=(date(2026, 9, 9), date(2026, 9, 9)))

    with db.cursor() as cur:
        cur.execute("SELECT rebuild_daily_demand(%s, %s)", (date(2026, 9, 7), date(2026, 9, 9)))
        cur.execute(
            "SELECT demand_date, request_count, is_complete FROM daily_demand "
            "ORDER BY demand_date, category_id"
        )
        rows = cur.fetchall()

    uncovered = {r[0] for r in rows if not r[2]}
    # The 7th and 8th were never fetched. They get a zero count with the flag
    # off, so training can drop them instead of learning a fake quiet weekend.
    assert date(2026, 9, 7) in uncovered
    assert date(2026, 9, 8) in uncovered
    assert all(r[1] == 0 for r in rows if r[0] in uncovered)
