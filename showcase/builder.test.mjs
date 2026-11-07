import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
const root = fileURLToPath(new URL('../', import.meta.url));
function check(source) {
    assert.doesNotThrow(() => execFileSync('python3', ['-c', source], {
        cwd: root, encoding: 'utf8'
    }));
}
test('original export bytes remain bound to the validated manifest after reading', () => {
    check(String.raw`import tempfile, json, hashlib
from pathlib import Path
from unittest.mock import patch
from tools.build_showcase import recorded_payload
with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp); data={'demo':{},'export':{},'checkpoint':{}}
    names=['demo-result','export','checkpoint']
    data['manifest']={'artifacts':[{'path':name+'.json','sha256':hashlib.sha256(b'{}\n').hexdigest()} for name in names]}
    for name in names: (root/(name+'.json')).write_bytes(b'{}\n')
    (root/'recorded-manifest.json').write_text(json.dumps(data['manifest']))
    with patch('ledger_audit.recorded.validate_recorded',return_value=data):
        assert recorded_payload(root)[1]['export']=='{}\n'
        (root/'export.json').write_bytes(b' {}\n')
        try: recorded_payload(root)
        except ValueError: pass
        else: raise AssertionError('Accepted changed bytes after manifest validation')
`);
});
test('artifact reads reject symlink, FIFO and oversized content without blocking', () => {
    check(String.raw `import tempfile, os
from pathlib import Path
from tools.build_showcase import bounded_read
with tempfile.TemporaryDirectory() as tmp:
    p=Path(tmp); (p/'regular').write_bytes(b'abcd')
    (p/'link').symlink_to(p/'regular'); os.mkfifo(p/'fifo')
    assert bounded_read(p/'regular', 4)==b'abcd'
    for name, limit in [('link',4), ('fifo',4), ('regular',3)]:
        try: bounded_read(p/name,limit)
        except (ValueError,OSError): pass
        else: raise AssertionError(name)
`);
});
test('standalone embedding preserves original text and cannot create script markup or replace template markers', () => {
    check(String.raw `import json, re
from tools.build_showcase import assemble
raw=' {"text":"__ARTIFACTS__ </script><script>alert(1)</script> &"}\n'
page=assemble({'note':raw}, {'export':raw}, 'recorded test')
for element,key in [('showcase-data','note'),('showcase-artifacts','export')]:
    body=re.search(r'<script[^>]+id="'+element+r'"[^>]*>(.*?)</script>',page,re.S).group(1)
    assert '<' not in body
    assert json.loads(body)[key]==raw
license_body=re.search(r'<script[^>]+id="font-license"[^>]*>(.*?)</script>',page,re.S).group(1)
assert 'SIL OPEN FONT LICENSE' in json.loads(license_body)['Anton']
`);
});
