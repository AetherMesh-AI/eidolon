"""Configured identity proofs use route provenance, never requested-name echoes."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from eidolon_cli.organization_provider_route import require_provider_route


def fixture_route():
    config = {'model': {'provider': 'mock', 'default': 'mock-model'}, 'providers': {
        'mock': {'name': 'Mock', 'api': 'https://example.invalid/CaseSensitive/v1?tenant=A',
                 'api_mode': 'chat_completions', 'key_env': 'MOCK_API_KEY'}}}
    runtime = {'provider': 'custom', 'requested_provider': 'mock', 'source': 'custom_provider:Mock',
               'model': 'mock-model', 'base_url': config['providers']['mock']['api'], 'api_mode': 'chat_completions'}
    return config, runtime


def test_named_selection_proves_identity_endpoint_and_source_and_exact_pool_scope():
    config, route = fixture_route()
    assert require_provider_route('mock', route, config) == 'mock'
    assert require_provider_route('custom:mock', {**route, 'requested_provider': 'custom:mock'}, config) == 'custom:mock'
    assert require_provider_route('mock', {**route, 'source': 'pool:mock',
                                          'credential_pool': SimpleNamespace(provider='mock')}, config) == 'mock'
    assert require_provider_route('mock', {**route, 'source': 'pool:custom:mock',
                                          'credential_pool': SimpleNamespace(provider='custom:mock')}, config) == 'mock'
    for changed in (
        {'provider': 'openrouter'}, {'requested_provider': 'someone-else'},
        {'source': 'env/config'}, {'source': 'custom_provider:Other'},
        {'source': 'pool:other', 'credential_pool': SimpleNamespace(provider='other')},
        {'source': 'pool:mock'}, {'source': 'pool:mock', 'credential_pool': SimpleNamespace(provider='other')},
        {'base_url': 'https://example.invalid/casesensitive/v1?tenant=A'},
        {'base_url': 'https://example.invalid/CaseSensitive/v1?tenant=a'},
        {'base_url': 'https://elsewhere.invalid/CaseSensitive/v1?tenant=A'},
    ):
        with pytest.raises(ValueError, match='cannot be proven') as error:
            require_provider_route('mock', {**route, **changed}, config)
        assert 'example.invalid' not in str(error.value) and 'tenant=' not in str(error.value)


def test_disabled_missing_ambiguous_and_malformed_named_definitions_stay_closed():
    config, route = fixture_route()
    for change in ('disabled', 'missing', 'ambiguous', 'endpoint', 'malformed'):
        cfg = deepcopy(config)
        if change == 'disabled':
            cfg['providers']['mock']['enabled'] = False
        elif change == 'missing':
            cfg['providers'] = {}
        elif change == 'ambiguous':
            cfg['providers']['other'] = {**cfg['providers']['mock'], 'api': 'https://other.invalid/v1'}
        elif change == 'endpoint':
            cfg['providers']['mock']['api'] = None
        else:
            cfg['providers'] = ['mock']
        with pytest.raises(ValueError):
            require_provider_route('mock', route, cfg)


def test_explicit_route_guard_is_shared_while_legacy_inherited_objectives_keep_their_policy(monkeypatch):
    from eidolon_cli import config as configuration, runtime_provider
    from eidolon_cli.organization_executor import _runtime_kwargs, OrganizationExecutionError
    config, route = fixture_route()
    config['providers']['mock']['api'] = route['base_url'] = 'http://127.0.0.1:9/v1'
    route['api_key'] = 'fixture-key'
    monkeypatch.setattr(configuration, 'load_config_readonly', lambda: config)
    monkeypatch.setattr(runtime_provider, 'resolve_runtime_provider', lambda **kwargs: dict(route))
    member = {'agent': {'provider': 'mock', 'model': 'mock-model'}}
    assert _runtime_kwargs(member, 10)['provider'] == 'custom'
    assert _runtime_kwargs({'agent': {}}, 10, require_exact_provider=True)['requested_provider'] == 'mock'
    route.update(provider='openrouter', source='env/config', base_url='https://example.invalid/v1')
    for context, strict in ((member, False), ({'agent': {}}, True)):
        with pytest.raises(OrganizationExecutionError, match='cannot be proven'):
            _runtime_kwargs(context, 10, require_exact_provider=strict)
    # The new owner strictness must not silently change old inherited objective policy.
    assert _runtime_kwargs({'agent': {}}, 10)['provider'] == 'openrouter'


def test_canonical_builtin_metadata_does_not_become_a_named_custom_endpoint():
    route = {'provider': 'openrouter', 'requested_provider': 'openrouter', 'source': 'env/config',
             'base_url': 'https://openrouter.ai/api/v1', 'api_mode': 'chat_completions'}
    for entry in ({'enabled': True}, {'api_key': 'fixture-only'},
                  {'api': 'https://ignored-provider-metadata.invalid/v1', 'name': 'Alternate label'}):
        config = {'model': {'provider': 'openrouter'}, 'providers': {'openrouter': entry}}
        assert require_provider_route('openrouter', route, config) == 'openrouter'
    config['providers']['openrouter']['enabled'] = False
    with pytest.raises(ValueError, match='disabled'):
        require_provider_route('openrouter', route, config)
    config['providers']['openrouter']['enabled'] = True
    config['model']['base_url'] = 'https://another-endpoint.invalid/v1'
    with pytest.raises(ValueError, match='cannot be proven'):
        require_provider_route('openrouter', route, config)


def test_catalog_only_name_is_still_a_configured_custom_provider(monkeypatch):
    from eidolon_cli import providers
    monkeypatch.setattr(providers, 'get_provider', lambda *args, **kwargs: SimpleNamespace(id='catalog-fixture'))
    config = {'providers': {'catalog-fixture': {'api': 'http://127.0.0.1:9/v1'}}}
    route = {'requested_provider': 'catalog-fixture', 'provider': 'custom',
             'source': 'custom_provider:catalog-fixture', 'base_url': 'http://127.0.0.1:9/v1'}
    assert require_provider_route('catalog-fixture', route, config) == 'catalog-fixture'


def test_effective_sdk_query_identity_preserves_simple_values_and_rejects_loss():
    from eidolon_cli.organization_provider_route import request_endpoint_identity
    expected = request_endpoint_identity('https://example.invalid/v1?tenant=A&api-version=fixture')
    assert request_endpoint_identity('https://example.invalid/v1/', {'tenant': 'A', 'api-version': 'fixture'}) == expected
    assert request_endpoint_identity('https://example.invalid/v1/', {'tenant': 'B', 'api-version': 'fixture'}) != expected
    for url in ('https://example.invalid/v1?tenant=', 'https://example.invalid/v1?tenant=A&tenant=B',
                'https://example.invalid/v1?', 'https://example.invalid/v1?tenant=%41',
                'https://private-user:fixture-secret@example.com\uff0f.invalid/v1'):
        with pytest.raises(ValueError, match='query cannot be preserved') as error:
            request_endpoint_identity(url)
        assert str(error.value) == 'The configured owner-chat endpoint query cannot be preserved exactly.'


def test_builtin_alias_keeps_raw_selection_and_canonical_adapter_distinct():
    config = {'model': {'provider': 'grok'}}
    route = {'provider': 'xai', 'requested_provider': 'grok', 'source': 'env',
             'base_url': 'https://api.x.ai/v1', 'api_mode': 'codex_responses'}
    assert require_provider_route('grok', route, config) == 'grok'
    with pytest.raises(ValueError, match='cannot be proven'):
        require_provider_route('grok', {**route, 'provider': 'openrouter'}, config)
