"""Validate saved evidence with read-only SQLite opens; never rerun the demo.

Manifest hashes bind bytes and current source, not an independent witness. A party
who replaces every artifact and manifest can manufacture a consistent dataset.
"""
import copy
import hashlib
from pathlib import Path

from . import core, formats as f
from .demo import ARTIFACT_NAMES, CASES, RECORDED_FILES, artifact_checks, assert_controls, source_files
from .files import read_bytes, read_json
from .projection import read_projection
from .reducer import counts, reduce_events, state_digest
from .storage import database, check_schema


def _same(a, b):
    if f.canonical(a) != f.canonical(b):
        raise f.AuditError('recorded_mismatch')


def _projection(snapshot, view, anchor, through):
    state = reduce_events(snapshot.records[:through])
    _same(view['state'], state)
    head = snapshot.records[through-1]['event_hash'] if through else f.digest('audit-genesis-v1', snapshot.header)
    verified = core.verification(snapshot, anchor)
    expected_meta = {'singleton': 1, 'source_ledger_id': snapshot.header['ledger_id'],
                     'source_snapshot_seq': len(snapshot.records), 'source_snapshot_hash': snapshot.head_hash,
                     'through_seq': through, 'through_hash': head, 'reducer_version': 'projection-v1',
                     'state_digest': state_digest(state), 'source_origin': snapshot.origin,
                     'source_export_digest': snapshot.source_export_digest, 'anchor_digest': verified['checkpoint_digest']}
    _same(view['meta'], expected_meta)
    return {'format': 'audit-replay-v1', 'verification': verified, 'through_seq': through,
            'through_hash': head, 'state_digest': state_digest(view['state']), 'counts': counts(view['state']),
            'provenance': {'source_origin': snapshot.origin, 'source_export_digest': snapshot.source_export_digest,
                           'reducer_version': 'projection-v1'}}


def _intervention(path, snapshot, case):
    """Confirm the controlled copy differs only in the intervention described."""
    records = copy.deepcopy(snapshot.records)
    head, seq = snapshot.head_hash, len(records)
    if case == 'payload_changed':
        records[1]['payload']['content_digest'] = '0' * 64
    elif case == 'middle_deleted':
        records.pop(1)
    elif case == 'tail_truncated':
        records.pop()
        seq, head = len(records), records[-1]['event_hash']
    expected_meta = {'singleton': 1, 'header_json': f.canonical(snapshot.header).decode(),
                     'head_seq': seq, 'head_hash': head, 'origin': snapshot.origin,
                     'source_export_digest': snapshot.source_export_digest}
    with database(path, readonly=True) as c:
        c.execute('BEGIN')
        check_schema(c)
        _same([dict(r) for r in c.execute('SELECT * FROM meta').fetchmany(2)], [expected_meta])
        rows = [dict(r) for r in c.execute('SELECT * FROM events ORDER BY seq').fetchmany(1001)]
        expected_rows = [{'seq': r['seq'], 'event_id': r['event_id'], 'record_json': f.canonical(r).decode()}
                         for r in records]
        _same(rows, expected_rows)
        c.execute('COMMIT')


def validate_recorded(root):
    root = Path(root)
    try:
        manifest = read_json(root / 'recorded-manifest.json')
        f.closed(manifest, 'format source_files artifacts')
        if manifest['format'] != 'audit-recorded-manifest-v1':
            raise f.AuditError('recorded_mismatch')
        _same(manifest['source_files'], source_files())
        expected_artifacts = [{'path': name, 'sha256': hashlib.sha256(read_bytes(root/name, f.MAX_DB)).hexdigest()}
                              for name in sorted(RECORDED_FILES)]
        _same(manifest['artifacts'], expected_artifacts)
        report = read_json(root / 'demo-result.json')
        cp = f.checkpoint(read_json(root / 'checkpoint.json', 4096))
        cp_digest = f.digest('audit-checkpoint-v1', cp)
        anchor = (cp, cp_digest)
        snapshot = core.read_snapshot(root / 'ledger.sqlite', anchor, readonly=True)
        exported = f.export(read_json(root / 'export.json'))
        core.snapshot_from_export(exported)
        _same(exported, core.export_value(snapshot))
        # Fixed demo shape, not a generic import mechanism.
        if len(snapshot.records) != 4 or snapshot.origin != 'local':
            raise f.AuditError('recorded_mismatch')
        if [r['type'] for r in snapshot.records] != ['run_started', 'artifact_recorded', 'artifact_recorded', 'run_finished']:
            raise f.AuditError('recorded_mismatch')
        if [r['payload']['artifact_id'] for r in snapshot.records[1:3]] != list(ARTIFACT_NAMES):
            raise f.AuditError('recorded_mismatch')
        _same(cp, {'format': 'audit-checkpoint-v1', 'header': snapshot.header, 'seq': 4, 'head_hash': snapshot.head_hash})
        prefix = read_projection(root / 'projection-prefix.sqlite', readonly=True)
        full = read_projection(root / 'projection-full.sqlite', readonly=True)
        for case in CASES:
            _intervention(root / ('tamper-'+case+'.sqlite'), snapshot, case)
        checks = [{'case': case,
                   'without_anchor': core.verify(root / ('tamper-'+case+'.sqlite'), readonly=True),
                   'with_anchor': core.verify(root / ('tamper-'+case+'.sqlite'), anchor, readonly=True)}
                  for case in CASES]
        expected = {'format': 'audit-demo-v1', 'initial_verification': core.verification(snapshot),
                    'checkpoint': cp, 'checkpoint_digest': cp_digest,
                    'replay_prefix': _projection(snapshot, prefix, anchor, 3),
                    'replay_full': _projection(snapshot, full, anchor, 4),
                    'artifact_checks': artifact_checks(root, snapshot), 'tamper_checks': checks}
        _same(report, expected)
        assert_controls(expected)
        result = {'format': 'audit-inspector-v1', 'demo': report, 'export': exported,
                  'checkpoint': cp, 'projections': {'prefix': prefix, 'full': full}, 'manifest': manifest}
        f.bounded(result, f.MAX_JSON-1)
        return result
    except (KeyError, TypeError, IndexError, AttributeError) as exc:
        raise f.AuditError('recorded_mismatch') from exc
