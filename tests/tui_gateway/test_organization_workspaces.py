"""Served RPC authority and the existing explicitly profile-scoped settings path."""
import json
import os
from pathlib import Path
from queue import Queue
import socket
from types import SimpleNamespace

import pytest

from tests.eidolon_cli.test_organization_workspaces import (
    origin, params, status_params, create, files, scope, deny_external_network,
)
from eidolon_cli import profile_isolation as isolation


@pytest.fixture
def served(origin, monkeypatch):
    deny_external_network(monkeypatch)
    import tui_gateway.server as server
    monkeypatch.setattr(server, '_eidolon_home', origin.home)
    return server, origin


def test_network_guard_permits_interpreter_socketpair_round_trips(served):
    for make_pair in (socket.socketpair, socket._fallback_socketpair):
        left, right = make_pair()
        try:
            left.settimeout(2)
            right.settimeout(2)
            left.sendall(b'local-pair')
            assert right.recv(10) == b'local-pair'
            right.sendall(b'reply')
            assert left.recv(5) == b'reply'
        finally:
            left.close()
            right.close()


def test_network_guard_denies_external_loopback_and_socketpair_impostor(served):
    with socket.socket() as listener, socket.socket() as client:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        for address in (('192.0.2.1', 443), ('api.openai.com', 443),
                        ('127.0.0.1', 443), listener.getsockname()):
            for connect in (client.connect, client.connect_ex):
                with pytest.raises(AssertionError, match='must never contact a provider'):
                    connect(address)

        def _fallback_socketpair():
            lsock, csock = listener, client
            csock.connect(lsock.getsockname())

        with pytest.raises(AssertionError, match='must never contact a provider'):
            _fallback_socketpair()


def call(server, method, params, transport=None):
    responses = Queue()
    transport = transport or SimpleNamespace(closed=False)
    transport.write = responses.put
    result = server.dispatch({'jsonrpc': '2.0', 'id': 42, 'method': 'organization.' + method,
                              'params': params}, transport=transport)
    return result if result is not None else responses.get(timeout=15)


def test_real_rpc_capability_creation_lookup_descriptor_and_no_activation(served, monkeypatch):
    server, origin = served
    before = files(origin.home)
    capability = call(server, 'workspaceCreation', {})['result']
    assert capability['origin']['organizationId'] == origin.metadata['organizationId']
    assert files(origin.home) == before
    value = params(origin)
    result = call(server, 'createWorkspace', value)['result']
    assert call(server, 'workspaceCreationStatus', status_params(value))['result'] == result
    assert call(server, 'createWorkspace', value)['result'] == result
    assert server._eidolon_home == origin.home
    assert not (origin.home / 'active_profile').exists()
    home = origin.home / 'profiles' / result['profile']
    monkeypatch.setattr(server, '_eidolon_home', home)
    descriptor = call(server, 'descriptor', {})['result']
    assert descriptor['scope']['profile'] == result['profile']
    assert descriptor['metadata']['organizationId'] == result['organizationId']
    assert descriptor['metadata']['owner']['ownerId'] == result['ownerId']
    assert descriptor['isolation']['isolationVersion'] == result['isolationVersion']
    assert descriptor['isolation']['credentialMode'] == isolation.MODE


@pytest.mark.parametrize('method', ['workspaceCreation', 'createWorkspace', 'workspaceCreationStatus'])
def test_rpc_rejects_missing_transport_foreign_scope_and_old_runtime_before_writes(served, monkeypatch, method):
    server, origin = served
    value = {} if method == 'workspaceCreation' else params(origin) if method == 'createWorkspace' else status_params(params(origin))
    before = files(origin.home)
    request = {'jsonrpc': '2.0', 'id': 42, 'method': 'organization.' + method, 'params': value}
    assert server.handle_request(request)['error']['code'] == 4001
    assert call(server, method, value, SimpleNamespace(closed=True))['error']['code'] == 4001
    assert call(server, method, {**value, 'profile': 'sibling'})['error']['code'] == -32602
    monkeypatch.delitem(server._methods, 'organization.' + method)
    assert call(server, method, value)['error']['code'] == -32601
    assert files(origin.home) == before


def test_existing_rest_keys_and_config_setup_is_exactly_profile_local(served, monkeypatch):
    from starlette.testclient import TestClient
    from eidolon_cli.web_server import app, _SESSION_HEADER_NAME, _SESSION_TOKEN
    from eidolon_cli.runtime_provider import resolve_runtime_provider
    server, origin = served
    home_a, a = create(origin)
    home_b, _ = create(origin, 'second')
    root_env = (origin.home / '.env').read_bytes()
    root_config = (origin.home / 'config.yaml').read_bytes()
    before_b = files(home_b)
    before_env = dict(os.environ)
    client = TestClient(app)
    client.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    try:
        response = client.put('/api/env', json={'profile': a['profile'],
            'key': 'OPENAI_API_KEY', 'value': 'api-owned-synthetic-key'})
        assert response.status_code == 200, response.text
        response = client.put('/api/config', json={'profile': a['profile'], 'config': {
            'model': {'provider': 'openai-api', 'default': 'workspace-test-model'}}})
        assert response.status_code == 200, response.text
        key_scope = {'profile': a['profile'], 'key': 'OPENAI_API_KEY'}
        assert client.post('/api/env/reveal', json=key_scope).json()['value'] == 'api-owned-synthetic-key'
        assert client.put('/api/env', json={**key_scope, 'value': 'rotated-owned-synthetic-key'}).status_code == 200
        assert client.post('/api/env/reveal', json=key_scope).json()['value'] == 'rotated-owned-synthetic-key'
        assert client.request('DELETE', '/api/env', json=key_scope).status_code == 200
        with scope(home_a), pytest.raises(isolation.ProfileIsolationError):
            resolve_runtime_provider(requested='openai-api')
        assert client.put('/api/env', json={**key_scope, 'value': 'api-owned-synthetic-key'}).status_code == 200
    finally:
        client.close()
    assert dict(os.environ) == before_env
    assert (origin.home / '.env').read_bytes() == root_env
    assert (origin.home / 'config.yaml').read_bytes() == root_config
    assert files(home_b) == before_b
    with scope(home_a):
        assert resolve_runtime_provider(requested='openai-api')['api_key'] == 'api-owned-synthetic-key'
    with scope(home_b), pytest.raises(isolation.ProfileIsolationError):
        resolve_runtime_provider(requested='openai-api')
    # The old unscoped key RPC must not silently save this profile's key at launch home.
    response = server.handle_request({'jsonrpc': '2.0', 'id': 42, 'method': 'model.save_key',
        'params': {'profile': a['profile'], 'slug': 'openai-api', 'api_key': 'must-not-save'}})
    assert response['error']['code'] == 4006
    assert (origin.home / '.env').read_bytes() == root_env


@pytest.mark.parametrize('filename', ['.env', 'auth.json', 'auth.lock'])
def test_rest_setup_rejects_symlink_before_foreign_access(served, monkeypatch, filename):
    from starlette.testclient import TestClient
    from eidolon_cli.web_server import app, _SESSION_HEADER_NAME, _SESSION_TOKEN
    _, origin = served
    home, created = create(origin)
    before = (home / '.env').read_bytes()
    outside = origin.home / 'foreign.env'
    outside.write_text('OPENAI_API_KEY=do-not-change\n')
    (home / filename).unlink(missing_ok=True)
    (home / filename).symlink_to(outside)
    opened = []
    original_open = Path.open
    def checked_open(path, *args, **kwargs):
        if path.resolve() == outside:
            opened.append(str(path))
            raise AssertionError('Foreign credentials must not be opened')
        return original_open(path, *args, **kwargs)
    client = TestClient(app)
    client.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    try:
        with monkeypatch.context() as patched:
            patched.setattr(Path, 'open', checked_open)
            response = client.put('/api/env', json={'profile': created['profile'],
                'key': 'OPENAI_API_KEY', 'value': 'must-not-write'})
            assert response.status_code >= 400
    finally:
        client.close()
    assert opened == []
    assert outside.read_text() == 'OPENAI_API_KEY=do-not-change\n'
    if filename != '.env':
        assert (home / '.env').read_bytes() == before
