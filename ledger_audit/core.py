"""Atomic append, independently pinned verification, and portable JSON transfer."""
from datetime import datetime, timezone
from pathlib import Path
import uuid

from . import formats as f
from .files import new_target, read_json, write_json
from .reducer import reduce_events
from .storage import database, scan, create_ledger, validate_chain, Snapshot


def load_anchor(checkpoint_path=None, expected_digest=None):
    if (checkpoint_path is None) != (expected_digest is None):
        raise f.AuditError('invalid_input')
    if checkpoint_path is None:
        return None
    f.pattern(expected_digest, f.HASH)
    return (f.checkpoint(read_json(checkpoint_path, 4096)), expected_digest)


def verification(snapshot, anchor=None):
    h, records = snapshot.header, snapshot.records
    n = len(records)
    result = {'format': 'audit-verify-v1', 'ledger_id': h['ledger_id'], 'chain': 'consistent',
              'snapshot_seq': n, 'head_hash': snapshot.head_hash, 'anchor': 'none',
              'checkpoint_digest': None, 'anchored_seq': 0, 'unanchored_tail': n, 'failure': None}
    if anchor is not None:
        cp, expected = anchor
        computed = f.digest('audit-checkpoint-v1', cp)
        result['checkpoint_digest'] = computed
        failure = None
        if computed != expected:
            failure = 'checkpoint_digest_mismatch'
        elif cp['header'] != h:
            failure = 'anchor_mismatch'
        elif n < cp['seq']:
            failure = 'truncated_before_anchor'
        else:
            head = records[cp['seq'] - 1]['event_hash'] if cp['seq'] else f.digest('audit-genesis-v1', h)
            if head != cp['head_hash']:
                failure = 'anchor_mismatch'
        if failure:
            result.update(anchor='failed', anchored_seq=None, unanchored_tail=None,
                          failure={'code': failure, 'seq': cp['seq']})
        else:
            result.update(anchor='exact' if n == cp['seq'] else 'prefix',
                          anchored_seq=cp['seq'], unanchored_tail=n-cp['seq'])
    return result


def require_anchor(snapshot, anchor):
    result = verification(snapshot, anchor)
    if result['failure']:
        raise f.IntegrityFailure(result['failure']['code'], result['failure']['seq'])
    return result


def invalid_verification(exc):
    return {'format': 'audit-verify-v1', 'ledger_id': None, 'chain': 'invalid',
            'snapshot_seq': None, 'head_hash': None, 'anchor': 'failed',
            'checkpoint_digest': None, 'anchored_seq': None, 'unanchored_tail': None,
            'failure': {'code': exc.code, 'seq': exc.seq}}


def read_snapshot(path, anchor=None, *, readonly=False):
    with database(path, readonly=readonly) as c:
        c.execute('BEGIN')
        snapshot = scan(c)
        require_anchor(snapshot, anchor)
        c.execute('COMMIT')
        return snapshot


def verify(path, anchor=None, *, readonly=False):
    try:
        with database(path, readonly=readonly) as c:
            c.execute('BEGIN')
            snapshot = scan(c)
            result = verification(snapshot, anchor)
            c.execute('COMMIT')
            return result
    except f.IntegrityFailure as exc:
        return invalid_verification(exc)


def file_result(operation, snapshot, artifact_digest=None):
    return {'format': 'audit-file-result-v1', 'operation': operation,
            'ledger_id': snapshot.header['ledger_id'], 'count': len(snapshot.records),
            'head_hash': snapshot.head_hash, 'artifact_digest': artifact_digest,
            'origin': snapshot.origin}


def init(path):
    h = {'format': 'audit-header-v1', 'ledger_id': uuid.uuid4().hex,
         'event_version': 'event-v1', 'reducer_version': 'projection-v1'}
    with new_target(path) as temp:
        with database(temp, initialize=True) as c:
            c.execute('BEGIN IMMEDIATE')
            create_ledger(c, h)
            c.execute('COMMIT')
        snapshot = read_snapshot(temp)
    return file_result('init', snapshot)


def append(path, value, anchor=None):
    f.event_input(value)
    with database(path) as c:
        c.execute('BEGIN IMMEDIATE')
        snapshot = scan(c)
        require_anchor(snapshot, anchor)
        for r in snapshot.records:
            if r['event_id'] == value['event_id']:
                old = f.input_from_record(r)
                if f.digest('audit-input-v1', old) != f.digest('audit-input-v1', value) or f.canonical(old) != f.canonical(value):
                    raise f.AuditError('conflict', 1)
                c.execute('COMMIT')
                return {'format': 'audit-append-v1', 'disposition': 'existing', 'record': r}
        if len(snapshot.records) >= f.MAX_EVENTS:
            raise f.AuditError('limit_exceeded')
        r = {'seq': len(snapshot.records)+1, **value,
             'recorded_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
             'prev_hash': snapshot.head_hash}
        r['event_hash'] = f.event_hash(snapshot.header, r)
        f.record(r)
        reduce_events([*snapshot.records, r])
        c.execute('INSERT INTO events VALUES(?,?,?)', (r['seq'], r['event_id'], f.canonical(r).decode()))
        c.execute('UPDATE meta SET head_seq=?,head_hash=? WHERE singleton=1', (r['seq'], r['event_hash']))
        c.execute('COMMIT')
        return {'format': 'audit-append-v1', 'disposition': 'appended', 'record': r}


def checkpoint(path, output):
    snapshot = read_snapshot(path)
    cp = {'format': 'audit-checkpoint-v1', 'header': snapshot.header,
          'seq': len(snapshot.records), 'head_hash': snapshot.head_hash}
    f.checkpoint(cp)
    write_json(output, cp, 4096)
    return file_result('checkpoint', snapshot, f.digest('audit-checkpoint-v1', cp))


def export_value(snapshot):
    return {'format': 'audit-export-v1', 'header': snapshot.header,
            'count': len(snapshot.records), 'head_hash': snapshot.head_hash,
            'events': snapshot.records,
            'provenance': {'producer': 'agent-audit-ledger', 'version': 'v1',
                           'source_origin': snapshot.origin,
                           'source_export_digest': snapshot.source_export_digest}}


def export_file(path, output, anchor=None):
    snapshot = read_snapshot(path, anchor)
    value = f.export(export_value(snapshot))
    write_json(output, value)
    return file_result('export', snapshot, f.digest('audit-export-v1', value))


def snapshot_from_export(value):
    f.export(value)
    state = validate_chain(value['header'], value['events'], value['count'], value['head_hash'])
    return Snapshot(value['header'], value['events'], value['head_hash'],
                    value['provenance']['source_origin'], value['provenance']['source_export_digest'], state)


def import_file(source, output, anchor=None):
    value = read_json(source)
    snapshot = snapshot_from_export(value)
    require_anchor(snapshot, anchor)
    source_digest = f.digest('audit-export-v1', value)
    with new_target(output) as temp:
        with database(temp, initialize=True) as c:
            c.execute('BEGIN IMMEDIATE')
            create_ledger(c, snapshot.header, snapshot.records, 'imported', source_digest)
            c.execute('COMMIT')
        copied = read_snapshot(temp, anchor)
    return file_result('import', copied, source_digest)
