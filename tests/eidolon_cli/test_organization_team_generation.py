import json
import threading
from types import SimpleNamespace

import pytest

from eidolon_cli import organization_team_generation as generation
from eidolon_cli.organization_executor import OrganizationExecutionError

TEAM = {'executive': {'name': 'Ash'}, 'members': []}

@pytest.mark.parametrize('raw', ['{}', '[]', '```json\n{}\n```', '{"executive":{},"executive":{},"members":[]}', '{"executive":NaN,"members":[]}', pytest.param('x' * 64001, id='oversized-response')])
def test_invalid_proposal_rejected(raw):
    with pytest.raises(OrganizationExecutionError):
        generation.parse_proposal(raw)


def test_schema_semantics_remain_caller_owned():
    assert generation.parse_proposal(json.dumps(TEAM)) == TEAM


def fixture_agent(monkeypatch, *, output=None, send=True, twice=False, partial=False):
    calls = []
    closed = []
    class Agent:
        _organization_intervention = None
        def _organization_check(self): pass
        def close(self): closed.append(True)
        def interrupt(self, *args, **kwargs): pass
        def run_conversation(self, user_message):
            calls.append(json.loads(user_message))
            if send:
                self._organization_reserve(provider='custom', model='test', input_limit=3000, output_limit=8000)
            if twice:
                self._organization_reserve(provider='custom', model='test', input_limit=3000, output_limit=8000)
            return {'completed': not partial, 'final_response': output or json.dumps(TEAM)}
    monkeypatch.setattr(generation, 'readiness_reason', lambda _: None)
    monkeypatch.setattr(generation, '_guard_plugin_integrations', lambda: None)
    monkeypatch.setattr(generation, '_runtime_kwargs', lambda *a, **k: {'api_mode': 'chat_completions', 'provider': 'custom', 'requested_provider': 'test-endpoint', 'model': 'test', 'base_url': 'http://127.0.0.1:1234/v1'})
    monkeypatch.setattr(generation, '_create_agent', lambda *a, **k: Agent())
    import eidolon_cli.organization_evidence as evidence
    monkeypatch.setattr(evidence, 'input_limits', lambda *a: (None, 16000, None))
    return calls, closed


def test_reservation_precedes_complete_proposal(monkeypatch, tmp_path):
    calls, closed = fixture_agent(monkeypatch)
    reserved = []
    result = generation.generate(tmp_path, 'Build games', 'Persistent team', 'Ash and Ember', reserve_model_call=lambda **k: reserved.append(k))
    assert result == TEAM
    assert len(reserved) == 1
    assert reserved[0]['selected_provider'] == 'test-endpoint'
    assert calls[0]['namingStyle'] == 'Ash and Ember'
    assert closed == [True]

@pytest.mark.parametrize('kwargs', [{'send': False}, {'twice': True}, {'partial': True}, {'output': '{}'}])
def test_no_retry_or_unreserved_success(monkeypatch, tmp_path, kwargs):
    calls, closed = fixture_agent(monkeypatch, **kwargs)
    reserved = []
    with pytest.raises(OrganizationExecutionError):
        generation.generate(tmp_path, 'Jobs', '', '', reserve_model_call=lambda **k: reserved.append(k))
    assert len(calls) == 1
    assert len(reserved) <= 1
    assert closed == [True]


def test_cancelled_before_send(monkeypatch, tmp_path):
    calls, _ = fixture_agent(monkeypatch)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(OrganizationExecutionError):
        generation.generate(tmp_path, 'Jobs', '', '', reserve_model_call=lambda **k: pytest.fail('send'), cancel=cancel)
    assert calls == []


def test_requires_reservation(tmp_path):
    with pytest.raises(ValueError):
        generation.generate(tmp_path, 'Jobs', '', '', reserve_model_call=None)

@pytest.fixture
def loopback_provider(tmp_path, monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from eidolon_cli import config
    received = []
    mode = {'status': 200, 'output': json.dumps(TEAM)}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers()
            self.wfile.write(b'{"data":[{"id":"team-fixture"}]}')
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(mode['status']); self.send_header('Content-Type', 'application/json'); self.end_headers()
            payload = {'id':'team', 'object':'chat.completion', 'model':'team-fixture',
                       'choices':[{'index':0,'message':{'role':'assistant','content':mode['output']},'finish_reason':'stop'}],
                       'usage':{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20}}
            if mode['status'] != 200: payload = {'error':{'message':'fixture failure'}}
            self.wfile.write(json.dumps(payload).encode())
        def log_message(self, *_): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    home = tmp_path / 'profile'; home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setenv('OPENAI_API_KEY', 'local-fixture-only')
    (home / 'config.yaml').write_text(json.dumps({
        'model': {'provider':'custom','default':'team-fixture','base_url':f'http://127.0.0.1:{server.server_port}/v1',
                  'api_mode':'chat_completions','context_length':128000},
        'context':{'memory_trim':{'enabled':False}},'agent':{'environment_probe':False},'compression':{'enabled':False}}))
    (home / 'SOUL.md').write_text('PRIVATE SOUL MUST NOT LEAK')
    config._LOAD_CONFIG_CACHE.clear()
    try: yield home, received, mode
    finally:
        server.shutdown(); server.server_close(); thread.join(3)
        config._LOAD_CONFIG_CACHE.clear()


def test_real_http_generation_one_reserved_send_no_tools(loopback_provider):
    home, received, _ = loopback_provider
    reservations = []
    assert generation.generate(home, 'Build tools', 'Keep context', 'Ash and Ember', reserve_model_call=lambda **k: reservations.append(k)) == TEAM
    assert len(received) == len(reservations) == 1
    wire = received[0]
    assert not wire.get('tools') and not wire.get('functions')
    assert wire.get('max_tokens', wire.get('max_completion_tokens')) <= generation.OUTPUT_TOKENS
    assert 'PRIVATE SOUL' not in json.dumps(wire)
    assert 'EIDOLON_STARTER_TEAM_V1' in json.dumps(wire)


def test_real_http_failure_is_not_retried(loopback_provider):
    home, received, mode = loopback_provider
    mode['status'] = 503
    reservations = []
    with pytest.raises(OrganizationExecutionError):
        generation.generate(home, 'Build tools', '', '', reserve_model_call=lambda **k: reservations.append(k))
    assert len(received) == len(reservations) == 1


@pytest.mark.parametrize('raw', [
    '{}', '[]', '```json\n{}\n```',
    '{"suggestion":"first","suggestion":"second","assumptions":[]}',
    '{"suggestion":"text","assumptions":[NaN]}',
    '{"suggestion":"text","assumptions":[1e999]}',
    json.dumps({'suggestion': 'text', 'assumptions': [], 'team': {}}),
    json.dumps({'suggestion': ' ', 'assumptions': []}),
    json.dumps({'suggestion': 'text', 'assumptions': ['']}),
    json.dumps({'suggestion': 'text', 'assumptions': ['x'] * 6}),
    json.dumps({'suggestion': 'text', 'assumptions': ['x' * 301]}),
    pytest.param(json.dumps({'suggestion': 'x' * 4001, 'assumptions': []}), id='oversized-suggestion'),
    pytest.param(' ' * 64001, id='oversized-response'),
])
def test_improvement_requires_bounded_exact_json(raw):
    with pytest.raises(generation.TeamInputRejected):
        generation.parse_improvement(raw)


def test_real_http_improvement_only_receives_current_form_without_tools(loopback_provider):
    home, received, mode = loopback_provider
    suggestion = {'suggestion': 'Research useful tools and write a concise report.',
                  'assumptions': ['The owner wants a written deliverable.']}
    mode['output'] = json.dumps(suggestion)
    reservations = []
    assert generation.improve(home, 'jobs', 'Research tools', '', 'Short names',
        reserve_model_call=lambda **values: reservations.append(values)) == suggestion
    assert len(received) == len(reservations) == 1
    wire = received[0]
    assert not wire.get('tools') and not wire.get('functions')
    assert wire.get('max_tokens', wire.get('max_completion_tokens')) <= generation.OUTPUT_TOKENS
    assert 'PRIVATE SOUL' not in json.dumps(wire)
    messages = wire['messages']
    assert [message['role'] for message in messages] == ['system', 'user']
    assert messages[0]['content'] == generation.IMPROVE_SYSTEM.strip()
    assert json.loads(messages[-1]['content']) == {
        'field': 'jobs', 'jobs': 'Research tools', 'goals': '', 'namingStyle': 'Short names'}


@pytest.mark.parametrize('field,jobs,goals', [('jobs', '', 'Goal'), ('goals', 'Job', ''), ('namingStyle', 'Job', 'Goal')])
def test_improvement_rejects_missing_selected_input_before_any_send(tmp_path, monkeypatch, field, jobs, goals):
    calls, _ = fixture_agent(monkeypatch)
    with pytest.raises(ValueError):
        generation.improve(tmp_path, field, jobs, goals, '', reserve_model_call=lambda **k: pytest.fail('send'))
    assert not calls
