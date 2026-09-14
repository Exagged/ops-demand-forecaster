# Step 3: LightGBM challenger, features, and paired evaluation

## Outcome

The trained LightGBM challenger did not outperform the four-week weekday mean.
Keep `weekday_mean_4w` as the current preferred forecasting policy. It was
selected on validation data before the evaluation models were fitted, and its
advantage persisted during evaluation. The trained models remain available for
inspection and reproducible inference.

The previous **11.67% WAPE** benchmark remains the full Step 2 June 1–August 30
result. Step 3 uses a later, smaller evaluation window so the challenger has
history for fitting and validation. Its scores must be compared with baselines
recomputed on the same dates, not against 11.67% from a different period.

## Run locally

Copy the updated code, config, tests, requirements, and docs into your project;
retain `.env`, `.venv`, and the existing Docker database volume. Step 3 adds no
SQL migrations and does not write to your database.

From the project root with the virtual environment activated:

```bash
python -m pip install -r requirements.txt
python -m pytest -q
python -m src.train --source database --output reports/step3_database
```

Training defaults to the scope-aware PostgreSQL loader already verified in Step
2. It uses a read-only repeatable-read transaction. To use the supplied archived
snapshot instead, choose a new output directory:

```bash
python -m src.train --source archives --output reports/step3_archive_rerun
```

The CLI refuses a nonempty output directory, preventing stale results from a
previous candidate set from being mixed with the new experiment.

The delivered run used Python 3.12. Numerical/model dependencies are pinned in
`requirements.txt`; use Python 3.12 to reproduce this environment. If macOS
reports a missing OpenMP runtime when importing LightGBM, install it using
`brew install libomp`, then rerun. See the [official installation guide](https://lightgbm.readthedocs.io/en/v4.6.0/Installation-Guide.html).

## Chronological protocol fixed before fitting

| Stage | Origins (end of observed day) | Scored targets | Predictions per model |
| --- | --- | --- | ---: |
| Initial history | Begins May 1, 2026 | First supervised target May 29 | Not evaluation |
| Model selection | June 28, July 5, July 12, July 19 | June 29–July 26 | 84 |
| Reserved comparison | July 26, August 2, 9, 16, 23 | July 27–August 30 | 105 |

At each origin, the model is refitted on an expanding history of **matured
training labels whose target date is no later than that origin**. Parameters
are frozen after model selection. Earlier evaluation-week outcomes may enter
later weekly fits once observed; they never enter an earlier forecast or change
the selected parameters.

We had already inspected baseline results over these months, so the later
comparison is not described as a pristine unseen holdout. No tuning was done
after inspecting the challenger evaluation results. The input is a revised
historical snapshot, not a reconstruction of what the NYC API exposed at each
past origin. A prospective evaluation remains necessary for that claim.

## Feature contract

`src/features.py` builds one row per origin × target × category × area.

- Known calendar: horizon 1–7, target weekday, weekend flag, day of month.
- History ending at origin: last observed count and count seven days earlier.
- Rolling statistics: means over 7/14/28 days; population standard deviations
  over 7/28 days; seven-day minimum and maximum.
- Recent trend: last seven-day mean minus the preceding seven-day mean.
- Same-weekday history: target minus 7, 14, 21, and 28 days.
- Same-weekday mean and latest-minus-mean trend.
- Category and area, using categorical mappings learned from training rows only.

Only the explicit feature allowlist reaches LightGBM. Actual future counts,
status/closure fields, raw dates, and audit columns are excluded. Unknown model
categories are rejected rather than silently assigned a learned value.

Every origin needs 28 complete daily observations. Both baselines and LightGBM
are evaluated on the same complete seven-day segment windows; missing history
or targets are recorded as skips, never artificial zeros. All eligibility/fit
skip reports were empty for the supplied snapshot.

Daily training origins produce multiple horizon examples sharing a target date.
Training weights give each unique target/category/area equal total weight.
These rows are not independent observations. At the first evaluation fit there
were 1,176 training examples but only 177 unique target/category/area labels;
at the last fit, 1,764 examples and 261 unique labels. The 200,424 source request
records must not be presented as 200,424 independent model training examples.

## Model and selection

Pooled direct LightGBM GBDT with a Poisson objective, CPU execution, seed 42,
one thread, deterministic/column-wise settings, learning rate 0.05, L2 penalty
5, and 31 histogram bins. The three candidate configurations were specified
before fitting:

| Candidate | Leaves | Depth | Trees | Minimum child samples | Validation WAPE |
| --- | ---: | ---: | ---: | ---: | ---: |
| shallow_60 | 7 | 3 | 60 | 30 | 23.98% |
| shallow_120 | 7 | 3 | 120 | 30 | 18.81% |
| regularized_120 | 15 | 4 | 120 | 50 | 18.26% |

`regularized_120` was the best GBT candidate. The four-week mean still had lower
validation WAPE, **17.47%**, so it remained the preferred policy before the final
comparison. Candidates are ranked using pooled paired MAE, which gives the same
ranking as WAPE when evaluated on identical targets with positive total demand.
There is no random train/test split, test-set early stopping, or search across
evaluation scores. Zero-total training labels produce an explicit constant-zero
bundle; normal count predictions are required to be finite and nonnegative.

LightGBM objective and categorical/model APIs are documented in the [official
parameters guide](https://lightgbm.readthedocs.io/en/v4.6.0/Parameters.html) and
[regressor reference](https://lightgbm.readthedocs.io/en/v4.6.0/pythonapi/lightgbm.LGBMRegressor.html).

## Matched comparison results

**July 27–August 30: five origins, 105 forecasts per model, 39,083 total actual
requests per model.**

| Model | MAE (requests/category/day) | WAPE | Bias (actual − prediction) |
| --- | ---: | ---: | ---: |
| Four-week weekday mean | **31.97** | **8.59%** | +9.10 |
| Seasonal naive | 34.70 | 9.32% | +2.51 |
| LightGBM | 39.09 | 10.50% | +12.90 |

Positive bias means underforecasting. The candidate has about 22.26% more
absolute error than the four-week mean on these paired targets; this is not an
improvement over the earlier 11.67% benchmark.

| Category | Four-week mean WAPE | LightGBM WAPE |
| --- | ---: | ---: |
| Blocked Driveway | 9.03% | **8.29%** |
| Illegal Parking | **6.82%** | 9.97% |
| Noise - Residential | **11.81%** | 12.65% |

The biggest gap is Illegal Parking, where LightGBM underforecast by an average
43.43 requests/day. This is an observed error pattern, not proof of a causal
explanation. Feature gain is dominated by same-weekday history, but importance
is descriptive and cannot establish causality. Five evaluation weeks do not
support a strong statistical-significance or annual-seasonality claim.

Do not select a new category-specific ensemble from this evaluation table and
then report that same table as independent evidence of its performance.

## Artifacts and replay

`reports/step3_archive/` contains:

- `selection.json`: frozen parameters and validation-selected policy.
- `validation_leaderboard.csv` and all three candidates' validation predictions.
- `validation/` and `evaluation/`: paired predictions; MAE/WAPE/bias overall,
  by category, horizon, origin, and category × horizon; paired error differences.
- `evaluation_training_audit.csv`: cutoffs, target bounds, row/unique-label
  counts, and training-data hashes for each weekly fit.
- `validation_features.csv`, `evaluation_features.csv`, and `daily_input.csv`.
- Feature/fit skip reports and input/config/source-code provenance metadata.
- `models/<origin>/model.txt` and `metadata.json`: native LightGBM text models,
  training-only categorical maps, feature order, cutoffs, and model checksums.
- `feature_importance.csv`: gain importance of the last evaluated model.
- `saved_model_forecast.csv`: a replay of that model's 21 predictions.

To replay the last saved model without needing future actual observations:

```bash
python -m src.predict --source archives \
  --model-dir reports/step3_archive/models/2026-08-23 \
  --origin 2026-08-23 \
  --output reports/replayed_forecast.csv
```

The replay CLI supplies only the preceding 28 complete days, verifies the saved
model checksum, and rejects a model trained after the requested origin. It
returns all three forecasts; it does not silently promote the challenger.
For a later date, ingest complete live history first and use `--source database`.
A saved model is not automatically retrained when running inference.

## Verification and limits

- Step 2 checkpoint: the user reported **all 46 tests passed on native PostgreSQL**
  and confirmed matching archive/database baseline results.
- Step 3 suite in this environment: **48 passed, 14 native PostgreSQL tests
  skipped**. All 16 new model/feature tests executed. The 14 DB tests are unchanged.
- Tests cover future-observation invariance of features and trained predictions,
  matured-label guards, future-data invariance of candidate selection, training-only
  category mapping, paired baseline equality with Step 2, missing-day exclusion,
  model serialization/checksum integrity, and inference without future labels.
- Actual archived-data selection and evaluation completed without skips.
- Reloading the final saved model reproduced all 21 final-origin predictions.
- This turn did not connect to the user's PostgreSQL instance or execute the
  new database-source CLI there. Run the commands above for local verification.

The model implementation is complete even though it did not win. Further model
experiments should use more historical development data and a newly reserved
or prospective evaluation period. The four-month snapshot is a limited basis
for learning annual seasonality or rare operational disruptions.
