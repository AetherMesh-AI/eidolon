"""Metadata transactions preserve execution state and original intent receipts."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading
import uuid

import pytest

from eidolon_cli import organization_metadata as metadata
from eidolon_cli.organization_metadata import MetadataError, read_descriptor, save_metadata


def save(path, key='initialize', previous=None, **changes):
    return save_metadata(path, display_name=changes.get('name', 'Research organization'),
                         owner_display_name=changes.get('owner', 'Alex'),
                         expected_organization_id=previous['organizationId'] if previous else None,
                         expected_revision=previous['revision'] if previous else None,
                         idempotency_key=key)


def rows(path):
    with sqlite3.connect(path) as conn:
        return {name: conn.execute(f'SELECT * FROM "{name}"').fetchall()
                for name, in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_concurrent_initialization_one_winner_and_retry_survives_new_revision(tmp_path):
    path = tmp_path / 'organization' / 'state.db'
    barrier = threading.Barrier(2)
    def initialize(key):
        barrier.wait(timeout=5)
        try:
            return key, save(path, key)
        except MetadataError as exc:
            return key, exc.reason
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(initialize, key) for key in ('one', 'two')]
        outcomes = [future.result(timeout=10) for future in futures]
    winner = next((key, result) for key, result in outcomes if isinstance(result, dict))
    loser = next(result for _, result in outcomes if isinstance(result, str))
    assert loser == 'identity_conflict'
    original = winner[1]
    updated = save(path, 'rename', original['metadata'], name='Next name', owner='Sam')
    assert updated['metadata']['revision'] == original['metadata']['revision'] + 1
    assert updated['metadata']['organizationId'] == original['metadata']['organizationId']
    assert updated['metadata']['owner']['ownerId'] == original['metadata']['owner']['ownerId']
    assert save(path, winner[0]) == original  # Before the now-stale absent-record CAS.
    assert save(path, 'rename', original['metadata'], name='Next name', owner='Sam') == updated
    with pytest.raises(MetadataError, match='idempotency_conflict'):
        save(path, winner[0], name='Different intent')
    with pytest.raises(MetadataError, match='revision_conflict'):
        save(path, 'stale', original['metadata'], name='Stale rename')
    assert read_descriptor(path) == updated
    assert len(rows(path)['organization_metadata_audit']) == 2
    assert len(rows(path)['organization_metadata_receipts']) == 2


def test_metadata_preserves_all_existing_execution_records_and_later_store_initialization(tmp_path):
    from eidolon_cli.organization_store import OrganizationStore
    from tests.organization_identity_helpers import _work_claim, _complete
    path = tmp_path / 'organization' / 'state.db'
    original = save(path)['metadata']
    # Metadata-only storage must not seed any execution tables or owner agent.
    assert set(rows(path)) == {'organization_metadata', 'organization_metadata_audit', 'organization_metadata_receipts'}
    store = OrganizationStore(path)
    _, claim = _work_claim(store, 'retained-objective', agentId='worker-1')
    assert store.finish(claim, {'summary': 'Retained findings', 'deliverable': 'Durable evidence',
                               'memory': {'facts': ['Preserve this context.']}})
    _complete(store)
    before = {name: values for name, values in rows(path).items() if not name.startswith('organization_metadata')}
    saved = save(path, 'rename', original, owner='Displayed owner')
    after = {name: values for name, values in rows(path).items() if not name.startswith('organization_metadata')}
    assert before == after  # Includes agents, history, conversations, policy, grants, budgets and evidence.
    assert saved['metadata']['owner']['ownerId'] != 'owner'
    assert saved['metadata']['owner']['ownerId'] == original['owner']['ownerId']
    OrganizationStore(path)
    assert read_descriptor(path) == saved


def test_backup_restore_and_copied_uuid_remain_independent_ledgers(tmp_path):
    from eidolon_cli.backup import create_quick_snapshot, restore_quick_snapshot
    home = tmp_path / 'profile-a'; path = home / 'organization' / 'state.db'
    first = save(path)
    snapshot = create_quick_snapshot(hermes_home=home)
    assert snapshot
    save(path, 'rename', first['metadata'], name='Later name')
    assert restore_quick_snapshot(snapshot, hermes_home=home)
    assert read_descriptor(path) == first
    assert save(path) == first
    other = tmp_path / 'profile-b' / 'organization' / 'state.db'; other.parent.mkdir(parents=True)
    with sqlite3.connect(path) as source, sqlite3.connect(other) as destination:
        source.backup(destination)
    copied = read_descriptor(other)
    assert copied == first
    changed = save(other, 'only-b', copied['metadata'], name='Copied organization')
    assert changed['metadata']['organizationId'] == first['metadata']['organizationId']
    assert read_descriptor(path) == first
    assert read_descriptor(other) == changed


def test_interrupted_initialization_and_failed_receipt_leave_no_partial_change(tmp_path, monkeypatch):
    path = tmp_path / 'organization' / 'state.db'
    real = metadata._save_in_transaction
    def interrupt(*args):
        real(*args)
        raise KeyboardInterrupt('interrupted before commit')
    with monkeypatch.context() as patch:
        patch.setattr(metadata, '_save_in_transaction', interrupt)
        with pytest.raises(KeyboardInterrupt):
            save(path)
    assert rows(path) == {}
    first = save(path)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TRIGGER fail_receipt BEFORE INSERT ON organization_metadata_receipts "
                     "BEGIN SELECT RAISE(ABORT, 'receipt failed'); END")
    before = rows(path)
    with pytest.raises(MetadataError):
        save(path, 'rename', first['metadata'], name='Must roll back')
    assert rows(path) == before
    assert read_descriptor(path) == first


@pytest.mark.parametrize('change', [{'name': ''}, {'name': 'x'*161}, {'owner': 'A\nB'}, {'name': 'bad\u202ename'}])
def test_invalid_names_do_not_create_storage(tmp_path, change):
    path = tmp_path / 'organization' / 'state.db'
    with pytest.raises(ValueError):
        save(path, **change)
    assert not path.parent.exists()


def test_identity_and_revision_preconditions_reject_retargeting(tmp_path):
    path = tmp_path / 'state.db'; original = save(path)
    with pytest.raises(MetadataError, match='identity_conflict'):
        save(path, 'foreign', {**original['metadata'], 'organizationId': str(uuid.uuid4())})
    with pytest.raises(ValueError):
        save(path, 'bad-revision', {**original['metadata'], 'revision': True})
    assert read_descriptor(path) == original
    # A receipt from a replaced identity must not be replayed onto its replacement.
    with sqlite3.connect(path) as conn:
        conn.execute('UPDATE organization_metadata SET organization_id=?', (str(uuid.uuid4()),))
    with pytest.raises(MetadataError, match='identity_conflict'):
        save(path)


def test_process_restart_and_crash_recovery_are_atomic(tmp_path):
    import json
    import subprocess
    import sys
    path = tmp_path / 'state.db'
    original = save(path)
    read = subprocess.check_output([sys.executable, '-c',
        'import json,sys; from pathlib import Path; from eidolon_cli.organization_metadata import read_descriptor; '
        'print(json.dumps(read_descriptor(Path(sys.argv[1]))))', str(path)], text=True)
    assert json.loads(read) == original
    before = rows(path)
    child = subprocess.run([sys.executable, '-c', '''
import os,sys
from pathlib import Path
from eidolon_cli import organization_metadata as m
original = m._save_in_transaction
def crash(*args):
    original(*args)
    os._exit(17)
m._save_in_transaction = crash
record = m.read_descriptor(Path(sys.argv[1]))['metadata']
m.save_metadata(Path(sys.argv[1]), display_name='Uncommitted', owner_display_name='Alex',
    expected_organization_id=record['organizationId'], expected_revision=record['revision'], idempotency_key='crash')
''', str(path)], timeout=10)
    assert child.returncode == 17
    # Opening a writer performs SQLite crash recovery, then an identical retry
    # must still return the original result without any partial audit/receipt.
    assert save(path) == original
    assert rows(path) == before


def test_later_normal_service_initialization_preserves_metadata_without_starting(tmp_path, monkeypatch):
    from eidolon_cli import organization_service as services
    from eidolon_constants import set_eidolon_home_override, reset_eidolon_home_override
    home = tmp_path / 'profile'; path = home / 'organization' / 'state.db'
    original = save(path)
    monkeypatch.setattr(services, '_services', {})
    token = set_eidolon_home_override(home)
    try:
        service = services.get_service()
        assert not service.running
        assert service.store.snapshot()['objectives'] == []
        assert read_descriptor(path) == original
    finally:
        assert services.stop_services()
        reset_eidolon_home_override(token)


def test_stale_update_cannot_provision_missing_ledger(tmp_path):
    path = tmp_path / 'missing' / 'state.db'
    with pytest.raises(MetadataError, match='initialization_conflict'):
        save(path, 'stale', {'organizationId': str(uuid.uuid4()), 'revision': 1})
    assert not path.parent.exists()


def test_concurrent_updates_compare_revision_inside_write_lock(tmp_path):
    path = tmp_path / 'state.db'; first = save(path)['metadata']
    barrier = threading.Barrier(2)
    def update(name):
        barrier.wait(timeout=5)
        try:
            return save(path, name, first, name=name)
        except MetadataError as exc:
            return exc.reason
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(update, name) for name in ('A', 'B')]
        outcomes = [future.result(timeout=10) for future in futures]
    assert outcomes.count('revision_conflict') == 1
    winner = next(result for result in outcomes if isinstance(result, dict))
    assert read_descriptor(path) == winner
    assert winner['metadata']['revision'] == first['revision'] + 1
    assert len(rows(path)['organization_metadata_receipts']) == 2
