"""Package prerequisites retain exact independent proof before downstream work."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from eidolon_cli.organization_evidence import context_report
from eidolon_cli.organization_executor import _prompt, _SYSTEM
from tests.eidolon_cli.test_organization_work_packages import (
    decompose, finish_review, ledger_dump, make_store, next_nonstaffing, plan_all,
    plan_output,
)


def test_independent_plan_receipt_survives_upstream_review_completion(tmp_path):
    store, _ = make_store(tmp_path)
    decompose(store)
    plans = [store.claim_next() for _ in range(3)]
    dependent = next(claim for claim in plans if claim['agent_id'] == 'lead-c')
    context = store.context(dependent)
    assert context['dependencies'] == []
    agent = SimpleNamespace(provider='local', model='fixture',
                            context_compressor=SimpleNamespace(context_length=128000))
    report = context_report(dependent, context, _prompt(dependent, context, dependent['type']), _SYSTEM, agent)
    store.record_context_receipt(dependent, report)
    for claim in plans:
        if claim != dependent:
            assert store.finish(claim, plan_output(claim))
    for _ in range(10):
        claim = next_nonstaffing(store)
        if claim is None:
            break
        if claim['type'] == 'request.review':
            finish_review(store, claim)
        else:
            assert claim['type'] == 'work.draft'
            assert store.finish(claim, {'summary': 'Prepared', 'deliverable': 'Reviewed upstream finding'})
    upstream = [row for row in store.context(dependent)['workPackages']
                if row['id'] in context['workPackage']['dependencyIds']]
    assert len(upstream) == 2 and all(row['status'] == 'completed' for row in upstream)
    assert store.context(dependent)['dependencies'] == []
    assert store.finish(dependent, plan_output(dependent))
    work = next_nonstaffing(store)
    assert work['agent_id'] == 'writer-c'
    assert {row['workPackageId'] for row in store.context(work)['dependencies']} == set(context['workPackage']['dependencyIds'])


@pytest.mark.parametrize('corruption', [
    'missing-artifact', 'changed-body', 'rehashed-body', 'missing-review',
    'review-link', 'self-review', 'review-hash',
])
def test_package_dependencies_refuse_missing_or_changed_independently_reviewed_evidence(tmp_path, corruption):
    store, _ = make_store(tmp_path)
    decompose(store)
    plan_all(store)
    for _ in range(20):
        claim = next_nonstaffing(store)
        assert claim is not None
        if claim['type'] == 'work.draft' and claim['agent_id'] == 'writer-c':
            break
        if claim['type'] == 'request.review':
            finish_review(store, claim)
        else:
            assert claim['type'] == 'work.draft'
            assert store.finish(claim, {'summary': 'Exact finding',
                                       'deliverable': f"Reviewed finding by {claim['agent_id']}."})
    else:
        pytest.fail('The independent upstream packages did not finish')
    context = store.context(claim)
    assert {item['workPackageId'] for item in context['dependencies']} == set(context['workPackage']['dependencyIds'])
    assert _prompt(claim, context, claim['type'])
    evidence = context['dependencies'][0]
    with store._write() as conn:
        review = conn.execute('SELECT * FROM reviews WHERE task_id=?', (evidence['taskId'],)).fetchone()
        replacement = 'Unreviewed replacement finding.'
        payload = json.loads(conn.execute('SELECT payload FROM requests WHERE id=?', (review['request_id'],)).fetchone()[0])
        payload['evidenceHashes'] = {}
        mutations = {
            'missing-artifact': ('DELETE FROM evidence WHERE id=?', (evidence['evidenceId'],)),
            'changed-body': ('UPDATE evidence SET content=? WHERE id=?', (replacement, evidence['evidenceId'])),
            'rehashed-body': ('UPDATE evidence SET content=?,sha256=? WHERE id=?',
                              (replacement, hashlib.sha256(replacement.encode()).hexdigest(), evidence['evidenceId'])),
            'missing-review': ('DELETE FROM reviews WHERE request_id=?', (review['request_id'],)),
            'review-link': ('UPDATE reviews SET evidence_ids=? WHERE request_id=?', ('[]', review['request_id'])),
            'self-review': ('UPDATE reviews SET agent_id=(SELECT author_id FROM tasks WHERE id=?) WHERE request_id=?',
                            (evidence['taskId'], review['request_id'])),
            'review-hash': ('UPDATE requests SET payload=? WHERE id=?', (json.dumps(payload), review['request_id'])),
        }
        conn.execute(*mutations[corruption])
    with pytest.raises(ValueError, match='Package dependency'):
        store.context(claim)
    assert store.heartbeat(claim)
    assert not any(row['type'] == 'request.integrate' for row in store.snapshot()['requests'])


@pytest.mark.parametrize('project_id', [[], {}, True, 1])
def test_malformed_package_project_id_is_a_transactional_validation_error(tmp_path, project_id):
    store, _ = make_store(tmp_path)
    decompose(store)
    claim = store.claim_next()
    output = plan_output(claim)
    output['tasks'][0]['projectId'] = project_id
    before = ledger_dump(store)
    with pytest.raises(ValueError, match='projectId'):
        store.finish(claim, output)
    assert ledger_dump(store) == before
    assert store.heartbeat(claim)
    assert store.finish(claim, plan_output(claim))
