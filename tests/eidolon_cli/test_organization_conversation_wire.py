"""Synthetic wire proof: leased worker asks peer, peer replies, same work resumes."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from eidolon_cli.organization_executor import execute
from eidolon_cli.organization_store import OrganizationStore
from tests.eidolon_cli.test_organization_requests import ledger, start_work


def test_real_provider_exchange_resumes_work_and_charges_each_turn(tmp_path, monkeypatch):
    from eidolon_cli import config
    store = ledger(tmp_path)
    _, work = start_work(store)
    home = tmp_path / 'profile'
    home.mkdir()
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"data":[{"id":"conversation-fixture"}]}')

        def do_POST(self):
            wire = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            assert not wire.get('tools')
            seen.append(wire)
            content = wire['messages'][-1]['content']
            submitted = json.loads(content.split('Submitted context:\n', 1)[1])
            kind = submitted['request']['type']
            if kind == 'request.message':
                assert set(submitted['context']) == {'agent', 'agentContext', 'conversation'}
                result = {'reply': 'The board is the audience. Put the main finding first.'}
            elif not submitted['context']['internalConversations']:
                result = {'messages': [{'recipientId': 'advisor', 'subject': 'Audience', 'body': 'Which audience?'}]}
            else:
                answer = submitted['context']['internalConversations'][0]['messages'][-1]['body']
                assert 'board' in answer
                result = {'summary': 'Board brief', 'deliverable': 'Main finding for the board. Supporting details follow.'}
            payload = {'id': 'fixture', 'object': 'chat.completion', 'created': 1,
                       'model': 'conversation-fixture', 'choices': [{'index': 0, 'finish_reason': 'stop',
                       'message': {'role': 'assistant', 'content': json.dumps(result)}}],
                       'usage': {'prompt_tokens': 200, 'completion_tokens': 50, 'total_tokens': 250}}
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    (home / 'config.yaml').write_text(json.dumps({'model': {'provider': 'custom', 'default': 'conversation-fixture',
        'base_url': f'http://127.0.0.1:{server.server_port}/v1', 'api_mode': 'chat_completions', 'streaming': False,
        'context_length': 128000}, 'agent': {'environment_probe': False}, 'compression': {'enabled': False}}))
    (home / '.env').write_text('OPENAI_API_KEY=local-fixture-only\n')
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(home))
    config._LOAD_CONFIG_CACHE.clear()

    def turn(claim):
        context = store.context(claim)
        context.update(reserveModelCall=lambda **values: store.reserve_model_call(claim, **values),
                       recordContextReceipt=lambda report: store.record_context_receipt(claim, report),
                       recordEvidencePass=lambda report: store.record_evidence_pass(claim, report))
        result = execute(claim, context, threading.Event())
        assert 'intervention' not in result, result
        assert store.finish(claim, result)

    try:
        turn(work)
        store = OrganizationStore(store.path)
        delivery = store.claim_next()
        assert delivery['type'] == 'request.message'
        turn(delivery)
        store = OrganizationStore(store.path)
        resumed = store.claim_next()
        assert resumed['id'] == work['id'] and resumed['agent_id'] == work['agent_id']
        turn(resumed)
        assert len(seen) == 3
        assert len(store.execution_audit(work['id'])['modelCalls']) == 2
        assert len(store.execution_audit(delivery['id'])['modelCalls']) == 1
        assert len(store.execution_audit(work['id'])['contexts']) == 2
        assert store.snapshot()['conversations'][0]['status'] == 'answered'
        assert store.claim_next()['type'] == 'request.review'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
        config._LOAD_CONFIG_CACHE.clear()
