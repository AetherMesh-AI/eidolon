"""Real transactional claims, bounded routine aging and immutable opportunities."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import sqlite3
import time
import pytest
from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_store import OrganizationStore
from eidolon_cli.organization_scheduling import order_candidates, record_scheduling_claim


def store_at(path):
    return OrganizationStore(path, replace(OrganizationSettings(), max_inflight=1, max_open_objectives=10))


def add(store, key, priority):
    return store.create_objective(key, priority=priority, idempotency_key=key, delivery_mode='managed_artifact')


def state(store):
    with store._connect() as conn:
        return conn.execute('SELECT urgent_streak FROM organization_scheduler_state').fetchone()[0]


def test_old_cosmetic_never_ages_into_urgent_and_p2_never_into_p1(tmp_path):
    store = store_at(tmp_path/'state.db')
    old = add(store, 'old', 'P5')
    p2 = add(store, 'old p2', 'P2')
    p1 = add(store, 'new p1', 'P1')
    with store._write() as conn:
        conn.execute('UPDATE requests SET created=0 WHERE objective_id IN (?,?)', (old['id'], p2['id']))
    assert store.claim_next()['objective_id'] == p1['id']
    store.cancel(p1['id'])
    assert store.claim_next()['objective_id'] == p2['id']
    store.cancel(p2['id'])
    claim = store.claim_next()
    assert claim['objective_id'] == old['id'] and claim['priority'] == 1
    with store._connect() as conn:
        receipt = conn.execute('SELECT * FROM organization_scheduler_claims WHERE request_id=?', (claim['id'],)).fetchone()
        assert receipt['score'] == 3 and receipt['class'] == 'routine'


def test_three_urgent_then_routine_survives_reopen(tmp_path):
    path = tmp_path/'state.db'
    store = store_at(path)
    routine = add(store, 'routine', 'P5')
    urgent = [add(store, 'urgent'+str(i), 'P1') for i in range(4)]
    for i in range(3):
        claim = store.claim_next()
        assert claim['objective_id'] in [o['id'] for o in urgent]
        assert state(store) == i+1
        store.cancel(claim['objective_id'])
        store = store_at(path)
    assert store.claim_next()['objective_id'] == routine['id']
    assert state(store) == 0


def test_blocked_routine_retains_owed_turn_and_urgent_falls_through(tmp_path, monkeypatch):
    store = store_at(tmp_path/'state.db')
    routine = add(store, 'routine', 'P5')
    urgent = add(store, 'urgent', 'P1')
    with store._write() as conn:
        conn.execute('UPDATE organization_scheduler_state SET urgent_streak=3')
    check = store._dependencies_ready
    monkeypatch.setattr(store, '_dependencies_ready', lambda conn, row: row['objective_id'] != routine['id'] and check(conn, row))
    assert store.claim_next()['objective_id'] == urgent['id']
    assert state(store) == 3
    store.cancel(urgent['id'])
    monkeypatch.setattr(store, '_dependencies_ready', check)
    assert store.claim_next()['objective_id'] == routine['id']
    assert state(store) == 0


def test_claim_transaction_failure_rolls_back_cursor_receipt_and_usage(tmp_path, monkeypatch):
    store = store_at(tmp_path/'state.db')
    add(store, 'urgent', 'P1')
    import eidolon_cli.organization_store as module
    real = module.record_scheduling_claim
    def fail(conn, request, token, **kwargs):
        real(conn, request, token, **kwargs)
        raise RuntimeError('simulated commit failure')
    monkeypatch.setattr(module, 'record_scheduling_claim', fail)
    with pytest.raises(RuntimeError, match='commit failure'):
        store.claim_next()
    assert state(store) == 0
    with store._connect() as conn:
        assert not conn.execute('SELECT * FROM organization_scheduler_claims').fetchall()
        assert not conn.execute('SELECT * FROM objective_usage').fetchall()
        assert conn.execute('SELECT status FROM requests').fetchone()[0] == 'queued'


def test_concurrent_claimers_emit_one_receipt_and_live_claim_cannot_be_rewritten(tmp_path):
    path = tmp_path/'state.db'
    store = store_at(path)
    add(store, 'urgent', 'P1')
    stores = [store_at(path) for _ in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda s:s.claim_next(), stores))
    claim = next(c for c in claims if c)
    assert sum(c is not None for c in claims) == 1
    with store._write() as conn:
        record_scheduling_claim(conn, claim, claim['token'], now=time.time())
        with pytest.raises(ValueError, match='exact live claim'):
            record_scheduling_claim(conn, claim, 'other-token', now=time.time())
        assert conn.execute('SELECT count(*) FROM organization_scheduler_claims').fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            conn.execute('DELETE FROM organization_scheduler_claims')
    assert state(store) == 1


def test_strict_urgent_order_is_not_p2_starvation_freedom(tmp_path):
    store = store_at(tmp_path/'state.db')
    p2 = add(store, 'p2', 'P2')
    for i in range(4):
        p1 = add(store, str(i), 'P1')
        claim = store.claim_next()
        assert claim['objective_id'] == p1['id']
        store.cancel(p1['id'])
    assert state(store) == 3
    assert store.claim_next()['objective_id'] == p2['id']


def test_order_is_read_only_and_future_creation_has_no_negative_aging(tmp_path):
    store = store_at(tmp_path/'state.db')
    with store._connect() as conn:
        rows = [{'id':'a','priority':1,'created':200}, {'id':'b','priority':3,'created':100}]
        before = conn.total_changes
        assert [r['id'] for r in order_candidates(conn, rows, now=100)] == ['b','a']
        assert conn.total_changes == before


# Reuse the real installation fixture and exclusive SQLite lock from recovery178.
from tests.eidolon_cli.test_organization_shared_runtime import installation, configure, objective


@pytest.mark.parametrize('blocked', ['shared_lock', 'budget', 'policy'])
def test_real_admission_blockers_never_spend_fairness_credit(installation, blocked):
    from contextlib import closing
    root = installation
    configure(root, cost='0.3')
    objective(root.alpha)
    store = root.alpha.store
    with store._write() as conn:
        conn.execute('UPDATE organization_scheduler_state SET urgent_streak=3')
    if blocked == 'shared_lock':
        with closing(sqlite3.connect(root.ledger.path, isolation_level=None)) as blocker:
            blocker.execute('PRAGMA journal_mode=DELETE')
            blocker.execute('BEGIN EXCLUSIVE')
            assert store.claim_next() is None
            blocker.rollback()
    elif blocked == 'budget':
        with store._write() as conn:
            conn.execute('UPDATE objective_budgets SET deadline=0')
        assert store.claim_next() is None
    else:
        store.pause_configuration()
        assert store.claim_next() is None
    with store._connect() as conn:
        assert conn.execute('SELECT count(*) FROM organization_scheduler_claims').fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM objective_usage').fetchone()[0] == 0
    assert state(store) == 3
    if blocked == 'shared_lock':
        assert store.claim_next() is None
        root.now[0] += 1
        assert store.claim_next() is not None
        with store._connect() as conn:
            assert conn.execute('SELECT count(*) FROM organization_scheduler_claims').fetchone()[0] == 1
