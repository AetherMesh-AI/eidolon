"""Real local-provider staffing, typed request, and persistent continuation flow."""
from collections import Counter
import json
import hashlib
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_service import OrganizationService
from eidolon_cli.organization_store import OrganizationStore


@pytest.mark.linux_only
@pytest.mark.parametrize('owner_fallback', [False, True])
def test_hire_question_answer_resumes_same_identity_through_acceptance(tmp_path, monkeypatch, owner_fallback):
    from eidolon_cli import config

    home = tmp_path / 'profile'
    home.mkdir()
    received, errors, writer_identities = [], [], []
    provider_calls = Counter()
    writer = {'id': 'hired-writer', 'name': 'Persistent writer', 'team': 'general',
              'capabilities': ['work.draft'], 'responsibilities': ['Write board briefs']}
    proposal = {'members': [writer]}
    answer_text = 'The audience is the board. Include a technical appendix.'
    deliverable = 'Board brief with a technical appendix.'

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
                if kind in {'request.review', 'request.integrate', 'request.accept'}:
                    clarification, = context['objectiveClarifications']
                    assert clarification['requestedOutcome']['bodySha256'] in context['evidenceBodies']
                    assert context['evidenceBodies'][clarification['response']['text']['bodySha256']] == answer_text
                    assert clarification['response']['responderId'] == ('owner' if owner_fallback else 'advisor')
                    assert not clarification['historical']
                provider_calls[kind] += 1
                assert not body.get('tools')
                if kind == 'request.plan':
                    assert context['agent']['id'] == 'manager'
                    if not context['requestResponses']:
                        output = {'requests': [{'type': 'request.hire', 'team': 'general',
                                                'requestedOutcome': 'Create the persistent board writer.',
                                                'managementProposal': proposal}]}
                    else:
                        response = context['requestResponses'][0]
                        assert response['requesterId'] == 'manager'
                        canonical = response['managementProposal']
                        assert canonical['transfers'] == []
                        assert len(canonical['members']) == 1
                        proposed = canonical['members'][0]
                        assert {key: proposed[key] for key in writer} == writer
                        assert proposed['role'] == 'Worker' and proposed['manager_id'] == 'manager'
                        assert proposed['authority'] == proposed['managed_teams'] == proposed['tool_grants'] == []
                        assert proposed['provider'] is None and proposed['model'] is None
                        assert response['response']['responderId'] == 'staffer'
                        assert response['response']['decision'] == 'approved'
                        assert any(staff['id'] == writer['id'] for staff in context['staffing'])
                        output = {'workers': 1, 'tasks': [{'title': 'Write board brief', 'type': 'work.draft',
                                  'description': 'Write a brief for the confirmed audience.', 'team': 'general',
                                  'agentId': writer['id'], 'dependsOn': []}]}
                elif kind == 'work.draft':
                    writer_identities.append((context['agent']['id'], context['agent']['identityId'],
                                              context['task']['id']))
                    assert context['agent']['id'] == writer['id']
                    if not context['requestResponses']:
                        output = {'requests': [{'type': 'request.question', 'requestedOutcome': 'Who is the audience?',
                                                'team': 'general'}]}
                    else:
                        response = context['requestResponses'][0]
                        assert response['requesterId'] == writer['id']
                        assert response['response']['responderId'] == ('owner' if owner_fallback else 'advisor')
                        assert response['response']['text'] == answer_text
                        output = {'summary': 'Board brief prepared', 'deliverable': deliverable,
                                  'memory': {'facts': [answer_text]}}
                elif kind == 'request.question':
                    assert not owner_fallback
                    assert context['agent']['id'] == 'advisor'
                    assert context['requestContract']['requiredAuthority'] == 'answer.question'
                    assert context['requestContract']['requesterId'] == writer['id']
                    assert context['requestContract']['requestedOutcome'] == 'Who is the audience?'
                    output = {'answer': answer_text, 'decision': 'answered'}
                elif kind == 'request.review':
                    artifact = context['evidence'][0]
                    exact = context['evidenceBodies'][artifact['content']['bodySha256']]
                    assert hashlib.sha256(exact.encode()).hexdigest() == artifact['sha256']
                    assert len(exact.encode()) == artifact['content']['utf8Bytes']
                    assert exact == deliverable
                    output = {'approved': True, 'summary': 'Brief satisfies the assigned scope.',
                              'evidenceIds': [item['id'] for item in context['evidence']]}
                elif kind == 'request.integrate':
                    output = {'summary': 'Integrated board brief', 'deliverable': deliverable}
                elif kind == 'request.accept':
                    ids = [item['id'] for item in context['evidence']]
                    output = {'approved': True, 'summary': 'The complete brief satisfies the objective.',
                              'evidenceIds': ids, 'conflicts': [], 'criteriaResults': [
                                  {'criterion': criterion, 'satisfied': True, 'evidenceIds': ids,
                                   'reason': 'The persisted brief includes the requested appendix.'}
                                  for criterion in context['objective']['acceptanceCriteria']]}
                else:
                    raise AssertionError(f'Unexpected provider stage: {kind}')
            except Exception as error:
                errors.append(repr(error))
                output = {'intervention': 'Local provider assertion failed'}
            response = {'id': 'typed-request-flow', 'object': 'chat.completion', 'created': 1,
                        'model': 'organization-test', 'choices': [{'index': 0, 'finish_reason': 'stop',
                        'message': {'role': 'assistant', 'content': json.dumps(output)}}],
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
           'organization': {'max_inflight': 1, 'roster': [
               {'id': 'staffer', 'name': 'Scoped staffing manager', 'role': 'Manager', 'team': 'operations',
                'capabilities': ['request.hire'], 'authority': ['staff.manage'], 'managed_teams': ['general']},
               {'id': 'advisor', 'name': 'Audience advisor', 'role': 'Manager', 'team': 'general',
                'capabilities': ['request.question'], 'authority': [] if owner_fallback else ['answer.question']},
           ]}}
    (home / 'config.yaml').write_text(json.dumps(cfg), encoding='utf-8')
    (home / '.env').write_text('OPENAI_API_KEY=local-fixture-only\n', encoding='utf-8')
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(home))
    config._LOAD_CONFIG_CACHE.clear()
    settings = OrganizationSettings.from_config(cfg)
    store = OrganizationStore(home / 'organization' / 'state.db', settings)
    objective = store.create_objective('Prepare a board brief', idempotency_key='typed-once')
    service = OrganizationService(store, home=home, poll_seconds=0.01)
    owner_question_id = None
    try:
        service.start()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            snapshot = store.snapshot()
            if errors or snapshot['objectives'][0]['status'] == 'completed':
                break
            if snapshot['objectives'][0]['status'] == 'needs_input':
                pending = [request for request in snapshot['requests'] if request['status'] == 'pending_intervention']
                if owner_fallback and len(pending) == 1 and pending[0]['type'] == 'request.question':
                    owner_question_id = pending[0]['id']
                    assert service.respond(owner_question_id, text=answer_text, decision='answered', idempotency_key='owner-answer')
                    assert not service.respond(owner_question_id, text=answer_text, decision='answered', idempotency_key='owner-answer')
                else:
                    break
            time.sleep(0.01)
        assert not errors, errors
        assert snapshot['objectives'][0]['status'] == 'completed', snapshot['requests']
        assert len(writer_identities) == 2 and writer_identities[0] == writer_identities[1]
        assert provider_calls == Counter({'request.plan': 2, 'work.draft': 2, 'request.review': 1,
                                         'request.integrate': 1, 'request.accept': 1,
                                         **({} if owner_fallback else {'request.question': 1})})
        assert all(request['status'] == 'completed' for request in snapshot['requests'])
        hire = next(request for request in snapshot['requests'] if request['type'] == 'request.hire')
        question = next(request for request in snapshot['requests'] if request['type'] == 'request.question')
        work = next(request for request in snapshot['requests'] if request['type'] == 'work.draft')
        assert hire['agentId'] == 'staffer' and hire['team'] == 'general'
        assert question['parentRequestId'] == work['id'] and work['agentId'] == writer['id']
        assert service.stop()
        reopened = OrganizationStore(store.path, settings)
        retained = next(agent for agent in reopened.snapshot()['agents'] if agent['id'] == writer['id'])
        assert retained['identityId'] == writer_identities[0][1]
        assert retained['context']['memory']['facts'] == [answer_text]
        assert len(retained['context']['recentHistory']) == 1
        assert reopened.create_objective('Prepare a board brief', idempotency_key='typed-once')['id'] == objective['id']
        assert reopened.claim_next() is None
        if owner_question_id:
            assert not reopened.respond(owner_question_id, answer_text, idempotency_key='owner-answer')
        with reopened._connect() as conn:
            assert conn.execute('SELECT count(*) FROM organization_management_receipts').fetchone()[0] == 1
            assert conn.execute('SELECT count(*) FROM request_responses').fetchone()[0] == 2
            assert conn.execute('SELECT count(*) FROM tasks').fetchone()[0] == 1
        assert reopened.settings.tool_grants == settings.tool_grants == ()
        assert reopened.settings.read_roots == settings.read_roots == ()
    finally:
        assert service.stop()
        server.shutdown()
        server.server_close()
        thread.join(3)
        config._LOAD_CONFIG_CACHE.clear()


@pytest.mark.linux_only
@pytest.mark.parametrize('work_type', ['work.inspect', 'work.edit'])
def test_real_tool_worker_can_pause_for_question_before_reading(tmp_path, monkeypatch, work_type):
    from eidolon_cli import config
    from eidolon_cli.organization_executor import execute

    home = tmp_path / 'profile'
    home.mkdir()
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'facts.txt').write_text('Existing source text.\n', encoding='utf-8')
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"data":[{"id":"organization-test"}]}')

        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            output = {'requests': [{'type': 'request.question', 'requestedOutcome': 'Which exact file should I inspect?'}]}
            response = {'id': 'question-before-read', 'object': 'chat.completion', 'created': 1,
                        'model': 'organization-test', 'choices': [{'index': 0, 'finish_reason': 'stop',
                        'message': {'role': 'assistant', 'content': json.dumps(output)}}]}
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
           'organization': {'max_inflight': 1, 'capabilities': [work_type], 'tool_grants': ['read_file'],
                            'read_roots': [str(source)], 'roster': [
                                {'id': 'reader', 'name': 'Reader', 'team': 'general',
                                 'capabilities': [work_type], 'tool_grants': ['read_file']}]}}
    (home / 'config.yaml').write_text(json.dumps(cfg), encoding='utf-8')
    (home / '.env').write_text('OPENAI_API_KEY=local-fixture-only\n', encoding='utf-8')
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(home))
    config._LOAD_CONFIG_CACHE.clear()
    store = OrganizationStore(home / 'organization' / 'state.db', OrganizationSettings.from_config(cfg))
    store.create_objective('Inspect the requested source', idempotency_key='question-first')
    plan = store.claim_next()
    store.finish(plan, {'workers': 1, 'tasks': [{'title': 'Inspect source', 'type': work_type,
                        'description': 'Ask for the exact source file before reading.', 'agentId': 'reader'}]})
    hire = store.claim_next()
    store.finish(hire, {})
    work = store.claim_next()
    context = store.context(work)
    context.update({
        'recordToolStart': lambda call_id, name, args: store.record_tool_start(work, call_id, name, args),
        'recordToolFinish': lambda ident, result, status: store.record_tool_finish(work, ident, result, status),
        'requestToolReceipts': lambda: store.request_tool_receipts(work),
    })
    if work_type == 'work.edit':
        context['resolveWorkspaceSource'] = lambda path, loader: store.capture_workspace_source(work, path, loader)
    try:
        result = execute(work, context, threading.Event())
        assert len(received) == 1
        assert 'requests' in result, result
        assert store.request_tool_receipts(work) == []
        assert store.finish(work, result)
        parent = next(request for request in store.snapshot()['requests'] if request['id'] == work['id'])
        assert parent['status'] == 'waiting_response'
        assert parent['toolReceipts'] == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
        config._LOAD_CONFIG_CACHE.clear()


def test_typed_dependencies_cannot_cycle_through_task_dependencies(tmp_path):
    settings = OrganizationSettings.from_config({'organization': {'max_inflight': 1, 'roster': [
        {'id': 'writer', 'name': 'Writer', 'team': 'general', 'capabilities': ['work.draft']},
        {'id': 'advisor', 'name': 'Advisor', 'role': 'Manager', 'team': 'general',
         'capabilities': ['request.question'], 'authority': ['answer.question']},
    ]}})
    store = OrganizationStore(tmp_path / 'state.db', settings)
    store.create_objective('Prepare and publish', idempotency_key='cyclic-requests')
    plan = store.claim_next()
    store.finish(plan, {'workers': 1, 'tasks': [
        {'title': 'Prepare', 'description': 'Prepare the brief', 'type': 'work.draft', 'agentId': 'writer'},
        {'title': 'Publish', 'description': 'Prepare the publication', 'type': 'work.draft',
         'agentId': 'writer', 'dependsOn': [0]},
    ]})
    store.finish(store.claim_next(), {})
    first = store.claim_next()
    later = next(request for request in store.snapshot()['requests']
                 if request['type'] == 'work.draft' and request['id'] != first['id'])
    with pytest.raises(ValueError, match='acyclic|dependency|dependencies'):
        store.finish(first, {'requests': [{'type': 'request.question', 'requestedOutcome': 'Use publication results?',
                                          'dependencyIds': [later['id']]}]})
    assert store.heartbeat(first)
    assert not any(request['parentRequestId'] for request in store.snapshot()['requests'])


def test_staffing_request_waits_for_unrelated_live_work_without_owner_intervention(tmp_path):
    settings = OrganizationSettings.from_config({'organization': {'max_inflight': 2, 'roster': [
        {'id': 'writer-a', 'name': 'Writer A', 'team': 'general', 'capabilities': ['work.draft']},
        {'id': 'writer-b', 'name': 'Writer B', 'team': 'general', 'capabilities': ['work.draft']},
        {'id': 'staffer', 'name': 'Staffer', 'role': 'Manager', 'team': 'general',
         'capabilities': ['request.hire'], 'authority': ['staff.manage']},
    ]}})
    store = OrganizationStore(tmp_path / 'state.db', settings)
    store.create_objective('Independent parallel assignments', idempotency_key='concurrent-hire')
    store.finish(store.claim_next(), {'workers': 2, 'tasks': [
        {'title': 'A', 'description': 'Prepare A', 'type': 'work.draft', 'agentId': 'writer-a'},
        {'title': 'B', 'description': 'Prepare B', 'type': 'work.draft', 'agentId': 'writer-b'},
    ]})
    store.finish(store.claim_next(), {})
    first, unrelated = store.claim_next(), store.claim_next()
    store.finish(first, {'requests': [{'type': 'request.hire', 'requestedOutcome': 'Add a persistent helper.',
        'managementProposal': {'members': [{'id': 'helper', 'name': 'Helper', 'team': 'general',
                                            'capabilities': ['work.draft']}]}}]})
    # Staffing changes require a quiescent ledger. The temporary live sibling
    # must delay admission rather than turn an authorized request into a failure.
    assert store.claim_next() is None
    assert store.heartbeat(unrelated)
    store.finish(unrelated, {'summary': 'Unrelated B done', 'deliverable': 'Prepared B.'})
    management = store.claim_next()
    assert management['type'] == 'request.hire'
    # The queued unrelated review cannot start until staffing commits.
    assert store.claim_next() is None
    assert store.finish(management, {})
    assert any(agent['id'] == 'helper' for agent in store.snapshot()['agents'])
    assert not any(request['status'] == 'pending_intervention' for request in store.snapshot()['requests'])


def test_idle_scheduler_adopts_peer_configuration_without_an_rpc(tmp_path):
    store = OrganizationStore(tmp_path / 'organization' / 'state.db')
    executed = threading.Event()
    seen = []

    def execute(request, context, cancel):
        seen.append((request['objective_id'], context['maxInflight']))
        executed.set()
        return {'intervention': 'Stop after proving idle peer configuration adoption.'}

    service = OrganizationService(store, home=tmp_path, executor=execute, poll_seconds=0.01)
    try:
        service.start()
        peer = OrganizationStore(store.path)
        peer.configure_organization({'max_inflight': 1}, expected_generation=peer._policy_generation,
                                    idempotency_key='peer-save')
        objective = peer.create_objective('Resume without a UI connection', idempotency_key='peer-goal')
        assert executed.wait(5)
        assert seen == [(objective['id'], 1)]
        assert service.settings.max_inflight == service.store.settings.max_inflight == 1
    finally:
        assert service.stop()


@pytest.mark.parametrize('proposal', [
    {'members': [{'id': 'invalid-name', 'name': {'unexpected': 'object'}, 'capabilities': ['work.draft']}]},
    {'transfers': [{'fromAgentId': 'worker-1', 'toAgentId': 'worker-2', 'taskIds': None, 'includeMemory': True}]},
])
def test_malformed_staffing_proposal_rolls_back_children_and_preserves_original_claim(tmp_path, proposal):
    store = OrganizationStore(tmp_path / 'state.db')
    store.create_objective('Validate a proposed staffing change', idempotency_key='malformed-proposal')
    planner = store.claim_next()
    before = store.snapshot()
    with pytest.raises(ValueError, match='Management member|Management transfer'):
        store.finish(planner, {'requests': [
            {'type': 'request.question', 'requestedOutcome': 'A valid first sibling must roll back too.'},
            {'type': 'request.hire', 'requestedOutcome': 'Apply this staffing proposal.',
             'managementProposal': proposal},
        ]})
    after = store.snapshot()
    assert after['requests'] == before['requests']
    assert after['agents'] == before['agents']
    assert after['activity'] == before['activity']
    assert store.heartbeat(planner)
    assert store.context(planner)['agent']['id'] == planner['agent_id']
    with store._connect() as conn:
        assert not conn.execute('SELECT 1 FROM request_continuations').fetchone()
        assert not conn.execute('SELECT 1 FROM organization_management_receipts').fetchone()
        assert not conn.execute('SELECT 1 FROM request_responses').fetchone()
