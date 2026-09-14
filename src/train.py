"""Chronological LightGBM selection and matched, expanding-window evaluation.

Run python -m src.train --source database (or archives). Model parameters are
selected using validation origins only and then frozen for evaluation origins.
"""
from __future__ import annotations
import argparse
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import platform
import numpy as np
import pandas as pd
from src.backtest import COLUMNS, summarize
from src.config import load_config
from src.demand import date_range, load_database
from src.features import FEATURES, build_examples, training_slice
from src.gbt import fit_model

KEY = ['origin_date','target_date','category','area','horizon_days']


def weekly_origins(start, end):
    if start > end or (end-start).days % 7:
        raise ValueError('origin endpoints must be ordered and on the same weekday')
    return date_range(start, end)[::7]


def protocol(settings):
    dates = {k: date.fromisoformat(str(settings[k])) for k in (
        'data_start','validation_first_origin','validation_last_origin',
        'evaluation_first_origin','evaluation_last_origin')}
    validation = weekly_origins(dates['validation_first_origin'], dates['validation_last_origin'])
    evaluation = weekly_origins(dates['evaluation_first_origin'], dates['evaluation_last_origin'])
    if validation[-1]+timedelta(days=7) > evaluation[0]:
        raise ValueError('validation target window overlaps evaluation future targets')
    if dates['data_start']+timedelta(days=28) > validation[0]:
        raise ValueError('not enough calendar history before validation')
    ids = [c['id'] for c in settings['candidates']]
    if not ids or len(ids) != len(set(ids)) or any(not i.replace('_','').isalnum() for i in ids):
        raise ValueError('candidate IDs must be unique alphanumeric/underscore names')
    return dates, validation, evaluation


def walk_forward(training_examples, forecast_examples, origins, params, minimum_rows=100, artifact_dir=None):
    records, skipped, audit = [], [], []
    for origin in origins:
        future = forecast_examples[forecast_examples.origin_date == origin].copy()
        if future.empty:
            skipped.append(dict(origin_date=origin, category='*', area='*', reason='no_complete_forecast_windows'))
            continue
        # Every included segment must be evaluated for the full horizon by every model.
        sizes = future.groupby(['category','area']).horizon_days.agg(list)
        if any(sorted(h) != list(range(1,8)) for h in sizes):
            raise ValueError('forecast frame contains a partial segment window')
        try:
            train = training_slice(training_examples, origin, minimum_rows)
            model = fit_model(train, origin, params)
            estimates = model.predict(future)
        except ValueError as exc:
            skipped.append(dict(origin_date=origin, category='*', area='*', reason=f'model_ineligible: {exc}'))
            continue
        if artifact_dir is not None:
            model.save(Path(artifact_dir)/str(origin))
        audit.append(dict(origin_date=origin, max_training_target=train.target_date.max(),
                          min_training_target=train.target_date.min(), training_rows=len(train),
                          unique_training_targets=model.metadata['unique_training_targets'],
                          training_sha256=model.metadata['training_sha256']))
        for i, (_, row) in enumerate(future.iterrows()):
            predictions = {'seasonal_naive':row.weekday_lag1, 'weekday_mean_4w':row.weekday_mean4, 'lightgbm':estimates[i]}
            for name, prediction in predictions.items():
                record = {k:row[k] for k in KEY}
                error = float(row.actual-prediction)
                record.update(model=name, actual=float(row.actual), prediction=float(prediction),
                              error=error, absolute_error=abs(error))
                records.append(record)
    return (pd.DataFrame(records, columns=COLUMNS),
            pd.DataFrame(skipped, columns=['origin_date','category','area','reason']), pd.DataFrame(audit))


def assert_paired(predictions):
    if predictions.empty:
        raise ValueError('no eligible paired predictions')
    if predictions.duplicated(KEY+['model']).any():
        raise ValueError('duplicate predictions')
    counts = predictions.groupby(KEY).model.agg(lambda s: set(s))
    if not counts.map(lambda s: s == {'seasonal_naive','weekday_mean_4w','lightgbm'}).all():
        raise ValueError('models were not scored on identical keys')
    if predictions.groupby(KEY).actual.nunique().ne(1).any():
        raise ValueError('models were scored against different actuals')


def select_candidate(training_examples, validation_examples, origins, candidates, minimum_rows, output=None):
    rows, outputs, key_reference = [], {}, None
    for candidate in candidates:
        cid = candidate['id']; params = {k:v for k,v in candidate.items() if k!='id'}
        predictions, skipped, audit = walk_forward(training_examples, validation_examples, origins, params, minimum_rows)
        assert_paired(predictions)
        keys = set(map(tuple,predictions[KEY].itertuples(index=False,name=None)))
        if key_reference is not None and keys != key_reference:
            raise ValueError('candidates have different validation coverage; comparison rejected')
        key_reference = keys
        metrics = summarize(predictions[predictions.model=='lightgbm'], ['model']).iloc[0]
        # MAE and WAPE rank identically on paired data; MAE stays defined for all-zero demand.
        rows.append(dict(candidate=cid, n=int(metrics.n), mae=float(metrics.mae),
                         wape=None if pd.isna(metrics.wape) else float(metrics.wape), bias=float(metrics.bias)))
        outputs[cid] = (predictions, skipped, audit)
        if output:
            folder = Path(output)/cid; folder.mkdir(parents=True,exist_ok=True)
            predictions.to_csv(folder/'predictions.csv',index=False)
            skipped.to_csv(folder/'skipped.csv',index=False)
            audit.to_csv(folder/'training_audit.csv',index=False)
    leaderboard = pd.DataFrame(rows).sort_values(['mae','candidate']).reset_index(drop=True)
    winner_id = leaderboard.iloc[0].candidate
    selected = next(c for c in candidates if c['id']==winner_id)
    return selected, leaderboard, outputs[winner_id]


def write_comparison(predictions, output):
    assert_paired(predictions)
    output = Path(output); output.mkdir(parents=True,exist_ok=True)
    predictions.to_csv(output/'predictions.csv',index=False)
    for name, by in [('overall',['model']),('by_category',['model','category','area']),
                     ('by_horizon',['model','horizon_days']),('by_origin',['model','origin_date']),
                     ('by_category_horizon',['model','category','area','horizon_days'])]:
        summarize(predictions,by).to_csv(output/f'{name}.csv',index=False)
    pivot = predictions.pivot(index=KEY,columns='model',values='absolute_error')
    pivot['gbt_minus_mean4_absolute_error'] = pivot.lightgbm-pivot.weekday_mean_4w
    pivot['gbt_minus_naive_absolute_error'] = pivot.lightgbm-pivot.seasonal_naive
    pivot.reset_index().to_csv(output/'paired_errors.csv',index=False)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config')
    p.add_argument('--source',choices=['database','archives'],default='database')
    p.add_argument('--manifest',default='data/archive_manifest.json')
    p.add_argument('--output',default='reports/step3')
    args = p.parse_args(argv)
    cfg=load_config(args.config); settings=cfg.raw['modeling']
    dates, validation_origins, evaluation_origins = protocol(settings)
    start, end=dates['data_start'],evaluation_origins[-1]+timedelta(days=7)
    if args.source=='database':
        from src.db import connect
        with connect() as conn:
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            demand=load_database(conn,cfg,start,end)
        evidence={'type':'database','coverage':'reconciled successful live runs matching exact configured scope'}
    else:
        from src.archive_demand import load_archives
        demand,evidence=load_archives(Path(args.manifest),cfg,start,end)
    training_examples, training_skips=build_examples(demand,date_range(start+timedelta(days=27),end-timedelta(days=1)),False)
    validation_examples, validation_skips=build_examples(demand,validation_origins)
    evaluation_examples, evaluation_skips=build_examples(demand,evaluation_origins)
    output=Path(args.output)
    # Never silently overwrite a previous experiment or mix stale candidate artifacts.
    if output.exists() and any(output.iterdir()):
        raise ValueError('output directory is not empty; choose a new experiment directory')
    output.mkdir(parents=True,exist_ok=True)
    demand.to_csv(output/'daily_input.csv',index=False)
    training_skips.to_csv(output/'training_feature_skips.csv',index=False)
    validation_skips.to_csv(output/'validation_feature_skips.csv',index=False)
    evaluation_skips.to_csv(output/'evaluation_feature_skips.csv',index=False)
    validation_examples.to_csv(output/'validation_features.csv',index=False)
    evaluation_examples.to_csv(output/'evaluation_features.csv',index=False)
    # Selection never receives evaluation-origin rows or labels.
    selection_train=training_examples[training_examples.target_date<=validation_origins[-1]].copy()
    selected,board,(validation_predictions,selection_skips,selection_audit)=select_candidate(
        selection_train,validation_examples,validation_origins,settings['candidates'],settings['min_training_rows'],output/'candidates')
    board.to_csv(output/'validation_leaderboard.csv',index=False)
    params={k:v for k,v in selected.items() if k!='id'}
    selected_record={'selected_candidate':selected,'selection_metric':'pooled paired validation MAE; WAPE equivalent when total demand > 0',
                     'selection_target_end':str(validation_origins[-1]+timedelta(days=7)),
                     'evaluation_first_origin':str(evaluation_origins[0]),
                     'validation_selected_policy':summarize(validation_predictions,['model']).sort_values(['mae','model']).iloc[0].model}
    # Persist the frozen choice BEFORE any evaluation-origin model is fitted.
    (output/'selection.json').write_text(json.dumps(selected_record,indent=2)+'\n')
    write_comparison(validation_predictions,output/'validation')
    predictions,fit_skips,audit=walk_forward(training_examples,evaluation_examples,evaluation_origins,params,
                                             settings['min_training_rows'],output/'models')
    write_comparison(predictions,output/'evaluation')
    fit_skips.to_csv(output/'evaluation_fit_skips.csv',index=False)
    audit.to_csv(output/'evaluation_training_audit.csv',index=False)
    # Save the last evaluated model's gain importance; descriptive, not causal.
    from src.gbt import ModelBundle
    last_origin=audit.origin_date.max()
    last_model=ModelBundle.load(output/'models'/str(last_origin))
    if last_model.booster is not None:
        pd.DataFrame({'feature':FEATURES,'gain':last_model.booster.feature_importance(importance_type='gain')}).sort_values('gain',ascending=False).to_csv(output/'feature_importance.csv',index=False)
    import lightgbm, sklearn, scipy
    metadata=dict(protocol=settings,input_evidence=evidence,scope=cfg.raw['scope'],
                  evaluation_type='revised-data retrospective rolling-origin comparison; baseline results previously inspected',
                  model_selection='validation only; parameters frozen before evaluation; refit each origin on matured labels',
                  overlap_note='training examples share target dates across horizons; equal total weight per target/segment',
                  evaluation_rows=len(predictions),validation_rows=len(validation_predictions),
                  selected_candidate=selected['id'],
                  versions={'python':platform.python_version(),'numpy':np.__version__,'pandas':pd.__version__,
                            'lightgbm':lightgbm.__version__,'sklearn':sklearn.__version__,'scipy':scipy.__version__},
                  input_sha256=hashlib.sha256((output/'daily_input.csv').read_bytes()).hexdigest(),
                  config_sha256=hashlib.sha256(json.dumps(cfg.raw,sort_keys=True,default=str).encode()).hexdigest(),
                  code_sha256={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(Path(__file__).parent.glob('*.py'))})
    (output/'metadata.json').write_text(json.dumps(metadata,indent=2,default=str)+'\n')
    print('Selected candidate:',selected['id'])
    print('Evaluation (identical dates for every model):')
    print(summarize(predictions,['model']).to_string(index=False))
    print(f'Reports and model artifacts: {output}')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
