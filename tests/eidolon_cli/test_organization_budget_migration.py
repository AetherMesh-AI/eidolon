"""Warm startup scales with missing budgets, without rewriting durable ceilings."""
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from eidolon_cli.organization_store import OrganizationStore


def test_budget_migration_does_not_issue_per_objective_work_for_existing_budgets(tmp_path):
    store = OrganizationStore(tmp_path / 'state.db')
    with store._write() as conn:
        def migration_statements():
            statements = []
            conn.set_trace_callback(statements.append)
            try:
                store._migrate_budgets(conn)
            finally:
                conn.set_trace_callback(None)
            return len(statements)

        empty = migration_statements()
        # Bulk fixture represents retained history, without expensive UI snapshots.
        conn.executemany('INSERT INTO objectives VALUES (?,?,?,?,?,?,?,?)', [
            (f'objective-{index}', f'key-{index}', 'hash', 'Retained work', 'Description', 3, 1000, 1)
            for index in range(200)])
        store._migrate_budgets(conn)
        assert conn.execute('SELECT count(*) FROM objective_budgets').fetchone()[0] == 200
        warm = migration_statements()
        # A set-based lookup may scan index entries, but cannot run a migration
        # and budget/usage lookups separately for every already-budgeted record.
        assert warm <= empty * 2


@pytest.mark.parametrize('legacy_usage_unknown', [0, 1])
def test_concurrent_startup_repairs_missing_budget_without_resetting_existing_usage(tmp_path, legacy_usage_unknown):
    store = OrganizationStore(tmp_path / 'state.db')
    preserved = store.create_objective('Preserve reservations', idempotency_key='preserved')
    claim = store.claim_next()
    store.reserve_model_call(claim, provider='fixture', model='offline', input_limit=100, output_limit=100)
    store.finish(claim, {'intervention': 'Retained unreported usage'})
    missing = store.create_objective('Repair legacy budget', idempotency_key='missing')
    legacy_claim = store.claim_next()
    assert legacy_claim['objective_id'] == missing['id']
    store.finish(legacy_claim, {'intervention': 'Legacy usage cannot be reconstructed'})
    with store._write() as conn:
        # Distinct ceilings and uncertainty must survive every concurrent opener.
        conn.execute('UPDATE objective_budgets SET max_model_calls=2,max_total_tokens=5000,'
                     "deadline=deadline-100,max_cost_usd='0.1',legacy_usage_unknown=? WHERE objective_id=?",
                     (legacy_usage_unknown, preserved['id']))
        before = tuple(conn.execute('SELECT * FROM objective_budgets WHERE objective_id=?', (preserved['id'],)).fetchone())
        reservations = [tuple(row) for row in conn.execute('SELECT * FROM organization_model_calls')]
        conn.execute('DELETE FROM objective_budgets WHERE objective_id=?', (missing['id'],))
    barrier = threading.Barrier(4)

    def reopen(_):
        barrier.wait(timeout=10)
        return OrganizationStore(store.path)

    with ThreadPoolExecutor(max_workers=4) as pool:
        reopened = list(pool.map(reopen, range(4)))
    for current in reopened:
        with current._connect() as conn:
            assert tuple(conn.execute('SELECT * FROM objective_budgets WHERE objective_id=?', (preserved['id'],)).fetchone()) == before
            assert [tuple(row) for row in conn.execute('SELECT * FROM organization_model_calls')] == reservations
            repair = conn.execute('SELECT * FROM objective_budgets WHERE objective_id=?', (missing['id'],)).fetchone()
            assert repair['legacy_usage_unknown'] == 1
            assert repair['deadline'] == conn.execute('SELECT created FROM objectives WHERE id=?', (missing['id'],)).fetchone()[0] + store.settings.objective_timeout_seconds
        with pytest.raises(ValueError, match='Prior model usage is unknown'):
            current.retry(legacy_claim['id'], idempotency_key='must-not-resume')
        assert current.claim_next() is None
