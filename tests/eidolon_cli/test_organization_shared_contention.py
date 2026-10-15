"""Transient storage contention defers admission without weakening hard limits."""
import sqlite3
import pytest
from contextlib import closing

from tests.eidolon_cli.test_organization_shared_runtime import installation, configure, objective, shared_dump
from eidolon_cli.organization_shared_allowance import SharedAllowanceError


@pytest.mark.parametrize("locked_database", ["shared", "root_metadata"])
def test_controlled_shared_ledger_lock_records_admission_reason(installation, locked_database):
    root = installation
    configure(root, cost='0.3')
    item = objective(root.alpha)
    retained_shared = shared_dump(root)
    def unchanged_admission_accounting():
        assert shared_dump(root) == retained_shared
        with root.alpha.store._connect() as conn:
            for table in ('organization_model_calls', 'organization_shared_call_receipts'):
                assert conn.execute('SELECT count(*) FROM ' + table).fetchone()[0] == 0
    path = root.ledger.path if locked_database == 'shared' else root.store.path
    with closing(sqlite3.connect(path, isolation_level=None)) as blocker:
        blocker.execute('PRAGMA journal_mode=DELETE')
        blocker.execute('BEGIN EXCLUSIVE')
        assert root.alpha.store.claim_next() is None
        blocker.rollback()
    with root.alpha.store._connect() as conn:
        row = conn.execute('SELECT status,reason,attempts FROM requests WHERE objective_id=?', (item['id'],)).fetchone()
        assert row['status'] == 'queued'
        assert 'locked' in row['reason']
        assert row['attempts'] == 0
    unchanged_admission_accounting()
    assert root.alpha.store.claim_next() is None  # Contention backoff prevents a hot loop.
    root.now[0] += 1
    claim = root.alpha.store.claim_next()
    assert claim is not None
    assert claim['objective_id'] == item['id']
    unchanged_admission_accounting()
    assert root.alpha.store.claim_next() is None


def test_controlled_metadata_lock_preserves_storage_reason(installation, monkeypatch):
    from eidolon_cli.organization_metadata import MetadataError
    import eidolon_cli.organization_shared_allowance as shared
    root = installation
    configure(root)
    def locked_identity(*args, **kwargs):
        try:
            raise MetadataError('locked')
        except MetadataError:
            raise SharedAllowanceError('organization_identity_locked') from None
    monkeypatch.setattr(shared, '_identity', locked_identity)
    try:
        with root.ledger._connection():
            pass
    except SharedAllowanceError as exc:
        assert exc.reason == 'organization_identity_locked'
    else:
        raise AssertionError('Lock must prevent admission')


@pytest.mark.parametrize('reason', ['pool_identity_mismatch', 'unreadable_or_corrupt', 'organization_identity_corrupt', 'exhausted_configuredCostUsd'])
def test_permanent_shared_errors_remain_intervention(installation, monkeypatch, reason):
    import eidolon_cli.organization_shared_allowance as shared
    root = installation
    configure(root)
    item = objective(root.alpha)
    def unavailable(*args, **kwargs):
        raise SharedAllowanceError(reason)
    monkeypatch.setattr(shared, 'resolve_binding', unavailable)
    assert root.alpha.store.claim_next() is None
    with root.alpha.store._connect() as conn:
        row = conn.execute('SELECT status,reason,attempts FROM requests WHERE objective_id=?', (item['id'],)).fetchone()
        assert row['status'] == 'pending_intervention'
        assert reason in row['reason']
        assert row['attempts'] == 0


@pytest.mark.parametrize('action', ['cancel', 'deadline'])
def test_deferred_claim_rechecks_cancellation_and_deadline(installation, action):
    root = installation
    configure(root)
    item = objective(root.alpha)
    with closing(sqlite3.connect(root.ledger.path, isolation_level=None)) as blocker:
        blocker.execute('BEGIN EXCLUSIVE')
        assert root.alpha.store.claim_next() is None
        blocker.rollback()
    if action == 'cancel':
        root.alpha.store.cancel(item['id'])
        root.now[0] += 1
    else:
        root.now[0] += 86400 * 30
    assert root.alpha.store.claim_next() is None
    with root.alpha.store._connect() as conn:
        assert conn.execute('SELECT count(*) FROM organization_model_calls').fetchone()[0] == 0
        assert conn.execute('SELECT max(attempts) FROM requests').fetchone()[0] == 0


def test_deferred_claim_rechecks_changed_shared_allocation(installation):
    root = installation
    configure(root)
    item = objective(root.alpha)
    with closing(sqlite3.connect(root.ledger.path, isolation_level=None)) as blocker:
        blocker.execute('BEGIN EXCLUSIVE')
        assert root.alpha.store.claim_next() is None
        blocker.rollback()
    configuration = root.ledger.read()['configuration']
    allocation = next(a for a in configuration['allocations'] if a['profile'] == 'alpha')
    allocation['protected'] = dict(allocation['limits'])
    root.ledger.configure(configuration, expected_revision=1, idempotency_key='protect-all',
        root_organization_id=root.organization_id, root_owner_id=root.owner_id)
    root.now[0] += 1
    assert root.alpha.store.claim_next() is None
    with root.alpha.store._connect() as conn:
        row = conn.execute('SELECT status,reason,attempts FROM requests WHERE objective_id=?', (item['id'],)).fetchone()
        assert row['status'] == 'pending_intervention'
        assert 'capacity is exhausted' in row['reason']
        assert row['attempts'] == 0
        assert conn.execute('SELECT count(*) FROM organization_model_calls').fetchone()[0] == 0
