"""Actor-authored control requests keep the actual persistent requester."""
from eidolon_cli.organization_store import OrganizationStore


def _start_work(store):
    store.create_objective('Prepare a supported recommendation', idempotency_key='goal')
    planner = store.claim_next()
    store.finish(planner, {'tasks': [
        {'title': 'Analyze', 'description': 'Support the recommendation', 'type': 'work.analyze'},
    ]})
    work = store.claim_next()
    assert work['type'] == 'work.analyze'
    return planner, work


def _request(store, identifier):
    return next(request for request in store.snapshot()['requests'] if request['id'] == identifier)


def _finish_work(store, work):
    store.finish(work, {'summary': 'Compared options', 'deliverable': 'Option A follows from the supplied facts.'})
    review = store.claim_next()
    assert review['type'] == 'request.review'
    return review, [evidence['id'] for evidence in store.context(review)['evidence']]


def test_review_and_revision_requesters_are_the_actual_worker_and_reviewer(tmp_path):
    store = OrganizationStore(tmp_path / 'state.db')
    planner, work = _start_work(store)
    assert _request(store, work['id'])['requesterId'] == planner['agent_id']
    review, ids = _finish_work(store, work)
    assert _request(store, review['id'])['requesterId'] == work['agent_id']
    store.finish(review, {'approved': False, 'summary': 'Explain the supporting facts.', 'evidenceIds': ids})
    revision = store.claim_next()
    assert revision['task_id'] == work['task_id'] and revision['id'] != work['id']
    assert _request(store, revision['id'])['requesterId'] == review['agent_id']
    assert _request(store, work['id'])['requesterId'] == planner['agent_id']
    assert _request(store, review['id'])['requesterId'] == work['agent_id']
    with store._connect() as conn:
        historical = conn.execute('SELECT agent_id FROM agent_history WHERE request_id=?', (work['id'],)).fetchone()
        assert historical['agent_id'] == work['agent_id']


def test_executive_and_owner_replans_keep_distinct_requester_provenance(tmp_path):
    store = OrganizationStore(tmp_path / 'state.db')
    original_plan, work = _start_work(store)
    review, ids = _finish_work(store, work)
    store.finish(review, {'approved': True, 'summary': 'The task is supported.', 'evidenceIds': ids})
    integration = store.claim_next()
    assert integration['type'] == 'request.integrate'
    store.finish(integration, {'summary': 'Integrated recommendation', 'deliverable': 'Choose option A.'})
    acceptance = store.claim_next()
    context = store.context(acceptance)
    assert context['agent']['role'] == 'Executive'
    assert _request(store, acceptance['id'])['requesterId'] == integration['agent_id']
    ids = [evidence['id'] for evidence in context['evidence']]
    store.finish(acceptance, {
        'approved': False, 'summary': 'The final recommendation needs stronger support.',
        'evidenceIds': ids, 'conflicts': [], 'criteriaResults': [
            {'criterion': criterion, 'satisfied': False, 'evidenceIds': ids,
             'reason': 'The final recommendation omits its factual support.'}
            for criterion in context['objective']['acceptanceCriteria']],
    })
    executive_replan = store.claim_next()
    assert executive_replan['type'] == 'request.plan'
    assert _request(store, executive_replan['id'])['requesterId'] == acceptance['agent_id']
    store.fail(executive_replan, 'Owner scope clarification required.')
    store.resolve(executive_replan['id'], 'request_replan', 'Use the revised owner scope.', idempotency_key='owner-replan')
    owner_replan = store.claim_next()
    assert owner_replan['type'] == 'request.plan' and owner_replan['id'] != executive_replan['id']
    assert _request(store, owner_replan['id'])['requesterId'] == 'owner'
    assert _request(store, original_plan['id'])['requesterId'] == 'owner'
    assert _request(store, executive_replan['id'])['requesterId'] == acceptance['agent_id']
