"""Archive RPCs use the authenticated current-profile organization boundary."""
import pytest

from eidolon_cli import organization_service as services
import tui_gateway.server as server


@pytest.fixture(autouse=True)
def isolated_services(monkeypatch):
    services.stop_services()
    monkeypatch.setattr(services, '_services', {})
    yield
    assert services.stop_services()


def rpc(method, **params):
    return server.dispatch({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})


def test_history_rpc_retains_exact_objective_and_fences_unbound_transport():
    # Creating through the store avoids starting any billable executor.
    store = services.get_service().store
    objective = store.create_objective('Archive me', idempotency_key='create')
    store.cancel(objective['id'])
    before = rpc('organization.historyObjective', id=objective['id'])['result']
    result = rpc('organization.archive', id=objective['id'], archived=True, expectedRevision=0, idempotencyKey='archive')
    assert 'error' not in result, result
    assert result['result']['objectives'] == []
    page = rpc('organization.history', state='archived')['result']
    assert page['items'][0]['id'] == objective['id']
    assert page['items'][0]['history']['archived'] is True
    exact = rpc('organization.historyObjective', id=objective['id'])['result']
    assert exact['requests'] == before['requests']
    assert exact['objectives'][0]['history']['archived'] is True
    restore = rpc('organization.archive', id=objective['id'], archived=False, expectedRevision=1, idempotencyKey='restore')['result']
    assert restore['objectives'][0]['status'] == 'cancelled'
    assert not services.get_service().running
    for method in ['organization.history', 'organization.historyObjective', 'organization.archive']:
        response = server.handle_request({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': {}})
        assert response['error']['code'] == 4001


@pytest.mark.parametrize('method,params', [
    ('organization.history', {'query': [], 'state': 'all'}),
    ('organization.history', {'limit': True}),
    ('organization.history', {'profile': '../../foreign'}),
    ('organization.history', {'path': '/tmp/foreign.db'}),
    ('organization.historyObjective', {'id': 'missing'}),
    ('organization.historyObjective', {'id': 'missing', 'path': '/tmp/foreign.db'}),
    ('organization.archive', {'id': 'missing', 'archived': 'true', 'expectedRevision': 0, 'idempotencyKey': 'k'}),
    ('organization.archive', {'id': 'missing', 'archived': True, 'expectedRevision': False, 'idempotencyKey': 'k'}),
    ('organization.archive', {'id': 'missing', 'archived': True, 'expectedRevision': 0, 'idempotencyKey': 'k', 'actorId': 'owner'}),
])
def test_history_rpc_rejects_invalid_or_cross_boundary_payloads(method, params):
    assert rpc(method, **params)['error']['code'] == -32602


def test_archive_rpc_waits_for_cancelled_provider_in_another_runtime():
    from eidolon_cli.organization_service import _ExecutionLock
    organization = services.get_service()
    objective = organization.store.create_objective('Wait for cancellation', idempotency_key='create')
    claim = organization.store.claim_next()
    organization.cancel(objective['id'])
    with _ExecutionLock(organization.home / 'organization' / 'execution-locks', claim['id']) as acquired:
        assert acquired
        response = rpc('organization.archive', id=objective['id'], archived=True, expectedRevision=0, idempotencyKey='archive')
        assert response['error']['code'] == -32602
        assert 'still active' in response['error']['message']
        assert rpc('organization.history')['result']['items'] == []
    assert 'error' not in rpc('organization.archive', id=objective['id'], archived=True, expectedRevision=0, idempotencyKey='archive')
