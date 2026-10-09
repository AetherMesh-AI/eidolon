"""Unanswerable linked requests retain bounded owner recovery without forged answers."""
from dataclasses import replace
import time

import pytest

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_store import OrganizationStore
from tests.organization_package_helpers import decompose


def pending_question(tmp_path, kind='request.question', **limits):
    store = OrganizationStore(tmp_path / 'state.db', replace(OrganizationSettings(), **limits))
    objective = store.create_objective('Prepare a brief', idempotency_key='objective')
    decompose(store)
    plan = store.claim_next()
    store.finish(plan, {'tasks': [{'title': 'Analyze', 'description': 'Use the supplied facts', 'type': 'work.analyze'}]})
    work = store.claim_next()
    proposal = {'managementProposal': {'members': [{'id': 'new-writer', 'name': 'New writer', 'capabilities': ['work.draft']}]}} if kind == 'request.hire' else {}
    store.finish(work, {'requests': [{'type': kind, 'requestedOutcome': 'An unavailable fact is needed.', **proposal}]})
    handler = store.claim_next()
    if handler:
        store.fail(handler, 'The owner must resolve the missing fact')
    request = next(row for row in store.snapshot()['requests'] if row['parentRequestId'] == work['id'])
    assert request['status'] == 'pending_intervention'
    return store, objective, work, request


@pytest.mark.parametrize('kind', ['request.question', 'request.decision', 'request.permission', 'request.hire'])
@pytest.mark.parametrize('action', ['amend_scope', 'request_replan'])
def test_linked_request_recovery_replans_once_and_fences_old_answers(tmp_path, kind, action):
    store, objective, work, request = pending_question(tmp_path, kind)
    actions = {item['action'] for item in request['allowedResolutions']}
    expected_response = {'approve_request', 'deny_request'} if kind in {'request.permission', 'request.hire'} else {'answer_request'}
    assert actions == expected_response | {'amend_scope', 'request_replan'}
    before = store.settings
    text = 'Produce a brief using only supplied facts; omit the unavailable fact.'
    assert store.resolve(request['id'], action, text, idempotency_key='recover')
    reopened = OrganizationStore(store.path)
    assert not reopened.resolve(request['id'], action, text, idempotency_key='recover')
    snapshot = reopened.snapshot()
    assert snapshot['objectives'][0]['id'] == objective['id']
    assert snapshot['objectives'][0]['acceptance']['round'] == 1
    assert len(snapshot['objectives'][0]['ownerResolutions']) == 1
    original = {row['id']: row for row in snapshot['requests']}
    assert original[work['id']]['status'] == original[request['id']]['status'] == 'cancelled'
    assert original[request['id']]['response'] is None
    assert original[request['id']]['requestedOutcome'] == request['requestedOutcome']
    assert reopened.settings == before
    with pytest.raises(ValueError, match='not awaiting'):
        reopened.respond(request['id'], 'Late answer', 'denied' if kind in {'request.permission', 'request.hire'} else 'answered', idempotency_key='late')
    assert not reopened.finish(work, {'summary': 'Late work', 'deliverable': 'Must not publish'})
    fresh = reopened.claim_next()
    assert fresh['type'] == 'request.decompose'
    context = reopened.context(fresh)
    assert context['objective']['round'] == 1
    assert context['feedback'] == text
    assert context['objective']['description'] == (text if action == 'amend_scope' else 'Prepare a brief')
    assert context['objective']['acceptanceCriteria'] == [text if action == 'amend_scope' else 'Prepare a brief']


@pytest.mark.parametrize('limit', ['replans', 'stages', 'deadline', 'cancelled'])
def test_linked_recovery_preserves_existing_limits_and_never_offers_replay(tmp_path, limit):
    store, _, _, request = pending_question(tmp_path, max_replans=0 if limit == 'replans' else 2)
    with store._write() as conn:
        if limit == 'stages':
            conn.execute('UPDATE objective_control SET max_stages=(SELECT count(*) FROM objective_usage)')
        elif limit == 'deadline':
            conn.execute('UPDATE objective_budgets SET deadline=?', (time.time() - 1,))
    if limit == 'cancelled':
        store.cancel(request['objectiveId'])
    reopened = OrganizationStore(store.path)
    current = next(row for row in reopened.snapshot()['requests'] if row['id'] == request['id'])
    assert {item['action'] for item in current['allowedResolutions']} == ({'answer_request'} if limit in {'replans', 'stages'} else set())
    with pytest.raises(ValueError, match='unavailable'):
        reopened.resolve(request['id'], 'request_replan', 'Do not bypass limits', idempotency_key='bypass')
    assert reopened.snapshot()['objectives'][0]['acceptance']['round'] == 0
    assert reopened.snapshot()['objectives'][0]['ownerResolutions'] == []


@pytest.mark.parametrize('winner', ['answer', 'replan'])
def test_answer_and_replan_serialize_and_share_owner_capacity(tmp_path, monkeypatch, winner):
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    from threading import Event

    store, _, work, request = pending_question(tmp_path, max_owner_resolutions=1)
    peer = OrganizationStore(store.path)
    locked, competing = Event(), Event()
    write = store._write

    @contextmanager
    def hold_first_transaction():
        with write() as conn:
            locked.set()
            assert competing.wait(5)
            yield conn

    monkeypatch.setattr(store, '_write', hold_first_transaction)

    def act(target, action):
        if action == 'answer':
            return target.respond(request['id'], 'Use supplied facts', idempotency_key='answer')
        return target.resolve(request['id'], 'request_replan', 'Use supplied facts', idempotency_key='replan')

    def compete():
        assert locked.wait(5)
        competing.set()
        with pytest.raises(ValueError, match='unavailable|not awaiting'):
            act(peer, 'replan' if winner == 'answer' else 'answer')

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(act, store, winner)
        second = pool.submit(compete)
        assert first.result(timeout=15)
        second.result(timeout=15)
    monkeypatch.setattr(store, '_write', write)
    reopened = OrganizationStore(store.path)
    assert not act(reopened, winner)
    snapshot = reopened.snapshot()
    rows = {row['id']: row for row in snapshot['requests']}
    assert snapshot['objectives'][0]['acceptance']['round'] == (winner == 'replan')
    assert rows[work['id']]['status'] == ('queued' if winner == 'answer' else 'cancelled')
    assert rows[request['id']]['status'] == ('completed' if winner == 'answer' else 'cancelled')
    assert (rows[request['id']]['response'] is not None) == (winner == 'answer')
    assert len(snapshot['objectives'][0]['ownerResolutions']) == (winner == 'replan')

    # Either kind of owner action consumes the same allowance for the next question.
    fresh = reopened.claim_next()
    assert fresh
    assert reopened.finish(fresh, {'requests': [{'type': 'request.question', 'requestedOutcome': 'A second missing fact is needed.'}]})
    handler = reopened.claim_next()
    if handler:
        reopened.fail(handler, 'The owner must resolve the second missing fact')
    question = next(row for row in reopened.snapshot()['requests'] if row['parentRequestId'] == fresh['id'] and row['id'] != request['id'])
    assert question['status'] == 'pending_intervention'
    assert question['allowedResolutions'] == []
    with pytest.raises(ValueError, match='capacity reached'):
        reopened.respond(question['id'], 'Second answer', idempotency_key='second-answer')
    with pytest.raises(ValueError, match='unavailable'):
        reopened.resolve(question['id'], 'request_replan', 'Second replan', idempotency_key='second-replan')
    assert reopened.snapshot()['objectives'][0]['acceptance']['round'] == (winner == 'replan')


def _hold_peer_recovery_lock(directory, request_id, ready, release):
    from pathlib import Path
    from eidolon_cli.organization_service import _ExecutionLock

    with _ExecutionLock(Path(directory), request_id) as acquired:
        ready.put(acquired)
        if acquired and not release.wait(30):
            raise TimeoutError('Parent did not release the execution lock')


@pytest.mark.parametrize('held_request', ['question', 'parent'])
def test_linked_recovery_waits_for_independent_runtime_lock(tmp_path, monkeypatch, held_request):
    import multiprocessing
    from eidolon_cli.organization_service import OrganizationService

    store, _, work, request = pending_question(tmp_path)
    service = OrganizationService(store, home=tmp_path)
    starts = []
    monkeypatch.setattr(service, 'start', lambda: starts.append(True))
    context = multiprocessing.get_context('spawn')
    ready, release = context.Queue(), context.Event()
    process = context.Process(target=_hold_peer_recovery_lock, args=(
        str(tmp_path / 'organization' / 'execution-locks'),
        request['id'] if held_request == 'question' else work['id'], ready, release,
    ))
    payload = dict(action='request_replan', text='Use supplied facts', idempotency_key='replan')
    process.start()
    try:
        assert ready.get(timeout=20)
        before = store.snapshot()
        with pytest.raises(ValueError, match='another runtime'):
            service.resolve(request['id'], **payload)
        assert starts == []
        assert not store.resolution_recorded(request['id'], **payload)
        after = store.snapshot()
        assert after['requests'] == before['requests']
        assert after['objectives'] == before['objectives']
    finally:
        release.set()
        process.join(timeout=10)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        ready.close()
    assert process.exitcode == 0
    assert service.resolve(request['id'], **payload)
    assert not service.resolve(request['id'], **payload)
    assert starts == [True]
    snapshot = OrganizationStore(store.path).snapshot()
    assert snapshot['objectives'][0]['acceptance']['round'] == 1
    rows = {row['id']: row for row in snapshot['requests']}
    assert rows[request['id']]['status'] == rows[work['id']]['status'] == 'cancelled'
