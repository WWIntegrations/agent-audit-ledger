"""Human summary by default, `--json` for machines; bounded diagnostics, no network operations."""
import argparse
from pathlib import Path
import sqlite3
import sys
import tempfile

from . import core, formats as f
from .demo import make_demo
from .files import read_json
from .projection import replay
from .summary import clean, error_line, render


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise f.AuditError('invalid_input')


def parser():
    p = Parser(prog='ledger-audit', description='Local hash-chained ledger and real SQL replay.')
    sub = p.add_subparsers(dest='command', required=True)
    for name in ('init', 'append', 'verify', 'checkpoint', 'export', 'import', 'replay', 'demo'):
        cmd = sub.add_parser(name)
        cmd.add_argument('--json', action='store_true', help='print the full machine-readable JSON result')
        if name == 'demo':
            cmd.add_argument('--out-dir', help='new directory for the demo files (default: a fresh temporary one)')
            continue
        cmd.add_argument('--db', required=True)
        if name == 'append':
            cmd.add_argument('--event', required=True)
        if name in ('checkpoint', 'export'):
            cmd.add_argument('--out', required=True)
        if name == 'import':
            cmd.add_argument('--export', dest='export_path', required=True)
        if name == 'replay':
            cmd.add_argument('--out-db', required=True)
            cmd.add_argument('--through-seq')
        if name in ('append', 'verify', 'export', 'import', 'replay'):
            cmd.add_argument('--checkpoint')
            cmd.add_argument('--expected-checkpoint-digest')
    return p


def main(argv=None):
    try:
        a = parser().parse_args(argv)
        as_json = a.json
        if as_json:
            print(f.canonical({'format': 'audit-runtime-v1', 'python': sys.version.split()[0],
                               'sqlite': sqlite3.sqlite_version}).decode(), file=sys.stderr)
        anchor = core.load_anchor(getattr(a, 'checkpoint', None), getattr(a, 'expected_checkpoint_digest', None))
        if a.command == 'init':
            result = core.init(a.db)
        elif a.command == 'append':
            result = core.append(a.db, read_json(a.event, 4096), anchor)
        elif a.command == 'verify':
            result = core.verify(a.db, anchor)
        elif a.command == 'checkpoint':
            result = core.checkpoint(a.db, a.out)
        elif a.command == 'export':
            result = core.export_file(a.db, a.out, anchor)
        elif a.command == 'import':
            result = core.import_file(a.export_path, a.db, anchor)
        elif a.command == 'replay':
            t = None
            if a.through_seq is not None:
                if not a.through_seq.isascii() or not a.through_seq.isdecimal() or len(a.through_seq) > 4:
                    raise f.AuditError('invalid_input')
                t = f.integer(int(a.through_seq), upper=1000)
            result = replay(a.db, a.out_db, t, anchor)
        else:
            out_dir = a.out_dir or str(Path(tempfile.mkdtemp(prefix='audit-ledger-demo-')) / 'run')
            result = make_demo(out_dir)
            if not as_json:
                print('Demo files: %s' % clean(out_dir))
        if as_json:
            sys.stdout.buffer.write(f.bounded(result, f.MAX_JSON-1) + b'\n')
        else:
            print('\n'.join(render(result)))
        sys.stdout.flush()
        return 1 if result.get('format') == 'audit-verify-v1' and result['failure'] is not None else 0
    except f.AuditError as exc:
        if '--json' in (argv if argv is not None else sys.argv[1:]):
            print(f.canonical({'format': 'audit-error-v1', 'code': exc.code}).decode(), file=sys.stderr)
        else:
            print(error_line(exc.code), file=sys.stderr)
        return exc.exit_code
    except OSError:
        if '--json' in (argv if argv is not None else sys.argv[1:]):
            print('{"code":"io_error","format":"audit-error-v1"}', file=sys.stderr)
        else:
            print('error: io_error - a file operation failed', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
