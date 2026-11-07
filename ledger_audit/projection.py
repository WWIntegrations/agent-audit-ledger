"""Replay events as real SQL effects, then compare with the pure reducer."""
from . import formats as f
from .core import read_snapshot, verification
from .files import new_target
from .reducer import reduce_events, state_digest, counts
from .storage import database, check_schema

RUNS_SQL = """CREATE TABLE runs (
 run_id TEXT PRIMARY KEY NOT NULL, actor TEXT NOT NULL, input_digest TEXT NOT NULL,
 started_seq INTEGER NOT NULL, finished_seq INTEGER, outcome TEXT,
 CHECK((finished_seq IS NULL AND outcome IS NULL) OR
       (finished_seq IS NOT NULL AND outcome IN ('success','failed'))))"""
ARTIFACTS_SQL = """CREATE TABLE artifacts (
 run_id TEXT NOT NULL REFERENCES runs(run_id), artifact_id TEXT NOT NULL,
 content_digest TEXT NOT NULL, byte_count INTEGER NOT NULL CHECK(byte_count BETWEEN 0 AND 1048576),
 event_seq INTEGER NOT NULL, PRIMARY KEY(run_id,artifact_id))"""
PROJECTION_META_SQL = """CREATE TABLE projection_meta (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1), source_ledger_id TEXT NOT NULL,
 source_snapshot_seq INTEGER NOT NULL, source_snapshot_hash TEXT NOT NULL,
 through_seq INTEGER NOT NULL, through_hash TEXT NOT NULL, reducer_version TEXT NOT NULL,
 state_digest TEXT NOT NULL, source_origin TEXT NOT NULL, source_export_digest TEXT,
 anchor_digest TEXT)"""
PROJECTION_SCHEMA = [('table', 'runs', 'runs', RUNS_SQL),
                     ('table', 'artifacts', 'artifacts', ARTIFACTS_SQL),
                     ('table', 'projection_meta', 'projection_meta', PROJECTION_META_SQL),
                     ('index', 'sqlite_autoindex_runs_1', 'runs', None),
                     ('index', 'sqlite_autoindex_artifacts_1', 'artifacts', None)]


def sql_state(c):
    runs = [dict(r) for r in c.execute('SELECT * FROM runs ORDER BY run_id').fetchmany(1001)]
    artifacts = [dict(r) for r in c.execute('SELECT * FROM artifacts ORDER BY run_id,artifact_id').fetchmany(1001)]
    if len(runs) > 1000 or len(artifacts) > 1000:
        raise f.AuditError('limit_exceeded')
    for r in runs:
        f.pattern(r['run_id'])
        f.pattern(r['actor'])
        f.pattern(r['input_digest'], f.HASH)
        f.integer(r['started_seq'], 1, 1000)
        if r['outcome'] is None:
            if r['finished_seq'] is not None:
                raise f.AuditError('invalid_input')
        else:
            if r['outcome'] not in ('success', 'failed'):
                raise f.AuditError('invalid_input')
            f.integer(r['finished_seq'], r['started_seq'] + 1, 1000)
    for a in artifacts:
        f.pattern(a['run_id'])
        f.pattern(a['artifact_id'])
        f.pattern(a['content_digest'], f.HASH)
        f.integer(a['byte_count'], upper=1048576)
        f.integer(a['event_seq'], 1, 1000)
    return {'format': 'audit-state-v1', 'reducer_version': 'projection-v1',
            'runs': runs, 'artifacts': artifacts}


def _apply_sql(c, records):
    for r in records:
        p, seq = r['payload'], r['seq']
        if r['type'] == 'run_started':
            c.execute('INSERT INTO runs VALUES(?,?,?,?,NULL,NULL)',
                      (p['run_id'], p['actor'], p['input_digest'], seq))
        else:
            run = c.execute('SELECT outcome FROM runs WHERE run_id=?', (p['run_id'],)).fetchone()
            if run is None or run['outcome'] is not None:
                raise f.IntegrityFailure('semantic_error', seq)
            if r['type'] == 'artifact_recorded':
                c.execute('INSERT INTO artifacts VALUES(?,?,?,?,?)',
                          (p['run_id'], p['artifact_id'], p['content_digest'], p['byte_count'], seq))
            else:
                c.execute('UPDATE runs SET finished_seq=?,outcome=? WHERE run_id=?',
                          (seq, p['outcome'], p['run_id']))


def read_projection(path, *, readonly=False):
    with database(path, readonly=readonly) as c:
        c.execute('BEGIN')
        check_schema(c, PROJECTION_SCHEMA)
        rows = c.execute('SELECT * FROM projection_meta').fetchmany(2)
        if len(rows) != 1:
            raise f.IntegrityFailure('head_mismatch')
        meta = dict(rows[0])
        try:
            if meta['singleton'] != 1 or meta['reducer_version'] != 'projection-v1':
                raise f.AuditError('invalid_input')
            f.pattern(meta['source_ledger_id'], f.LEDGER_ID)
            f.integer(meta['source_snapshot_seq'], upper=1000)
            f.integer(meta['through_seq'], upper=meta['source_snapshot_seq'])
            for name in ('source_snapshot_hash', 'through_hash', 'state_digest'):
                f.pattern(meta[name], f.HASH)
            if meta['anchor_digest'] is not None:
                f.pattern(meta['anchor_digest'], f.HASH)
            f.origin(meta['source_origin'], meta['source_export_digest'])
            state = sql_state(c)
            if state_digest(state) != meta['state_digest']:
                raise f.AuditError('invalid_input')
            all_seq = [r['started_seq'] for r in state['runs']] + [
                r['finished_seq'] for r in state['runs'] if r['finished_seq'] is not None] + [
                a['event_seq'] for a in state['artifacts']]
            if sorted(all_seq) != list(range(1, meta['through_seq'] + 1)):
                raise f.AuditError('invalid_input')
        except f.AuditError as exc:
            raise f.IntegrityFailure('record_invalid') from exc
        c.execute('COMMIT')
        return {'meta': meta, 'state': state}


def replay(path, output, through_seq=None, anchor=None):
    snapshot = read_snapshot(path, anchor)
    n = len(snapshot.records)
    t = n if through_seq is None else f.integer(through_seq, upper=n)
    selected = snapshot.records[:t]
    expected = reduce_events(selected)
    verified = verification(snapshot, anchor)
    through_hash = selected[-1]['event_hash'] if selected else f.digest('audit-genesis-v1', snapshot.header)
    meta = {'singleton': 1, 'source_ledger_id': snapshot.header['ledger_id'],
            'source_snapshot_seq': n, 'source_snapshot_hash': snapshot.head_hash,
            'through_seq': t, 'through_hash': through_hash, 'reducer_version': 'projection-v1',
            'state_digest': state_digest(expected), 'source_origin': snapshot.origin,
            'source_export_digest': snapshot.source_export_digest,
            'anchor_digest': verified['checkpoint_digest']}
    with new_target(output) as temp:
        with database(temp, initialize=True) as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute(RUNS_SQL)
            c.execute(ARTIFACTS_SQL)
            c.execute(PROJECTION_META_SQL)
            _apply_sql(c, selected)
            actual = sql_state(c)
            if f.canonical(actual) != f.canonical(expected):
                raise f.IntegrityFailure('semantic_error')
            c.execute('INSERT INTO projection_meta VALUES(?,?,?,?,?,?,?,?,?,?,?)', tuple(meta.values()))
            c.execute('COMMIT')
        reopened = read_projection(temp)
        if reopened != {'meta': meta, 'state': expected}:
            raise f.IntegrityFailure('semantic_error')
    return {'format': 'audit-replay-v1', 'verification': verified,
            'through_seq': t, 'through_hash': through_hash,
            'state_digest': state_digest(reopened['state']), 'counts': counts(reopened['state']),
            'provenance': {'source_origin': snapshot.origin,
                           'source_export_digest': snapshot.source_export_digest,
                           'reducer_version': 'projection-v1'}}
