import gzip,hashlib,json
from datetime import date
import pytest
from src.archive_demand import load_archives
from tests.test_ingestion import RECORD


def manifest(tmp_path,record=RECORD,expected=1):
    p=tmp_path/'page.gz'
    with gzip.open(p,'wt') as f:f.write(json.dumps(record)+'\n')
    m={'coverage_basis':'owner_attested_successful_reconciled_live_runs','agency':'NYPD','borough':'BROOKLYN',
       'partitions':[{'start':'2026-09-09','end':'2026-09-09','mode':'backfill','status':'ok','expected_rows':expected,
                      'files':[{'path':'page.gz','rows':1,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}]}]}
    path=tmp_path/'manifest.json';path.write_text(json.dumps(m));return path


def test_valid_archive_and_missing_dates(tmp_path,cfg):
    d,e=load_archives(manifest(tmp_path),cfg,date(2026,9,8),date(2026,9,9))
    assert d.request_count.sum()==1 and d.is_complete.sum()==3
    assert d.loc[d.demand_date==date(2026,9,8),'request_count'].isna().all()


def test_tampered_archive_rejected(tmp_path,cfg):
    p=manifest(tmp_path);(tmp_path/'page.gz').write_bytes(b'changed')
    with pytest.raises(ValueError,match='checksum'):load_archives(p,cfg,date(2026,9,9),date(2026,9,9))


def test_incomplete_partition_rejected(tmp_path,cfg):
    with pytest.raises(ValueError,match='partition count'):
        load_archives(manifest(tmp_path,expected=2),cfg,date(2026,9,9),date(2026,9,9))
