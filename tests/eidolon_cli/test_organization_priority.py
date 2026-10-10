"""Owner priority revisions with real SQLite claims, immutable admission and audit."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from eidolon_cli.organization_store import OrganizationStore
from tests.eidolon_cli.test_organization_store import plan


def create(store, key, priority='P5'):
    return store.create_objective(key, priority=priority, idempotency_key=key, delivery_mode='managed_artifact')


def change(store, objective, priority, revision=0, key='change'):
    return store.change_objective_priority(objective['id'], priority, expected_revision=revision, idempotency_key=key)


def test_priority_orders_survive_restart_and_replay_without_rewriting_admission(tmp_path):
    store = OrganizationStore(tmp_path/'state.db')
    first = create(store, 'original')
    second = create(store, 'other', 'P2')
    with store._connect() as conn:
        original = dict(conn.execute('SELECT * FROM objectives WHERE id=?', (first['id'],)).fetchone())
        budgets = list(map(tuple, conn.execute('SELECT * FROM objective_budgets')))
    receipt = change(store, first, 'P1')
    assert receipt['previousPriority'] == 'P5' and receipt['revision'] == 1
    store = OrganizationStore(store.path)
    assert change(store, first, 'P1') == receipt
    assert create(store, 'original')['priority'] == 'P1'  # original P5 admission still replays
    with pytest.raises(ValueError, match='different objective'):
        create(store, 'original', 'P1')
    with pytest.raises(ValueError, match='different change'):
        change(store, first, 'P2')
    with pytest.raises(ValueError, match='refresh'):
        change(store, first, 'P2', key='stale')
    assert store.claim_next()['objective_id'] == first['id']
    assert store.cancel(first['id'])
    with pytest.raises(ValueError, match='nonterminal'):
        change(store, first, 'P2', revision=1, key='cancelled')
    assert change(store, first, 'P1') == receipt  # retry never replays the mutation
    with store._connect() as conn:
        row = dict(conn.execute('SELECT * FROM objectives WHERE id=?', (first['id'],)).fetchone())
        assert {k:v for k,v in row.items() if k not in ('priority','cancelled')} == {k:v for k,v in original.items() if k not in ('priority','cancelled')}
        assert list(map(tuple, conn.execute('SELECT * FROM objective_budgets'))) == budgets
        assert conn.execute('SELECT count(*) FROM priority_changes').fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            conn.execute("UPDATE priority_changes SET priority=1")
    assert store.claim_next()['objective_id'] == second['id']
    store.set_objective_archived(first['id'], True, expected_revision=0, idempotency_key='archive')
    with pytest.raises(ValueError, match='nonterminal'):
        change(store, first, 'P2', revision=1, key='archived')
    from tests.eidolon_cli.test_organization_acceptance import _plan, _work_and_review, _integrate, _decision
    completed = OrganizationStore(tmp_path/'completed.db')
    done = create(completed, 'completed')
    _plan(completed)
    _work_and_review(completed)
    accept = _integrate(completed)
    completed.finish(accept, _decision(completed, accept))
    with pytest.raises(ValueError, match='nonterminal'):
        change(completed, done, 'P1')


def test_lowering_and_concurrent_versions_preserve_active_claim_and_future_work(tmp_path):
    store = OrganizationStore(tmp_path/'state.db')
    first = create(store, 'running')
    plan(store)  # claims and completes executive/manager stages, leaving queued tasks
    claim = store.claim_next()
    before = dict(claim)
    change(store, first, 'P1')
    with store._connect() as conn:
        running = dict(conn.execute('SELECT * FROM requests WHERE id=?', (claim['id'],)).fetchone())
        assert running['token'] == before['token'] and running['priority'] == before['priority']
        assert running['lease'] == before['lease'] and running['status'] == 'running'
        assert all(row[0] == 5 for row in conn.execute('SELECT priority FROM tasks WHERE objective_id=?', (first['id'],)))
    def attempt(value):
        try:return change(OrganizationStore(store.path), first, value, revision=1, key=value)
        except ValueError as error:return str(error)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ['P4','P5']))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert any('refresh' in result for result in results if isinstance(result,str))
    winner = next(result for result in results if isinstance(result,dict))
    assert store.heartbeat(claim)
    assert store.finish(claim, {'summary':'Retained result','deliverable':'Existing worker continues.'})
    with store._connect() as conn:
        review = conn.execute("SELECT * FROM requests WHERE task_id=? AND type='request.review'", (claim['task_id'],)).fetchone()
        assert review['priority'] == 6-int(winner['priority'][1])
        assert conn.execute('SELECT count(*) FROM evidence WHERE request_id=?', (claim['id'],)).fetchone()[0] == 1
    other = create(store, 'new higher objective', 'P2')
    # Explicitly compare ready root planning work at the same dependency level.
    queued = create(store, 'lowered root', 'P1')
    change(store, queued, 'P5', key='lower-root')
    assert store.claim_next()['objective_id'] == other['id']
    from tests.organization_package_helpers import decompose
    planner = OrganizationStore(tmp_path/'planner.db')
    planned = create(planner, 'old planner')
    decompose(planner)
    old = planner.claim_next()
    change(planner, planned, 'P1', key='during-plan')
    assert planner.finish(old, {'tasks':[{'title':'New child','description':'Scoped draft','type':'work.draft'}]})
    with planner._connect() as conn:
        assert conn.execute('SELECT priority FROM tasks').fetchone()[0] == 5
        assert conn.execute("SELECT priority FROM requests WHERE status='queued'").fetchone()[0] == 5
        assert conn.execute('SELECT priority FROM requests WHERE id=?', (old['id'],)).fetchone()[0] == old['priority']
    retry = planner.claim_next()
    change(planner, planned, 'P5', revision=1, key='during-worker')
    planner.fail(retry, 'Scripted retryable failure', retryable=True)
    with planner._connect() as conn:
        row = conn.execute('SELECT * FROM requests WHERE id=?', (retry['id'],)).fetchone()
        assert row['status'] == 'queued' and row['priority'] == 1 and row['available'] > 0
