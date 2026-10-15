"""Real-ledger observation must not provision, adopt policy or drive execution."""
from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3
import threading

import pytest

from eidolon_cli.organization_metadata import save_metadata
from eidolon_cli.organization_observation import ObservationError, read_observation
from eidolon_cli.organization_service import OrganizationService
from eidolon_cli.organization_store import OrganizationStore


def files(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file() and not p.name.endswith(('-shm', '-wal'))}


def seed(home):
    store = OrganizationStore(home / 'organization' / 'state.db')
    store.create_objective('Observe the retained objective', idempotency_key='seed')
    return store


def forbid_runtime(monkeypatch):
    import eidolon_cli.organization_service as services
    def forbidden(*args, **kwargs):
        raise AssertionError('Observation attempted mutable initialization or scheduler activity')
    monkeypatch.setattr(OrganizationStore, '__init__', forbidden)
    monkeypatch.setattr(OrganizationStore, '_connect', forbidden)
    monkeypatch.setattr(OrganizationStore, '_write', forbidden)
    monkeypatch.setattr(services, 'get_service', forbidden)
    monkeypatch.setattr(OrganizationService, '__init__', forbidden)
    monkeypatch.setattr(OrganizationService, 'start', forbidden)
    monkeypatch.setattr(OrganizationService, 'refresh_configuration', forbidden)


def test_missing_and_metadata_only_do_not_initialize_execution(tmp_path, monkeypatch):
    forbid_runtime(monkeypatch)
    before = list(tmp_path.iterdir())
    assert read_observation(tmp_path)['reason'] == 'database_missing'
    assert list(tmp_path.iterdir()) == before
    path = tmp_path / 'organization' / 'state.db'
    save_metadata(path, display_name='Saved identity', owner_display_name='Owner',
                  expected_organization_id=None, expected_revision=None, idempotency_key='metadata')
    before = files(tmp_path)
    assert read_observation(tmp_path) == {'capabilityVersion': 1, 'state': 'unconfigured',
                                       'snapshot': None, 'reason': 'execution_not_initialized'}
    assert files(tmp_path) == before
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='objectives'").fetchone() is None


@pytest.mark.parametrize('damage', ['legacy', 'missing_table', 'missing_column', 'missing_migrated_column'])
def test_older_schema_is_unsupported_without_migration(tmp_path, damage):
    path = tmp_path / 'organization' / 'state.db'
    if damage == 'legacy':
        path.parent.mkdir()
        with sqlite3.connect(path) as conn:
            conn.execute('CREATE TABLE legacy (value TEXT)')
    else:
        seed(tmp_path)
        with sqlite3.connect(path) as conn:
            if damage == 'missing_table':
                conn.execute('DROP TABLE organization_management_audit')
            elif damage == 'missing_migrated_column':
                conn.execute('ALTER TABLE owner_chat_turns DROP COLUMN context_message_ids')
            else:
                conn.execute('ALTER TABLE organization_management_audit RENAME COLUMN before_state TO old_before')
    before = files(tmp_path)
    assert read_observation(tmp_path)['state'] == 'unsupported'
    assert files(tmp_path) == before


def test_corrupt_locked_and_unreadable_are_visible_failures(tmp_path):
    path = tmp_path / 'organization' / 'state.db'; path.parent.mkdir()
    path.write_bytes(b'corrupt sqlite')
    with pytest.raises(ObservationError, match='corrupt'):
        read_observation(tmp_path)
    path.unlink()
    seed(tmp_path)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('PRAGMA journal_mode=DELETE')
        conn.execute('BEGIN EXCLUSIVE')
        with pytest.raises(ObservationError, match='locked'):
            read_observation(tmp_path)
        conn.rollback()
    path.unlink(); path.mkdir()
    with pytest.raises(ObservationError, match='unreadable'):
        read_observation(tmp_path)


def test_projection_matches_existing_snapshot_and_preserves_all_durable_state(tmp_path, monkeypatch):
    store = seed(tmp_path)
    expected = store.snapshot()
    before = files(tmp_path)
    forbid_runtime(monkeypatch)
    observed = read_observation(tmp_path)
    assert observed['state'] == 'available'
    assert observed['snapshot'] == expected
    assert files(tmp_path) == before


def test_committed_wal_is_observed_not_ignored(tmp_path):
    store = seed(tmp_path)
    with closing(sqlite3.connect(store.path)) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.execute("UPDATE objectives SET title='Committed in WAL'")
        writer.commit()
        assert Path(str(store.path) + '-wal').stat().st_size > 0
        assert read_observation(tmp_path)['snapshot']['objectives'][0]['title'] == 'Committed in WAL'


def test_policy_change_and_pause_are_reported_without_adoption(tmp_path):
    store = seed(tmp_path)
    (tmp_path / 'config.yaml').write_text('organization:\n  max_workers: 3\n')
    before = files(tmp_path)
    result = read_observation(tmp_path)['snapshot']
    assert result['runtime']['state'] == 'policy_changed'
    assert result['runtime']['capabilities'] == []
    assert all(not r['allowedResolutions'] for r in result['requests'])
    assert files(tmp_path) == before
    (tmp_path / 'config.yaml').unlink()
    store.pause_configuration()
    before = files(tmp_path)
    assert read_observation(tmp_path)['snapshot']['runtime']['state'] == 'policy_changed'
    assert files(tmp_path) == before


def test_invalid_configuration_is_not_repaired_or_adopted(tmp_path):
    seed(tmp_path)
    (tmp_path / 'config.yaml').write_text('organization: [not, a, mapping]\n')
    before = files(tmp_path)
    with pytest.raises(ObservationError, match='configuration_invalid'):
        read_observation(tmp_path)
    assert files(tmp_path) == before


def test_stopped_scheduler_with_queued_work_is_not_started(tmp_path, monkeypatch):
    store = seed(tmp_path)
    calls = []
    service = OrganizationService(store, home=tmp_path, settings=store.settings,
                                  executor=lambda *args: calls.append(args), poll_seconds=0.01)
    before = files(tmp_path)
    forbid_runtime(monkeypatch)
    for _ in range(3):
        assert any(r['status'] == 'queued' for r in read_observation(tmp_path)['snapshot']['requests'])
    assert not service.running
    assert calls == []
    assert files(tmp_path) == before


def test_active_scheduler_continues_its_independently_authorized_work(tmp_path, monkeypatch):
    store = seed(tmp_path)
    entered, release = threading.Event(), threading.Event()
    calls = []
    def execute(request, context, cancel):
        calls.append(request['id']); entered.set()
        assert release.wait(20)
        return {'intervention': 'Deterministic fixture finished'}
    service = OrganizationService(store, home=tmp_path, settings=store.settings,
                                  executor=execute, poll_seconds=0.01)
    try:
        service.start()
        assert entered.wait(10)
        thread = service._thread
        # Reject observation-driven service entry without sabotaging the running
        # scheduler's own storage operations or independent heartbeat work.
        import eidolon_cli.organization_service as services
        monkeypatch.setattr(services, 'get_service', lambda: pytest.fail('Observation acquired a service'))
        monkeypatch.setattr(service, 'start', lambda: pytest.fail('Observation restarted the scheduler'))
        for _ in range(3):
            observed = read_observation(tmp_path)['snapshot']
            assert any(r['status'] == 'running' for r in observed['requests'])
            assert service.running and service._thread is thread
            assert len(calls) == 1
    finally:
        release.set()
        service.stop()


@pytest.mark.parametrize('statement', [
    "UPDATE objectives SET title='Unauthorized write'",
    'CREATE TABLE unauthorized (value TEXT)',
])
def test_sqlite_itself_denies_projection_writes(tmp_path, monkeypatch, statement):
    from eidolon_cli.organization_observation import _ObservationView
    seed(tmp_path)
    before = files(tmp_path)
    project = _ObservationView._snapshot
    def attempted_write(self, conn):
        assert conn.execute('PRAGMA query_only').fetchone()[0] == 1
        conn.execute('PRAGMA query_only=OFF')
        with pytest.raises(sqlite3.OperationalError, match='readonly'):
            conn.execute(statement)
        return project(self, conn)
    monkeypatch.setattr(_ObservationView, '_snapshot', attempted_write)
    assert read_observation(tmp_path)['state'] == 'available'
    assert files(tmp_path) == before


def test_corrupt_persisted_configuration_is_not_repaired(tmp_path):
    store = seed(tmp_path)
    with sqlite3.connect(store.path) as conn:
        conn.execute("INSERT INTO organization_configuration VALUES (1, 'not-json', 0)")
    before = files(tmp_path)
    with pytest.raises(ObservationError, match='corrupt'):
        read_observation(tmp_path)
    assert files(tmp_path) == before


@pytest.mark.parametrize('value', ['null', '[]', '{}', 'invalid-json'])
def test_corrupt_recorded_policy_is_visible(tmp_path, value):
    store = seed(tmp_path)
    with sqlite3.connect(store.path) as conn:
        conn.execute('UPDATE organization_policy SET settings=?', (value,))
    before = files(tmp_path)
    with pytest.raises(ObservationError, match='corrupt'):
        read_observation(tmp_path)
    assert files(tmp_path) == before


def test_completed_outcome_and_unresolved_intervention_remain_durable(tmp_path, monkeypatch):
    from tests.organization_outcomes_helpers import accepted
    store = OrganizationStore(tmp_path / 'organization' / 'state.db')
    completed, acceptance, evidence = accepted(store)
    blocked = store.create_objective('A question still needs an answer', idempotency_key='blocked')
    store.fail(store.claim_next(), 'Need owner input')
    expected = store.snapshot()
    before = files(tmp_path)
    forbid_runtime(monkeypatch)
    snapshot = read_observation(tmp_path)['snapshot']
    assert snapshot == expected
    outcome = next(item for item in snapshot['outcomes']['items'] if item['objectiveId'] == completed['id'])
    assert outcome['status'] == 'accepted'
    assert outcome['acceptanceRequestId'] == acceptance['id']
    assert outcome['evidenceIds'] == evidence
    assert any(item['objectiveId'] == blocked['id'] for item in snapshot['attention']['items'])
    assert files(tmp_path) == before


def test_projection_keeps_one_read_transaction_during_concurrent_commit(tmp_path, monkeypatch):
    from eidolon_cli.organization_observation import _ObservationView
    store = seed(tmp_path)
    original = store.snapshot()['objectives'][0]['title']
    project = _ObservationView._snapshot
    def concurrent_commit(self, conn):
        with sqlite3.connect(store.path) as writer:
            writer.execute("UPDATE objectives SET title='New committed title'")
        return project(self, conn)
    monkeypatch.setattr(_ObservationView, '_snapshot', concurrent_commit)
    assert read_observation(tmp_path)['snapshot']['objectives'][0]['title'] == original
    monkeypatch.setattr(_ObservationView, '_snapshot', project)
    assert read_observation(tmp_path)['snapshot']['objectives'][0]['title'] == 'New committed title'


def test_missing_optional_build_outputs_remains_passive(tmp_path, monkeypatch):
    store=seed(tmp_path)
    with sqlite3.connect(store.path) as conn:conn.execute('DROP TABLE project_build_outputs')
    before=files(tmp_path)
    forbid_runtime(monkeypatch)
    assert read_observation(tmp_path)['state'] == 'available'
    assert files(tmp_path)==before
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='project_build_outputs'").fetchone() is None
