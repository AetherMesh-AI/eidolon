"""Supported ordinary choices are distinct from dedicated setup authorization."""
import asyncio
from unittest.mock import Mock

from tests.eidolon_cli.test_isolated_provider_inventory import origin, workspace, options


def test_empty_isolated_workspace_lists_only_supported_ordinary_provider(workspace):
    result = options()
    assert {row['slug'] for row in result['providers']} == {'openai-api'}


def test_isolated_oauth_catalog_does_not_offer_unsupported_signin(workspace, monkeypatch):
    from eidolon_cli.web_routers import oauth
    status = Mock(side_effect=AssertionError('Unsupported OAuth status must not be inspected'))
    monkeypatch.setattr(oauth, '_resolve_provider_status', status)
    assert asyncio.run(oauth.list_oauth_providers(execution_only=True)) == {'providers': [], 'supportedProviders': ['openai-api']}
    status.assert_not_called()


def test_empty_workspace_already_has_dedicated_setup_capability(workspace, monkeypatch):
    import sys
    from eidolon_cli.organization_team_setup import team_setup
    from tui_gateway import organization_credential_transport as transport
    home, identity = workspace
    class Owner:
        closed = False
    owner = Owner()
    monkeypatch.setattr(sys, 'platform', 'darwin')
    monkeypatch.setitem(transport._owners, owner, (home.resolve(), object()))
    result = team_setup(home, identity['organizationId'], subscription_transport=owner)
    assert result['state'] == 'available'
    assert result['setupSubscriptionState'] == 'available'
    assert 'model_not_configured' in result['readiness']['blockers']
    assert 'subscription_generation_unsupported' not in result['readiness']['blockers']
    assert team_setup(home, identity['organizationId'])['setupSubscriptionState'] == 'local_owner_required'


def test_legacy_profile_keeps_ordinary_oauth_catalog(origin, monkeypatch):
    from eidolon_cli.web_routers import oauth
    status = Mock(return_value={})
    monkeypatch.setattr(oauth, '_resolve_provider_status', status)
    result = asyncio.run(oauth.list_oauth_providers(execution_only=True))
    assert 'openai-codex' in {row['id'] for row in result['providers']}
    assert status.called


def test_corrupt_isolation_does_not_reopen_ordinary_choices(workspace):
    import pytest
    from fastapi import HTTPException
    from eidolon_cli.profile_isolation import MARKER, ProfileIsolationError
    from eidolon_cli.web_routers import oauth
    home, _ = workspace
    (home / MARKER).write_text('{}')
    with pytest.raises(HTTPException) as failure:
        options()
    assert failure.value.status_code == 500
    with pytest.raises(ProfileIsolationError):
        asyncio.run(oauth.list_oauth_providers(execution_only=True))


def test_isolated_connection_management_still_lists_existing_accounts(workspace, monkeypatch):
    from eidolon_cli.web_routers import oauth
    monkeypatch.setattr(oauth, '_resolve_provider_status', Mock(return_value={}))
    result = asyncio.run(oauth.list_oauth_providers())
    assert 'openai-codex' in {row['id'] for row in result['providers']}
