"""Prepared owner API cannot retarget the served home or initialize execution."""
from types import SimpleNamespace

import pytest

import tui_gateway.server as server
from eidolon_cli import organization_prepared_validation_setup as setup
from tests.eidolon_cli.test_organization_prepared_validation_contract import prepared
from tests.eidolon_cli.test_organization_prepared_validation_setup import owner, confirmed, revoke_params
from tests.eidolon_cli.test_organization_root_grants import folder_profile
from tests.eidolon_cli.test_organization_owner_policy import forbid_execution
from tests.tui_gateway.test_organization_owner_policy import call

pytestmark = pytest.mark.linux_only


@pytest.fixture
def served(owner, monkeypatch):
    monkeypatch.setattr(server, '_eidolon_home', owner.home)
    forbid_execution(monkeypatch)
    return owner


def test_served_owner_review_save_revoke_and_exact_retries(served):
    read = call('organization.preparedValidationSetup', {'expectedOrganizationId': served.view['organizationId']})
    assert 'error' not in read, read
    assert read['result']['scope']['kind'] == 'served-profile'
    preview = call('organization.previewPreparedValidation', served.preview_params)
    assert 'error' not in preview, preview
    assert preview['result']['candidate']['verification']['state'] == 'verified_bytes_runtime_unprobed'
    params = confirmed(served)
    saved = call('organization.savePreparedValidation', params)
    assert 'error' not in saved, saved
    assert call('organization.savePreparedValidation', params) == saved
    revocation = revoke_params(served, saved['result'])
    revoked = call('organization.revokePreparedValidation', revocation)
    assert 'error' not in revoked, revoked
    assert revoked['result']['receipt']['disabled'] is True
    assert call('organization.revokePreparedValidation', revocation) == revoked
    assert setup.prepared_validation_setup(served.home, served.view['organizationId'])['registrationRefs'] == []


@pytest.mark.parametrize('field', ['profile', 'home', 'actorId', 'connectionId', 'ownerId', 'command'])
def test_all_mutations_reject_caller_routing_or_command_fields(served, field):
    requests = [
        ('organization.preparedValidationSetup', {'expectedOrganizationId': served.view['organizationId']}),
        ('organization.previewPreparedValidation', served.preview_params),
        ('organization.savePreparedValidation', confirmed(served)),
        ('organization.revokePreparedValidation', {'expectedOrganizationId': served.view['organizationId'],
            'expectedOwnerId': served.view['ownerId'], 'expectedGeneration': served.view['policyGeneration'],
            'expectedSidecarRevision': 0, 'projectId': 'notes', 'registrationRevision': 1,
            'registrationHash': 'a' * 64, 'idempotencyKey': 'revoke', 'confirmed': True}),
    ]
    for method, params in requests:
        assert call(method, {**params, field: 'other'})['error']['code'] == -32602


def test_closed_transport_cannot_read_or_prepare(served):
    for method, params in [('organization.preparedValidationSetup',
                           {'expectedOrganizationId': served.view['organizationId']}),
                          ('organization.previewPreparedValidation', served.preview_params)]:
        assert call(method, params, SimpleNamespace(closed=True))['error']['code'] == 4001
