"""Real process crashes and races. Only disposable databases created by each test."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

from ledger_audit import core, formats as f
from test_integrity import start_input, artifact_input, finish_input

ROOT = Path(__file__).resolve().parents[1]

CHILD = '''
import json, os, sqlite3, sys
from ledger_audit import core, formats as f
mode, path, raw = sys.argv[1:]
real_connect = sqlite3.connect
class Controlled(sqlite3.Connection):
    def execute(self, sql, parameters=()):
        if mode == 'trace_commit' and sql == 'COMMIT':
            print('REACHED_COMMIT', file=sys.stderr, flush=True)
        result = super().execute(sql, parameters)
        if ((mode == 'after_insert' and sql.startswith('INSERT INTO events')) or
            (mode == 'after_update' and sql.startswith('UPDATE meta')) or
            (mode == 'after_commit' and sql == 'COMMIT')):
            os._exit(71)
        return result
sqlite3.connect = lambda *a, **kw: real_connect(*a, **kw, factory=Controlled)
try:
    result = core.append(path, json.loads(raw))
    print(json.dumps(result), flush=True)
except f.AuditError as exc:
    print(exc.code, file=sys.stderr, flush=True)
    sys.exit(exc.exit_code)
'''


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.p = Path(self.t.name)
        self.db = self.p/'ledger.sqlite'
        core.init(self.db)

    def spawn(self, event, mode='normal'):
        return subprocess.Popen([sys.executable, '-c', CHILD, mode, str(self.db), json.dumps(event)],
                                cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def collect(self, child):
        out, err = child.communicate(timeout=15)
        return child.returncode, out, err

    def test_same_id_same_input_one_event_two_identical_records(self):
        children = [self.spawn(start_input()) for _ in range(2)]
        results = [self.collect(c) for c in children]
        self.assertEqual([r[0] for r in results], [0, 0], results)
        values = [json.loads(r[1]) for r in results]
        self.assertEqual(sorted(r['disposition'] for r in values), ['appended', 'existing'])
        self.assertEqual(values[0]['record'], values[1]['record'])
        self.assertEqual(core.verify(self.db)['snapshot_seq'], 1)

    def test_same_id_different_input_has_one_conflict(self):
        children = [self.spawn(start_input(run=run)) for run in ('alpha', 'beta')]
        results = [self.collect(c) for c in children]
        self.assertEqual(sorted(r[0] for r in results), [0, 1], results)
        self.assertIn('conflict', ''.join(r[2] for r in results))
        self.assertEqual(core.verify(self.db)['snapshot_seq'], 1)

    def test_many_writers_leave_contiguous_sequence(self):
        core.append(self.db, start_input())
        children = [self.spawn(artifact_input('event'+str(i), 'file'+str(i))) for i in range(8)]
        results = [self.collect(c) for c in children]
        self.assertTrue(all(r[0] == 0 for r in results), results)
        rows = core.read_snapshot(self.db).records
        self.assertEqual([r['seq'] for r in rows], list(range(1, 10)))
        self.assertEqual(len({r['event_id'] for r in rows}), 9)

    def test_finish_racing_artifact_is_serializable(self):
        core.append(self.db, start_input())
        children = [self.spawn(finish_input()), self.spawn(artifact_input())]
        results = [self.collect(c) for c in children]
        self.assertEqual(results[0][0], 0, results)
        self.assertIn(results[1][0], (0, 1), results)
        rows = core.read_snapshot(self.db).records
        self.assertEqual(rows[-1]['type'], 'run_finished')
        self.assertEqual(len(rows), 3 if results[1][0] == 0 else 2)

    def test_crashes_before_and_after_commit_have_whole_state(self):
        for mode, expected in (('after_insert', 0), ('after_update', 0), ('after_commit', 1)):
            with self.subTest(mode=mode):
                db = self.p/(mode+'.sqlite')
                core.init(db)
                old = self.db
                self.db = db
                result = self.collect(self.spawn(start_input(), mode))
                self.db = old
                self.assertEqual(result[0], 71, result)
                self.assertEqual(core.verify(db)['snapshot_seq'], expected)
                retry = core.append(db, start_input())
                self.assertEqual(retry['disposition'], 'existing' if expected else 'appended')
                self.assertEqual(core.verify(db)['snapshot_seq'], 1)

    def test_reader_blocks_commit_then_writer_rolls_back(self):
        core.append(self.db, start_input())
        with closing(sqlite3.connect(self.db, isolation_level=None)) as reader:
            reader.execute('BEGIN')
            reader.execute('SELECT * FROM events').fetchall()
            began = time.monotonic()
            child = self.spawn(artifact_input(), 'trace_commit')
            result = self.collect(child)
            duration = time.monotonic() - began
            self.assertEqual(result[0], 2, result)
            self.assertIn('REACHED_COMMIT', result[2])
            self.assertIn('busy', result[2])
            self.assertLess(duration, 8)
            reader.execute('ROLLBACK')
        self.assertEqual(core.verify(self.db)['snapshot_seq'], 1)
        self.assertEqual(core.append(self.db, artifact_input())['disposition'], 'appended')

    def test_atomic_publication_race_has_one_winner(self):
        target = self.p/'race.sqlite'
        children = [subprocess.Popen([sys.executable, '-m', 'ledger_audit', 'init', '--db', str(target)],
                                     cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    for _ in range(2)]
        results = [self.collect(c) for c in children]
        self.assertEqual(sorted(r[0] for r in results), [0, 2], results)
        self.assertEqual(core.verify(target)['snapshot_seq'], 0)
        self.assertEqual(core.verify(target)['chain'], 'consistent')

    def test_killed_new_output_is_absent_before_publish_complete_after(self):
        script = '''
import os,sys
from ledger_audit import core, files
stage,path=sys.argv[1:]
if stage=='before':
    real=files.os.link
    def stopped(*a,**kw): os._exit(72)
    files.os.link=stopped
else:
    def stopped(*a,**kw): os._exit(72)
    files.sync_directory=stopped
core.init(path)
'''
        for stage in ('before', 'after'):
            target = self.p/(stage+'.sqlite')
            child = subprocess.run([sys.executable, '-c', script, stage, str(target)], cwd=ROOT, timeout=10)
            self.assertEqual(child.returncode, 72)
            self.assertEqual(target.exists(), stage == 'after')
            if target.exists():
                self.assertEqual(core.verify(target)['chain'], 'consistent')
