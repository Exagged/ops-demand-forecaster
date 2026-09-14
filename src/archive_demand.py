"""Replay explicitly attested archive partitions, validating every file and row.

Hashes/counts prove integrity of this supplied export, not upstream completeness.
Coverage is accepted only from the manifest's explicit owner attestation.
"""
from collections import Counter
from datetime import date
import gzip
import hashlib
import json
from pathlib import Path
from src.demand import date_range, make_grid
from src.normalize import normalize


def load_archives(manifest_path, cfg, start, end):
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    if manifest.get('coverage_basis') != 'owner_attested_successful_reconciled_live_runs':
        raise ValueError('archive coverage requires explicit run attestation')
    if manifest['agency'] != cfg.scope.agency or manifest['borough'] != cfg.scope.borough:
        raise ValueError('archive scope does not match config')
    records, covered, total = {}, set(), 0
    for partition in manifest['partitions']:
        lo, hi = date.fromisoformat(partition['start']), date.fromisoformat(partition['end'])
        if lo > hi or partition['mode'] != 'backfill' or partition['status'] != 'ok':
            raise ValueError('invalid archive partition')
        seen = set()
        for entry in partition['files']:
            path = manifest_path.parent / entry['path']
            if hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']:
                raise ValueError(f'archive checksum mismatch: {path.name}')
            file_rows = 0
            with gzip.open(path, 'rt') as handle:
                for line in handle:
                    raw = json.loads(line)
                    row = normalize(raw)
                    if not lo <= row['created_date'] <= hi:
                        raise ValueError('record outside attested partition')
                    if row['agency'] != cfg.scope.agency or row['borough'] != cfg.scope.borough:
                        raise ValueError('record outside attested agency/borough')
                    rid = row['request_id']
                    if rid in seen or rid in records:
                        raise ValueError('duplicate request ID in archive snapshot')
                    seen.add(rid)
                    records[rid] = row
                    file_rows += 1
            if file_rows != entry['rows']:
                raise ValueError('archive page row count mismatch')
        if len(seen) != partition['expected_rows']:
            raise ValueError('archive partition count mismatch')
        total += len(seen)
        covered.update(date_range(lo, hi))
    counts = Counter((r['created_date'], r['complaint_type'], r['borough'])
                     for r in records.values() if r['complaint_type'] in cfg.scope.categories)
    evidence = {'type': 'archives', 'coverage_basis': manifest['coverage_basis'],
                'manifest_sha256': hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                'unique_raw_records': total, 'scoped_records':sum(counts.values()),
                'note': 'Live run statuses supplied by owner; hashes/counts verified locally. No DB dump supplied.'}
    return make_grid(cfg, start, end, covered, counts), evidence
