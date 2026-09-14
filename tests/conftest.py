"""Shared test setup.

Database tests need Postgres running (docker compose up -d). If it is not
reachable they skip rather than fail, so the pure-Python tests still run.
"""

from __future__ import annotations

import os
import uuid

import pytest
from psycopg import sql

from src.config import load_config
from src.db import REPO_ROOT, connect, run_sql_file

TABLES = [
    "forecast",
    "model_run",
    "daily_demand",
    "request_observation",
    "request_version",
    "service_request",
    "category",
    "area",
    "ingestion_run",
]


def _database_available() -> bool:
    try:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(scope="session")
def schema():
    if not _database_available():
        pytest.skip("Postgres is not reachable; integration tests require a local database")
    name = "ops_test_" + uuid.uuid4().hex
    previous = os.environ.get("PGOPTIONS")
    with connect(autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
    # An isolated search_path means TRUNCATE below cannot hit public data.
    os.environ["PGOPTIONS"] = (previous or "") + f" -c search_path={name}"
    try:
        for path in sorted((REPO_ROOT / "sql").glob("*.sql")):
            run_sql_file(path)
        yield
    finally:
        if previous is None:
            os.environ.pop("PGOPTIONS", None)
        else:
            os.environ["PGOPTIONS"] = previous
        with connect(autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name)))


@pytest.fixture
def db(schema):  # noqa: ANN001
    """A connection against an empty set of tables."""
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE")
        conn.commit()
        yield conn


@pytest.fixture
def cfg():
    return load_config()
