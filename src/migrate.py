"""Applies every file in sql/ in filename order.

The migration files are written to be safe to run again, so this doubles as a
"make sure the schema is current" command.

    python -m src.migrate
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from src.db import REPO_ROOT, run_sql_file

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("migrate")


def main() -> int:
    sql_dir = REPO_ROOT / "sql"
    files = sorted(sql_dir.glob("*.sql"))
    if not files:
        log.error("No .sql files found in %s", sql_dir)
        return 1
    for path in files:
        log.info("Applying %s", path.name)
        run_sql_file(path)
    log.info("Schema is up to date (%d files)", len(files))
    return 0


if __name__ == "__main__":
    sys.exit(main())
