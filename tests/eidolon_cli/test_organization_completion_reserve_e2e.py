"""Real SQLite, runtime transport, loopback POSTs and protected completion admission."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

import pytest

from eidolon_cli import config, organization_executor as executor
from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_store import OrganizationStore
from tests.eidolon_cli.test_organization_completion_reserve import work_claim, usage


@pytest.mark.parametrize('allow_first_failed_send', [False, True])
def test_real_executor_denies_ordinary_initial_or_retry_post_but_admits_completion(tmp_path, monkeypatch, allow_first_failed_send):
    received, errors = [], []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"data":[{"id":"reserve-fixture","context_length":128000}]}')

        def do_POST(self):
            wire = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            received.append(wire)
            try:
                assert not wire.get('tools')
                prompt = next(row['content'] for row in wire['messages'] if row['role'] == 'user')
                submitted = json.loads(prompt.split('Submitted context:\n', 1)[1])
                kind = submitted['request']['type']
                if kind == 'work.draft':
                    assert allow_first_failed_send
                    self.send_response(500)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(b'{"error":{"message":"Synthetic transient failure","type":"server_error"}}')
                    return
                assert kind == 'request.review'
                output = {'approved': True, 'summary': 'Exact retained fact independently checked',
                          'evidenceIds': [row['id'] for row in submitted['context']['evidence']]}
            except Exception as exc:
                errors.append(repr(exc))
                output = {'intervention': 'Fixture request assertion failed'}
            response = {'id': 'local-reserve-result', 'object': 'chat.completion', 'created': 1,
                        'model': 'reserve-fixture', 'choices': [{'index': 0, 'message': {
                            'role': 'assistant', 'content': json.dumps(output)}, 'finish_reason': 'stop'}]}
            data = json.dumps(response).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    home = tmp_path / 'profile'
    home.mkdir()
    cfg = {'model': {'provider': 'custom', 'default': 'reserve-fixture',
                     'base_url': f'http://127.0.0.1:{server.server_port}/v1',
                     'api_mode': 'chat_completions', 'streaming': False, 'context_length': 128000},
           'agent': {'environment_probe': False}, 'compression': {'enabled': False},
           'organization': {'max_model_calls': 2 if allow_first_failed_send else 1,
                            'completion_reserve_model_calls': 1, 'max_inflight': 1}}
    (home / 'config.yaml').write_text(json.dumps(cfg), encoding='utf-8')
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setenv('OPENAI_API_KEY', 'local-loopback-fixture-only')
    config._LOAD_CONFIG_CACHE.clear()
    store = OrganizationStore(home / 'organization' / 'state.db', OrganizationSettings.from_config(cfg))
    objective = store.create_objective('Retain and review exact fact', idempotency_key='fixture')

    def execute(claim):
        context = {**store.context(claim),
                   'reserveModelCall': lambda **entry: store.reserve_model_call(claim, **entry),
                   'recordContextReceipt': lambda report: store.record_context_receipt(claim, report),
                   'recordEvidencePass': lambda report: store.record_evidence_pass(claim, report)}
        return executor.execute(claim, context, threading.Event())

    try:
        work = work_claim(store)
        result = execute(work)
        assert 'protected completion model-call reserve' in result.get('intervention', ''), result
        assert len(received) == int(allow_first_failed_send)
        ordinary_calls = store.execution_audit(work['id'])['modelCalls']
        assert len(ordinary_calls) == len(received)
        assert all(row['classification'] == 'ordinary' for row in ordinary_calls)
        assert usage(store, objective)['completionReserve']['protectedRemaining']['modelCalls'] == 1
        # Produce the retained fixture artifact through the normal backend path;
        # the failed/unsent attempts above remain charged independently of result.
        assert store.finish(work, {'summary': 'Fixture fact', 'deliverable': 'A retained exact fact.'})
        review = store.claim_next()
        assert review['type'] == 'request.review'
        accepted = execute(review)
        assert accepted.get('approved') is True, accepted
        assert len(received) == int(allow_first_failed_send) + 1
        assert not errors, errors
        assert store.finish(review, accepted)
        reopened = OrganizationStore(store.path)
        view = usage(reopened, objective)
        assert view['modelCalls'] == len(received) == view['modelCallLimit']
        assert view['completionReserve']['completionReserved']['modelCalls'] == 1
        assert view['completionReserve']['protectedRemaining']['modelCalls'] == 0
        assert reopened.execution_audit(review['id'])['modelCalls'][0]['classification'] == 'completion'
        assert reopened.claim_next() is None  # Protection cannot authorize beyond hard caps.
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
        config._LOAD_CONFIG_CACHE.clear()
