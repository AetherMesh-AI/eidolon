"""Served profile Improve dispatch uses ordinary text helper without team admission."""
import json
from queue import Queue
from types import SimpleNamespace

import pytest

import tui_gateway.server as server
from agent import oneshot
from eidolon_constants import get_eidolon_home


@pytest.fixture
def served(tmp_path, monkeypatch):
    home = tmp_path / 'profile'
    home.mkdir()
    (home / 'config.yaml').write_text('model:\n  provider: openai-codex\n  default: fixture-model\n')
    monkeypatch.setattr(server, '_eidolon_home', home)
    return home


def call(variables=None, transport=None, **extra):
    replies = Queue()
    transport = transport or SimpleNamespace(closed=False)
    transport.write = replies.put
    result = server.dispatch({'jsonrpc': '2.0', 'id': 9, 'method': 'llm.oneshot', 'params': {
        'template': 'improve_team_input', 'variables': variables or {'field': 'jobs', 'jobs': 'Report'},
        **extra}}, transport=transport)
    return result if result is not None else replies.get(timeout=10)


def test_selected_profile_works_without_conversation_or_organization(served, monkeypatch):
    seen = []
    def reply(**kw):
        seen.append((get_eidolon_home(), kw))
        return json.dumps({'suggestion': 'Write a report.', 'assumptions': []})
    monkeypatch.setattr(oneshot, 'run_oneshot', reply)
    result = call()
    assert 'result' in result, result
    assert seen[0][0] == served
    assert seen[0][1]['main_runtime'] == {'provider': 'openai-codex', 'model': 'fixture-model'}
    assert seen[0][1]['strict_runtime'] is True
    assert list(served.rglob('*.db')) == []


def test_invalid_input_rejects_before_sender(served, monkeypatch):
    monkeypatch.setattr(oneshot, 'run_oneshot', lambda **kw: pytest.fail('must not send'))
    assert call({'field': 'jobs', 'jobs': 'x' * 4001})['error']['code'] == 4032
    assert call(session_id='foreign')['error']['code'] == 4001
    assert call(provider='openrouter')['error']['code'] == -32602


def test_disconnect_discards_result(served, monkeypatch):
    transport = SimpleNamespace(closed=False)
    def reply(**kw):
        transport.closed = True
        return '{"suggestion":"Discard me","assumptions":[]}'
    monkeypatch.setattr(oneshot, 'run_oneshot', reply)
    assert call(transport=transport)['error']['code'] == 4001


def test_provider_failure_is_actionable(served, monkeypatch):
    def fail(**kw):
        raise RuntimeError('unavailable')
    monkeypatch.setattr(oneshot, 'run_oneshot', fail)
    result = call()
    assert result['error']['code'] == 5030
    assert 'no fallback provider was used' in result['error']['message']


def test_live_session_route_and_generation_are_pinned(served, monkeypatch):
    transport = SimpleNamespace(closed=False)
    agent = SimpleNamespace(provider='openrouter', model='session-model', base_url='https://fixture.invalid',
                            api_key='test-only', api_mode='chat_completions', auth_mode='api_key')
    session = {'agent': agent, 'transport': transport, 'profile_home': str(served)}
    monkeypatch.setitem(server._sessions, 'fixture-session', session)
    seen = []
    def reply(**kw):
        seen.append(True)
        assert kw['main_runtime']['model'] == 'session-model'
        assert kw['main_runtime']['provider'] == 'openrouter'
        server._sessions['fixture-session'] = dict(session)
        return '{"suggestion":"Late result","assumptions":[]}'
    monkeypatch.setattr(oneshot, 'run_oneshot', reply)
    assert call(transport=transport, session_id='fixture-session')['error']['code'] == 4001

    assert seen == [True]
