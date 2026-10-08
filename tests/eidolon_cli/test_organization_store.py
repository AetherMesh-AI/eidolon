"""State-machine contracts with real, independent SQLite connections."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import sqlite3
import time

import pytest

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_store import OrganizationStore
from tests.organization_package_helpers import decompose


@pytest.fixture
def store(tmp_path):
    return OrganizationStore(tmp_path / 'organization' / 'state.db')


def objective(store, key='objective', **kwargs):
    return store.create_objective('Prepare a decision brief', 'Use the supplied context only.', idempotency_key=key, **kwargs)


def plan(store, tasks=None, **extra):
    decompose(store)
    claim = store.claim_next()
    assert claim['type'] == 'request.plan'
    store.finish(claim, {'tasks': tasks or [
        {'title': 'Draft the brief', 'description': 'Produce a useful brief.', 'type': 'work.draft', 'team': 'general', 'dependsOn': []},
        {'title': 'Assess the brief', 'description': 'Explain tradeoffs.', 'type': 'work.analyze', 'team': 'general', 'dependsOn': [0]},
    ], **extra})
    return claim


def work(store, text='A substantive deliverable grounded in the submitted context.'):
    claim = store.claim_next()
    assert claim['type'].startswith('work.')
    store.finish(claim, {'summary': 'Prepared the deliverable', 'deliverable': text})
    return claim


def review(store, approved=True):
    claim = store.claim_next()
    assert claim['type'] == 'request.review'
    evidence = store.context(claim)['evidence']
    store.finish(claim, {'approved': approved, 'summary': 'Meets the supplied acceptance criteria' if approved else 'Add a concrete comparison.',
                         'evidenceIds': [e['id'] for e in evidence]})
    return claim


def accept_objective(store):
    integrate = store.claim_next()
    assert integrate['type'] == 'request.integrate'
    store.finish(integrate, {'summary': 'Integrated decision brief', 'deliverable': 'Full integrated decision brief and analysis.'})
    accept = store.claim_next()
    assert accept['type'] == 'request.accept' and accept['agent_id'] != integrate['agent_id']
    context = store.context(accept)
    ids = [item['id'] for item in context['evidence']]
    store.finish(accept, {'approved': True, 'summary': 'Entire objective is satisfied by the integrated brief.',
                          'evidenceIds': ids, 'conflicts': [], 'criteriaResults': [
                              {'criterion': item, 'satisfied': True, 'evidenceIds': ids, 'reason': 'Exact integrated text covers the criterion.'}
                              for item in context['objective']['acceptanceCriteria']]})


def test_real_ledger_runs_dag_review_and_restart_with_no_unreviewed_completion(store):
    obj = objective(store)
    plan(store)
    first = work(store)
    # A dependent task cannot claim before independent review, even though its input exists.
    request = store.claim_next()
    assert request['type'] == 'request.review'
    assert request['agent_id'] != first['agent_id']
    assert store.snapshot()['objectives'][0]['status'] != 'completed'
    evidence = store.context(request)['evidence']
    assert evidence[0]['sha256'] == hashlib.sha256(evidence[0]['content'].encode()).hexdigest()
    assert store.evidence(evidence[0]['id'])['content'] == evidence[0]['content']
    with pytest.raises(ValueError, match='exactly'):
        store.finish(request, {'approved': True, 'summary': 'Fine', 'evidenceIds': ['invented']})
    store.finish(request, {'approved': True, 'summary': 'Reviewed supplied evidence.', 'evidenceIds': [evidence[0]['id']]})
    restarted = OrganizationStore(store.path)
    second = restarted.claim_next()
    assert second['type'] == 'work.analyze'
    assert restarted.context(second)['dependencies'][0]['deliverable'] == evidence[0]['content']
    restarted.finish(second, {'summary': 'Assessment', 'deliverable': 'A clear comparison of the supplied tradeoffs.'})
    review(restarted)
    assert restarted.snapshot()["objectives"][0]["status"] != "completed"
    accept_objective(restarted)
    snapshot = restarted.snapshot()
    assert snapshot['objectives'][0]['id'] == obj['id']
    assert snapshot['objectives'][0]['status'] == 'completed'
    assert all(task['review'] == 'approved' for task in snapshot['tasks'])
    assert len(snapshot['knowledge']) == 2
    assert restarted.claim_next() is None
    # Retrying an already accepted result cannot create extra tasks/evidence.
    assert restarted.finish(first, {'summary': 'duplicate', 'deliverable': 'duplicate'}) is False
    assert restarted.snapshot()['knowledge'] == snapshot['knowledge']


def test_idempotent_submission_and_claim_are_atomic_across_connections(store):
    def submit(_):
        return objective(OrganizationStore(store.path))['id']
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(submit, range(8)))
    assert len(set(ids)) == 1
    with pytest.raises(ValueError, match='different objective'):
        store.create_objective('Different', idempotency_key='objective')
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda _: OrganizationStore(store.path).claim_next(), range(8)))
    assert len([c for c in claims if c is not None]) == 1
    claim = next(c for c in claims if c)
    assert store.heartbeat(claim)
    with store._write() as conn:
        conn.execute('UPDATE requests SET lease=? WHERE id=?', (time.time()-1, claim['id']))
    assert store.recover_expired() == 1
    assert store.recover_expired() == 0
    assert not store.heartbeat(claim)
    assert not store.finish(claim, {'tasks': []})
    assert store.snapshot()['requests'][0]['status'] == 'pending_intervention'
    assert store.claim_next() is None  # uncertain interrupted call is not auto-replayed
    assert store.retry(claim['id'])
    retry = store.claim_next()
    assert retry['token'] != claim['token']
    assert retry['attempts'] == 2
    assert store.fail(retry, 'Provider unavailable')
    with pytest.raises(ValueError, match='Attempt limit'):
        store.retry(retry['id'])


@pytest.mark.parametrize(('kind', 'team'), [('work.terminal', 'general'), ('work.draft', 'other-team')])
def test_unhandled_type_or_foreign_team_becomes_visible_intervention(store, kind, team):
    objective(store)
    plan(store, [{'title': 'Special task', 'description': 'Needs a specific capability.', 'type': kind, 'team': team, 'dependsOn': []}])
    assert store.claim_next() is None
    snapshot = store.snapshot()
    request = next(r for r in snapshot['requests'] if r['type'] == kind)
    assert request['status'] == 'pending_intervention'
    assert kind in request['reason'] and team in request['reason']
    assert snapshot['objectives'][0]['status'] == 'needs_input'
    assert snapshot['tasks'][0]['status'] == 'blocked'


def test_priority_capacity_and_cancellation_fence_inflight_results(store):
    low = objective(store, 'low', priority='low')
    high = objective(store, 'high', priority='high')
    claim = store.claim_next()
    assert claim['objective_id'] == high['id']
    assert store.claim_next() is None  # the executive slot is occupied
    assert store.cancel(high['id'])
    assert not store.cancel(high['id'])
    assert not store.finish(claim, {'tasks': []})
    assert not store.heartbeat(claim)
    assert store.claim_next()['objective_id'] == low['id']
    with pytest.raises(ValueError, match='Cancelled'):
        store.retry(claim['id'])


def test_rejected_review_revises_with_evidence_then_stops_at_budget(tmp_path):
    store = OrganizationStore(tmp_path/'state.db', replace(OrganizationSettings(), max_revisions=1))
    objective(store)
    plan(store, [{'title': 'Draft', 'description': 'Write it', 'type': 'work.draft', 'team': 'general', 'dependsOn': []}])
    work(store)
    review(store, False)
    revision = store.claim_next()
    context = store.context(revision)
    assert context['feedback'] == 'Add a concrete comparison.'
    assert context['evidence'][0]['content']
    store.finish(revision, {'summary': 'Revision', 'deliverable': 'Improved but insufficient.'})
    review(store, False)
    assert store.claim_next() is None
    pending = next(r for r in store.snapshot()['requests'] if r['status'] == 'pending_intervention')
    assert 'revision limit' in pending['reason']
    assert store.snapshot()['objectives'][0]['status'] == 'needs_input'
    with pytest.raises(ValueError, match='Attempt limit'):
        store.retry(pending['id'])


@pytest.mark.parametrize('first_type', ['work.draft', 'request.hire'])
def test_invalid_plan_is_transactional_and_staffing_is_bounded(store, first_type):
    def claim_independent_requests(ledger):
        # Coarse platform clocks can give these independent requests the same
        # timestamp. Either deterministic ID tie order must retain both the
        # existing worker's authority and the director's capacity guard.
        ordered_types = [first_type, next(kind for kind in ('work.draft', 'request.hire') if kind != first_type)]
        with ledger._write() as conn:
            # These synthetic tied IDs also own a durable contract. Retarget
            # the fixture atomically rather than leaving dangling provenance.
            conn.execute('PRAGMA defer_foreign_keys=ON')
            for index, kind in enumerate(ordered_types):
                conn.execute("UPDATE request_contracts SET request_id=? WHERE request_id IN (SELECT id FROM requests WHERE type=? AND status='queued')",
                             (f'req_tied_{index}', kind))
                conn.execute("UPDATE requests SET id=?,created=1 WHERE type=? AND status='queued'",
                             (f'req_tied_{index}', kind))
        claims = [ledger.claim_next(), ledger.claim_next()]
        assert [claim['type'] for claim in claims] == ordered_types
        return {claim['type']: claim for claim in claims}

    objective(store)
    decompose(store)
    claim = store.claim_next()
    with pytest.raises(ValueError, match='earlier'):
        store.finish(claim, {'tasks': [{'title': 'Bad graph', 'description': 'Cycle', 'type': 'work.draft', 'dependsOn': [0]}]})
    assert not store.snapshot()['tasks']
    assert next(row for row in store.snapshot()['requests'] if row['id'] == claim['id'])['status'] == 'running'
    store.finish(claim, {'tasks': [{'title': 'Draft', 'description': 'Write it', 'type': 'work.draft', 'dependsOn': []}], 'workers': 2})
    claims = claim_independent_requests(store)
    executing, hire = claims['work.draft'], claims['request.hire']
    assert store.claim_next() is None
    store.finish(hire, {})
    assert any(a['id'] == 'worker-2' for a in store.snapshot()['agents'])
    assert not store.finish(hire, {})
    assert store.fail(executing, 'Permission needed')
    other = OrganizationStore(store.path.parent/'other.db', replace(OrganizationSettings(), max_workers=1))
    objective(other)
    plan(other, [{'title':'Draft','description':'Write','type':'work.draft','dependsOn':[]}], workers=2)
    other_claims = claim_independent_requests(other)
    excess = other_claims['request.hire']
    with pytest.raises(ValueError, match='capacity'):
        other.finish(excess, {})
    other.fail(excess, 'Staffing exceeds configured capacity')
    assert not any(a['id'] == 'worker-2' for a in other.snapshot()['agents'])


def test_profile_isolation_input_limits_and_backpressure(tmp_path):
    settings = replace(OrganizationSettings(), max_open_objectives=1)
    first = OrganizationStore(tmp_path/'profile-one'/'state.db', settings)
    second = OrganizationStore(tmp_path/'profile-two'/'state.db', settings)
    obj = objective(first)
    assert second.snapshot()['objectives'] == []
    with pytest.raises(ValueError, match='not found'):
        second.cancel(obj['id'])
    with pytest.raises(ValueError, match='open-objective limit'):
        objective(first, 'second')
    with pytest.raises(ValueError, match='Title'):
        first.create_objective('x'*501, idempotency_key='too-long')
    assert first.cancel(obj['id'])
    objective(first, 'second')
    for config in [{'max_workers': 99}, {'max_attempts': True}, {'capabilities': ['work.terminal']}]:
        with pytest.raises(ValueError):
            OrganizationSettings.from_config({'organization': config})


def test_duplicate_submission_returns_accepted_objective_outside_history_window(store):
    oldest = objective(store, 'oldest')
    store.cancel(oldest['id'])
    for index in range(26):
        current = objective(store, str(index))
        store.cancel(current['id'])
    assert not any(o['id'] == oldest['id'] for o in store.snapshot()['objectives'])
    assert objective(store, 'oldest')['id'] == oldest['id']


def test_retry_receipt_prevents_duplicate_transport_replay_after_quick_failure(store):
    objective(store)
    first = store.claim_next()
    store.fail(first, 'Provider unavailable')
    assert store.retry(first['id'], idempotency_key='retry-once')
    second = store.claim_next()
    assert second['attempts'] == 2
    store.fail(second, 'Provider still unavailable')
    assert not store.retry(first['id'], idempotency_key='retry-once')
    assert store.claim_next() is None
    assert store.retry_recorded(first['id'], 'retry-once')
    with pytest.raises(ValueError, match='different request'):
        store.retry('other-request', idempotency_key='retry-once')
