"""Completion protection is durable, atomic, bounded and backend-classified."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
import json
import sqlite3
import threading

import pytest

from eidolon_cli.organization_budget import OrganizationModelCost, budget_view
from eidolon_cli.organization_completion_reserve import COMPLETION_REQUEST_TYPES, COMPLETION_RESERVE_FIELDS
from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_store import OrganizationStore
from tests.organization_package_helpers import claim_after_decomposition


def configured(tmp_path, **changes):
    settings = replace(OrganizationSettings(max_output_tokens=512, max_context_tokens=4096), **changes)
    store = OrganizationStore(tmp_path / 'state.db', settings)
    objective = store.create_objective('Produce a supported result', idempotency_key='objective')
    return store, objective


def reserve(store, claim, tokens=200, **changes):
    return store.reserve_model_call(claim, **{'provider': 'local', 'model': 'fixture',
        'input_limit': tokens - 100, 'output_limit': 100, **changes})


def usage(store, objective):
    with store._connect() as conn:
        return budget_view(conn, objective['id'], store.settings)


def backend_request(store, objective, kind):
    # The trusted producer queues a separate stage; the public model result API
    # cannot select any of these request types (covered below).
    with store._write() as conn:
        return store._request(conn, objective['id'], kind, store.settings.team, 3)


def work_claim(store):
    plan = claim_after_decomposition(store)
    assert store.finish(plan, {'tasks': [{'title': 'Write supported brief', 'description': 'Use the supplied facts', 'type': 'work.draft'}]})
    while True:
        claim = store.claim_next()
        if claim['type'] != 'request.hire':
            assert claim['type'] == 'work.draft'
            return claim
        assert store.finish(claim, {})


def test_ordinary_exhaustion_preserves_real_review_integration_acceptance_and_receipts(tmp_path):
    store, objective = configured(tmp_path, max_model_calls=4, completion_reserve_model_calls=3,
                                 max_total_tokens=4096, completion_reserve_tokens=2000)
    work = work_claim(store)
    ordinary = reserve(store, work, tokens=1000)
    with pytest.raises(ValueError, match='protected completion'):
        reserve(store, {**work, 'type': 'request.accept', 'classification': 'completion'})
    store.finish(work, {'summary': 'Complete fact', 'deliverable': 'The supported fact is retained.'})
    review = store.claim_next()
    assert review['type'] == 'request.review'
    reserve(store, review, tokens=500)
    evidence = store.context(review)['evidence']
    store.finish(review, {'approved': True, 'summary': 'Fact supported', 'evidenceIds': [row['id'] for row in evidence]})
    integrate = store.claim_next()
    assert integrate['type'] == 'request.integrate'
    reserve(store, integrate, tokens=500)
    store.finish(integrate, {'summary': 'Integrated fact', 'deliverable': 'The supported result is this fact.'})
    accept = store.claim_next()
    assert accept['type'] == 'request.accept'
    reserve(store, accept, tokens=500)
    context = store.context(accept)
    ids = [row['id'] for row in context['evidence']]
    store.finish(accept, {'approved': True, 'summary': 'All criteria independently satisfied',
        'evidenceIds': ids, 'conflicts': [], 'criteriaResults': [
            {'criterion': criterion, 'satisfied': True, 'reason': 'Exact retained evidence supports it', 'evidenceIds': ids}
            for criterion in context['objective']['acceptanceCriteria']]})
    reopened = OrganizationStore(store.path)
    result = reopened.snapshot()['objectives'][0]
    assert result['status'] == 'completed'
    assert result['usage']['modelCalls'] == result['usage']['modelCallLimit'] == 4
    view = result['usage']['completionReserve']
    assert view['completionReserved']['modelCalls'] == 3
    assert view['protectedRemaining']['modelCalls'] == view['ordinaryRemaining']['modelCalls'] == 0
    assert view['protectedRemaining']['tokens'] == 500
    assert result['usage']['usageComplete'] is False  # Missing provider usage never refunds admission.
    assert reopened.execution_audit(work['id'])['modelCalls'][0]['id'] == ordinary
    for claim, category in ((work, 'ordinary'), (review, 'completion'), (integrate, 'completion'), (accept, 'completion')):
        receipt = reopened.execution_audit(claim['id'])['modelCalls'][0]
        assert (receipt['classification'], receipt['request_type'], receipt['classification_policy']) == (category, claim['type'], 'completion-v1')
    assert reopened.create_objective('Produce a supported result', idempotency_key='objective')['id'] == objective['id']
    assert usage(reopened, objective)['modelCalls'] == 4


@pytest.mark.parametrize('dimension', ['calls', 'tokens', 'cost'])
def test_two_instances_concurrently_admit_only_ordinary_headroom_and_bounded_completion(tmp_path, dimension):
    settings = {'max_model_calls': 6, 'completion_reserve_model_calls': 4}
    if dimension == 'tokens':
        settings = {'max_total_tokens': 4096, 'completion_reserve_tokens': 3296}
    elif dimension == 'cost':
        settings = {'max_cost_usd': '0.000006', 'completion_reserve_cost_usd': '0.000004',
                    'model_costs': (OrganizationModelCost('local', 'fixture', '0.005', '0.005'),)}
    store, objective = configured(tmp_path, **settings)
    ordinary = store.claim_next()
    backend_request(store, objective, 'request.review')
    complete = store.claim_next()
    assert complete['type'] == 'request.review'
    peers = [OrganizationStore(store.path), OrganizationStore(store.path)]
    barrier = threading.Barrier(8)
    tokens = 400 if dimension == 'tokens' else 200

    def attempt(index):
        barrier.wait(timeout=10)
        claim = ordinary if index < 5 else complete
        try:
            return index, reserve(peers[index % 2], claim, tokens=tokens)
        except ValueError as exc:
            return index, str(exc)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))
    assert len([value for index, value in results if index < 5 and value.startswith('modelcall_')]) == 2
    assert len([value for index, value in results if index >= 5 and value.startswith('modelcall_')]) == 3
    view = usage(store, objective)
    assert view['modelCalls'] == 5
    assert view['completionReserve']['completionReserved']['modelCalls'] == 3
    assert view['reservedTokens'] == 5 * tokens
    assert all('protected completion' in value for _, value in results if not value.startswith('modelcall_'))
    reserve(store, complete, tokens=tokens)  # Completion can consume its remaining protected allowance.
    assert usage(store, objective)['modelCalls'] == 6


@pytest.mark.parametrize('kind', COMPLETION_REQUEST_TYPES)
def test_classification_is_immutable_backend_request_type_and_retries_never_refund(tmp_path, kind):
    store, objective = configured(tmp_path, max_model_calls=6, completion_reserve_model_calls=4)
    ordinary = store.claim_next()
    reserve(store, ordinary)
    assert store.finish(ordinary, {'intervention': 'Stop ordinary stage'})
    backend_request(store, objective, kind)
    complete = store.claim_next()
    assert complete['type'] == kind
    first = reserve(store, {**complete, 'type': 'work.draft'})
    second = reserve(store, complete)  # Potential fallback physical sends remain charged even if unused.
    store.fail(complete, 'Transport outcome unknown', retryable=False)
    reopened = OrganizationStore(store.path)
    assert reopened.retry(complete['id'], idempotency_key='retry')
    assert not reopened.retry(complete['id'], idempotency_key='retry')
    retry = reopened.claim_next()
    assert retry['token'] != complete['token']
    with pytest.raises(ValueError, match='no longer owned'):
        reserve(reopened, complete)
    reserve(reopened, retry)
    view = usage(reopened, objective)['completionReserve']
    assert view['completionReserved']['modelCalls'] == 3
    assert view['protectedRemaining']['modelCalls'] == 1
    assert view['ordinaryRemaining']['modelCalls'] == 1
    assert [row['id'] for row in reopened.execution_audit(complete['id'])['modelCalls']][:2] == [first, second]
    for statement in ("UPDATE organization_model_call_classes SET classification='ordinary'",
                      "DELETE FROM organization_model_call_classes", "UPDATE organization_model_calls SET input_limit=1",
                      "DELETE FROM organization_model_calls", "UPDATE objective_completion_reserves SET model_calls=0",
                      "DELETE FROM objective_completion_reserves", "UPDATE requests SET type='request.accept' WHERE type='request.decompose'"):
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            with reopened._write() as conn:
                conn.execute(statement)
    reopened.cancel(objective['id'])
    with pytest.raises(ValueError, match='no longer owned'):
        reserve(reopened, retry)
    assert usage(reopened, objective)['completionReserve'] == view


def test_cap_lowering_and_reserve_configuration_cannot_release_captured_protection(tmp_path):
    store, objective = configured(tmp_path, max_model_calls=5, completion_reserve_model_calls=3,
                                 max_total_tokens=6000, completion_reserve_tokens=5000)
    claim = store.claim_next()
    reserve(store, claim, tokens=1000)
    before = usage(store, objective)
    lowered = replace(store.settings, max_model_calls=2, max_total_tokens=4096,
                      completion_reserve_model_calls=0, completion_reserve_tokens=0)
    peer = OrganizationStore(store.path, lowered)
    with pytest.raises(ValueError, match='no longer owned'):
        reserve(store, claim)
    assert peer.retry(claim['id'], idempotency_key='lowered')
    retry = peer.claim_next()
    with pytest.raises(ValueError, match='protected completion'):
        reserve(peer, retry)
    view = usage(peer, objective)
    assert view['completionReserve']['captured'] == before['completionReserve']['captured']
    assert view['completionReserve']['protectedRemaining']['modelCalls'] == 3
    assert view['completionReserve']['protectedRemaining']['tokens'] == 5000
    assert view['completionReserve']['ordinaryRemaining']['tokens'] == 0
    assert view['completionReserve']['ordinaryRemaining']['modelCalls'] == 0
    assert view['completionReserve']['totalRemaining']['modelCalls'] == 1
    peer.reload_configuration(replace(lowered, max_model_calls=1))
    with pytest.raises(ValueError, match='budget exhausted'):
        peer.retry(retry['id'], idempotency_key='hard-exhausted')
    assert usage(peer, objective)['completionReserve']['protectedRemaining']['modelCalls'] == 3
    assert usage(peer, objective)['deadlineAt'] == before['deadlineAt']


@pytest.mark.parametrize('change', ['reserve', 'source', 'expired_lease'])
def test_reserve_policy_source_and_expired_lease_fence_physical_admission(tmp_path, change):
    store, objective = configured(tmp_path, completion_reserve_model_calls=1)
    claim = store.claim_next()
    if change == 'expired_lease':
        with store._write() as conn:
            conn.execute('UPDATE requests SET lease=0 WHERE id=?', (claim['id'],))
    else:
        updated = replace(store.settings, **({'completion_reserve_model_calls': 2} if change == 'reserve' else {'read_roots': (str(tmp_path),)}))
        peer = OrganizationStore(store.path, updated)
        assert peer._policy_generation > store._policy_generation
    with pytest.raises(ValueError, match='no longer owned'):
        reserve(store, claim)
    assert usage(store, objective)['modelCalls'] == 0


def test_legacy_migration_preserves_caps_deadlines_unknown_marker_and_live_policy_generation(tmp_path):
    from eidolon_cli.organization_policy import _fingerprint, _AUTHORITY_FIELDS
    store, objective = configured(tmp_path)
    claim = store.claim_next()
    reserve(store, claim)
    with store._write() as conn:
        caps = tuple(conn.execute('SELECT * FROM objective_budgets').fetchone())
        conn.execute('DROP TABLE organization_completion_reserve_migration')
        conn.execute('DROP TABLE organization_model_call_classes')
        conn.execute('DROP TABLE objective_completion_reserves')
        values = asdict(store.settings)
        for name in COMPLETION_RESERVE_FIELDS:
            values.pop(name)
        old_fields = tuple(name for name in _AUTHORITY_FIELDS if name not in COMPLETION_RESERVE_FIELDS)
        old_hash = _fingerprint(values, old_fields)
        conn.execute('UPDATE organization_policy SET fingerprint=?,settings=?', (old_hash, json.dumps(values)))
    generation = store._policy_generation
    reopened = OrganizationStore(store.path)
    assert reopened._policy_generation == generation
    assert reopened.heartbeat(claim)
    receipt = reopened.execution_audit(claim['id'])['modelCalls'][0]
    assert receipt['classification'] == 'ordinary' and receipt['classification_policy'] == 'legacy-v0'
    with reopened._write() as conn:
        assert tuple(conn.execute('SELECT * FROM objective_budgets').fetchone()) == caps
        conn.execute('UPDATE objective_budgets SET legacy_usage_unknown=1')
    newer = OrganizationStore(store.path, replace(reopened.settings, completion_reserve_model_calls=3, completion_reserve_tokens=1000))
    view = usage(newer, objective)
    assert view['legacyUsageUnknown'] is True
    assert view['completionReserve']['captured'] == {'modelCalls': 0, 'tokens': 0, 'configuredCostUsd': None}
    for name in ('completionReserved', 'protectedRemaining', 'ordinaryRemaining', 'totalRemaining'):
        assert view['completionReserve'][name] == dict.fromkeys(('modelCalls', 'tokens', 'configuredCostUsd'))
    assert not view['completionReserve']['admissionUsageKnown']
    assert view['completionReserve']['unavailableReason'] == 'legacy-usage-unknown'
    with pytest.raises(ValueError, match='Prior model usage is unknown'):
        newer.retry(claim['id'], idempotency_key='unknown')
    fresh = newer.create_objective('New bounded work', idempotency_key='fresh')
    assert fresh['usage']['completionReserve']['captured']['modelCalls'] == 3
