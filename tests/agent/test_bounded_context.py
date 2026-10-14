"""Explicit bounded context is opt-in and never weakens ordinary init policy."""
from types import SimpleNamespace
import pytest

from agent.bounded_context import BoundedContextWindow, explicit_context_window
from agent.agent_init import _enforce_minimum_context, _configure_ollama_num_ctx


@pytest.mark.parametrize('value', [True, 0, 4095, 2_000_001, '32768'])
def test_invalid_explicit_window_is_never_adopted(value):
    with pytest.raises(ValueError):
        BoundedContextWindow(value)


def test_small_bounded_caller_avoids_discovery_while_legacy_minimum_is_unchanged(monkeypatch):
    plain = SimpleNamespace(provider='custom', model='fixture', _config_context_length=None,
                            context_compressor=SimpleNamespace(context_length=32768))
    assert explicit_context_window(plain) is None
    with pytest.raises(ValueError, match='below the minimum'):
        _enforce_minimum_context(plain)
    bounded = SimpleNamespace(**vars(plain), _bounded_context_window=BoundedContextWindow(32768))
    _enforce_minimum_context(bounded)
    from agent import agent_init
    def unexpected(*args, **kwargs):
        raise AssertionError('A bounded caller must not probe local model metadata')
    monkeypatch.setattr(agent_init, 'query_ollama_num_ctx', unexpected)
    _configure_ollama_num_ctx(bounded, {}, None)
    assert bounded._ollama_num_ctx is None
    with pytest.raises(ValueError, match='differs'):
        _configure_ollama_num_ctx(bounded, {'ollama_num_ctx': 4096}, None)
