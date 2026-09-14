# Step 2: baselines and rolling-origin backtest

## Install into the existing project

The updated ZIP is the complete project, including the original raw archives.
Extract it to a separate folder, then copy its code/config/docs/tests into your
existing project, retaining your existing `.env` and `.venv`. No database dump
is included; your Docker volume remains the live data store. Do not run
`docker compose down -v`.

From the project root in your active Python environment:

```bash
python -m pip install -r requirements.txt
python -m src.migrate
python -m pytest -q
python -m src.backtest --source database --output reports/baselines_database
```

Migration 003 adds `request_observation` and `forecast_live_request`, replaces
the legacy rebuild function, and refreshes an existing daily display grid.
Run migrations before running updated ingestion. No re-backfill is required.
The backtest performs a read-only repeatable-read database transaction and
writes CSV/JSON reports. It does not populate the forecast/model_run tables.

To reproduce the included results without PostgreSQL or network access:

```bash
python -m src.backtest --source archives --output reports/baselines_archive
```

The archive route verifies page checksums, row counts, unique IDs, scope, and
partition dates against `data/archive_manifest.json`. Completeness is based on
the owner's successful-run attestation, not on an invented source count. Do
not generate manifests for arbitrary incomplete downloads and call them complete.

## Input audit

| Run | Window | Archived rows / unique IDs |
| --- | --- | ---: |
| 3 | May 1–31, 2026 | 53,447 |
| 4 | June 1–30, 2026 | 52,398 |
| 5 | July 1–31, 2026 | 46,511 |
| 2 | August 1–31, 2026 | 48,068 |
| Total | May–August | 200,424 |

May–July totals 152,356 as reported. August is 48,068, not the earlier typed
48,868; the archive count agrees with the screenshot. Ingestion collected all
NYPD categories in Brooklyn. Forecast evaluation selects only the three
configured categories. Source row totals therefore exceed forecast demand.

## Exact forecast contract

Origin is the **end** of the last fully observed local NYC day. At origin O,
forecast target days O+1 through O+7. Both models see exactly the previous
28 complete days, O−27 through O. For target T:

- Seasonal naive: y(T−7).
- Four-week weekday mean: [y(T−7)+y(T−14)+y(T−21)+y(T−28)]/4.

All reference days are at or before O. Passing future values into the baseline
function cannot alter predictions. Horizons beyond seven days are rejected;
there is no hidden use of observations from inside a forecast week.

The default schedule is 13 Sunday origins, May 31 through August 23. Evaluation
targets span June 1 through August 30: 91 days × 3 categories = 273 forecasts
per model, 546 total. August 31 is excluded because its full Monday–Sunday
window is unavailable. May 4–31 supplies initial warm-up.

A missing or incomplete warm-up or target day excludes the whole segment-origin
for both models, with a recorded reason. A verified complete zero-count day is
valid. Duplicate daily keys and invalid complete counts fail explicitly.
Custom origin ranges can be supplied with `--first-origin` and `--last-origin`.

## Fixture isolation and source revisions

The legacy grid was not safe as a forecasting input: fixture runs could mark
dates complete, and coverage did not establish an exact agency/borough match.
`src/demand.py` now derives coverage from reconciled successful live runs whose
query exactly matches the configured agency/borough and full window. NYC
midnight, rather than the database session timezone, controls complete-day status.
The loader counts only matching live request observations. Unknown dates have
null demand and cannot silently become training zeros.

`forecast_live_request` recovers the latest preserved successful live observation;
fixture and failed-run changes are excluded. New ingestion records every run's
observation, including returns to an earlier payload hash. The original
`request_version` table remains the distinct-hash history. For existing runs,
the view combines preserved live versions with the current live request state.
Previously overwritten historical states that were never preserved cannot be
reconstructed; the supplied monthly archives are the reference for this export.

Fixture CLI loads are blocked in a database containing live runs. Historical
fixtures are retained but excluded from forecasts. The legacy daily display
grid also excludes fixture counts/coverage; the stricter loader remains the
authoritative scope-specific forecasting input.

The test suite now uses a randomly named isolated schema and drops only that
schema afterward. Its TRUNCATE statements cannot target public tables. The
connected role needs permission to create schemas. PostgreSQL tests skip when
no database is reachable; skipped tests are not passes.

## Results from the supplied archives

| Model | Forecasts | MAE (requests/category/day) | WAPE | Bias (actual − forecast) |
| --- | ---: | ---: | ---: | ---: |
| Seasonal naive | 273 | 50.42 | 13.17% | −4.39 |
| Four-week weekday mean | 273 | 44.67 | 11.67% | −8.77 |

The weekday mean has lower aggregate absolute error and wins on WAPE in all
three categories, but has greater average overforecasting (negative bias).
This is a baseline benchmark, not a causal staffing result or measured ROI.
WAPE is sum(abs(actual−forecast))/sum(actual), stored as a ratio. A zero total
actual demand yields blank/undefined WAPE, not zero. Results are available by
category, horizon, origin, and category × horizon. Do not average percentage
errors or compare models evaluated on different records.

Artifacts in `reports/baselines_archive/`:

- `daily_input.csv`: selected grid and completeness flags.
- `predictions.csv`: origin, target, horizon, segment, model, actual, prediction, errors.
- `overall.csv`, `by_category.csv`, `by_horizon.csv`, `by_origin.csv`, `by_category_horizon.csv`.
- `skipped_origins.csv`: no skipped segment-origins for this archive evaluation.
- `metadata.json`: input/config/code hashes and evidence provenance.

The database run may differ if records have since been revised. Compare its
`daily_input.csv` with the archived snapshot first; never assert identical
results solely because the date range is identical.

## Verification actually completed

- Python 3.12, pinned requirements: **32 tests passed; 14 native PostgreSQL tests skipped** because no native server was available in the build environment.
- The complete supplied-archive backtest ran successfully with **546 prediction rows and zero skipped segment-origins**.
- Migrations were applied twice in PGlite 0.5.8 (embedded PostgreSQL/WASM); SQL assertions passed for fixture exclusion, identical legacy hashes, live reversions followed by failed/fixture edits, and grid counts/coverage.
- Native PostgreSQL/psycopg integration tests are included for your local run. They were not executed against your Mac's database.

Optional reproduction of the embedded SQL checks (Node is not needed by the app):

```bash
npm install --prefix tests --ignore-scripts --no-audit --no-fund
node tests/sql_provenance.mjs
```

PGlite is an embedded PostgreSQL build: https://pglite.dev/ . This check does
not substitute for the native psycopg integration suite.

## Limits and next step

This is a revised-data backtest. All historical rows were retrieved later;
it is not a point-in-time simulation of what NYC exposed at each origin.
Late reporting and source revisions remain unobserved historically. Snapshot
collection is needed for a prospective test.

No LLM, challenger model, prediction intervals, dashboard, or staffing ROI was
added. Both baselines are now reproducible comparators for Step 3. These results
have been inspected, so do not later describe the same period as an untouched
holdout. Define the challenger development protocol and a new held-out or
prospective evaluation period before tuning it.
