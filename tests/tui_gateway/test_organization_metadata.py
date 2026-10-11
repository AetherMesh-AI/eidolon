"""Real RPC dispatch is confined to its authenticated served profile, without services."""
from contextlib import closing
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from queue import Queue

import pytest

from eidolon_constants import set_eidolon_home_override, reset_eidolon_home_override
from eidolon_cli import organization_metadata as metadata
from eidolon_cli import organization_service as services
from eidolon_cli.organization_store import OrganizationStore
import tui_gateway.server as server


@pytest.fixture
def served(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    home = tmp_path / '.eidolon'; home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setattr(server, '_eidolon_home', home)
    def forbidden(*args, **kwargs):
        raise AssertionError('Metadata constructed or started execution state')
    monkeypatch.setattr(services, 'get_service', forbidden)
    monkeypatch.setattr(services.OrganizationService, 'start', forbidden)
    monkeypatch.setattr(OrganizationStore, '__init__', forbidden)
    monkeypatch.setattr(server, '_profile_home', forbidden)
    return home


def call(name='organization.descriptor', params=None, transport=None):
    request = {'jsonrpc': '2.0', 'id': 11, 'method': name, 'params': params or {}}
    responses = Queue()
    transport = transport or SimpleNamespace(closed=False)
    transport.write = lambda response: responses.put(response)
    result = server.dispatch(request, transport=transport)
    return result if result is not None else responses.get(timeout=10)


def intent():
    return {'displayName': 'Independent organization', 'ownerDisplayName': 'Alex',
            'expectedOrganizationId': None, 'expectedRevision': None, 'idempotencyKey': 'save-once'}


def test_rpc_descriptor_save_retry_and_restart_never_construct_execution_state(served):
    descriptor = call()['result']
    assert descriptor['scope'] == {'kind': 'served-profile', 'profile': 'default'}
    assert descriptor['state'] == 'unconfigured'
    assert not (served / 'organization').exists()
    original = call('organization.saveMetadata', intent())['result']
    assert original['metadata']['owner']['displayName'] == 'Alex'
    assert call()['result'] == original
    assert call('organization.saveMetadata', intent())['result'] == original
    renamed = {**intent(), 'displayName': 'Renamed', 'idempotencyKey': 'rename',
               'expectedOrganizationId': original['metadata']['organizationId'], 'expectedRevision': 1}
    current = call('organization.saveMetadata', renamed)['result']
    assert current['metadata']['revision'] == 2
    assert call('organization.saveMetadata', intent())['result'] == original
    assert metadata.read_descriptor(served / 'organization' / 'state.db')['metadata'] == current['metadata']
    assert not (served / 'state.db').exists()  # No human chat store provisioned.


def test_rpc_rejects_unbound_dead_and_scope_widening_before_any_profile_read(served):
    request = {'jsonrpc': '2.0', 'id': 1, 'method': 'organization.descriptor', 'params': {}}
    assert server.handle_request(request)['error']['code'] == 4001
    assert call(transport=SimpleNamespace(closed=True))['error']['code'] == 4001
    for extra in ({'profile': 'sibling'}, {'profile': 'default'}, {'path': str(served)},
                  {'organizationId': 'another-uuid'}, {'connectionId': 'other'}, {'ownerId': 'owner'}):
        assert call(params=extra)['error']['code'] == -32602
        assert call('organization.saveMetadata', {**intent(), **extra})['error']['code'] == -32602
    assert not (served / 'organization').exists()


def test_inherited_context_cannot_redirect_descriptor_or_save_and_copied_ids_are_route_qualified(served, monkeypatch):
    first = call('organization.saveMetadata', intent())['result']
    sibling = served / 'profiles' / 'sibling'; sibling.mkdir(parents=True)
    path = sibling / 'organization' / 'state.db'; path.parent.mkdir()
    with closing(sqlite3.connect(served / 'organization' / 'state.db')) as source, closing(sqlite3.connect(path)) as dest:
        source.backup(dest)
    token = set_eidolon_home_override(sibling)
    try:
        assert call()['result'] == first
        assert call('organization.saveMetadata', {**intent(), 'idempotencyKey': 'active-only',
                    'expectedOrganizationId': first['metadata']['organizationId'], 'expectedRevision': 1,
                    'displayName': 'Only served route'})['result']['scope'] == first['scope']
    finally:
        reset_eidolon_home_override(token)
    assert metadata.read_descriptor(path)['metadata'] == first['metadata']
    # A genuinely different served endpoint may expose the copied ID, with its own scope.
    monkeypatch.setattr(server, '_eidolon_home', sibling)
    other = call()['result']
    assert other['metadata']['organizationId'] == first['metadata']['organizationId']
    assert other['scope'] == {'kind': 'served-profile', 'profile': 'sibling'}
    assert other['scope'] != first['scope']


def test_rpc_exposes_conflicts_and_storage_failures_without_leaking_paths(served):
    call('organization.saveMetadata', intent())
    conflict = call('organization.saveMetadata', {**intent(), 'ownerDisplayName': 'Different'})
    assert conflict['error']['data']['reason'] == 'idempotency_conflict'
    path = served / 'organization' / 'state.db'
    path.write_bytes(b'corrupt')
    response = call()
    assert response['error']['data']['reason'] == 'corrupt'
    assert str(served) not in str(response)


@pytest.mark.linux_only
def test_rpc_rejects_symlink_to_unauthorized_sibling_ledger(served, tmp_path):
    outside = served / 'profiles' / 'sibling'; outside.mkdir(parents=True)
    (served / 'organization').symlink_to(outside, target_is_directory=True)
    assert call()['error']['code'] == 4001
    assert call('organization.saveMetadata', intent())['error']['code'] == 4001
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize('damage', ['json', 'root', 'missing', 'version', 'owner', 'revision', 'timestamp', 'identity'])
def test_corrupt_receipt_is_storage_error_without_repair_or_cas_fallback(served, damage):
    import copy
    import json
    original = call('organization.saveMetadata', intent())['result']
    path = served / 'organization' / 'state.db'
    # The original receipt must still be decoded before an otherwise stale CAS.
    call('organization.saveMetadata', {**intent(), 'idempotencyKey': 'later',
         'expectedOrganizationId': original['metadata']['organizationId'], 'expectedRevision': 1,
         'displayName': 'Later revision'})
    result = copy.deepcopy({key: value for key, value in original.items() if key != 'scope'})
    mutations = {
        'json': lambda: '{not JSON',
        'root': lambda: json.dumps([]),
        'missing': lambda: json.dumps({'capabilityVersion': 1, 'state': 'ready'}),
        'version': lambda: json.dumps({**result, 'capabilityVersion': True}),
        'owner': lambda: json.dumps({**result, 'metadata': {**result['metadata'], 'owner': None}}),
        'revision': lambda: json.dumps({**result, 'metadata': {**result['metadata'], 'revision': False}}),
        'timestamp': lambda: json.dumps({**result, 'metadata': {**result['metadata'], 'createdAt': 'yesterday'}}),
        'identity': lambda: json.dumps({**result, 'metadata': {**result['metadata'],
                    'organizationId': result['metadata']['owner']['ownerId']}}),
    }
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('UPDATE organization_metadata_receipts SET result_json=? WHERE idempotency_key=?',
                     (mutations[damage](), intent()['idempotencyKey']))
        conn.commit()
        before = tuple(conn.iterdump())
    response = call('organization.saveMetadata', intent())
    assert response['error']['code'] == 5072
    assert response['error']['data'] == {'reason': 'corrupt'}
    assert str(served) not in str(response)
    with closing(sqlite3.connect(path)) as conn:
        assert tuple(conn.iterdump()) == before
