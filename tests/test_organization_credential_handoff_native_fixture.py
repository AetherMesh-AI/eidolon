"""Synthetic fixture setup and dispatch boundary; no native APIs or live provider."""
import json
import sys
import uuid

import pytest

from scripts.ci import organization_credential_handoff_native_fixture as fixture


def seeded(tmp_path):
    rendezvous = str(uuid.uuid4())
    (tmp_path / '.credential-handoff-rendezvous').write_text(rendezvous)
    (tmp_path / 'config.yaml').write_text('{}')
    return fixture.seed(tmp_path), rendezvous


def test_one_exact_synthetic_request_and_grant_with_fail_closed_dispatch(tmp_path):
    data, rendezvous = seeded(tmp_path)
    observed = fixture.inspect(tmp_path)
    assert observed['modelCalls'] == observed['request']['execution_count'] == 0
    assert observed['request']['status'] == 'queued'
    assert observed['grants'][0]['actor'] == data['identityId']
    assert observed['grants'][0]['permissions'] == ['replace_value']
    assert observed['entries'][0]['version'] == 0 and observed['nativeStorageExercised'] is False
    assert not fixture.dispatch_allowed(tmp_path, data, rendezvous)
    (tmp_path / '.credential-handoff-start').write_text('wrong-rendezvous')
    assert not fixture.dispatch_allowed(tmp_path, data, rendezvous)
    (tmp_path / '.credential-handoff-start').write_text(rendezvous)
    assert fixture.dispatch_allowed(tmp_path, data, rendezvous)
    assert not fixture.dispatch_allowed(tmp_path, {**data, 'requestId': 'wrong-task'}, rendezvous)
    assert 'SYNTHETIC_' not in json.dumps(observed)
    configuration = (tmp_path / 'config.yaml').read_bytes()
    with pytest.raises(RuntimeError, match='Fresh isolated handoff sandbox'):
        fixture.seed(tmp_path)
    assert (tmp_path / 'config.yaml').read_bytes() == configuration


def test_install_requires_explicit_server_marker_and_exact_loopback_rendezvous(tmp_path, monkeypatch):
    monkeypatch.delenv('EIDOLON_NATIVE_CREDENTIAL_HANDOFF_FIXTURE', raising=False)
    with pytest.raises(RuntimeError, match='Explicit synthetic handoff server'):
        fixture.install()
    monkeypatch.setenv('EIDOLON_NATIVE_CREDENTIAL_HANDOFF_FIXTURE', '1')
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(sys, 'argv', ['eidolon', 'serve'])
    with pytest.raises((RuntimeError, FileNotFoundError)):
        fixture.install()
    seeded(tmp_path)
    with pytest.raises(RuntimeError, match='Exact loopback'):
        fixture.install()


@pytest.mark.linux_only
def test_real_service_held_model_round_replaces_once_then_retires(tmp_path, monkeypatch, caplog):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    import time
    from eidolon_constants import set_eidolon_home_override, reset_eidolon_home_override
    from eidolon_cli import config, organization_service, organization_credential_service
    from eidolon_cli.organization_credential_handoff import handoffs
    from eidolon_cli.organization_config import OrganizationSettings
    from eidolon_cli.organization_store import OrganizationStore

    rendezvous = str(uuid.uuid4())
    (tmp_path / '.credential-handoff-rendezvous').write_text(rendezvous)
    received, public_ids = [], []
    held, released = threading.Event(), threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.reply({'data': [{'id': 'organization-test'}]})

        def do_POST(self):
            if self.path != '/' + rendezvous + '/v1/chat/completions':
                self.send_error(404); return
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            received.append(request)
            number = len(received)
            if number == 1:
                held.set()
                if not released.wait(15):
                    self.send_error(408); return
                name, args = 'organization_credential_catalog', {}
            elif number == 2:
                result = json.loads(next(message['content'] for message in reversed(request['messages']) if message['role'] == 'tool'))
                public_ids.append(result['result']['replacementHandoffs'][0]['handoffId'])
                name, args = 'organization_credential_replace', {'handoffId': public_ids[0]}
            else:
                name, args = None, None
            message = ({'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'handoff-' + str(number),
                'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]} if name else
                {'role': 'assistant', 'content': json.dumps({'summary': 'Completed synthetic handoff',
                    'deliverable': 'Harmless authorization reference ' + public_ids[0]})})
            self.reply({'id': 'synthetic-handoff', 'object': 'chat.completion', 'created': 1, 'model': 'organization-test',
                'choices': [{'index': 0, 'message': message, 'finish_reason': 'tool_calls' if name else 'stop'}],
                'usage': {'prompt_tokens': 30, 'completion_tokens': 20, 'total_tokens': 50}})

        def reply(self, value):
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers()
            self.wfile.write(json.dumps(value).encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    settings = {'model': {'provider': 'custom', 'default': 'organization-test',
        'base_url': f'http://127.0.0.1:{server.server_port}/{rendezvous}/v1', 'api_mode': 'chat_completions',
        'streaming': False, 'context_length': 128000}, 'agent': {'environment_probe': False},
        'compression': {'enabled': False}, 'approvals': {'mode': 'off'}}
    (tmp_path / 'config.yaml').write_text(json.dumps(settings))
    data = fixture.seed(tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-loopback-only')
    monkeypatch.setenv('EIDOLON_NATIVE_CREDENTIAL_HANDOFF_FIXTURE', '1')
    monkeypatch.setattr(sys, 'argv', ['eidolon', 'serve'])
    # Restore the test-only install overrides even if a regression raises.
    monkeypatch.setattr(organization_service.OrganizationService, '__init__', organization_service.OrganizationService.__init__)
    monkeypatch.setattr(organization_credential_service, 'native_backend', organization_credential_service.native_backend)
    fixture.install()
    config._LOAD_CONFIG_CACHE.clear()
    token = set_eidolon_home_override(tmp_path)
    store = OrganizationStore(tmp_path / 'organization' / 'state.db', OrganizationSettings.from_config({
        'organization': {'max_workers': 1, 'max_inflight': 1, 'max_attempts': 1}}))
    service = organization_service.OrganizationService(store, home=tmp_path)
    try:
        service.start()
        assert not held.wait(.1)
        (tmp_path / '.credential-handoff-start').write_text(rendezvous)
        assert held.wait(10), fixture.inspect(tmp_path)
        catalog, owner = organization_credential_service.owner_catalog(tmp_path, data['organizationId'], data['ownerId'])
        target = handoffs.targets(catalog, owner, data['entryId'], 1)[0]
        before = set(catalog.inputs._pending)
        prepared = handoffs.prepare(catalog, owner, data['entryId'], 1, target['actorId'], target['requestId'],
            target['generation'], 'synthetic-owner-prepare', object(), 'SYNTHETIC_SERVICE_HANDOFF_21', lambda: None,
            target['grantRevision'])
        handles = set(catalog.inputs._pending) - before
        released.set()
        deadline = time.monotonic() + 10
        while fixture.inspect(tmp_path)['request']['status'] == 'running' and time.monotonic() < deadline:
            time.sleep(.05)
        observed = fixture.inspect(tmp_path)
        assert observed['request']['status'] == 'completed', observed
        assert observed['modelCalls'] == 3 and observed['entries'][0]['version'] == 1
        assert handoffs.status(catalog, owner, prepared['operationId'])['state'] == 'committed'
        assert handoffs.targets(catalog, owner, data['entryId'], 2) == []
        assert not fixture.dispatch_allowed(tmp_path, data, rendezvous)
        exposed = json.dumps(received) + json.dumps(observed) + caplog.text
        exposed += ''.join(path.read_bytes().decode('latin1') for path in (tmp_path / 'organization').glob('*.db*'))
        assert 'SYNTHETIC_SERVICE_HANDOFF_21' not in exposed
        assert all(handle not in exposed for handle in handles)
    finally:
        released.set()
        service.stop()
        server.shutdown(); server.server_close(); thread.join(5)
        reset_eidolon_home_override(token)
        config._LOAD_CONFIG_CACHE.clear()
