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

    def raw(self, *args, code=0):
        p = subprocess.run([sys.executable, '-m', 'ledger_audit', *map(str, args)],
                           cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, code, p.stderr + p.stdout)
        return p

    def call(self, *args, code=0):
        p = self.raw(*args, '--json', code=code)
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

    def test_default_output_is_a_short_human_summary(self):
        p = self.raw('demo', '--out-dir', self.base / 'human')
        self.assertLess(len(p.stdout), 1500)
        self.assertEqual(len(p.stdout.splitlines()), 7, p.stdout)
        self.assertIn('Verdict: PASS', p.stdout)
        self.assertNotIn('{', p.stdout)
        self.assertEqual(p.stderr, '')

    def test_demo_can_run_twice_without_out_dir(self):
        import os
        env_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(env_tmp.cleanup)
        outs = []
        for _ in range(2):
            p = subprocess.run([sys.executable, '-m', 'ledger_audit', 'demo'], cwd=ROOT, capture_output=True,
                               text=True, timeout=30, env={**os.environ, 'TMPDIR': env_tmp.name})
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn('Verdict: PASS', p.stdout)
            outs.append(p.stdout.splitlines()[0])
        self.assertNotEqual(outs[0], outs[1])

    def test_demo_works_from_an_installed_copy_without_pyproject(self):
        import shutil
        site = self.base / 'site'
        shutil.copytree(ROOT / 'ledger_audit', site / 'ledger_audit', ignore=shutil.ignore_patterns('__pycache__'))
        p = subprocess.run([sys.executable, '-B', '-m', 'ledger_audit', 'demo', '--out-dir', str(self.base / 'inst')],
                           cwd=site, capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('Verdict: PASS', p.stdout)

    def test_human_output_escapes_terminal_control_and_bidi(self):
        evil = '\x1b]8;;http://evil\x1b\\X\u202e\r'
        from ledger_audit.summary import render
        out = '\n'.join(render({'format': 'audit-append-v1', 'disposition': 'appended',
                                'record': {'event_id': evil, 'seq': 1, 'type': 'run_started'}}))
        self.assertNotIn('\x1b', out)
        self.assertNotIn('\u202e', out)
        self.assertNotIn('\r', out)
        self.assertIn('\\x1b', out)
        self.assertIn('\\u202e', out)
        weird = self.base / ('d\u202e\x1b')
        r = self.raw('demo', '--out-dir', weird)
        self.assertNotIn('\x1b', r.stdout)
        self.assertNotIn('\u202e', r.stdout)

    def test_human_verify_and_error_hint_for_existing_target(self):
        self.start()
        self.finish()
        out = self.raw('verify', '--db', self.db).stdout
        self.assertIn('OK    chain consistent, 2 event(s)', out)
        self.assertIn('no checkpoint given', out)
        err = self.raw('init', '--db', self.db, code=2).stderr
        self.assertIn('output_exists', err)
        self.assertIn('Choose a new path', err)

    def test_examples_directory_commands_work_as_documented(self):
        self.raw('init', '--db', self.db)
        self.raw('append', '--db', self.db, '--event', ROOT / 'examples' / 'start.json')
        self.raw('append', '--db', self.db, '--event', ROOT / 'examples' / 'finish.json')
        self.assertIn('2 event(s)', self.raw('verify', '--db', self.db).stdout)


    def test_common_failures_say_what_to_do(self):
        missing = self.raw('verify', '--db', self.base / 'typo.sqlite', code=2)
        self.assertIn('storage_error', missing.stderr)
        self.assertIn('init', missing.stderr)
        self.assertNotIn('typo', missing.stderr)
        self.assertFalse((self.base / 'typo.sqlite').exists())
        self.call('init', '--db', self.db)
        finish = self.event('finish', 'run_finished', {'run_id': 'run', 'outcome': 'success'})
        early = self.raw('append', '--db', self.db, '--event', finish, code=1)
        self.assertIn('semantic_error', early.stderr)
        self.assertIn('run_started', early.stderr)

    def test_edited_ledger_with_invalid_utf8_exits_1(self):
        self.start()
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute("UPDATE events SET record_json=CAST(x'7bff7d' AS TEXT)")
        r = self.call('verify', '--db', self.db, code=1)
        self.assertEqual(r['failure']['code'], 'record_invalid')
        self.assertIn('FAIL  chain is not usable: record_invalid', self.raw('verify', '--db', self.db, code=1).stdout)

    def test_every_error_code_has_a_hint(self):
        import re
        from ledger_audit.summary import HINTS
        source = ''.join(p.read_text() for p in sorted((ROOT / 'ledger_audit').glob('*.py')))
        codes = set(re.findall(r"(?:AuditError|IntegrityFailure)\('([a-z_]+)'", source))
        codes |= {'truncated_before_anchor', 'anchor_mismatch', 'checkpoint_digest_mismatch'}
        self.assertGreater(len(codes), 20)
        self.assertEqual(sorted(codes - set(HINTS)), [])


if __name__ == '__main__':
    unittest.main()
