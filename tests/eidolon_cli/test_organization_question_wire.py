"""Real synthetic provider transport proves management escalation and accounting."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

import pytest

from eidolon_cli.organization_executor import execute
from eidolon_cli.organization_store import OrganizationStore
from tests.eidolon_cli.test_organization_question_routing import question_store, work_claim


@pytest.mark.parametrize('escalate', [False, True])
def test_provider_question_uses_leadership_and_preserves_exact_shared_accounting(tmp_path, monkeypatch, escalate):
    from eidolon_cli import config
    from eidolon_cli.organization_budget import budget_view
    store, objective, plan = question_store(tmp_path)
    work = work_claim(store, plan)
    origin_task = store.context(work)['task']
    question = 'Which audience? Keep the exact wording — including punctuation.'
    answer = 'The board. Keep a technical appendix.'
    escalation = 'I cannot answer; the Executive has the audience details.'
    seen, errors = [], []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"data":[{"id":"question-fixture"}]}')

        def do_POST(self):
            wire = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            try:
                assert not wire.get('tools')
                data = json.loads(wire['messages'][-1]['content'].split('Submitted context:\n', 1)[1])
                context, kind = data['context'], data['request']['type']
                seen.append((context['agent']['id'], kind))
                if kind == 'request.question':
                    origin = context['requestOrigin']
                    assert origin['requestId'] == work['id']
                    assert origin['requestType'] == work['type']
                    assert origin['task']['id'] == work['task_id']
                    assert origin['task']['description'] == origin_task['description']
                    assert origin['workPackageId'] == origin_task['workPackageId']
                    assert context['requestContract']['requestedOutcome'] == question
                    assert context['requestContract']['requesterId'] == 'writer'
                    if context['agent']['id'] == 'z-manager' and escalate:
                        result = {'cannot_answer': escalation}
                    else:
                        if escalate:
                            receipt, = context['requestContract']['questionRouting']['receipts']
                            assert receipt['agentId'] == 'z-manager' and receipt['text'] == escalation
                        result = {'answer': answer}
                elif not context['requestResponses']:
                    result = {'requests': [{'type': 'request.question', 'requestedOutcome': question}]}
                else:
                    response, = context['requestResponses']
                    assert response['requestedOutcome'] == question
                    assert response['response']['text'] == answer
                    assert response['response']['responderId'] == ('chief' if escalate else 'z-manager')
                    result = {'summary': 'Board brief', 'deliverable': 'Board finding with technical appendix.'}
            except Exception as exc:
                errors.append(repr(exc))
                result = {'intervention': 'Synthetic provider assertion failed.'}
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'id': 'fixture', 'object': 'chat.completion', 'created': 1,
                'model': 'question-fixture', 'choices': [{'index': 0, 'finish_reason': 'stop',
                'message': {'role': 'assistant', 'content': json.dumps(result)}}],
                'usage': {'prompt_tokens': 200, 'completion_tokens': 50, 'total_tokens': 250}}).encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    home = tmp_path / 'profile'
    home.mkdir()
    (home / 'config.yaml').write_text(json.dumps({'model': {'provider': 'custom', 'default': 'question-fixture',
        'base_url': f'http://127.0.0.1:{server.server_port}/v1', 'api_mode': 'chat_completions',
        'streaming': False, 'context_length': 128000}, 'agent': {'environment_probe': False},
        'compression': {'enabled': False}}))
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
        assert not errors and 'intervention' not in result, (errors, result)
        assert store.finish(claim, result)

    try:
        turn(work)
        question_claim = store.claim_next()
        question_id = question_claim['id']
        turn(question_claim)
        if escalate:
            store = OrganizationStore(store.path)
            question_claim = store.claim_next()
            assert question_claim['id'] == question_id
            turn(question_claim)
        store = OrganizationStore(store.path)
        resumed = store.claim_next()
        assert resumed['id'] == work['id'] and resumed['agent_id'] == 'writer'
        turn(resumed)
        assert seen == [('writer', 'work.draft'), ('z-manager', 'request.question'),
                        *([('chief', 'request.question')] if escalate else []), ('writer', 'work.draft')]
        calls = store.execution_audit(question_id)['modelCalls']
        assert len(calls) == (2 if escalate else 1)
        assert len({call['token'] for call in calls}) == len(calls)
        with store._connect() as conn:
            budget = budget_view(conn, objective['id'], store.settings)
            assert budget['modelCalls'] == len(seen)
            reservations = conn.execute('SELECT input_limit,output_limit FROM organization_model_calls WHERE objective_id=?',
                                        (objective['id'],)).fetchall()
            assert budget['reservedTokens'] == sum(row['input_limit'] + row['output_limit'] for row in reservations)
            usage = conn.execute('SELECT input_tokens,output_tokens FROM objective_usage WHERE request_id=? ORDER BY rowid',
                                 (question_id,)).fetchall()
            assert len(usage) == len(calls)
            assert sum(row['input_tokens'] for row in usage) == 200 * len(calls)
            assert sum(row['output_tokens'] for row in usage) == 50 * len(calls)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        config._LOAD_CONFIG_CACHE.clear()
