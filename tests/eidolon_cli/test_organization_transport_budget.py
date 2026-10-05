"""Physical provider caps cannot be inflated by adapters or merged body controls."""
import sys
import threading
import time
from types import ModuleType, SimpleNamespace

import pytest

from eidolon_cli import organization_executor as executor
from eidolon_cli.organization_evidence import CONTEXT_RESERVE_TOKENS, wire_input_bound


def boundary():
    agent = executor._ToolFreeBoundary()
    agent.provider, agent.model, agent.api_mode = 'custom', 'bounded-fixture', 'chat_completions'
    agent.base_url, agent.request_overrides = 'http://127.0.0.1:1/v1', {}
    agent.tools, agent.valid_tool_names = [], set()
    agent._organization_cancel, agent._organization_deadline = threading.Event(), time.monotonic() + 30
    agent._organization_intervention = None
    agent._organization_prompt = 'Complete exact input'
    agent._organization_input_limit, agent._organization_output_limit = 10000, 8000
    reservations = []
    agent._organization_reserve = lambda **entry: reservations.append(entry)
    return agent, reservations


def test_effective_wire_budget_rejects_merged_overrides_fanout_uncapped_and_native_inflation(monkeypatch):
    wire = {'model': 'bounded-fixture', 'max_tokens': 8000,
            'messages': [{'role': 'user', 'content': 'Complete exact input'}]}
    agent, rows = boundary()
    agent._organization_check(wire); agent._organization_reserve_call(wire)
    assert rows[0]['input_limit'] == wire_input_bound(wire) + CONTEXT_RESERVE_TOKENS
    assert rows[0]['input_limit'] < agent._organization_input_limit
    assert rows[0]['output_limit'] == 8000
    for changes in ({'extra_body': {'model': 'different-expensive-model'}},
                    {'extra_body': {'max_tokens': 100000}}, {'n': 2}, {'extra_body': {'best_of': 2}},
                    {'max_tokens': 16000}, {'max_tokens': None},
                    {'messages': [{'role': 'user', 'content': 'Only a summary remains'}]},
                    {'extra_body': {'messages': [{'role': 'user', 'content': 'Override evidence'}]}}):
        agent, rows = boundary()
        changed = {**wire, **changes}
        if changed.get('max_tokens') is None:
            changed.pop('max_tokens')
        with pytest.raises(executor.OrganizationExecutionError):
            agent._organization_check(changed); agent._organization_reserve_call(changed)
        assert not rows

    from agent.anthropic_adapter import build_anthropic_kwargs
    normalized = build_anthropic_kwargs('claude-sonnet-4.6', wire['messages'], [], 8000, {'enabled': False})
    executor._guard_wire_controls(normalized, 'claude-sonnet-4.6', api_mode='anthropic_messages')
    with pytest.raises(executor.OrganizationExecutionError, match='alternate route'):
        executor._guard_wire_controls({**normalized, 'model': 'different-model'}, 'claude-sonnet-4.6', api_mode='anthropic_messages')

    from agent.gemini_native_adapter import GeminiNativeClient
    import httpx
    calls = []
    client = GeminiNativeClient(api_key='local-only', http_client=httpx.Client(transport=httpx.MockTransport(lambda request: calls.append(request))))
    class Base:
        def _create_request_openai_client(self, **kwargs):
            return client
    class Agent(executor._ToolFreeBoundary, Base):
        pass
    native = Agent()
    native._organization_reserve = lambda **_: None
    native._organization_output_limit = 8000
    try:
        with pytest.raises(executor.OrganizationExecutionError, match='Gemini thinking'):
            native._create_request_openai_client(reason='test', api_kwargs={
                **wire, 'extra_body': {'thinking_config': {'thinkingLevel': 'high'}}})
        assert not calls
    finally:
        client.close()

    # Only trusted ledger callback errors may be persisted verbatim. Arbitrary
    # SDK/setup ValueErrors often contain authenticated URLs or headers.
    def bad_setup(*args):
        raise ValueError('https://private-provider-token@example.invalid/v1')
    monkeypatch.setattr(executor, '_guard_plugin_integrations', lambda **_: None)
    monkeypatch.setattr(executor, '_runtime_kwargs', bad_setup)
    rejected = executor.execute({'type': 'work.draft'}, {}, threading.Event())
    assert 'ValueError' in rejected['intervention']
    assert 'private-provider-token' not in rejected['intervention']


def test_bedrock_has_local_one_attempt_sdk_policy_and_only_one_explicit_cache_retry(monkeypatch):
    from agent.chat_completion_nonstream import _NonStreamRequest
    policy, calls, closed = [], [], []
    class Config:
        def __init__(self, **kwargs):
            policy.append(kwargs)
    package, module = ModuleType('botocore'), ModuleType('botocore.config')
    module.Config = Config
    monkeypatch.setitem(sys.modules, 'botocore', package)
    monkeypatch.setitem(sys.modules, 'botocore.config', module)
    class Client:
        def converse(self, **options):
            calls.append(options)
            if len(calls) == 1:
                raise ValueError('Synthetic cache marker rejection')
            return {'complete': True}
        def close(self):
            closed.append(True)
    def create_client(service, *, region_name, config):
        assert service == 'bedrock-runtime' and region_name == 'test-region'
        return Client()
    monkeypatch.setitem(sys.modules, 'agent.bedrock_adapter', SimpleNamespace(
        _require_boto3=lambda: SimpleNamespace(client=create_client),
        normalize_converse_response=lambda value: value,
        recover_from_cache_point_rejection=lambda exc, options: {**options, 'cacheRetry': True}))
    def init(self, agent, options):
        self.api_kwargs = options
        self.result = {'response': None, 'error': None}
    monkeypatch.setattr(_NonStreamRequest, '__init__', init)
    agent = SimpleNamespace(_organization_deadline=time.monotonic() + 20,
                            _organization_check=lambda options: None)
    request = executor._bounded_bedrock_request(agent, {'modelId': 'bounded-fixture', '__bedrock_region__': 'test-region'})
    request._call()
    assert request.result == {'response': {'complete': True}, 'error': None}
    assert len(calls) == 2 and calls[0]['modelId'] == calls[1]['modelId']
    assert policy[0]['retries'] == {'mode': 'standard', 'total_max_attempts': 1}
    assert closed == [True]
