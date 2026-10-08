"""Real localhost-provider reads stay fresh across clarification and restart."""
from tests.organization_package_helpers import decomposition_result
from collections import Counter
import hashlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_service import OrganizationService
from eidolon_cli.organization_store import OrganizationStore


def _read_after_owner_answer_and_restart(tmp_path, monkeypatch):
    from eidolon_cli import config
    from tools import file_tools

    tmp_path = tmp_path.resolve()
    source = tmp_path / 'source'
    source.mkdir()
    prices = source / 'prices.txt'
    initial_source = 'A costs 40 dollars.\nB costs 70 dollars.\n'
    revised_source = 'A costs 95 dollars.\nB costs 65 dollars.\n'
    prices.write_text(initial_source, encoding='utf-8')
    home = tmp_path / 'profile'
    home.mkdir()
    question_text = 'Are these prices final, or should I wait for the revised prices?'
    answer_text = 'Use the revised prices in root0/prices.txt and recommend the cheaper option.'
    deliverable = ('root0/prices.txt lines 1–2: A costs 95 dollars, B costs 65 dollars. '
                   'Choose B; it is 30 dollars cheaper.')
    received, errors, read_results, reader_identities, reviewed_stages = [], [], [], [], []
    provider_calls = Counter()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"data":[{"id":"organization-test"}]}')

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            received.append(body)
            try:
                prompt = next(message['content'] for message in body['messages']
                              if message['role'] == 'user' and 'Submitted context:\n' in message.get('content', ''))
                data = json.loads(prompt.split('Submitted context:\n', 1)[1])
                kind, context = data['request']['type'], data['context']
                provider_calls[kind] += 1
                if kind == 'request.decompose':
                    output = decomposition_result(context)
                elif kind == 'request.plan':
                    assert not body.get('tools')
                    output = {'workers': 1, 'tasks': [{'title': 'Compare current prices', 'type': 'work.inspect',
                              'description': 'Read root0/prices.txt and recommend the cheaper option using final prices.',
                              'team': 'general', 'agentId': 'reader', 'dependsOn': []}]}
                elif kind == 'work.inspect':
                    resumed = bool(context['requestResponses'])
                    if resumed:
                        response, = context['requestResponses']
                        assert response['requestedOutcome'] == question_text
                        assert response['response']['text'] == answer_text
                        assert response['response']['responderId'] == 'owner'
                    results = [message for message in body['messages'] if message['role'] == 'tool']
                    if not results:
                        reader_identities.append((context['agent']['id'], context['agent']['identityId'],
                                                  context['task']['id']))
                        # Providers may reuse a call ID in a new execution of the same assignment.
                        self.respond({'role': 'assistant', 'content': None, 'tool_calls': [
                            {'id': 'read-prices', 'type': 'function', 'function': {'name': 'read_file',
                             'arguments': '{"path":"root0/prices.txt","offset":1,"limit":2}'}}]}, 'tool_calls')
                        return
                    result, = results
                    assert result['tool_call_id'] == 'read-prices'
                    observation = json.loads(result['content'])
                    expected_source = revised_source if resumed else initial_source
                    expected_content = '\n'.join(f'{number}|{line}'
                                                 for number, line in enumerate(expected_source.splitlines(), 1))
                    assert observation['success'] and not observation['truncated']
                    assert observation['content'] == expected_content, 'Resumed read replayed pre-clarification source bytes'
                    assert observation['file_size'] == len(expected_source.encode())
                    with store._connect() as conn:
                        receipt = dict(conn.execute('SELECT * FROM tool_receipts ORDER BY created DESC,id DESC LIMIT 1').fetchone())
                    assert receipt['status'] == 'completed' and receipt['result'] == result['content']
                    assert receipt['result_sha256'] == hashlib.sha256(result['content'].encode()).hexdigest()
                    read_results.append((receipt['id'], result['content']))
                    output = ({'summary': 'Compared revised prices', 'deliverable': deliverable} if resumed else
                              {'requests': [{'type': 'request.question', 'requestedOutcome': question_text}]})
                else:
                    assert kind in {'request.review', 'request.integrate', 'request.accept'}
                    assert not body.get('tools')
                    clarification, = context['objectiveClarifications']
                    assert not clarification['historical']
                    assert context['evidenceBodies'][clarification['requestedOutcome']['bodySha256']] == question_text
                    assert context['evidenceBodies'][clarification['response']['text']['bodySha256']] == answer_text
                    assert clarification['response']['responderId'] == 'owner'
                    receipt, = context['toolReceipts']
                    assert receipt['id'] == read_results[1][0] != read_results[0][0]
                    assert receipt['result'] == read_results[1][1]
                    assert receipt['status'] == 'completed' and receipt['toolName'] == 'read_file'
                    assert receipt['resultSha256'] == hashlib.sha256(receipt['result'].encode()).hexdigest()
                    assert read_results[0][0] not in json.dumps(context)
                    task_proof, = [item for item in context['evidence'] if item['toolReceiptIds']]
                    assert task_proof['toolReceiptIds'] == [receipt['id']]
                    for artifact in context['evidence']:
                        exact = context['evidenceBodies'][artifact['content']['bodySha256']]
                        assert exact == deliverable
                        assert hashlib.sha256(exact.encode()).hexdigest() == artifact['sha256']
                        assert len(exact.encode()) == artifact['content']['utf8Bytes']
                    reviewed_stages.append((kind, task_proof['id'], receipt['id']))
                    ids = [item['id'] for item in context['evidence']]
                    if kind == 'request.review':
                        output = {'approved': True, 'summary': 'The revised source supports recommending B.', 'evidenceIds': ids}
                    elif kind == 'request.integrate':
                        output = {'summary': 'Integrated revised price recommendation', 'deliverable': deliverable}
                    else:
                        integrated, = [item for item in context['evidence'] if item.get('kind') == 'integrated_deliverable']
                        assert integrated['toolReceiptIds'] == []
                        output = {'approved': True, 'summary': 'The complete objective uses the owner-confirmed revised prices.',
                                  'evidenceIds': ids, 'conflicts': [], 'criteriaResults': [
                                      {'criterion': criterion, 'satisfied': True, 'evidenceIds': ids,
                                       'reason': 'The fresh source read supports the exact final recommendation.'}
                                      for criterion in context['objective']['acceptanceCriteria']]}
                self.respond({'role': 'assistant', 'content': json.dumps(output)}, 'stop')
            except Exception as error:
                errors.append(repr(error))
                self.respond({'role': 'assistant', 'content': '{"intervention":"Local provider assertion failed"}'}, 'stop')

        def respond(self, message, finish):
            response = {'id': 'read-clarification-flow', 'object': 'chat.completion', 'created': 1,
                        'model': 'organization-test', 'choices': [{'index': 0, 'message': message, 'finish_reason': finish}],
                        'usage': {'prompt_tokens': 30, 'completion_tokens': 20, 'total_tokens': 50}}
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    cfg = {'model': {'provider': 'custom', 'default': 'organization-test',
                    'base_url': f'http://127.0.0.1:{server.server_port}/v1',
                    'api_mode': 'chat_completions', 'streaming': False, 'context_length': 128000},
           'agent': {'environment_probe': False}, 'compression': {'enabled': False},
           'organization': {'max_inflight': 1, 'max_attempts': 1, 'max_tool_calls': 1,
                            'capabilities': ['work.inspect'], 'tool_grants': ['read_file'], 'read_roots': [str(source)],
                            'roster': [{'id': 'reader', 'name': 'Price reader', 'team': 'general',
                                        'capabilities': ['work.inspect'], 'tool_grants': ['read_file']}]}}
    (home / 'config.yaml').write_text(json.dumps(cfg), encoding='utf-8')
    (home / '.env').write_text('OPENAI_API_KEY=local-fixture-only\n', encoding='utf-8')
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(home))
    config._LOAD_CONFIG_CACHE.clear()

    def no_backend(*_):
        raise AssertionError('Organization read attempted to create a shell backend')

    monkeypatch.setattr(file_tools, '_get_file_ops', no_backend)
    settings = OrganizationSettings.from_config(cfg)
    store = OrganizationStore(home / 'organization' / 'state.db', settings)
    objective = store.create_objective('Compare final prices', 'Recommend the cheaper option from root0/prices.txt.',
                                       idempotency_key='read-clarification-once',
                                       acceptance_criteria=['Use the final prices and cite the cheaper option and exact difference.'])
    dispatch = threading.Event()
    dispatch.set()
    service = OrganizationService(store, home=home, poll_seconds=0.01, can_dispatch=dispatch.is_set)

    def wait_for_terminal_status():
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            snapshot = store.snapshot()
            if errors or snapshot['objectives'][0]['status'] in {'needs_input', 'completed'}:
                return snapshot
            time.sleep(0.01)
        pytest.fail(f'Organization did not settle: {snapshot["requests"]}')

    try:
        service.start()
        snapshot = wait_for_terminal_status()
        assert not errors, errors
        assert snapshot['objectives'][0]['status'] == 'needs_input', snapshot['requests']
        question, = [request for request in snapshot['requests'] if request['status'] == 'pending_intervention']
        work, = [request for request in snapshot['requests'] if request['type'] == 'work.inspect']
        assert question['type'] == 'request.question' and question['parentRequestId'] == work['id']
        assert work['status'] == 'waiting_response' and work['attempts'] == 0
        initial_receipt, = store.tool_receipts(work['id'])
        assert (initial_receipt['id'], initial_receipt['result']) == read_results[0]
        assert all(not task['evidence'] for task in snapshot['tasks'])

        # Persist the owner's response while the host drains, then recreate both runtime and ledger.
        dispatch.clear()
        assert service.respond(question['id'], text=answer_text, decision='answered', idempotency_key='owner-answer')
        assert not service.respond(question['id'], text=answer_text, decision='answered', idempotency_key='owner-answer')
        prices.write_text(revised_source, encoding='utf-8')
        assert service.stop()
        store = OrganizationStore(store.path, settings)
        queued = next(request for request in store.snapshot()['requests'] if request['id'] == work['id'])
        assert queued['status'] == 'queued' and queued['attempts'] == 0
        assert store.tool_receipts(work['id']) == [initial_receipt]
        service = OrganizationService(store, home=home, poll_seconds=0.01)
        service.start()
        snapshot = wait_for_terminal_status()
        assert not errors, errors
        outcome, = snapshot['objectives']
        assert outcome['id'] == objective['id'] and outcome['status'] == 'completed', snapshot['requests']
        assert outcome['acceptance']['status'] == 'accepted'
        assert outcome['result'] == deliverable == store.evidence(outcome['acceptance']['deliverableId'])['content']
        assert all(item['satisfied'] for item in outcome['acceptance']['criteriaResults'])
        assert all(request['status'] == 'completed' for request in snapshot['requests'])
        completed_work = next(request for request in snapshot['requests'] if request['id'] == work['id'])
        assert completed_work['attempts'] == settings.max_attempts == 1
        assert reader_identities[0] == reader_identities[1]
        assert provider_calls == Counter({'request.decompose': 1, 'request.plan': 1, 'work.inspect': 4, 'request.review': 1,
                                         'request.integrate': 1, 'request.accept': 1})
        assert [stage for stage, _, _ in reviewed_stages] == ['request.review', 'request.integrate', 'request.accept']
        assert len({proof for _, proof, _ in reviewed_stages}) == 1
        assert str(source) not in json.dumps(received)
        assert service.stop()

        reopened = OrganizationStore(store.path, settings)
        old_receipt, fresh_receipt = reopened.tool_receipts(work['id'])
        assert old_receipt == initial_receipt
        assert fresh_receipt['id'] != old_receipt['id']
        assert fresh_receipt['toolCallId'] == old_receipt['toolCallId'] == 'read-prices'
        assert fresh_receipt['attempt'] > old_receipt['attempt']
        assert (fresh_receipt['id'], fresh_receipt['result']) == read_results[1]
        proof = reopened.evidence(reviewed_stages[0][1])
        assert proof['content'] == deliverable and proof['toolReceipts'] == [fresh_receipt]
        assert reopened.claim_next() is None
        assert not reopened.respond(question['id'], text=answer_text, decision='answered', idempotency_key='owner-answer')
    finally:
        assert service.stop()
        server.shutdown()
        server.server_close()
        thread.join(3)
        config._LOAD_CONFIG_CACHE.clear()


@pytest.mark.linux_only
def test_native_linux_read_after_owner_answer_and_restart_uses_fresh_receipt(tmp_path, monkeypatch):
    _read_after_owner_answer_and_restart(tmp_path, monkeypatch)


@pytest.mark.macos_only
def test_native_macos_read_after_owner_answer_and_restart_uses_fresh_receipt(tmp_path, monkeypatch):
    _read_after_owner_answer_and_restart(tmp_path, monkeypatch)
