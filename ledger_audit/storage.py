"""Fixed SQLite schema, bounded scans and explicit transactions."""
from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import sqlite3
import stat
import sys
import time

from . import formats as f
from .reducer import reduce_events

META_SQL = """CREATE TABLE meta (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 header_json TEXT NOT NULL CHECK(typeof(header_json)='text'),
 head_seq INTEGER NOT NULL CHECK(typeof(head_seq)='integer' AND head_seq BETWEEN 0 AND 1000),
 head_hash TEXT NOT NULL CHECK(typeof(head_hash)='text' AND length(head_hash)=64),
 origin TEXT NOT NULL CHECK(origin IN ('local','imported')),
 source_export_digest TEXT,
 CHECK((origin='local' AND source_export_digest IS NULL) OR
       (origin='imported' AND length(source_export_digest)=64)))"""
EVENTS_SQL = """CREATE TABLE events (
 seq INTEGER PRIMARY KEY CHECK(typeof(seq)='integer' AND seq BETWEEN 1 AND 1000),
 event_id TEXT UNIQUE NOT NULL CHECK(typeof(event_id)='text'),
 record_json TEXT NOT NULL CHECK(typeof(record_json)='text' AND length(record_json)<=8192))"""
LEDGER_SCHEMA = [('table', 'meta', 'meta', META_SQL),
                 ('table', 'events', 'events', EVENTS_SQL),
                 ('index', 'sqlite_autoindex_events_1', 'events', None)]


@dataclass
class Snapshot:
    header: dict
    records: list
    head_hash: str
    origin: str
    source_export_digest: str | None
    state: dict


def _db_stat(path):
    try:
        s = os.lstat(path)
        if not stat.S_ISREG(s.st_mode):
            raise f.AuditError('unsafe_input')
        if s.st_size > f.MAX_DB:
            raise f.AuditError('limit_exceeded')
        for suffix in ('-wal', '-shm'):
            if os.path.lexists(str(path) + suffix):
                raise f.AuditError('unsupported_journal')
        journal = str(path) + '-journal'
        if os.path.lexists(journal):
            j = os.lstat(journal)
            if not stat.S_ISREG(j.st_mode) or j.st_nlink != 1:
                raise f.AuditError('unsafe_input')
            if j.st_size > f.MAX_DB:
                raise f.AuditError('limit_exceeded')
        return s
    except OSError as exc:
        raise f.AuditError('storage_error') from exc


def _text(raw):
    """Decode TEXT strictly. Invalid UTF-8 stays bytes, so the domain checks reject the edited value."""
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return raw


def translate_sql(exc):
    code = getattr(exc, 'sqlite_errorcode', 0) & 255
    if code in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB):
        return f.IntegrityFailure('sqlite_integrity')
    if code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
        return f.AuditError('busy')
    if code in (sqlite3.SQLITE_TOOBIG, sqlite3.SQLITE_FULL):
        return f.AuditError('limit_exceeded')
    if code == sqlite3.SQLITE_INTERRUPT:
        return f.AuditError('budget_exceeded')
    return f.AuditError('storage_error')


@contextmanager
def database(path, *, initialize=False, readonly=False):
    path = Path(path).absolute()
    before = _db_stat(path)
    c = None
    try:
        if sys.version_info < (3, 11) or sqlite3.sqlite_version_info < (3, 31):
            raise f.AuditError('unsupported_runtime')
        if readonly and os.path.lexists(str(path) + '-journal'):
            raise f.AuditError('recovery_required')
        c = sqlite3.connect(path.as_uri() + ('?mode=ro' if readonly else '?mode=rw'),
                            uri=True, isolation_level=None, timeout=2)
        c.row_factory = sqlite3.Row
        c.text_factory = _text
        after = os.lstat(path)
        if (before.st_ino, before.st_dev) != (after.st_ino, after.st_dev):
            raise f.AuditError('input_changed')
        for limit, value in ((sqlite3.SQLITE_LIMIT_LENGTH, 65536),
                             (sqlite3.SQLITE_LIMIT_SQL_LENGTH, 65536),
                             (sqlite3.SQLITE_LIMIT_ATTACHED, 0)):
            c.setlimit(limit, value)
            if c.getlimit(limit) != value:
                raise f.AuditError('unsupported_runtime')
        deadline = time.monotonic() + 5
        c.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        settings = {'foreign_keys': 1, 'trusted_schema': 0, 'mmap_size': 0,
                    'synchronous': 3, 'busy_timeout': 2000}
        if sys.platform == 'darwin':
            settings['fullfsync'] = 1
        for name, value in settings.items():
            c.execute(f'PRAGMA {name}={value}')
            row = c.execute(f'PRAGMA {name}').fetchone()
            if row is None or row[0] != value:
                raise f.AuditError('unsupported_runtime')
        if initialize:
            c.execute('PRAGMA page_size=4096')
            c.execute('PRAGMA journal_mode=DELETE')
        if c.execute('PRAGMA journal_mode').fetchone()[0] != 'delete':
            raise f.AuditError('unsupported_journal')
        if c.execute('PRAGMA page_size').fetchone()[0] != 4096:
            raise f.AuditError('unsupported_schema')
        if not readonly:
            value = c.execute('PRAGMA max_page_count=8192').fetchone()[0]
            if value != 8192:
                raise f.AuditError('limit_exceeded')
        yield c
    except sqlite3.DatabaseError as exc:
        raise translate_sql(exc) from exc
    finally:
        if c is not None:
            try:
                if c.in_transaction:
                    c.rollback()
            finally:
                c.close()


def check_schema(c, expected=LEDGER_SCHEMA):
    rows = [tuple(r) for r in c.execute('SELECT type,name,tbl_name,sql FROM sqlite_master').fetchmany(20)]
    if len(rows) != len(expected) or set(rows) != set(expected):
        raise f.IntegrityFailure('schema_mismatch')
    if [r[0] for r in c.execute('PRAGMA quick_check').fetchmany(2)] != ['ok']:
        raise f.IntegrityFailure('sqlite_integrity')
    if c.execute('PRAGMA foreign_key_check').fetchone() is not None:
        raise f.IntegrityFailure('sqlite_integrity')


def create_ledger(c, h, records=(), origin='local', source=None):
    c.execute(META_SQL)
    c.execute(EVENTS_SQL)
    head = records[-1]['event_hash'] if records else f.digest('audit-genesis-v1', h)
    c.execute('INSERT INTO meta VALUES(1,?,?,?,?,?)',
              (f.canonical(h).decode(), len(records), head, origin, source))
    c.executemany('INSERT INTO events VALUES(?,?,?)',
                  [(r['seq'], r['event_id'], f.canonical(r).decode()) for r in records])


def validate_chain(h, records, count, head):
    try:
        f.header(h)
        f.integer(count, upper=f.MAX_EVENTS)
        f.pattern(head, f.HASH)
    except f.AuditError as exc:
        raise f.IntegrityFailure('head_mismatch') from exc
    if len(records) > f.MAX_EVENTS:
        raise f.AuditError('limit_exceeded')
    prev = f.digest('audit-genesis-v1', h)
    seen = set()
    for seq, r in enumerate(records, 1):
        try:
            f.record(r)
        except f.AuditError as exc:
            raise f.IntegrityFailure('record_invalid', seq) from exc
        if r['seq'] != seq:
            raise f.IntegrityFailure('sequence_gap', seq)
        if r['event_id'] in seen:
            raise f.IntegrityFailure('record_invalid', seq)
        seen.add(r['event_id'])
        if r['prev_hash'] != prev or r['event_hash'] != f.event_hash(h, r):
            raise f.IntegrityFailure('hash_mismatch', seq)
        prev = r['event_hash']
    if len(records) != count or head != prev:
        raise f.IntegrityFailure('head_mismatch')
    return reduce_events(records)


def scan(c):
    check_schema(c)
    meta = c.execute('SELECT * FROM meta').fetchmany(2)
    if len(meta) != 1 or meta[0]['singleton'] != 1:
        raise f.IntegrityFailure('head_mismatch')
    m = meta[0]
    try:
        h = f.header(f.parse(m['header_json'].encode(), 4096))
        if f.canonical(h).decode() != m['header_json']:
            raise f.AuditError('invalid_input')
        f.origin(m['origin'], m['source_export_digest'])
    except (f.AuditError, AttributeError) as exc:
        raise f.IntegrityFailure('record_invalid') from exc
    rows = c.execute('SELECT seq,event_id,record_json FROM events ORDER BY seq').fetchmany(f.MAX_EVENTS + 1)
    if len(rows) > f.MAX_EVENTS:
        raise f.AuditError('limit_exceeded')
    records = []
    for seq, row in enumerate(rows, 1):
        if row['seq'] != seq:
            raise f.IntegrityFailure('sequence_gap', seq)
        try:
            raw = row['record_json'].encode('utf-8')
            r = f.record(f.parse(raw, 8192))
            if f.canonical(r) != raw or r['seq'] != row['seq'] or r['event_id'] != row['event_id']:
                raise f.AuditError('invalid_input')
        except (f.AuditError, AttributeError) as exc:
            raise f.IntegrityFailure('record_invalid', seq) from exc
        records.append(r)
    state = validate_chain(h, records, m['head_seq'], m['head_hash'])
    return Snapshot(h, records, m['head_hash'], m['origin'], m['source_export_digest'], state)
