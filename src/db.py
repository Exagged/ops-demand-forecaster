"""Database connection helper. Everything reads DATABASE_URL from the env."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import psycopg
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_URL = "postgresql://forecaster:forecaster@localhost:5433/demand"


def database_url() -> str:
    load_dotenv(REPO_ROOT / ".env", override=False)
    return os.environ.get("DATABASE_URL", DEFAULT_URL)


@contextmanager
def connect(autocommit: bool = False) -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(database_url(), autocommit=autocommit)
    try:
        yield conn
    finally:
        conn.close()


def run_sql_file(path: str | Path) -> None:
    """Apply a migration file. The files are written to be re-runnable."""
    sql_text = Path(path).read_text(encoding="utf-8")
    with connect(autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(sql_text)
