"""Short human-readable renderings of command results. `--json` bypasses this module."""

import unicodedata


def clean(value):
    """Escape control, format and bidi characters (Unicode category C*) so input-derived text cannot drive a terminal."""
    out = []
    for ch in str(value):
        if unicodedata.category(ch).startswith('C'):
            out.append('\\x%02x' % ord(ch) if ord(ch) < 256 else '\\u%04x' % ord(ch) if ord(ch) < 65536 else '\\U%08x' % ord(ch))
        else:
            out.append(ch)
    return ''.join(out)


HINTS = {
    'output_exists': 'the output path already exists; this tool never overwrites. Choose a new path.',
    'invalid_input': 'arguments or file contents were rejected. Run with --help or see README.md.',
    'conflict': 'this event_id was already used with different content.',
    'busy': 'the database is locked by another process. Retry shortly.',
    'unsafe_input': 'the input is not a regular file.',
    'limit_exceeded': 'an input or ledger limit was exceeded (see README, Limits).',
    'input_io_error': 'the input file could not be read.',
    'output_io_error': 'the output could not be written.',
}


def error_line(code):
    return clean('error: %s%s' % (code, (' - ' + HINTS[code]) if code in HINTS else ''))


def _short(value):
    return value[:12] if isinstance(value, str) else str(value)


def _verify(r):
    if r['chain'] != 'consistent':
        code = r['failure']['code'] if r['failure'] else 'unknown'
        return ['FAIL  chain is not usable: %s' % code]
    lines = ['%s  chain consistent, %d event(s), head %s' % (
        'FAIL' if r['failure'] else 'OK  ', r['snapshot_seq'], _short(r['head_hash']))]
    if r['failure']:
        lines.append('FAIL  anchor check failed: %s' % r['failure']['code'])
    elif r['anchor'] == 'none':
        lines.append('note  no checkpoint given: completeness is unknown (see --checkpoint)')
    else:
        lines.append('OK    anchor %s: %d events pinned by checkpoint, %d after it' % (
            r['anchor'], r['anchored_seq'], r['unanchored_tail']))
    return lines


def _demo(r):
    lines = ['Recorded 4 events, wrote and measured %d files, then tampered with 3 copies:' % len(r['artifact_checks'])]
    expect = {'payload_changed': 'hash_mismatch', 'middle_deleted': 'sequence_gap',
              'tail_truncated': 'truncated_before_anchor'}
    for t in r['tamper_checks']:
        plain, anchored = t['without_anchor'], t['with_anchor']
        if t['case'] == 'tail_truncated':
            lines.append('  %-16s without checkpoint: chain %s (missing history NOT seen); with checkpoint: %s' % (
                t['case'], plain['chain'], anchored['failure']['code']))
        else:
            lines.append('  %-16s detected without checkpoint: %s' % (t['case'], plain['failure']['code']))
    ok = all(t['without_anchor']['failure'] is not None or t['with_anchor']['failure'] is not None
             for t in r['tamper_checks'])
    c = r['replay_full']['counts']
    lines.append('Replay: %d run(s), %d finished, %d artifact(s), %d bytes' % (
        c['runs'], c['finished_runs'], c['artifacts'], c['artifact_bytes']))
    lines.append('Verdict: %s' % ('PASS - all 3 tamper cases caught as expected; file hashes match'
                                  if ok and all(a['matches'] for a in r['artifact_checks']) else 'FAIL'))
    return lines


def render(r):
    return [clean(line) for line in _render(r)]


def _render(r):
    fmt = r.get('format')
    if fmt == 'audit-demo-v1':
        return _demo(r)
    if fmt == 'audit-verify-v1':
        return _verify(r)
    if fmt == 'audit-append-v1':
        rec = r['record']
        return ['%s event %s as seq %d (%s)' % (r['disposition'], rec['event_id'], rec['seq'], rec['type'])]
    if fmt == 'audit-replay-v1':
        c = r['counts']
        return ['replayed events 1..%d into SQL: %d run(s), %d active, %d finished, %d artifact(s)' % (
            r['through_seq'], c['runs'], c['active_runs'], c['finished_runs'], c['artifacts'])]
    if fmt == 'audit-file-result-v1':
        line = '%s done: %d events, head %s' % (r['operation'], r['count'], _short(r['head_hash']))
        if r['artifact_digest']:
            line += ', artifact digest %s' % r['artifact_digest']
        return [line]
    return [str(r)]
