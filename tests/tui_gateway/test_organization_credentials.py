"""Owner admission and real RPC/storage boundaries, using synthetic values only."""
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
import json

import pytest

from eidolon_cli.organization_credential_security import BackendState, CredentialError
from eidolon_cli.organization_metadata import save_metadata
from tui_gateway.organization_credential_transport import register_owner, retire_owner, secure_local_owner_home
import tui_gateway.server as server


class Transport:
    closed = False
    def __init__(self):
        self.responses = Queue()
    def write(self, response):
        self.responses.put(response)


class Backend:
    def __init__(self):
        self.values = {}
    def status(self):
        return BackendState('synthetic-test', 'available')
    def put(self, binding, value):
        self.values[binding.operation_id] = value
        return 'opaque-reference'


@pytest.fixture
def served(tmp_path, monkeypatch):
    home = tmp_path / 'profile'; home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setattr(server, '_eidolon_home', home)
    result = save_metadata(home / 'organization' / 'state.db', display_name='Synthetic organization',
        owner_display_name='Synthetic owner', expected_organization_id=None, expected_revision=None,
        idempotency_key='identity')
    metadata = result['metadata']
    base = {'expectedOrganizationId': metadata['organizationId'], 'expectedOwnerId': metadata['owner']['ownerId']}
    transport = Transport(); register_owner(transport, home)
    backend = Backend()
    monkeypatch.setattr('eidolon_cli.organization_credential_service.native_backend', lambda directory: backend)
    yield home, base, transport, backend
    retire_owner(transport)


def call(transport, method, params):
    result = server.dispatch({'jsonrpc': '2.0', 'id': 1, 'method': 'organization.credentials.' + method, 'params': params}, transport)
    return result if result is not None else transport.responses.get(timeout=10)


def mutation(base, **extra):
    return {**base, 'action': 'create', 'entryId': None, 'expectedRevision': None, 'operationId': 'create',
        'metadata': {'service': 'github', 'label': 'eidolonReleases', 'type': 'token',
                     'description': 'Synthetic release token', 'kind': 'secret', 'expiresAt': None},
        'inputHandle': None, 'confirmed': True, **extra}


def test_owner_route_catalog_mutation_secure_input_and_receipt_roundtrip(served, caplog):
    home, base, transport, backend = served
    catalog = call(transport, 'catalog', base)['result']
    assert catalog['entries'] == [] and catalog['secureInputAvailable'] is True
    assert not (home / 'organization' / 'credentials.db').exists()
    intent = mutation(base)
    created = call(transport, 'mutate', intent)['result']
    assert call(transport, 'mutate', intent)['result'] == created
    value = 'SYNTHETIC_RPC_PRIVATE_49b371'
    submitted = call(transport, 'secureInput', {**base, 'entryId': created['entryId'], 'expectedRevision': 1,
        'operationId': 'replace', 'value': value})['result']
    replaced = call(transport, 'mutate', mutation(base, action='replace_value', entryId=created['entryId'],
        expectedRevision=1, operationId='replace', metadata=None, inputHandle=submitted['inputHandle']))['result']
    assert replaced['version'] == 1
    assert call(transport, 'receipt', {**base, 'operationId': 'replace'})['result']['receipt'] == replaced
    final = call(transport, 'catalog', base)['result']
    assert final['entries'][0]['status'] == 'available'
    assert backend.values == {'replace': value.encode()}
    for exposed in (json.dumps(final), json.dumps(replaced), caplog.text, (home / 'organization' / 'credentials.db').read_bytes().decode('latin1')):
        assert value not in exposed
    assert 'opaque-reference' not in json.dumps(final)


def test_client_claims_foreign_owner_insecure_routes_and_raw_values_are_denied(served):
    home, base, transport, backend = served
    untrusted = Transport()
    untrusted.auth_identity = {'owner': True, 'organizationId': base['expectedOrganizationId']}
    assert call(untrusted, 'catalog', base)['error']['code'] == 4001
    untrusted.credential_owner_home = home
    assert call(untrusted, 'catalog', base)['error']['code'] == 4001
    assert call(transport, 'catalog', {**base, 'expectedOwnerId': 'foreign'})['error']['data']['reason'] == 'identity_changed'
    assert call(transport, 'catalog', {**base, 'profile': 'other'})['error']['code'] == -32602
    assert call(transport, 'mutate', {**mutation(base), 'value': 'SYNTHETIC'})['error']['code'] == -32602
    assert call(transport, 'mutate', mutation(base, confirmed=False))['error']['data']['reason'] == 'confirmation_required'
    assert not (home / 'organization' / 'credentials.db').exists()
    transport.closed = True
    assert call(transport, 'catalog', base)['error']['code'] == 4001


def test_secure_handles_cannot_cross_transports_and_disconnect_retires_them(served):
    home, base, transport, backend = served
    entry = call(transport, 'mutate', mutation(base))['result']
    params = {**base, 'entryId': entry['entryId'], 'expectedRevision': 1, 'operationId': 'replace', 'value': 'synthetic'}
    handle = call(transport, 'secureInput', params)['result']['inputHandle']
    other = Transport(); register_owner(other, home)
    intent = mutation(base, action='replace_value', entryId=entry['entryId'], expectedRevision=1,
                      operationId='replace', metadata=None, inputHandle=handle)
    assert call(other, 'mutate', intent)['error']['data']['reason'] == 'secure_input_invalid'
    retire_owner(transport)
    register_owner(transport, home)
    assert call(transport, 'mutate', intent)['error']['data']['reason'] == 'secure_input_invalid'
    assert not backend.values
    retire_owner(other)


def test_transport_capability_requires_verified_loopback_binding_and_peer(served):
    home, base, transport, backend = served
    app = SimpleNamespace(state=SimpleNamespace(auth_required=False, bound_host='127.0.0.1'))
    ws = SimpleNamespace(client=SimpleNamespace(host='127.0.0.1'), scope={'server': ('127.0.0.1', 9000)}, headers={})
    assert secure_local_owner_home(ws, app) == home
    for state in (SimpleNamespace(bound_host='127.0.0.1'),
                  SimpleNamespace(auth_required=None, bound_host='127.0.0.1'),
                  SimpleNamespace(auth_required='false', bound_host='127.0.0.1'),
                  SimpleNamespace(auth_required=True, bound_host='127.0.0.1'),
                  SimpleNamespace(auth_required=False, bound_host='0.0.0.0'),
                  SimpleNamespace(auth_required=False)):
        assert secure_local_owner_home(ws, SimpleNamespace(state=state)) is None
    for changed in ({'client': SimpleNamespace(host='10.0.0.1')}, {'scope': {}},
                    {'headers': {'x-forwarded-for': '127.0.0.1'}}):
        assert secure_local_owner_home(SimpleNamespace(**{**vars(ws), **changed}), app) is None


def test_oversized_input_and_backend_exception_never_echo_values(served, caplog):
    home, base, transport, backend = served
    entry = call(transport, 'mutate', mutation(base))['result']
    value = 'SYNTHETIC_DO_NOT_ECHO' * 4000
    error = call(transport, 'secureInput', {**base, 'entryId': entry['entryId'], 'expectedRevision': 1,
                 'operationId': 'oversized', 'value': value})
    assert error['error']['data']['reason'] == 'invalid_secure_input'
    assert value[:30] not in repr(error) + caplog.text
    submitted = call(transport, 'secureInput', {**base, 'entryId': entry['entryId'], 'expectedRevision': 1,
                 'operationId': 'exception', 'value': 'SYNTHETIC_EXCEPTION'})['result']
    def fail(binding, secret):
        raise RuntimeError(secret.decode())
    backend.put = fail
    response = call(transport, 'mutate', mutation(base, action='replace_value', entryId=entry['entryId'],
        expectedRevision=1, operationId='exception', metadata=None, inputHandle=submitted['inputHandle']))
    assert 'SYNTHETIC_EXCEPTION' not in repr(response) + caplog.text
    assert call(transport, 'receipt', {**base, 'operationId': 'exception'})['result']['receipt'] is None


def test_owner_grants_require_active_identity_exact_scope_and_explicit_review(served):
    from eidolon_cli.organization_config import OrganizationSettings
    from eidolon_cli.organization_store import OrganizationStore
    home, base, transport, backend = served
    OrganizationStore(home / 'organization' / 'state.db', OrganizationSettings())
    entry = call(transport, 'mutate', mutation(base))['result']
    details = call(transport, 'catalog', base)['result']
    agent = next(item for item in details['agents'] if item['id'] == 'worker-1')
    intent = {**base, 'actorId': agent['identityId'], 'scope': entry['entryId'],
        'permissions': ['discover', 'edit_metadata'], 'bindings': [], 'expectedRevision': 0,
        'operationId': 'grant', 'confirmed': True}
    for override, reason in (({'actorId': agent['id']}, 'agent_unavailable'),
                             ({'confirmed': False}, 'confirmation_required'),
                             ({'permissions': ['use'], 'bindings': [{'tool': 'ssh.exec', 'destination': 'host'}]}, 'adapter_unavailable')):
        assert call(transport, 'configureGrant', {**intent, **override})['error']['data']['reason'] == reason
    granted = call(transport, 'configureGrant', intent)['result']
    assert granted['revision'] == 1
    assert call(transport, 'configureGrant', intent)['result'] == granted
    assert call(transport, 'configureGrant', {**intent, 'operationId': 'stale'})['error']['data']['reason'] == 'revision_conflict'
    assert call(transport, 'catalog', base)['result']['grants'][0]['actor'] == agent['identityId']
    revoked = call(transport, 'configureGrant', {**intent, 'permissions': [], 'operationId': 'withdraw', 'expectedRevision': 1})['result']
    assert revoked['revision'] == 2
    created_scope = {**intent, 'scope': 'create:ssh:dl380:password:secret', 'permissions': ['create'], 'operationId': 'create-grant'}
    assert call(transport, 'configureGrant', created_scope)['result']['revision'] == 1
    assert call(transport, 'configureGrant', {**created_scope, 'scope': 'create:*', 'operationId': 'wildcard'})['error']['data']['reason'] == 'invalid_scope'


def test_owner_cancel_operation_fences_missing_receipt_without_replaying(served):
    home, base, transport, backend = served
    params = {**base, 'operationId': 'create', 'confirmed': True}
    assert call(transport, 'cancelOperation', {**params, 'confirmed': False})['error']['data']['reason'] == 'confirmation_required'
    assert call(transport, 'cancelOperation', params)['result'] == {'operationId': 'create', 'state': 'cancelled', 'receipt': None}
    assert call(transport, 'mutate', mutation(base))['error']['data']['reason'] == 'operation_cancelled'
    assert call(transport, 'catalog', base)['result']['entries'] == []
