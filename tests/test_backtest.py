from datetime import date, timedelta
import pandas as pd
import pytest
from src.baselines import predict
from src.backtest import evaluate, summarize

ORIGIN = date(2026, 5, 31)


def grid(days=42):
    start = ORIGIN-timedelta(days=27)
    return pd.DataFrame([dict(demand_date=start+timedelta(days=i),category='Noise',area='Brooklyn',
                              request_count=(i%7+1)*10, is_complete=True) for i in range(days)])


def test_baselines_exact_arithmetic_and_weekday_alignment():
    history = {ORIGIN-timedelta(days=i): 28-i for i in range(28)}
    p = predict(history, ORIGIN)
    assert p['seasonal_naive'] == list(map(float, range(22,29)))
    assert p['weekday_mean_4w'] == [x-10.5 for x in range(22,29)]


def test_future_mutation_cannot_change_forecast():
    d=grid(); original,_=evaluate(d,ORIGIN,ORIGIN)
    d.loc[d.demand_date>ORIGIN,'request_count']=100000
    mutated,_=evaluate(d,ORIGIN,ORIGIN)
    assert original.prediction.tolist()==mutated.prediction.tolist()
    assert original.actual.tolist()!=mutated.actual.tolist()


def test_predict_defensively_ignores_future_keys():
    h={ORIGIN-timedelta(days=i): 5 for i in range(28)}
    expected=predict(h,ORIGIN)
    h.update({ORIGIN+timedelta(days=i):999 for i in range(1,8)})
    assert predict(h,ORIGIN)==expected


def test_perfect_weekly_series():
    predictions,skips=evaluate(grid(),ORIGIN,ORIGIN+timedelta(days=7))
    assert len(predictions)==28
    assert skips.empty
    assert predictions.absolute_error.sum()==0
    assert set(predictions.horizon_days)==set(range(1,8))


@pytest.mark.parametrize('offset,reason',[(-10,'incomplete_28_day_history'),(3,'incomplete_target_window')])
def test_incomplete_days_skip_both_models_and_whole_window(offset,reason):
    d=grid();d.loc[d.demand_date==ORIGIN+timedelta(days=offset),'is_complete']=False
    predictions,skips=evaluate(d,ORIGIN,ORIGIN)
    assert predictions.empty
    assert skips.reason.tolist()==[reason]


def test_absent_day_is_not_a_zero():
    d=grid();d=d[d.demand_date!=ORIGIN-timedelta(days=20)]
    p,s=evaluate(d,ORIGIN,ORIGIN)
    assert p.empty and len(s)==1


def test_complete_zeros_are_valid_and_wape_is_undefined():
    d=grid();d['request_count']=0
    p,s=evaluate(d,ORIGIN,ORIGIN)
    m=summarize(p,['model'])
    assert not p.empty and s.empty
    assert m.mae.eq(0).all() and m.wape.isna().all()


def test_short_history_no_partial_baseline_comparison():
    p,s=evaluate(grid().iloc[10:],ORIGIN,ORIGIN)
    assert p.empty and len(s)==1


def test_duplicate_daily_rows_rejected():
    d=grid()
    with pytest.raises(ValueError,match='duplicate'):
        evaluate(pd.concat([d,d.iloc[:1]]),ORIGIN,ORIGIN)


@pytest.mark.parametrize('bad',[None,-1,float('inf'),1.5])
def test_invalid_complete_counts_rejected(bad):
    d=grid();d['request_count']=d.request_count.astype(float);d.loc[0,'request_count']=bad
    with pytest.raises(ValueError,match='counts'):
        evaluate(d,ORIGIN,ORIGIN)


def test_segment_isolation():
    a=grid();b=grid();b['category']='Parking';b['request_count']*=3
    p,_=evaluate(pd.concat([a,b]),ORIGIN,ORIGIN)
    assert len(p)==28
    assert p.absolute_error.sum()==0


def test_weighted_wape_not_mean_daily_percentage():
    p=pd.DataFrame({'model':['a','a'],'actual':[1,99],'absolute_error':[1,9],'error':[1,-9]})
    m=summarize(p,['model']).iloc[0]
    assert m.wape==pytest.approx(.1)
    assert m.mae==5 and m.bias==-4


@pytest.mark.parametrize('h',[0,8])
def test_invalid_horizon(h):
    with pytest.raises(ValueError):predict({},ORIGIN,h)
