import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from ledger_audit import formats as f
from ledger_audit.demo import make_demo


class RecordedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'recorded'
        make_demo(self.root)

    def validator(self):
        self.assertIsNotNone(importlib.util.find_spec('ledger_audit.recorded'), 'Missing recorded-artifact validation')
        from ledger_audit.recorded import validate_recorded
        return validate_recorded

    def test_readonly_validation_binds_actual_sql_artifacts(self):
        validate = self.validator()
        files = sorted(p for p in self.root.rglob('*') if p.is_file())
        before = [(p.name, hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns) for p in files]
        r = validate(self.root)
        self.assertEqual(r['format'], 'audit-inspector-v1')
        self.assertEqual(r['projections']['prefix']['state']['runs'][0]['outcome'], None)
        self.assertEqual(r['projections']['full']['state']['runs'][0]['outcome'], 'success')
        self.assertEqual(before, [(p.name, hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns) for p in files])

    def test_changed_report_with_updated_manifest_is_rejected(self):
        validate = self.validator()
        report_path = self.root/'demo-result.json'
        report = f.parse(report_path.read_bytes())
        report['replay_full']['counts']['artifact_bytes'] += 1
        report_path.write_bytes(f.canonical(report)+b'\n')
        manifest_path = self.root/'recorded-manifest.json'
        manifest = f.parse(manifest_path.read_bytes())
        for row in manifest['artifacts']:
            if row['path'] == 'demo-result.json':
                row['sha256'] = hashlib.sha256(report_path.read_bytes()).hexdigest()
        manifest_path.write_bytes(f.canonical(manifest)+b'\n')
        with self.assertRaises(f.AuditError):
            validate(self.root)
