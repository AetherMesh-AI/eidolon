"""Organization outcome revisions track offline writes and late execution receipts."""
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from eidolon_cli.organization_store import OrganizationStore
from tests.organization_outcomes_helpers import (
    accepted,
    cancelled,
    item,
)
from tests.organization_outcomes_helpers import (
    store as store,
)


def test_offline_reopen_same_terminal_state_invalidates_old_ack(store):
    objective, _, _ = accepted(store)
    old = item(store)
    store.mark_outcome_seen(objective['id'], old['revision'])
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE objective_control SET status='blocked' WHERE objective_id=?", (objective['id'],))
    assert store.outcomes()['items'] == []
    with pytest.raises(ValueError, match='refresh'):
        store.mark_outcome_seen(objective['id'], old['revision'])
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE objective_control SET status='accepted' WHERE objective_id=?", (objective['id'],))
    current = item(OrganizationStore(store.path))
    assert current['revision'] > old['revision'] and not current['seen']
    assert current['createdAt'] == old['createdAt']
    with store._connect() as conn:
        assert conn.execute('SELECT count(*) FROM owner_outcomes').fetchone()[0] == 1
    with pytest.raises(ValueError, match='refresh'):
        store.mark_outcome_seen(objective['id'], old['revision'])


def test_material_changes_advance_generation_but_noop_and_bookkeeping_do_not(store):
    objective, claim, ids = accepted(store)
    old = item(store)
    store.mark_outcome_seen(objective['id'], old['revision'])
    seen = store.outcomes()
    with sqlite3.connect(store.path) as conn:
        conn.execute('UPDATE objective_control SET status=status,summary=summary WHERE objective_id=?', (objective['id'],))
        conn.execute('UPDATE requests SET lease=123,available=4 WHERE id=?', (claim['id'],))
    assert store.outcomes() == seen
    for statement, parameters in [
        ('UPDATE objective_control SET summary=? WHERE objective_id=?', ('Updated decision summary', objective['id'])),
        ('UPDATE objectives SET title=? WHERE id=?', ('Updated title', objective['id'])),
        ('UPDATE objective_deliverables SET content=? WHERE id=?', ('Changed persisted bytes', old['deliverableId'])),
        ('UPDATE evidence SET summary=? WHERE id=?', ('Changed source evidence', ids[0])),
        ('UPDATE objective_acceptances SET summary=? WHERE request_id=?', ('Updated review rationale', claim['id'])),
    ]:
        before = item(store)
        store.mark_outcome_seen(objective['id'], before['revision'])
        with sqlite3.connect(store.path) as conn:
            conn.execute(statement, parameters)
        after = item(store)
        assert after['revision'] > before['revision'] and not after['seen']
        with pytest.raises(ValueError, match='refresh'):
            store.mark_outcome_seen(objective['id'], before['revision'])
    # Actual cancellation overrides a previously accepted control, never inferred
    # from a failed request or from a control string that merely says cancelled.
    with sqlite3.connect(store.path) as conn:
        conn.execute('UPDATE objectives SET cancelled=1 WHERE id=?', (objective['id'],))
    assert item(store)['status'] == 'cancelled'


def test_concurrent_old_seen_cannot_hide_new_result_even_with_identical_clock(store):
    objective = cancelled(store)
    old = item(store)
    barrier = threading.Barrier(2)
    def acknowledge():
        barrier.wait()
        try:
            store.mark_outcome_seen(objective['id'], old['revision'])
        except ValueError as exc:
            assert 'refresh' in str(exc)
    def change():
        barrier.wait()
        with store._write() as conn:
            conn.execute('UPDATE objectives SET title=? WHERE id=?', ('New title', objective['id']))
            conn.execute('UPDATE owner_outcomes SET updated=created WHERE objective_id=?', (objective['id'],))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(acknowledge), pool.submit(change)]
        for future in futures:
            future.result()
    now = item(OrganizationStore(store.path))
    assert now['revision'] > old['revision'] and not now['seen']
    assert now['updatedAt'] == old['createdAt']


def test_late_project_and_tool_receipts_make_cancelled_outcome_unread(store):
    """Cancellation can be observed before an in-flight execution writes back."""
    from eidolon_cli.organization_history import settlement_blocker
    objective = store.create_objective('Interrupted project run', idempotency_key='late')
    claim = store.claim_next()
    with store._write() as conn:
        conn.execute('INSERT INTO project_run_starts VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                     ('run-late', claim['id'], claim['token'], objective['id'], 0,
                      '[]', 'snapshot-hash', '{}', None, 1, 1))
        conn.execute('INSERT INTO tool_receipts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                     ('tool-late', claim['id'], 1, 'call', 'read_file', '{}', 'running', None, None, 1, None, None))
    store.cancel(objective['id'])
    observed = item(store)
    assert observed['status'] == 'cancelled'
    store.mark_outcome_seen(objective['id'], observed['revision'])
    with store._connect() as conn:
        assert 'unconfirmed' in settlement_blocker(conn, objective['id']).lower()
    with sqlite3.connect(store.path) as conn:
        conn.execute('INSERT INTO project_run_results VALUES (?,?,?,?)',
                     ('run-late', '{"status":"cancelled"}', 'result-hash', 2))
    result = item(store)
    assert result['revision'] > observed['revision'] and not result['seen']
    assert result['status'] == 'cancelled' and result['acceptanceRequestId'] is None
    with pytest.raises(ValueError, match='refresh'):
        store.mark_outcome_seen(objective['id'], observed['revision'])
    store.mark_outcome_seen(objective['id'], result['revision'])
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE tool_receipts SET status='failed',result='Interrupted read',reason='Cancelled' WHERE id='tool-late'")
    finished = item(store)
    assert finished['revision'] > result['revision'] and not finished['seen']
    with store._connect() as conn:
        assert settlement_blocker(conn, objective['id']) is None
    assert store.claim_next() is None
