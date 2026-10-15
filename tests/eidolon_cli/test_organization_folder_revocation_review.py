"""Atomic revocation failures and independently reviewed multi-folder holds."""
import os
import sqlite3

import pytest

from eidolon_cli import organization_folder_revocation as revocation
from eidolon_cli.organization_folder_setup import folder_setup, preview_folder, register_folder
from eidolon_cli.organization_owner_policy import OwnerPolicyError
from eidolon_cli.organization_store import OrganizationStore
from tests.eidolon_cli.test_organization_root_grants import folder_profile


pytestmark = pytest.mark.skipif(os.name != 'posix', reason='Folder grants require no-follow POSIX descriptors')


def _dump(path):
    with sqlite3.connect(path) as conn:
        return '\n'.join(conn.iterdump())


def _revoke_params(profile, alias, key):
    view = revocation.folder_access(profile.home, profile.identity['organizationId'])
    grant = next(item for item in view['grants'] if item['alias'] == alias)
    return {'expectedOrganizationId': view['organizationId'],
            'expectedGeneration': view['policyGeneration'], 'alias': alias,
            'grantHash': grant['grantHash'], 'confirmed': True, 'idempotencyKey': key}


def _review_params(profile, alias):
    view = revocation.folder_access(profile.home, profile.identity['organizationId'])
    grant = next(item for item in view['grants'] if item['alias'] == alias)
    return {'expectedOrganizationId': view['organizationId'],
            'expectedGeneration': view['policyGeneration'], 'alias': alias,
            'grantHash': grant['grantHash'], 'confirmed': True,
            'reviewVersion': view['reviewVersion'], 'acknowledgeUncertain': False}


def _register_second(profile):
    register_folder(profile.home, profile.apply)
    root = profile.root.with_name('second')
    root.mkdir()
    view = folder_setup(profile.home, profile.identity['organizationId'])
    params = {'expectedOrganizationId': view['organizationId'],
              'expectedGeneration': view['policyGeneration'], 'sourceRevision': view['sourceRevision'],
              'folder': {'path': str(root), 'projectId': 'second', 'team': 'general'}}
    preview = preview_folder(profile.home, params)
    return register_folder(profile.home, {
        **{key: value for key, value in params.items() if key != 'folder'},
        'candidate': preview['candidate'], 'previewVersion': preview['previewVersion'],
        'confirmed': True, 'idempotencyKey': 'grant-second'})


def test_revocation_failure_rolls_back_generation_pause_claim_and_receipt(folder_profile, monkeypatch):
    profile = folder_profile
    register_folder(profile.home, profile.apply)
    store = OrganizationStore(profile.path)
    store.create_objective('Unrelated plan', delivery_mode='managed_artifact', idempotency_key='plan')
    claim = store.claim_next()
    assert claim is not None
    with store._write() as conn:
        conn.execute("INSERT INTO tool_receipts(id,request_id,attempt,tool_call_id,tool_name,arguments,status,created) "
                     "VALUES ('inflight',?,1,'call','read_file','{}','running',0)", (claim['id'],))
    params = _revoke_params(profile, 'root0', 'revoke')
    before = _dump(profile.path)
    original_pending = OrganizationStore._pending

    def fail_after_fencing(conn, request, reason):
        original_pending(conn, request, reason)
        assert conn.execute('SELECT generation FROM organization_policy').fetchone()[0] > params['expectedGeneration']
        assert conn.execute("SELECT status FROM tool_receipts WHERE id='inflight'").fetchone()[0] == 'unknown'
        raise RuntimeError('Injected failure after claim fencing')

    with monkeypatch.context() as patch:
        patch.setattr(OrganizationStore, '_pending', staticmethod(fail_after_fencing))
        with pytest.raises(RuntimeError, match='Injected failure'):
            revocation.revoke_folder(profile.home, params)

    assert _dump(profile.path) == before
    assert store.heartbeat(claim)
    receipt = revocation.revoke_folder(profile.home, params)
    assert receipt['receipt']['accessRevoked'] is True
    assert revocation.revoke_folder(profile.home, params) == receipt


def test_held_sqlite_writer_does_not_report_or_commit_revocation(folder_profile):
    profile = folder_profile
    register_folder(profile.home, profile.apply)
    params = _revoke_params(profile, 'root0', 'revoke')
    before = _dump(profile.path)
    blocker = sqlite3.connect(profile.path, isolation_level=None)
    try:
        blocker.execute('BEGIN IMMEDIATE')
        with pytest.raises(OwnerPolicyError) as error:
            revocation.revoke_folder(profile.home, params)
        assert error.value.reason == 'locked'
        assert _dump(profile.path) == before
        view = revocation.folder_access(profile.home, profile.identity['organizationId'])
        assert not view['grants'][0]['revoked']
        assert view['policyGeneration'] == params['expectedGeneration']
    finally:
        blocker.rollback()
        blocker.close()

    receipt = revocation.revoke_folder(profile.home, params)
    assert receipt['receipt']['accessRevoked'] is True
    assert revocation.revoke_folder(profile.home, params) == receipt
    with sqlite3.connect(profile.path) as conn:
        assert conn.execute('SELECT count(*) FROM organization_root_revocations').fetchone()[0] == 1


def test_review_failure_does_not_release_hold_or_commit_review(folder_profile, monkeypatch):
    profile = folder_profile
    register_folder(profile.home, profile.apply)
    revocation.revoke_folder(profile.home, _revoke_params(profile, 'root0', 'revoke'))
    params = _review_params(profile, 'root0')
    before = _dump(profile.path)
    original_reload = OrganizationStore._reload_configuration

    def fail_after_adoption(store, conn, settings):
        original_reload(store, conn, settings)
        assert conn.execute('SELECT count(*) FROM organization_root_revocation_reviews').fetchone()[0] == 1
        raise RuntimeError('Injected failure after review adoption')

    with monkeypatch.context() as patch:
        patch.setattr(OrganizationStore, '_reload_configuration', fail_after_adoption)
        with pytest.raises(RuntimeError, match='Injected failure'):
            revocation.review_revocation(profile.home, params)

    assert _dump(profile.path) == before
    assert revocation.folder_access(profile.home, profile.identity['organizationId'])['paused']
    result = revocation.review_revocation(profile.home, params)
    assert result['reviewed'] is True
    assert revocation.review_revocation(profile.home, params) == result


def test_each_outstanding_revocation_requires_its_own_review(folder_profile):
    profile = folder_profile
    _register_second(profile)
    store = OrganizationStore(profile.path)
    store.create_objective('Unaffected planning', delivery_mode='managed_artifact', idempotency_key='plan')
    for alias in ('root0', 'root1'):
        revocation.revoke_folder(profile.home, _revoke_params(profile, alias, 'revoke-' + alias))

    first = _review_params(profile, 'root0')
    first_result = revocation.review_revocation(profile.home, first)
    view = revocation.folder_access(profile.home, profile.identity['organizationId'])
    assert view['paused']
    assert {item['alias']: item['reviewed'] for item in view['grants']} == {'root0': True, 'root1': False}
    assert OrganizationStore(profile.path).claim_next() is None
    before = _dump(profile.path)
    assert revocation.review_revocation(profile.home, first) == first_result
    assert _dump(profile.path) == before
    assert revocation.folder_access(profile.home, profile.identity['organizationId'])['paused']

    revocation.review_revocation(profile.home, _review_params(profile, 'root1'))
    resumed = OrganizationStore(profile.path)
    assert resumed.settings.revoked_root_aliases == ('root0', 'root1')
    assert resumed.claim_next() is not None
    assert not revocation.folder_access(profile.home, profile.identity['organizationId'])['paused']


def test_old_review_replay_cannot_clear_a_later_revocation_hold(folder_profile):
    profile = folder_profile
    _register_second(profile)
    revocation.revoke_folder(profile.home, _revoke_params(profile, 'root0', 'revoke-first'))
    first = _review_params(profile, 'root0')
    original = revocation.review_revocation(profile.home, first)
    revocation.revoke_folder(profile.home, _revoke_params(profile, 'root1', 'revoke-later'))
    before = _dump(profile.path)

    assert revocation.review_revocation(profile.home, first) == original
    assert _dump(profile.path) == before
    view = revocation.folder_access(profile.home, profile.identity['organizationId'])
    assert view['paused']
    assert {item['alias']: item['reviewed'] for item in view['grants']} == {'root0': True, 'root1': False}
    assert OrganizationStore(profile.path).claim_next() is None
