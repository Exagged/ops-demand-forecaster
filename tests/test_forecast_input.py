from datetime import date, datetime, timezone
from src.demand import load_database
from src.ingest_311 import where_clause
from tests.test_ingestion import load, RECORD

DAY=date(2026,9,9)


class Cursor:
    def __init__(self,runs,counts):self.responses=[runs,counts]
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def execute(self,*args):pass
    def fetchall(self):return self.responses.pop(0)


class Connection:
    def __init__(self,runs,counts=()):self.c=Cursor(runs,counts)
    def cursor(self):return self.c


def test_scope_mismatch_cannot_certify_zero_day(cfg):
    d=load_database(Connection([(DAY,DAY,datetime(2026,9,11,tzinfo=timezone.utc),'wrong scope')]),cfg,DAY,DAY)
    assert not d.is_complete.any()
    assert d.request_count.isna().all()


def test_nyc_midnight_not_utc_controls_completeness(cfg):
    query=where_clause(cfg,DAY,DAY)
    # Sep 10 02:00 UTC is still Sep 9 in New York.
    d=load_database(Connection([(DAY,DAY,datetime(2026,9,10,2,tzinfo=timezone.utc),query)]),cfg,DAY,DAY)
    assert not d.is_complete.any()
    d=load_database(Connection([(DAY,DAY,datetime(2026,9,10,4,tzinfo=timezone.utc),query)]),cfg,DAY,DAY)
    assert d.is_complete.all() and d.request_count.eq(0).all()


def test_fixture_run_never_certifies_coverage_or_counts(db,cfg):
    load(db,cfg,[RECORD],mode='fixtures')
    d=load_database(db,cfg,DAY,DAY)
    assert not d.is_complete.any()
    assert db.execute('SELECT count(*) FROM forecast_live_request').fetchone()[0]==0


def test_live_plus_fixture_counts_only_live(db,cfg):
    run=load(db,cfg,[RECORD])
    db.execute('UPDATE ingestion_run SET query=%s WHERE run_id=%s',(where_clause(cfg,DAY,DAY),run['run_id']))
    db.commit()
    load(db,cfg,[dict(RECORD,unique_key='999999999')],mode='fixtures')
    d=load_database(db,cfg,DAY,DAY)
    assert d.is_complete.all() and d.request_count.sum()==1


def test_fixture_collision_does_not_override_live_category(db,cfg):
    run=load(db,cfg,[RECORD])
    db.execute('UPDATE ingestion_run SET query=%s WHERE run_id=%s',(where_clause(cfg,DAY,DAY),run['run_id']))
    db.commit()
    load(db,cfg,[dict(RECORD,complaint_type='Illegal Parking')],mode='fixtures')
    d=load_database(db,cfg,DAY,DAY)
    assert d.loc[d.category=='Noise - Residential','request_count'].iloc[0]==1
    assert d.loc[d.category=='Illegal Parking','request_count'].iloc[0]==0


def test_identical_live_record_after_fixture_is_recovered(db,cfg):
    load(db,cfg,[RECORD],mode='fixtures')
    load(db,cfg,[RECORD],mode='backfill')
    assert db.execute('SELECT count(*) FROM forecast_live_request').fetchone()[0]==1


def test_failed_edit_cannot_override_successful_live_record(db,cfg):
    load(db,cfg,[RECORD])
    load(db,cfg,[dict(RECORD,complaint_type='Illegal Parking')],status='failed')
    assert db.execute('SELECT category FROM forecast_live_request').fetchone()[0]=='Noise - Residential'


def test_live_reversion_then_failed_edit_keeps_latest_live_observation(db,cfg):
    load(db,cfg,[RECORD])
    changed=dict(RECORD,complaint_type='Illegal Parking')
    load(db,cfg,[changed])
    load(db,cfg,[RECORD])
    load(db,cfg,[changed],status='failed')
    assert db.execute('SELECT category FROM forecast_live_request').fetchone()[0]=='Noise - Residential'


def test_fixture_cli_refuses_live_database(db,cfg):
    from src.ingest_311 import ingest_fixtures
    import pytest
    load(db,cfg,[RECORD])
    with pytest.raises(ValueError,match='blocked'):
        ingest_fixtures(cfg)
