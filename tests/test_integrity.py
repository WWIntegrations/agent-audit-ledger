from contextlib import closing
"""Concrete tamper controls against isolated, newly created test data."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from ledger_audit import core, formats as f
from ledger_audit.files import read_json
from ledger_audit.projection import replay, read_projection


def start_input(event='start', run='run'):
    return {'event_id': event, 'type': 'run_started', 'payload': {'run_id': run, 'actor': 'observer', 'input_digest': 'a'*64}}


def artifact_input(event='artifact', artifact='file'):
    return {'event_id': event, 'type': 'artifact_recorded', 'payload': {'run_id': 'run', 'artifact_id': artifact, 'content_digest': 'b'*64, 'byte_count': 17}}


def finish_input():
    return {'event_id': 'finish', 'type': 'run_finished', 'payload': {'run_id': 'run', 'outcome': 'success'}}


class IntegrityTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.p = Path(self.t.name)
        self.db = self.p/'ledger.sqlite'
        core.init(self.db)

    def populate(self):
        for e in (start_input(), artifact_input(), finish_input()):
            core.append(self.db, e)

    def anchor(self):
        p = self.p/'anchor.json'
        r = core.checkpoint(self.db, p)
        return (read_json(p), r['artifact_digest'])

    def replace_record(self, seq, fn):
        with closing(sqlite3.connect(self.db)) as c, c:
            row = json.loads(c.execute('SELECT record_json FROM events WHERE seq=?', (seq,)).fetchone()[0])
            fn(row)
            c.execute('UPDATE events SET record_json=? WHERE seq=?', (f.canonical(row).decode(), seq))

    def test_rehashed_chain_is_consistent_but_fails_anchor(self):
        self.populate()
        anchor = self.anchor()
        with closing(sqlite3.connect(self.db)) as c, c:
            h = json.loads(c.execute('SELECT header_json FROM meta').fetchone()[0])
            rows = [json.loads(x[0]) for x in c.execute('SELECT record_json FROM events ORDER BY seq')]
            rows[1]['payload']['byte_count'] = 99
            previous = f.digest('audit-genesis-v1', h)
            for row in rows:
                row['prev_hash'] = previous
                row['event_hash'] = f.event_hash(h, row)
                previous = row['event_hash']
                c.execute('UPDATE events SET record_json=? WHERE seq=?', (f.canonical(row).decode(), row['seq']))
            c.execute('UPDATE meta SET head_hash=?', (previous,))
        self.assertEqual(core.verify(self.db)['chain'], 'consistent')
        self.assertEqual(core.verify(self.db, anchor)['failure']['code'], 'anchor_mismatch')

    def test_older_anchor_does_not_protect_later_tail(self):
        core.append(self.db, start_input())
        anchor = self.anchor()
        core.append(self.db, artifact_input())
        core.append(self.db, finish_input())
        self.assertEqual(core.verify(self.db, anchor)['anchor'], 'prefix')
        with closing(sqlite3.connect(self.db)) as c, c:
            head = json.loads(c.execute('SELECT record_json FROM events WHERE seq=2').fetchone()[0])['event_hash']
            c.execute('DELETE FROM events WHERE seq=3')
            c.execute('UPDATE meta SET head_seq=2,head_hash=?', (head,))
        r = core.verify(self.db, anchor)
        self.assertEqual((r['anchor'], r['anchored_seq'], r['unanchored_tail']), ('prefix', 1, 1))

    def test_wrong_digest_other_ledger_and_empty_anchor(self):
        anchor = self.anchor()
        self.assertEqual(core.verify(self.db, (anchor[0], '0'*64))['failure']['code'], 'checkpoint_digest_mismatch')
        other = copy.deepcopy(anchor[0])
        other['header']['ledger_id'] = '0'*32
        self.assertEqual(core.verify(self.db, (other, f.digest('audit-checkpoint-v1', other)))['failure']['code'], 'anchor_mismatch')
        self.assertEqual(core.verify(self.db, anchor)['anchor'], 'exact')
        with self.assertRaises(f.AuditError):
            core.load_anchor(self.p/'anchor.json', None)

    def test_corrupt_later_record_blocks_short_replay(self):
        self.populate()
        self.replace_record(3, lambda r: r.update(recorded_at='2026-11-01T00:00:00.000000Z'))
        target = self.p/'projection.sqlite'
        with self.assertRaises(f.IntegrityFailure):
            replay(self.db, target, 1)
        self.assertFalse(target.exists())

    def test_replay_zero_and_real_sql_rows(self):
        self.populate()
        result = replay(self.db, self.p/'zero.sqlite', 0)
        self.assertEqual(result['counts'], dict(runs=0, active_runs=0, finished_runs=0, artifacts=0, artifact_bytes=0))
        r = replay(self.db, self.p/'full.sqlite')
        view = read_projection(self.p/'full.sqlite')
        self.assertEqual(view['state']['artifacts'][0]['byte_count'], 17)
        self.assertEqual(r['counts']['artifact_bytes'], 17)
        self.assertEqual(view['meta']['through_seq'], 3)

    def test_unknown_schema_rejected_before_trigger_runs(self):
        core.append(self.db, start_input())
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute('CREATE TABLE surprise(value TEXT)')
            c.execute("CREATE TRIGGER injected AFTER INSERT ON events BEGIN INSERT INTO surprise VALUES('ran'); END")
        self.assertEqual(core.verify(self.db)['failure']['code'], 'schema_mismatch')
        with self.assertRaises(f.IntegrityFailure):
            core.append(self.db, finish_input())
        with closing(sqlite3.connect(self.db)) as c, c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM surprise').fetchone()[0], 0)

    def test_record_canonical_and_index_columns_match(self):
        core.append(self.db, start_input())
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute("UPDATE events SET event_id='different'")
        self.assertEqual(core.verify(self.db)['failure']['code'], 'record_invalid')
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute("UPDATE events SET event_id='start',record_json=record_json||' '")
        self.assertEqual(core.verify(self.db)['failure']['code'], 'record_invalid')

    def test_semantic_error_and_idempotency_conflict_preserve_head(self):
        self.populate()
        before = core.verify(self.db)
        with self.assertRaises(f.IntegrityFailure):
            core.append(self.db, artifact_input('late', 'second'))
        with self.assertRaises(f.AuditError) as cm:
            core.append(self.db, start_input('start', 'another'))
        self.assertEqual(cm.exception.code, 'conflict')
        self.assertEqual(core.verify(self.db), before)

    def test_import_bad_count_and_existing_target(self):
        self.populate()
        path = self.p/'export.json'
        core.export_file(self.db, path)
        value = read_json(path)
        value['count'] = 2
        path.write_bytes(f.canonical(value))
        target = self.p/'import.sqlite'
        with self.assertRaises(f.IntegrityFailure):
            core.import_file(path, target)
        self.assertFalse(target.exists())
        value['count'] = 3
        path.write_bytes(f.canonical(value))
        before = hashlib.sha256(self.db.read_bytes()).digest()
        with self.assertRaises(f.AuditError):
            core.import_file(path, self.db)
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).digest(), before)

    def test_safe_fd_and_oversized_new_fixture(self):
        fifo = self.p/'pipe'
        os.mkfifo(fifo)
        with self.assertRaises(f.AuditError):
            read_json(fifo)
        link = self.p/'link.json'
        link.symlink_to(self.db)
        with self.assertRaises(f.AuditError):
            read_json(link)
        oversized = self.p/'oversized-new.sqlite'
        with oversized.open('xb') as stream:
            stream.seek(f.MAX_DB)
            stream.write(b'x')
        with self.assertRaises(f.AuditError) as cm:
            core.verify(oversized)
        self.assertEqual(cm.exception.code, 'limit_exceeded')
        self.assertEqual(core.verify(self.db)['snapshot_seq'], 0)

    def test_parser_and_numeric_boundaries(self):
        bad = [b'{"a":1,"a":2}', b'1.0', b'1e2', b'NaN', b'-1', b'9007199254740992', b'"\\ud800"', b'\xef\xbb\xbf{}', b'['*11+b'0'+b']'*11]
        for data in bad:
            with self.subTest(data=data), self.assertRaises(f.AuditError):
                f.parse(data)
        for patch in ({'byte_count': True}, {'byte_count': 1048577}, {'content_digest': 'B'*64}):
            value = artifact_input()
            value['payload'].update(patch)
            with self.assertRaises(f.AuditError):
                f.event_input(value)
        good = artifact_input()
        good['payload']['byte_count'] = 1048576
        f.event_input(good)
        self.assertEqual(f.parse(b'9007199254740991'), 9007199254740991)
        self.assertEqual(f.parse(b'{}'+b' '*4094, 4096), {})
        with self.assertRaises(f.AuditError):
            f.parse(b'{}'+b' '*4095, 4096)
        with self.assertRaises(f.AuditError):
            f.event_input({**start_input(), 'extra': None})

    def test_invalid_calendar_timestamp(self):
        core.append(self.db, start_input())
        self.replace_record(1, lambda r: r.update(recorded_at='2026-02-30T10:00:00.000000Z'))
        self.assertEqual(core.verify(self.db)['failure']['code'], 'record_invalid')
