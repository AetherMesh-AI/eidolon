"""Registered disposable workspaces, real claims/loop/receipts, synthetic HTTP only."""
import hashlib
import json
import threading
from types import SimpleNamespace

import httpx
import pytest

from eidolon_cli import config, organization_executor, profile_isolation
from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_service import OrganizationService, _Running
from eidolon_cli.organization_store import OrganizationStore
from tests.eidolon_cli.test_organization_workspaces import origin, create, configure, scope, deny_external_network  # noqa: F401
from tests.organization_package_helpers import claim_after_decomposition


@pytest.fixture
def workspace(origin, tmp_path, monkeypatch):
    home, result = create(origin)
    configure(home)
    source = tmp_path / 'project'
    source.mkdir()
    (source / 'notes.txt').write_text('Before\n')
    cfg = {'model': {'provider': 'openai-api', 'default': 'workspace-test-model',
                    'streaming': False, 'context_length': 128000},
           'agent': {'environment_probe': False}, 'compression': {'enabled': False},
           'organization': {'capabilities': ['work.inspect', 'work.edit'],
             'tool_grants': ['read_file', 'patch'], 'read_roots': [str(source)],
             'roster': [{'id': 'editor', 'name': 'Editor', 'team': 'general',
                        'capabilities': ['work.inspect', 'work.edit'], 'tool_grants': ['read_file', 'patch']}]}}
    (home / 'config.yaml').write_text(json.dumps(cfg))
    config._LOAD_CONFIG_CACHE.clear()
    deny_external_network(monkeypatch)
    with scope(home):
        assert profile_isolation.verify_isolation(home)['organizationId'] == result['organizationId']
        settings = OrganizationSettings.from_config(cfg)
        store = OrganizationStore(home / 'organization' / 'state.db', settings)
        yield SimpleNamespace(home=home, store=store, source=source, settings=settings)
    config._LOAD_CONFIG_CACHE.clear()


def claim_work(w, kind):
    objective = w.store.create_objective('Inspect source', idempotency_key='objective')
    plan = claim_after_decomposition(w.store)
    assert w.store.finish(plan, {'tasks': [{'title': 'Inspect', 'description': 'Read root0/notes.txt',
        'type': kind, 'agentId': 'editor'}], 'workers': 1})
    claim = w.store.claim_next()
    while claim and claim['type'] == 'request.hire':
        assert w.store.finish(claim, {})
        claim = w.store.claim_next()
    assert claim['type'] == kind
    return objective, claim


def execute(w, claim, executor=None):
    service = OrganizationService(w.store, home=w.home, settings=w.settings,
                                  **({'executor': executor} if executor else {}))
    record = _Running(claim)
    service._work(record)
    assert record.error is None, record.error
    return record


@pytest.mark.parametrize('kind', ['work.inspect', 'work.edit'])
def test_registered_scheduler_shared_tool_turn_and_independent_review(workspace, monkeypatch, kind):
    from agent import conversation_loop
    w = workspace
    objective, claim = claim_work(w, kind)
    calls, turns = [], []
    original = conversation_loop.run_conversation
    def observed(agent, *args, **kwargs):
        turns.append(agent.session_id)
        return original(agent, *args, **kwargs)
    monkeypatch.setattr(conversation_loop, 'run_conversation', observed)

    def http(transport, request):
        assert request.url.host == 'api.openai.com' and request.url.path == '/v1/chat/completions'
        body = json.loads(request.read())
        calls.append(body)
        prompt = next(m['content'] for m in body['messages'] if m['role'] == 'user')
        submitted = json.loads(prompt.split('Submitted context:\n', 1)[1])
        stage = submitted['request']['type']
        if stage == kind:
            assert [t['function']['name'] for t in body['tools']] == ['read_file']
            reads = [m for m in body['messages'] if m['role'] == 'tool']
            if not reads:
                message = {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'read-1',
                    'type': 'function', 'function': {'name': 'read_file', 'arguments': '{"path":"root0/notes.txt"}'}}]}
                finish = 'tool_calls'
            else:
                receipts = w.store.request_tool_receipts(claim)
                assert len(receipts) == 1 and receipts[0]['status'] == 'completed'
                source = json.loads(reads[0]['content'])
                assert 'Before' in source['content']
                output = {'summary': 'Read source', 'deliverable': 'Before is present.',
                          'memory': {'facts': ['The source contains Before.']}}
                if kind == 'work.edit':
                    output = {'summary': 'Propose replacement', 'edit': {'path': 'root0/notes.txt',
                        'baseRevision': source['workspaceRevision'], 'baseSha256': source['sourceSha256'],
                        'oldText': 'Before', 'newText': 'After'}}
                message, finish = {'role': 'assistant', 'content': json.dumps(output)}, 'stop'
        else:
            assert stage == 'request.review' and not body.get('tools')
            context = submitted['context']
            evidence = context['evidence'][0]
            output = {'approved': True, 'summary': 'Independently checked retained evidence.',
                      'evidenceIds': [evidence['id']]}
            if kind == 'work.edit':
                proposal = evidence['editProposal']
                raw = context['evidenceBodies'][proposal['newContent']['bodySha256']]
                assert raw == 'After\n'
                assert proposal['newSha256'] == hashlib.sha256(raw.encode()).hexdigest()
                output.update(proposalId=proposal['id'], proposalSha256=proposal['proposalSha256'])
            message, finish = {'role': 'assistant', 'content': json.dumps(output)}, 'stop'
        return httpx.Response(200, json={'id': 'synthetic', 'object': 'chat.completion',
            'created': 1, 'model': 'workspace-test-model', 'choices': [{'index': 0,
            'message': message, 'finish_reason': finish}],
            'usage': {'prompt_tokens': 30, 'completion_tokens': 20, 'total_tokens': 50}})
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', http)
    record = execute(w, claim)
    assert 'intervention' not in record.result, record.result
    assert len(calls) == 2 and len(turns) == 1
    assert w.store.finish(claim, record.result)
    review = w.store.claim_next()
    assert review['type'] == 'request.review' and review['agent_id'] != claim['agent_id']
    checked = execute(w, review)
    assert checked.result.get('approved') is True, checked.result
    assert w.store.finish(review, checked.result)
    assert len(calls) == 3 and len(turns) == 2 and turns[0] != turns[1]
    assert (w.source / 'notes.txt').read_text() == 'Before\n'


@pytest.mark.parametrize('change', ['revocation', 'root_replacement', 'schema', 'handler', 'budget', 'cancel', 'model', 'removed_scope'])
def test_between_round_change_blocks_next_send(workspace, monkeypatch, change):
    from agent import conversation_loop
    from tools.registry import registry
    w = workspace
    objective, claim = claim_work(w, 'work.inspect')
    calls, agents = [], []
    original_turn = conversation_loop.run_conversation
    def observed(agent, *args, **kwargs):
        agents.append(agent)
        return original_turn(agent, *args, **kwargs)
    monkeypatch.setattr(conversation_loop, 'run_conversation', observed)
    original_finish = w.store.record_tool_finish
    def finish(*args):
        result = original_finish(*args)
        if change == 'revocation':
            with w.store._write() as conn:
                conn.execute('INSERT OR IGNORE INTO organization_policy_pause VALUES (1)')
        elif change == 'root_replacement':
            w.source.rename(w.source.with_name('old-project'))
            w.source.mkdir()
            (w.source / 'notes.txt').write_text('Foreign replacement')
        elif change == 'removed_scope':
            agents[0]._organization_tool_execution = None
            agents[0]._isolated_source_admission = None
            agents[0].tools = []
            agents[0].valid_tool_names = set()
        elif change == 'model':
            agents[0].model = 'unselected-model'
        elif change == 'schema':
            agents[0].tools[0]['function']['name'] = 'terminal'
        elif change == 'handler':
            monkeypatch.setattr(registry.get_entry('read_file'), 'handler', lambda *a: pytest.fail('Foreign handler ran'))
        elif change == 'budget':
            with w.store._write() as conn:
                conn.execute('UPDATE objective_budgets SET max_model_calls=1 WHERE objective_id=?', (objective['id'],))
        else:
            record.cancel.set()
        return result
    monkeypatch.setattr(w.store, 'record_tool_finish', finish)
    def http(transport, request):
        assert request.url.host == 'api.openai.com'
        calls.append(json.loads(request.read()))
        assert len(calls) == 1, 'A changed boundary must stop the next physical send'
        return httpx.Response(200, json={'id': 'synthetic', 'object': 'chat.completion', 'created': 1,
            'model': 'workspace-test-model', 'choices': [{'index': 0, 'finish_reason': 'tool_calls',
            'message': {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'read-1',
                'type': 'function', 'function': {'name': 'read_file', 'arguments': '{"path":"root0/notes.txt"}'}}]}}],
            'usage': {'prompt_tokens': 30, 'completion_tokens': 20, 'total_tokens': 50}})
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', http)
    record = _Running(claim)
    service = OrganizationService(w.store, home=w.home, settings=w.settings)
    service._work(record)
    assert record.error is None, record.error
    assert 'intervention' in record.result, record.result
    assert len(calls) == 1
    with w.store._connect() as conn:
        receipts = conn.execute('SELECT status FROM tool_receipts WHERE request_id=?', (claim['id'],)).fetchall()
        assert len(receipts) == 1 and receipts[0]['status'] == 'completed'
        assert conn.execute('SELECT count(*) FROM organization_model_calls WHERE request_id=?', (claim['id'],)).fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM evidence WHERE request_id=?', (claim['id'],)).fetchone()[0] == 0


@pytest.mark.parametrize('change', ['foreign_token', 'expired', 'foreign_home', 'callbacks', 'capsule', 'no_admission'])
def test_untrusted_or_stale_entry_cannot_construct_agent(workspace, monkeypatch, change):
    from eidolon_cli.organization_isolated_admission import scheduler_admission
    from eidolon_constants import set_eidolon_home_override, reset_eidolon_home_override
    w = workspace
    _, claim = claim_work(w, 'work.inspect')
    context = w.store.context(claim)
    def forbidden(*args, **kwargs):
        pytest.fail('Rejected entry must not resolve provider or construct agent')
    monkeypatch.setattr(organization_executor, '_runtime_kwargs', forbidden)
    if change == 'no_admission':
        result = organization_executor.execute(claim, context, threading.Event())
        assert 'intervention' in result
        return
    if change == 'foreign_token':
        claim = {**claim, 'token': 'foreign-token'}
    elif change == 'expired':
        with w.store._write() as conn:
            conn.execute('UPDATE requests SET lease=0 WHERE id=?', (claim['id'],))
    if change in {'foreign_token', 'expired'}:
        with pytest.raises(ValueError, match='lease'):
            with scheduler_admission(w.home, w.store, claim, context, lambda: False):
                pytest.fail('Invalid claim admitted')
        return
    assert w.store.begin_dispatch(claim)
    with scheduler_admission(w.home, w.store, claim, context, lambda: False):
        token = None
        if change == 'foreign_home':
            token = set_eidolon_home_override(w.home.parent.parent)
        elif change == 'callbacks':
            context['recordToolStart'] = lambda *a: pytest.fail('Forged callback ran')
        else:
            context['_credentialCapsule'] = object()
        try:
            result = organization_executor.execute(claim, context, threading.Event())
            assert 'intervention' in result
        finally:
            if token is not None:
                reset_eidolon_home_override(token)
