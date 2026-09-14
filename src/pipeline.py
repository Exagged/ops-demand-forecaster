"""Evaluate, refit on matured labels, and publish a portable seven-day release."""
import argparse
from datetime import date, timedelta, datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import pandas as pd
from src import train
from src.config import load_config
from src.demand import date_range, load_database
from src.features import build_examples, training_slice
from src.gbt import fit_model
from src.predict import forecast_saved
from src.backtest import evaluate, summarize


def publish(demand, cfg, origin, experiment, destination):
    experiment, destination = Path(experiment), Path(destination)
    if destination.exists():
        raise ValueError('release directory already exists; use a new --publish-dir')
    selection = json.loads((experiment/'selection.json').read_text())
    evaluation_meta = json.loads((experiment/'metadata.json').read_text())
    if evaluation_meta['scope'] != json.loads(json.dumps(cfg.raw['scope'], default=str)):
        raise ValueError('experiment scope differs from release scope')
    evaluation_end = date.fromisoformat(str(cfg.raw['modeling']['evaluation_last_origin'])) + timedelta(days=7)
    if origin < evaluation_end:
        raise ValueError('release origin must be at or after the evaluation target end')
    # Publication never admits labels after its explicit origin.
    demand = demand[demand.demand_date <= origin].copy()
    examples, _ = build_examples(demand, date_range(demand.demand_date.min()+timedelta(days=27), origin-timedelta(days=1)), False)
    matured = training_slice(examples, origin, cfg.raw['modeling']['min_training_rows'])
    params = {k:v for k,v in selection['selected_candidate'].items() if k != 'id'}
    model = fit_model(matured, origin, params)
    forecast = forecast_saved(model, demand, origin)
    if set(forecast.category) != set(cfg.scope.categories):
        raise ValueError('release does not cover all configured categories')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='release-', dir=destination.parent))
    try:
        model.save(temporary/'model')
        forecast.to_csv(temporary/'forecast.csv', index=False)
        demand.to_csv(temporary/'history.csv', index=False)
        shutil.copy(experiment/'evaluation'/'overall.csv', temporary/'evaluation.csv')
        shutil.copy(experiment/'validation'/'overall.csv', temporary/'validation.csv')
        settings = cfg.raw['backtest']
        baseline, skipped = evaluate(demand, date.fromisoformat(str(settings['first_origin'])),
                                     date.fromisoformat(str(settings['last_origin'])), 7, 7)
        if not skipped.empty:
            raise ValueError('incomplete baseline comparison; inspect data coverage')
        summarize(baseline, ['model']).to_csv(temporary/'baseline_full.csv', index=False)
        metadata = dict(version=1, scope=cfg.raw['scope'], origin=str(origin),
                        generated_at=datetime.now(timezone.utc).isoformat(),
                        policy=selection['validation_selected_policy'],
                        selected_candidate=selection['selected_candidate'],
                        evaluation_start=str(date.fromisoformat(str(cfg.raw['modeling']['evaluation_first_origin']))+timedelta(days=1)),
                        evaluation_end=str(evaluation_end),
                        baseline_start=str(date.fromisoformat(str(settings['first_origin']))+timedelta(days=1)),
                        baseline_end=str(date.fromisoformat(str(settings['last_origin']))+timedelta(days=7)),
                        caveat='Revised-data retrospective evaluation; historical availability is not reconstructed. No calibrated prediction intervals.',
                        input_evidence=evaluation_meta['input_evidence'],
                        checksums={str(p.relative_to(temporary)):hashlib.sha256(p.read_bytes()).hexdigest()
                                   for p in temporary.rglob('*') if p.is_file()})
        (temporary/'release.json').write_text(json.dumps(metadata, indent=2, default=str)+'\n')
        temporary.rename(destination)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', choices=['database','archives'], default='database')
    parser.add_argument('--config')
    parser.add_argument('--manifest', default='data/archive_manifest.json')
    parser.add_argument('--forecast-origin', type=date.fromisoformat, required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--publish-dir', required=True)
    args = parser.parse_args(argv)
    if Path(args.publish_dir).exists():
        raise ValueError('choose a new release directory')
    cfg = load_config(args.config)
    commands = ['--source',args.source,'--manifest',args.manifest,'--output',args.output]
    if args.config:
        commands += ['--config',args.config]
    train.main(commands)
    start = date.fromisoformat(str(cfg.raw['modeling']['data_start']))
    if args.source == 'archives':
        from src.archive_demand import load_archives
        demand, _ = load_archives(Path(args.manifest),cfg,start,args.forecast_origin)
    else:
        from src.db import connect
        with connect() as conn:
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            demand = load_database(conn,cfg,start,args.forecast_origin)
    # Detect source changes between experiment and publication; do not mix snapshots.
    experiment_input = pd.read_csv(Path(args.output)/'daily_input.csv')
    common = demand[demand.demand_date <= date.fromisoformat(experiment_input.demand_date.max())].copy()
    comparable = pd.read_csv(__import__('io').StringIO(common.to_csv(index=False)))
    pd.testing.assert_frame_equal(experiment_input.reset_index(drop=True),comparable.reset_index(drop=True))
    result = publish(demand,cfg,args.forecast_origin,args.output,args.publish_dir)
    print(f'Release ready: {result}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
