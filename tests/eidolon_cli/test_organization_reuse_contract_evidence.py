"""Local evidence only: real shared turn/store, synthetic HTTP, unchanged isolation."""
import json
import threading
import uuid
from pathlib import Path

import httpx
import pytest

from eidolon_cli import config, organization_executor as executor, profile_isolation
from eidolon_cli.organization_store import OrganizationStore
from tests.organization_identity_helpers import _complete, _identity, _memory, _work_claim


def test_shared_turn_retains_identity_memory_and_requires_review(tmp_path, monkeypatch):
    from agent import conversation_loop
    from run_agent import AIAgent

    home = tmp_path / 'profile'
    home.mkdir()
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(home))
    (home / '.env').write_text('OPENAI_API_KEY=synthetic-not-a-credential\n')
    (home / 'config.yaml').write_text(json.dumps({'model': {
        'provider': 'custom', 'default': 'organization-test',
        'base_url': 'http://127.0.0.1:1/v1', 'api_mode': 'chat_completions',
        'streaming': False, 'context_length': 128000},
        'agent': {'environment_probe': False}, 'compression': {'enabled': False}}))
    config._LOAD_CONFIG_CACHE.clear()
    wire, turns = [], []
    output = {'summary': 'Compared the supplied alternatives.',
              'deliverable': 'Option A meets the supplied requirements.',
              'memory': {'facts': ['Option A meets the supplied requirements.']}}

    def synthetic_http(transport, request):
        assert request.url.host == '127.0.0.1' and request.url.port == 1
        if request.method == 'GET':
            return httpx.Response(200, json={'data': [{'id': 'organization-test'}]})
        assert request.method == 'POST' and request.url.path == '/v1/chat/completions'
        wire.append(json.loads(request.read()))
        return httpx.Response(200, json={'id': 'synthetic', 'object': 'chat.completion',
            'created': 1, 'model': 'organization-test', 'choices': [{'index': 0,
            'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps(output)}}],
            'usage': {'prompt_tokens': 30, 'completion_tokens': 20, 'total_tokens': 50}})

    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', synthetic_http)
    original = conversation_loop.run_conversation

    def observed_turn(agent, *args, **kwargs):
        assert isinstance(agent, AIAgent)
        turns.append(agent.session_id)
        return original(agent, *args, **kwargs)

    monkeypatch.setattr(conversation_loop, 'run_conversation', observed_turn)
    try:
        store = OrganizationStore(home / 'organization' / 'state.db')
        identity = _identity(store)
        objective, claim = _work_claim(store, 'first', agentId='worker-1')
        result = executor.execute(claim, store.context(claim), threading.Event())
        assert 'intervention' not in result, result
        assert len(turns) == len(wire) == 1
        assert not wire[0].get('tools')
        assert store.finish(claim, result)
        assert not store.finish(claim, result)
        assert _memory(store)['memory']['facts'] == output['memory']['facts']
        assert len(_memory(store)['recentHistory']) == 1
        with store._connect() as conn:
            review = conn.execute("SELECT agent_id,status FROM requests WHERE task_id=? AND type='request.review'",
                                  (claim['task_id'],)).fetchone()
            assert review is not None and review['agent_id'] != claim['agent_id']
        assert store.snapshot()['objectives'][0]['status'] != 'completed'
        _complete(store)  # Existing fixture supplies explicit review/integration/acceptance results.
        reopened = OrganizationStore(store.path)
        assert _identity(reopened) == identity
        _, next_claim = _work_claim(reopened, 'second', agentId='worker-1')
        context = reopened.context(next_claim)
        assert context['agent']['identityId'] == identity['identityId']
        assert context['agentContext']['memory']['facts'] == output['memory']['facts']
        assert context['agentContext']['recentHistory'][0]['objectiveId'] == objective['id']
    finally:
        config._LOAD_CONFIG_CACHE.clear()


@pytest.mark.parametrize('kind', ['work.inspect', 'work.edit'])
def test_isolated_tool_assignment_rejected_before_runtime_or_tool_scope(tmp_path, monkeypatch, kind):
    from eidolon_cli import runtime_provider, organization_tool_executor
    home = tmp_path / 'isolated'
    home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    marker = {'version': 1, 'state': 'ready', 'profile': 'org-' + 'a' * 32,
              'operation': 'b' * 64, 'organizationId': str(uuid.uuid4()), 'ownerId': str(uuid.uuid4())}
    (home / profile_isolation.MARKER).write_text(json.dumps(marker))
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'notes.txt').write_text('Synthetic source only.')
    context = {'toolPolicy': {'tools': ['read_file'], 'readRoots': [str(source)],
                             'maxToolCalls': 1, 'maxResultChars': 1000},
               'recordToolStart': lambda *a: None, 'recordToolFinish': lambda *a: None,
               'requestToolReceipts': lambda: [], 'resolveWorkspaceSource': lambda *a: None}
    def forbidden(*args, **kwargs):
        pytest.fail('Isolation rejection must precede runtime resolution and tool-scope construction')
    monkeypatch.setattr(runtime_provider, 'resolve_runtime_provider', forbidden)
    monkeypatch.setattr(organization_tool_executor, 'tool_execution', forbidden)
    with pytest.raises(profile_isolation.ProfileIsolationError, match='tool-free organization work only'):
        profile_isolation.require_tool_free_execution(kind=kind, context=context)
    result = executor.execute({'type': kind}, context, threading.Event())
    assert result['intervention'] == ("Model execution failed (ProfileIsolationError). "
        "Check the current profile's provider and model settings, then retry.")
    assert json.loads((home / profile_isolation.MARKER).read_text()) == marker
    assert (source / 'notes.txt').read_text() == 'Synthetic source only.'
