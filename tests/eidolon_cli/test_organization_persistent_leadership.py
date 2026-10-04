"""Persistent leadership and independently bounded assignment/execution contracts."""
import json

import pytest

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_executor import _parse_output, _prompt, OrganizationExecutionError
from eidolon_cli.organization_roster import configured_workers, select_staff_activation
from eidolon_cli.organization_store import OrganizationStore


def settings():
    return OrganizationSettings.from_config({'organization': {'max_workers': 1, 'max_inflight': 1, 'roster': [
        {'id': 'lunavale', 'name': 'Lunavale', 'role': 'Executive', 'team': 'lunavale',
         'purpose': 'Build and maintain Lunavale'},
        {'id': 'services', 'name': 'Account services', 'role': 'Manager', 'manager_id': 'lunavale', 'team': 'accounts'},
        {'id': 'runtime', 'name': 'Runtime', 'role': 'Manager', 'manager_id': 'lunavale', 'team': 'runtime'},
        {'id': 'account-specialist', 'name': 'Accounts specialist', 'manager_id': 'services', 'team': 'accounts',
         'capabilities': ['work.draft'], 'responsibilities': ['Own account service documentation']},
        {'id': 'runtime-specialist', 'name': 'Runtime specialist', 'manager_id': 'runtime', 'team': 'runtime',
         'capabilities': ['work.analyze'], 'responsibilities': ['Maintain runtime decisions']},
    ]}})


def finish_goal(store, key):
    objective = store.create_objective('Lunavale services', idempotency_key=key,
                                       executive_id='lunavale', manager_id='services')
    plan = store.claim_next()
    assert plan['agent_id'] == 'services'
    assert store.context(plan)['agent']['role'] == 'Manager'
    assert store.finish(plan, {'tasks': [
        {'title': 'Runtime requirements', 'description': 'Analyze runtime tradeoffs', 'type': 'work.analyze',
         'team': 'runtime', 'agentId': 'runtime-specialist', 'managerId': 'runtime'},
        {'title': 'Service contract', 'description': 'Write the account contract', 'type': 'work.draft',
         'team': 'accounts', 'agentId': 'account-specialist', 'managerId': 'services', 'dependsOn': [0]},
    ], 'workers': 2, 'memory': {'decisions': ['Runtime owns concurrency, services owns identity.']}})
    assigned = store.snapshot()['tasks']
    assert {task['assignedAgentId'] for task in assigned} == {'runtime-specialist', 'account-specialist'}
    assert all(task['ownerId'] == task['assignedAgentId'] for task in assigned)
    seen = []
    while (claim := store.claim_next()) is not None:
        assert store.claim_next() is None  # One execution slot, several persistent staff.
        context = store.context(claim)
        seen.append((claim['type'], claim['agent_id']))
        if claim['type'] == 'request.hire':
            result = {'summary': 'Existing specialists activated'}
        elif claim['type'] in {'request.review', 'request.accept'}:
            ids = [row['id'] for row in context['evidence']]
            result = {'approved': True, 'summary': 'Checked exact retained evidence', 'evidenceIds': ids}
            if claim['type'] == 'request.accept':
                assert claim['agent_id'] == 'lunavale'
                result.update(conflicts=[], criteriaResults=[{'criterion': value, 'satisfied': True,
                              'evidenceIds': ids, 'reason': 'The coherent evidence meets this requirement.'}
                              for value in context['objective']['acceptanceCriteria']])
        else:
            result = {'summary': 'Prepared scoped result', 'deliverable': 'A supported result using supplied context.',
                      'memory': {'facts': [f"{claim['agent_id']} retains its own scoped result."]}}
            if claim['type'] == 'request.integrate':
                assert claim['agent_id'] == 'services'
        assert store.finish(claim, result)
    assert store.snapshot()['objectives'][0]['status'] == 'completed'
    return objective, seen


def test_existing_leaders_and_cross_manager_workers_reuse_identity_context_after_restart(tmp_path):
    store = OrganizationStore(tmp_path / 'organization.db', settings())
    first, first_stages = finish_goal(store, 'first')
    before = {agent['id']: agent for agent in store.snapshot()['agents']}
    assert first['ownerId'] == 'lunavale' and first['managerId'] == 'services'
    assert ('work.analyze', 'runtime-specialist') in first_stages
    assert ('work.draft', 'account-specialist') in first_stages
    assert before['runtime-specialist']['context']['memory']['facts'] == ['runtime-specialist retains its own scoped result.']
    assert before['account-specialist']['context']['memory']['facts'] == ['account-specialist retains its own scoped result.']
    assert before['runtime']['managerId'] == 'lunavale'
    store = OrganizationStore(store.path)
    second, second_stages = finish_goal(store, 'second')
    after = {agent['id']: agent for agent in store.snapshot()['agents']}
    assert set(before) == set(after)
    assert all(before[key]['identityId'] == after[key]['identityId'] for key in before)
    assert ('request.hire', 'director') not in second_stages
    assert {row['objectiveId'] for row in after['runtime-specialist']['context']['recentHistory']} == {first['id'], second['id']}
    assert len([row for row in after.values() if row['role'] == 'Worker' and row['lifecycle'] == 'active']) > store.settings.max_inflight


def test_leader_assignment_is_validated_and_bound_to_idempotency(tmp_path):
    store = OrganizationStore(tmp_path / 'organization.db', settings())
    with pytest.raises(ValueError):
        store.create_objective('Goal', idempotency_key='bad', executive_id='lunavale', manager_id='manager')
    assert not store.snapshot()['objectives']
    store.create_objective('Goal', idempotency_key='same', executive_id='lunavale', manager_id='services')
    with pytest.raises(ValueError, match='Idempotency'):
        store.create_objective('Goal', idempotency_key='same', executive_id='lunavale', manager_id='runtime')


def test_roster_hierarchy_and_capacity_are_independent_of_execution_slots():
    config = settings()
    workers = configured_workers(config)
    assert len(workers) > config.max_inflight
    assert set(select_staff_activation(config, [], 2)) == set(workers)
    bad = [{'id': 'loop', 'name': 'Loop', 'role': 'Manager', 'manager_id': 'loop'}]
    with pytest.raises(ValueError, match='leader'):
        OrganizationSettings.from_config({'organization': {'roster': bad}})
    bad = [{'id': 'leader', 'name': 'Leader', 'role': 'Manager', 'tool_grants': ['read_file']}]
    with pytest.raises(ValueError, match='tool-free'):
        OrganizationSettings.from_config({'organization': {'tool_grants': ['read_file'], 'roster': bad}})


def test_executor_preserves_typed_memory_and_exact_assignment_without_gaining_authority():
    own = {'memory': {'facts': ['A scoped fact'], 'decisions': [], 'lessons': [], 'openQuestions': []},
           'recentHistory': [], 'revision': 1, 'updatedAt': None}
    context = {'agent': {'id': 'runtime-specialist', 'role': 'Worker'}, 'agentContext': own,
               'objective': {'title': 'Goal'}, 'toolPolicy': {'tools': [], 'readRoots': []}}
    prompt = _prompt({'type': 'work.analyze'}, context, 'work.analyze')
    assert 'A scoped fact' in prompt and 'runtime-specialist' in prompt
    result = _parse_output(json.dumps({'summary': 'Useful result', 'deliverable': 'Complete result',
                                      'memory': {'lessons': ['Keep the useful observation']}}), 'work.analyze', context)
    assert result['memory']['lessons'] == ['Keep the useful observation']
    with pytest.raises(OrganizationExecutionError, match='memory'):
        _parse_output(json.dumps({'summary': 'Useful result', 'deliverable': 'Complete result',
                                  'memory': {'tool_grants': ['terminal']}}), 'work.analyze', context)
    plan = {'tasks': [{'title': 'Scoped', 'description': 'Task', 'team': 'runtime', 'type': 'work.analyze',
                       'agentId': 'runtime-specialist', 'managerId': 'runtime'}], 'workers': 2}
    parsed = _parse_output(json.dumps(plan), 'request.plan', {})
    assert parsed['tasks'][0]['agentId'] == 'runtime-specialist'
    assert parsed['tasks'][0]['managerId'] == 'runtime'


def test_large_retained_memory_is_a_bounded_prompt_view_not_a_history_rewrite():
    from copy import deepcopy
    values = [str(index) + 'x' * 998 for index in range(24)]
    own = {'memory': {key: list(values) for key in ('facts', 'decisions', 'lessons', 'openQuestions')},
           'recentHistory': [{'summary': 'h' * 2000, 'requestId': str(index)} for index in range(12)],
           'contextSummary': 's' * 2000, 'revision': 24}
    before = deepcopy(own)
    context = {'agent': {'id': 'specialist'}, 'agentContext': own,
               'staffing': [{'id': f'worker-{index}', 'role': 'Worker', 'team': 'general',
                            'capabilities': ['work.draft'], 'responsibilities': ['r' * 500] * 12,
                            'purpose': 'p' * 3000} for index in range(64)],
               'objective': {'description': 'd' * 30000}, 'toolPolicy': {'tools': [], 'readRoots': []}}
    prompt = _prompt({'type': 'work.draft'}, context, 'work.draft')
    payload = json.loads(prompt.split('Submitted context:\n', 1)[1])['context']
    assert payload['agentContext']['workingSetTruncated']
    assert len(payload['staffing']) == 64
    assert own == before
    assert payload['objective'] == context['objective']
