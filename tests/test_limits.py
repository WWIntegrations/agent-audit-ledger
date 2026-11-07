"""Resource boundaries and injected SQLite failures around real transactions."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from ledger_audit import core, formats as f, storage
from ledger_audit.files import read_json
from test_integrity import start_input, finish_input


class LimitTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.p = Path(self.t.name)
        self.db = self.p/'ledger.sqlite'
        core.init(self.db)

    def test_event_capacity_rejects_new_but_allows_identical_retry(self):
        # Bounded fixture builder uses valid records and one SQL transaction.
        snapshot = core.read_snapshot(self.db)
        records = []
        prev = snapshot.head_hash
        for i in range(1, 1001):
            inp = start_input('event'+str(i), 'run'+str(i))
            r = {**inp, 'seq': i, 'recorded_at': '2026-11-07T00:00:00.000000Z', 'prev_hash': prev}
            r['event_hash'] = f.event_hash(snapshot.header, r)
            prev = r['event_hash']
            records.append(r)
        with closing(sqlite3.connect(self.db)) as c, c:
            c.executemany('INSERT INTO events VALUES(?,?,?)', [(r['seq'], r['event_id'], f.canonical(r).decode()) for r in records])
            c.execute('UPDATE meta SET head_seq=1000,head_hash=?', (prev,))
        self.assertEqual(core.verify(self.db)['snapshot_seq'], 1000)
        with self.assertRaises(f.AuditError) as cm:
            core.append(self.db, start_input('overflow', 'overflow'))
        self.assertEqual(cm.exception.code, 'limit_exceeded')
        self.assertEqual(core.append(self.db, start_input('event1000', 'run1000'))['disposition'], 'existing')
        self.assertEqual(core.verify(self.db)['snapshot_seq'], 1000)

    def test_json_raw_limits_and_unicode_are_not_silently_normalized(self):
        self.assertEqual(f.parse(b'{}'+b' '*(8192-2), 8192), {})
        with self.assertRaises(f.AuditError):
            f.parse(b'{}'+b' '*(8192-1), 8192)
        file = self.p/'large.json'
        with file.open('xb') as stream:
            stream.write(b'{}')
            chunk = b' ' * 65536
            for _ in range(255):
                stream.write(chunk)
            stream.write(b' ' * (65536 - 2))
        self.assertEqual(file.stat().st_size, 16*1024*1024)
        self.assertEqual(read_json(file), {})
        with file.open('ab') as stream:
            stream.write(b' ')
        with self.assertRaises(f.AuditError):
            read_json(file)
        self.assertNotEqual(f.canonical(f.parse('"é"'.encode())), f.canonical(f.parse('"é"'.encode())))

    def test_sqlite_length_cap_rejects_oversized_stored_record(self):
        core.append(self.db, start_input())
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute('PRAGMA ignore_check_constraints=ON')
            c.execute('UPDATE events SET record_json=?', ('x'*70000,))
        with self.assertRaises(f.AuditError) as cm:
            core.verify(self.db)
        self.assertEqual(cm.exception.code, 'limit_exceeded')

    def test_page_limit_and_pragmas_are_restored_on_every_connection(self):
        for _ in range(2):
            with storage.database(self.db) as c:
                self.assertEqual(c.execute('PRAGMA max_page_count').fetchone()[0], 8192)
                self.assertEqual(c.execute('PRAGMA synchronous').fetchone()[0], 3)
                self.assertEqual(c.execute('PRAGMA journal_mode').fetchone()[0], 'delete')
                self.assertEqual(c.execute('PRAGMA foreign_keys').fetchone()[0], 1)
                self.assertEqual(c.execute('PRAGMA trusted_schema').fetchone()[0], 0)

    def test_injected_ioerr_and_full_rollback_prior_insert(self):
        # Inject a SQLite API error after a real INSERT, not a claimed disk failure.
        core.append(self.db, start_input())
        real = sqlite3.connect
        for code in (sqlite3.SQLITE_IOERR, sqlite3.SQLITE_FULL):
            class Failing(sqlite3.Connection):
                def execute(self, sql, params=()):
                    if sql.startswith('UPDATE meta'):
                        exc = sqlite3.OperationalError('injected test failure')
                        exc.sqlite_errorcode = code
                        raise exc
                    return super().execute(sql, params)
            def connect(*a, **kw):
                return real(*a, **kw, factory=Failing)
            with self.subTest(code=code), patch.object(storage.sqlite3, 'connect', connect):
                with self.assertRaises(f.AuditError):
                    core.append(self.db, finish_input())
            self.assertEqual(core.verify(self.db)['snapshot_seq'], 1)
        self.assertEqual(core.append(self.db, finish_input())['disposition'], 'appended')

    def test_unsupported_pragma_readback_is_a_failure(self):
        real = sqlite3.connect
        class Unsupported(sqlite3.Connection):
            def execute(self, sql, params=()):
                if sql == 'PRAGMA synchronous':
                    return super().execute('SELECT 2')
                return super().execute(sql, params)
        with patch.object(storage.sqlite3, 'connect', lambda *a, **kw: real(*a, **kw, factory=Unsupported)):
            with self.assertRaises(f.AuditError) as cm:
                core.append(self.db, start_input())
        self.assertEqual(cm.exception.code, 'unsupported_runtime')
        self.assertEqual(core.verify(self.db)['snapshot_seq'], 0)
