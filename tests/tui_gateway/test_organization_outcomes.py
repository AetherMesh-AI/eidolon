"""Real authenticated/profile-scoped RPC dispatch for owner outcomes metadata."""
from types import SimpleNamespace

import pytest

from eidolon_cli import organization_service as services
from eidolon_cli.organization_store import OrganizationStore
import tui_gateway.server as server
from tui_gateway.transport import bind_transport, reset_transport


@pytest.fixture(autouse=True)
def isolated_services(monkeypatch):
    services.stop_services()
    monkeypatch.setattr(services, '_services', {})
    # These RPCs must not execute work, even when other requests are queued.
    def forbidden_execution(*args):
        raise AssertionError('Outcome must never start organization execution')
    monkeypatch.setattr(services, '_execute', forbidden_execution)
    yield
    assert services.stop_services()


def rpc(method, **params):
    return server.dispatch({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})


def blocked(store):
    objective = store.create_objective('Owner needs to see this', idempotency_key='create')
    store.cancel(objective['id'])
    return objective['id']


def test_outcomes_ack_is_idempotent_returns_snapshot_and_does_not_start_work():
    organization = services.get_service()
    request_id = blocked(organization.store)
    organization.store.create_objective('Keep other queued work stopped', idempotency_key='queued')
    before = organization.store.snapshot()
    page = rpc('organization.outcomes', limit=1)['result']
    observed = page['items'][0]
    first = rpc('organization.markOutcomeSeen', id=request_id, revision=observed['revision'])['result']
    second = rpc('organization.markOutcomeSeen', id=request_id, revision=observed['revision'])['result']
    assert first == second
    assert first['requests'] == before['requests']
    assert first['outcomes']['unread'] == 0 and first['outcomes']['total'] == 1
    assert first['outcomes']['items'][0]['seen']
    assert rpc('organization.outcomes', unreadOnly=True)['result']['items'] == []
    assert not organization.running
    with organization.store._write() as conn:
        conn.execute('UPDATE objectives SET title=? WHERE id=?', ('New owner decision needed', request_id))
    stale = rpc('organization.markOutcomeSeen', id=request_id, revision=observed['revision'])
    assert stale['error']['code'] == -32602 and 'refresh' in stale['error']['message']
    assert rpc('organization.outcomes')['result']['unread'] == 1
    assert not organization.running


@pytest.mark.parametrize('method', ['organization.outcomes', 'organization.markOutcomeSeen'])
def test_outcomes_requires_live_owner_transport(method):
    request = {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': {}}
    assert server.handle_request(request)['error']['code'] == 4001
    token = bind_transport(SimpleNamespace(closed=True))
    try:
        assert server.handle_request(request)['error']['code'] == 4001
    finally:
        reset_transport(token)
    assert not services.state_path().exists()


@pytest.mark.parametrize('method,params', [
    ('organization.outcomes', {'path': '/tmp/other-profile.db'}),
    ('organization.outcomes', {'profile': '../../other-profile'}),
    ('organization.outcomes', {'limit': True}),
    ('organization.outcomes', {'limit': 101}),
    ('organization.outcomes', {'unreadOnly': 'yes'}),
    ('organization.outcomes', {'before': 'missing'}),
    ('organization.outcomes', {'offset': 1}),
    ('organization.markOutcomeSeen', {'id': 'missing', 'revision': 1}),
    ('organization.markOutcomeSeen', {'id': 'missing', 'revision': True}),
    ('organization.markOutcomeSeen', {'id': 'missing', 'revision': 0}),
    ('organization.markOutcomeSeen', {'id': 'missing', 'revision': 2**53}),
    ('organization.markOutcomeSeen', {'id': 'missing', 'revision': 1, 'actorId': 'owner'}),
    ('organization.markOutcomeSeen', {'id': 'missing', 'revision': 1, 'seen': True}),
    ('organization.markOutcomeSeen', {'id': 'missing', 'revision': 1, 'status': 'completed'}),
])
def test_outcomes_rpc_rejects_untrusted_fields_and_invalid_values(method, params):
    assert rpc(method, **params)['error']['code'] == -32602


def test_owner_outcomes_routes_to_exact_profile_and_survives_service_restart(tmp_path, monkeypatch):
    from eidolon_cli import profiles
    root = tmp_path / 'profiles-root'
    root.mkdir()
    monkeypatch.setattr(profiles, '_get_default_eidolon_home', lambda: root)
    stores, ids = {}, {}
    for name in ('alpha', 'beta'):
        home = root / 'profiles' / name
        home.mkdir(parents=True)
        (home / 'config.yaml').write_text('{}\n')
        stores[name] = OrganizationStore(home / 'organization' / 'state.db')
        ids[name] = blocked(stores[name])
    alpha = rpc('organization.outcomes', profile='alpha')['result']
    assert [row['objectiveId'] for row in alpha['items']] == [ids['alpha']]
    cross = rpc('organization.markOutcomeSeen', profile='beta', id=ids['alpha'], revision=alpha['items'][0]['revision'])
    assert cross['error']['code'] == -32602
    cursor = rpc('organization.outcomes', profile='beta', before=ids['alpha'])
    assert cursor['error']['code'] == -32602
    seen = rpc('organization.markOutcomeSeen', profile='alpha', id=ids['alpha'], revision=alpha['items'][0]['revision'])['result']
    assert seen['runtime']['profile'] == 'alpha' and seen['outcomes']['unread'] == 0
    assert rpc('organization.outcomes', profile='beta')['result']['unread'] == 1
    assert services.stop_services()
    assert rpc('organization.outcomes', profile='alpha')['result']['unread'] == 0
    assert rpc('organization.outcomes', profile='beta')['result']['unread'] == 1
    assert all(not service.running for service in services._services.values())
