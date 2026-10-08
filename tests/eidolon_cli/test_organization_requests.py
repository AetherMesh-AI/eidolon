"""Linked requests retain authority, exact answers and persistent continuations."""
import json

import pytest

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_executor import _parse_output, _prompt, OrganizationExecutionError
from eidolon_cli.organization_store import OrganizationStore


def ledger(tmp_path, roster=None):
    settings = OrganizationSettings.from_config({'organization': {'max_inflight': 1, 'roster': roster or [
        {'id': 'writer', 'name': 'Writer', 'team': 'general', 'capabilities': ['work.draft']},
        {'id': 'advisor', 'name': 'Advisor', 'role': 'Manager', 'team': 'general',
         'capabilities': ['request.question', 'request.decision'],
         'authority': ['answer.question', 'answer.decision']},
    ]}})
    return OrganizationStore(tmp_path / 'state.db', settings)


def start_work(store):
    objective = store.create_objective('Prepare a useful brief', idempotency_key='goal')
    plan = store.claim_next()
    store.finish(plan, {'tasks': [{'title': 'Draft', 'description': 'Write the brief',
                                 'type': 'work.draft', 'agentId': 'writer'}]})
    hire = store.claim_next()
    assert hire['type'] == 'request.hire'
    store.finish(hire, {})
    work = store.claim_next()
    assert work['agent_id'] == 'writer'
    return objective, work


def ask(store, work, kind='request.question', **fields):
    store.finish(work, {'requests': [{'type': kind, 'requestedOutcome': 'Which audience?', **fields}]})
    return next(request for request in store.snapshot()['requests'] if request['parentRequestId'] == work['id'])


def test_answer_restarts_same_assignment_and_keeps_exact_provenance(tmp_path):
    store = ledger(tmp_path)
    _, work = start_work(store)
    question = ask(store, work)
    assert question['requesterId'] == 'writer' and question['requiredAuthority'] == 'answer.question'
    assert next(row for row in store.snapshot()['requests'] if row['id'] == work['id'])['status'] == 'waiting_response'
    assert not store.finish(work, {'requests': [{'type': 'request.question', 'requestedOutcome': 'duplicate'}]})
    answer = store.claim_next()
    assert answer['agent_id'] == 'advisor'
    assert store.context(answer)['requestContract']['requestedOutcome'] == 'Which audience?'
    store.finish(answer, {'answer': 'The board. Keep implementation details in an appendix.', 'decision': 'answered'})
    assert not store.finish(answer, {'answer': 'overwritten'})
    reopened = OrganizationStore(store.path)
    resumed = reopened.claim_next()
    assert resumed['id'] == work['id'] and resumed['agent_id'] == 'writer'
    context = reopened.context(resumed)
    assert context['requestResponses'][0]['response']['text'].startswith('The board.')
    assert context['requestResponses'][0]['response']['responderId'] == 'advisor'
    assert context['requestResponses'][0]['requesterId'] == 'writer'
    reopened.finish(resumed, {'summary': 'Board brief', 'deliverable': 'Brief and technical appendix.',
                              'memory': {'facts': ['The requested audience is the board.']}})
    writer = next(row for row in reopened.snapshot()['agents'] if row['id'] == 'writer')
    assert writer['context']['memory']['facts'] == ['The requested audience is the board.']
    assert len(writer['context']['recentHistory']) == 1


def test_unhandled_permission_requires_exact_owner_answer_without_grant_expansion(tmp_path):
    store = ledger(tmp_path)
    obj, work = start_work(store)
    permission = ask(store, work, 'request.permission')
    before = store.settings
    assert store.claim_next() is None
    pending = next(row for row in store.snapshot()['requests'] if row['id'] == permission['id'])
    assert pending['status'] == 'pending_intervention'
    assert {item['action'] for item in pending['allowedResolutions']} == {'approve_request', 'deny_request'}
    with pytest.raises(ValueError, match='response'):
        store.retry(permission['id'], idempotency_key='bypass')
    with pytest.raises(ValueError, match='explicit'):
        store.respond(permission['id'], 'Allow it', idempotency_key='answer')
    assert store.respond(permission['id'], 'Only use submitted text.', 'denied', idempotency_key='answer')
    assert not store.respond(permission['id'], 'Only use submitted text.', 'denied', idempotency_key='answer')
    with pytest.raises(ValueError, match='different input'):
        store.respond(permission['id'], 'Expanded approval', 'approved', idempotency_key='answer')
    assert store.settings == before
    resumed = store.claim_next()
    assert store.context(resumed)['requestResponses'][0]['response']['decision'] == 'denied'
    store.cancel(obj['id'])
    assert not store.finish(resumed, {'summary': 'late', 'deliverable': 'late'})
    assert not store.respond(permission['id'], 'Only use submitted text.', 'denied', idempotency_key='answer')


def test_only_accepted_type_team_and_authority_route_answers(tmp_path):
    store = ledger(tmp_path, [
        {'id': 'writer', 'name': 'Writer', 'team': 'general', 'capabilities': ['work.draft']},
        {'id': 'no-authority', 'name': 'No authority', 'role': 'Manager', 'team': 'general',
         'capabilities': ['request.question'], 'responsibilities': ['Answer everything']},
        {'id': 'wrong-team', 'name': 'Wrong team', 'role': 'Manager', 'team': 'private',
         'capabilities': ['request.question'], 'authority': ['answer.question']},
        {'id': 'wrong-type', 'name': 'Wrong type', 'role': 'Manager', 'team': 'general',
         'capabilities': ['request.decision'], 'authority': ['answer.question']},
    ])
    obj, work = start_work(store)
    question = ask(store, work)
    assert store.claim_next() is None
    assert store.respond(question['id'], 'For engineering.', idempotency_key='owner')
    resumed = store.claim_next()
    child = ask(store, resumed, team='missing')
    store.claim_next()
    store.cancel(obj['id'])
    with pytest.raises(ValueError, match='not awaiting'):
        store.respond(child['id'], 'Too late', idempotency_key='late')


def test_invalid_links_and_forged_authority_roll_back_and_request_budget_is_bounded(tmp_path):
    store = ledger(tmp_path)
    _, work = start_work(store)
    for invalid in [
        {'requiredAuthority': 'none'}, {'requesterId': 'owner'},
        {'dependencyIds': [work['id']]}, {'dependencyIds': ['foreign']}, {'evidenceIds': ['invented']},
    ]:
        with pytest.raises(ValueError):
            ask(store, work, **invalid)
        assert store.context(work)['requestResponses'] == []
    question = ask(store, work)
    answer = store.claim_next()
    nested = ask(store, answer, team='missing')
    store.claim_next()
    # The nested owner response resumes the existing advisor, then its writer.
    store = OrganizationStore(store.path)
    store.respond(nested['id'], 'Board audience', idempotency_key='nested')
    resumed_advisor = store.claim_next()
    assert resumed_advisor['id'] == answer['id']
    store.finish(resumed_advisor, {'answer': 'Board audience'})
    resumed_writer = store.claim_next()
    assert resumed_writer['id'] == work['id']
    assert store.context(resumed_writer)['requestResponses'][0]['id'] == question['id']


def test_model_wire_parser_preserves_typed_request_and_uses_durable_answers(tmp_path):
    store = ledger(tmp_path)
    _, work = start_work(store)
    payload = {'requests': [{'type': 'request.question', 'requestedOutcome': 'Which audience?'}]}
    result = _parse_output(json.dumps(payload), 'work.draft', store.context(work))
    assert result['requests'][0]['requiredAuthority'] == 'answer.question'
    store.finish(work, result)
    answer = store.claim_next()
    prompt = _prompt(answer, store.context(answer), answer['type'])
    assert 'Which audience?' in prompt
    parsed = _parse_output('{"answer":"The board","decision":"answered"}', answer['type'], store.context(answer))
    store.finish(answer, parsed)
    resumed = store.claim_next()
    assert 'The board' in _prompt(resumed, store.context(resumed), resumed['type'])
    with pytest.raises(OrganizationExecutionError, match='only return'):
        _parse_output(json.dumps({**payload, 'deliverable': 'premature'}), 'work.draft', {})


@pytest.mark.parametrize('kind', ['request.question', 'request.decision', 'request.permission'])
def test_planning_clarification_survives_downstream_restart_and_replan(tmp_path, kind):
    store = ledger(tmp_path)
    objective = store.create_objective('Prepare a brief', idempotency_key='clarified')
    plan = store.claim_next()
    ask(store, plan, kind, team='owner-only')
    assert store.claim_next() is None
    question = next(row for row in store.snapshot()['requests'] if row['type'] == kind)
    answer = 'Use the customer audience. The internal engineering audience is wrong.'
    decision = 'answered' if kind == 'request.question' else 'denied'
    grants = store.settings.tool_grants
    store.respond(question['id'], answer, decision, idempotency_key='answer')
    store = OrganizationStore(store.path, store.settings)
    plan = store.claim_next()
    context = store.context(plan)
    clarification, = context['objectiveClarifications']
    assert clarification['response']['text'] == answer
    assert clarification['response']['responderId'] == 'owner'
    assert clarification['response']['decision'] == decision
    assert store.settings.tool_grants == grants
    assert clarification['response']['createdAt']
    assert clarification['originRequestId'] == plan['id']
    criteria = context['objective']['acceptanceCriteria']
    store.finish(plan, {'tasks': [{'title': 'Draft', 'description': 'Write a brief',
                                 'type': 'work.draft', 'agentId': 'writer'}]})
    store.finish(store.claim_next(), {})  # existing staffing controller
    work = store.claim_next()
    clarification['parentStatus'] = 'completed'
    assert store.context(work)['objectiveClarifications'] == [clarification]
    store.finish(work, {'summary': 'Brief', 'deliverable': 'A brief without copied clarification text.'})
    review = store.claim_next()
    ids = [item['id'] for item in store.context(review)['evidence']]
    store.finish(review, {'approved': True, 'summary': 'Reviewed', 'evidenceIds': ids})
    integrate = store.claim_next()
    store.finish(integrate, {'summary': 'Combined', 'deliverable': 'Integrated brief.'})
    accept = store.claim_next()
    context = store.context(accept)
    assert context['objective']['acceptanceCriteria'] == criteria
    assert context['objectiveClarifications'] == [clarification]
    from eidolon_cli.organization_evidence import project_evidence
    wire = project_evidence(context)
    assert wire['evidenceBodies'][wire['objectiveClarifications'][0]['response']['text']['bodySha256']] == answer
    # A new explicit owner scope amendment preserves history without silently
    # promoting old-round answers to current acceptance criteria.
    store.fail(accept, 'Scope needs changing')
    store.resolve(accept['id'], 'amend_scope', 'Now prepare a public audience brief', idempotency_key='amend')
    replanned = store.claim_next()
    context = store.context(replanned)
    assert context['objectiveClarifications'][0]['historical'] is True
    assert context['objectiveClarifications'][0]['round'] == 0
    assert context['objective']['round'] == 1
    assert context['objective']['description'] == 'Now prepare a public audience brief'
    assert context['objective']['acceptanceCriteria'] == ['Now prepare a public audience brief']
    ask(store, replanned)
    advisor = store.claim_next()
    assert advisor['type'] == 'request.question'
    nested = ask(store, advisor, team='owner-only')
    assert store.claim_next() is None
    store.respond(nested['id'], 'Public readers, with no internal details.', idempotency_key='nested-answer')
    advisor = store.claim_next()
    store.finish(advisor, {'answer': 'Use the public reader scope.'})
    resumed_plan = store.claim_next()
    context = store.context(resumed_plan)
    nested_context = next(row for row in context['objectiveClarifications'] if row['id'] == nested['id'])
    assert nested_context['originRequestId'] == replanned['id']
    assert nested_context['round'] == 1 and nested_context['historical'] is False
    assert context['objective']['acceptanceCriteria'] == ['Now prepare a public audience brief']
    store.finish(resumed_plan, {'intervention': 'Waiting for scope'})
    other = store.create_objective('Separate brief', idempotency_key='separate')
    next_plan = store.claim_next()
    assert next_plan['objective_id'] == other['id'] != objective['id']
    assert store.context(next_plan)['objectiveClarifications'] == []
