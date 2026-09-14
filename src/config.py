"""Loads configs/mvp.yaml and exposes it as plain dataclasses."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = REPO_ROOT / "configs" / "mvp.yaml"


@dataclass
class Scope:
    agency: str
    borough: str
    categories: list[str]
    timezone: str


@dataclass
class SocrataSettings:
    domain: str
    dataset: str
    page_size: int
    requests_per_second: float
    max_retries: int
    app_token: str | None = None

    @property
    def json_url(self) -> str:
        return f"https://{self.domain}/resource/{self.dataset}.json"


@dataclass
class Config:
    scope: Scope
    socrata: SocrataSettings
    backfill_start: date
    backfill_end: date
    partition: str
    rolling_days: int
    raw_archive: Path
    fixtures: Path
    raw: dict = field(default_factory=dict)


def load_config(path: str | Path | None = None) -> Config:
    path = Path(path) if path else DEFAULT_CONFIG
    with open(path, "r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)

    scope = Scope(
        agency=raw["scope"]["agency"],
        borough=raw["scope"]["borough"],
        categories=list(raw["scope"]["categories"]),
        timezone=raw["scope"]["timezone"],
    )

    socrata = SocrataSettings(
        domain=raw["socrata"]["domain"],
        dataset=raw["socrata"]["dataset"],
        page_size=int(raw["socrata"]["page_size"]),
        requests_per_second=float(raw["socrata"]["requests_per_second"]),
        max_retries=int(raw["socrata"]["max_retries"]),
        app_token=os.environ.get("SOCRATA_APP_TOKEN") or None,
    )

    return Config(
        scope=scope,
        socrata=socrata,
        backfill_start=date.fromisoformat(str(raw["backfill"]["start_date"])),
        backfill_end=date.fromisoformat(str(raw["backfill"]["end_date"])),
        partition=raw["backfill"]["partition"],
        rolling_days=int(raw["incremental"]["rolling_days"]),
        raw_archive=REPO_ROOT / raw["paths"]["raw_archive"],
        fixtures=REPO_ROOT / raw["paths"]["fixtures"],
        raw=raw,
    )
