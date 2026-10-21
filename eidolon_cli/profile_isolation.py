"""Opt-in organization workspaces: owned credentials and a bounded direct API lane.

This is an application credential boundary, not an OS or filesystem sandbox.
Existing profiles without an isolation marker/registration keep their old policy.
"""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
import re
import uuid


VERSION = 1
MARKER = '.organization-isolation.json'
WORKSPACES = '.organization-workspaces'
PROVIDER = 'openai-api'
ENDPOINT = 'https://api.openai.com/v1'
MODE = 'profile-local-openai-api'
_FIELDS = {'version', 'state', 'profile', 'operation', 'organizationId', 'ownerId'}


class ProfileIsolationError(RuntimeError):
    pass


def local_path(path: Path) -> Path:
    """Reject symlinks, including ancestors, instead of following a foreign home."""
    path = Path(path).absolute()
    try:
        if path.resolve() != path or path.is_symlink():
            raise ProfileIsolationError('Workspace storage must not contain symlinks')
    except (OSError, RuntimeError) as exc:
        raise ProfileIsolationError('Workspace storage is unavailable') from exc
    return path


def _json_file(path: Path):
    path = local_path(path)
    try:
        if path.stat().st_size > 16384:
            raise ProfileIsolationError('Workspace isolation record is too large')
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        raise ProfileIsolationError('Workspace isolation record is unreadable') from exc


def _present(path: Path) -> bool:
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ProfileIsolationError('Workspace isolation record is inaccessible') from exc


def isolation_registration(home: Path) -> Path | None:
    home = Path(home).absolute()
    if home.parent.name == 'profiles':
        return home.parent.parent / WORKSPACES / 'profiles' / (home.name + '.json')
    return None


def read_isolation(home: Path | None = None) -> dict | None:
    from eidolon_constants import get_eidolon_home
    home = Path(home or get_eidolon_home()).absolute()
    marker = home / MARKER
    registration = isolation_registration(home)
    # lstat-style existence includes dangling symlinks: corruption cannot opt out.
    marked = _present(marker)
    if registration is not None and _present(registration.parent.parent):
        local_path(registration)
    registered = registration is not None and _present(registration)
    if not marked and not registered:
        return None
    local_path(home)
    if not marked:
        raise ProfileIsolationError('Registered workspace is missing its isolation marker')
    value = _json_file(marker)
    if (not isinstance(value, dict) or set(value) != _FIELDS
            or type(value['version']) is not int or value['version'] != VERSION
            or not isinstance(value['state'], str)
            or value['state'] not in {'staging', 'ready'}
            or not isinstance(value['profile'], str)
            or not re.fullmatch(r'org-[0-9a-f]{32}', value['profile'])
            or not isinstance(value['operation'], str)
            or not re.fullmatch(r'[0-9a-f]{64}', value['operation'])):
        raise ProfileIsolationError('Unsupported workspace isolation marker')
    for field in ('organizationId', 'ownerId'):
        ident = value[field]
        try:
            valid = ident is None if value['state'] == 'staging' else str(uuid.UUID(ident)) == ident
        except (ValueError, TypeError, AttributeError):
            valid = False
        if not valid:
            raise ProfileIsolationError('Invalid workspace isolation identity')
    if home.parent.name == 'profiles':
        if value['profile'] != home.name or not registered or _json_file(registration) != value:
            raise ProfileIsolationError('Workspace isolation registration does not match')
    return value


def verify_isolation(home: Path | None = None) -> dict | None:
    from eidolon_constants import get_eidolon_home
    from eidolon_cli.organization_metadata import read_descriptor
    home = Path(home or get_eidolon_home()).absolute()
    value = read_isolation(home)
    if value is None:
        return None
    if value['state'] != 'ready':
        raise ProfileIsolationError('Workspace creation is not complete')
    if home.parent.name != 'profiles':
        raise ProfileIsolationError('Only a published workspace may execute')
    descriptor = read_descriptor(local_path(home / 'organization' / 'state.db'))
    metadata = descriptor.get('metadata') or {}
    if (descriptor['state'] != 'ready' or metadata.get('organizationId') != value['organizationId']
            or (metadata.get('owner') or {}).get('ownerId') != value['ownerId']):
        raise ProfileIsolationError('Workspace ledger does not match its isolation identity')
    if home.parent.name == 'profiles':
        receipt = _json_file(home.parent.parent / WORKSPACES / 'operations' / value['operation'] / 'operation.json')
        result = receipt.get('result') if isinstance(receipt, dict) else None
        payload = receipt.get('payload') if isinstance(receipt, dict) else None
        try:
            digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        except (ValueError, TypeError, RecursionError) as exc:
            raise ProfileIsolationError('Workspace creation receipt is invalid') from exc
        if (not isinstance(result, dict) or receipt.get('version') != VERSION
                or receipt.get('inputHash') != digest or receipt.get('profile') != value['profile']
                or result.get('organizationId') != value['organizationId']
                or result.get('ownerId') != value['ownerId'] or result.get('activated') is not False
                or result.get('credentialMode') != MODE or result.get('isolationVersion') != VERSION):
            raise ProfileIsolationError('Workspace creation receipt does not match its isolation identity')
    return value


def isolation_view(home: Path) -> dict | None:
    value = verify_isolation(home)
    return ({'isolationVersion': VERSION, 'credentialMode': MODE,
             'profile': value['profile'], 'organizationId': value['organizationId'],
             'ownerId': value['ownerId'], 'supportedProviders': [PROVIDER],
             'executionMode': 'tool-free-organization'} if value else None)


def profile_secrets(home: Path) -> dict[str, str]:
    from agent.secret_scope import load_env_file
    return load_env_file(local_path(Path(home) / '.env'))


def isolated_route(*, requested=None, explicit_base_url=None,
                   target_model=None, home: Path | None = None) -> dict | None:
    """Credential-free effective route for a durably published strict workspace.

    Read actual owned configuration even when a caller already has a config
    snapshot. ``request_overrides`` is inspection-only diagnostic metadata: do
    not serialize, mutate or forward it into the runtime request.
    """
    from eidolon_constants import get_eidolon_home, set_eidolon_home_override, reset_eidolon_home_override
    home = Path(home or get_eidolon_home()).absolute()
    if verify_isolation(home) is None:
        return None
    # Exact local declarations prove that this profile opted in; effective config
    # still governs runtime behavior and must agree, including managed overlays.
    from eidolon_cli.config import read_user_config_raw, load_config_readonly, is_provider_enabled
    token = set_eidolon_home_override(home)
    try:
        raw = read_user_config_raw(local_path(home / 'config.yaml')).get('model')
        configuration = load_config_readonly(initialize_home=False)
        effective = configuration.get('model')
        providers = configuration.get('providers')
        block = providers.get(PROVIDER) if isinstance(providers, dict) else None
        if isinstance(block, dict) and not is_provider_enabled(block):
            raise ProfileIsolationError('OpenAI API is disabled in this workspace configuration')
    except ProfileIsolationError:
        raise
    except Exception as exc:
        raise ProfileIsolationError('Workspace model configuration is unavailable') from exc
    finally:
        reset_eidolon_home_override(token)
    if (not isinstance(raw, dict) or raw.get('provider') != PROVIDER
            or not isinstance(raw.get('default'), str) or not raw['default'].strip() or len(raw['default']) > 300
            or not isinstance(effective, dict) or effective.get('provider') != PROVIDER
            or effective.get('default') != raw['default']):
        raise ProfileIsolationError('Select OpenAI API and a model in this workspace’s settings first')
    if requested not in (None, '', 'auto', PROVIDER):
        raise ProfileIsolationError('This workspace supports only its own OpenAI API credentials')
    for config in (raw, effective):
        if str(config.get('openai_runtime') or '').strip().lower() == 'codex_app_server':
            from eidolon_cli.organization_subscription import APP_SERVER_BLOCKER
            raise ProfileIsolationError(APP_SERVER_BLOCKER)
        if (config.get('base_url') not in (None, '', ENDPOINT, ENDPOINT + '/')
                or config.get('api_mode') not in (None, '', 'chat_completions')
                or any(config.get(key) for key in ('api_key', 'api', 'default_headers'))):
            raise ProfileIsolationError('This workspace requires a direct OpenAI API route without inline credentials or headers')
    if explicit_base_url not in (None, '', ENDPOINT, ENDPOINT + '/'):
        raise ProfileIsolationError('Explicit credentials or endpoint do not belong to this workspace')
    model = raw['default'] if target_model is None else target_model
    if not isinstance(model, str) or not model.strip() or len(model) > 300:
        raise ProfileIsolationError('A bounded model name is required')
    return {'provider': PROVIDER, 'requested_provider': PROVIDER, 'model': model.strip(),
            'api_mode': 'chat_completions', 'base_url': ENDPOINT,
            'request_overrides': effective.get('request_overrides')}


def isolated_runtime(*, requested=None, explicit_api_key=None, explicit_base_url=None,
                     target_model=None, home: Path | None = None) -> dict | None:
    from eidolon_constants import get_eidolon_home
    home = Path(home or get_eidolon_home()).absolute()
    route = isolated_route(requested=requested, explicit_base_url=explicit_base_url,
                           target_model=target_model, home=home)
    if route is None:
        return None
    secrets = profile_secrets(home)
    key = secrets.get('OPENAI_API_KEY', '').strip()
    from eidolon_cli.auth import has_usable_secret
    if (not has_usable_secret(key) or '${' in key or any(ord(c) < 32 for c in key)
            or secrets.get('OPENAI_BASE_URL') not in (None, '', ENDPOINT, ENDPOINT + '/')):
        raise ProfileIsolationError('Add an OpenAI API key in this workspace’s Keys settings first')
    if explicit_api_key not in (None, '', key):
        raise ProfileIsolationError('Explicit credentials or endpoint do not belong to this workspace')
    # Keep the exact runtime shape; inspection-only overrides never become
    # newly forwarded provider request options through this shared helper.
    return {'provider': PROVIDER, 'requested_provider': PROVIDER, 'model': route['model'],
            'api_mode': 'chat_completions', 'api_key': key, 'base_url': ENDPOINT,
            'source': 'profile-local-env'}


def block_auxiliary() -> None:
    if read_isolation() is not None:
        raise ProfileIsolationError('Auxiliary inference is unavailable in this isolated workspace; use tool-free organization work')


def isolate_plugin_manager(manager) -> bool:
    """A strict profile has an empty plugin registry, never inherited callbacks."""
    if verify_isolation(Path(manager.home_path)) is None:
        return False
    state = ('_plugins', '_hooks', '_middleware', '_system_prompt_sections', '_context_engine',
             '_registration_order', '_persistent_carryover', '_ownership_ledger', '_subscriptions',
             '_gateway_message_injector', '_memory_hook_registrations', '_predeclared_modules',
             '_predeclared_tools', '_plugin_tool_names', '_plugin_platform_names', '_cli_commands',
             '_plugin_commands', '_plugin_skills', '_portable_mcp_servers', '_aux_tasks',
             '_approval_transports', '_slack_action_handlers', '_platform_handler_factories')
    if any(getattr(manager, name, None) for name in state):
        raise ProfileIsolationError('Plugin registrations are unavailable in this isolated workspace')
    manager._discovered = True
    return True


def require_tool_free_execution(*, kind=None, context=None, execution=None) -> None:
    from eidolon_cli.organization_isolated_admission import check_active
    check_active(context=context, execution=execution)
    if read_isolation() is None:
        return
    if (context or {}).get('_credentialCapsule') is not None:
        raise ProfileIsolationError('Credential tools are unavailable in this isolated workspace')
    if kind in {'work.inspect', 'work.edit'} or execution is not None:
        from eidolon_cli.organization_isolated_admission import require_admission
        require_admission(context=context, execution=execution)


def require_owned_auth_path(path: Path) -> None:
    from eidolon_constants import get_eidolon_home
    home = Path(get_eidolon_home()).absolute()
    if read_isolation(home) is not None:
        if Path(path).absolute() != home / 'auth.json':
            raise ProfileIsolationError('Auth storage must belong to this isolated workspace')
        local_path(path)
        local_path(home / 'auth.lock')


def validate_log_path(path: Path) -> None:
    path = Path(path).absolute()
    if verify_isolation(path.parent.parent) is not None:
        local_path(path)


def allow_log_record(path: Path, producer_home) -> bool:
    """Queued logs use the producer stamp, never the listener's active context."""
    try:
        path = Path(path).absolute()
        destination = path.parent.parent
        target = verify_isolation(destination)
        if not isinstance(producer_home, str) or not producer_home:
            return target is None
        source = Path(producer_home).absolute()
        origin = verify_isolation(source)
        if origin is not None or target is not None:
            local_path(path)
            return source == destination
        return True
    except (ProfileIsolationError, OSError, RuntimeError, ValueError):
        return False


def initialize_agent(agent, *, platform, provider, model, api_key, base_url, api_mode,
                     enabled_toolsets, fallback_model) -> None:
    from eidolon_constants import get_eidolon_home
    if read_isolation() is None:
        return
    if (platform != 'organization' or enabled_toolsets != [] or fallback_model
            or provider != PROVIDER or api_mode != 'chat_completions'):
        raise ProfileIsolationError('This isolated workspace currently supports tool-free organization work only')
    isolated_runtime(requested=provider, target_model=model, explicit_api_key=api_key,
                     explicit_base_url=base_url)
    admission = getattr(agent, '_isolated_source_admission', None)
    if admission is not None:
        from eidolon_cli.organization_isolated_admission import require_admission
        require_admission(admission=admission)
    agent._isolated_workspace_home = str(local_path(get_eidolon_home()))


def validate_agent_send(agent) -> None:
    from eidolon_constants import get_eidolon_home
    pinned = getattr(agent, '_isolated_workspace_home', None)
    if pinned is None:
        if read_isolation() is not None:
            raise ProfileIsolationError('An unbound agent cannot use this isolated workspace')
        return
    if local_path(get_eidolon_home()) != Path(pinned):
        raise ProfileIsolationError('Agent workspace changed before provider send')
    isolated_runtime(home=Path(pinned), requested=agent.provider, target_model=agent.model,
                     explicit_api_key=agent.api_key, explicit_base_url=agent.base_url)
    execution = getattr(agent, '_organization_tool_execution', None)
    admission = getattr(agent, '_isolated_source_admission', None)
    from eidolon_cli.organization_isolated_admission import check_active, require_admission
    check_active(execution=execution, agent=agent)
    if execution is not None or admission is not None:
        require_admission(execution=execution, admission=admission, agent=agent)


def create_isolated_client(agent, kwargs: dict):
    from eidolon_constants import get_eidolon_home
    pinned = getattr(agent, '_isolated_workspace_home', None)
    if pinned is None:
        if read_isolation() is not None:
            raise ProfileIsolationError('An unbound agent cannot use this isolated workspace')
        return None
    if local_path(get_eidolon_home()) != Path(pinned):
        raise ProfileIsolationError('Agent workspace changed before client construction')
    route = isolated_runtime(home=Path(pinned), requested=getattr(agent, 'provider', None),
                             target_model=getattr(agent, 'model', None),
                             explicit_api_key=kwargs.get('api_key'), explicit_base_url=kwargs.get('base_url'))
    if route is None or getattr(agent, 'api_mode', None) != 'chat_completions':
        raise ProfileIsolationError('The agent no longer has a supported isolated route')
    if any(value for key, value in kwargs.items()
           if key not in {'api_key', 'base_url', 'timeout', 'max_retries'}):
        raise ProfileIsolationError('Custom SDK options are unavailable in this isolated workspace')
    import httpx
    from openai import OpenAI
    transport = httpx.Client(trust_env=False, follow_redirects=False)
    try:
        return OpenAI(api_key=route['api_key'], base_url=ENDPOINT, organization='', project='',
                      max_retries=0, http_client=transport,
                      **({'timeout': kwargs['timeout']} if 'timeout' in kwargs else {}))
    except BaseException:
        transport.close()
        raise
