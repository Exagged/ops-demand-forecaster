"""LightGBM model bundle with training-only category mapping and text persistence."""
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
import lightgbm as lgb
from src.features import FEATURES, NUMERIC_FEATURES, CATEGORICAL_FEATURES

BASE_PARAMS = dict(objective='poisson', learning_rate=0.05, random_state=42,
                   n_jobs=1, deterministic=True, force_col_wise=True, verbosity=-1,
                   reg_lambda=5.0, max_bin=31, importance_type='gain')


def matrix(frame, categories):
    out = frame[FEATURES].copy()
    for column in NUMERIC_FEATURES:
        out[column] = pd.to_numeric(out[column], errors='raise').astype(float)
        if not np.isfinite(out[column]).all():
            raise ValueError(f'non-finite model feature: {column}')
    for column in CATEGORICAL_FEATURES:
        if not out[column].isin(categories[column]).all():
            raise ValueError(f'unseen or missing category in {column}')
        out[column] = pd.Categorical(out[column], categories=categories[column])
    return out


class ModelBundle:
    def __init__(self, booster, metadata):
        self.booster, self.metadata = booster, metadata

    def predict(self, frame):
        x = matrix(frame, self.metadata['categories'])
        values = np.zeros(len(x)) if self.booster is None else self.booster.predict(x, num_threads=1)
        if not np.isfinite(values).all():
            raise ValueError('model produced a non-finite prediction')
        return np.maximum(values, 0.0)

    def save(self, folder):
        folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
        meta = dict(self.metadata)
        if self.booster is not None:
            self.booster.save_model(str(folder/'model.txt'))
            meta['model_sha256'] = hashlib.sha256((folder/'model.txt').read_bytes()).hexdigest()
        else:
            (folder/'model.txt').unlink(missing_ok=True)
        (folder/'metadata.json').write_text(json.dumps(meta, indent=2, sort_keys=True)+'\n')

    @classmethod
    def load(cls, folder):
        folder = Path(folder); meta = json.loads((folder/'metadata.json').read_text())
        if meta['features'] != FEATURES:
            raise ValueError('saved model feature contract differs from this code')
        booster = None
        if meta['kind'] != 'constant_zero':
            if hashlib.sha256((folder/'model.txt').read_bytes()).hexdigest() != meta['model_sha256']:
                raise ValueError('model checksum mismatch')
            booster = lgb.Booster(model_file=str(folder/'model.txt'))
        return cls(booster, meta)


def fit_model(train, cutoff, params):
    if train.empty or not ((train.feature_max_date <= train.origin_date) &
                           (train.origin_date < train.target_date) & (train.target_date <= cutoff)).all():
        raise ValueError('fit received immature targets or future features')
    y = train.actual.to_numpy(dtype=float)
    if not np.isfinite(y).all() or (y < 0).any():
        raise ValueError('invalid training targets')
    categories = {c: sorted(train[c].unique().tolist()) for c in CATEGORICAL_FEATURES}
    x = matrix(train, categories)
    # Daily origins create several horizon examples for one target. Give each
    # unique target/segment equal total weight, computed ONLY in this training slice.
    repetitions = train.groupby(['target_date','category','area']).actual.transform('size')
    weights = 1.0 / repetitions.to_numpy()
    weights *= len(weights) / weights.sum()
    metadata = dict(features=FEATURES, categories=categories, training_cutoff=str(cutoff),
                    max_training_target=str(train.target_date.max()),
                    training_rows=len(train), unique_training_targets=len(train.drop_duplicates(['target_date','category','area'])),
                    params={**BASE_PARAMS, **params}, lightgbm_version=lgb.__version__,
                    training_sha256=hashlib.sha256(train.to_csv(index=False).encode()).hexdigest(),
                    kind='constant_zero' if y.sum() == 0 else 'lightgbm_poisson')
    booster = None
    if y.sum() > 0:
        estimator = lgb.LGBMRegressor(**metadata['params'])
        estimator.fit(x, y, sample_weight=weights, categorical_feature=CATEGORICAL_FEATURES)
        booster = estimator.booster_
    return ModelBundle(booster, metadata)
