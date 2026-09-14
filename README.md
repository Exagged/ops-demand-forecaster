# End-to-end release: forecasting and capacity planning

Start with [RUNBOOK.md](RUNBOOK.md) for installation, database training, archive reproduction, and Streamlit launch. The complete application is `app.py`; the one-command evaluation/refit/publish workflow is `python -m src.pipeline`. The included demonstration forecasts September 1–7, 2026 from data through August 31.

---

# Operations Demand Forecaster and Capacity Planner

Forecasts daily 311 service request volume for one agency, one borough, and
three request categories, then turns that forecast into a staffing scenario a
manager can act on.

**The decision it supports:** a service operations manager deciding which days
next week need extra review capacity, and for which categories.

**What it does not claim:** optimal staffing, reduced resolution time, or any
causal effect. Staff rosters, handling effort, and outcomes are not in this
data. Throughput per person is an input the user types in, labeled as an
assumption everywhere it appears.

Status: Step 3 implemented. Ingestion, origin-safe features, seasonal baselines,
LightGBM selection/evaluation, and saved-model inference are available. The
four-week weekday mean remains the preferred policy on the measured comparison.
The interactive app remains future work. See [Step 3 guide](docs/step3.md).

## Stack

Python batch jobs, PostgreSQL with versioned SQL migrations, gzipped JSONL raw
archives, pytest, and a Streamlit app. One database, one scheduler, no
orchestration framework.

## Setup

Requires Docker and Python 3.11 or newer.

```bash
git clone <your-repo-url> && cd ops-demand-forecaster

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env

docker compose up -d          # Postgres on host port 5433
python -m src.migrate         # create the schema
```

## Run it

```bash
# Optional: committed sample, only in an empty/disposable database.
# Fixtures never certify live forecast coverage; blocked once live runs exist.
python -m src.ingest_311 --mode fixtures

# Real data. One month first to see it work, then the full range.
python -m src.ingest_311 --mode backfill --start 2026-08-01 --end 2026-08-31
python -m src.ingest_311 --mode backfill

# Daily job. Refetches a rolling 30 day window to catch edits to old rows.
python -m src.ingest_311 --mode incremental

pytest -q
```

Scope lives in `configs/mvp.yaml`. Change the agency, borough, or categories
there and the ingest, the aggregation, and the tests all follow.

## How the data model handles a mutable source

311 is a live dataset. Rows get edited after they are created, and an export
pulled today shows today's values for a request filed two years ago. Three
choices follow from that:

1. **Request identity is upserted, versions are appended.** `service_request`
   holds one row per `unique_key`. `request_version` gets a new row only when
   the hash of the tracked fields changes, so a daily refetch of unchanged rows
   adds no new hash while a late edit is still captured. Step 2 also stores
   each run’s observation in `request_observation`, preserving chronology
   when an existing payload hash reappears.
2. **Counts are distinct requests, never version rows.** A request edited five
   times is one request.
3. **A missing day is flagged, not zeroed.** `daily_demand.is_complete` is true
   only when a successful run covered that whole day and ran after the day
   ended. A day nobody fetched gets a zero count with the flag off, so training
   can drop it instead of learning a quiet Tuesday that never happened.

## Layout

```
configs/mvp.yaml            scope, date windows, API settings
sql/001_schema.sql          tables
sql/002_daily_demand.sql    the daily aggregation, as a function
src/config.py               config loader
src/normalize.py            typing, redaction of untracked fields, payload hashing
src/socrata.py              paging, throttling, backoff
src/ingest_311.py           the job: stage to a temp table, merge in SQL
src/migrate.py              applies sql/ in order
tests/                      idempotency, late edits, partial fetch recovery
docs/data_sources.md        endpoint, field mapping, known limits
data/fixtures/              20 real records with addresses removed
```

## Data caveats

See `docs/data_sources.md`. The short version: requests are reports, not
incidents; counts reflect who calls 311 as much as what is happening; a closed
request is not a solved problem; and historical rows carry revised values, so
any backtest on them is a revised-data backtest.

## Step 2: baseline backtest

After copying the updated code into your project (keep your existing `.env`):

```bash
python -m src.migrate
python -m pytest -q
python -m src.backtest --source database --output reports/baselines_database
```

Tests create and drop a uniquely named test schema; they do not truncate your
public tables. The database role must have CREATE permission on the database.
No DB reachable means integration tests skip, not pass.

The supplied archives can be evaluated without PostgreSQL or an API call:

```bash
python -m src.backtest --source archives --output reports/baselines_archive
```

The manifest records owner-attested coverage and checksums for this supplied
snapshot. It is not a substitute for a source reconciliation log. Full setup,
metrics, actual results, and limitations: [docs/step2.md](docs/step2.md).

## Step 3: trained challenger

```bash
python -m pip install -r requirements.txt
python -m pytest -q
python -m src.train --source database --output reports/step3_database
```

For offline reproduction, use `--source archives` and a new output folder.
The provided July 27–August 30 paired comparison scored the four-week mean at
8.59% WAPE, seasonal naive at 9.32%, and LightGBM at 10.50%. The full Step 2
benchmark remains 11.67% over its different, longer evaluation period. Do not
compare metrics across those date ranges as if they represented model gains.

Read [docs/step3.md](docs/step3.md) for the frozen selection protocol, feature
contract, exact results, saved-model replay, and verification limits.
