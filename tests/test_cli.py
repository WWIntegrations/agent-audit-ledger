from contextlib import closing
"""End-to-end contracts: assert effects, never prerecorded success responses."""
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class LedgerCLI(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.db = self.base / 'ledger.sqlite'

    def call(self, *args, code=0):
        p = subprocess.run([sys.executable, '-m', 'ledger_audit', *map(str, args)],
                           cwd=ROOT, capture_output=True, text=True, timeout=15)
        self.assertEqual(p.returncode, code, p.stderr + p.stdout)
        return json.loads(p.stdout) if p.stdout else json.loads(p.stderr.splitlines()[-1])

    def event(self, event_id, kind, payload):
        p = self.base / (event_id + '.json')
        p.write_text(json.dumps({'event_id': event_id, 'type': kind, 'payload': payload}))
        return p

    def start(self):
        self.call('init', '--db', self.db)
        e = self.event('start', 'run_started', {'run_id': 'run', 'actor': 'observer', 'input_digest': 'a'*64})
        return self.call('append', '--db', self.db, '--event', e)

    def finish(self):
        e = self.event('finish', 'run_finished', {'run_id': 'run', 'outcome': 'success'})
        return self.call('append', '--db', self.db, '--event', e)

    def test_retry_and_real_sql_projection(self):
        self.start()
        first = self.finish()
        retry = self.finish()
        self.assertEqual(first['disposition'], 'appended')
        self.assertEqual(retry['disposition'], 'existing')
        self.assertEqual(first['record'], retry['record'])
        full = self.base / 'full.sqlite'
        prefix = self.base / 'prefix.sqlite'
        r = self.call('replay', '--db', self.db, '--out-db', full)
        self.assertEqual(r['counts'], {'runs': 1, 'active_runs': 0, 'finished_runs': 1, 'artifacts': 0, 'artifact_bytes': 0})
        self.call('replay', '--db', self.db, '--through-seq', '1', '--out-db', prefix)
        with closing(sqlite3.connect(full)) as c, c:
            self.assertEqual(c.execute('SELECT outcome,finished_seq FROM runs').fetchone(), ('success', 2))
        with closing(sqlite3.connect(prefix)) as c, c:
            self.assertEqual(c.execute('SELECT outcome,finished_seq FROM runs').fetchone(), (None, None))

    def test_tail_truncation_requires_independent_anchor(self):
        self.start()
        self.finish()
        cp = self.base / 'checkpoint.json'
        receipt = self.call('checkpoint', '--db', self.db, '--out', cp)
        with closing(sqlite3.connect(self.db)) as c, c:
            row = json.loads(c.execute('SELECT record_json FROM events WHERE seq=1').fetchone()[0])
            c.execute('DELETE FROM events WHERE seq=2')
            c.execute('UPDATE meta SET head_seq=1,head_hash=?', (row['event_hash'],))
        unanchored = self.call('verify', '--db', self.db)
        self.assertEqual((unanchored['chain'], unanchored['anchor']), ('consistent', 'none'))
        anchored = self.call('verify', '--db', self.db, '--checkpoint', cp,
                             '--expected-checkpoint-digest', receipt['artifact_digest'], code=1)
        self.assertEqual(anchored['failure']['code'], 'truncated_before_anchor')

    def test_import_preserves_records_but_marks_provenance(self):
        self.start()
        source = self.base / 'source.json'
        imported = self.base / 'imported.sqlite'
        second = self.base / 'second.json'
        self.call('export', '--db', self.db, '--out', source)
        imp = self.call('import', '--export', source, '--db', imported)
        self.assertEqual(imp['origin'], 'imported')
        self.call('export', '--db', imported, '--out', second)
        a, b = json.loads(source.read_text()), json.loads(second.read_text())
        self.assertEqual(a['events'], b['events'])
        self.assertEqual(b['provenance']['source_origin'], 'imported')
        self.assertNotEqual(a['provenance'], b['provenance'])

    def test_strict_input_and_existing_outputs_preserve_state(self):
        self.start()
        bad = self.base / 'bad.json'
        bad.write_text('{"event_id":"x","event_id":"y","type":"run_finished","payload":{"run_id":"run","outcome":"success"}}')
        self.call('append', '--db', self.db, '--event', bad, code=2)
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        self.call('init', '--db', self.db, code=2)
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)
        self.assertEqual(self.call('verify', '--db', self.db)['snapshot_seq'], 1)

    def test_real_demo_and_negative_controls(self):
        d = self.base / 'demo'
        result = self.call('demo', '--out-dir', d)
        self.assertEqual(result['format'], 'audit-demo-v1')
        self.assertEqual(len(result['artifact_checks']), 2)
        self.assertTrue(all(x['matches'] for x in result['artifact_checks']))
        checks = {x['case']: x for x in result['tamper_checks']}
        self.assertEqual(checks['payload_changed']['without_anchor']['failure']['code'], 'hash_mismatch')
        self.assertEqual(checks['middle_deleted']['without_anchor']['failure']['code'], 'sequence_gap')
        self.assertEqual(checks['tail_truncated']['without_anchor']['chain'], 'consistent')
        self.assertEqual(checks['tail_truncated']['with_anchor']['failure']['code'], 'truncated_before_anchor')
        self.assertEqual(result['replay_full']['counts']['artifact_bytes'], sum(x['measured_bytes'] for x in result['artifact_checks']))


if __name__ == '__main__':
    unittest.main()
