"""Explicit objective leadership survives task lifecycle and independent handoffs."""
import json

import pytest

from eidolon_cli.organization_store import OrganizationStore
from tests.eidolon_cli.test_organization_management import configure, member, settings


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
