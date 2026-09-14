"""Revised-data rolling-origin evaluation. No database mutations or network calls."""
from __future__ import annotations
import argparse
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import pandas as pd
from src.baselines import MODELS, predict
from src.config import load_config
from src.demand import date_range, load_database

COLUMNS = ['origin_date', 'target_date', 'horizon_days', 'category', 'area',
           'model', 'actual', 'prediction', 'error', 'absolute_error']


def evaluate(demand, first_origin, last_origin, horizon=7, step=7):
    if first_origin > last_origin or step < 1 or not 1 <= horizon <= 7:
        raise ValueError('invalid origin range, step, or horizon')
    required = {'demand_date','category','area','request_count','is_complete'}
    if not required <= set(demand.columns):
        raise ValueError('missing input columns')
    if demand.duplicated(['demand_date','category','area']).any():
        raise ValueError('duplicate daily segment rows')
    rows, skips = [], []
    for (category, area), segment in demand.groupby(['category','area'], sort=True):
        complete = segment[segment.is_complete.eq(True)].copy()
        values = pd.to_numeric(complete.request_count, errors='coerce')
        if values.isna().any() or not values.map(lambda x: 0 <= x < float('inf') and x == int(x)).all():
            raise ValueError('complete days must contain finite nonnegative integer counts')
        series = dict(zip(complete.demand_date, values))
        origin = first_origin
        while origin <= last_origin:
            warmup = date_range(origin - timedelta(days=27), origin)
            targets = date_range(origin + timedelta(days=1), origin + timedelta(days=horizon))
            reason = None
            if any(d not in series for d in warmup):
                reason = 'incomplete_28_day_history'
            elif any(d not in series for d in targets):
                reason = 'incomplete_target_window'
            if reason:
                skips.append(dict(origin_date=origin, category=category, area=area, reason=reason))
            else:
                estimates = predict({d: series[d] for d in warmup}, origin, horizon)
                for model in MODELS:
                    for h, target in enumerate(targets, 1):
                        actual, pred = series[target], estimates[model][h-1]
                        rows.append(dict(origin_date=origin, target_date=target, horizon_days=h,
                                         category=category, area=area, model=model, actual=actual,
                                         prediction=pred, error=actual-pred, absolute_error=abs(actual-pred)))
            origin += timedelta(days=step)
    return (pd.DataFrame(rows, columns=COLUMNS),
            pd.DataFrame(skips, columns=['origin_date','category','area','reason']))


def summarize(predictions, by):
    result = []
    if predictions.empty:
        return pd.DataFrame(columns=by + ['n','mae','wape','bias','actual_total'])
    for key, g in predictions.groupby(by, sort=True, dropna=False):
        key = key if isinstance(key, tuple) else (key,)
        denominator = float(g.actual.sum())
        result.append(dict(zip(by, key), n=len(g), mae=float(g.absolute_error.mean()),
                           wape=float(g.absolute_error.sum()) / denominator if denominator else None,
                           bias=float(g.error.mean()), actual_total=denominator))
    return pd.DataFrame(result)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config')
    p.add_argument('--source', choices=['database','archives'], default='database')
    p.add_argument('--manifest', default='data/archive_manifest.json')
    p.add_argument('--output', default='reports/baselines')
    p.add_argument('--first-origin', type=date.fromisoformat)
    p.add_argument('--last-origin', type=date.fromisoformat)
    args = p.parse_args(argv)
    cfg = load_config(args.config)
    settings = cfg.raw['backtest']
    first = args.first_origin or date.fromisoformat(str(settings['first_origin']))
    last = args.last_origin or date.fromisoformat(str(settings['last_origin']))
    horizon, step = int(settings['horizon_days']), int(settings['step_days'])
    start, end = first-timedelta(days=27), last+timedelta(days=horizon)
    if args.source == 'database':
        from src.db import connect
        with connect() as conn:
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            demand = load_database(conn, cfg, start, end)
        evidence = {'type': 'database', 'coverage': 'successful reconciled live runs matching exact scope query'}
    else:
        from src.archive_demand import load_archives
        demand, evidence = load_archives(Path(args.manifest), cfg, start, end)
    predictions, skipped = evaluate(demand, first, last, horizon, step)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    demand.to_csv(output/'daily_input.csv', index=False)
    predictions.to_csv(output/'predictions.csv', index=False)
    skipped.to_csv(output/'skipped_origins.csv', index=False)
    for name, by in [('overall',['model']), ('by_category',['model','category','area']),
                     ('by_horizon',['model','horizon_days']), ('by_origin',['model','origin_date']),
                     ('by_category_horizon',['model','category','area','horizon_days'])]:
        summarize(predictions, by).to_csv(output/f'{name}.csv', index=False)
    metadata = {'evaluation_type': 'revised-data retrospective backtest; not point-in-time replay',
                'origin_definition': 'end of last fully observed local day',
                'bias_definition': 'actual minus prediction; positive means underforecast',
                'wape_unit': 'ratio; blank when actual total is zero',
                'first_origin': str(first), 'last_origin': str(last), 'horizon': horizon, 'step': step,
                'scope': cfg.raw['scope'], 'input_sha256': hashlib.sha256((output/'daily_input.csv').read_bytes()).hexdigest(),
                'config_sha256': hashlib.sha256(json.dumps(cfg.raw, sort_keys=True, default=str).encode()).hexdigest(),
                'code_sha256': {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(Path(__file__).parent.glob('*.py'))},
                'input_evidence': evidence, 'prediction_rows': len(predictions), 'skipped_segment_origins':len(skipped)}
    (output/'metadata.json').write_text(json.dumps(metadata, indent=2)+'\n')
    if predictions.empty:
        print(f'No eligible origins. See {output}/skipped_origins.csv')
        return 2
    print(summarize(predictions, ['model']).to_string(index=False))
    print(f'{len(predictions)} predictions; {len(skipped)} skipped segment-origins. Reports: {output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
