"""The saved experiment must retain each controlled intervention's scope."""
from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from ledger_audit import core, formats as f
from ledger_audit.demo import make_demo
from ledger_audit.files import read_json
from ledger_audit.recorded import validate_recorded


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.root = Path(self.t.name)

    def test_payload_case_cannot_hide_an_additional_tail_deletion(self):
        root = self.root/'recorded'
        make_demo(root)
        name = 'tamper-payload_changed.sqlite'
        with closing(sqlite3.connect(root/name)) as c, c:
            c.execute('DELETE FROM events WHERE seq=4')
        manifest_path = root/'recorded-manifest.json'
        manifest = read_json(manifest_path)
        for row in manifest['artifacts']:
            if row['path'] == name:
                row['sha256'] = hashlib.sha256((root/name).read_bytes()).hexdigest()
        manifest_path.write_bytes(f.canonical(manifest)+b'\n')
        # First failure remains hash_mismatch at seq 2. Checking only its code misses this change.
        with self.assertRaises(f.AuditError):
            validate_recorded(root)

    def test_always_green_verifier_fails_demo_with_bounded_diagnostic(self):
        original = core.verify
        def always_green(path, anchor=None, **kwargs):
            return {'format': 'audit-verify-v1', 'ledger_id': 'a'*32, 'chain': 'consistent',
                    'snapshot_seq': 4, 'head_hash': 'b'*64, 'anchor': 'none',
                    'checkpoint_digest': None, 'anchored_seq': 0, 'unanchored_tail': 4, 'failure': None}
        with patch.object(core, 'verify', always_green):
            with self.assertRaises(f.AuditError) as cm:
                make_demo(self.root/'false-green')
        self.assertEqual(cm.exception.code, 'demo_control_failed')
