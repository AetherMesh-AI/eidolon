"""Real provider resolution and complete large-evidence integration, localhost only."""
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from eidolon_cli import config, organization_executor as executor
from eidolon_cli.organization_evidence import verify_context_receipt, wire_input_bound, CONTEXT_RESERVE_TOKENS


def test_large_integration_uses_bounded_real_provider_calls_and_all_exact_bytes(tmp_path, monkeypatch):
    received, chunks, reports, passes, reservations = [], [], [], [], []
    provider_failure = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'data': [{'id': 'bounded-fixture', 'context_length': 128000}]}).encode())

        def do_POST(self):
            wire = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            received.append(wire)
            if provider_failure.is_set():
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"local fixture transient error","type":"server_error"}}')
                return
            prompt = next(message['content'] for message in wire['messages'] if message['role'] == 'user')
            submitted = json.loads(prompt.split('Submitted context:\n', 1)[1])
            if 'sourceChunk' in submitted:
                chunks.append(submitted['sourceChunk'])
                output = {'approved': True, 'findings': 'Verified exact original slice: ' + submitted['sourceChunk'][:25] + ' ... ' + submitted['sourceChunk'][-25:], 'conflicts': []}
            else:
                output = {'summary': 'All six findings integrated.', 'deliverable': 'Complete synthesis of six exact original findings.'}
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'id': 'fixture-result', 'model': 'bounded-fixture',
                'object': 'chat.completion', 'created': 1,
                'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': json.dumps(output)}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 200, 'completion_tokens': 100}}).encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    home = tmp_path / 'profile'; home.mkdir()
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setenv('OPENAI_API_KEY', 'localhost-only-test-key')
    (home / 'config.yaml').write_text(json.dumps({
        'model': {'provider': 'custom', 'default': 'bounded-fixture',
                  'base_url': f'http://127.0.0.1:{server.server_port}/v1',
                  'api_mode': 'chat_completions', 'streaming': False, 'context_length': 128000},
        'agent': {'environment_probe': False}, 'compression': {'enabled': False}}))
    config._LOAD_CONFIG_CACHE.clear()
    evidence = []
    for index in range(6):
        content = f'BEGIN-{index}\n' + chr(65 + index) * 27500 + f'\nEND-{index}'
        evidence.append({'id': f'artifact-{index}', 'content': content,
                         'sha256': hashlib.sha256(content.encode()).hexdigest(), 'toolReceipts': []})
    context = {'objective': {'title': 'Integrate exact findings', 'description': 'Combine all six original findings'},
               'evidence': evidence, 'maxContextTokens': 128000, 'maxOutputTokens': 8000, 'timeoutSeconds': 60,
               'recordEvidencePass': passes.append, 'recordContextReceipt': reports.append,
               'reserveModelCall': lambda **entry: reservations.append(entry)}
    try:
        result = executor.execute({'type': 'request.integrate'}, context, threading.Event())
        assert result.get('deliverable') == 'Complete synthesis of six exact original findings.', result
        assert chunks == [item['content'] for item in evidence]
        assert len(received) == len(passes) + 1 == len(reservations)
        verify_context_receipt(context, reports[0], passes, approved=True)
        systems = []
        for wire, reserved in zip(received, reservations):
            bound = wire_input_bound(wire) + CONTEXT_RESERVE_TOKENS
            assert bound + wire['max_tokens'] <= 128000
            assert reserved['input_limit'] >= bound
            assert reserved['output_limit'] == wire['max_tokens']
            systems.append(next(message['content'] for message in wire['messages'] if message['role'] in {'system', 'developer'}))
        assert len(set(systems)) == 1
        assert 'No body is summarized' not in received[-1]['messages'][-1]['content']
        assert result['usage'] == {'inputTokens': 200 * len(received), 'outputTokens': 100 * len(received)}

        # The real runtime retries a provider error, but admission must fence the
        # physical retry before any second POST, not merely after final usage.
        received.clear(); reservations.clear(); reports.clear(); passes.clear()
        provider_failure.set()
        def one_call_only(**entry):
            if reservations:
                raise ValueError('Model-call budget exhausted before the second physical attempt.')
            reservations.append(entry)
        retried = executor.execute({'type': 'work.draft'}, {
            'objective': {'description': 'Produce one bounded draft'}, 'timeoutSeconds': 30,
            'reserveModelCall': one_call_only,
        }, threading.Event())
        assert 'Model-call budget exhausted' in retried.get('intervention', ''), retried
        assert len(received) == len(reservations) == 1

    finally:
        server.shutdown(); server.server_close(); thread.join(3)
        config._LOAD_CONFIG_CACHE.clear()
