"""Real root descriptors and SQLite provenance for reviewed folder registration."""
from dataclasses import replace
import json
import os
import sqlite3

import pytest

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_root_grants import ROOT_GRANT_SCHEMA, check_new_root, directory_identity, resolve_root_grants


pytestmark = pytest.mark.skipif(os.name != 'posix', reason='Folder grants require no-follow POSIX descriptors')


def ledger():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript(ROOT_GRANT_SCHEMA)
    return conn


def add(conn, path, prefix=(), index=0):
    identity = directory_identity(str(path))
    conn.execute('INSERT INTO organization_root_grants VALUES (?,?,?,?,?)',
                 (f'root{index}', str(path), json.dumps(list(prefix)), identity['device'], identity['inode']))


def test_roots_resolve_once_and_preserve_source_prefix(tmp_path):
    source, first, second = [tmp_path / name for name in ['source', 'first', 'second']]
    for path in [source, first, second]: path.mkdir()
    conn = ledger()
    base = replace(OrganizationSettings(), read_roots=(str(source),))
    add(conn, first, base.read_roots, 1)
    resolved = resolve_root_grants(conn, base)
    assert resolved.read_roots == (str(source), str(first))
    assert resolve_root_grants(conn, resolved) == resolved
    add(conn, second, base.read_roots, 2)
    resolved = resolve_root_grants(conn, resolved)
    assert resolved.read_roots == (str(source), str(first), str(second))
    assert resolved.ledger_read_roots == (str(first), str(second))
    assert resolve_root_grants(conn, resolved) == resolved
    with pytest.raises(ValueError, match='source aliases changed'):
        resolve_root_grants(conn, replace(base, read_roots=()))


def test_replaced_folder_identity_and_overlap_are_rejected(tmp_path):
    first, other = tmp_path / 'first', tmp_path / 'other'
    first.mkdir(); other.mkdir()
    conn = ledger(); add(conn, first)
    first.rename(tmp_path / 'retained'); first.mkdir()
    with pytest.raises(ValueError, match='identity changed'):
        resolve_root_grants(conn, OrganizationSettings())
    settings = replace(OrganizationSettings(), read_roots=(str(other),))
    nested = other / 'nested'; nested.mkdir()
    for path in [other, nested, tmp_path]:
        with pytest.raises(ValueError, match='distinct'):
            check_new_root(str(path), settings)
    link = tmp_path / 'link'; link.symlink_to(other, target_is_directory=True)
    with pytest.raises(ValueError, match='symbolic'):
        directory_identity(str(link))
    assert check_new_root(str(first), settings)


def test_provenance_cannot_be_injected_by_source_config(tmp_path):
    settings = OrganizationSettings.from_config({'organization': {'ledger_read_roots': ['/other']}})
    assert settings.ledger_read_roots == ()
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        conn = ledger(); root = tmp_path / 'root'; root.mkdir(); add(conn, root)
        conn.execute('DELETE FROM organization_root_grants')


def test_review_register_reload_two_folders_without_yaml_or_tool_grants(tmp_path, monkeypatch):
    from eidolon_cli import organization_metadata as metadata
    from eidolon_cli.organization_store import OrganizationStore
    from eidolon_cli.organization_folder_setup import folder_setup, preview_folder, register_folder
    from eidolon_cli.organization_owner_policy import OwnerPolicyError
    home = tmp_path / 'profile'; home.mkdir()
    monkeypatch.setenv('EIDOLON_HOME', str(home))
    monkeypatch.setenv('HERMES_HOME', str(home))
    path = home / 'organization' / 'state.db'
    identity = metadata.save_metadata(path, display_name='Folder organization', owner_display_name='Owner',
        expected_organization_id=None, expected_revision=None, idempotency_key='identity')['metadata']
    store = OrganizationStore(path)
    folders = [tmp_path / 'notes', tmp_path / 'other']
    for root in folders: root.mkdir()
    receipts = []
    for i, root in enumerate(folders):
        view = folder_setup(home, identity['organizationId'])
        params = {'expectedOrganizationId': identity['organizationId'], 'expectedGeneration': view['policyGeneration'],
                  'sourceRevision': view['sourceRevision'], 'folder': {'path': str(root), 'projectId': f'project{i}', 'team': 'general'}}
        preview = preview_folder(home, params)
        with sqlite3.connect(path) as conn:
            assert conn.execute('SELECT COUNT(*) FROM organization_root_grants').fetchone()[0] == i
        apply = {key: value for key, value in params.items() if key != 'folder'}
        apply.update(candidate=preview['candidate'], previewVersion=preview['previewVersion'], idempotencyKey=f'folder{i}', confirmed=True)
        bad = {**apply, 'previewVersion': '0' * 64}
        with pytest.raises(OwnerPolicyError): register_folder(home, bad)
        result = register_folder(home, apply)
        assert register_folder(home, apply) == result
        receipts.append((apply, result))
        reopened = OrganizationStore(path)
        assert reopened.settings.read_roots == tuple(str(root) for root in folders[:i+1])
        assert reopened.settings.tool_grants == ()
        assert [p.root for p in reopened.settings.projects] == [f'root{j}' for j in range(i+1)]
    assert register_folder(home, receipts[0][0]) == receipts[0][1]
    assert not (home / 'config.yaml').exists()
    assert all(list(root.iterdir()) == [] for root in folders)
    assert not store.snapshot()['objectives']


def test_descriptor_open_checks_reviewed_identity_after_replacement(tmp_path):
    from tools.organization_file_read import organization_file_read_scope, OrganizationFileReadError
    root = tmp_path / 'root'; root.mkdir()
    identity = directory_identity(str(root))
    root.rename(tmp_path / 'old'); root.mkdir()
    (root / 'secret.txt').write_text('replacement data')
    with pytest.raises(OrganizationFileReadError, match='identity changed'):
        with organization_file_read_scope((str(root),), 1000,
            expected_root_identities={'root0': (identity['device'], identity['inode'])}):
            pytest.fail('A substituted root must not enter the read scope')


@pytest.fixture
def folder_profile(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from eidolon_cli import organization_metadata as metadata
    from eidolon_cli.organization_store import OrganizationStore
    from eidolon_cli.organization_folder_setup import folder_setup, preview_folder
    home = tmp_path / 'profile'; home.mkdir()
    monkeypatch.setenv('EIDOLON_HOME', str(home)); monkeypatch.setenv('HERMES_HOME', str(home))
    path = home / 'organization' / 'state.db'
    identity = metadata.save_metadata(path, display_name='Folders', owner_display_name='Owner',
        expected_organization_id=None, expected_revision=None, idempotency_key='identity')['metadata']
    store = OrganizationStore(path)
    root = tmp_path / 'root'; root.mkdir()
    view = folder_setup(home, identity['organizationId'])
    params = {'expectedOrganizationId': identity['organizationId'], 'expectedGeneration': view['policyGeneration'],
              'sourceRevision': view['sourceRevision'], 'folder': {'path': str(root), 'projectId': 'notes', 'team': 'general'}}
    preview = preview_folder(home, params)
    apply = {key: value for key, value in params.items() if key != 'folder'}
    apply.update(candidate=preview['candidate'], previewVersion=preview['previewVersion'], idempotencyKey='grant', confirmed=True)
    return SimpleNamespace(home=home, path=path, store=store, root=root, params=params, apply=apply, identity=identity)


@pytest.mark.parametrize('attack', ['generation', 'source', 'owner', 'candidate', 'confirmed', 'active', 'managed', 'source_file', 'path_swap'])
def test_registration_rejects_changed_authority_atomically(folder_profile, monkeypatch, attack):
    from copy import deepcopy
    import uuid
    from eidolon_cli.organization_folder_setup import register_folder
    from eidolon_cli.organization_owner_policy import OwnerPolicyError
    p = folder_profile; params = deepcopy(p.apply)
    if attack == 'generation': params['expectedGeneration'] += 1
    if attack == 'source': params['sourceRevision'] = '0' * 64
    if attack == 'owner': params['expectedOrganizationId'] = str(uuid.uuid4())
    if attack == 'candidate': params['candidate']['project']['id'] = 'other'
    if attack == 'confirmed': params['confirmed'] = False
    if attack == 'active': p.store.create_objective('Keep active', idempotency_key='active')
    if attack == 'managed': monkeypatch.setattr('eidolon_cli.organization_folder_setup.roots_managed', lambda: True)
    if attack == 'source_file': (p.home / 'config.yaml').write_text('{}')
    if attack == 'path_swap': p.root.rename(p.root.with_name('old')); p.root.mkdir()
    with pytest.raises((ValueError, OwnerPolicyError)):
        register_folder(p.home, params)
    with sqlite3.connect(p.path) as conn:
        for table in ['organization_root_grants', 'organization_project_registry', 'organization_folder_receipts']:
            assert conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0


def test_registration_rolls_back_failure_after_insertion(folder_profile, monkeypatch):
    from eidolon_cli.organization_folder_setup import register_folder
    from eidolon_cli.organization_store import OrganizationStore
    def fail(*args, **kwargs): raise RuntimeError('Injected adoption failure')
    monkeypatch.setattr(OrganizationStore, '_reload_configuration', fail)
    with pytest.raises(RuntimeError, match='Injected'):
        register_folder(folder_profile.home, folder_profile.apply)
    with sqlite3.connect(folder_profile.path) as conn:
        for table in ['organization_root_grants', 'organization_project_registry', 'organization_folder_receipts']:
            assert conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0


def test_exact_receipt_replays_after_directory_and_source_drift(folder_profile):
    from eidolon_cli.organization_folder_setup import register_folder
    from eidolon_cli.organization_owner_policy import OwnerPolicyError
    p = folder_profile
    result = register_folder(p.home, p.apply)
    p.root.rename(p.root.with_name('old')); p.root.mkdir()
    (p.home / 'config.yaml').write_text('{}')
    assert register_folder(p.home, p.apply) == result
    with pytest.raises(OwnerPolicyError, match='idempotency_conflict'):
        register_folder(p.home, {**p.apply, 'sourceRevision': '0' * 64})


def test_unwinding_execution_capacity_blocks_registration(folder_profile):
    from eidolon_cli.organization_service import _ExecutionLock
    from eidolon_cli.organization_folder_setup import register_folder
    from eidolon_cli.organization_owner_policy import OwnerPolicyError
    p = folder_profile
    with _ExecutionLock(p.home / 'organization' / 'capacity-locks', '2') as held:
        assert held
        with pytest.raises(OwnerPolicyError, match='execution_still_stopping'):
            register_folder(p.home, p.apply)
    assert register_folder(p.home, p.apply)['receipt']['candidate']['project']['id'] == 'notes'


def test_concurrent_registration_replays_one_atomic_receipt(folder_profile):
    from concurrent.futures import ThreadPoolExecutor
    from eidolon_cli.organization_folder_setup import register_folder
    p = folder_profile
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(register_folder, p.home, p.apply) for _ in range(2)]
        results = [future.result(timeout=10) for future in futures]
    assert results[0] == results[1]
    with sqlite3.connect(p.path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM organization_root_grants').fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM organization_folder_receipts').fetchone()[0] == 1
