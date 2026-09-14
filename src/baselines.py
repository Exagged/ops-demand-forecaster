"""Seven-day baselines. Origin is the last fully observed NYC calendar day."""
from datetime import date, timedelta
from math import isfinite

MODELS = ('seasonal_naive', 'weekday_mean_4w')


def predict(history: dict[date, float], origin: date, horizon: int = 7) -> dict[str, list[float]]:
    if not 1 <= horizon <= 7:
        raise ValueError('horizon must be between 1 and 7')
    # Deliberately slice even if a caller passes future observations.
    history = {d: v for d, v in history.items() if d <= origin}
    result = {m: [] for m in MODELS}
    for h in range(1, horizon + 1):
        target = origin + timedelta(days=h)
        days = [target - timedelta(days=7 * k) for k in range(1, 5)]
        if any(d not in history for d in days):
            raise ValueError('four complete same-weekday observations required')
        values = [float(history[d]) for d in days]
        if any(not isfinite(v) or v < 0 for v in values):
            raise ValueError('counts must be finite and nonnegative')
        result['seasonal_naive'].append(values[0])
        result['weekday_mean_4w'].append(sum(values) / 4)
    return result
