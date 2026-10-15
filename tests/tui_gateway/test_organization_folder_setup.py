"""Folder grants use the immutable served owner endpoint, never caller routing."""
import os
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

import tui_gateway.server as server
from tests.tui_gateway.test_organization_owner_policy import call
from tests.eidolon_cli.test_organization_root_grants import folder_profile

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='Folder grants require no-follow POSIX descriptors')


@pytest.fixture
def served(folder_profile, monkeypatch):
    monkeypatch.setattr(server, '_eidolon_home', folder_profile.home)
    def forbidden(*args, **kwargs): raise AssertionError('No service initialization or profile selection')
    monkeypatch.setattr(server, '_profile_home', forbidden)
    monkeypatch.setattr('eidolon_cli.organization_service.get_service', forbidden)
    return folder_profile


def test_folder_rpc_uses_served_home_and_explicit_review(served):
    params = {'expectedOrganizationId': served.identity['organizationId']}
    result = call('organization.folderSetup', params)
    assert 'error' not in result, result
    assert result['result']['scope']['kind'] == 'served-profile'
    preview = call('organization.previewProjectFolder', served.params)
    assert 'error' not in preview, preview
    applied = call('organization.registerProjectFolder', served.apply)
    assert 'error' not in applied, applied
    assert call('organization.registerProjectFolder', served.apply) == applied


@pytest.mark.parametrize('extra', ['profile', 'home', 'actorId', 'connectionId', 'ownerId'])
def test_folder_rpc_cannot_route_to_another_profile(served, extra):
    result = call('organization.folderSetup', {'expectedOrganizationId': served.identity['organizationId'], extra: 'other'})
    assert result['error']['code'] == -32602


def test_folder_rpc_rejects_wrong_identity_and_dead_transport(served):
    result = call('organization.folderSetup', {'expectedOrganizationId': str(uuid.uuid4())})
    assert 'error' in result
    result = call('organization.folderSetup', {'expectedOrganizationId': served.identity['organizationId']}, SimpleNamespace(closed=True))
    assert result['error']['code'] == 4001
