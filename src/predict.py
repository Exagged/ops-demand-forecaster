"""Forecast from a saved Step 3 model using only 28 completed history days."""
import argparse
from datetime import date, timedelta
from pathlib import Path
import pandas as pd
from src.config import load_config
from src.demand import load_database
from src.features import build_examples
from src.gbt import ModelBundle


def forecast_saved(model, demand, origin):
    if date.fromisoformat(model.metadata['training_cutoff']) > origin:
        raise ValueError('saved model was trained after the requested forecast origin')
    examples,skipped=build_examples(demand,[origin],require_targets=False)
    if not skipped.empty or examples.empty:
        raise ValueError('all requested segments need 28 complete history days')
    values=model.predict(examples)
    output=examples[['origin_date','target_date','horizon_days','category','area']].copy()
    output['lightgbm']=values
    output['seasonal_naive']=examples.weekday_lag1
    output['weekday_mean_4w']=examples.weekday_mean4
    output['model_training_cutoff']=model.metadata['training_cutoff']
    return output


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model-dir',required=True)
    p.add_argument('--origin',type=date.fromisoformat,required=True)
    p.add_argument('--source',choices=['database','archives'],default='database')
    p.add_argument('--config')
    p.add_argument('--manifest',default='data/archive_manifest.json')
    p.add_argument('--output',default='reports/saved_model_forecast.csv')
    args=p.parse_args(argv);cfg=load_config(args.config)
    start=args.origin-timedelta(days=27)
    if args.source=='database':
        from src.db import connect
        with connect() as conn:
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            demand=load_database(conn,cfg,start,args.origin)
    else:
        from src.archive_demand import load_archives
        demand,_=load_archives(Path(args.manifest),cfg,start,args.origin)
    model=ModelBundle.load(args.model_dir)
    forecast=forecast_saved(model,demand,args.origin)
    output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True)
    forecast.to_csv(output,index=False)
    print(f'{len(forecast)} future predictions written to {output}')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
