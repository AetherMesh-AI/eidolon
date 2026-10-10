"""Owner dispatch gates against actual SQLite claim, completion and restart paths."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import sqlite3

import pytest
from eidolon_cli.organization_store import OrganizationStore
from tests.organization_package_helpers import decompose


def create(store, key):
    return store.create_objective(key, idempotency_key=key, delivery_mode='managed_artifact')


def toggle(store, obj, paused=True, revision=0, key='pause'):
    return store.set_objective_paused(obj['id'], paused, expected_revision=revision, idempotency_key=key)


def test_pause_isolates_dispatch_and_restart_preserves_admission_and_allowances(tmp_path):
    store = OrganizationStore(tmp_path/'state.db')
    one, two = create(store, 'one'), create(store, 'two')
    with store._connect() as conn:
        before = {table:list(map(tuple, conn.execute(f'SELECT * FROM {table}'))) for table in ('objectives','objective_budgets','requests','agents')}
    receipt = toggle(store, one)
    restarted = OrganizationStore(store.path)
    assert toggle(restarted, one) == receipt
    assert create(restarted, 'one')['id'] == one['id']
    with restarted._connect() as conn:
        assert before == {table:list(map(tuple, conn.execute(f'SELECT * FROM {table}'))) for table in before}
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            conn.execute('DELETE FROM dispatch_changes')
    claim = restarted.claim_next()
    assert claim['objective_id'] == two['id']
    restarted.cancel(two['id'])
    assert restarted.claim_next() is None
    assert toggle(restarted, one, False, 1, 'resume')['revision'] == 2
    assert toggle(restarted, one) == receipt  # historical retry cannot pause again
    assert restarted._objective_view(one['id'])['dispatchControl']['paused'] is False
    assert restarted.claim_next()['objective_id'] == one['id']
    with pytest.raises(ValueError, match='different change'):
        toggle(restarted, two)
    with pytest.raises(ValueError, match='refresh'):
        toggle(restarted, one, True, 0, 'stale')


def test_claimed_stage_finishes_children_and_retries_wait_behind_gate(tmp_path):
    store = OrganizationStore(tmp_path/'state.db')
    obj = create(store, 'one')
    decompose(store)
    planner = store.claim_next()
    toggle(store, obj)
    other = create(store, 'unrelated')
    assert store.claim_next()['objective_id'] == other['id']
    store.cancel(other['id'])
    assert store.heartbeat(planner)
    assert store._objective_view(obj['id'])['dispatchControl']['runningCount'] == 1
    assert store.finish(planner, {'tasks':[{'title':'Draft','description':'Scoped brief','type':'work.draft'}]})
    assert store.claim_next() is None
    toggle(store, obj, False, 1, 'resume')
    worker = store.claim_next()
    toggle(store, obj, True, 2, 'pause-again')
    assert store.fail(worker, 'Temporary failure', retryable=True)
    assert store.claim_next() is None
    with store._connect() as conn:
        retry = conn.execute('SELECT * FROM requests WHERE id=?', (worker['id'],)).fetchone()
        assert retry['status'] == 'queued'
        assert conn.execute('SELECT count(*) FROM tasks').fetchone()[0] == 1
    # The same write lock resolves an admission racing the pause.
    other = OrganizationStore(tmp_path/'race.db')
    race = create(other, 'race')
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(lambda: OrganizationStore(other.path).claim_next()), pool.submit(lambda: toggle(OrganizationStore(other.path), race))]
        claimed, _ = [f.result() for f in futures]
    assert other.claim_next() is None
    if claimed is not None:
        assert other.heartbeat(claimed)
    assert other._objective_view(race['id'])['dispatchControl']['paused']


def test_resume_denial_preserves_gate_deadline_and_receipts(tmp_path, monkeypatch):
    store = OrganizationStore(tmp_path/'state.db')
    obj = create(store, 'one')
    from tests.eidolon_cli.test_organization_store import plan
    plan(store, tasks=[{'title':'Draft','description':'Scoped brief','type':'work.draft'}])
    toggle(store, obj)
    with store._connect() as conn:
        budget = dict(conn.execute('SELECT * FROM objective_budgets').fetchone())
    import eidolon_cli.organization_budget as budgets
    with monkeypatch.context() as clock:
        clock.setattr(budgets.time, 'time', lambda: budget['deadline'] + 1)
        with pytest.raises(ValueError, match='deadline'):
            toggle(store, obj, False, 1, 'resume')
    store.pause_configuration()
    with pytest.raises(ValueError, match='grants or routing'):
        toggle(store, obj, False, 1, 'resume')
    assert store._objective_view(obj['id'])['dispatchControl']['paused']
    # A current but revoked routing policy also cannot authorize ready work.
    current = OrganizationStore(store.path, settings=replace(store.settings, capabilities=()))
    with pytest.raises(ValueError, match='authority'):
        toggle(current, obj, False, 1, 'resume')
    with current._connect() as conn:
        assert dict(conn.execute('SELECT * FROM objective_budgets').fetchone()) == budget
        assert conn.execute('SELECT count(*) FROM dispatch_changes').fetchone()[0] == 1
    current.cancel(obj['id'])
    current.set_objective_archived(obj['id'], True, expected_revision=0, idempotency_key='archive')
    assert toggle(current, obj)['paused']  # exact receipt survives terminal/archive
    with pytest.raises(ValueError, match='nonterminal'):
        toggle(current, obj, False, 1, 'new')


def test_concurrent_owner_saves_and_inflight_completion_cannot_reopen_terminal_work(tmp_path):
    from tests.eidolon_cli.test_organization_acceptance import _plan, _work_and_review, _integrate, _decision
    store = OrganizationStore(tmp_path/'state.db')
    obj = create(store, 'one')
    def pause(key):
        try:return toggle(OrganizationStore(store.path), obj, key=key)
        except ValueError as exc:return str(exc)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(pause, ['a','b']))
    assert sum(isinstance(item, dict) for item in results) == 1
    assert any('refresh' in item for item in results if isinstance(item,str))
    toggle(store, obj, False, 1, 'resume')
    _plan(store); _work_and_review(store); accept = _integrate(store)
    toggle(store, obj, True, 2, 'during-acceptance')
    assert store.finish(accept, _decision(store, accept))
    assert store._objective_view(obj['id'])['status'] == 'completed'
    with pytest.raises(ValueError, match='nonterminal'):
        toggle(store, obj, False, 3, 'terminal-resume')
    assert store.claim_next() is None


def test_transition_bound_finishes_open_and_old_receipts_never_reapply(tmp_path):
    store = OrganizationStore(tmp_path/'state.db')
    obj = create(store, 'one')
    for revision in range(100):
        toggle(store, obj, revision % 2 == 0, revision, f'change-{revision}')
    assert store._objective_view(obj['id'])['dispatchControl'] == {'paused':False, 'revision':100, 'runningCount':0}
    with pytest.raises(ValueError, match='0 to 99'):
        toggle(store, obj, True, 100, 'over-limit')
    assert toggle(store, obj, True, 0, 'change-0')['paused'] is True
    assert store._objective_view(obj['id'])['dispatchControl']['paused'] is False
    with pytest.raises(ValueError, match='boolean'):
        toggle(store, obj, 1, 0, 'wrong-type')


def test_resume_preserves_pending_staff_activation_before_worker_dispatch(tmp_path):
    from eidolon_cli.organization_config import OrganizationSettings
    from tests.eidolon_cli.test_organization_store import plan
    settings = OrganizationSettings.from_config({'organization': {'roster': [
        {'id': 'specialist', 'name': 'Specialist', 'team': 'general', 'capabilities': ['work.draft']},
    ]}})
    store = OrganizationStore(tmp_path/'state.db', settings)
    obj = create(store, 'one')
    plan(store, tasks=[{'title':'Draft','description':'Scoped brief','type':'work.draft'}])
    toggle(store, obj)
    with store._connect() as conn:
        before = list(map(tuple, conn.execute('SELECT * FROM agents')))
        assert conn.execute("SELECT count(*) FROM requests WHERE type='request.hire' AND status='queued'").fetchone()[0] == 1
    toggle(store, obj, False, 1, 'resume')
    with store._connect() as conn:
        assert list(map(tuple, conn.execute('SELECT * FROM agents'))) == before
    hire = store.claim_next()
    assert hire['type'] == 'request.hire'
    assert store.finish(hire, {})
    work = store.claim_next()
    assert work['type'] == 'work.draft' and work['agent_id'] == 'specialist'
