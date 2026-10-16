"""Bounded retained prepared outcomes for repair planning, never new authority."""
import json
import time

from eidolon_cli.organization_prepared_validation_ledger import _bounded_text, _checked_json
from eidolon_cli.organization_prepared_validation_contract import _envelope_shape
from eidolon_cli.organization_prepared_validation_verification import prepared_validation_artifact


def prepared_replan_history(conn, objective_id, round_number, *, completing_request=None):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='prepared_validation_sets'").fetchone():
        return []
    count = conn.execute('SELECT count(*) FROM prepared_validation_sets WHERE objective_id=? AND round<=?',
                         (objective_id, round_number)).fetchone()[0]
    if count > 100:
        raise ValueError('Prepared replan history exceeds the reviewable bound; decompose the objective')
    history = []
    for row in conn.execute(f'SELECT {_bounded_text("envelope", 131072)},{_bounded_text("envelope_sha256", 64)} '
            'FROM prepared_validation_sets WHERE objective_id=? AND round<=? ORDER BY round,generation,project_id',
            (objective_id, round_number)):
        envelope = _envelope_shape(_checked_json(*row))
        review = conn.execute(f'SELECT {_bounded_text("record", 2 * 1024**2)},{_bounded_text("sha256", 64)} '
            'FROM prepared_validation_pair_reviews WHERE set_id=?', (envelope['setId'],)).fetchone()
        review_value = _checked_json(*review) if review else None
        if review_value and (review_value.get('setId') != envelope['setId'] or review_value.get('envelopeSha256') != row[1]):
            raise ValueError('Prepared historical review differs from its retained pair')
        if review_value:
            request = conn.execute(f'SELECT {_bounded_text("payload", 2 * 1024**2)},{_bounded_text("agent_id", 128)},'
                f'{_bounded_text("status", 32)},{_bounded_text("type", 64)},{_bounded_text("objective_id", 128)},'
                f'{_bounded_text("token", 256)},CASE WHEN typeof(lease) IN (\'real\',\'integer\') THEN lease END '
                'FROM requests WHERE id=?', (review_value['requestId'],)).fetchone()
            completing = (request is not None and conn.in_transaction and review_value['requestId'] == completing_request
                          and request[2] == 'running' and type(request[5]) is str and bool(request[5])
                          and type(request[6]) in (int, float) and request[6] > time.time())
            if (request is None or type(request[0]) is not str or json.loads(request[0]) != review_value['request']
                    or tuple(request[1:5]) != (review_value['reviewerId'], 'running' if completing else 'completed', 'request.test_review', objective_id)
                    or review_value['reviewerId'] in review_value['contributorIds']
                    or type(review_value['approved']) is not bool):
                raise ValueError('Prepared historical review lacks its exact independent completed request')
        for step in envelope['steps']:
            retained = conn.execute('SELECT 1 FROM prepared_validation_results WHERE run_id=?', (step['runId'],)).fetchone()
            artifact = prepared_validation_artifact(conn, step['runId']) if retained else None
            history.append({'runId': step['runId'], 'requestId': envelope['requestId'],
                'projectId': envelope['projectId'], 'round': envelope['round'], 'setId': envelope['setId'],
                'executionContract': 'prepared-validation-v1', 'checkKind': step['checkKind'],
                'status': artifact['projectExecution']['status'] if artifact else 'unknown',
                'snapshotSha256': envelope['source']['snapshotSha256'],
                'evidenceIds': [step['runId']] if artifact else [],
                'review': {'approved': review_value['approved'], 'reviewerId': review_value['reviewerId'],
                           'summary': review_value['summary'], 'requestId': review_value['requestId']} if review_value else None})
    if len(json.dumps(history).encode()) > 262144:
        raise ValueError('Prepared replan evidence exceeds its context bound; decompose the review')
    return history
