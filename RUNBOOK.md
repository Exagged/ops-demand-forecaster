# Complete project runbook

Use Python 3.11 or 3.12. Run commands from the extracted project root containing `app.py` and `compose.yaml`. Merge the delivered source into your existing project without deleting your local `.env`, virtual environment, or Docker volume. The ZIP excludes credentials and environments. Do not run `docker compose down -v`.

## 1. Install and test

With your existing virtual environment activated:

```bash
python -m pip install -r requirements.txt
docker compose up -d
python -m src.migrate
python -m pytest -q
```

If LightGBM on macOS reports that `libomp.dylib` is missing, install its runtime with `brew install libomp`, then retry. No new SQL migration is needed for Steps 3–4: migrations 001–003 are included, the ingestion schema is unchanged, and forecasts/models are persisted as portable files. Existing `model_run`/`forecast` database tables are not populated by this release.

Database tests use a unique temporary PostgreSQL schema; fixtures never truncate public production tables. If PostgreSQL is unavailable, 14 integration tests explicitly skip. A run with skipped database tests is not a full database integration pass.

## 2. Launch the included demonstration immediately

```bash
python -m streamlit run app.py
```

Open the local URL printed in the terminal, normally http://localhost:8501. No database or API call is made by the app. The included `artifacts/nypd_brooklyn_demo` release was fitted through August 31, 2026 and forecasts September 1–7. It is a historical demonstration, not a current forecast. Agency/borough selectors enumerate prepared releases; the supplied scope is NYPD/Brooklyn, with Noise - Residential, Illegal Parking, and Blocked Driveway categories.

## 3. Train, compare, and publish against your verified database

```bash
python -m src.pipeline --source database --forecast-origin 2026-08-31 --output reports/full_database_v1 --publish-dir artifacts/nypd_brooklyn_local_v1
python -m streamlit run app.py
```

This single pipeline command validates source coverage; runs the fixed candidate grid using validation-only selection; scores all models on identical rolling-origin evaluation windows; refits the chosen LightGBM candidate using matured targets through the forecast origin; saves the native model and seven-day predictions for all three methods; recomputes the full Step 2 benchmark; and publishes a checksum-validated release atomically. The default app policy is selected on validation, not the evaluation scores. Select the new release in the sidebar. The source data must remain unchanged during the pipeline; if ingestion revises the historical experiment rows between reads, publication aborts rather than mixing snapshots.

Existing output/release directories are never silently overwritten. For another run, change both suffixes to `v2`, etc. Failed experiment output is retained for diagnosis; use a new output directory after fixing its cause.

## 4. Reproduce entirely from included archives

```bash
python -m src.pipeline --source archives --forecast-origin 2026-08-31 --output reports/full_archive_v1 --publish-dir artifacts/nypd_brooklyn_archive_v1
```

The manifest verifies archived page hashes/counts and owner-attested completed partitions. It covers May–August 2026. Missing days are not replaced with zero. Archive provenance does not reconstruct what records were available at an earlier forecast origin; all historical scores are revised-data retrospective results.

## 5. Refresh forecasts or add scopes

For a later origin, first ingest complete days through that origin using the existing ingestion CLI, then pass the new date to `src.pipeline`. Use `python -m src.ingest_311 --help` for its backfill/incremental options. The default fixed validation/evaluation dates intentionally remain unchanged; retraining on newer matured labels does not make historical evaluation a fresh holdout. Configure a new future evaluation period explicitly when enough new data has accumulated, and freeze the experiment before inspecting that period.

For another agency/borough, copy `configs/mvp.yaml`, change its scope, ingest a complete history for that exact scope, and supply `--config configs/your_scope.yaml` to ingestion and `src.pipeline`. Use a separate publish directory under `artifacts`. The app discovers all valid releases automatically. Existing Brooklyn archives cannot train another borough. New categories need complete history, including observed zero-count dates. Never substitute one scope's model for another.

To use a separate collection of releases:

```bash
OPS_ARTIFACT_DIR=/absolute/path/to/artifacts python -m streamlit run app.py
```

## 6. What is saved

- `reports/<run>/selection.json`: candidate settings and validation-selected default, written before evaluation fits.
- `validation/` and `evaluation/`: predictions and overall/category/horizon/origin metrics, plus paired errors.
- `models/<origin>/`: rolling-origin native LightGBM models and training-cutoff metadata.
- Feature tables, skip reports, training audit, source/config/code hashes and runtime versions.
- `artifacts/<release>/model/`: final model fitted through the requested origin (not an evaluation model).
- `forecast.csv`: 21 category/day rows with all three forecast methods and model cutoff.
- `history.csv`, `evaluation.csv`, `validation.csv`, `baseline_full.csv`, `release.json`: portable app inputs, provenance and checksums.

Old `reports/step3_archive` artifacts retain the earlier feature contract for historical reference. Adding holiday features requires retraining; do not load those old models with the new code. Use `reports/end_to_end_archive` and the supplied current release.

## 7. Interpretation

The original full-period weekday-mean hurdle is 11.67% WAPE across June 1–August 30. The ML comparison uses July 27–August 30, with 105 predictions per model (315 total), following validation targets June 29–July 26. WAPE is sum absolute error divided by sum actual demand; it is undefined when the denominator is zero. Bias is actual minus forecast, so positive bias indicates underforecasting. Do not claim that falling below 11.67% on a shorter window proves a baseline win.

The new holiday-feature experiment uses the same previously inspected historical evaluation period; it is a retrospective refinement, not an untouched holdout. Candidate settings are frozen before this run's evaluation. At each origin, training sees only matured target labels and features computed at or before their own origin. Daily training examples overlap targets across horizons; weighting assigns equal total weight to each unique target/segment. US federal **observed** holidays and adjacent days are known calendar signals. Four months cannot establish robust annual holiday effects.

Staffing is `ceil(sum(selected daily forecasts) / requests per person per day)`. It pools interchangeable capacity across selected categories. Staff-days are summed daily positions, not unique employees. Requests above capacity are a same-day scenario, without queue carryover. Throughput is user-specified, not estimated; no SLA, shift coverage, absence allowance, uncertainty intervals, or financial ROI is claimed.

## 8. Supporting documentation

- [Streamlit AppTest](https://docs.streamlit.io/develop/api-reference/app-testing/st.testing.v1.apptest)
- [pandas holiday calendars](https://pandas.pydata.org/docs/user_guide/timeseries.html#holidays-holiday-calendars)
- `docs/step2.md` and `docs/step3.md` retain the earlier implementation detail; this runbook governs the end-to-end release.
