"""Improve uses the shared stateless request with a pinned, non-fallback route."""
import json
from types import SimpleNamespace

import pytest

from agent import auxiliary_client as aux
from agent.oneshot import run_oneshot


@pytest.fixture
def wire(monkeypatch):
    calls = []
    client = SimpleNamespace(base_url='https://fixture.invalid/v1')
    monkeypatch.setattr(aux, '_get_cached_client', lambda provider, model, **kw: (client, model))
    def send(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content='{"suggestion":"Clear weekly report","assumptions":[]}',
            reasoning=None, reasoning_content=None, reasoning_details=None), finish_reason='stop')])
    client.chat = SimpleNamespace(completions=SimpleNamespace(create=send))
    return calls


def improve(**kw):
    return run_oneshot(template='improve_team_input', variables={
        'field': 'jobs', 'jobs': 'Report', 'goals': 'Weekly', 'namingStyle': ''},
        strict_runtime=True, **kw)


@pytest.mark.parametrize('provider', ['openai-codex', 'openrouter'])
def test_exact_route_one_text_request(wire, provider):
    assert json.loads(improve(main_runtime={'provider': provider, 'model': 'fixture-model'}))['suggestion']
    assert len(wire) == 1
    assert wire[0]['model'] == 'fixture-model'
    assert not wire[0].get('tools')
    messages = wire[0]['messages']
    assert [m['role'] for m in messages] == ['system', 'user']
    assert json.loads(messages[1]['content'])['jobs'] == 'Report'


@pytest.mark.parametrize('runtime', [None, {}, {'provider': 'auto', 'model': 'x'}, {'provider': 'openrouter'}])
def test_missing_explicit_route_never_sends(wire, runtime):
    with pytest.raises(ValueError):
        improve(main_runtime=runtime)
    assert wire == []


def test_unavailable_provider_never_falls_back(monkeypatch, wire):
    monkeypatch.setattr(aux, '_get_cached_client', lambda *a, **kw: (None, None))
    monkeypatch.setattr(aux, '_try_configured_fallback_for_unavailable_client',
                        lambda *a, **kw: pytest.fail('fallback attempted'))
    with pytest.raises(RuntimeError, match='no fallback'):
        improve(main_runtime={'provider': 'openai-codex', 'model': 'fixture-model'})
    assert wire == []


def test_send_failure_is_not_retried_or_rerouted(monkeypatch, wire):
    calls = []
    def fail(*a, **kw):
        calls.append(True)
        raise ConnectionError('fixture failed')
    monkeypatch.setattr(aux, '_relay_sync_completion', fail)
    with pytest.raises(ConnectionError):
        improve(main_runtime={'provider': 'openai-codex', 'model': 'fixture-model'})
    assert calls == [True]


@pytest.mark.parametrize('variables', [
    {'field': 'jobs', 'jobs': 'x' * 4001}, {'field': 'jobs', 'jobs': ''},
    {'field': 'goals', 'goals': 'x', 'namingStyle': 'x' * 501},
])
def test_boundaries_reject_before_send(wire, variables):
    with pytest.raises(ValueError):
        run_oneshot(template='improve_team_input', variables=variables, strict_runtime=True)
    assert wire == []


def test_resolved_provider_replacement_is_rejected(monkeypatch, wire):
    client = SimpleNamespace(_hermes_aux_effective_provider='openrouter')
    monkeypatch.setattr(aux, '_get_cached_client', lambda *a, **kw: (client, 'fixture-model'))
    with pytest.raises(RuntimeError, match='no fallback'):
        improve(main_runtime={'provider': 'openai-codex', 'model': 'fixture-model'})
    assert wire == []


def test_selected_model_replacement_is_rejected(monkeypatch, wire):
    monkeypatch.setattr(aux, '_get_cached_client', lambda *a, **kw: (SimpleNamespace(), 'different-model'))
    with pytest.raises(RuntimeError, match='no replacement'):
        improve(main_runtime={'provider': 'openai-codex', 'model': 'fixture-model'})
    assert wire == []


def test_credit_error_does_not_retry_with_smaller_output(monkeypatch):
    calls = []
    def fail(**kw):
        calls.append(kw)
        raise RuntimeError('fixture budget failure')
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fail)),
                             base_url='https://fixture.invalid')
    monkeypatch.setattr(aux, '_get_cached_client', lambda *a, **kw: (client, 'fixture-model'))
    monkeypatch.setattr(aux, '_affordable_max_tokens_from_error', lambda exc: 10)
    with pytest.raises(RuntimeError, match='fixture budget'):
        improve(main_runtime={'provider': 'openrouter', 'model': 'fixture-model'})
    assert len(calls) == 1


@pytest.mark.parametrize('error', [
    'stream not supported',
    'AccessDeniedException: not authorized to perform bedrock:InvokeModelWithResponseStream',
])
def test_anthropic_improve_stream_rejection_has_one_physical_attempt(monkeypatch, error):
    from unittest.mock import Mock

    messages = Mock()
    messages.stream.side_effect = RuntimeError(error)
    client = aux.AnthropicAuxiliaryClient(
        SimpleNamespace(messages=messages), 'fixture-model', 'synthetic', 'https://fixture.invalid')
    monkeypatch.setattr(aux, '_get_cached_client', lambda *a, **kw: (client, 'fixture-model'))
    with pytest.raises(RuntimeError, match=error):
        improve(main_runtime={'provider': 'anthropic', 'model': 'fixture-model'})
    assert messages.stream.call_count == 1
    messages.create.assert_not_called()
    assert '_allow_stream_retry' not in messages.stream.call_args.kwargs


def test_anthropic_default_still_recovers_from_unavailable_stream():
    from unittest.mock import Mock
    from agent.anthropic_adapter import create_anthropic_message

    messages = Mock()
    messages.stream.side_effect = RuntimeError('stream not supported')
    result = create_anthropic_message(SimpleNamespace(messages=messages), {'model': 'fixture-model'})
    assert result is messages.create.return_value
    assert messages.stream.call_count == 1
    assert messages.create.call_count == 1
