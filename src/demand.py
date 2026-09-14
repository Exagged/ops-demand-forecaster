"""Coverage-aware, scope-specific daily input; never trust legacy grid zeros."""
from datetime import date, timedelta
import pandas as pd
from src.ingest_311 import where_clause


def date_range(start, end):
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def load_database(conn, cfg, start: date, end: date):
    from zoneinfo import ZoneInfo
    from datetime import datetime, time
    with conn.cursor() as cur:
        cur.execute('''SELECT window_start, window_end, fetched_at, query
                       FROM ingestion_run WHERE mode IN ('backfill','incremental')
                       AND status='ok' AND source_count=rows_fetched
                       AND window_start IS NOT NULL AND window_end IS NOT NULL''')
        runs = cur.fetchall()
        covered = set()
        for lo, hi, fetched, query in runs:
            # Existing ingestion fetches all categories for exactly this agency/borough.
            # Reject other or narrower queries instead of guessing coverage.
            if query != where_clause(cfg, lo, hi) or fetched is None:
                continue
            for day in date_range(max(start, lo), min(end, hi)):
                midnight = datetime.combine(day + timedelta(days=1), time(), ZoneInfo(cfg.scope.timezone))
                if fetched >= midnight:
                    covered.add(day)
        cur.execute('''SELECT demand_date, category, area, count(*)
                       FROM forecast_live_request
                       WHERE agency=%s AND area=%s AND category=ANY(%s)
                       AND demand_date BETWEEN %s AND %s GROUP BY 1,2,3''',
                    (cfg.scope.agency, cfg.scope.borough, cfg.scope.categories, start, end))
        counts = {(d, c, a): n for d, c, a, n in cur.fetchall()}
    return make_grid(cfg, start, end, covered, counts)


def make_grid(cfg, start, end, covered, counts):
    return pd.DataFrame([
        {'demand_date': d, 'category': c, 'area': cfg.scope.borough,
         'request_count': counts.get((d, c, cfg.scope.borough), 0) if d in covered else None,
         'is_complete': d in covered}
        for d in date_range(start, end) for c in cfg.scope.categories
    ])
