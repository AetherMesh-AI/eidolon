"""Real dispatch: observation is confined to the authenticated served profile."""
from pathlib import Path
from queue import Queue
from types import SimpleNamespace

import pytest

from eidolon_constants import set_eidolon_home_override, reset_eidolon_home_override
from eidolon_cli.organization_store import OrganizationStore
import tui_gateway.server as server


@pytest.fixture
def served(tmp_path, monkeypatch):
    home = tmp_path / '.eidolon'; home.mkdir()
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setattr(server, '_eidolon_home', home)
    import eidolon_cli.organization_service as services
    def forbidden(*args, **kwargs):
        raise AssertionError('Observation entered a service or profile-enumeration path')
    monkeypatch.setattr(services, 'get_service', forbidden)
    monkeypatch.setattr(services.OrganizationService, 'start', forbidden)
    monkeypatch.setattr(server, '_profile_home', forbidden)
    return home


def call(params=None, transport=None, method="organization.observe"):
    responses = Queue()
    transport = transport or SimpleNamespace(closed=False)
    transport.write = lambda response: responses.put(response)
    result = server.dispatch({'jsonrpc': '2.0', 'id': 7, 'method': method,
                              'params': {} if params is None else params}, transport=transport)
    return result if result is not None else responses.get(timeout=10)


def test_missing_read_does_not_provision_and_initialized_read_retains_ids(served):
    result = call()['result']
    assert result['scope'] == {'kind': 'served-profile', 'profile': 'default'}
    assert result['state'] == 'unconfigured'
    assert not (served / 'organization').exists()
    store = OrganizationStore(served / 'organization' / 'state.db')
    objective = store.create_objective('Visible only on this endpoint', idempotency_key='local')
    result = call()['result']
    assert result['state'] == 'available'
    assert result['snapshot']['objectives'][0]['id'] == objective['id']
    assert any(r['status'] == 'queued' for r in result['snapshot']['requests'])


def test_unbound_dead_transport_and_scope_widening_are_rejected(served):
    request = {'jsonrpc': '2.0', 'id': 1, 'method': 'organization.observe', 'params': {}}
    assert server.handle_request(request)['error']['code'] == 4001
    assert call(transport=SimpleNamespace(closed=True))['error']['code'] == 4001
    for params in ({'profile': 'sibling'}, {'profile': 'default'}, {'path': str(served)},
                   {'organizationId': 'copied-id'}, {'connectionId': 'other'}):
        assert call(params)['error']['code'] == -32602
    assert not (served / 'organization').exists()


def test_inherited_home_cannot_redirect_observation(served):
    source = OrganizationStore(served / 'organization' / 'state.db')
    own = source.create_objective('Served records', idempotency_key='source')
    sibling = served / 'profiles' / 'sibling'
    other = OrganizationStore(sibling / 'organization' / 'state.db')
    foreign = other.create_objective('Sibling records', idempotency_key='sibling')
    token = set_eidolon_home_override(sibling)
    try:
        result = call()['result']
        assert result['scope']['profile'] == 'default'
        assert {o['id'] for o in result['snapshot']['objectives']} == {own['id']}
        assert foreign['id'] not in str(result)
    finally:
        reset_eidolon_home_override(token)


def test_corruption_is_an_error_not_an_empty_organization(served):
    path = served / 'organization' / 'state.db'; path.parent.mkdir()
    path.write_bytes(b'corrupt database')
    result = call()
    assert result['error']['code'] == 5073
    assert result['error']['data']['reason'] == 'corrupt'
    assert str(served) not in str(result)


@pytest.mark.linux_only
def test_symlink_to_an_unauthorized_sibling_is_rejected(served):
    sibling = served / 'profiles' / 'sibling'; sibling.mkdir(parents=True)
    (served / 'organization').symlink_to(sibling, target_is_directory=True)
    assert call()['error']['code'] == 4001
    assert list(sibling.iterdir()) == []



def test_inherited_secret_scope_is_replaced_without_hydration(served, monkeypatch):
    from agent.secret_scope import set_secret_scope, reset_secret_scope, current_secret_scope
    from eidolon_cli.organization_config import OrganizationSettings
    import eidolon_cli.env_loader as env_loader
    (served / '.env').write_text('OBSERVATION_TEAM=served-team\n')
    (served / 'config.yaml').write_text('organization:\n  team: ${OBSERVATION_TEAM}\n')
    store = OrganizationStore(served / 'organization' / 'state.db',
                              settings=OrganizationSettings(team='served-team'))
    store.create_objective('Served policy', idempotency_key='scoped')
    def forbidden(*args, **kwargs):
        raise AssertionError('Observation hydrated an external secret source')
    monkeypatch.setattr(env_loader, 'hydrate_profile_secret_sources', forbidden)
    token = set_secret_scope({'OBSERVATION_TEAM': 'foreign-team'})
    try:
        result = call()['result']
        assert result['state'] == 'available'
        assert result['snapshot']['runtime']['state'] == 'ready'
        assert current_secret_scope() == {'OBSERVATION_TEAM': 'foreign-team'}
        assert 'foreign-team' not in str(result)
    finally:
        reset_secret_scope(token)


def test_passive_detail_reads_never_construct_service_or_mutate_policy(served, monkeypatch):
    import sqlite3
    import eidolon_cli.organization_service as services
    store = OrganizationStore(served / 'organization' / 'state.db')
    objective = store.create_objective('Needs owner review', idempotency_key='attention')
    claim = store.claim_next()
    assert store.fail(claim, 'Needs facts')
    identity = next(a['identityId'] for a in store.snapshot()['agents'] if a['id'] == 'manager')
    thread = store.owner_chat_open('manager', identity)
    (served / 'config.yaml').write_text('organization:\n  max_workers: 3\n')
    def dump():
        with sqlite3.connect(store.path) as conn:
            return list(conn.iterdump())
    before = dump()
    def forbidden(*args, **kwargs):
        raise AssertionError('Passive read acquired mutable runtime authority')
    monkeypatch.setattr(OrganizationStore, '__init__', forbidden)
    monkeypatch.setattr(OrganizationStore, '_connect', forbidden)
    monkeypatch.setattr(OrganizationStore, '_write', forbidden)
    monkeypatch.setattr(services.OrganizationService, '__init__', forbidden)
    monkeypatch.setattr(services.OrganizationService, 'refresh_configuration', forbidden)
    reads = [
        ('organization.attention', {'limit': 1, 'unreadOnly': True}),
        ('organization.outcomes', {}), ('organization.history', {'state': 'all'}),
        ('organization.historyObjective', {'id': objective['id']}),
        ('organization.ownerChat.read', {'threadId': thread, 'identityId': identity}),
        ('organization.toolReceipts', {'id': claim['id']}),
        ('organization.executionAudit', {'id': claim['id']}),
        ('organization.projectSetup', {}), ('organization.checkSubmission', {'idempotencyKey': 'attention'}),
    ]
    for method, params in reads:
        result = call({'method': method, 'params': params}, method='organization.observeRead')
        assert 'error' not in result, (method, result)
        assert result['result']['state'] == 'available'
        assert result['result']['scope'] == {'kind': 'served-profile', 'profile': 'default'}
    attention = call({'method': 'organization.attention', 'params': {}}, method='organization.observeRead')['result']['data']
    assert attention['items'][0]['requestId'] == claim['id']
    assert attention['total'] == attention['unread'] == 1
    assert dump() == before


def test_passive_detail_read_rejects_mutations_and_scope_widening(served):
    for method, params in [('organization.createObjective', {}), ('organization.attention', {'profile': 'sibling'}),
                           ('organization.ownerChat.send', {}), ('profiles.list', {})]:
        response = call({'method': method, 'params': params}, method='organization.observeRead')
        assert response['error']['code'] == -32602
    assert not (served / 'organization').exists()


@pytest.mark.parametrize('statement', ['CREATE TABLE forbidden_write (id TEXT)',
                                      'UPDATE organization_policy SET generation=generation+1'])
def test_detail_projection_sql_is_enforced_read_only(served, monkeypatch, statement):
    import sqlite3
    from eidolon_cli.organization_observation_reads import _ReadView, _READS
    store = OrganizationStore(served / 'organization' / 'state.db')
    def attempted_write(view, **params):
        with view._connect() as conn:
            conn.execute(statement)
        raise AssertionError('SQLite accepted an observation write')
    monkeypatch.setitem(_READS, 'organization.attention', (attempted_write, set()))
    with sqlite3.connect(store.path) as conn:
        before = list(conn.iterdump())
    response = call({'method': 'organization.attention', 'params': {}}, method='organization.observeRead')
    assert response['error']['code'] == 5073
    with sqlite3.connect(store.path) as conn:
        assert list(conn.iterdump()) == before


def test_passive_owner_chat_read_preserves_complete_legacy_inner_contract(served):
    store = OrganizationStore(served / 'organization' / 'state.db')
    identity = next(a['identityId'] for a in store.snapshot()['agents'] if a['id'] == 'manager')
    thread = store.owner_chat_open('manager', identity)
    expected = {**store.owner_chat_read(thread, identity), 'profile': 'default'}
    response = call({'method': 'organization.ownerChat.read',
                     'params': {'threadId': thread, 'identityId': identity}}, method='organization.observeRead')
    assert response['result']['data'] == expected
    assert response['result']['data']['profile'] == response['result']['scope']['profile']
