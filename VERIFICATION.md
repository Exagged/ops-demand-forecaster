# End-to-end verification — September 12, 2026

- Full archive pipeline completed with fixed candidate settings and new observed-holiday features.
- 62 pytest tests passed; 14 native PostgreSQL integration tests skipped because no PostgreSQL server was available here. The user previously verified the Step 2 PostgreSQL suite locally. No new SQL migration was introduced.
- Streamlit AppTest exercised rendering, scope changes, throughput changes, empty selections and missing releases. The live Streamlit HTTP health endpoint returned `ok` in a subprocess smoke test.
- Final model reload reproduced the persisted 21 LightGBM forecasts at numerical tolerance 1e-12. Final fit cutoff and maximum training target: August 31, 2026. Forecast horizon: September 1–7.
- Corrupted releases are rejected; final releases publish only after complete validation and are never silently overwritten.

| Model | Matched predictions | MAE | WAPE | Bias (actual − forecast) |
|---|---:|---:|---:|---:|
| Four-week weekday mean | 105 | 31.9714 | 8.5894% | 9.1048 |
| Seasonal naïve | 105 | 34.7048 | 9.3237% | 2.5143 |
| LightGBM, holiday features | 105 | 38.5138 | 10.3471% | 16.0015 |

Evaluation targets: July 27–August 30. No skipped evaluation feature windows or fits. Full Step 2 weekday-mean WAPE is 11.6659% across June 1–August 30. The shorter-window LightGBM result does not demonstrate an improvement over that benchmark. The default remains weekday mean, chosen by validation MAE before this run's evaluation.

Known limits: retrospective revised records, previously inspected historical evaluation period, four months of one agency/borough data, assumed interchangeable staff productivity, no calibrated intervals or queue simulation. These are documented in the UI and runbook. Native PostgreSQL integration and visual rendering on the user's Mac remain local checks.
