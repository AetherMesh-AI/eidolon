"""Durable dispatch boundaries survive process death without duplicate effects."""
from concurrent.futures import ThreadPoolExecutor
import multiprocessing
import time

import pytest

from eidolon_cli.organization_store import OrganizationStore


def expire(store, claim):
    with store._write() as conn:
        conn.execute('UPDATE requests SET lease=0,available=0 WHERE id=?', (claim['id'],))


def status(store, claim):
    with store._connect() as conn:
        return dict(conn.execute('SELECT * FROM requests WHERE id=?', (claim['id'],)).fetchone())


def prepare(path):
    store = OrganizationStore(path)
    store.create_objective('Write a brief', 'Only supplied context.', idempotency_key='one')
    return store


def crash_worker(path, dispatched, ready):
    store = OrganizationStore(path)
    claim = store.claim_next(recoverable=True)
    if dispatched:
        assert store.begin_dispatch(claim)
    ready.send(claim)
    time.sleep(60)


@pytest.mark.parametrize('dispatched', [False, True])
def test_killed_process_recovers_only_positive_not_dispatched_record(tmp_path, dispatched):
    store = prepare(tmp_path / 'state.db')
    context = multiprocessing.get_context('spawn')
    parent, child = context.Pipe(False)
    process = context.Process(target=crash_worker, args=(str(store.path), dispatched, child))
    process.start()
    try:
        assert parent.poll(20)
        claim = parent.recv()
    finally:
        process.terminate()
        process.join(10)
    expire(store, claim)
    reopened = OrganizationStore(store.path)
    assert reopened.recover_expired() == 1
    assert status(reopened, claim)['status'] == ('pending_intervention' if dispatched else 'queued')
    assert not reopened.begin_dispatch(claim)
    assert not reopened.finish(claim, {'tasks': []})
    assert reopened.recover_expired() == 0


@pytest.mark.parametrize('change', ['legacy', 'dispatch', 'payload', 'limit', 'cancel'])
def test_unsafe_or_terminal_attempt_is_not_replayed(tmp_path, change):
    store = prepare(tmp_path / 'state.db')
    claim = store.claim_next(recoverable=change != 'legacy')
    if change == 'dispatch':
        assert store.begin_dispatch(claim)
        assert not store.begin_dispatch(claim)
    if change == 'cancel':
        store.cancel(claim['objective_id'])
    with store._write() as conn:
        if change == 'payload':
            conn.execute("UPDATE requests SET payload='{} ' WHERE id=?", (claim['id'],))
        if change == 'limit':
            conn.execute('UPDATE requests SET attempts=? WHERE id=?', (store.settings.max_attempts, claim['id']))
    expire(store, claim)
    store.recover_expired()
    assert status(store, claim)['status'] == ('cancelled' if change == 'cancel' else 'pending_intervention')


def test_concurrent_recovery_preserves_identity_usage_and_retry_bound(tmp_path):
    store = prepare(tmp_path / 'state.db')
    claim = store.claim_next(recoverable=True)
    expire(store, claim)
    peers = [OrganizationStore(store.path) for _ in range(4)]
    with ThreadPoolExecutor(4) as pool:
        assert sum(pool.map(lambda peer: peer.recover_expired(), peers)) == 1
    row = status(store, claim)
    assert row['attempts'] == claim['attempts']
    with store._write() as conn:
        assert conn.execute('SELECT count(*) FROM objective_usage WHERE request_id=?', (claim['id'],)).fetchone()[0] == 1
        conn.execute('UPDATE requests SET available=0 WHERE id=?', (claim['id'],))
    retry = store.claim_next(recoverable=True)
    assert retry['id'] == claim['id'] and retry['agent_id'] == claim['agent_id']
    assert retry['token'] != claim['token'] and retry['attempts'] == claim['attempts'] + 1
    expire(store, retry)
    store.recover_expired()
    assert status(store, retry)['status'] == 'pending_intervention'


def test_model_reservation_is_retained_even_if_dispatch_marker_missing(tmp_path):
    store = prepare(tmp_path / 'state.db')
    claim = store.claim_next(recoverable=True)
    store.reserve_model_call(claim, provider='test', model='bounded', input_limit=100, output_limit=100)
    expire(store, claim)
    store.recover_expired()
    assert status(store, claim)['status'] == 'pending_intervention'
    with store._connect() as conn:
        calls = conn.execute('SELECT * FROM organization_model_calls WHERE request_id=?', (claim['id'],)).fetchall()
        assert len(calls) == 1 and calls[0]['input_limit'] + calls[0]['output_limit'] == 200


def test_policy_change_cannot_recover_old_authority(tmp_path):
    from dataclasses import replace
    store = prepare(tmp_path / 'state.db')
    claim = store.claim_next(recoverable=True)
    peer = OrganizationStore(store.path, replace(store.settings, max_total_tokens=store.settings.max_total_tokens + 100))
    expire(peer, claim)
    peer.recover_expired()
    assert status(peer, claim)['status'] != 'queued'
    assert not store.begin_dispatch(claim)


def test_real_service_marks_dispatch_before_executor_and_commits_once(tmp_path):
    import threading
    from eidolon_cli.organization_service import OrganizationService
    store = prepare(tmp_path / 'state.db')
    entered = threading.Event()
    observed = []
    def execute(claim, context, cancel):
        with store._connect() as conn:
            row = conn.execute('SELECT dispatched FROM organization_execution_journal WHERE request_id=? AND token=?',
                               (claim['id'], claim['token'])).fetchone()
        observed.append(row['dispatched'])
        entered.set()
        return {'intervention': 'End deterministic test'}
    service = OrganizationService(store, home=tmp_path, executor=execute, poll_seconds=0.01)
    try:
        service.start()
        assert entered.wait(20)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if store.snapshot()['requests'][0]['status'] == 'pending_intervention':
                break
            time.sleep(0.01)
        assert store.snapshot()['requests'][0]['status'] == 'pending_intervention'
        assert observed == [1]
        assert store.recover_expired() == 0
    finally:
        assert service.stop()


@pytest.mark.linux_only
def test_durable_source_success_reconciles_without_republishing(tmp_path, monkeypatch):
    import threading
    from tests.eidolon_cli.test_organization_project_security import _source_ready
    from eidolon_cli import organization_source_integration as integration
    store, _, objective, claim = _source_ready(tmp_path, monkeypatch)
    # The helper uses the legacy direct-store API. Explicitly bind this attempt
    # as the real scheduler does atomically at claim time.
    with store._write() as conn:
        store._journal_claim(conn, claim['id'], claim['token'])
    assert store.begin_dispatch(claim)
    assert store.run_project_stage(claim, threading.Event()) == {}
    expire(store, claim)
    reopened = OrganizationStore(store.path)
    assert reopened.recover_expired() == 1
    assert status(reopened, claim)['status'] == 'queued'
    with reopened._write() as conn:
        conn.execute('UPDATE requests SET available=0 WHERE id=?', (claim['id'],))
    retry = reopened.claim_next(recoverable=True)
    assert retry['id'] == claim['id']
    monkeypatch.setattr(integration, 'integrate_source', lambda *args, **kwargs:
                        pytest.fail('Durable source success must only verify the receipt'))
    assert reopened.begin_dispatch(retry)
    assert reopened.run_project_stage(retry, threading.Event()) == {}
    assert reopened.finish(retry, {})
    assert reopened.recover_expired() == 0
    with reopened._connect() as conn:
        assert conn.execute('SELECT count(*) FROM project_source_receipts').fetchone()[0] == 1


@pytest.mark.linux_only
@pytest.mark.parametrize('damage', ['missing', 'mismatch', 'cancelled', 'source', 'grant'])
def test_reconcile_only_never_falls_back_to_publication(tmp_path, monkeypatch, damage):
    import threading
    from tests.eidolon_cli.test_organization_project_security import _source_ready
    from eidolon_cli import organization_source_integration as integration
    store, source, _, claim = _source_ready(tmp_path, monkeypatch)
    with store._write() as conn:
        store._journal_claim(conn, claim['id'], claim['token'])
    assert store.begin_dispatch(claim)
    assert store.run_project_stage(claim, threading.Event()) == {}
    expire(store, claim)
    assert store.recover_expired() == 1
    with store._write() as conn:
        conn.execute('UPDATE requests SET available=0 WHERE id=?', (claim['id'],))
    retry = store.claim_next(recoverable=True)
    assert retry is not None
    if damage == 'cancelled':
        store.cancel(retry['objective_id'])
    elif damage == 'source':
        source.write_text('Changed after recovery', encoding='utf-8')
    elif damage == 'grant':
        from dataclasses import replace
        store = OrganizationStore(store.path, replace(store.settings,
            tool_grants=tuple(grant for grant in store.settings.tool_grants if grant != 'integrate_source')))
    else:
        # Simulate storage corruption outside the supported immutable receipt API.
        with store._write() as conn:
            if damage == 'missing':
                conn.execute('DROP TRIGGER immutable_project_source_receipts_delete')
                conn.execute('DELETE FROM project_source_receipts WHERE request_id=?', (retry['id'],))
            else:
                conn.execute('UPDATE organization_reconcile_only SET receipt_sha256=? WHERE request_id=?', ('bad', retry['id']))
    monkeypatch.setattr(integration, 'integrate_source', lambda *args, **kwargs:
                        pytest.fail('Reconciliation cannot fall through to publication'))
    with pytest.raises(ValueError):
        store.run_project_stage(retry, threading.Event())


def test_legacy_dispatch_gets_no_undispatched_recovery_privilege(tmp_path):
    store = prepare(tmp_path / 'state.db')
    claim = store.claim_next()
    assert store.begin_dispatch(claim)
    assert not store.begin_dispatch(claim)
    expire(store, claim)
    assert store.recover_expired() == 1
    assert status(store, claim)['status'] == 'pending_intervention'


def test_recovery_does_not_reset_stage_budget(tmp_path):
    store = prepare(tmp_path / 'state.db')
    claim = store.claim_next(recoverable=True)
    with store._write() as conn:
        conn.execute('UPDATE objective_control SET max_stages=1 WHERE objective_id=?', (claim['objective_id'],))
    expire(store, claim)
    assert store.recover_expired() == 1
    with store._write() as conn:
        conn.execute('UPDATE requests SET available=0 WHERE id=?', (claim['id'],))
    assert store.claim_next(recoverable=True) is None
    assert status(store, claim)['status'] == 'pending_intervention'
    assert 'stage budget' in status(store, claim)['reason']
