# NYC 311 Operations Demand Forecaster & Capacity Planner

[![Streamlit App](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://ops-demand-forecaster.streamlit.app)

🔗 **Live Interactive Demo:** [ops-demand-forecaster.streamlit.app](https://ops-demand-forecaster.streamlit.app)

An end-to-end forecasting pipeline and interactive workforce planning tool built on NYC 311 operational data. 

The application forecasts daily incoming service request volume for NYPD operations in Brooklyn across three major complaint categories (*Noise - Residential*, *Illegal Parking*, and *Blocked Driveway*), then translates those forecasts into actionable staffing schedules a team lead can stress-test.

---

## The Operational Problem

* **The decision it supports:** Helps an operations manager anticipate next week's incoming call load, identify potential capacity deficits across individual complaint types, and evaluate how shifting staffing levels or worker throughput affects workload backlog.
* **What it does not claim:** This is not an automated dispatch engine or an optimization model claiming causal impact on response times. Handling effort and shift rosters are not published in 311 data. Worker throughput is explicitly structured as an interactive, user-adjusted planning parameter.

---

## Model Selection & Honest Benchmarking

During rolling-origin out-of-sample evaluation (July 27 – August 30), a simple rolling 4-week weekday mean outperformed the gradient-boosted challenger:

| Model | Evaluation WAPE | Role in App |
|---|---|---|
| **4-Week Weekday Mean (Baseline)** | **8.59%** | **Production Default** |
| Seasonal Naive | 9.32% | Benchmark |
| LightGBM (Challenger) | 10.50% | Integrated Alternative |

Because the weekday mean showed tighter calibration and lower variance across recurrent day-of-week demand spikes, it remains the production default rather than deploying machine learning for the sake of complexity. Both models are preserved in the UI so operators can compare projections side-by-side.

---

## How It Works

```
NYC OpenData (Socrata API)
        │
        ▼
PostgreSQL (Docker)
  ├── Content-hash versioning on incoming records
  ├── Distinct complaint aggregations
  └── Flagged incomplete ingestion windows
        │
        ├─── Rolling 4-Week Weekday Mean Baseline (Default)
        └─── LightGBM Regressor (Trained Challenger)
        │
        ▼
Frozen Release Bundle (artifacts/nypd_brooklyn_demo/)
        │
        ▼
Interactive Streamlit Planner (Zero-DB Standalone Web App)
```

### Handling Mutable Source Records
NYC 311 updates past records retroactively as tickets get resolved. Querying historical dates today yields revised data rather than original snapshots. To prevent data leakage and maintain reproducible runs:
* **Upsert identity, append versions:** `service_request` holds one stable record per `unique_key`. `request_version` only creates a new entry when the SHA-256 hash of tracked attributes changes.
* **Count tickets, not modifications:** Aggregations group by distinct ticket identity. A record modified multiple times still counts as one service request.
* **Explicit ingestion audit:** Any date without an end-of-day fetch is flagged with `is_complete = false`, preventing the model from misinterpreting missed ingestion runs as low-demand days.

---

## Running Locally

### Prerequisites
* Python 3.11 or 3.12
* Docker & Docker Compose

### 1. Clone & Set Up Virtual Environment
```bash
git clone [https://github.com/michaelvelazquez/ops-demand-forecaster.git](https://github.com/michaelvelazquez/ops-demand-forecaster.git)
cd ops-demand-forecaster

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
```

### 2. Start PostgreSQL & Run Migrations
```bash
docker compose up -d
python -m src.migrate
```

### 3. Run Automated Tests
Run the 76-test suite covering data leakage safeguards, schema migrations, and feature engineering:
```bash
python -m pytest -q
```

### 4. Launch the App Locally
```bash
python -m streamlit run app.py
```
Visit `http://localhost:8501` to view the planner. Demo mode reads precomputed bundles from `artifacts/`, requiring no live database connection to evaluate scenarios.

---

## Pipeline Commands

* **Run ingestion backfill:**
  ```bash
  python -m src.ingest_311 --mode backfill --start 2026-08-01 --end 2026-08-31
  ```
* **Run rolling incremental fetch:**
  ```bash
  python -m src.ingest_311 --mode incremental
  ```
* **Run backtest suite:**
  ```bash
  python -m src.backtest --source database --output reports/baselines_database
  ```
* **Retrain LightGBM model:**
  ```bash
  python -m src.train --source database --output reports/step3_database
  ```
* **Build a frozen release for web deployment:**
  ```bash
  python -m src.pipeline
  ```

---

## Project Structure

```
├── app.py                     # Streamlit capacity planner application
├── artifacts/                 # Standalone releases and model manifests for demo mode
├── configs/
│   └── mvp.yaml               # Agency scope, borough selection, and target categories
├── docs/                      # Technical specifications, schema notes, and backtest logs
├── sql/                       # Idempotent DDL migrations and aggregation routines
├── src/
│   ├── baselines.py           # Weekday mean and seasonal naive calculation logic
│   ├── gbt.py                 # LightGBM feature generation and model fitting
│   ├── ingest_311.py          # API ingestion, hash calculation, and upsert handling
│   ├── migrate.py             # Schema migration runner
│   └── planner.py             # Capacity calculations and staffing scenario engine
└── tests/                     # Test suite validating leakage, database, and inference
```
