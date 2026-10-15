"""Actual accepted predecessor, intake admission and claim fences in one ledger."""
import json

import pytest

from tests.eidolon_cli.test_organization_dependency_receipts import accepted, damage
from tests.eidolon_cli.test_organization_request_intake import response, reserve


def prepared(tmp_path, *, explicit=True):
    store, predecessor, _ = accepted(tmp_path)
    text = f'Use objective {predecessor} to prepare the follow-on report.' if explicit else 'Prepare a follow-on report.'
    intake, _ = store.begin_intake(text, idempotency_key='follow-on')
    reserve(store, intake)
    finished = store.finish_intake(intake['id'], response(store, intake, dependencyCandidateIds=[predecessor]),
                                   usage={'inputTokens': 13, 'outputTokens': 7})
    return store, predecessor, intake, finished


def test_exact_owner_reference_admits_one_pinned_dependency_and_claims(tmp_path):
    store, predecessor, intake, finished = prepared(tmp_path)
    assert finished['status'] == 'proposed'
    admitted = store.admit_intake(intake['id'])
    assert admitted['status'] == 'admitted'
    assert store.admit_intake(intake['id']) == admitted
    with store._connect() as conn:
        edges = conn.execute('SELECT * FROM organization_objective_dependencies').fetchall()
        assert len(edges) == 1
        assert edges[0]['predecessor_id'] == predecessor
        assert edges[0]['successor_id'] == admitted['objectiveId']
        record = json.loads(edges[0]['record'])
        assert record['predecessor']['receipt']['liveSourceVerified'] is False
    claim = store.claim_next()
    assert claim is not None and claim['objective_id'] == admitted['objectiveId']
    assert admitted['usage']['inputTokens'] == 13
    assert admitted['usage']['outputTokens'] == 7


def test_model_only_dependency_stays_review_required_without_objective(tmp_path):
    store, _, intake, finished = prepared(tmp_path, explicit=False)
    assert finished['status'] == 'needs_review'
    assert 'affirmative dependency directive' in finished['reason']
    with pytest.raises(ValueError, match='review'):
        store.admit_intake(intake['id'])
    with store._connect() as conn:
        assert conn.execute('SELECT count(*) FROM objectives').fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM organization_objective_dependencies').fetchone()[0] == 0
    assert finished['usage']['reservedTokens'] == 2000


def test_changed_acceptance_before_admission_creates_no_successor(tmp_path):
    store, _, intake, _ = prepared(tmp_path)
    damage(store, "UPDATE objective_deliverables SET content=content || 'corrupt'")
    with pytest.raises(ValueError, match='context changed'):
        store.admit_intake(intake['id'])
    with store._connect() as conn:
        assert conn.execute('SELECT count(*) FROM objectives').fetchone()[0] == 1


def test_invalidated_predecessor_blocks_claim_without_attempt_or_scheduler_debit(tmp_path):
    store, predecessor, intake, _ = prepared(tmp_path)
    admitted = store.admit_intake(intake['id'])
    with store._connect() as conn:
        before_claims = conn.execute('SELECT count(*) FROM organization_scheduler_claims').fetchone()[0]
    damage(store, 'UPDATE objectives SET cancelled=1 WHERE id=?', (predecessor,))
    assert store.claim_next() is None
    with store._connect() as conn:
        assert conn.execute('SELECT count(*) FROM organization_scheduler_claims').fetchone()[0] == before_claims
        assert conn.execute('SELECT sum(attempts) FROM requests WHERE objective_id=?', (admitted['objectiveId'],)).fetchone()[0] == 0


def test_retained_dependency_body_reaches_prompt_without_becoming_acceptance_evidence(tmp_path):
    from eidolon_cli.organization_executor import _prompt
    store, predecessor, intake, _ = prepared(tmp_path)
    admitted = store.admit_intake(intake['id'])
    claim = store.claim_next()
    context = store.context(claim)
    dependency = context['acceptedDependencies'][0]
    assert dependency['objectiveId'] == predecessor
    assert dependency['historical'] and dependency['untrusted'] and not dependency['liveSourceVerified']
    assert dependency['content']
    assert dependency['artifactId'] not in [item['id'] for item in context['evidence']]
    prompt = _prompt(claim, context, claim['type'])
    submitted = json.loads(prompt.split('Submitted context:\n', 1)[1])
    assert submitted['context']['acceptedDependencies'][0]['content'] == dependency['content']
    assert 'not acceptance evidence' in prompt
    assert admitted['objectiveId'] != predecessor


def test_predecessor_drift_after_claim_blocks_dispatch_and_model_reservation(tmp_path):
    from eidolon_cli.organization_objective_dependencies import ObjectiveDependencyError
    store, predecessor, intake, _ = prepared(tmp_path)
    store.admit_intake(intake['id'])
    claim = store.claim_next(recoverable=True)
    assert store.context(claim)['acceptedDependencies']
    with store._connect() as conn:
        before = conn.execute('SELECT count(*) FROM organization_model_calls').fetchone()[0]
    damage(store, "UPDATE objective_deliverables SET content=content || 'changed' WHERE objective_id=?", (predecessor,))
    with pytest.raises(ObjectiveDependencyError):
        store.begin_dispatch(claim)
    with pytest.raises(ObjectiveDependencyError, match='stale_dependency_before_model_call'):
        store.reserve_model_call(claim, provider='test', model='small', input_limit=50, output_limit=50)
    with store._connect() as conn:
        assert conn.execute('SELECT count(*) FROM organization_model_calls').fetchone()[0] == before
        assert conn.execute('SELECT dispatched FROM organization_execution_journal WHERE request_id=? AND token=?',
                            (claim['id'], claim['token'])).fetchone()[0] == 0


@pytest.mark.parametrize('unknown', [False, True])
def test_omitted_owner_prerequisite_requires_review_before_admission(tmp_path, unknown):
    store, predecessor, _ = accepted(tmp_path)
    requested = 'unknown-objective' if unknown else predecessor
    intake, _ = store.begin_intake(f'Use objective {requested} to prepare the follow-on report.',
                                    idempotency_key='omitted-prerequisite')
    reserve(store, intake)
    finished = store.finish_intake(intake['id'], response(store, intake, dependencyCandidateIds=[]),
                                   usage={'inputTokens': 13, 'outputTokens': 7})
    assert finished['status'] == 'needs_review'
    assert finished['reason']
    with pytest.raises(ValueError, match='review'):
        store.admit_intake(intake['id'])
    with store._connect() as conn:
        assert conn.execute('SELECT count(*) FROM objectives').fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM organization_objective_dependencies').fetchone()[0] == 0
    assert finished['usage']['inputTokens'] == 13
    assert finished['usage']['outputTokens'] == 7
