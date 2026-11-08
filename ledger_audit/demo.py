"""Execute a small local experiment. Tampering only targets new demo-owned copies."""
import hashlib
import os
from pathlib import Path
import sqlite3

from . import core, formats as f
from .files import new_target, read_bytes, read_json, write_json
from .projection import replay
from .storage import database

CASES = ('payload_changed', 'middle_deleted', 'tail_truncated')
ARTIFACT_NAMES = ('first', 'second')
RECORDED_FILES = ('ledger.sqlite', 'checkpoint.json', 'export.json',
                  'projection-prefix.sqlite', 'projection-full.sqlite',
                  'artifacts/first.txt', 'artifacts/second.txt',
                  'tamper-payload_changed.sqlite', 'tamper-middle_deleted.sqlite',
                  'tamper-tail_truncated.sqlite', 'demo-result.json')


def source_files():
    package = Path(__file__).resolve().parent
    root = package.parent
    # An installed package has no pyproject.toml beside it; a source checkout does.
    extra = [root / 'pyproject.toml'] if (root / 'pyproject.toml').is_file() else []
    paths = sorted([*package.glob('*.py'), *extra], key=lambda p: p.relative_to(root).as_posix())
    return [{'path': p.relative_to(root).as_posix(),
             'sha256': hashlib.sha256(read_bytes(p, 256*1024)).hexdigest()} for p in paths]


def write_manifest(root):
    value = {'format': 'audit-recorded-manifest-v1', 'source_files': source_files(),
             'artifacts': [{'path': name, 'sha256': hashlib.sha256(read_bytes(root/name, f.MAX_DB)).hexdigest()}
                           for name in sorted(RECORDED_FILES)]}
    write_json(root / 'recorded-manifest.json', value)


def _copy_and_modify(source, target, case):
    with new_target(target) as temp:
        with database(source) as src:
            dest = sqlite3.connect(temp)
            try:
                src.backup(dest)
            finally:
                dest.close()
        with database(temp) as c:
            c.execute('BEGIN IMMEDIATE')
            if case == 'payload_changed':
                r = f.parse(c.execute('SELECT record_json FROM events WHERE seq=2').fetchone()[0].encode())
                r['payload']['content_digest'] = '0' * 64
                c.execute('UPDATE events SET record_json=? WHERE seq=2', (f.canonical(r).decode(),))
            elif case == 'middle_deleted':
                c.execute('DELETE FROM events WHERE seq=2')
            elif case == 'tail_truncated':
                r = f.parse(c.execute('SELECT record_json FROM events WHERE seq=3').fetchone()[0].encode())
                c.execute('DELETE FROM events WHERE seq=4')
                c.execute('UPDATE meta SET head_seq=3,head_hash=?', (r['event_hash'],))
            else:
                raise f.AuditError('invalid_input')
            c.execute('COMMIT')


def artifact_checks(root, snapshot):
    by_id = {r['payload']['artifact_id']: r['payload'] for r in snapshot.records if r['type'] == 'artifact_recorded'}
    result = []
    for name in ARTIFACT_NAMES:
        data = read_bytes(root / 'artifacts' / (name + '.txt'), 1048576)
        sha = hashlib.sha256(data).hexdigest()
        p = by_id[name]
        result.append({'artifact_id': name, 'measured_bytes': len(data), 'measured_sha256': sha,
                       'recorded_sha256': p['content_digest'],
                       'matches': sha == p['content_digest'] and len(data) == p['byte_count']})
    return result


def assert_controls(result):
    def failure_code(check):
        failure = check.get('failure')
        return failure.get('code') if isinstance(failure, dict) else None
    checks = {x['case']: x for x in result['tamper_checks']}
    if (len(checks) != 3 or not all(a['matches'] for a in result['artifact_checks']) or
            result['initial_verification']['failure'] is not None or
            failure_code(checks['payload_changed']['without_anchor']) != 'hash_mismatch' or
            failure_code(checks['middle_deleted']['without_anchor']) != 'sequence_gap' or
            checks['tail_truncated']['without_anchor']['chain'] != 'consistent' or
            failure_code(checks['tail_truncated']['with_anchor']) != 'truncated_before_anchor' or
            result['replay_prefix']['counts']['active_runs'] != 1 or
            result['replay_full']['counts']['finished_runs'] != 1):
        raise f.AuditError('demo_control_failed')


def make_demo(out_dir):
    root = Path(out_dir)
    try:
        root.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise f.AuditError('output_exists') from exc
    (root / 'artifacts').mkdir(mode=0o700)
    contents = (b'Autumn field notes: five seedlings, two trays.\n',
                b'Observed locally. No network calls or generated answers.\n')
    for name, data in zip(ARTIFACT_NAMES, contents):
        with (root / 'artifacts' / (name+'.txt')).open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    db = root / 'ledger.sqlite'
    core.init(db)
    core.append(db, {'event_id': 'start', 'type': 'run_started',
                     'payload': {'run_id': 'field-notes', 'actor': 'local-observer',
                                 'input_digest': hashlib.sha256(b'local field notes demo v1').hexdigest()}})
    for name in ARTIFACT_NAMES:
        data = read_bytes(root / 'artifacts' / (name+'.txt'), 1048576)
        core.append(db, {'event_id': 'record-'+name, 'type': 'artifact_recorded',
                         'payload': {'run_id': 'field-notes', 'artifact_id': name,
                                     'content_digest': hashlib.sha256(data).hexdigest(), 'byte_count': len(data)}})
    core.append(db, {'event_id': 'finish', 'type': 'run_finished',
                     'payload': {'run_id': 'field-notes', 'outcome': 'success'}})
    initial = core.verify(db)
    cp_receipt = core.checkpoint(db, root / 'checkpoint.json')
    cp = read_json(root / 'checkpoint.json', 4096)
    # This value stays in the controller, outside each deliberately altered copy.
    expected_digest = cp_receipt['artifact_digest']
    anchor = (cp, expected_digest)
    core.export_file(db, root / 'export.json', anchor)
    prefix = replay(db, root / 'projection-prefix.sqlite', 3, anchor)
    full = replay(db, root / 'projection-full.sqlite', None, anchor)
    checks = []
    for case in CASES:
        copy = root / ('tamper-' + case + '.sqlite')
        _copy_and_modify(db, copy, case)
        checks.append({'case': case, 'without_anchor': core.verify(copy),
                       'with_anchor': core.verify(copy, anchor)})
    result = {'format': 'audit-demo-v1', 'initial_verification': initial,
              'checkpoint': cp, 'checkpoint_digest': expected_digest,
              'replay_prefix': prefix, 'replay_full': full,
              'artifact_checks': artifact_checks(root, core.read_snapshot(db)), 'tamper_checks': checks}
    assert_controls(result)
    write_json(root / 'demo-result.json', result)
    write_manifest(root)
    return result
