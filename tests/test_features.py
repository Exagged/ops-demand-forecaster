from datetime import date, timedelta
import numpy as np
import pandas as pd
import pytest
from src.features import FEATURES, build_examples, training_slice, feature_row
from src.gbt import fit_model, ModelBundle
from src.train import walk_forward, select_candidate, assert_paired, protocol
from src.backtest import evaluate

START = date(2026,5,1)
ORIGIN = date(2026,6,28)
PARAMS = dict(num_leaves=7,max_depth=3,n_estimators=10,min_child_samples=10)


def grid(days=110):
    return pd.DataFrame([dict(demand_date=START+timedelta(days=i), category=c,area='BROOKLYN',
                              request_count=int(scale*(20+(i%7)*3+i//14)),is_complete=True)
                         for c,scale in [('Noise',1),('Parking',3)] for i in range(days)])


def examples(demand=None):
    d=grid() if demand is None else demand
    origins=[START+timedelta(days=i) for i in range(27,100)]
    return build_examples(d,origins,False)[0]


def test_feature_values_and_origin_alignment():
    series={ORIGIN-timedelta(days=i):float(28-i) for i in range(28)}
    row=feature_row(series,ORIGIN,ORIGIN+timedelta(days=7),'Noise','BROOKLYN')
    assert row['weekday_lag1']==28 and row['weekday_lag4']==7
    assert row['weekday_mean4']==17.5
    assert row['origin_last']==28 and row['origin_lag7']==21
    assert row['mean7']==25 and row['recent_trend']==7
    assert row['feature_max_date']==ORIGIN


def test_all_features_invariant_to_future_observation_mutation():
    d=grid(); before,_=build_examples(d,[ORIGIN])
    d.loc[d.demand_date>ORIGIN,'request_count']=999999
    after,_=build_examples(d,[ORIGIN])
    pd.testing.assert_frame_equal(before[FEATURES],after[FEATURES])
    assert not before.actual.equals(after.actual)


def test_missing_history_or_partial_evaluation_window_excluded():
    d=grid(); d.loc[d.demand_date==ORIGIN-timedelta(days=2),'is_complete']=False
    rows,skips=build_examples(d,[ORIGIN])
    assert rows.empty and set(skips.reason)=={'incomplete_28_day_history'}
    d=grid();d.loc[d.demand_date==ORIGIN+timedelta(days=4),'is_complete']=False
    rows,skips=build_examples(d,[ORIGIN])
    assert rows.empty and set(skips.reason)=={'incomplete_target_window'}


def test_training_targets_must_have_matured_at_cutoff():
    train=training_slice(examples(),ORIGIN)
    assert train.target_date.max()<=ORIGIN
    assert (train.feature_max_date<=train.origin_date).all()
    assert (train.origin_date<train.target_date).all()
    assert set(FEATURES).isdisjoint({'actual','target_date','closed_at','current_status','origin_date'})


def test_future_mutation_cannot_change_fitted_model_or_origin_predictions():
    d=grid(); all_examples=examples(d);future,_=build_examples(d,[ORIGIN])
    before=fit_model(training_slice(all_examples,ORIGIN),ORIGIN,PARAMS)
    d.loc[d.demand_date>ORIGIN,'request_count']=123456
    after=fit_model(training_slice(examples(d),ORIGIN),ORIGIN,PARAMS)
    assert before.metadata['training_sha256']==after.metadata['training_sha256']
    np.testing.assert_allclose(before.predict(future),after.predict(future),rtol=0,atol=0)


def test_guard_rejects_future_labels_at_model_boundary():
    train=training_slice(examples(),ORIGIN)
    train.loc[0,'target_date']=ORIGIN+timedelta(days=1)
    with pytest.raises(ValueError,match='immature'):fit_model(train,ORIGIN,PARAMS)


def test_model_bundle_roundtrip_and_tamper_detection(tmp_path):
    train=training_slice(examples(),ORIGIN);f,_=build_examples(grid(),[ORIGIN])
    model=fit_model(train,ORIGIN,PARAMS);model.save(tmp_path)
    loaded=ModelBundle.load(tmp_path)
    np.testing.assert_allclose(model.predict(f),loaded.predict(f),rtol=0,atol=0)
    with (tmp_path/'model.txt').open('a') as out:out.write('\ntampered\n')
    with pytest.raises(ValueError,match='checksum'):ModelBundle.load(tmp_path)


def test_encoder_uses_training_categories_only():
    train=training_slice(examples(),ORIGIN);f,_=build_examples(grid(),[ORIGIN])
    model=fit_model(train,ORIGIN,PARAMS);f.loc[0,'category']='NEW_UNSEEN_CATEGORY'
    with pytest.raises(ValueError,match='unseen'):model.predict(f)


def test_zero_training_data_has_valid_nonnegative_saved_predictions(tmp_path):
    d=grid();d['request_count']=0;train=training_slice(examples(d),ORIGIN)
    f,_=build_examples(d,[ORIGIN]);model=fit_model(train,ORIGIN,PARAMS)
    model.save(tmp_path)
    assert (ModelBundle.load(tmp_path).predict(f)==0).all()


def test_paired_baselines_exactly_match_step2():
    d=grid();f,_=build_examples(d,[ORIGIN]);train=examples(d)
    predictions,skips,audit=walk_forward(train,f,[ORIGIN],PARAMS)
    assert_paired(predictions)
    old,_=evaluate(d,ORIGIN,ORIGIN)
    new=predictions[predictions.model!='lightgbm']
    keys=['model','category','target_date']
    pd.testing.assert_frame_equal(old.sort_values(keys).reset_index(drop=True),new.sort_values(keys).reset_index(drop=True),check_dtype=False)
    assert skips.empty and (audit.max_training_target<=audit.origin_date).all()


def test_unpaired_comparisons_fail():
    f,_=build_examples(grid(),[ORIGIN]);p,_,_=walk_forward(examples(),f,[ORIGIN],PARAMS)
    with pytest.raises(ValueError,match='identical'):assert_paired(p.iloc[1:])


def test_insufficient_training_excludes_all_models_for_that_origin():
    f,_=build_examples(grid(),[ORIGIN])
    p,s,_=walk_forward(examples(),f,[ORIGIN],PARAMS,minimum_rows=100000)
    assert p.empty and len(s)==1


def test_selection_unchanged_by_post_validation_target_mutation():
    origins=[ORIGIN,ORIGIN+timedelta(days=7)]
    d=grid();f,_=build_examples(d,origins)
    candidates=[{'id':'small',**PARAMS},{'id':'larger',**PARAMS,'n_estimators':20}]
    selected,board,_=select_candidate(examples(d),f,origins,candidates,100)
    d.loc[d.demand_date>origins[-1]+timedelta(days=7),'request_count']=543210
    changed,_=build_examples(d,origins)
    selected2,board2,_=select_candidate(examples(d),changed,origins,candidates,100)
    assert selected==selected2
    pd.testing.assert_frame_equal(board,board2)


def test_protocol_rejects_overlapping_validation_targets(cfg):
    bad=dict(cfg.raw['modeling']);bad['evaluation_first_origin']='2026-07-19'
    with pytest.raises(ValueError,match='overlaps'):protocol(bad)


def test_saved_inference_needs_no_future_labels_and_matches_evaluation():
    from src.predict import forecast_saved
    d=grid();model=fit_model(training_slice(examples(d),ORIGIN),ORIGIN,PARAMS)
    past=d[d.demand_date<=ORIGIN].copy()
    output=forecast_saved(model,past,ORIGIN)
    f,_=build_examples(d,[ORIGIN])
    np.testing.assert_array_equal(output.lightgbm,model.predict(f))
    assert len(output)==14


def test_saved_inference_rejects_model_trained_after_origin():
    from src.predict import forecast_saved
    model=fit_model(training_slice(examples(),ORIGIN),ORIGIN,PARAMS)
    with pytest.raises(ValueError,match='trained after'):
        forecast_saved(model,grid(),ORIGIN-timedelta(days=7))
