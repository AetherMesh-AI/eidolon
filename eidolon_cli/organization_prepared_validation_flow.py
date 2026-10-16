"""Prepared-pair scheduling and finish transitions, with no legacy fallback."""
import json

from eidolon_cli.organization_prepared_validation_authority import EXECUTION_CONTRACT, require_prepared_authority
from eidolon_cli.organization_prepared_validation_current import verify_current_prepared_pair, current_prepared_authority
from eidolon_cli.organization_prepared_validation_verification import prepared_validation_artifact


def ensure_prepared_execution(store, conn, objective_id, project_id, priority, round_number):
    require_prepared_authority(store, conn, objective_id, project_id)
    latest = conn.execute('SELECT id,round FROM prepared_validation_sets WHERE objective_id=? AND project_id=? '
                          'ORDER BY generation DESC LIMIT 1', (objective_id, project_id)).fetchone()
    if latest is not None and latest['round'] == round_number:
        verify_current_prepared_pair(store, conn, objective_id, project_id)
        return False
    store._request(conn, objective_id, 'request.project_test', store.settings.team, priority,
        payload={'executionContract': EXECUTION_CONTRACT, 'projectId': project_id, 'round': round_number})
    return True


def prepared_stage_availability(store, conn, request):
    payload = json.loads(request['payload'])
    if request['type'] != 'request.project_test':
        raise ValueError('Prepared validation does not authorize source publication')
    require_prepared_authority(store, conn, request['objective_id'], payload['projectId'], execution=True)
    if conn.execute('SELECT 1 FROM prepared_validation_sets WHERE request_id=?', (request['id'],)).fetchone():
        raise ValueError('Prepared reservation already exists; automatic replay is prohibited')
    from eidolon_cli.organization_prepared_validation_settlement import prepared_runtime_settlement
    if prepared_runtime_settlement(conn)['unsettledRunCount']:
        raise ValueError('An earlier prepared runtime outcome or cleanup must be reconciled before execution')
    from eidolon_cli.organization_prepared_validation_activity import objective_project_reserved_units
    ceiling = conn.execute('SELECT max_runs FROM project_execution_budgets WHERE objective_id=?',
                           (request['objective_id'],)).fetchone()
    used = objective_project_reserved_units(conn, request['objective_id'])['totalUnits']
    if ceiling is None or used + 2 > min(ceiling['max_runs'], store.settings.max_project_runs):
        raise ValueError('Prepared validation requires two available reserved run units')


def finish_prepared_execution(store, conn, request, result):
    store._require_project_execution_controller(request)
    if result:
        raise ValueError('Prepared execution accepts no model-provided outcome')
    project_id = json.loads(request['payload'])['projectId']
    require_prepared_authority(store, conn, request['objective_id'], project_id, execution=True)
    row = conn.execute('SELECT id FROM prepared_validation_sets WHERE request_id=? AND lease_token=?',
                       (request['id'], request['token'])).fetchone()
    if row is None:
        raise ValueError('No prepared pair was reserved by this exact backend lease')
    runs = list(conn.execute('SELECT run_id FROM prepared_validation_steps WHERE set_id=? ORDER BY step_id', (row['id'],)))
    artifacts = []
    for run in runs:
        # An unstarted mandatory step remains charged. Its absence cannot count
        # as success, but a preceding retained failure may be exposed safely.
        if conn.execute('SELECT 1 FROM prepared_validation_results WHERE run_id=?', (run[0],)).fetchone():
            artifacts.append(prepared_validation_artifact(conn, run[0]))
    failed = [item for item in artifacts if item is not None and item['projectExecution']['status'] != 'passed']
    if failed:
        gate = store._request(conn, request['objective_id'], 'request.project_failed', request['team'], request['priority'],
            payload={'executionContract': EXECUTION_CONTRACT, 'projectId': project_id, 'setId': row['id'],
                     'evidenceIds': [item['id'] for item in failed]})
        store._pending(conn, conn.execute('SELECT * FROM requests WHERE id=?', (gate,)).fetchone(),
            'Prepared validation did not pass. Both reserved units remain charged; inspect the exact retained outcome before replanning.')
        return
    verified = verify_current_prepared_pair(store, conn, request['objective_id'], project_id, require_review=False)
    if verified['envelope']['setId'] != row['id']:
        raise ValueError('Prepared finish cannot reuse another generation')
    envelope = verified['envelope']
    store._request(conn, request['objective_id'], 'request.test_review', request['team'], request['priority'],
        payload={'executionContract': EXECUTION_CONTRACT, 'projectId': project_id, 'setId': row['id'],
                 'round': envelope['round'], 'evidenceIds': verified['evidenceIds'], 'evidenceHashes': verified['evidenceHashes']})


def finish_prepared_review(store, conn, request, result):
    from eidolon_cli.organization_prepared_validation_verification import record_prepared_validation_pair_review
    record_prepared_validation_pair_review(conn, request, result,
        current_authority=lambda connection, envelope, snapshot: current_prepared_authority(store, connection, envelope, snapshot, require_review=False))
    if result['approved']:
        return
    control = conn.execute('SELECT round,max_replans FROM objective_control WHERE objective_id=?',
                           (request['objective_id'],)).fetchone()
    if control['round'] < min(control['max_replans'], store.settings.max_replans):
        store._replan(conn, request['objective_id'], result['summary'], completing_request=request['id'], requester_id=request['agent_id'])
    else:
        gate = store._request(conn, request['objective_id'], 'request.project_failed', request['team'], request['priority'],
            payload={'evidenceIds': result['evidenceIds'], 'budgetExhausted': True})
        store._pending(conn, conn.execute('SELECT * FROM requests WHERE id=?', (gate,)).fetchone(),
            'Independent prepared-pair review rejected the result and the replan budget is exhausted.')
