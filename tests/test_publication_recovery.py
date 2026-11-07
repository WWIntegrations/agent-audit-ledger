"""A process can disappear between hard-link publication and temp-name cleanup."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ledger_audit import core
from ledger_audit.files import read_json

ROOT = Path(__file__).resolve().parents[1]


class PublicationRecoveryTests(unittest.TestCase):
    def test_crash_after_link_keeps_complete_db_and_json_readable(self):
        script = '''
import os,sys
from ledger_audit import core, files
kind,path=sys.argv[1:]
real_link=files.os.link
def publish_then_exit(*args,**kwargs):
    real_link(*args,**kwargs)
    os._exit(73)
files.os.link=publish_then_exit
if kind=='db': core.init(path)
else: files.write_json(path, {'complete': True})
'''
        with tempfile.TemporaryDirectory() as temp:
            for kind in ('db', 'json'):
                with self.subTest(kind=kind):
                    target = Path(temp)/('target.'+kind)
                    child = subprocess.run([sys.executable, '-c', script, kind, str(target)], cwd=ROOT, timeout=10)
                    self.assertEqual(child.returncode, 73)
                    self.assertEqual(target.stat().st_nlink, 2)
                    if kind == 'db':
                        self.assertEqual(core.verify(target)['chain'], 'consistent')
                    else:
                        self.assertEqual(read_json(target), {'complete': True})
