from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import shutil
import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest
from src.features import feature_row
from src.gbt import ModelBundle
from src.predict import forecast_saved
from src.planner import load_release, staffing, catalog

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT/'artifacts'/'nypd_brooklyn_demo'


def test_observed_holiday_and_neighbors_known_at_origin():
    origin = date(2026,6,28)
    series = {origin-timedelta(days=i):10 for i in range(28)}
    assert feature_row(series,origin,date(2026,7,3),'Noise','BROOKLYN')['target_holiday'] == 1
    assert feature_row(series,origin,date(2026,7,2),'Noise','BROOKLYN')['target_before_holiday'] == 1
    assert feature_row(series,origin,date(2026,7,4),'Noise','BROOKLYN')['target_after_holiday'] == 1


def test_release_prediction_replay_without_future_labels():
    meta, forecast = load_release(DEMO)
    history = pd.read_csv(DEMO/'history.csv')
    history['demand_date'] = pd.to_datetime(history.demand_date).dt.date
    model = ModelBundle.load(DEMO/'model')
    replay = forecast_saved(model,history,date.fromisoformat(meta['origin']))
    np.testing.assert_allclose(replay.lightgbm,forecast.lightgbm,rtol=1e-12)
    assert model.metadata['max_training_target'] == meta['origin']
    assert len(forecast) == 21


def test_staffing_uses_pooled_daily_ceiling():
    frame = pd.DataFrame({'target_date':[date(2026,9,1)]*2,'category':['a','b'], 'weekday_mean_4w':[10.1,9.9]})
    result = staffing(frame,['a','b'],'weekday_mean_4w',20,0)
    assert result.required_staff.iloc[0] == 1
    assert result.unserved_requests.iloc[0] == 20
    assert staffing(frame,['a'],'weekday_mean_4w',10,1).required_staff.iloc[0] == 2


@pytest.mark.parametrize('throughput',[0,-1,float('nan'),float('inf')])
def test_invalid_throughput_rejected(throughput):
    _, frame = load_release(DEMO)
    with pytest.raises(ValueError):
        staffing(frame,list(frame.category.unique()),'lightgbm',throughput,1)


def test_empty_scope_rejected():
    _, frame = load_release(DEMO)
    with pytest.raises(ValueError):
        staffing(frame,[],'lightgbm',20,1)


def test_tampered_release_rejected(tmp_path):
    shutil.copytree(DEMO,tmp_path/'release')
    with (tmp_path/'release'/'forecast.csv').open('a') as f:
        f.write('corruption')
    with pytest.raises(ValueError,match='checksum'):
        load_release(tmp_path/'release')
    found, errors = catalog(tmp_path)
    assert not found and len(errors)==1


def test_wrong_scope_rejected(tmp_path):
    shutil.copytree(DEMO,tmp_path/'release')
    p = tmp_path/'release'/'release.json'
    meta = json.loads(p.read_text()); meta['scope']['borough']='QUEENS'
    p.write_text(json.dumps(meta))
    with pytest.raises(ValueError,match='scope'):
        load_release(p.parent)


def test_app_changes_capacity_and_handles_empty_categories():
    at = AppTest.from_file(str(ROOT/'app.py'),default_timeout=20).run()
    assert not at.exception
    before = at.metric[1].value
    at.number_input[0].set_value(40.0).run()
    assert not at.exception and at.metric[1].value != before
    at.multiselect[0].set_value([]).run()
    assert not at.exception and any('at least one' in x.value for x in at.info)


def test_app_handles_no_releases(tmp_path,monkeypatch):
    monkeypatch.setenv('OPS_ARTIFACT_DIR',str(tmp_path))
    at = AppTest.from_file(str(ROOT/'app.py')).run()
    assert not at.exception and 'No validated forecasts' in at.info[0].value


def test_app_supports_multiple_agencies_and_boroughs(tmp_path,monkeypatch):
    # Synthetic alternate scope used only for UI isolation tests, never shipped as a forecast.
    shutil.copytree(DEMO,tmp_path/'one')
    shutil.copytree(DEMO,tmp_path/'two')
    p = tmp_path/'two'; meta=json.loads((p/'release.json').read_text())
    meta['scope']['agency']='TEST'; meta['scope']['borough']='QUEENS'
    for name in ['forecast.csv','history.csv']:
        frame=pd.read_csv(p/name); frame['area']='QUEENS'; frame.to_csv(p/name,index=False)
        meta['checksums'][name]=hashlib.sha256((p/name).read_bytes()).hexdigest()
    (p/'release.json').write_text(json.dumps(meta))
    monkeypatch.setenv('OPS_ARTIFACT_DIR',str(tmp_path))
    at=AppTest.from_file(str(ROOT/'app.py')).run()
    at.selectbox[0].set_value('TEST').run()
    assert not at.exception and at.selectbox[1].value=='QUEENS'


def test_publication_rejects_pre_evaluation_origin(tmp_path):
    from src.pipeline import publish
    from src.config import load_config
    with pytest.raises(ValueError,match='evaluation target end'):
        publish(pd.DataFrame(),load_config(),date(2026,8,1),ROOT/'reports/end_to_end_archive',tmp_path/'out')
