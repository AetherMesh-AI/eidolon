"""The authenticated RPC boundary owns answers/configuration and profile refresh."""
import pytest

from eidolon_cli import organization_service as services
from eidolon_cli.organization_store import OrganizationStore
import tui_gateway.server as server


def rpc(method, **params):
    result = server.dispatch({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})
    assert 'error' not in result, result
    return result['result']


@pytest.fixture
def service(monkeypatch):
    services.stop_services()
    monkeypatch.setattr(services, '_services', {})
    organization = services.get_service()
    # Ledger transitions stay deterministic; the RPC's real transport/profile
    # checks and mutation methods remain intact. Model execution has separate E2E.
    monkeypatch.setattr(organization, 'start', lambda: None)
    yield organization
    services.stop_services()


def pending_question(store):
    store.create_objective('Question before planning', idempotency_key='objective')
    planner = store.claim_next()
    store.finish(planner, {'requests': [{'type': 'request.question', 'requestedOutcome': 'Which launch date?'}]})
    assert store.claim_next() is None
    return planner, next(row for row in store.snapshot()['requests'] if row['type'] == 'request.question')


def test_rpc_response_replays_once_resumes_original_identity_and_rejects_forgery(service):
    planner, question = pending_question(service.store)
    for extra in ({'responderId': 'manager'}, {'authority': '*'}, {'id': 'another-profile-request'}):
        result = server.dispatch({'jsonrpc': '2.0', 'id': 1, 'method': 'organization.respond', 'params': {
            'id': question['id'], 'text': 'Friday', 'decision': 'answered', 'idempotencyKey': 'answer', **extra}})
        assert result['error']['code'] == -32602
    request = {'id': question['id'], 'text': 'Friday', 'decision': 'answered', 'idempotencyKey': 'answer'}
    first = rpc('organization.respond', **request)
    assert rpc('organization.respond', **request)['requests'] == first['requests']
    response = next(row for row in first['requests'] if row['id'] == question['id'])['response']
    assert response['text'] == 'Friday' and response['responderId'] == 'owner'
    resume = service.store.claim_next()
    assert resume['id'] == planner['id'] and resume['agent_id'] == planner['agent_id']
    unbound = server.handle_request({'jsonrpc': '2.0', 'id': 1, 'method': 'organization.respond', 'params': request})
    assert unbound['error']['code'] == 4001


def test_config_rpc_live_refresh_preserves_saved_roster_and_exact_duplicate_with_active_worker(service):
    initial = rpc('organization.snapshot')
    config = initial['runtime']['management']['configuration']
    config['max_inflight'] = 1
    config['roster'].append({'id': 'advisor', 'name': 'Advisor', 'role': 'Manager',
        'team': 'general', 'capabilities': ['request.question'], 'authority': ['answer.question']})
    params = {'configuration': config, 'expectedGeneration': initial['runtime']['management']['generation'],
              'idempotencyKey': 'configure'}
    snapshot = rpc('organization.configure', **params)
    assert snapshot['runtime']['maxInflight'] == 1
    assert any(row['id'] == 'advisor' for row in snapshot['agents'])
    # Delivery retry may arrive after the successful configuration started work.
    service._running['active-placeholder'] = object()
    try:
        assert rpc('organization.configure', **params)['runtime']['management'] == snapshot['runtime']['management']
    finally:
        service._running.clear()
    foreign = OrganizationStore(service.store.path)
    foreign.configure_organization({'max_inflight': 2}, expected_generation=foreign._policy_generation,
                                   idempotency_key='second-runtime')
    with service.store._connect() as conn:
        assert not service.store._policy_current(conn)
    refreshed = rpc('organization.snapshot')
    assert refreshed['runtime']['maxInflight'] == 2
    assert refreshed['runtime']['state'] == 'ready'
    assert service.settings.max_inflight == 2
    assert {row['id'] for row in refreshed['runtime']['management']['configuration']['roster']} == {row['id'] for row in config['roster']}


def test_new_handler_configuration_refreshes_unhandled_question_without_retrying_failed_calls(service):
    _, question = pending_question(service.store)
    initial = rpc('organization.snapshot')['runtime']['management']
    config = initial['configuration']
    config['roster'].append({'id': 'advisor', 'name': 'Advisor', 'role': 'Manager',
        'team': 'general', 'capabilities': ['request.question'], 'authority': ['answer.question']})
    updated = rpc('organization.configure', configuration=config, expectedGeneration=initial['generation'],
                  idempotencyKey='handler')
    assert next(row for row in updated['requests'] if row['id'] == question['id'])['status'] == 'queued'
    claim = service.store.claim_next()
    assert claim['id'] == question['id'] and claim['agent_id'] == 'advisor'
    service.store.fail(claim, 'Provider stopped')
    service.store.refresh_unhandled_requests()
    assert next(row for row in service.store.snapshot()['requests'] if row['id'] == question['id'])['status'] == 'pending_intervention'
