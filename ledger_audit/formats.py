"""Closed, bounded JSON and the versioned canonical representation."""
from datetime import datetime
import hashlib
import json
import re

MAX_JSON = 16 * 1024 * 1024
MAX_EVENTS = 1000
MAX_DB = 32 * 1024 * 1024
MAX_INT = 9007199254740991
ID = re.compile(r'[a-z][a-z0-9_-]{0,63}\Z')
HASH = re.compile(r'[0-9a-f]{64}\Z')
LEDGER_ID = re.compile(r'[0-9a-f]{32}\Z')
STAMP = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z\Z', re.ASCII)


class AuditError(Exception):
    def __init__(self, code, exit_code=2):
        super().__init__(code)
        self.code = code
        self.exit_code = exit_code


class IntegrityFailure(AuditError):
    def __init__(self, code, seq=None):
        super().__init__(code, 1)
        self.seq = seq


def canonical(value):
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=True,
                          separators=(',', ':'), allow_nan=False).encode('ascii')
    except (ValueError, TypeError, RecursionError) as exc:
        raise AuditError('invalid_input') from exc


def digest(domain, value):
    return hashlib.sha256(domain.encode('ascii') + b'\n' + canonical(value)).hexdigest()


def closed(value, keys):
    if type(value) is not dict or set(value) != set(keys.split()):
        raise AuditError('invalid_input')
    return value


def integer(value, lower=0, upper=MAX_INT):
    if type(value) is not int or not lower <= value <= upper:
        raise AuditError('invalid_input')
    return value


def pattern(value, regex=ID):
    if type(value) is not str or regex.fullmatch(value) is None:
        raise AuditError('invalid_input')
    return value


def _pairs(pairs):
    obj = {}
    for k, v in pairs:
        if k in obj:
            raise AuditError('invalid_input')
        obj[k] = v
    return obj


def _forbid(_):
    raise AuditError('invalid_input')


def _integer(token):
    if len(token) > 16 or not token.isascii() or not token.isdecimal():
        raise AuditError('invalid_input')
    return integer(int(token))


def parse(data, limit=MAX_JSON):
    if type(data) is not bytes or len(data) > limit:
        raise AuditError('limit_exceeded')
    try:
        text = data.decode('utf-8')
        if text.startswith('\ufeff'):
            raise AuditError('invalid_input')
        depth = 0
        quoted = escaped = False
        for ch in text:
            if quoted:
                if escaped:
                    escaped = False
                elif ch == '\\':
                    escaped = True
                elif ch == '"':
                    quoted = False
            elif ch == '"':
                quoted = True
            elif ch in '[{':
                depth += 1
                if depth > 10:
                    raise AuditError('limit_exceeded')
            elif ch in ']}':
                depth -= 1
        obj = json.loads(text, object_pairs_hook=_pairs, parse_int=_integer,
                         parse_float=_forbid, parse_constant=_forbid)
        stack = [obj]
        while stack:
            item = stack.pop()
            if isinstance(item, str) and any(0xD800 <= ord(c) <= 0xDFFF for c in item):
                raise AuditError('invalid_input')
            if isinstance(item, dict):
                stack.extend(item.keys())
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)
        return obj
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise AuditError('invalid_input') from exc


def bounded(value, limit):
    data = canonical(value)
    if len(data) > limit:
        raise AuditError('limit_exceeded')
    return data


def header(value):
    closed(value, 'format ledger_id event_version reducer_version')
    if (value['format'], value['event_version'], value['reducer_version']) != (
            'audit-header-v1', 'event-v1', 'projection-v1'):
        raise AuditError('invalid_input')
    pattern(value['ledger_id'], LEDGER_ID)
    return value


def event_input(value):
    closed(value, 'event_id type payload')
    pattern(value['event_id'])
    kind, p = value['type'], value['payload']
    if kind == 'run_started':
        closed(p, 'run_id actor input_digest')
        pattern(p['actor'])
        pattern(p['input_digest'], HASH)
    elif kind == 'artifact_recorded':
        closed(p, 'run_id artifact_id content_digest byte_count')
        pattern(p['artifact_id'])
        pattern(p['content_digest'], HASH)
        integer(p['byte_count'], upper=1048576)
    elif kind == 'run_finished':
        closed(p, 'run_id outcome')
        if p['outcome'] not in ('success', 'failed'):
            raise AuditError('invalid_input')
    else:
        raise AuditError('invalid_input')
    pattern(p['run_id'])
    bounded(value, 4096)
    return value


def input_from_record(value):
    return {k: value[k] for k in ('event_id', 'type', 'payload')}


def record(value):
    closed(value, 'seq event_id type payload recorded_at prev_hash event_hash')
    integer(value['seq'], 1, MAX_EVENTS)
    event_input(input_from_record(value))
    pattern(value['prev_hash'], HASH)
    pattern(value['event_hash'], HASH)
    pattern(value['recorded_at'], STAMP)
    try:
        datetime.strptime(value['recorded_at'], '%Y-%m-%dT%H:%M:%S.%fZ')
    except ValueError as exc:
        raise AuditError('invalid_input') from exc
    bounded(value, 8192)
    return value


def event_hash(h, r):
    return digest('audit-event-v1', {'header': h, **{k: v for k, v in r.items() if k != 'event_hash'}})


def checkpoint(value):
    closed(value, 'format header seq head_hash')
    if value['format'] != 'audit-checkpoint-v1':
        raise AuditError('invalid_input')
    header(value['header'])
    integer(value['seq'], upper=MAX_EVENTS)
    pattern(value['head_hash'], HASH)
    bounded(value, 4096)
    return value


def provenance(value):
    closed(value, 'producer version source_origin source_export_digest')
    if (value['producer'], value['version']) != ('agent-audit-ledger', 'v1'):
        raise AuditError('invalid_input')
    origin(value['source_origin'], value['source_export_digest'])
    return value


def origin(kind, source):
    if kind == 'local' and source is None:
        return
    if kind == 'imported':
        pattern(source, HASH)
        return
    raise AuditError('invalid_input')


def export(value):
    closed(value, 'format header count head_hash events provenance')
    if value['format'] != 'audit-export-v1':
        raise AuditError('invalid_input')
    header(value['header'])
    integer(value['count'], upper=MAX_EVENTS)
    pattern(value['head_hash'], HASH)
    provenance(value['provenance'])
    if type(value['events']) is not list or len(value['events']) > MAX_EVENTS:
        raise AuditError('limit_exceeded')
    for r in value['events']:
        record(r)
    bounded(value, MAX_JSON - 1)
    return value
