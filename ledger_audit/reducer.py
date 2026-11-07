"""Pure replay representation, independently checked against SQL materialization."""
from .formats import IntegrityFailure, digest


def reduce_events(records):
    runs, artifacts = {}, {}
    for r in records:
        p, seq = r['payload'], r['seq']
        key = p['run_id']
        if r['type'] == 'run_started':
            if key in runs:
                raise IntegrityFailure('semantic_error', seq)
            runs[key] = {'run_id': key, 'actor': p['actor'], 'input_digest': p['input_digest'],
                         'started_seq': seq, 'finished_seq': None, 'outcome': None}
        else:
            if key not in runs or runs[key]['outcome'] is not None:
                raise IntegrityFailure('semantic_error', seq)
            if r['type'] == 'run_finished':
                runs[key]['outcome'] = p['outcome']
                runs[key]['finished_seq'] = seq
            else:
                artifact_key = (key, p['artifact_id'])
                if artifact_key in artifacts:
                    raise IntegrityFailure('semantic_error', seq)
                artifacts[artifact_key] = {**p, 'event_seq': seq}
    return {'format': 'audit-state-v1', 'reducer_version': 'projection-v1',
            'runs': [runs[k] for k in sorted(runs)],
            'artifacts': [artifacts[k] for k in sorted(artifacts)]}


def counts(state):
    finished = sum(r['outcome'] is not None for r in state['runs'])
    return {'runs': len(state['runs']), 'active_runs': len(state['runs']) - finished,
            'finished_runs': finished, 'artifacts': len(state['artifacts']),
            'artifact_bytes': sum(a['byte_count'] for a in state['artifacts'])}


def state_digest(state):
    return digest('audit-state-v1', state)
