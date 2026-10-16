"""Real store lifecycle with synthetic host receipts; no Docker or model calls."""
import json
import time
from types import SimpleNamespace

import pytest

from eidolon_cli.organization_prepared_validation_current import verify_current_prepared_pair
from eidolon_cli.organization_store import OrganizationStore
from eidolon_cli import organization_executor as executor
from eidolon_cli import organization_prepared_validation_setup as setup
from eidolon_cli.organization_prepared_validation_authority import has_prepared_binding
from eidolon_cli.organization_evidence import context_report, verify_context_receipt
from tests.eidolon_cli.test_organization_prepared_validation_stage_runtime import (
    confirmed, folder_profile, host, owner, prepared, scalar, setup_running,  # noqa: F401
)

pytestmark = pytest.mark.linux_only
CRITERIA = [
    {'id': 'report', 'kind': 'deliverable', 'text': 'Explain exact prepared validation coverage and limitations.'},
    {'id': 'compiler', 'kind': 'authorized_check', 'checkKind': 'project_typecheck', 'projectId': 'notes',
     'text': 'The exact selected source passes the compiler check.'},
    {'id': 'ui', 'kind': 'authorized_check', 'checkKind': 'project_tests', 'projectId': 'notes',
     'text': 'The exact selected UI suite passes nonempty tests.'},
]


@pytest.fixture
def running(owner):
    return setup_running(owner, acceptance_criteria=CRITERIA, full_planning=True)


def finish_host(running, monkeypatch):
    calls = host(monkeypatch, running)
    result = running.store.run_project_stage(running.claim, running.cancel)
    assert result == {} and len(calls) == 2
    assert running.store.finish(running.claim, result) is True
    review = running.store.claim_next(recoverable=True)
    assert review is not None and review['type'] == 'request.test_review'
    assert review['agent_id'] != 'inspector'
    return calls, review


def record_complete_context(store, request):
    context = store.context(request)
    prompt = executor._prompt(request, context, request['type'])
    model = SimpleNamespace(provider='synthetic', model='synthetic-review',
                            context_compressor=SimpleNamespace(context_length=128000))
    report = context_report(request, context, prompt, executor._SYSTEM, model)
    assert report['status'] == 'complete' and report['fullEvidence'] is True
    verify_context_receipt(context, report, [], approved=True)
    store.record_context_receipt(request, report)
    for item in context['evidence']:
        assert store.evidence(item['id'])['sha256'] == item['sha256']
    return context


def review_result(running, review):
    record_complete_context(running.store, review)
    return {'approved': True,
        'summary': 'Synthetic independent review of both typed receipts and all selected source/test/configuration evidence.',
        'evidenceIds': json.loads(review['payload'])['evidenceIds']}


def acceptance_claim(store):
    integrate = store.claim_next(recoverable=True)
    assert integrate is not None and integrate['type'] == 'request.integrate'
    record_complete_context(store, integrate)
    assert store.finish(integrate, {'summary': 'Prepared compiler and UI pair retained with independent review.',
        'deliverable': 'Both exact typed prepared checks passed. The selected tests/configurations and source evidence were independently reviewed. This fixture proves orchestration; native runtime coverage is separately gated.'})
    acceptance = store.claim_next(recoverable=True)
    assert acceptance is not None and acceptance['type'] == 'request.accept'
    assert acceptance['agent_id'] != integrate['agent_id']
    context = record_complete_context(store, acceptance)
    evidence_ids = json.loads(acceptance['payload'])['evidenceIds']
    result = {'approved': True, 'summary': 'All exact typed criteria have retained independently reviewed evidence.',
        'evidenceIds': evidence_ids, 'conflicts': [], 'criteriaResults': [
            {'criterion': criterion['text'], 'criterionId': criterion['id'], 'criterionRevision': criterion['revision'],
             'satisfied': True, 'evidenceIds': evidence_ids,
             'reason': 'Exact current retained source, whole-pair review and typed check evidence support this criterion.'}
            for criterion in context['objective']['outcomeContract']['criteria']]}
    return acceptance, result


def finish_acceptance(store):
    acceptance, result = acceptance_claim(store)
    assert store.finish(acceptance, result)
    return acceptance


def test_store_dispatch_finish_independent_review_and_reopened_verification(running, monkeypatch):
    calls, review = finish_host(running, monkeypatch)
    payload = json.loads(review['payload'])
    assert payload['executionContract'] == 'prepared-validation-v1'
    assert len(payload['evidenceIds']) == 4
    with running.store._connect() as conn:
        with pytest.raises(ValueError, match='whole-pair review'):
            verify_current_prepared_pair(running.store, conn, running.objective, 'notes')
    assert running.store.finish(review, review_result(running, review)) is True
    finish_acceptance(running.store)
    reopened = OrganizationStore(running.store.path, running.store.settings)
    with reopened._connect() as conn:
        verified = verify_current_prepared_pair(reopened, conn, running.objective, 'notes')
        assert verified['runtimeReceiptsVerified'] is verified['independentReviewVerified'] is True
        assert verified['sourceDeliveryAuthorized'] is verified['replayAllowed'] is False
        assert verified['evidenceHashes'] == payload['evidenceHashes']
        assert {item[1]['projectExecution']['checkKind'] for item in verified['checks']} == {'project_typecheck', 'project_tests'}
    assert len(calls) == 2
    assert scalar(running, 'SELECT sum(units) FROM prepared_validation_steps') == 2
    assert scalar(running, 'SELECT count(*) FROM prepared_validation_pair_reviews') == 1
    assert scalar(running, 'SELECT status FROM objective_control WHERE objective_id=?', (running.objective,)) == 'accepted'
    assert scalar(running, 'SELECT count(*) FROM objective_contract_acceptances') == 1


@pytest.mark.parametrize('response', [{'status': 'passed'}, {'approved': True}, {'receiptSha256': 'a' * 64}])
def test_model_output_cannot_complete_unexecuted_backend_stage(running, response):
    with pytest.raises(ValueError, match='model-provided'):
        running.store.finish(running.claim, response)
    assert scalar(running, 'SELECT count(*) FROM prepared_validation_sets') == 0
    assert scalar(running, "SELECT count(*) FROM requests WHERE type='request.test_review'") == 0


def test_failed_first_check_finishes_as_intervention_without_pair_review(running, monkeypatch):
    def fail(receipt, _options, _number):
        receipt['status'] = 'failed'
    calls = host(monkeypatch, running, fail)
    result = running.store.run_project_stage(running.claim, running.cancel)
    assert running.store.finish(running.claim, result)
    assert len(calls) == 1 and scalar(running, 'SELECT sum(units) FROM prepared_validation_steps') == 2
    assert scalar(running, "SELECT count(*) FROM requests WHERE type='request.project_failed' AND status='pending_intervention'") == 1
    assert scalar(running, "SELECT count(*) FROM requests WHERE type='request.test_review'") == 0


def change_current_authority(running, change):
    if change == 'source':
        (running.owner.root / 'apps/desktop/src/main.test.tsx').write_text('changed after execution')
    elif change == 'bundle':
        (running.owner.dependency / 'node_modules/typescript/lib/typescript.js').write_text('// substituted compiler bytes')
    elif change == 'bundle_inode':
        running.owner.dependency.rename(running.owner.dependency.with_name('retained-original-dependencies'))
        running.owner.dependency.mkdir()
    elif change == 'author':
        with running.store._write() as conn:
            conn.execute("UPDATE staff_state SET active=0 WHERE agent_id='inspector'")
    elif change == 'registration':
        from tests.eidolon_cli.test_organization_prepared_validation_setup import revoke_params
        setup.revoke_prepared_validation(running.owner.home, revoke_params(running.owner, running.saved))
    else:
        raise AssertionError(change)


@pytest.mark.parametrize('change', ['source', 'bundle', 'bundle_inode', 'author', 'registration'])
def test_pair_review_rechecks_current_source_bundle_and_authority(running, monkeypatch, change):
    _calls, review = finish_host(running, monkeypatch)
    result = review_result(running, review)
    change_current_authority(running, change)
    with pytest.raises(ValueError):
        running.store.finish(review, result)
    assert scalar(running, 'SELECT count(*) FROM prepared_validation_pair_reviews') == 0
    assert scalar(running, 'SELECT count(*) FROM objective_acceptances') == 0
    assert scalar(running, 'SELECT sum(units) FROM prepared_validation_steps') == 2


@pytest.mark.parametrize('change', ['source', 'bundle', 'author', 'registration'])
def test_accepted_history_cannot_reauthorize_stale_current_bytes_or_grants(running, monkeypatch, change):
    _calls, review = finish_host(running, monkeypatch)
    assert running.store.finish(review, review_result(running, review))
    finish_acceptance(running.store)
    change_current_authority(running, change)
    reopened = OrganizationStore(running.store.path, running.store.settings)
    with reopened._connect() as conn:
        with pytest.raises(ValueError):
            verify_current_prepared_pair(reopened, conn, running.objective, 'notes')
    assert scalar(running, 'SELECT count(*) FROM objective_acceptances') == 1


@pytest.mark.parametrize('forgery', ['missing_evidence', 'duplicate_evidence', 'foreign_evidence', 'extra_receipt',
    'nonboolean_approval', 'empty_summary', 'same_author', 'payload_hash'])
def test_pair_review_rejects_forged_or_malformed_inputs_atomically(running, monkeypatch, forgery):
    _calls, review = finish_host(running, monkeypatch)
    result = review_result(running, review)
    if forgery == 'missing_evidence': result['evidenceIds'].pop()
    if forgery == 'duplicate_evidence': result['evidenceIds'].append(result['evidenceIds'][0])
    if forgery == 'foreign_evidence': result['evidenceIds'][0] = 'unrelated-artifact'
    if forgery == 'extra_receipt': result['receipt'] = {'status': 'passed'}
    if forgery == 'nonboolean_approval': result['approved'] = 1
    if forgery == 'empty_summary': result['summary'] = ''
    if forgery in {'same_author', 'payload_hash'}:
        with running.store._write() as conn:
            if forgery == 'same_author':
                conn.execute("UPDATE requests SET agent_id='inspector' WHERE id=?", (review['id'],))
                review['agent_id'] = 'inspector'
            else:
                payload = json.loads(review['payload'])
                payload['evidenceHashes'][payload['evidenceIds'][0]] = 'a' * 64
                conn.execute('UPDATE requests SET payload=? WHERE id=?', (json.dumps(payload), review['id']))
    with pytest.raises(ValueError):
        running.store.finish(review, result)
    assert scalar(running, 'SELECT count(*) FROM prepared_validation_pair_reviews') == 0
    assert scalar(running, "SELECT status FROM requests WHERE id=?", (review['id'],)) == 'running'


def test_independent_rejection_never_becomes_acceptance_or_refunds_the_pair(running, monkeypatch):
    _calls, review = finish_host(running, monkeypatch)
    result = review_result(running, review)
    result.update(approved=False, summary='The selected assertions do not sufficiently cover the requested behavior.')
    assert running.store.finish(review, result)
    assert scalar(running, 'SELECT approved FROM prepared_validation_pair_reviews') == 0
    assert scalar(running, 'SELECT sum(units) FROM prepared_validation_steps') == 2
    assert scalar(running, 'SELECT count(*) FROM objective_acceptances') == 0
    with running.store._connect() as conn:
        next_plan = conn.execute("SELECT payload FROM requests WHERE type='request.decompose' AND status='queued'").fetchone()
        history = json.loads(next_plan['payload'])['projectExecutionHistory']
    assert len(history) == 2
    assert all(item['review']['approved'] is False and item['review']['requestId'] == review['id'] for item in history)


@pytest.mark.parametrize('forgery', ['criterion_revision', 'criterion_evidence', 'final_evidence'])
def test_typed_acceptance_rejects_wrong_criterion_or_evidence(running, monkeypatch, forgery):
    _calls, review = finish_host(running, monkeypatch)
    assert running.store.finish(review, review_result(running, review))
    acceptance, result = acceptance_claim(running.store)
    if forgery == 'criterion_revision': result['criteriaResults'][1]['criterionRevision'] += 1
    if forgery == 'criterion_evidence': result['criteriaResults'][1]['evidenceIds'] = ['unknown-artifact']
    if forgery == 'final_evidence': result['evidenceIds'] = result['evidenceIds'][:-1]
    with pytest.raises(ValueError):
        running.store.finish(acceptance, result)
    assert scalar(running, 'SELECT count(*) FROM objective_acceptances') == 0
    assert scalar(running, 'SELECT count(*) FROM objective_contract_acceptances') == 0


def test_crash_after_successful_dispatch_still_blocks_automatic_replay(running, monkeypatch):
    calls = host(monkeypatch, running)
    assert running.store.begin_dispatch(running.claim)
    assert running.store.run_project_stage(running.claim, running.cancel) == {}
    with running.store._write() as conn:
        conn.execute('UPDATE requests SET lease=? WHERE id=?', (time.time() - 1, running.claim['id']))
    assert running.store.recover_expired() == 1
    assert scalar(running, 'SELECT status FROM requests WHERE id=?', (running.claim['id'],)) == 'pending_intervention'
    with pytest.raises(ValueError):
        running.store.run_project_stage(running.claim, running.cancel)
    assert len(calls) == 2 and scalar(running, 'SELECT sum(units) FROM prepared_validation_steps') == 2


def test_registration_does_not_activate_an_unselected_legacy_objective(running):
    with running.store._write() as conn:
        objective = running.store._create_objective(conn, 'Legacy planning-only folder',
            idempotency_key='legacy-unselected', project_ids=['notes'], delivery_mode='managed_artifact')
        assert has_prepared_binding(conn, objective, 'notes') is False
        assert running.store.ensure_project_execution(conn, objective) is False
        assert not conn.execute('SELECT 1 FROM prepared_validation_registration_bindings WHERE objective_id=?', (objective,)).fetchone()
        assert not conn.execute('SELECT 1 FROM prepared_validation_sets WHERE objective_id=?', (objective,)).fetchone()


@pytest.mark.parametrize('phase', ['pair_review', 'acceptance', 'after_maybe_integrate'])
@pytest.mark.parametrize('loss', ['lease', 'deadline'])
def test_long_verification_cannot_commit_after_lease_or_deadline_expiry(running, monkeypatch, phase, loss):
    _calls, review = finish_host(running, monkeypatch)
    result = review_result(running, review)
    claim = review
    if phase == 'acceptance':
        assert running.store.finish(review, result)
        claim, result = acceptance_claim(running.store)
    with running.store._write() as conn:
        if loss == 'lease':
            expiry = conn.execute('SELECT lease FROM requests WHERE id=?', (claim['id'],)).fetchone()[0] + 1
        else:
            expiry = conn.execute('SELECT deadline FROM objective_budgets WHERE objective_id=?', (running.objective,)).fetchone()[0] + 1
            conn.execute('UPDATE requests SET lease=? WHERE id=?', (expiry + 1000, claim['id']))
    name = {'pair_review': '_finish_test_review', 'acceptance': '_verify_required_checks',
            'after_maybe_integrate': '_maybe_integrate'}[phase]
    checked = getattr(running.store, name)
    def slow_verification(*args, **kwargs):
        value = checked(*args, **kwargs)
        monkeypatch.setattr(time, 'time', lambda: expiry)
        return value
    monkeypatch.setattr(running.store, name, slow_verification)
    with pytest.raises(ValueError, match='original lease or deadline'):
        running.store.finish(claim, result)
    assert scalar(running, 'SELECT status FROM requests WHERE id=?', (claim['id'],)) == 'running'
    assert scalar(running, 'SELECT count(*) FROM objective_acceptances') == 0
    assert scalar(running, 'SELECT count(*) FROM prepared_validation_pair_reviews') == int(phase == 'acceptance')
    assert scalar(running, 'SELECT sum(units) FROM prepared_validation_steps') == 2
    if phase == 'after_maybe_integrate':
        assert scalar(running, "SELECT count(*) FROM requests WHERE type='request.integrate'") == 0


def test_accepted_prepared_pair_is_ineligible_for_downstream_dependency_links(running, monkeypatch):
    from eidolon_cli.organization_dependency_receipts import accepted_dependency_receipt
    _calls, review = finish_host(running, monkeypatch)
    assert running.store.finish(review, review_result(running, review))
    finish_acceptance(running.store)
    with running.store._connect() as conn:
        assert accepted_dependency_receipt(conn, running.objective) is None


def test_failure_history_retains_both_charged_runs_without_inventing_second_result(running, monkeypatch):
    from eidolon_cli.organization_project_execution import replan_project_history
    def fail(receipt, _options, _number):
        receipt['status'] = 'failed'
    host(monkeypatch, running, fail)
    assert running.store.finish(running.claim, running.store.run_project_stage(running.claim, running.cancel))
    with running.store._connect() as conn:
        history = replan_project_history(conn, running.objective, 0)
    assert len(history) == 2
    assert {item['status'] for item in history} == {'failed', 'unknown'}
    assert sum(len(item['evidenceIds']) for item in history) == 1
    assert all(item['executionContract'] == 'prepared-validation-v1' for item in history)
    assert scalar(running, 'SELECT sum(units) FROM prepared_validation_steps') == 2


@pytest.mark.parametrize('completing_request', [None, 'unrelated-running-request'])
def test_history_never_accepts_an_unrelated_incomplete_review(running, monkeypatch, completing_request):
    from eidolon_cli.organization_prepared_validation_history import prepared_replan_history
    _calls, review = finish_host(running, monkeypatch)
    assert running.store.finish(review, review_result(running, review))
    with running.store._write() as conn:
        conn.execute("UPDATE requests SET status='running',token='other-attempt',lease=? WHERE id=?",
                     (time.time() + 100, review['id']))
        with pytest.raises(ValueError, match='completed request'):
            prepared_replan_history(conn, running.objective, 0, completing_request=completing_request)

