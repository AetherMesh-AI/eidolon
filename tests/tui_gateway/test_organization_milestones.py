from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import tui_gateway.server as server
from eidolon_cli import organization_service as services
from eidolon_cli.organization_metadata import save_metadata
from tests.eidolon_cli.test_organization_milestones import fixture as make_store
from tests.tui_gateway.test_organization_owner_policy import call


@pytest.fixture
def setup(tmp_path, monkeypatch):
    home = tmp_path / 'served'; home.mkdir()
    state = home / 'organization'; state.mkdir()
    store, first, second = make_store(state)
    monkeypatch.setattr(server, '_eidolon_home', home)
    identity = save_metadata(store.path, display_name='Example', owner_display_name='Owner',
        expected_organization_id=None, expected_revision=None, idempotency_key='identity')['metadata']
    service = SimpleNamespace(store=store, start=Mock())
    monkeypatch.setattr(services, 'get_service', lambda: service)
    params = {'expectedOrganizationId': identity['organizationId'], 'expectedOwnerId': identity['owner']['ownerId']}
    return service, first, params


def test_passive_read_and_explicit_revision_never_start_execution(setup, monkeypatch):
    service, first, params = setup
    with service.store._connect() as conn:
        before = '\n'.join(conn.iterdump())
    monkeypatch.setattr(services, 'get_service', Mock(side_effect=AssertionError('Passive read must not load service')))
    read = call('organization.milestones', params)
    assert 'error' not in read, read
    assert read['result']['milestones'] == []
    with service.store._connect() as conn:
        assert '\n'.join(conn.iterdump()) == before
    monkeypatch.setattr(services, 'get_service', lambda: service)
    intent = dict(params, milestoneId='launch', title='Launch', projectIds=['alpha'], objectiveIds=[first],
                  dependencyIds=[], expectedRevision=0, idempotencyKey='save', confirmed=True)
    result = call('organization.reviseMilestone', intent)
    assert 'error' not in result, result
    assert call('organization.reviseMilestone', intent)['result'] == result['result']
    assert service.start.call_count == 0
    assert len(call('organization.milestones', params)['result']['milestones']) == 1


@pytest.mark.parametrize('change', [{'profile':'other'}, {'expectedOrganizationId':'other'}, {'expectedOwnerId':'other'}, {'confirmed':False}])
def test_wrong_scope_or_unconfirmed_write_rejected(setup, change):
    service, first, params = setup
    intent = dict(params, milestoneId='launch', title='Launch', projectIds=['alpha'], objectiveIds=[first],
                  dependencyIds=[], expectedRevision=0, idempotencyKey='save', confirmed=True)
    assert 'error' in call('organization.reviseMilestone', {**intent, **change})
    assert service.store.milestones() == []
    assert service.start.call_count == 0


def test_closed_transport_rejected(setup):
    _, _, params = setup
    assert 'error' in call('organization.milestones', params, transport=SimpleNamespace(closed=True))


def test_receipt_reconciliation_is_passive_and_bound_to_owner(setup, monkeypatch):
    service, first, params = setup
    intent = dict(params, milestoneId='launch', title='Launch', projectIds=['alpha'], objectiveIds=[first],
                  dependencyIds=[], expectedRevision=0, idempotencyKey='save', confirmed=True)
    saved = call('organization.reviseMilestone', intent)['result']['receipt']
    monkeypatch.setattr(services, 'get_service', Mock(side_effect=AssertionError('Receipt lookup must remain passive')))
    with service.store._connect() as conn:
        before = '\n'.join(conn.iterdump())
    assert call('organization.milestoneReceipt', dict(params, idempotencyKey='save'))['result']['receipt'] == saved
    assert call('organization.milestoneReceipt', dict(params, idempotencyKey='missing'))['result']['receipt'] is None
    assert 'error' in call('organization.milestoneReceipt', dict(params, idempotencyKey='save', expectedOwnerId='other'))
    with service.store._connect() as conn:
        assert '\n'.join(conn.iterdump()) == before


def test_receipt_lookup_rejects_partial_milestone_storage(setup):
    service, _, params = setup
    with service.store._connect(write=True) as conn:
        conn.execute('DROP TABLE organization_milestone_links')
    assert 'error' in call('organization.milestoneReceipt', dict(params, idempotencyKey='missing'))
