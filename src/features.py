"""Origin-safe supervised examples for pooled direct seven-day demand forecasts.

Only complete local-day counts at/before each row's origin enter features.
Future dates supply calendar features, never future observed demand.
"""
from datetime import date, timedelta
from functools import lru_cache
from pandas.tseries.holiday import USFederalHolidayCalendar
import numpy as np
import pandas as pd
from src.demand import date_range

NUMERIC_FEATURES = [
    'horizon_days', 'target_weekday', 'target_weekend', 'target_day_of_month',
    'target_month', 'target_holiday', 'target_before_holiday', 'target_after_holiday',
    'origin_last', 'origin_lag7', 'mean7', 'mean14', 'mean28', 'std7', 'std28',
    'min7', 'max7', 'recent_trend', 'weekday_lag1', 'weekday_lag2',
    'weekday_lag3', 'weekday_lag4', 'weekday_mean4', 'weekday_trend',
]
CATEGORICAL_FEATURES = ['category', 'area']
FEATURES = NUMERIC_FEATURES + CATEGORICAL_FEATURES
META = ['origin_date', 'target_date', 'feature_max_date', 'history_start_date', 'actual']


@lru_cache(maxsize=32)
def holiday_dates(year):
    """Published US federal observed holidays; calendar signals, not closure claims."""
    return frozenset(USFederalHolidayCalendar().holidays(
        start=f'{year-1}-12-01', end=f'{year+1}-01-31').date)


def series_by_segment(demand):
    required = {'demand_date', 'category', 'area', 'request_count', 'is_complete'}
    if not required <= set(demand.columns):
        raise ValueError('missing daily input columns')
    if demand.duplicated(['demand_date', 'category', 'area']).any():
        raise ValueError('duplicate daily segment rows')
    result = {}
    for key, g in demand.groupby(['category', 'area'], sort=True):
        valid = g[g.is_complete.eq(True)]
        values = pd.to_numeric(valid.request_count, errors='coerce')
        if not np.isfinite(values).all() or (values < 0).any() or (values != np.floor(values)).any():
            raise ValueError('complete counts must be finite nonnegative integers')
        if not all(isinstance(d, date) and not isinstance(d, pd.Timestamp) for d in valid.demand_date):
            raise ValueError('demand_date must contain Python local calendar dates')
        result[key] = dict(zip(valid.demand_date, values.astype(float)))
    return result


def feature_row(series, origin, target, category, area):
    horizon = (target - origin).days
    if not 1 <= horizon <= 7:
        raise ValueError('only direct horizons 1 through 7 are supported')
    dates = date_range(origin - timedelta(days=27), origin)
    if any(d not in series for d in dates):
        return None
    values = np.asarray([series[d] for d in dates], dtype=float)
    if not np.isfinite(values).all() or (values < 0).any():
        raise ValueError('invalid history values')
    weekday_days = [target - timedelta(days=7*k) for k in range(1, 5)]
    # For horizons <=7 these references are guaranteed to be no later than origin.
    if max(weekday_days) > origin:
        raise ValueError('future observed data referenced')
    week_values = [float(series[d]) for d in weekday_days]
    row = dict(origin_date=origin, target_date=target, feature_max_date=origin,
               history_start_date=dates[0], category=category, area=area,
               horizon_days=horizon, target_weekday=target.weekday(),
               target_weekend=int(target.weekday() >= 5), target_day_of_month=target.day,
               origin_last=values[-1], origin_lag7=values[-8],
               mean7=values[-7:].mean(), mean14=values[-14:].mean(), mean28=values.mean(),
               std7=values[-7:].std(ddof=0), std28=values.std(ddof=0),
               min7=values[-7:].min(), max7=values[-7:].max(),
               recent_trend=values[-7:].mean()-values[-14:-7].mean(),
               weekday_mean4=float(np.mean(week_values)),
               weekday_trend=week_values[0]-float(np.mean(week_values)))
    row.update(target_month=target.month, target_holiday=int(target in holiday_dates(target.year)),
               target_before_holiday=int(target+timedelta(days=1) in holiday_dates(target.year)),
               target_after_holiday=int(target-timedelta(days=1) in holiday_dates(target.year)))
    row.update({f'weekday_lag{i+1}': v for i, v in enumerate(week_values)})
    return row


def build_examples(demand, origins, require_targets=True):
    """Return eligible seven-day segment windows and explicit exclusion reasons.

    Training labels may be partially mature; set require_targets=False then use
    training_slice to admit only observed targets no later than a fit cutoff.
    """
    rows, skips = [], []
    for (category, area), series in series_by_segment(demand).items():
        for origin in origins:
            targets = date_range(origin+timedelta(days=1), origin+timedelta(days=7))
            if any(d not in series for d in date_range(origin-timedelta(days=27), origin)):
                skips.append(dict(origin_date=origin, category=category, area=area, reason='incomplete_28_day_history'))
                continue
            if require_targets and any(d not in series for d in targets):
                skips.append(dict(origin_date=origin, category=category, area=area, reason='incomplete_target_window'))
                continue
            for target in targets:
                row = feature_row(series, origin, target, category, area)
                row['actual'] = series.get(target, np.nan)
                rows.append(row)
    return (pd.DataFrame(rows, columns=FEATURES+META),
            pd.DataFrame(skips, columns=['origin_date', 'category', 'area', 'reason']))


def training_slice(examples, cutoff, minimum_rows=100):
    train = examples[(examples.target_date <= cutoff) & examples.actual.notna()].copy()
    if len(train) < minimum_rows:
        raise ValueError(f'insufficient matured training rows at {cutoff}: {len(train)} < {minimum_rows}')
    if not ((train.feature_max_date <= train.origin_date) &
            (train.origin_date < train.target_date) & (train.target_date <= cutoff)).all():
        raise ValueError('training date boundary violation')
    return train.sort_values(['origin_date','category','area','target_date']).reset_index(drop=True)
