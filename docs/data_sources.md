# Data source

## Endpoint

NYC 311 Service Requests, 2010 to present, published by NYC Open Data.

- JSON: `https://data.cityofnewyork.us/resource/erm2-nwe9.json`
- CSV: same path with `.csv`
- Retrieved for this project on 2026-09-10. Field names below were read off a
  live sample from that date, not from the published data dictionary alone.

Reads are public and need no OAuth. An app token is optional and free
(`SOCRATA_APP_TOKEN` in `.env`). Without one, requests share a throttling pool
by IP address. No fixed quota is published for anonymous reads, so the client
sleeps between calls and backs off on 429 and 5xx instead of assuming a limit.

## Scope

Set in `configs/mvp.yaml`. Currently NYPD requests in Brooklyn, with three
categories in scope for forecasting:

- Noise - Residential
- Illegal Parking
- Blocked Driveway

Requests outside those categories are still stored, because the borough and
agency filter runs at the API and the category filter runs downstream. Only
in-scope rows reach `daily_demand`.

## Field mapping

| Source field | Column | Notes |
| --- | --- | --- |
| `unique_key` | `service_request.request_id` | Primary key. Stable across edits. |
| `created_date` | `created_at`, `created_date` | No timezone marker. Values are local NYC wall clock and are parsed naive. |
| `closed_date` | `closed_at` | Often absent. Never used as a predictor. |
| `agency` | `agency` | |
| `complaint_type` | `category.source_label` | Label can be renamed by the city, so the surrogate key is what joins. |
| `descriptor` | `descriptor` | |
| `borough` | `area.source_label` (level `borough`) | Uppercase in the source. |
| `community_board` | archived in payload | Reserved for a finer area grain later. |
| `status` | `current_status` | |
| `resolution_action_updated_date` | `resolution_updated_at` | |
| `open_data_channel_type` | archived in payload | |

Everything in `TRACKED_FIELDS` (`src/normalize.py`) is stored as JSONB on
`request_version`. Fields outside that list are archived in `data/raw/` but do
not participate in change detection, so a new column appearing in the source
does not make every row look edited.

## Retrieval strategy

**Backfill.** 24 months, one month per partition, ordered by `unique_key` with
`$limit` / `$offset` paging. The upper bound of each partition is exclusive, so
a request filed at 23:59 on the last day of a month lands in exactly one
partition. Each partition is reconciled against a `count(unique_key)` query;
a mismatch marks the run `mismatch` rather than `ok`.

**Incremental.** Daily. Refetch a rolling 30 day window of creation dates. A
created-date watermark alone would miss edits to older rows, which is why
`request_version` exists and why the window is a window rather than a cursor.

**Raw archive.** Every page is written to `data/raw/*.jsonl.gz` before parsing.
Archives are gitignored; they can be large and they mirror data that is already
public.

## What this data is not

- A request is not a unique physical incident. Duplicate reports of one pothole
  are separate requests, and the source itself marks some as duplicates only
  after the fact.
- Counts reflect reporting behavior as much as underlying conditions. Category
  definitions and reporting propensity change over time and across
  neighborhoods.
- A closed request is not a resolved problem. Resolution text often says an
  officer arrived and found nothing.
- This is not the full set of 311 interactions, only what is published here.
- Historical rows carry today's revised values. An export pulled now is not a
  record of what was visible on any past day. Backtests on this data are
  revised-data backtests, and the snapshots this pipeline starts collecting are
  what will eventually support a point-in-time claim.

## Fixtures

`data/fixtures/requests.jsonl` holds 20 real records pulled on 2026-09-10 with
street addresses, coordinates, and resolution text removed. It is enough to run
the schema, the merge logic, and the tests with no network and no credentials.
It is not enough to fit a model; that needs the backfill.
