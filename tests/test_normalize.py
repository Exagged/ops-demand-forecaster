"""Tests that need no database."""

from __future__ import annotations

from datetime import date

import pytest

from src.config import load_config
from src.ingest_311 import month_partitions, where_clause
from src.normalize import SkippedRecord, normalize, payload_hash

BASE = {
    "unique_key": "70341016",
    "created_date": "2026-09-09T01:49:19.000",
    "agency": "NYPD",
    "complaint_type": "Noise - Residential",
    "descriptor": "Loud Music/Party",
    "status": "In Progress",
    "borough": "BROOKLYN",
}


def test_local_day_is_taken_from_the_wall_clock():
    # 23:40 local must stay on the 9th. A UTC conversion would push it to the
    # 10th and shift late-evening noise complaints into the wrong day.
    record = dict(BASE, created_date="2026-09-09T23:40:00.000")
    row = normalize(record)
    assert row["created_date"] == date(2026, 9, 9)


def test_hash_ignores_key_order_and_fetch_metadata():
    a = dict(BASE)
    b = {k: BASE[k] for k in reversed(list(BASE))}
    b["fetched_at"] = "2026-09-10T08:00:00"
    b["latitude"] = "40.66"
    assert payload_hash(a) == payload_hash(b)


def test_hash_changes_when_a_tracked_field_changes():
    closed = dict(BASE, status="Closed", closed_date="2026-09-09T02:10:00.000")
    assert payload_hash(BASE) != payload_hash(closed)


@pytest.mark.parametrize("missing", ["unique_key", "created_date", "complaint_type", "borough"])
def test_records_missing_required_fields_are_skipped(missing):
    record = dict(BASE)
    record.pop(missing)
    with pytest.raises(SkippedRecord):
        normalize(record)


def test_month_partitions_cover_the_range_without_gaps_or_overlap():
    parts = list(month_partitions(date(2024, 9, 15), date(2024, 12, 3)))
    assert parts[0] == (date(2024, 9, 15), date(2024, 9, 30))
    assert parts[-1] == (date(2024, 12, 1), date(2024, 12, 3))
    for earlier, later in zip(parts, parts[1:]):
        assert later[0] == earlier[1].fromordinal(earlier[1].toordinal() + 1)


def test_where_clause_upper_bound_is_exclusive():
    cfg = load_config()
    clause = where_clause(cfg, date(2025, 1, 1), date(2025, 1, 31))
    # The next partition starts at 2025-02-01, so this one must stop short of
    # it or requests filed on the 31st at 23:59 get counted twice.
    assert "created_date >= '2025-01-01T00:00:00'" in clause
    assert "created_date < '2025-02-01T00:00:00'" in clause
