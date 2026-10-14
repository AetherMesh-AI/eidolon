"""Pinned direct-chat transport with one reservation per joined physical send."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from eidolon_cli.organization_executor import OrganizationExecutionError, _ToolFreeBoundary
from eidolon_cli.organization_provider_route import request_endpoint_identity


@dataclass(frozen=True)
class ModelRouteFailure:
    kind: str
    response_sha256: str


def effective_reasoning(options, expected):
    """Recognize only lossless effort encodings; never bless adapter clamping."""
    values = []
    forbidden = {'thinking', 'thinking_config', 'thinkingConfig', 'reasoning_budget', 'thinking_budget', 'budget_tokens', 'max_reasoning_tokens', 'think', 'enable_thinking', 'reasoning_enabled', 'reasoning_max_tokens'}
    def walk(value):
        if not isinstance(value, dict):
            return
        for key, item in value.items():
            if key in {'messages', 'input', 'system', 'instructions', 'tools'}:
                continue
            if key in forbidden:
                raise OrganizationExecutionError('This reasoning transport cannot prove the assigned exact reasoning mode')
            if key == 'reasoning_effort':
                values.append(item)
            elif key == 'reasoning':
                if (not isinstance(item, dict) or set(item) - {'effort', 'enabled'}
                        or ('enabled' in item and type(item['enabled']) is not bool)):
                    raise OrganizationExecutionError('This reasoning encoding is unsupported for assigned routes')
                effort = item.get('effort')
                if item.get('enabled') is False:
                    if effort not in (None, 'none'):
                        raise OrganizationExecutionError('Conflicting reasoning controls cannot authorize a send')
                    effort = 'none'
                if effort is None:
                    raise OrganizationExecutionError('The reasoning effort is implicit; an assigned route requires an exact mode')
                values.append(effort)
            elif isinstance(item, dict):
                walk(item)
    walk(options)
    # Disabled reasoning on a non-reasoning transport is represented by omission.
    # Enabled effort may never be absent, inferred or silently clamped.
    if (not values and expected != 'none') or any(value != expected for value in values):
        raise OrganizationExecutionError('The effective wire reasoning differs from the assigned exact mode; no model call was sent')
    return expected


def require_supported_route(candidate, config):
    """Reject known probing/preloading transports before resolving credentials."""
    from urllib.parse import urlsplit
    from eidolon_cli.providers import normalize_provider, host_mandated_api_mode
    from eidolon_cli.organization_provider_route import _named_entry
    from utils import base_url_host_matches
    selected = _named_entry(candidate['selectedProvider'], config)
    model = config.get('model') or {}
    model = model if isinstance(model, dict) else {}
    provider = 'custom' if selected else normalize_provider(candidate['selectedProvider'])
    mode = selected.get('api_mode') if selected else model.get('api_mode')
    endpoint = selected['endpoint'] if selected else model.get('base_url', '')
    unsupported = ('openrouter.ai', 'nousresearch.com', 'models.github.ai', 'githubcopilot.com',
                   'ollama.com', 'lmstudio.ai', 'perplexity.ai')
    parsed = urlsplit(str(endpoint))
    host = (parsed.hostname or '').lower()
    mode = host_mandated_api_mode(str(endpoint)) or mode
    if (provider not in {'custom', 'openai'} or mode not in (None, 'chat_completions')
            or (provider == 'openai' and model.get('openai_runtime') == 'codex_app_server')
            or candidate['selectedProvider'].lower() in {'ollama', 'local', 'lmstudio', 'ollama-cloud'}
            or any(base_url_host_matches(str(endpoint), name) for name in unsupported)
            or any(part in {'ollama', 'lmstudio'} for part in host.split('.'))
            or parsed.port in {11434, 1234}
            or model.get('ollama_num_ctx') is not None):
        raise OrganizationExecutionError('Assigned routes currently support generic custom/named-custom and OpenAI direct chat-completions only; discovery-backed, aggregator and local-model adapters remain on legacy routing')


def bind_runtime(candidate, kwargs):
    if kwargs['api_mode'] != 'chat_completions' or kwargs['provider'] not in {'custom', 'openai'}:
        raise OrganizationExecutionError('Assigned model routes currently require a direct chat-completions transport; subscription quota is unknown and unsupported transports are blocked')
    require_supported_route({'selectedProvider': kwargs['provider']}, {'model': {'provider': kwargs['provider'], 'base_url': kwargs['base_url']}})
    endpoint = request_endpoint_identity(kwargs['base_url'])
    return {**candidate, 'adapterProvider': kwargs['provider'],
            'endpointIdentity': hashlib.sha256(json.dumps(endpoint, separators=(',', ':')).encode()).hexdigest()}


class RoutedBoundary(_ToolFreeBoundary):
    def _supports_reasoning_extra_body(self):
        return False  # Effort is owner-declared and serialized, never discovered.

    def _organization_check(self, options=None):
        try:
            return self._check_assigned_route(options)
        except ValueError as exc:
            self._organization_route_error = True
            self._organization_intervention = str(exc)
            raise

    def _check_assigned_route(self, options=None):
        super()._organization_check(options)
        snapshot = getattr(self, '_organization_route_snapshot', None)
        if snapshot is None:
            return
        endpoint = request_endpoint_identity(self.base_url, self._client_kwargs.get('default_query'))
        digest = hashlib.sha256(json.dumps(endpoint, separators=(',', ':')).encode()).hexdigest()
        if (self.provider != snapshot['adapterProvider'] or self.model != snapshot['model']
                or self.api_mode != 'chat_completions' or digest != snapshot['endpointIdentity']):
            raise OrganizationExecutionError('The assigned provider, model or endpoint changed before sending')
        if options is not None:
            if options.get('extra_query') not in (None, {}):
                raise OrganizationExecutionError('Per-call endpoint query overrides cannot change an assigned route')
            if options.get('model') != snapshot['model']:
                raise OrganizationExecutionError('The effective wire model differs from the assigned exact model')
            caps = [options[k] for k in ('max_tokens', 'max_completion_tokens', 'max_output_tokens') if k in options]
            if not caps or any(type(cap) is not int or cap != snapshot['maxOutputTokens'] for cap in caps):
                raise OrganizationExecutionError('The effective wire output cap differs from the assigned exact cap')
            effective_reasoning(options, snapshot['reasoning'])

    def _create_request_openai_client(self, *, reason, api_kwargs=None):
        client = super()._create_request_openai_client(reason=reason, api_kwargs=api_kwargs)
        from openai import OpenAI, AzureOpenAI
        try:
            if type(client) not in {OpenAI, AzureOpenAI} or client.max_retries != 0:
                raise OrganizationExecutionError('Assigned routes require an exact direct SDK client with retries disabled')
            if getattr(getattr(client, '_client', None), 'follow_redirects', None) is not False:
                raise OrganizationExecutionError('Assigned routes require redirects disabled before sending')
            digest = hashlib.sha256(json.dumps(request_endpoint_identity(str(client.base_url), client.default_query), separators=(',', ':')).encode()).hexdigest()
            if digest != self._organization_route_snapshot['endpointIdentity']:
                raise OrganizationExecutionError('The request client changed the assigned endpoint')
        except ValueError as exc:
            self._organization_route_error = True
            self._organization_intervention = str(exc)
            self._close_request_openai_client(client, reason='organization_route_rejected')
            raise OrganizationExecutionError(str(exc)) from exc
        return client

    def _interruptible_streaming_api_call(self, api_kwargs, *, on_first_delta=None):
        options = {key: value for key, value in api_kwargs.items() if key not in {'stream', 'stream_options'}}
        options['stream'] = False
        return self._interruptible_api_call(options)

    def _interruptible_api_call(self, api_kwargs):
        from agent.chat_completion_nonstream import _NonStreamRequest
        from agent.chat_completion_helpers import _check_stale_giveup
        if getattr(self, '_organization_route_error', False) or not getattr(self, '_organization_allow_next_send', True):
            raise OrganizationExecutionError('The previous provider outcome cannot authorize another send in this attempt')
        api_kwargs = dict(api_kwargs)
        # Owner-declared direct-chat routes use an explicit standard effort
        # even for unknown model names. Do not silently default server reasoning.
        extra = api_kwargs.get('extra_body') or {}
        if ('reasoning_effort' not in api_kwargs and 'reasoning' not in api_kwargs
                and not any(k in extra for k in ('reasoning', 'reasoning_effort', 'thinking', 'thinking_config', 'thinkingConfig'))):
            api_kwargs['reasoning_effort'] = self._organization_route_snapshot['reasoning']
        self._organization_check(api_kwargs)
        _check_stale_giveup(self)
        agent = self

        class RoutedRequest(_NonStreamRequest):
            def _call(self):
                try:
                    client = self._make_client('organization_model_route')
                    agent._organization_check(self.api_kwargs)
                    # The selected client and final options are proved before the
                    # atomic dispatch + reservation transaction, immediately at send.
                    agent._organization_reserve_call(self.api_kwargs, attempts=1)
                    agent._organization_allow_next_send = False
                    response = client.chat.completions.create(**self.api_kwargs)
                    self.result['response'] = response
                    choices = getattr(response, 'choices', ())
                    choice = choices[0] if len(choices) == 1 else None
                    message = getattr(choice, 'message', None)
                    agent._organization_raw_response = getattr(message, 'content', None)
                    agent._organization_complete_response = bool(choice is not None
                        and getattr(choice, 'finish_reason', None) == 'stop'
                        and not getattr(message, 'refusal', None)
                        and not getattr(message, 'tool_calls', None)
                        and not getattr(message, 'function_call', None))
                    if not agent._organization_complete_response:
                        tool_response = (getattr(agent, '_organization_tool_execution', None) is not None
                            and getattr(choice, 'finish_reason', None) == 'tool_calls'
                            and bool(getattr(message, 'tool_calls', None)) and not getattr(message, 'refusal', None))
                        if tool_response:
                            agent._organization_allow_next_send = True
                        if not tool_response:
                            agent._organization_route_error = True
                            agent._organization_intervention = 'The assigned provider did not return a complete supported response; automatic replay is not authorized.'
                    agent._organization_physical_sends = getattr(agent, '_organization_physical_sends', 0) + 1
                except Exception as exc:
                    agent._organization_route_error = True
                    self.result['error'] = exc
                finally:
                    self.clients.close_once('request_complete' if self.result['response'] is not None else 'request_error_cleanup')

        request = RoutedRequest(self, api_kwargs)
        try:
            response = request.run()
            return response
        except BaseException:
            self._organization_route_error = True
            raise
        finally:
            if request.thread is not None:
                request.thread.join()


def invalid_json_failure(agent, raw, *, execution, passes):
    """Transport proof, not a model-authored field or semantic/schema rejection."""
    if (execution is not None or passes or not isinstance(raw, str)
            or raw != getattr(agent, '_organization_raw_response', None)
            or not getattr(agent, '_organization_complete_response', False)
            or getattr(agent, '_organization_physical_sends', 0) != 1
            or getattr(agent, '_organization_route_error', False)):
        return None
    # Code fences remain a supported legacy output format; only genuine JSON
    # decode failure qualifies. A valid but wrong schema never changes models.
    text = raw.strip()
    if text.startswith('```'):
        import re
        match = re.fullmatch(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
        if match:
            text = match[1]
    try:
        json.loads(text)
    except json.JSONDecodeError:
        return ModelRouteFailure('invalid-complete-json', hashlib.sha256(raw.encode()).hexdigest())
    return None
