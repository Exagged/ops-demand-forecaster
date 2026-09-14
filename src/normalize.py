"""Turns a raw 311 JSON record into the typed shape the database expects.

Two things here are load-bearing:

1. TRACKED_FIELDS decides what counts as a change. The source adds and renames
   columns over time, and fetch metadata changes on every run, so hashing the
   whole raw blob would flag every row as edited every day.

2. created_date in the source has no timezone marker. The values are already
   local NYC wall-clock time, so we parse them naive and derive the local day
   directly. Converting to UTC first would shift late-evening requests into the
   next day and quietly bias every weekday feature.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

# Fields that describe the request itself. Anything outside this set is
# archived in the payload but does not make a row look "changed".
TRACKED_FIELDS = (
    "unique_key",
    "created_date",
    "closed_date",
    "agency",
    "agency_name",
    "complaint_type",
    "descriptor",
    "status",
    "borough",
    "community_board",
    "resolution_action_updated_date",
    "open_data_channel_type",
)

REQUIRED_FIELDS = ("unique_key", "created_date", "complaint_type", "borough")


class SkippedRecord(Exception):
    """Raised when a record cannot be used, with the reason attached."""


def parse_timestamp(value: str | None) -> datetime | None:
    """Parse a Socrata floating timestamp. Returns None for blanks."""
    if value in (None, "", "N/A"):
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1]
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def tracked_subset(raw: dict) -> dict:
    """The slice of the record we treat as its identity for change detection."""
    return {key: raw.get(key) for key in TRACKED_FIELDS if raw.get(key) is not None}


def payload_hash(raw: dict) -> str:
    """Stable hash over the tracked fields only. Key order does not matter."""
    canonical = json.dumps(tracked_subset(raw), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def normalize(raw: dict) -> dict:
    """Return a typed row ready for the staging table.

    Raises SkippedRecord when a required field is missing, so the caller can
    count it rather than writing a half-formed row.
    """
    for field_name in REQUIRED_FIELDS:
        if not raw.get(field_name):
            raise SkippedRecord(f"missing {field_name}")

    created_at = parse_timestamp(raw["created_date"])
    if created_at is None:
        raise SkippedRecord("unparseable created_date")

    try:
        request_id = int(raw["unique_key"])
    except (TypeError, ValueError) as exc:
        raise SkippedRecord("non-numeric unique_key") from exc

    return {
        "request_id": request_id,
        "created_at": created_at,
        "created_date": created_at.date(),
        "closed_at": parse_timestamp(raw.get("closed_date")),
        "agency": str(raw["agency"]).strip() if raw.get("agency") else "UNKNOWN",
        "complaint_type": str(raw["complaint_type"]).strip(),
        "descriptor": (str(raw["descriptor"]).strip() if raw.get("descriptor") else None),
        "borough": str(raw["borough"]).strip().upper(),
        "status": (str(raw["status"]).strip() if raw.get("status") else None),
        "resolution_updated_at": parse_timestamp(raw.get("resolution_action_updated_date")),
        "payload_hash": payload_hash(raw),
        "payload": tracked_subset(raw),
    }
