"""Validated portable forecast releases and transparent staffing arithmetic."""
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd

MODELS = ['weekday_mean_4w','seasonal_naive','lightgbm']


def load_release(path):
    path = Path(path)
    meta = json.loads((path/'release.json').read_text())
    if meta['version'] != 1 or meta['policy'] not in MODELS:
        raise ValueError('unsupported release or policy')
    required = {'forecast.csv','history.csv','evaluation.csv','validation.csv','baseline_full.csv','model/metadata.json'}
    if not required <= set(meta['checksums']):
        raise ValueError('incomplete release manifest')
    for name, expected in meta['checksums'].items():
        file = (path/name).resolve()
        if not file.is_relative_to(path.resolve()) or hashlib.sha256(file.read_bytes()).hexdigest() != expected:
            raise ValueError('release checksum mismatch')
    forecast = pd.read_csv(path/'forecast.csv',parse_dates=['target_date','origin_date'])
    origin = date.fromisoformat(meta['origin'])
    categories = set(meta['scope']['categories'])
    if set(forecast.category) != categories or set(forecast.area) != {meta['scope']['borough']}:
        raise ValueError('forecast scope mismatch')
    if forecast.duplicated(['category','target_date']).any() or not forecast.origin_date.dt.date.eq(origin).all():
        raise ValueError('duplicate forecasts or mismatched origin')
    targets = {origin+timedelta(days=i) for i in range(1,8)}
    if any(set(g.target_date.dt.date) != targets for _,g in forecast.groupby('category')):
        raise ValueError('forecast must contain seven days per category')
    if not np.isfinite(forecast[MODELS].to_numpy()).all() or (forecast[MODELS] < 0).any().any():
        raise ValueError('invalid forecast quantities')
    model_meta = json.loads((path/'model'/'metadata.json').read_text())
    if date.fromisoformat(model_meta['training_cutoff']) > origin:
        raise ValueError('model trained after forecast origin')
    return meta, forecast


def catalog(root):
    found, errors = [], []
    for path in sorted(Path(root).glob('*/release.json')):
        try:
            meta, _ = load_release(path.parent)
            found.append((path.parent,meta))
        except (ValueError, KeyError, OSError, TypeError) as exc:
            errors.append(f'{path.parent.name}: {exc}')
    return found, errors


def staffing(forecast, categories, model, throughput, available_staff):
    if model not in MODELS or not categories or not set(categories) <= set(forecast.category):
        raise ValueError('select available categories and model')
    if not np.isfinite(throughput) or throughput <= 0:
        raise ValueError('throughput must be positive and finite')
    if not np.isfinite(available_staff) or available_staff < 0 or int(available_staff) != available_staff:
        raise ValueError('available staff must be a nonnegative integer')
    selected = forecast[forecast.category.isin(categories)]
    result = selected.groupby('target_date')[model].sum().rename('forecast_requests').to_frame()
    result['required_staff'] = np.ceil(result.forecast_requests/throughput).astype(int)
    result['available_capacity'] = throughput*available_staff
    result['unserved_requests'] = (result.forecast_requests-result.available_capacity).clip(lower=0)
    result['staff_gap'] = (result.required_staff-available_staff).clip(lower=0)
    return result.reset_index()
