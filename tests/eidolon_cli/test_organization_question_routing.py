"""Management answers follow durable assignments without expanding authority."""
from dataclasses import replace
import json

import pytest

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_executor import _parse_output
from eidolon_cli.organization_store import OrganizationStore
from tests.organization_package_helpers import decomposition_result


def question_store(tmp_path, *, manager_authority=True, package_manager='coordinator', **limits):
    roster = [
        {'id': 'chief', 'name': 'Chief', 'role': 'Executive',
         'capabilities': ['request.decompose', 'request.accept', 'request.question'],
         'authority': ['answer.question']},
        *[{'id': name, 'name': name, 'role': 'Manager', 'manager_id': 'chief',
           'capabilities': ['request.plan', 'request.integrate', 'request.question'],
           'authority': ['answer.question'] if manager_authority or name != 'z-manager' else []}
          for name in ('a-unrelated', 'coordinator', 'z-manager')],
        {'id': 'writer', 'name': 'Writer', 'manager_id': 'z-manager', 'capabilities': ['work.draft']},
    ]
    settings = OrganizationSettings.from_config({'organization': {'roster': roster, **limits}})
    store = OrganizationStore(tmp_path / 'state.db', settings)
    objective = store.create_objective('Write a board brief', idempotency_key='goal',
                                       manager_id='coordinator', executive_id='chief')
    executive = store.claim_next()
    packages = decomposition_result(store.context(executive))
    packages['workPackages'][0]['managerId'] = package_manager
    assert store.finish(executive, packages)
    plan = store.claim_next()
    return store, objective, plan


def work_claim(store, plan):
    assert store.finish(plan, {'tasks': [{'title': 'Brief', 'description': 'Prepare the board brief.',
                                         'type': 'work.draft', 'agentId': 'writer', 'managerId': 'z-manager'}]})
    claim = store.claim_next()
    while claim and claim['type'] == 'request.hire':
        store.finish(claim, {})
        claim = store.claim_next()
    assert claim['agent_id'] == 'writer'
    return claim


def ask(store, parent):
    assert store.finish(parent, {'requests': [{'type': 'request.question',
                                             'requestedOutcome': 'Which audience? Preserve this exact question.'}]})
    return next(row for row in store.snapshot()['requests'] if row['parentRequestId'] == parent['id'])


def request_view(store, identifier):
    return next(row for row in store.snapshot()['requests'] if row['id'] == identifier)


@pytest.mark.parametrize('stage', ['work', 'plan'])
def test_question_context_retains_exact_assignment_without_copying_requester_memory(tmp_path, stage):
    store, _, plan = question_store(tmp_path, package_manager='z-manager')
    parent = work_claim(store, plan) if stage == 'work' else plan
    private_fact = 'Private requester memory unrelated to the delegated assignment.'
    with store._write() as conn:
        conn.execute('UPDATE agent_context SET memory=? WHERE agent_id=?',
                     (json.dumps({'facts': [private_fact], 'decisions': [], 'lessons': [], 'openQuestions': []}),
                      parent['agent_id']))
    before = store.context(parent)
    assert private_fact in before['agentContext']['memory']['facts']
    assert 'requestOrigin' not in before
    ask(store, parent)
    store = OrganizationStore(store.path)
    answer = store.claim_next()
    context = store.context(answer)
    origin = context['requestOrigin']
    assert origin['requestId'] == parent['id']
    assert origin['requestType'] == parent['type']
    assert origin['workPackageId'] == before['workPackage']['id']
    if stage == 'work':
        assert origin['task'] == {key: before['task'][key] for key in
                                  ('id', 'title', 'description', 'type', 'team', 'revision', 'feedback')}
    else:
        assert origin['task'] is None
    assert set(origin) == {'requestId', 'requestType', 'workPackageId', 'task'}
    assert context['task'] is None  # The leader is answering, not taking over work.
    assert private_fact not in json.dumps(context)
    assert context['toolPolicy']['tools'] == []
    assert store.finish(answer, {'answer': 'Use the board audience for this assignment.'})
    resumed = OrganizationStore(store.path).claim_next()
    assert resumed['id'] == parent['id'] and resumed['agent_id'] == parent['agent_id']
    assert 'requestOrigin' not in store.context(resumed)


@pytest.mark.parametrize('mode', ['manager', 'escalate', 'skip', 'package_manager', 'legacy'])
def test_question_follows_assignment_and_keeps_one_request_across_restart(tmp_path, mode):
    store, objective, plan = question_store(tmp_path, manager_authority=mode != 'skip',
        package_manager='z-manager' if mode == 'package_manager' else 'coordinator')
    parent = plan if mode == 'package_manager' else work_claim(store, plan)
    question = ask(store, parent)
    if mode == 'legacy':
        # Simulate a pre-upgrade request, whose schema has no routing record.
        # Its unrelated same-team advisor must remain eligible after migration.
        with store._write() as conn:
            conn.execute('DELETE FROM question_routes WHERE request_id=?', (question['id'],))
    from eidolon_cli.organization_budget import budget_view
    with store._connect() as conn:
        before = budget_view(conn, objective['id'], store.settings)
    store = OrganizationStore(store.path)
    answer = store.claim_next()
    expected = {'skip': 'chief', 'package_manager': 'chief', 'legacy': 'a-unrelated'}.get(mode, 'z-manager')
    assert answer['agent_id'] == expected
    if mode == 'escalate':
        result = _parse_output('{"cannot_answer":"The owning Executive must supply the audience."}',
                               answer['type'], store.context(answer))
        assert store.finish(answer, result)
        assert not store.finish(answer, {'answer': 'Late answer'})
        store = OrganizationStore(store.path)
        next_answer = store.claim_next()
        assert next_answer['id'] == answer['id'] and next_answer['attempts'] == answer['attempts'] + 1
        assert next_answer['agent_id'] == 'chief'
        answer = next_answer
    assert store.finish(answer, {'answer': 'The board, with a technical appendix.'})
    resumed = OrganizationStore(store.path).claim_next()
    assert resumed['id'] == parent['id'] and resumed['agent_id'] == parent['agent_id']
    contract, = store.context(resumed)['requestResponses']
    assert contract['id'] == question['id'] and contract['requesterId'] == parent['agent_id']
    assert contract['requestedOutcome'] == question['requestedOutcome']
    assert contract['response']['responderId'] == answer['agent_id']
    assert contract['response']['text'] == 'The board, with a technical appendix.'
    if mode in {'skip', 'escalate'}:
        receipt, = contract['questionRouting']['receipts']
        assert receipt['agentId'] == 'z-manager'
        assert receipt['outcome'] == ('ineligible' if mode == 'skip' else 'cannot_answer')
        assert receipt['text']
    with store._connect() as conn:
        assert budget_view(conn, objective['id'], store.settings)['deadlineAt'] == before['deadlineAt']


@pytest.mark.parametrize('case', ['denied', 'uncertain', 'nested', 'exhausted', 'cancelled',
                                  'reassigned', 'revoked', 'no_authority', 'executive_cannot_answer',
                                  'transport_failure', 'expired', 'package_reassigned', 'objective_reassigned',
                                  'stage_budget', 'deadline'])
def test_unresolved_questions_stay_owner_visible_and_never_refresh_into_a_loop(tmp_path, case):
    store, objective, plan = question_store(tmp_path, max_attempts=1 if case == 'exhausted' else 2)
    parent = work_claim(store, plan)
    question = ask(store, parent)
    original_settings = store.settings
    if case == 'no_authority':
        staff = tuple(replace(member, authority=()) for member in store.settings.roster)
        store.reload_configuration(replace(store.settings, roster=staff))
        assert store.claim_next() is None
    else:
        answer = store.claim_next()
        if case == 'cancelled':
            store.cancel(objective['id'])
            assert not store.finish(answer, {'answer': 'Late answer'})
        elif case == 'transport_failure':
            assert store.fail(answer, 'Provider outcome unknown', retryable=True)
        elif case == 'expired':
            with store._write() as conn:
                conn.execute('UPDATE requests SET lease=0 WHERE id=?', (answer['id'],))
            assert store.recover_expired() == 1
            assert not store.finish(answer, {'answer': 'Late answer'})
        elif case == 'deadline':
            with store._write() as conn:
                conn.execute('UPDATE objective_budgets SET deadline=0 WHERE objective_id=?', (objective['id'],))
            assert not store.finish(answer, {'cannot_answer': 'Executive needs to answer.'})
        elif case == 'reassigned':
            # Fence authoritative ownership changes even without a policy bump.
            with store._write() as conn:
                conn.execute('UPDATE task_assignments SET manager_id=? WHERE task_id=?',
                             ('a-unrelated', parent['task_id']))
            assert store.finish(answer, {'answer': 'Stale authority'})
        elif case == 'package_reassigned':
            with store._write() as conn:
                conn.execute('UPDATE manager_work_packages SET manager_id=? WHERE plan_request_id=?',
                             ('a-unrelated', plan['id']))
            assert store.finish(answer, {'answer': 'Stale package ownership'})
        elif case == 'objective_reassigned':
            with store._write() as conn:
                conn.execute('UPDATE objective_assignments SET manager_id=? WHERE objective_id=?',
                             ('a-unrelated', objective['id']))
            assert store.finish(answer, {'answer': 'Stale objective ownership'})
        elif case == 'revoked':
            roster = tuple(replace(member, authority=()) if member.id == 'z-manager' else member
                           for member in store.settings.roster)
            store.reload_configuration(replace(store.settings, roster=roster))
            assert not store.finish(answer, {'answer': 'Revoked authority'})
        else:
            if case == 'stage_budget':
                with store._write() as conn:
                    conn.execute('UPDATE objective_control SET max_stages=(SELECT count(*) FROM objective_usage WHERE objective_id=?) WHERE objective_id=?',
                                 (objective['id'], objective['id']))
            result = {'denied': {'answer': 'I explicitly refuse this request.', 'decision': 'denied'},
                      'uncertain': {'intervention': 'The evidence is uncertain; owner must decide.'},
                      'nested': {'requests': [{'type': 'request.question', 'requestedOutcome': 'Ask again?'}]},
                      'exhausted': {'cannot_answer': 'Executive needs to answer.'},
                      'stage_budget': {'cannot_answer': 'Executive needs to answer.'},
                      'executive_cannot_answer': {'cannot_answer': 'Executive needs to answer.'}}[case]
            assert store.finish(answer, result)
            if case == 'executive_cannot_answer':
                executive = store.claim_next()
                assert executive['agent_id'] == 'chief' and executive['id'] == question['id']
                assert store.finish(executive, {'cannot_answer': 'Only the owner knows.'})
            assert store.claim_next() is None
    store = OrganizationStore(store.path)
    if case in {'no_authority', 'revoked'}:
        store.reload_configuration(original_settings)
    store.refresh_unhandled_requests()
    assert store.claim_next() is None
    current = request_view(store, question['id'])
    assert current['status'] == ('cancelled' if case == 'cancelled' else 'pending_intervention')
    assert current['response'] is None
    assert current['requesterId'] == 'writer' and current['parentRequestId'] == parent['id']
    assert request_view(store, parent['id'])['status'] == ('cancelled' if case == 'cancelled' else 'waiting_response')
    if case == 'deadline':
        assert current['allowedResolutions'] == []  # Expired objectives cannot authorize another turn.
    elif case != 'cancelled':
        assert current['allowedResolutions'][0]['action'] == 'answer_request'
    if case == 'denied':
        denial, = current['questionRouting']['receipts']
        assert denial['agentId'] == 'z-manager' and denial['outcome'] == 'denied'
        assert denial['text'] == 'I explicitly refuse this request.'
        assert store.respond(question['id'], 'Owner confirms the board audience.', idempotency_key='owner-after-denial')
        assert not store.respond(question['id'], 'Owner confirms the board audience.', idempotency_key='owner-after-denial')
        resumed = OrganizationStore(store.path).claim_next()
        assert resumed['id'] == parent['id'] and resumed['agent_id'] == 'writer'
        response, = store.context(resumed)['requestResponses']
        assert response['response']['responderId'] == 'owner'
        assert response['response']['text'] == 'Owner confirms the board audience.'
        assert response['questionRouting']['receipts'] == [denial]


@pytest.mark.parametrize('leader', ['z-manager', 'chief'])
@pytest.mark.parametrize('restriction', ['team', 'capability'])
def test_management_relationship_never_grants_team_or_question_capability(tmp_path, leader, restriction):
    store, _, plan = question_store(tmp_path)
    parent = work_claim(store, plan)
    question = ask(store, parent)
    roster = []
    for member in store.settings.roster:
        if member.id == leader:
            changes = ({'team': 'other-team'} if restriction == 'team' else
                       {'capabilities': tuple(kind for kind in member.capabilities if kind != 'request.question')})
            member = replace(member, **changes)
        roster.append(member)
    store.reload_configuration(replace(store.settings, roster=tuple(roster)))
    selected = store.claim_next()
    if leader == 'z-manager':
        assert selected['agent_id'] == 'chief' and selected['id'] == question['id']
        assert store.finish(selected, {'answer': 'The board.'})
    else:
        assert selected['agent_id'] == 'z-manager'
        assert store.finish(selected, {'cannot_answer': 'The Executive needs to answer.'})
        assert store.claim_next() is None
        assert request_view(store, question['id'])['status'] == 'pending_intervention'
    receipts = request_view(store, question['id'])['questionRouting']['receipts']
    excluded, = [receipt for receipt in receipts if receipt['agentId'] == leader]
    assert excluded['outcome'] == 'ineligible' and excluded['text']


def test_busy_assigned_manager_keeps_priority_over_idle_executive(tmp_path):
    store, _, plan = question_store(tmp_path, max_inflight=3)
    parent = work_claim(store, plan)
    # A second real objective occupies the Manager while the first worker asks.
    store.create_objective('Another brief', idempotency_key='busy',
                           manager_id='z-manager', executive_id='chief')
    executive = store.claim_next()
    assert store.finish(executive, decomposition_result(store.context(executive)))
    busy = store.claim_next()
    assert busy['agent_id'] == 'z-manager'
    question = ask(store, parent)
    assert store.claim_next() is None
    assert request_view(store, question['id'])['status'] == 'queued'
    store.fail(busy, 'Separate work needs owner input')
    answer = store.claim_next()
    assert answer['id'] == question['id'] and answer['agent_id'] == 'z-manager'
