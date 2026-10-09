"""Prove configured provider identity independently of its wire adapter label."""
from __future__ import annotations

from eidolon_cli.route_identity import normalize_route_base_url


def _name(value):
    return str(value or '').strip().lower().replace(' ', '-')


def _named_entry(requested, config):
    from eidolon_cli.config import is_provider_enabled
    from eidolon_cli.providers import custom_provider_aliases
    from eidolon_cli.runtime_provider_custom import _shadowed_by_builtin

    providers = config.get('providers')
    # Canonical built-ins may have enabled/api_key metadata blocks. The
    # resolver does not turn those into named custom endpoints.
    if _shadowed_by_builtin(_name(requested)):
        block = providers.get(str(requested).strip().lower()) if isinstance(providers, dict) else None
        if isinstance(block, dict) and not is_provider_enabled(block):
            raise ValueError('The configured provider entry is disabled.')
        return None
    candidates = []
    if isinstance(providers, dict):
        candidates.extend((str(key), entry) for key, entry in providers.items() if isinstance(entry, dict))
    legacy = config.get('custom_providers')
    if isinstance(legacy, list):
        candidates.extend((str(entry.get('provider_key') or ''), entry) for entry in legacy if isinstance(entry, dict))
    matches = []
    for key, entry in candidates:
        name = str(entry.get('name') or key)
        if _name(requested) not in custom_provider_aliases(name, key):
            continue
        endpoint = entry.get('api') or entry.get('url') or entry.get('base_url')
        matches.append({'name': name, 'key': key, 'endpoint': endpoint,
                        'enabled': is_provider_enabled(entry)})
    if len(matches) > 1:
        raise ValueError('The configured provider identity is ambiguous; select one exact enabled entry.')
    if not matches:
        return None
    entry = matches[0]
    if not entry['enabled'] or not isinstance(entry['endpoint'], str) or not entry['endpoint'].strip():
        raise ValueError('The configured provider entry is disabled or has no exact endpoint.')
    return entry


def require_provider_route(requested, runtime, config):
    """Return the selected identity only after validating the resolver's route.

    Resolver fallback keeps requested_provider, so that field alone never proves
    selection. Named providers deliberately expose the shared ``custom`` adapter;
    their exact enabled entry, endpoint and source establish the selected route.
    This helper performs no account lookup, credential read or provider call.
    """
    from eidolon_cli.providers import normalize_provider

    requested = str(requested or '').strip()
    if not requested or requested.lower() == 'auto':
        return str(runtime.get('requested_provider') or requested)
    reason = 'The configured organization provider route cannot be proven; a different provider was not authorized.'
    if _name(runtime.get('requested_provider')) != _name(requested):
        raise ValueError(reason)
    selected = _named_entry(requested, config)
    adapter = str(runtime.get('provider') or '')
    if selected is not None:
        expected = normalize_route_base_url(selected['endpoint'].strip())
        if adapter != 'custom' or normalize_route_base_url(runtime.get('base_url')) != expected:
            raise ValueError(reason)
        source = runtime.get('source')
        # Pool keys are scoped to the matched entry, including the historical
        # display-name namespace. Never accept an unrelated pool at the same URL.
        pool_keys = {_name(selected['key']), 'custom:' + _name(selected['name'])} - {''}
        sources = {'custom_provider:' + selected['name'], *('pool:' + key for key in pool_keys)}
        if source not in sources:
            raise ValueError(reason)
        if str(source).startswith('pool:'):
            pool = runtime.get('credential_pool')
            if pool is None or _name(getattr(pool, 'provider', None)) != source.removeprefix('pool:'):
                raise ValueError(reason)
    else:
        if normalize_provider(requested) != normalize_provider(adapter):
            raise ValueError(reason)
        configured = config.get('model')
        configured = configured if isinstance(configured, dict) else {}
        endpoint = configured.get('base_url')
        if adapter == 'custom' and not endpoint:
            raise ValueError(reason)
        if (endpoint and normalize_provider(str(configured.get('provider') or '')) == normalize_provider(requested)
                and normalize_route_base_url(endpoint) != normalize_route_base_url(runtime.get('base_url'))):
            raise ValueError(reason)
    return requested


def request_endpoint_identity(base_url, default_query=None):
    """Compare the effective SDK endpoint without dropping tenant query data.

    AIAgent splits simple query parameters into SDK default_query. Reject forms
    that its dict conversion would shorten or rewrite (duplicates, blank values,
    ambiguous encoding), rather than treating a lossy conversion as equivalent.
    """
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    reason = 'The configured owner-chat endpoint query cannot be preserved exactly.'
    raw = str(base_url or '')
    try:
        parsed = urlsplit(raw)
    except ValueError:
        raise ValueError(reason) from None
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(reason)
    if '?' in raw.split('#', 1)[0] and not parsed.query:
        raise ValueError(reason)
    try:
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        raise ValueError(reason) from None
    if (len({key for key, _ in pairs}) != len(pairs) or any(not key or not value for key, value in pairs)
            or (pairs and urlencode(pairs) != parsed.query)):
        raise ValueError(reason)
    if default_query is not None:
        if not isinstance(default_query, dict) or any(
                not isinstance(key, str) or not isinstance(value, str) or not key or not value
                for key, value in default_query.items()):
            raise ValueError(reason)
        if pairs and default_query:
            raise ValueError(reason)
        pairs = pairs or list(default_query.items())
    endpoint = normalize_route_base_url(urlunsplit((parsed.scheme, parsed.netloc, parsed.path, '', '')))
    return endpoint, tuple(pairs)
