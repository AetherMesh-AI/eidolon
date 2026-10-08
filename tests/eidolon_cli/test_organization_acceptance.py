"""Real SQLite objective acceptance, typed owner input and bounded histories."""
from dataclasses import replace
import hashlib
import json
import sqlite3
import time

import pytest

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_store import OrganizationStore, _SCHEMA
from tests.organization_package_helpers import decompose


def _objective(store, **kwargs):
    return store.create_objective('Decision brief', 'Produce one coherent recommendation from supplied facts.',
                                  idempotency_key='objective', acceptance_criteria=['One supported recommendation'], **kwargs)


def _plan(store):
    decompose(store)
    claim = store.claim_next()
    assert claim['type'] == 'request.plan'
    store.finish(claim, {'tasks': [{'title': 'Analyze', 'description': 'Support the recommendation', 'type': 'work.analyze'}]})


def _work_and_review(store):
    work = store.claim_next()
    assert work['type'] == 'work.analyze'
    store.finish(work, {'summary': 'Compared options', 'deliverable': 'Option A is supported by the supplied facts.'})
    review = store.claim_next()
    ids = [item['id'] for item in store.context(review)['evidence']]
    store.finish(review, {'approved': True, 'summary': 'The task result is supported.', 'evidenceIds': ids})
    return work, ids


def _integrate(store):
    claim = store.claim_next()
    assert claim['type'] == 'request.integrate'
    store.finish(claim, {'summary': 'Combined outcome', 'deliverable': 'Choose A. The exact supplied facts support this decision.'})
    accept = store.claim_next()
    assert accept['type'] == 'request.accept' and accept['agent_id'] != claim['agent_id']
    return accept


def _decision(store, claim, approved=True):
    context = store.context(claim)
    ids = [item['id'] for item in context['evidence']]
    return {'approved': approved, 'summary': 'Meets the complete objective.' if approved else 'Task artifacts conflict; reconcile the recommendation.',
            'evidenceIds': ids, 'conflicts': [] if approved else ['Task conclusions conflict.'],
            'criteriaResults': [{'criterion': criterion, 'satisfied': approved, 'evidenceIds': ids,
                                 'reason': 'Exact evidence supports the integrated outcome.' if approved else 'No coherent final outcome.'}
                                for criterion in context['objective']['acceptanceCriteria']]}


def test_clarification_resume_acceptance_is_durable_exact_and_not_task_completion(tmp_path):
    store = OrganizationStore(tmp_path / 'state.db')
    objective = _objective(store)
    decompose(store)
    plan = store.claim_next()
    store.finish(plan, {'intervention': 'Which facts should be prioritized?'})
    request = next(row for row in store.snapshot()['requests'] if row['id'] == plan['id'])
    assert 'provide_input' in [item['action'] for item in request['allowedResolutions']]
    assert store.resolve(plan['id'], 'provide_input', 'Prefer lower operational risk.', idempotency_key='answer')
    assert not store.resolve(plan['id'], 'provide_input', 'Prefer lower operational risk.', idempotency_key='answer')
    with pytest.raises(ValueError, match='different input'):
        store.resolve(plan['id'], 'provide_input', 'A different answer', idempotency_key='answer')
    store = OrganizationStore(store.path)
    claim = store.claim_next()
    assert store.context(claim)['ownerInputs'][0]['text'] == 'Prefer lower operational risk.'
    store.finish(claim, {'tasks': [{'title': 'Analyze', 'description': 'Support the recommendation', 'type': 'work.analyze'}]})
    _, task_evidence = _work_and_review(store)
    assert store.snapshot()['objectives'][0]['status'] != 'completed'
    accept = _integrate(store)
    context = store.context(accept)
    assert len(context['evidence']) == 2 and context['evidence'][-1]['kind'] == 'integrated_deliverable'
    assert context['agent']['role'] == 'Executive' and context['toolPolicy']['tools'] == []
    value = _decision(store, accept)
    with pytest.raises(ValueError, match='exactly'):
        store.finish(accept, {**value, 'evidenceIds': task_evidence})
    with pytest.raises(ValueError, match='every exact'):
        store.finish(accept, {**value, 'criteriaResults': []})
    assert store.finish(accept, value)
    reopened = OrganizationStore(store.path)
    outcome = reopened.snapshot()['objectives'][0]
    assert outcome['id'] == objective['id'] and outcome['status'] == 'completed'
    assert outcome['result'] == reopened.evidence(outcome['acceptance']['deliverableId'])['content']
    assert outcome['result'] != 'Compared options'
    assert reopened.claim_next() is None
    assert not reopened.finish(accept, value)
    assert not reopened.resolve(plan['id'], 'provide_input', 'Prefer lower operational risk.', idempotency_key='answer')


def test_conflicting_accepted_tasks_trigger_bounded_replan_and_preserve_exact_history(tmp_path):
    store = OrganizationStore(tmp_path / 'state.db', replace(OrganizationSettings(), max_replans=1))
    _objective(store)
    _plan(store)
    _, original_ids = _work_and_review(store)
    accept = _integrate(store)
    old_final = store.context(accept)['evidence'][-1]['id']
    rejection = _decision(store, accept, False)
    with pytest.raises(ValueError, match='conflicting'):
        store.finish(accept, {**rejection, 'approved': True})
    assert store.finish(accept, rejection)
    snap = store.snapshot()
    assert snap['objectives'][0]['acceptance']['round'] == 1
    assert snap['tasks'][0]['historical'] and not snap['tasks'][0]['currentRound']
    assert store.evidence(old_final)['content'] and store.evidence(original_ids[0])['content']
    _plan(store)
    _work_and_review(store)
    second = _integrate(store)
    store.finish(second, _decision(store, second, False))
    snap = store.snapshot()
    assert snap['objectives'][0]['status'] == 'needs_input'
    assert snap['objectives'][0]['acceptance']['status'] == 'blocked'
    gate = next(row for row in snap['requests'] if row['status'] == 'pending_intervention')
    assert gate['allowedResolutions'] == []
    assert store.claim_next() is None
    with pytest.raises(ValueError, match='unavailable'):
        store.resolve(gate['id'], 'request_replan', 'Try indefinitely', idempotency_key='over-budget')


def test_resolution_fences_live_claims_policy_changes_cancel_and_transaction_failures(tmp_path):
    store = OrganizationStore(tmp_path / 'state.db')
    objective = _objective(store)
    plan = store.claim_next()
    store.fail(plan, 'Need facts')
    with store._write() as conn:
        conn.execute("CREATE TRIGGER fail_resolution BEFORE INSERT ON owner_resolutions BEGIN SELECT RAISE(ABORT,'test disk failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match='test disk failure'):
        store.resolve(plan['id'], 'provide_input', 'The facts', idempotency_key='atomic')
    assert store.snapshot()['requests'][0]['status'] == 'pending_intervention'
    with store._write() as conn:
        conn.execute('DROP TRIGGER fail_resolution')
    newer = OrganizationStore(store.path, replace(store.settings, capabilities=('work.draft',)))
    with pytest.raises(ValueError, match='changed'):
        store.resolve(plan['id'], 'provide_input', 'The facts', idempotency_key='atomic')
    assert newer.resolve(plan['id'], 'provide_input', 'The facts', idempotency_key='atomic')
    live = newer.claim_next()
    assert not store.finish(plan, {'tasks': []})
    newer.cancel(objective['id'])
    assert not newer.finish(live, {'tasks': []})
    with pytest.raises(ValueError, match='unavailable'):
        newer.resolve(plan['id'], 'amend_scope', 'New scope', idempotency_key='cancelled')
    other = OrganizationStore(tmp_path / 'other' / 'state.db')
    with pytest.raises(ValueError, match='not found'):
        other.resolve(plan['id'], 'provide_input', 'The facts', idempotency_key='atomic')


def test_scope_amendment_is_audited_replans_without_changing_grants(tmp_path):
    store = OrganizationStore(tmp_path / 'state.db')
    _objective(store)
    plan = store.claim_next()
    store.fail(plan, 'Unsupported external action')
    assert store.resolve(plan['id'], 'amend_scope', 'Only draft the message.', idempotency_key='amend')
    next_plan = store.claim_next()
    context = store.context(next_plan)
    assert context['objective']['description'] == 'Only draft the message.'
    assert context['objective']['acceptanceCriteria'] == ['Only draft the message.']
    assert context['objective']['originalDescription'].startswith('Produce one')
    assert context['toolPolicy']['tools'] == []
    assert store.settings == OrganizationSettings()
    assert store.snapshot()['objectives'][0]['ownerResolutions'][0]['action'] == 'amend_scope'


def test_objective_stage_budget_survives_reopen_and_reports_unknown_usage(tmp_path):
    store = OrganizationStore(tmp_path / 'state.db', replace(OrganizationSettings(), max_stages=5))
    _objective(store)
    _plan(store)
    _work_and_review(store)
    integrate = store.claim_next()
    store.finish(integrate, {'summary': 'Integrated', 'deliverable': 'Full brief', 'usage': {'inputTokens': 35, 'outputTokens': 12}})
    assert store.claim_next() is None
    reopened = OrganizationStore(store.path)
    objective = reopened.snapshot()['objectives'][0]
    assert objective['status'] == 'needs_input'
    assert objective['usage']['stages'] == objective['usage']['stageLimit'] == 5
    assert objective['usage']['outputTokens'] == 12 and not objective['usage']['usageComplete']
    assert all(not item['allowedResolutions'] for item in reopened.snapshot()['requests'])


def test_required_project_tests_cannot_be_invented_by_model_approval(tmp_path):
    store = OrganizationStore(tmp_path / 'state.db')
    _objective(store, required_checks=['project_tests'])
    _plan(store)
    _work_and_review(store)
    assert store.claim_next() is None
    snapshot = store.snapshot()
    gate = next(row for row in snapshot['requests'] if row['type'] == 'request.project_test')
    assert gate['status'] == 'pending_intervention'
    assert 'explicitly granted worker' in gate['reason']
    assert snapshot['objectives'][0]['status'] != 'completed'
    with store._connect() as conn:
        with pytest.raises(ValueError, match='have not been executed'):
            store._verify_required_checks(conn, gate['objectiveId'], ['project_tests'])
    assert snapshot['objectives'][0]['requiredChecks'] == ['project_tests']


def test_old_settled_history_migrates_once_without_reopening(tmp_path):
    path = tmp_path / 'legacy.db'
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    conn.execute("INSERT INTO objectives VALUES ('old','key','digest','Old title','Old scope',3,?,0)", (time.time(),))
    conn.execute("INSERT INTO tasks(id,objective_id,title,description,type,team,priority,status,dependencies,result) VALUES ('task','old','Task','Scope','work.draft','general',3,'completed','[]','Legacy summary')")
    conn.execute("INSERT INTO requests(id,objective_id,task_id,type,team,priority,status,created) VALUES ('request','old','task','work.draft','general',3,'completed',?)", (time.time(),))
    conn.commit()
    conn.close()
    for _ in range(2):
        store = OrganizationStore(path)
        objective = store.snapshot()['objectives'][0]
        assert objective['status'] == 'completed' and objective['acceptance']['status'] == 'legacy_completed'
        assert objective['result'] == 'Legacy summary'
        assert store.claim_next() is None


def test_create_extended_identity_cannot_confuse_criteria_with_required_checks(tmp_path):
    store = OrganizationStore(tmp_path / 'state.db')
    store.create_objective('Same goal', 'Same facts', acceptance_criteria=['project_tests'], idempotency_key='same')
    with pytest.raises(ValueError, match='different objective'):
        store.create_objective('Same goal', 'Same facts', required_checks=['project_tests'], idempotency_key='same')
    assert store.snapshot()['objectives'][0]['requiredChecks'] == []


def test_final_acceptance_refuses_changed_integrated_bytes_and_self_approval(tmp_path):
    store = OrganizationStore(tmp_path / 'state.db')
    _objective(store)
    _plan(store)
    _work_and_review(store)
    accept = _integrate(store)
    result = _decision(store, accept)
    final_id = store.context(accept)['evidence'][-1]['id']
    with store._write() as conn:
        conn.execute("UPDATE objective_deliverables SET content='Changed after review context' WHERE id=?", (final_id,))
    with pytest.raises(ValueError, match='missing, stale or changed'):
        store.finish(accept, result)
    with store._write() as conn:
        conn.execute('UPDATE objective_deliverables SET content=?,author_id=? WHERE id=?',
                     ('Choose A. The exact supplied facts support this decision.', 'executive', final_id))
    with pytest.raises(ValueError, match='own final deliverable'):
        store.finish(accept, result)
    assert store.snapshot()['objectives'][0]['status'] != 'completed'


def test_acceptance_request_binds_hashes_even_if_artifact_hash_is_recomputed(tmp_path):
    store = OrganizationStore(tmp_path / 'state.db')
    _objective(store)
    _plan(store)
    _work_and_review(store)
    accept = _integrate(store)
    result = _decision(store, accept)
    final_id = store.context(accept)['evidence'][-1]['id']
    replacement = 'Different outcome with a recomputed hash.'
    with store._write() as conn:
        conn.execute('UPDATE objective_deliverables SET content=?,sha256=? WHERE id=?',
                     (replacement, hashlib.sha256(replacement.encode()).hexdigest(), final_id))
    with pytest.raises(ValueError, match='exactly the current'):
        store.finish(accept, result)
    assert store.snapshot()['objectives'][0]['status'] != 'completed'


def test_legacy_retry_cannot_bypass_owner_budget_on_an_unroutable_task(tmp_path):
    store = OrganizationStore(tmp_path / 'state.db', replace(OrganizationSettings(), max_owner_resolutions=1))
    _objective(store)
    decompose(store)
    plan = store.claim_next()
    store.finish(plan, {'tasks': [{'title': 'Unsupported', 'description': 'A missing capability', 'type': 'work.external'}]})
    assert store.claim_next() is None
    pending = next(item for item in store.snapshot()['requests'] if item['status'] == 'pending_intervention')
    assert store.retry(pending['id'], idempotency_key='one')
    assert store.claim_next() is None
    with pytest.raises(ValueError, match='resolution limit'):
        store.retry(pending['id'], idempotency_key='two')
    assert store.snapshot()['requests'][-1]['allowedResolutions'] == []


def test_legacy_open_review_binds_exact_bytes_when_claimed_by_new_runtime(tmp_path):
    store = OrganizationStore(tmp_path / 'state.db')
    _objective(store)
    _plan(store)
    work = store.claim_next()
    store.finish(work, {'summary': 'Original', 'deliverable': 'Original task artifact'})
    with store._write() as conn:
        row = conn.execute("SELECT * FROM requests WHERE type='request.review'").fetchone()
        payload = json.loads(row['payload'])
        payload.pop('evidenceHashes')
        conn.execute('UPDATE requests SET payload=? WHERE id=?', (json.dumps(payload), row['id']))
    review = store.claim_next()
    evidence = store.context(review)['evidence'][0]
    changed = 'Changed artifact with updated local digest'
    with store._write() as conn:
        conn.execute('UPDATE evidence SET content=?,sha256=? WHERE id=?',
                     (changed, hashlib.sha256(changed.encode()).hexdigest(), evidence['id']))
    with pytest.raises(ValueError, match='missing or has changed'):
        store.finish(review, {'approved': True, 'summary': 'Approved original bytes', 'evidenceIds': [evidence['id']]})


def test_missing_legacy_evidence_becomes_actionable_without_starving_other_work(tmp_path):
    store = OrganizationStore(tmp_path / 'state.db')
    _objective(store)
    _plan(store)
    work = store.claim_next()
    store.finish(work, {'summary': 'Original', 'deliverable': 'Original artifact'})
    with store._write() as conn:
        review = conn.execute("SELECT * FROM requests WHERE type='request.review'").fetchone()
        payload = json.loads(review['payload'])
        payload.pop('evidenceHashes')
        conn.execute('UPDATE requests SET payload=? WHERE id=?', (json.dumps(payload), review['id']))
        conn.execute('DELETE FROM evidence')
    other = store.create_objective('Other work', idempotency_key='other')
    claim = store.claim_next()
    assert claim['objective_id'] == other['id']
    pending = next(row for row in store.snapshot()['requests'] if row['id'] == review['id'])
    assert pending['status'] == 'pending_intervention' and 'evidence is missing' in pending['reason']
