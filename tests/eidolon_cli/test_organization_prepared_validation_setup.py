"""Live bounded preparation and owner sidecars never construct runtime services."""
from copy import deepcopy
import json
import sqlite3
from types import SimpleNamespace
import uuid

import pytest

from eidolon_cli import organization_prepared_validation_setup as setup
from eidolon_cli.organization_owner_policy import OwnerPolicyError
from eidolon_cli.organization_project_validation import digest
from tests.eidolon_cli.test_organization_prepared_validation_contract import prepared, raw
from tests.eidolon_cli.test_organization_root_grants import folder_profile
from tests.eidolon_cli.test_organization_owner_policy import forbid_execution, rows

pytestmark = pytest.mark.linux_only


@pytest.fixture
def owner(folder_profile, prepared):
    from eidolon_cli.organization_folder_setup import register_folder
    p = folder_profile
    register_folder(p.home, p.apply)
    files = {'package-lock.json': prepared['inputs']['lockfile_bytes'],
             **prepared['inputs']['package_manifests'],
             'apps/desktop/tsconfig.json': b'{"compilerOptions":{}}',
             'apps/desktop/vitest.config.ts': b'export default {}',
             'apps/desktop/src/main.test.tsx': b'reviewed fixture'}
    for path, data in files.items():
        destination = p.root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    dependency = p.root.parent / 'prepared-dependencies'
    dependencies = {
        'node_modules/typescript/package.json': raw({'name': 'typescript', 'version': '5.9.3'}),
        'node_modules/vitest/package.json': raw({'name': 'vitest', 'version': '3.2.4'}),
        'node_modules/typescript/lib/typescript.js': b'// inert compiler fixture',
        'node_modules/vitest/dist/node.js': b'// inert test fixture'}
    for path, data in dependencies.items():
        destination = dependency / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        destination.chmod(0o644)
    contract = prepared['contract']
    selection = {'projectId': 'notes', 'enabled': True, 'source': contract['source'],
                 'profile': contract['profile'],
                 'dependencies': {'directory': str(dependency), 'manifest': contract['dependencies']['manifest']}}
    view = setup.prepared_validation_setup(p.home, p.identity['organizationId'])
    params = {'expectedOrganizationId': view['organizationId'], 'expectedOwnerId': view['ownerId'],
              'expectedGeneration': view['policyGeneration'], 'sourceRevision': view['sourceRevision'],
              'expectedSidecarRevision': view['sidecarRevision'], 'selection': selection}
    return SimpleNamespace(**vars(p), dependency=dependency, selection=selection, view=view, preview_params=params)


def confirmed(owner):
    preview = setup.preview_prepared_validation(owner.home, owner.preview_params)
    params = {key: value for key, value in owner.preview_params.items() if key != 'selection'}
    return {**params, **{key: preview[key] for key in ('candidate', 'candidateHash', 'previewVersion')},
            'idempotencyKey': 'save-prepared', 'confirmed': True}


def revoke_params(owner, result):
    receipt = result['receipt']
    return {'expectedOrganizationId': receipt['organizationId'], 'expectedOwnerId': receipt['ownerId'],
            'expectedGeneration': receipt['policyGeneration'], 'expectedSidecarRevision': receipt['sidecarRevision'],
            'projectId': receipt['projectId'], 'registrationRevision': receipt['registrationRevision'],
            'registrationHash': receipt['candidateHash'], 'idempotencyKey': 'disable-prepared', 'confirmed': True}


def lookup(owner):
    with setup._connection(owner.home) as conn:
        return setup.current_prepared_registration(conn, 'notes', organization_id=owner.view['organizationId'],
            owner_id=owner.view['ownerId'], policy_generation=owner.view['policyGeneration'],
            source_revision=owner.view['sourceRevision'])


def test_preview_checks_live_bytes_save_is_sidecar_only_and_replay_exact(owner, monkeypatch):
    before = rows(owner.path)
    forbid_execution(monkeypatch)
    params = confirmed(owner)
    assert rows(owner.path) == before
    observation = params['candidate']['verification']
    assert observation['state'] == 'verified_bytes_runtime_unprobed'
    assert observation['stagingCleanupConfirmed'] is True
    assert observation['sourceFiles'] == len(owner.selection['source']['files'])
    assert observation['dependencyFiles'] == 4
    result = setup.save_prepared_validation(owner.home, params)
    assert setup.save_prepared_validation(owner.home, params) == result
    after = rows(owner.path)
    assert {name: after[name] for name in before} == before
    assert result['receipt']['policyGeneration'] == owner.view['policyGeneration']
    assert result['receipt']['sidecarRevision'] == 1
    assert result['receipt']['candidate']['contract']['revision'] == 1
    current = lookup(owner)
    assert current['registrationSha256'] == digest(params['candidate'])
    assert current['contract'] == params['candidate']['contract']
    view = setup.prepared_validation_setup(owner.home, owner.view['organizationId'])
    assert view['projects'][0]['recipe'] is None
    assert view['projects'][0]['enabled'] is True
    assert view['registrationRefs'] == [{'projectId': 'notes', 'revision': 1,
                                         'registrationSha256': current['registrationSha256']}]
    assert not (owner.home / 'config.yaml').exists()
    assert owner.root.joinpath('apps/desktop/src/main.test.tsx').read_bytes() == b'reviewed fixture'


@pytest.mark.parametrize('field,value', [
    ('expectedOrganizationId', str(uuid.uuid4())), ('expectedOwnerId', str(uuid.uuid4())),
    ('expectedGeneration', 999), ('expectedSidecarRevision', 9), ('sourceRevision', '0' * 64),
    ('expectedGeneration', True), ('expectedSidecarRevision', False),
])
def test_preview_rejects_stale_owner_policy_source_or_sidecar(owner, field, value):
    before = rows(owner.path)
    with pytest.raises((OwnerPolicyError, ValueError)):
        setup.preview_prepared_validation(owner.home, {**owner.preview_params, field: value})
    assert rows(owner.path) == before


@pytest.mark.parametrize('change', ['source', 'dependency', 'source_swap', 'dependency_swap', 'source_symlink',
                                    'dependency_symlink', 'candidate', 'hash', 'preview', 'confirmation'])
def test_save_rechecks_exact_review_and_live_directory_bytes(owner, change):
    params = confirmed(owner)
    before = rows(owner.path)
    if change == 'source':
        (owner.root / 'apps/desktop/tsconfig.json').write_text('{"changed":true}')
    elif change == 'dependency':
        (owner.dependency / 'node_modules/typescript/lib/typescript.js').write_bytes(b'// wrong compiler bytes')
    elif change.endswith('_swap'):
        path = owner.root if change == 'source_swap' else owner.dependency
        path.rename(path.with_name(path.name + '-retained'))
        path.mkdir()
    elif change.endswith('_symlink'):
        path = owner.root if change == 'source_symlink' else owner.dependency
        old = path.with_name(path.name + '-retained')
        path.rename(old)
        path.symlink_to(old, target_is_directory=True)
    elif change == 'candidate':
        params['candidate']['contract']['runtime']['allowLocalDocker'] = False
        params['candidateHash'] = digest(params['candidate'])
    elif change == 'hash':
        params['candidateHash'] = '0' * 64
    elif change == 'preview':
        params['previewVersion'] = '0' * 64
    else:
        params['confirmed'] = False
    with pytest.raises((OwnerPolicyError, ValueError, OSError)):
        setup.save_prepared_validation(owner.home, params)
    assert rows(owner.path) == before


def test_sidecar_cas_idempotency_and_immutable_history(owner):
    params = confirmed(owner)
    result = setup.save_prepared_validation(owner.home, params)
    with pytest.raises(OwnerPolicyError, match='idempotency_conflict'):
        setup.save_prepared_validation(owner.home, {**params, 'candidateHash': '0' * 64})
    with pytest.raises(OwnerPolicyError, match='sidecar_conflict'):
        setup.save_prepared_validation(owner.home, {**params, 'idempotencyKey': 'another'})
    with sqlite3.connect(owner.path) as conn:
        conn.execute('PRAGMA recursive_triggers=OFF')
        for table in (setup._TABLE, setup._RECEIPTS):
            with pytest.raises(sqlite3.IntegrityError, match='immutable'):
                conn.execute(f'INSERT OR REPLACE INTO {table} SELECT * FROM {table}')
            with pytest.raises(sqlite3.IntegrityError, match='immutable'):
                conn.execute(f'DELETE FROM {table}')
    assert setup.save_prepared_validation(owner.home, params) == result


def test_revoke_never_reads_removed_source_or_dependencies_and_retry_is_historical(owner, monkeypatch):
    params = confirmed(owner)
    result = setup.save_prepared_validation(owner.home, params)
    owner.root.rename(owner.root.with_name('removed-source'))
    owner.dependency.rename(owner.dependency.with_name('removed-dependencies'))
    view = setup.prepared_validation_setup(owner.home, owner.view['organizationId'])
    assert 'source_unreadable' in view['blockers']
    assert view['projects'][0]['enabled'] is True
    assert view['registrationRefs'] == []
    def forbidden(*args, **kwargs):
        raise AssertionError('Revocation may not depend on readable source or dependencies')
    monkeypatch.setattr(setup, '_source', forbidden)
    monkeypatch.setattr(setup, 'directory_identity', forbidden)
    forbid_execution(monkeypatch)
    revoke = revoke_params(owner, result)
    disabled = setup.revoke_prepared_validation(owner.home, revoke)
    assert disabled['receipt']['disabled'] is True
    assert disabled['receipt']['sidecarRevision'] == 2
    assert setup.revoke_prepared_validation(owner.home, revoke) == disabled
    assert lookup(owner) is None
    assert setup.save_prepared_validation(owner.home, params) == result
    assert lookup(owner) is None


@pytest.mark.parametrize('field,value', [('expectedOwnerId', str(uuid.uuid4())),
    ('expectedGeneration', 999), ('expectedSidecarRevision', 999), ('registrationRevision', 999),
    ('registrationHash', '0' * 64), ('confirmed', False)])
def test_revoke_requires_current_owner_and_exact_registration(owner, field, value):
    result = setup.save_prepared_validation(owner.home, confirmed(owner))
    before = rows(owner.path)
    with pytest.raises((ValueError, OwnerPolicyError)):
        setup.revoke_prepared_validation(owner.home, {**revoke_params(owner, result), field: value})
    assert rows(owner.path) == before
    assert lookup(owner)['enabled'] is True


def test_preparation_observes_midflight_policy_change_and_cleans_private_staging(owner, monkeypatch):
    original = setup.copy_verified_dependency_bundle
    destinations = []
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        destinations.append(args[1])
        with sqlite3.connect(owner.path) as conn:
            conn.execute('UPDATE organization_policy SET generation=generation+1 WHERE id=1')
        return result
    monkeypatch.setattr(setup, 'copy_verified_dependency_bundle', changed)
    with pytest.raises(OwnerPolicyError, match='generation_conflict'):
        setup.preview_prepared_validation(owner.home, owner.preview_params)
    assert destinations and all(not path.exists() for path in destinations)
    assert not any(name.startswith('organization_prepared') for name in rows(owner.path))


def test_root_revocation_and_stale_policy_fail_closed_for_future_objective_lookup(owner):
    from eidolon_cli.organization_folder_revocation import folder_access, revoke_folder
    setup.save_prepared_validation(owner.home, confirmed(owner))
    view = folder_access(owner.home, owner.view['organizationId'])
    revoke_folder(owner.home, {'expectedOrganizationId': view['organizationId'],
        'expectedGeneration': view['policyGeneration'], 'alias': 'root0',
        'grantHash': view['grants'][0]['grantHash'], 'confirmed': True, 'idempotencyKey': 'root-revoke'})
    with pytest.raises(OwnerPolicyError, match='generation_conflict'):
        lookup(owner)
    descriptor = setup.prepared_validation_setup(owner.home, owner.view['organizationId'])
    assert descriptor['projects'][0]['rootRevoked'] is True
    assert descriptor['registrationRefs'] == []


def test_duplicate_or_command_inputs_never_broaden_preparation(owner):
    for modification in (
        {'enabled': False}, {'command': 'npm install'}, {'projectId': 'missing'},
        {'source': {**owner.selection['source'], 'files': ['.env']}},
        {'source': {**owner.selection['source'], 'files': ['apps/desktop/src']}},
        {'dependencies': {**owner.selection['dependencies'], 'install': True}},
    ):
        with pytest.raises(ValueError):
            setup.preview_prepared_validation(owner.home,
                {**owner.preview_params, 'selection': {**owner.selection, **modification}})


def test_live_lookup_compares_actual_owner_policy_and_directory_identity(owner):
    setup.save_prepared_validation(owner.home, confirmed(owner))
    with setup._connection(owner.home) as conn:
        with pytest.raises(OwnerPolicyError, match='source_conflict'):
            setup.current_prepared_registration(conn, 'notes', organization_id=owner.view['organizationId'],
                owner_id=owner.view['ownerId'], policy_generation=owner.view['policyGeneration'], source_revision='0' * 64)
    owner.dependency.rename(owner.dependency.with_name('old-dependencies'))
    owner.dependency.mkdir()
    with pytest.raises(OwnerPolicyError, match='directory_identity_conflict'):
        lookup(owner)


def test_disabled_registration_never_revives_an_older_reference(owner, monkeypatch):
    first = setup.save_prepared_validation(owner.home, confirmed(owner))
    setup.revoke_prepared_validation(owner.home, revoke_params(owner, first))
    assert lookup(owner) is None
    view = setup.prepared_validation_setup(owner.home, owner.view['organizationId'])
    owner.preview_params['expectedSidecarRevision'] = view['sidecarRevision']
    params = confirmed(owner)
    params['idempotencyKey'] = 'explicit-reenable'
    second = setup.save_prepared_validation(owner.home, params)
    assert second['receipt']['registrationRevision'] == 3
    assert second['receipt']['candidateHash'] != first['receipt']['candidateHash']
    assert lookup(owner)['revision'] == 3
    # Even at registration capacity, a small revocation record can be appended.
    monkeypatch.setattr(setup, '_MAX_REGISTRATIONS', 3)
    setup.revoke_prepared_validation(owner.home, {**revoke_params(owner, second), 'idempotencyKey': 'disable-again'})
    assert lookup(owner) is None


def test_sidecar_preflights_blob_lengths_before_loading_and_stores_receipts_once(owner, monkeypatch):
    result = setup.save_prepared_validation(owner.home, confirmed(owner))
    with setup._connection(owner.home) as conn:
        stored = conn.execute(f'SELECT receipt FROM {setup._TABLE}').fetchone()[0]
        assert 'candidate' not in json.loads(stored)
        metadata = setup._sidecar_rows(conn)
        assert 'candidate' not in metadata[0].keys() and 'receipt' not in metadata[0].keys()
        assert setup._record(conn, metadata[0])[1] == result['receipt']
        monkeypatch.setattr(setup, '_MAX_CANDIDATE_BYTES', 100)
        queries = []
        conn.set_trace_callback(queries.append)
        with pytest.raises(OwnerPolicyError, match='prepared_registration_corrupt'):
            setup._sidecar_rows(conn)
        assert not any(query.startswith('SELECT candidate,receipt') for query in queries)


def test_dependency_swap_and_restore_cannot_verify_an_unapproved_opened_root(owner, monkeypatch):
    import shutil
    original = setup.copy_verified_dependency_bundle
    old = owner.dependency.with_name('original-dependencies')
    def swapped(*args, **kwargs):
        assert kwargs['expected_source_identity'] == (owner.dependency.stat().st_dev, owner.dependency.stat().st_ino)
        owner.dependency.rename(old)
        shutil.copytree(old, owner.dependency)
        try:
            return original(*args, **kwargs)
        finally:
            shutil.rmtree(owner.dependency)
            old.rename(owner.dependency)
    monkeypatch.setattr(setup, 'copy_verified_dependency_bundle', swapped)
    before = rows(owner.path)
    with pytest.raises(ValueError, match='identity'):
        confirmed(owner)
    assert rows(owner.path) == before


def check_registration(owner, registration, **overrides):
    with setup._connection(owner.home) as conn:
        return setup.check_current_prepared_registration(conn, registration,
            **{'organization_id': owner.view['organizationId'], 'owner_id': owner.view['ownerId'],
               'policy_generation': owner.view['policyGeneration'], 'source_revision': owner.view['sourceRevision'],
               **overrides})


def test_checkpoint_fence_reads_only_scalar_sidecar_columns_without_materializing_contract(owner, monkeypatch):
    setup.save_prepared_validation(owner.home, confirmed(owner))
    registration = lookup(owner)
    forbid_execution(monkeypatch)
    def forbidden(*args, **kwargs):
        pytest.fail('Checkpoint must not read, hash, or normalize a full contract/history')
    for name in ('_record', '_sidecar_rows', 'digest', 'normalize_prepared_validation_contract'):
        monkeypatch.setattr(setup, name, forbidden)
    with setup._connection(owner.home) as conn:
        def authorize(action, table, column, *_):
            if action == sqlite3.SQLITE_READ and table == setup._TABLE and column in {'candidate', 'receipt'}:
                pytest.fail('Checkpoint fetched a manifest-bearing blob')
            return sqlite3.SQLITE_OK
        conn.set_authorizer(authorize)
        for _ in range(3):
            assert setup.check_current_prepared_registration(conn, registration,
                organization_id=owner.view['organizationId'], owner_id=owner.view['ownerId'],
                policy_generation=owner.view['policyGeneration'], source_revision=owner.view['sourceRevision']) is None


@pytest.mark.parametrize('change', ['policy', 'source', 'root_revoke', 'root_swap',
                                   'dependency_swap', 'dependency_symlink', 'owner', 'schema'])
def test_checkpoint_fence_rechecks_fresh_authority_and_directory_identity(owner, change):
    setup.save_prepared_validation(owner.home, confirmed(owner))
    registration = lookup(owner)
    check_registration(owner, registration)
    if change == 'policy':
        with sqlite3.connect(owner.path) as conn:
            conn.execute('UPDATE organization_policy SET generation=generation+1 WHERE id=1')
    elif change == 'source':
        (owner.home / 'config.yaml').write_text('# changed configuration bytes\n')
    elif change == 'root_revoke':
        from eidolon_cli.organization_folder_revocation import folder_access, revoke_folder
        view = folder_access(owner.home, owner.view['organizationId'])
        revoke_folder(owner.home, {'expectedOrganizationId': view['organizationId'],
            'expectedGeneration': view['policyGeneration'], 'alias': 'root0',
            'grantHash': view['grants'][0]['grantHash'], 'confirmed': True, 'idempotencyKey': 'checkpoint-root-revoke'})
    elif change in {'root_swap', 'dependency_swap', 'dependency_symlink'}:
        path = owner.root if change == 'root_swap' else owner.dependency
        retained = path.with_name(path.name + '-retained')
        path.rename(retained)
        if change.endswith('symlink'):
            path.symlink_to(retained, target_is_directory=True)
        else:
            path.mkdir()
    elif change == 'owner':
        with sqlite3.connect(owner.path) as conn:
            conn.execute('UPDATE organization_metadata SET owner_id=?', (str(uuid.uuid4()),))
    else:
        with sqlite3.connect(owner.path) as conn:
            conn.execute(f'DROP TRIGGER {setup._TABLE}_no_replace')
    with pytest.raises((OwnerPolicyError, ValueError)):
        check_registration(owner, registration)


def test_checkpoint_fence_revoke_and_reenable_never_revive_captured_registration(owner):
    first = setup.save_prepared_validation(owner.home, confirmed(owner))
    registration = lookup(owner)
    setup.revoke_prepared_validation(owner.home, revoke_params(owner, first))
    with pytest.raises(OwnerPolicyError, match='prepared_registration_conflict'):
        check_registration(owner, registration)
    owner.preview_params['expectedSidecarRevision'] = 2
    setup.save_prepared_validation(owner.home, {**confirmed(owner), 'idempotencyKey': 'checkpoint-reenable'})
    with pytest.raises(OwnerPolicyError, match='prepared_registration_conflict'):
        check_registration(owner, registration)
    check_registration(owner, lookup(owner))


def test_checkpoint_fence_selected_row_sequence_is_not_unrelated_global_revision(owner):
    import shutil
    from eidolon_cli.organization_folder_setup import folder_setup, preview_folder, register_folder
    other = owner.root.with_name('other-project')
    shutil.copytree(owner.root, other)
    view = folder_setup(owner.home, owner.view['organizationId'])
    params = {'expectedOrganizationId': view['organizationId'], 'expectedGeneration': view['policyGeneration'],
              'sourceRevision': view['sourceRevision'],
              'folder': {'path': str(other), 'projectId': 'other', 'team': 'general'}}
    preview = preview_folder(owner.home, params)
    register_folder(owner.home, {**{key: value for key, value in params.items() if key != 'folder'},
        'candidate': preview['candidate'], 'previewVersion': preview['previewVersion'],
        'idempotencyKey': 'other-root', 'confirmed': True})
    owner.view = setup.prepared_validation_setup(owner.home, owner.view['organizationId'])
    owner.preview_params.update(expectedGeneration=owner.view['policyGeneration'], sourceRevision=owner.view['sourceRevision'])
    setup.save_prepared_validation(owner.home, confirmed(owner))
    registration = lookup(owner)
    owner.preview_params['selection'] = {**owner.selection, 'projectId': 'other'}
    owner.preview_params['expectedSidecarRevision'] = 1
    setup.save_prepared_validation(owner.home, {**confirmed(owner), 'idempotencyKey': 'other-registration'})
    assert setup.prepared_validation_setup(owner.home, owner.view['organizationId'])['sidecarRevision'] == 2
    assert registration['sidecarRevision'] == 1
    check_registration(owner, registration)


@pytest.mark.parametrize('field,value', [('sidecarRevision', 999), ('revision', 999),
    ('registrationSha256', '0' * 64), ('enabled', False), ('policyGeneration', True)])
def test_checkpoint_fence_requires_exact_captured_registration(owner, field, value):
    setup.save_prepared_validation(owner.home, confirmed(owner))
    registration = lookup(owner)
    with pytest.raises((OwnerPolicyError, ValueError)):
        check_registration(owner, {**registration, field: value})
