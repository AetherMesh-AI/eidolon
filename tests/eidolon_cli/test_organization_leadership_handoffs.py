"""Explicit objective leadership survives task lifecycle and independent handoffs."""
import json

import pytest

from eidolon_cli.organization_store import OrganizationStore
from tests.organization_package_helpers import claim_after_decomposition
from tests.eidolon_cli.test_organization_management import (
    apply, configure, hire_request, member, settings, staffing_store,
)


@pytest.mark.parametrize(('role', 'stage', 'paused'), [
    (role, stage, paused) for role in ('Manager', 'Executive', 'Both')
    for stage in ('request.plan', 'request.integrate', 'request.accept') for paused in (False, True)
] + [('Manager', 'work.draft', False), ('Split', 'work.draft', False), ('Split', 'work.draft', True)])
def test_objective_leadership_handoff_without_open_tasks_survives_restart(tmp_path, stage, role, paused):
    roster = [member('lead-one', role='Executive', capabilities=['request.accept']),
              member('lead-two', role='Executive', capabilities=['request.accept']),
              member('team-lead', role='Manager', manager_id='lead-one',
                     capabilities=['request.plan', 'request.integrate']),
              member('new-lead', role='Manager', manager_id='lead-one',
                     capabilities=['request.plan', 'request.integrate']),
              member('source', manager_id='team-lead'),
              member('peer-lead', role='Manager', manager_id='lead-one',
                     capabilities=['request.plan', 'request.integrate']),
              member('peer-worker', manager_id='peer-lead'),
              member('task-lead', role='Manager', manager_id='lead-one',
                     capabilities=['request.plan', 'request.integrate'])]
    store = OrganizationStore(tmp_path / 'organization.db', settings(*roster))
    objective = store.create_objective('Retain leadership across stages', idempotency_key='goal',
                                       executive_id='lead-one', manager_id='team-lead')
    while True:
        with store._connect() as conn:
            queued = conn.execute("SELECT type FROM requests WHERE status='queued' ORDER BY created,id LIMIT 1").fetchone()
        if queued['type'] == stage:
            with store._connect() as conn:
                hiring = conn.execute("SELECT 1 FROM requests WHERE type='request.hire' AND status='queued'").fetchone()
            if stage == 'work.draft' and hiring:
                hire = store.claim_next()
                assert hire['type'] == 'request.hire' and store.finish(hire, {})
                continue
            break
        claim = store.claim_next()
        if claim['type'] == 'request.plan':
            result = {'tasks': [{'title': 'Draft', 'description': 'A scoped result', 'type': 'work.draft',
                                 'team': 'red', 'agentId': 'source'}]}
            if stage == 'work.draft':
                result['tasks'].append({'title': 'Peer task', 'description': 'Dependent delegated work',
                                        'type': 'work.draft', 'team': 'red', 'agentId': 'peer-worker', 'dependsOn': [0]})
                result['workers'] = 2
        elif claim['type'] == 'request.hire':
            result = {}
        elif claim['type'] == 'request.review':
            result = {'approved': True, 'summary': 'Checked the scoped result',
                      'evidenceIds': [item['id'] for item in store.context(claim)['evidence']]}
        else:
            result = {'summary': 'Prepared scoped result', 'deliverable': 'The retained scoped result.',
                      'memory': {'facts': ['Retain this completed work.']}}
        assert store.finish(claim, result)
    task_ids = []
    if stage == 'work.draft':
        task_ids = [task['id'] for task in store.snapshot()['tasks'] if task['assignedAgentId'] == 'source']
        roster = [{**entry, 'manager_id': 'task-lead' if role == 'Split' else 'new-lead'}
                  if entry['id'] == 'source' else entry for entry in roster]
    else:
        assert not [task for task in store.snapshot()['tasks'] if task['status'] not in {'completed', 'cancelled'}]
    question = None
    if paused:
        stage_claim = store.claim_next()
        assert store.finish(stage_claim, {'requests': [{'type': 'request.question', 'requestedOutcome': 'Which audience?'}]})
        assert store.claim_next() is None
        question = next(row for row in store.snapshot()['requests'] if row['parentRequestId'] == stage_claim['id'])
    source, target = ('team-lead', 'new-lead') if role in {'Manager', 'Both', 'Split'} else ('lead-one', 'lead-two')
    before = next(agent for agent in store.snapshot()['agents'] if agent['id'] == source)
    if role == 'Executive':
        roster = [{**entry, 'manager_id': target} if entry['id'] == 'team-lead' else entry for entry in roster]
    roster = [{**entry, 'enabled': False} if entry['id'] == source else entry for entry in roster]
    transfers = [{'fromAgentId': source, 'toAgentId': target,
                  'taskIds': task_ids, 'objectiveIds': [objective['id']], 'includeMemory': True}]
    if role == 'Both':
        roster = [{**entry, 'manager_id': 'lead-two'} if entry['id'] == 'new-lead' else entry for entry in roster]
        transfers.append({'fromAgentId': 'lead-one', 'toAgentId': 'lead-two', 'objectiveIds': [objective['id']]})
        if paused:
            transfers.reverse()  # Final hierarchy is validated atomically in either order.
    if role == 'Split':
        transfers[0]['taskIds'] = []
        transfers.append({'fromAgentId': source, 'toAgentId': 'task-lead', 'taskIds': task_ids})
        if paused:
            transfers.reverse()
    configure(store, roster=roster, transfers=transfers)
    store = OrganizationStore(store.path)
    if question:
        assert store.respond(question['id'], 'Engineering', idempotency_key='handoff-answer')
    with store._connect() as conn:
        assignment = conn.execute('SELECT * FROM objective_assignments WHERE objective_id=?', (objective['id'],)).fetchone()
        assert assignment['manager_id' if role in {'Manager', 'Both', 'Split'} else 'executive_id'] == target
        audits = conn.execute("SELECT after_state FROM organization_management_audit WHERE kind='transfer'").fetchall()
        assert any(json.loads(audit[0])['objectiveIds'] == [objective['id']] for audit in audits)
    after = next(agent for agent in store.snapshot()['agents'] if agent['id'] == source)
    assert after['identityId'] == before['identityId']
    assert after['context'] == before['context']
    claim = store.claim_next()
    assert claim['type'] == stage
    expected = target if (role == 'Manager' and stage != 'request.accept') or (role == 'Executive' and stage == 'request.accept') else ('lead-one' if stage == 'request.accept' else 'team-lead')
    if role == 'Both':
        expected = 'lead-two' if stage == 'request.accept' else 'new-lead'
    if stage == 'work.draft':
        expected = 'source'
        peer = next(task for task in store.snapshot()['tasks'] if task['assignedAgentId'] == 'peer-worker')
        assert peer['managingAgentId'] == 'peer-lead' and peer['status'] != 'completed'
    assert claim['agent_id'] == expected
    if question:
        assert claim['id'] == stage_claim['id']
        assert store.context(claim)['requestResponses'][0]['response']['text'] == 'Engineering'
        assert not store.finish(stage_claim, {'summary': 'Stale result', 'deliverable': 'Do not commit.'})


@pytest.mark.parametrize('invalid', ['unowned', 'duplicate', 'worker', 'missing', 'outside', 'routes', 'cancelled', 'executive-scope', 'chain', 'project-scope'])
def test_objective_handoff_rejects_unowned_or_out_of_scope_work_atomically(tmp_path, invalid):
    store = staffing_store(tmp_path, managed_teams=())
    roster = [*store.configuration_snapshot()['roster'],
              member('lead-one', role='Executive', capabilities=['request.accept']),
              member('lead-two', role='Executive', capabilities=['request.accept']),
              member('team-lead', role='Manager', manager_id='lead-one',
                     capabilities=['request.plan', 'request.integrate']),
              member('new-lead', role='Manager', manager_id='lead-one',
                     capabilities=['request.plan', 'request.integrate'])]
    if invalid == 'project-scope':
        seed = settings(*roster, read_roots=[str(tmp_path)],
                        project_grants=[{'id': 'blue-check', 'files': ['root0/example.txt'], 'execution': {'root': 'root0'}}],
                        projects=[{'id': 'blue-project', 'root': 'root0', 'recipe': 'blue-check', 'team': 'blue'}])
        store.reload_configuration(seed)
    configure(store, roster=roster)
    goal = store.create_objective('Exact objective handoff', idempotency_key='goal',
                                  executive_id='lead-one', manager_id='team-lead',
                                  project_ids=['blue-project'] if invalid == 'project-scope' else None)
    source, target = ('source', 'source-two') if invalid == 'worker' else ('team-lead', 'new-lead')
    if invalid == 'worker':
        configure(store, roster=[*roster, member('source-two')])
    transfer = {'fromAgentId': source, 'toAgentId': target, 'objectiveIds': [goal['id']]}
    if invalid == 'unowned':
        transfer['fromAgentId'], transfer['toAgentId'] = 'new-lead', 'team-lead'
    if invalid == 'duplicate':
        transfer['objectiveIds'] *= 2
    if invalid == 'missing':
        transfer['objectiveIds'] = ['missing-objective']
    if invalid == 'routes':
        configure(store, roster=[{**entry, 'capabilities': ['request.hire']} if entry['id'] == 'new-lead' else entry for entry in roster])
    if invalid == 'cancelled':
        store.cancel(goal['id'])
    transfers = [transfer]
    if invalid in {'executive-scope', 'chain'}:
        changed = [{**entry, 'team': 'blue'} if entry['id'] in {'lead-one', 'lead-two'} else
                   {**entry, 'manager_id': 'lead-two'} if entry['id'] == 'new-lead' else entry for entry in roster]
        configure(store, roster=changed)
        if invalid == 'chain':
            transfers.append({'fromAgentId': 'new-lead', 'toAgentId': 'team-lead', 'objectiveIds': [goal['id']]})
        proposal = {'transfers': transfers}
        request = hire_request(store, proposal)
    if invalid == 'outside':
        # The staffing actor can manage red members, but not this blue objective's task scope.
        assert store.finish(claim_after_decomposition(store), {'tasks': [
            {'title': 'Blue work', 'description': 'Outside scope', 'type': 'work.draft', 'team': 'blue'}]})
        proposal = {'transfers': [transfer]}
        request = hire_request(store, proposal)
    if invalid == 'project-scope':
        proposal = {'transfers': transfers}
        request = hire_request(store, proposal)
    before, generation = store.configuration_snapshot(), store._policy_generation
    with pytest.raises(ValueError):
        if invalid in {'outside', 'executive-scope', 'chain', 'project-scope'}:
            apply(store, request, proposal)
        else:
            configure(store, transfers=[transfer])
    assert store.configuration_snapshot() == before and store._policy_generation == generation
    with store._connect() as conn:
        assert conn.execute('SELECT manager_id FROM objective_assignments WHERE objective_id=?', (goal['id'],)).fetchone()[0] == 'team-lead'
        assert not conn.execute("SELECT 1 FROM organization_management_audit WHERE kind='transfer'").fetchone()


def test_objective_handoff_preserves_unselected_peer_question_continuation(tmp_path):
    roster = [member('team-lead', role='Manager',
                     capabilities=['request.plan', 'request.integrate', 'request.question'],
                     authority=['answer.question']),
              member('new-lead', role='Manager', capabilities=['request.plan', 'request.integrate']),
              member('source', manager_id='team-lead')]
    store = OrganizationStore(tmp_path / 'organization.db', settings(*roster))
    goal = store.create_objective('Keep distinct responsibilities', idempotency_key='goal', manager_id='team-lead')
    assert store.finish(claim_after_decomposition(store), {'tasks': [
        {'title': 'Draft', 'description': 'Needs a peer answer', 'team': 'red', 'type': 'work.draft', 'agentId': 'source'}]})
    assert store.finish(store.claim_next(), {})  # Activate the configured worker.
    work = store.claim_next()
    assert store.finish(work, {'requests': [{'type': 'request.question', 'requestedOutcome': 'Which audience?'}]})
    question = store.claim_next()
    assert question['agent_id'] == 'team-lead'
    assert store.finish(question, {'requests': [{'type': 'request.question', 'team': 'blue',
                                                'requestedOutcome': 'Which engineering audience?'}]})
    assert store.claim_next() is None
    nested = next(row for row in store.snapshot()['requests'] if row['parentRequestId'] == question['id'])
    configure(store, transfers=[{'fromAgentId': 'team-lead', 'toAgentId': 'new-lead',
                                 'objectiveIds': [goal['id']]}])
    store = OrganizationStore(store.path)
    assert store.respond(nested['id'], 'Engineering leads', idempotency_key='peer-answer')
    resumed = store.claim_next()
    assert resumed['id'] == question['id'] and resumed['agent_id'] == 'team-lead'
    assert store.finish(resumed, {'answer': 'Engineering leads', 'decision': 'answered'})
    resumed_work = store.claim_next()
    assert resumed_work['id'] == work['id'] and resumed_work['agent_id'] == 'source'
    with store._connect() as conn:
        assert conn.execute('SELECT manager_id FROM task_assignments WHERE task_id=?', (work['task_id'],)).fetchone()[0] == 'team-lead'
        assert conn.execute('SELECT manager_id FROM objective_assignments WHERE objective_id=?', (goal['id'],)).fetchone()[0] == 'new-lead'
