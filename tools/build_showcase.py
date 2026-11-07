"""Build the offline viewer from a real demo or validated recorded files."""
import argparse
import base64
import json
import os
import re
from pathlib import Path
import stat
import sys
import tempfile
import hashlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def bounded_read(path, limit=16 * 1024 * 1024):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('Expected a regular file')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ValueError('Artifact exceeds limit')
        return raw
    finally:
        os.close(fd)


def safe_json(value):
    return (json.dumps(value, ensure_ascii=True, separators=(',', ':'))
            .replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e'))


def assemble(data, originals, mode):
    source = ROOT / 'showcase'
    template = (source / 'template.html').read_text()
    font = bounded_read(ROOT / 'docs/fonts/anton/Anton-Regular.ttf')
    license_text = bounded_read(ROOT / 'docs/fonts/anton/OFL.txt').decode('utf-8')
    style = (source / 'style.css').read_text().replace('__ANTON_DATA__', 'data:font/ttf;base64,' + base64.b64encode(font).decode('ascii'))
    script = (source / 'bundle.js').read_text()
    if '</script' in script.lower():
        raise ValueError('Unsafe inline script terminator')
    replacements = {
        '/*__STYLE__*/': style,
        '/*__SCRIPT__*/': script,
        '<!--__DIAGRAM__-->': (ROOT / 'docs/architecture.svg').read_text(),
        '__DATA__': safe_json(data),
        '__ARTIFACTS__': safe_json(originals),
        '__FONT_LICENSE__': safe_json({'Anton': license_text}),
        '__BUILD_INFO__': safe_json({'mode': mode}),
    }
    for marker in replacements:
        if template.count(marker) != 1:
            raise ValueError('Missing or repeated template marker: ' + marker)
    pattern = '|'.join(re.escape(marker) for marker in replacements)
    return re.sub(pattern, lambda match: replacements[match.group()], template)


def recorded_payload(directory):
    from ledger_audit.recorded import validate_recorded
    data = validate_recorded(directory)
    originals = {}
    artifact_hashes = {item['path']: item['sha256'] for item in data['manifest']['artifacts']}
    bindings = {'demo-result': 'demo', 'export': 'export', 'checkpoint': 'checkpoint'}
    for name, key in bindings.items():
        raw = bounded_read(directory / (name + '.json'))
        if hashlib.sha256(raw).hexdigest() != artifact_hashes.get(name + '.json'):
            raise ValueError('Artifact bytes changed after validation: ' + name)
        value = json.loads(raw)
        if value != data[key]:
            raise ValueError('Artifact changed after validation: ' + name)
        originals[name] = raw.decode('utf-8')
    # This is the real manifest, not a browser reconstruction of its hashes.
    manifest = bounded_read(directory / 'recorded-manifest.json')
    if json.loads(manifest) != data['manifest']:
        raise ValueError('Manifest changed after validation')
    originals['recorded-manifest'] = manifest.decode('utf-8')
    return data, originals


def build(from_recorded=False):
    if from_recorded:
        data, originals = recorded_payload(ROOT / 'examples/recorded')
        mode = 'Validated recorded local demo'
    else:
        from ledger_audit.demo import make_demo
        with tempfile.TemporaryDirectory(prefix='audit-showcase-') as temporary:
            directory = Path(temporary) / 'recorded'
            make_demo(directory)
            data, originals = recorded_payload(directory)
        mode = 'Fresh local demo executed during build'
    output = assemble(data, originals, mode)
    path = ROOT / 'docs/index.html'
    path.write_text(output, encoding='utf-8')
    print(f'{path.name}: {len(path.read_bytes())} bytes')
    print('SHA256 ' + hashlib.sha256(path.read_bytes()).hexdigest())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--from-recorded', action='store_true', help='Validate original recorded files without rerunning the demo.')
    build(parser.parse_args().from_recorded)
