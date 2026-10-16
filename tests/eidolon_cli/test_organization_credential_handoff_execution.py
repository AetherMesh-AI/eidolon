"""Full model round and service-finally fences for delegated value replacement."""
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from contextlib import contextmanager

import pytest

from tests.tui_gateway.test_organization_credential_handoff import delegated  # noqa: F401
from tests.tui_gateway.test_organization_credentials import call, served  # noqa: F401


def test_live_lease_is_rechecked_after_waiting_for_catalog_writer(delegated, monkeypatch):
    from eidolon_cli import organization_store
    from eidolon_cli.organization_credential_security import CredentialError
    home, base, transport, backend, store, claim, capsule, catalog, owner, params = delegated
    preparation = call(transport, 'prepareReplacement', params)['result']
    waiting = threading.Event()
    errors = []
    original = capsule.catalog._connection

    @contextmanager
    def connection(*, write=False):
        if write:
            waiting.set()
        with original(write=write) as conn:
            yield conn

    monkeypatch.setattr(capsule.catalog, '_connection', connection)

    def replace():
        try:
            capsule.invoke('organization_credential_replace', {'handoffId': preparation['handoffId']})
        except CredentialError as error:
            errors.append(error.reason)

    with catalog._connection(write=True):
        worker = threading.Thread(target=replace)
        worker.start()
        assert waiting.wait(5)
        # The first lease check succeeded, but catalog contention outlives it.
        monkeypatch.setattr(organization_store.time, 'time', lambda: claim['lease'] + 1)
    worker.join(5)
    assert not worker.is_alive() and errors == ['lease_invalid']
    assert not backend.values


@pytest.mark.linux_only
def test_real_model_receives_only_non_bearer_reference_and_preserves_accounting(delegated, monkeypatch, caplog):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from eidolon_cli import config, organization_executor as executor
    home, base, transport, backend, store, claim, capsule, catalog, owner, params = delegated
    before = set(catalog.inputs._pending)
    preparation = call(transport, 'prepareReplacement', params)['result']
    private_handles = set(catalog.inputs._pending) - before
    received = []
    public_id = preparation['handoffId']
    final = {'summary': 'Completed local replacement ' + public_id,
             'deliverable': 'Owner authorization reference ' + public_id}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.reply({'data': [{'id': 'organization-test'}]})

        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            if len(received) <= 2:
                name = 'organization_credential_catalog' if len(received) == 1 else 'organization_credential_replace'
                args = {} if len(received) == 1 else {'handoffId': public_id}
                message = {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'handoff-' + str(len(received)),
                    'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]}
                finish = 'tool_calls'
            else:
                message, finish = {'role': 'assistant', 'content': json.dumps(final)}, 'stop'
            self.reply({'id': 'local-handoff', 'object': 'chat.completion', 'created': 1, 'model': 'organization-test',
                'choices': [{'index': 0, 'message': message, 'finish_reason': finish}],
                'usage': {'prompt_tokens': 30, 'completion_tokens': 20, 'total_tokens': 50}})

        def reply(self, value):
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers()
            self.wfile.write(json.dumps(value).encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    monkeypatch.setattr(Path, 'home', lambda: home)
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-local-provider-only')
    (home / 'config.yaml').write_text(json.dumps({
        'model': {'provider': 'custom', 'default': 'organization-test', 'base_url': f'http://127.0.0.1:{server.server_port}/v1',
            'api_mode': 'chat_completions', 'streaming': False, 'context_length': 128000},
        'agent': {'environment_probe': False}, 'compression': {'enabled': False}, 'approvals': {'mode': 'off'}}))
    config._LOAD_CONFIG_CACHE.clear()
    try:
        result = executor.execute({'type': 'work.draft'}, {'_credentialCapsule': capsule, 'timeoutSeconds': 20,
            'objective': {'title': 'Complete approved local replacement', 'description': 'Use the supplied handoff reference.'}}, threading.Event())
        assert {key: value for key, value in result.items() if key != 'usage'} == final
        assert len(received) == 3 and result['usage'] == {'inputTokens': 90, 'outputTokens': 60}
        assert list(backend.values.values()) == [params['value'].encode()]
        store.finish(claim, result)
        exposed = json.dumps(received) + json.dumps(result) + caplog.text
        exposed += catalog.path.read_bytes().decode('latin1') + store.path.read_bytes().decode('latin1')
        assert public_id in exposed
        assert params['value'] not in exposed
        assert all(handle not in exposed for handle in private_handles)
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)
        config._LOAD_CONFIG_CACHE.clear()


@pytest.mark.parametrize('outcome', ['success', 'error', 'cancel', 'drain', 'lease-loss'])
def test_service_retires_exact_capsule_and_preserves_slot_cleanup(delegated, monkeypatch, outcome):
    from eidolon_cli.organization_service import OrganizationService, _Running
    home, base, transport, backend, store, claim, capsule, catalog, owner, params = delegated
    call(transport, 'prepareReplacement', params)
    monkeypatch.setattr('eidolon_cli.organization_credential_agent.scheduler_capsule', lambda *_: capsule)
    exits = []

    def execute(request, context, cancel):
        assert context['_credentialCapsule'] is capsule
        if outcome == 'error':
            raise RuntimeError('Synthetic provider failure')
        if outcome == 'cancel':
            cancel.set()
        return {'summary': 'Synthetic finish', 'deliverable': 'Synthetic output'}

    def can_dispatch():
        if outcome == 'lease-loss':
            with store._write() as conn:
                conn.execute('UPDATE requests SET token=? WHERE id=?', ('new-token', claim['id']))
        return outcome != 'drain'

    service = OrganizationService(store, home=home, executor=execute, can_dispatch=can_dispatch)
    record = _Running(claim, execution_slot=SimpleNamespace(__exit__=lambda: exits.append(True)))
    service._work(record)
    assert capsule.retired.is_set() and exits == [True] and service._wake.is_set()
    assert not backend.values
    assert call(transport, 'replacementStatus', {**base, 'operationId': 'prepare'})['result']['state'] == 'unavailable'
