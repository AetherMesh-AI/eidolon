"""No-progress loops stop visibly without losing retained responses or identities."""
import pytest

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_store import OrganizationStore


def store_with_advisors(tmp_path):
    settings = OrganizationSettings.from_config({'organization': {'max_inflight': 1, 'roster': [
        {'id': 'writer', 'name': 'Writer', 'team': 'general', 'capabilities': ['work.draft']},
        {'id': 'advisor-a', 'name': 'Advisor A', 'role': 'Manager', 'team': 'alpha',
         'capabilities': ['request.question'], 'authority': ['answer.question']},
        {'id': 'advisor-b', 'name': 'Advisor B', 'role': 'Manager', 'team': 'beta',
         'capabilities': ['request.question'], 'authority': ['answer.question']},
    ]}})
    store = OrganizationStore(tmp_path / 'state.db', settings)
    objective = store.create_objective('Write the brief', idempotency_key='goal')
    plan = store.claim_next()
    store.finish(plan, {'tasks': [{'title': 'Draft', 'description': 'Write the brief', 'type': 'work.draft', 'agentId': 'writer'}]})
    hire = store.claim_next()
    store.finish(hire, {})
    return store, objective, store.claim_next()


def question(team='alpha', outcome='Which audience?'):
    return {'type': 'request.question', 'requestedOutcome': outcome, 'team': team}


def test_duplicate_batch_and_answered_repetition_do_not_create_more_requests(tmp_path):
    store, _, work = store_with_advisors(tmp_path)
    initial = len(store.snapshot()['requests'])
    with pytest.raises(ValueError, match='Duplicate typed requests'):
        store.finish(work, {'requests': [question(), question(outcome='  Which audience?  ')]})
    assert len(store.snapshot()['requests']) == initial
    assert store.finish(work, {'requests': [question()]})
    advisor = store.claim_next()
    store.finish(advisor, {'answer': 'The board'})
    reopened = OrganizationStore(store.path)
    resumed = reopened.claim_next()
    assert resumed['id'] == work['id'] and resumed['agent_id'] == 'writer'
    before = len(reopened.snapshot()['requests'])
    with pytest.raises(ValueError, match='retained response'):
        reopened.finish(resumed, {'requests': [question(outcome='Which audience?')]})
    assert len(reopened.snapshot()['requests']) == before
    assert reopened.context(resumed)['requestResponses'][0]['response']['text'] == 'The board'
    reopened.fail(resumed, 'Repeated request needs owner clarification', retryable=False)
    pending = next(row for row in reopened.snapshot()['requests'] if row['id'] == work['id'])
    assert pending['status'] == 'pending_intervention'
    assert pending['reason'].startswith('Repeated request')


def test_specialist_escalation_can_cross_teams_but_ping_pong_cannot_return(tmp_path):
    store, objective, work = store_with_advisors(tmp_path)
    store.finish(work, {'requests': [question('alpha')]})
    alpha = store.claim_next()
    assert alpha['agent_id'] == 'advisor-a'
    store.finish(alpha, {'requests': [question('beta')]})
    beta = store.claim_next()
    assert beta['agent_id'] == 'advisor-b'
    before = len(store.snapshot()['requests'])
    with pytest.raises(ValueError, match='ping-pong'):
        store.finish(beta, {'requests': [question('alpha')]})
    assert len(store.snapshot()['requests']) == before
    store.fail(beta, 'Need owner answer', retryable=False)
    reopened = OrganizationStore(store.path)
    assert reopened.respond(beta['id'], 'The board', idempotency_key='owner-answer')
    resumed_alpha = reopened.claim_next()
    assert resumed_alpha['id'] == alpha['id'] and resumed_alpha['agent_id'] == 'advisor-a'
    reopened.finish(resumed_alpha, {'answer': 'The board'})
    resumed_writer = reopened.claim_next()
    assert resumed_writer['id'] == work['id'] and resumed_writer['agent_id'] == 'writer'
    assert reopened.cancel(objective['id'])
    assert not reopened.finish(resumed_writer, {'summary': 'Late', 'deliverable': 'Rejected after cancellation'})
    assert not reopened.respond(beta['id'], 'The board', idempotency_key='owner-answer')


def test_duplicate_plan_cannot_schedule_redundant_tasks_or_self_cycles(tmp_path):
    store = OrganizationStore(tmp_path / 'state.db')
    store.create_objective('One result', idempotency_key='goal')
    plan = store.claim_next()
    task = {'title': 'Analyze', 'description': 'Analyze the supplied facts', 'type': 'work.analyze', 'team': 'general'}
    with pytest.raises(ValueError, match='duplicate tasks'):
        store.finish(plan, {'tasks': [task, {**task, 'title': ' Analyze '}]})
    with pytest.raises(ValueError, match='earlier task'):
        store.finish(plan, {'tasks': [{**task, 'dependsOn': [0]}]})
    assert store.snapshot()['tasks'] == []
    assert store.finish(plan, {'tasks': [task]})
    assert len(store.snapshot()['tasks']) == 1
    assert not store.finish(plan, {'tasks': [task]})


def test_duplicate_guard_preserves_distinct_case_sensitive_source_targets(tmp_path):
    store = OrganizationStore(tmp_path / 'state.db')
    store.create_objective('Analyze both source targets', idempotency_key='goal')
    plan = store.claim_next()
    assert store.finish(plan, {'tasks': [
        {'title': 'Analyze README', 'description': 'Analyze supplied root0/README facts', 'type': 'work.analyze'},
        {'title': 'Analyze readme', 'description': 'Analyze supplied root0/readme facts', 'type': 'work.analyze'},
    ]})
    assert len(store.snapshot()['tasks']) == 2
