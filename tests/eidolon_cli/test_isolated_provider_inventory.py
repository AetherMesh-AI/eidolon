"""Real isolated workspace inventory, synthetic OAuth/metadata, no provider calls."""
import asyncio
import json
from unittest.mock import Mock

import pytest

from tests.eidolon_cli.test_organization_workspaces import (
    origin, create, scope, deny_external_network,
)


@pytest.fixture
def workspace(origin, monkeypatch):
    home, result = create(origin)
    deny_external_network(monkeypatch)
    from agent import models_dev
    from eidolon_cli import inventory, models, model_catalog
    monkeypatch.setattr(model_catalog, 'get_catalog', lambda **kwargs: {})
    monkeypatch.setattr(models_dev, 'fetch_models_dev', lambda: {
        'openai': {'name': 'OpenAI', 'env': ['OPENAI_API_KEY'], 'models': {}}})
    monkeypatch.setattr(models, 'cached_provider_model_ids', lambda *a, **k: ['synthetic-model'])
    monkeypatch.setattr(inventory, '_prewarm_pricing_async', lambda *a, **k: None)
    with scope(home):
        yield home, result


def options():
    from eidolon_cli.web_routers.models import get_model_options
    return asyncio.run(get_model_options(include_unconfigured=True))


def test_capability_inspection_does_not_resolve_credentials(workspace, monkeypatch):
    from eidolon_cli import auth
    resolver = Mock(side_effect=AssertionError('Capability check entered runtime'))
    monkeypatch.setattr(auth, 'resolve_provider', resolver)
    assert auth.is_runtime_provider_routable('openai-api')
    assert auth.is_runtime_provider_routable('openai-codex')
    assert not auth.is_runtime_provider_routable('not-a-provider')
    resolver.assert_not_called()


def test_empty_isolated_workspace_can_load_options_without_enabling_runtime(workspace):
    from eidolon_cli import auth
    from eidolon_cli.profile_isolation import ProfileIsolationError
    result = options()
    assert any(row['slug'] == 'openai-api' for row in result['providers'])
    with pytest.raises(ProfileIsolationError, match='Select a supported provider and a model'):
        auth.resolve_provider('openai-codex')


def test_successful_mock_oauth_followed_by_options_keeps_runtime_restriction(workspace, monkeypatch):
    from eidolon_cli.web_routers import oauth
    from eidolon_cli import auth
    from eidolon_cli.profile_isolation import ProfileIsolationError
    home, result = workspace
    session = {'profile': result['profile'], 'status': 'pending'}
    monkeypatch.setitem(oauth._oauth_sessions, 'synthetic-session', session)
    monkeypatch.setattr(oauth, '_codex_request_user_code', lambda _: {
        'user_code': 'synthetic', 'device_auth_id': 'synthetic', 'interval': 3})
    monkeypatch.setattr(oauth, '_codex_poll_authorization', lambda *a: {})
    monkeypatch.setattr(oauth, '_codex_exchange_tokens', lambda *a: {'access_token': 'synthetic'})
    saved = Mock()
    monkeypatch.setattr(auth, '_save_codex_tokens', saved)
    monkeypatch.setattr(oauth, '_profile_scope', lambda _: scope(home))
    oauth._codex_full_login_worker('synthetic-session')
    assert session['status'] == 'approved'
    saved.assert_called_once_with({'access_token': 'synthetic'})
    assert options()['providers']
    with pytest.raises(ProfileIsolationError):
        auth.resolve_provider('openai-codex')


def test_configured_isolated_route_survives_inventory(workspace):
    from eidolon_cli import auth
    from eidolon_cli.profile_isolation import ProfileIsolationError
    home, _ = workspace
    (home / 'config.yaml').write_text(json.dumps({'model': {
        'provider': 'openai-api', 'default': 'synthetic-model'}}))
    (home / '.env').write_text('OPENAI_API_KEY=synthetic-workspace-key\n')
    result = options()
    assert result['provider'] == 'openai-api'
    assert result['model'] == 'synthetic-model'
    assert auth.resolve_provider('openai-api') == 'openai-api'
    with pytest.raises(ProfileIsolationError, match='differs from this workspace’s selected route'):
        auth.resolve_provider('openai-codex')


def test_dedicated_generate_remains_separate_from_ordinary_oauth(workspace, monkeypatch):
    from types import SimpleNamespace
    from eidolon_cli import organization_team_generation as generation
    from eidolon_cli import organization_setup_subscription_transport as transport
    from eidolon_cli.organization_team_setup import presets
    from eidolon_cli import auth
    from eidolon_cli.profile_isolation import ProfileIsolationError

    options()
    # An ordinary login is not a dedicated setup authorization or runtime grant.
    with pytest.raises(ProfileIsolationError):
        auth.resolve_provider('openai-codex')
    forbidden = Mock(side_effect=AssertionError('Dedicated setup entered ordinary runtime'))
    for name in ('_create_agent', '_guard_plugin_integrations', '_runtime_kwargs', 'readiness_reason'):
        monkeypatch.setattr(generation, name, forbidden)
    request = generation.subscription_request('generateTeam', 'Write reports',
        'Clear outcomes', 'Short names', model_slug='synthetic-model')
    team = presets()[0]['team']
    send = Mock(return_value=SimpleNamespace(text=json.dumps(team),
        observation=SimpleNamespace(terminal_kind='completed')))
    monkeypatch.setattr(transport, 'send_once', send)
    prepared = SimpleNamespace(request=request)
    assert generation.complete_subscription(prepared) == team
    send.assert_called_once_with(prepared)
    forbidden.assert_not_called()


def test_capability_aliases_and_legacy_special_routes(monkeypatch):
    from eidolon_cli import auth
    monkeypatch.setattr(auth, '_plugin_aliases', lambda: {'synthetic-alias': 'openai-api'})
    for provider in (' OPENAI-API ', 'synthetic-alias', 'auto', 'moa', 'custom:local'):
        assert auth.is_runtime_provider_routable(provider)
    assert not auth.is_runtime_provider_routable('')
    assert not auth.is_runtime_provider_routable('unknown-provider')
