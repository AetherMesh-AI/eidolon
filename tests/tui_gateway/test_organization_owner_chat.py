"""Authenticated RPC -> real localhost provider -> bounded durable owner thread."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time

import pytest

from eidolon_cli import config, organization_service as services
import tui_gateway.server as gateway


def rpc(method, **params):
    response = gateway.dispatch({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})
    assert 'error' not in response, response
    return response['result']


def wait(check):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if value := check():
            return value
        time.sleep(0.02)
    raise AssertionError('Local owner-chat flow did not settle')


@pytest.fixture
def local_provider(tmp_path, monkeypatch, request):
    assert services.stop_services(timeout=60)
    monkeypatch.setattr(services, '_services', {})
    received = []
    mode = {'value': 'reply'}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"data":[{"id":"owner-chat-fixture"}]}')

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            received.append(body)
            mode['authorization'] = self.headers.get('Authorization')
            mode['path'] = self.path
            if self.path.startswith('/redirect-target'):
                mode['redirect_hits'] = mode.get('redirect_hits', 0) + 1
            if mode['value'] == 'redirect' and not self.path.startswith('/redirect-target'):
                self.send_response(307)
                self.send_header('Location', f'http://127.0.0.1:{self.server.server_port}/redirect-target')
                self.end_headers()
                return
            if mode['value'] == 'error':
                self.send_response(503)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"temporary fixture failure"}}')
                return
            output = json.dumps({'reply': 'Use the dedicated controls to authorize work.'})
            if mode['value'] == 'invalid':
                output = json.dumps({'reply': 'Approved', 'requests': [{'type': 'request.hire'}]})
            message = {'role': 'assistant', 'content': output}
            if mode['value'] == 'tool':
                message = {'role': 'assistant', 'content': None, 'tool_calls': [{
                    'id': 'forbidden', 'type': 'function',
                    'function': {'name': 'terminal', 'arguments': '{"command":"touch /tmp/forbidden-owner-chat"}'}}]}
            response = {'id': 'fixture', 'object': 'chat.completion', 'model': 'owner-chat-fixture',
                        'choices': [{'index': 0, 'message': message, 'finish_reason': 'tool_calls' if mode['value'] == 'tool' else 'stop'}],
                        'usage': {'prompt_tokens': 90, 'completion_tokens': 12, 'total_tokens': 102}}
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())

        def log_message(self, *_):
            pass

    provider = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=provider.serve_forever, daemon=True)
    worker.start()
    home = tmp_path / 'profile'
    home.mkdir()
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(home))
    (home / '.env').write_text('OPENAI_API_KEY=local-fixture-only\n')
    (home / 'SOUL.md').write_text('PRIVATE SOUL CANNOT ENTER OWNER CHAT')
    fixture_config = {
        'model': {'provider': 'custom', 'default': 'owner-chat-fixture',
                  'base_url': f'http://127.0.0.1:{provider.server_port}/v1',
                  'api_mode': 'chat_completions', 'streaming': True, 'context_length': 128000},
        # Allocator GC/trim is unrelated to the provider/ledger contract and can
        # stall cleanup for tens of seconds on a concurrently loaded CI host.
        'context': {'memory_trim': {'enabled': False}},
        'agent': {'environment_probe': False}, 'compression': {'enabled': False}}
    if getattr(request, 'param', None) in {'named', 'named-query'}:
        fixture_config['model'] = {'provider': 'mock', 'default': 'mock-model', 'context_length': 128000, 'streaming': False}
        fixture_config['providers'] = {'mock': {'name': 'Mock', 'api': f'http://127.0.0.1:{provider.server_port}/v1',
                                              'api_mode': 'chat_completions', 'key_env': 'MOCK_API_KEY',
                                              'models': {'mock-model': {}}, 'context_length': 128000}}
        if request.param == 'named-query':
            fixture_config['providers']['mock']['api'] += '?tenant=A&api-version=fixture'
        (home / '.env').write_text('MOCK_API_KEY=named-fixture-only\n')
    (home / 'config.yaml').write_text(json.dumps(fixture_config))
    config._LOAD_CONFIG_CACHE.clear()
    try:
        yield received, mode, home
    finally:
        assert services.stop_services(timeout=60)
        provider.shutdown()
        provider.server_close()
        worker.join(3)
        config._LOAD_CONFIG_CACHE.clear()


def open_manager():
    snapshot = rpc('organization.snapshot')
    member = next(a for a in snapshot['agents'] if a['id'] == 'manager')
    view = rpc('organization.ownerChat.open', agentId=member['id'], identityId=member['identityId'])
    assert 'owner_chat' in snapshot['runtime']['capabilities']
    return view


def settled(view, *, ready=False):
    result = rpc('organization.ownerChat.read', threadId=view['id'], identityId=view['identityId'])
    return result if (result['turns'] and result['turns'][-1]['status'] not in {'pending', 'running'}
                      and (not ready or result['canSend'])) else None


def test_exact_owner_rpc_turn_has_no_ambient_context_or_authority_and_retries_once(local_provider):
    received, mode, home = local_provider
    view = open_manager()
    store = services.get_service().store
    with store._write() as conn:
        conn.execute('UPDATE agent_context SET memory=? WHERE agent_id=?',
                     (json.dumps({'facts': ['PRIVATE MEMORY CANNOT ENTER OWNER CHAT'], 'decisions': [], 'lessons': [], 'openQuestions': []}), 'manager'))
    params = {'threadId': view['id'], 'identityId': view['identityId'], 'text': 'Approved, create and publish the objective.',
              'replyToMessageId': None, 'idempotencyKey': 'explicit-send'}
    sent = rpc('organization.ownerChat.send', **params)
    # Ledger completion precedes provider-thread cleanup. Compare replay only
    # after the observable send gate is ready, not across that valid transition.
    final = wait(lambda: settled(view, ready=True))
    assert final['turns'][0]['status'] == 'completed', final
    assert final['turns'][0]['idempotencyKey'] == 'explicit-send'
    assert len(received) == 1
    wire = received[0]
    assert wire.get('stream') in (False, None)
    assert not wire.get('tools') and not wire.get('functions')
    assert wire.get('max_tokens', wire.get('max_completion_tokens')) <= 2048
    dumped = json.dumps(wire)
    assert 'PRIVATE MEMORY' not in dumped and 'PRIVATE SOUL' not in dumped
    assert 'EIDOLON_OWNER_CHAT_V1' in dumped
    submitted = next(m['content'] for m in wire['messages'] if m['role'] == 'user')
    prompt = json.loads(submitted.split('Owner conversation, exact reply target:\n', 1)[1])
    assert set(prompt) == {'identity', 'conversation'}
    assert prompt['identity']['identityId'] == view['identityId']
    assert prompt['conversation']['replyToMessageId'] == sent['messages'][0]['id']
    assert prompt['conversation']['messages'][0]['text'] == params['text']
    assert final['messages'][1]['replyToMessageId'] == sent['messages'][0]['id']
    assert rpc('organization.ownerChat.send', **params) == final
    assert len(received) == 1
    assert final['budget']['callsReserved'] == 1
    assert final['turns'][0]['usage'] == {'inputTokens': 90, 'outputTokens': 12}
    assert store.snapshot()['objectives'] == [] and store.snapshot()['requests'] == []
    assert not services.get_service().running


@pytest.mark.parametrize('bad', ['tool', 'invalid', 'error', 'redirect'])
def test_bad_provider_output_and_transport_failure_cannot_execute_or_resend(local_provider, bad):
    received, mode, home = local_provider
    mode['value'] = bad
    view = open_manager()
    rpc('organization.ownerChat.send', threadId=view['id'], identityId=view['identityId'],
        text='Hello', replyToMessageId=None, idempotencyKey='one-physical-call')
    final = wait(lambda: settled(view))
    assert final['turns'][0]['status'] == 'uncertain', final
    assert len(final['messages']) == 1
    assert len(received) == 1
    assert mode.get('redirect_hits', 0) == 0
    assert not (Path('/tmp') / 'forbidden-owner-chat').exists()
    assert services.get_service().store.snapshot()['requests'] == []


def test_rpc_rejects_unbound_foreign_identity_profiles_and_unexpected_fields(local_provider):
    view = open_manager()
    request = {'jsonrpc': '2.0', 'id': 1, 'method': 'organization.ownerChat.read',
               'params': {'threadId': view['id'], 'identityId': view['identityId']}}
    assert gateway.handle_request(request)['error']['code'] == 4001
    invalid = [
        {**request['params'], 'identityId': 'other'},
        {**request['params'], 'profile': '../other'},
        {**request['params'], 'path': '/tmp/not-owner-chat'},
        {**request['params'], 'threadId': view['id'] + ' '},
    ]
    for params in invalid:
        assert gateway.dispatch({**request, 'params': params})['error']['code'] == -32602
    response = gateway.dispatch({**request, 'method': 'organization.ownerChat.send',
                                 'params': {**request['params'], 'text': 'Hello', 'idempotencyKey': 'missing-target'}})
    assert response['error']['code'] == -32602


def test_known_unsupported_route_is_readonly_blocked_without_budget_or_credentials(local_provider, monkeypatch):
    received, mode, home = local_provider
    import eidolon_cli.runtime_provider as provider
    monkeypatch.setattr(provider, 'resolve_runtime_provider', lambda **kwargs: pytest.fail('Readiness must not resolve credentials'))
    original = json.loads((home / 'config.yaml').read_text())
    cases = [
        {'provider': 'openai-codex', 'openai_runtime': 'codex_app_server', 'default': 'fixture'},
        {'provider': 'openai', 'openai_runtime': 'codex_app_server', 'default': 'fixture'},
        {'provider': 'openai-api', 'default': 'gpt-4.1'},
        {'provider': 'xai', 'default': 'fixture'},
        {'provider': 'custom', 'base_url': 'https://api.anthropic.com', 'default': 'fixture'},
    ]
    for selected in cases:
        (home / 'config.yaml').write_text(json.dumps({**original, 'model': selected}))
        config._LOAD_CONFIG_CACHE.clear()
        view = open_manager()
        assert not view['canSend'] and view['unavailableReason']
        response = gateway.dispatch({'jsonrpc': '2.0', 'id': 1, 'method': 'organization.ownerChat.send',
                                     'params': {'threadId': view['id'], 'identityId': view['identityId'], 'text': 'Hello',
                                                'replyToMessageId': None, 'idempotencyKey': 'blocked'}})
        assert response['error']['code'] == -32602
        assert view['budget']['callsReserved'] == 0 and view['turns'] == []
    assert received == []


def test_named_profile_threads_cannot_cross_route_identical_agent_names(tmp_path, monkeypatch):
    from eidolon_cli import profiles
    from eidolon_cli import organization_owner_chat_service as chat
    from agent.secret_scope import get_secret
    from eidolon_constants import get_eidolon_home
    assert services.stop_services(timeout=60)
    monkeypatch.setattr(services, '_services', {})
    root = tmp_path / 'profiles-root'
    root.mkdir()
    monkeypatch.setattr(profiles, '_get_default_eidolon_home', lambda: root)
    seen = []
    for name in ('alpha', 'beta'):
        home = root / 'profiles' / name
        home.mkdir(parents=True)
        (home / 'config.yaml').write_text('model:\n  provider: custom\n  api_mode: chat_completions\n  default: fixture\n')
        (home / '.env').write_text(f'OPENAI_API_KEY={name}-fixture-secret\n')

    def execute(context, cancel):
        seen.append((get_eidolon_home().name, get_secret('OPENAI_API_KEY'), context['identity']['identityId']))
        context['reserveModelCall'](provider='custom', model='fixture', input_limit=1000, output_limit=100)
        return {'reply': 'Profile-isolated response'}

    monkeypatch.setattr(chat, '_execute', execute)
    try:
        views = {}
        for name in ('alpha', 'beta'):
            snapshot = rpc('organization.snapshot', profile=name)
            member = next(a for a in snapshot['agents'] if a['id'] == 'manager')
            views[name] = rpc('organization.ownerChat.open', agentId='manager', identityId=member['identityId'], profile=name)
            assert views[name]['profile'] == name
        assert views['alpha']['identityId'] != views['beta']['identityId']
        a = views['alpha']
        response = gateway.dispatch({'jsonrpc': '2.0', 'id': 1, 'method': 'organization.ownerChat.read',
                                     'params': {'profile': 'beta', 'threadId': a['id'], 'identityId': a['identityId']}})
        assert response['error']['code'] == -32602
        for name, view in views.items():
            rpc('organization.ownerChat.send', profile=name, threadId=view['id'], identityId=view['identityId'],
                text='Hi', replyToMessageId=None, idempotencyKey='same-key-profile-local')
        wait(lambda: len(seen) == 2)
        assert sorted(seen) == [(name, f'{name}-fixture-secret', views[name]['identityId']) for name in ('alpha', 'beta')]
    finally:
        assert services.stop_services(timeout=60)


@pytest.mark.parametrize('local_provider', ['named'], indirect=True)
@pytest.mark.parametrize('explicit_provider', [None, 'mock', 'custom:mock'], ids=['inherited', 'member-alias', 'qualified-member-alias'])
def test_native_named_provider_keeps_selected_identity_separate_from_adapter(local_provider, explicit_provider):
    from dataclasses import replace
    from eidolon_cli.organization_roster import OrganizationStaff
    received, mode, home = local_provider
    if explicit_provider:
        service = services.get_service()
        member = OrganizationStaff('manager', 'Manager', 'general', ('request.plan', 'request.integrate'),
                                   role='Manager', manager_id='executive', provider=explicit_provider, model='mock-model')
        service.reload_configuration(replace(service.settings, roster=(member,)))
    view = open_manager()
    assert view['canSend']
    rpc('organization.ownerChat.send', threadId=view['id'], identityId=view['identityId'],
        text='Hello', replyToMessageId=None, idempotencyKey='named-provider')
    final = wait(lambda: settled(view))
    assert final['turns'][0]['status'] == 'completed', final
    assert final['turns'][0]['selectedProvider'] == (explicit_provider or 'mock')
    assert final['turns'][0]['provider'] == 'custom'
    assert final['turns'][0]['model'] == 'mock-model'
    assert len(received) == 1 and mode['authorization'] == 'Bearer named-fixture-only'
    assert 'base_url' not in json.dumps(final) and 'named-fixture-only' not in json.dumps(final)
    assert services.get_service().store.snapshot()['objectives'] == []


@pytest.mark.parametrize('local_provider', ['named'], indirect=True)
@pytest.mark.parametrize('changed', ['agent-url', 'client-url', 'adapter'])
def test_actual_owner_send_rejects_route_rewrites_after_resolution(local_provider, monkeypatch, changed):
    from eidolon_cli import organization_owner_chat_executor as executor
    received, _, _ = local_provider
    original = executor._create_agent
    request_clients = []

    def create(*args, **kwargs):
        agent = original(*args, **kwargs)
        if changed == 'agent-url':
            agent.base_url = 'http://127.0.0.1:9/altered'
        elif changed == 'client-url':
            agent._client_kwargs['base_url'] = 'http://127.0.0.1:9/altered'
            make_client = agent._create_openai_client

            def capture(*args, **kwargs):
                client = make_client(*args, **kwargs)
                request_clients.append(client)
                return client

            agent._create_openai_client = capture
        else:
            agent.provider = 'openrouter'
        return agent

    monkeypatch.setattr(executor, '_create_agent', create)
    view = open_manager()
    rpc('organization.ownerChat.send', threadId=view['id'], identityId=view['identityId'],
        text='Hello', replyToMessageId=None, idempotencyKey='changed-route')
    final = wait(lambda: settled(view))
    assert final['turns'][0]['status'] in {'blocked', 'uncertain'}
    assert 'endpoint' in final['turns'][0]['reason'] or 'route changed' in final['turns'][0]['reason']
    assert len(final['messages']) == 1 and received == []
    if changed == 'client-url':
        assert request_clients and all(client.is_closed() for client in request_clients)


@pytest.mark.parametrize('local_provider', ['named-query'], indirect=True)
@pytest.mark.parametrize('changed', [None, 'defaults', 'client', 'extra-query'])
def test_named_query_route_preserves_effective_tenant_and_rejects_rewrites(local_provider, monkeypatch, changed):
    from eidolon_cli import organization_owner_chat_executor as executor
    received, mode, _ = local_provider
    original = executor._create_agent

    def create(*args, **kwargs):
        agent = original(*args, **kwargs)
        if changed == 'defaults':
            agent._client_kwargs['default_query']['tenant'] = 'B'
        elif changed == 'client':
            make_client = agent._create_openai_client

            def replace_query(options, **kwargs):
                return make_client({**options, 'default_query': {'tenant': 'B', 'api-version': 'fixture'}}, **kwargs)

            agent._create_openai_client = replace_query
        elif changed == 'extra-query':
            send = agent._interruptible_api_call

            def override_query(options):
                return send({**options, 'extra_query': {'tenant': 'B'}})

            agent._interruptible_api_call = override_query
        return agent

    monkeypatch.setattr(executor, '_create_agent', create)
    view = open_manager()
    rpc('organization.ownerChat.send', threadId=view['id'], identityId=view['identityId'],
        text='Hello', replyToMessageId=None, idempotencyKey='query-route')
    final = wait(lambda: settled(view))
    if changed is None:
        assert final['turns'][0]['status'] == 'completed', final
        assert mode['path'] == '/v1/chat/completions?tenant=A&api-version=fixture'
        assert len(received) == 1
    else:
        assert final['turns'][0]['status'] in {'blocked', 'uncertain'}
        assert len(final['messages']) == 1 and received == []
        assert 'tenant=A' not in final['turns'][0]['reason']


@pytest.mark.parametrize('local_provider', ['named'], indirect=True)
def test_owner_request_rejects_sdk_fallback_that_follows_redirects(local_provider, monkeypatch):
    from eidolon_cli import organization_owner_chat_executor as executor
    received, _, _ = local_provider
    original = executor._create_agent
    clients = []

    def create(*args, **kwargs):
        agent = original(*args, **kwargs)
        make_client = agent._create_openai_client

        def sdk_fallback(options, **kwargs):
            client = make_client(options, **kwargs)
            client._client.follow_redirects = True
            clients.append(client)
            return client

        agent._create_openai_client = sdk_fallback
        return agent

    monkeypatch.setattr(executor, '_create_agent', create)
    view = open_manager()
    rpc('organization.ownerChat.send', threadId=view['id'], identityId=view['identityId'],
        text='Hello', replyToMessageId=None, idempotencyKey='no-redirect-fallback')
    final = wait(lambda: settled(view))
    assert final['turns'][0]['status'] == 'uncertain'
    assert 'redirects are disabled' in final['turns'][0]['reason']
    assert received == [] and clients and all(client.is_closed() for client in clients)


def test_renewal_rpc_is_idempotent_and_never_dispatches_provider(local_provider):
    received, _, _ = local_provider
    view = open_manager()
    service = services.get_service()
    turn, _ = service.store.owner_chat_reserve(view['id'], view['identityId'], 'Cancelled before dispatch', None, 'reserve-only')
    service.store.owner_chat_finish(turn, status='cancelled')
    params = {'threadId': view['id'], 'identityId': view['identityId'], 'idempotencyKey': 'renew-once',
              'expectedBudgetVersion': view['budget']['version'], 'expectedPolicyGeneration': view['policyGeneration'], 'additionalCalls': 1}
    renewed = rpc('organization.ownerChat.renew', **params)
    duplicate = rpc('organization.ownerChat.renew', **params)
    assert renewed['renewalReceipt'] == duplicate['renewalReceipt']
    assert duplicate['budget']['remainingCalls'] == view['budget']['remainingCalls']
    assert duplicate['budget']['callsReserved'] == 1 and received == []
    history = rpc('organization.ownerChat.read', threadId=view['id'], identityId=view['identityId'], limit=1)
    assert history['latestMessageId'] == renewed['latestMessageId']
    rejected = rpc('organization.ownerChat.renew', **{**params, 'idempotencyKey': 'stale-renewal'})
    assert rejected['renewalRejected'] is True and 'changed' in rejected['reason']
    assert rpc('organization.ownerChat.read', threadId=view['id'], identityId=view['identityId'])['budget'] == renewed['budget']
    malformed = gateway.dispatch({'jsonrpc': '2.0', 'id': 1, 'method': 'organization.ownerChat.renew',
                                  'params': {**params, 'automatic': True}})
    assert 'error' in malformed and received == []
