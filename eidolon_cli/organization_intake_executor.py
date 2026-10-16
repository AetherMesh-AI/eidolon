"""Bounded single-send intake inference through the configured profile route."""
import json
import threading
import time

from eidolon_cli.organization_executor import OrganizationExecutionError, _create_agent, _guard_plugin_integrations, _runtime_kwargs
from eidolon_cli.organization_intake_contract import SYSTEM
from eidolon_cli.organization_owner_chat_executor import _OwnerChatBoundary, readiness_reason

INPUT_TOKENS = 24000
OUTPUT_TOKENS = 4000
TIMEOUT_SECONDS = 120


def execute(prompt, *, reserve_model_call, cancel):
    if not callable(reserve_model_call):
        raise ValueError('Intake inference requires a durable physical-send reservation')
    if cancel.is_set():
        raise OrganizationExecutionError('Intake cancelled before dispatch')
    reason = readiness_reason({})
    if reason:
        raise OrganizationExecutionError(reason)
    content = json.dumps(prompt, ensure_ascii=False, allow_nan=False)
    agent, watcher = None, None
    done = threading.Event()
    sent = False
    try:
        deadline = time.monotonic() + TIMEOUT_SECONDS
        _guard_plugin_integrations()
        kwargs = _runtime_kwargs({'agent': {}, 'maxOutputTokens': OUTPUT_TOKENS}, TIMEOUT_SECONDS,
                                 require_exact_provider=True)
        if kwargs['api_mode'] != 'chat_completions':
            raise OrganizationExecutionError('Intake requires a supported direct single-send chat-completions route')
        kwargs.update(ephemeral_system_prompt=SYSTEM, credential_pool=None)
        from eidolon_cli.organization_evidence import CONTEXT_RESERVE_TOKENS, prompt_input_bound, require_fits, input_limits
        require_fits(prompt_input_bound(content, SYSTEM) + CONTEXT_RESERVE_TOKENS, INPUT_TOKENS)
        agent = _create_agent(kwargs, cancel, deadline, boundary=_OwnerChatBoundary)
        from eidolon_cli.organization_provider_route import request_endpoint_identity
        agent._owner_chat_route = {'provider': kwargs['provider'], 'model': kwargs['model'],
                                  'endpoint': request_endpoint_identity(kwargs['base_url'])}
        _, input_limit, _ = input_limits(agent, {'maxContextTokens': INPUT_TOKENS + OUTPUT_TOKENS,
                                               'maxOutputTokens': OUTPUT_TOKENS})
        require_fits(prompt_input_bound(content, SYSTEM), input_limit)
        agent._disable_streaming = True
        agent._organization_prompt = content
        agent._organization_input_limit = input_limit
        agent._organization_output_limit = OUTPUT_TOKENS

        def reserve(**values):
            nonlocal sent
            agent._organization_check()
            if sent:
                raise OrganizationExecutionError('Intake cannot retry a physical model call')
            reserve_model_call(**values, selected_provider=kwargs.get('requested_provider') or kwargs['provider'])
            sent = True
        agent._organization_reserve = reserve

        def watch():
            while not done.wait(0.05):
                if cancel.is_set() or time.monotonic() >= deadline:
                    agent.interrupt('Intake cancelled or timed out', hard_cancel=True)
                    return
        watcher = threading.Thread(target=watch, name='intake-cancellation', daemon=True)
        watcher.start()
        _guard_plugin_integrations()
        agent._organization_check()
        result = agent.run_conversation(user_message=content)
        if cancel.is_set() or time.monotonic() >= deadline:
            raise OrganizationExecutionError('Intake cancelled or timed out; no response accepted')
        if agent._organization_intervention:
            raise OrganizationExecutionError(agent._organization_intervention)
        if not sent or not isinstance(result, dict) or result.get('completed') is False or any(
                result.get(key) for key in ('failed', 'partial', 'interrupted')):
            raise OrganizationExecutionError('Intake provider did not return a complete reserved response')
        return {'raw': result.get('final_response'), 'usage': {
            'inputTokens': getattr(agent, 'session_prompt_tokens', None),
            'outputTokens': getattr(agent, 'session_completion_tokens', None)}}
    finally:
        done.set()
        if watcher is not None:
            watcher.join(timeout=0.2)
        if agent is not None:
            agent.close()
