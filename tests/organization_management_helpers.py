"""Shared real-ledger management, staffing and transfer setup."""
import json
import time
from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_store import OrganizationStore
from tests.organization_package_helpers import claim_after_decomposition


def member(ident, **values):
    return {'id': ident, 'name': ident.title(), 'team': 'red', 'capabilities': ['work.draft'], **values}


def settings(*members, **values):
    return OrganizationSettings.from_config({'organization': {'roster': list(members), **values}})


def configure(store, **values):
    return store.configure_organization(values, expected_generation=store._policy_generation,
                                        idempotency_key=str(time.time_ns()))


def staffing_store(tmp_path, *, authority=True, managed_teams=(), **values):
    leader = member('staffer', role='Manager', capabilities=['request.hire'],
                    authority=['staff.manage'] if authority else [], managed_teams=list(managed_teams),
                    responsibilities=['Own staffing everywhere; this text confers no authority.'])
    return OrganizationStore(tmp_path / 'organization.db', settings(leader, member('source'), **values))


def hire_request(store, proposal, actor='staffer', team='red', owner=False):
    objective = store.create_objective('Scoped organization work', idempotency_key=str(time.time_ns()),
                                       delivery_mode='managed_artifact')
    with store._write() as conn:
        request_id = store._request(conn, objective['id'], 'request.hire', team, 3,
                                    payload={'managementProposal': proposal})
        store._stamp_claim_policy(conn, request_id)
        conn.execute('UPDATE requests SET status=?,agent_id=?,token=?,lease=? WHERE id=?',
                     ('pending_intervention' if owner else 'running', actor, 'test-token', time.time() + 60, request_id))
        return dict(conn.execute('SELECT * FROM requests WHERE id=?', (request_id,)).fetchone())


def apply(store, request, proposal, actor='staffer'):
    with store._write() as conn:
        return store.apply_management(conn, request, proposal, actor)


def open_task(store, source='source'):
    objective = store.create_objective('Transfer scoped work', idempotency_key=str(time.time_ns()))
    claim = claim_after_decomposition(store)
    assert store.finish(claim, {'tasks': [{'title': 'Draft', 'description': 'Write this', 'team': 'red',
                                         'type': 'work.draft', 'agentId': source}], 'workers': 1})
    task_id = next(task['id'] for task in store.snapshot()['tasks'] if task['objectiveId'] == objective['id'])
    with store._write() as conn:
        conn.execute("UPDATE agent_context SET memory=?,revision=3 WHERE agent_id=?",
                     (json.dumps({'facts': ['Scoped source fact'], 'decisions': [], 'lessons': [], 'openQuestions': []}), source))
        request = conn.execute("SELECT * FROM requests WHERE task_id=? AND type='work.draft'", (task_id,)).fetchone()
        conn.execute('INSERT INTO request_continuations VALUES (?,?)', (request['id'], source))
        conn.execute("UPDATE requests SET status='waiting_response',agent_id=? WHERE id=?", (source, request['id']))
        historical = store._request(conn, objective['id'], 'work.draft', 'red', 3, task_id)
        conn.execute("UPDATE requests SET status='completed',agent_id=? WHERE id=?", (source, historical))
        conn.execute('INSERT INTO agent_history VALUES (?,?,?,?,?,?,?,?)',
                     (historical, source, objective['id'], task_id, 'work.draft', 'Retained original history', '[]', time.time()))
    return task_id, request['id'], historical


def leadership_goal(tmp_path):
    roster = [member('lead-one', role='Executive', capabilities=['request.accept']),
              member('lead-two', role='Executive', capabilities=['request.accept']),
              member('team-lead', role='Manager', manager_id='lead-one',
                     capabilities=['request.plan', 'request.integrate']),
              member('source', manager_id='team-lead')]
    store = OrganizationStore(tmp_path / 'organization.db', settings(*roster))
    objective = store.create_objective('Explicit leadership handoff', idempotency_key='goal',
                                       executive_id='lead-one', manager_id='team-lead')
    plan = store.claim_next()
    store.finish(plan, {'tasks': [{'title': str(index), 'description': 'Scoped draft', 'type': 'work.draft',
                                  'team': 'red', 'agentId': 'source', 'managerId': 'team-lead'} for index in range(2)],
                        'workers': 1})
    tasks = [task['id'] for task in store.snapshot()['tasks']]
    return store, roster, objective['id'], tasks, plan['id']


def paused_unpinned_work(tmp_path):
    store = OrganizationStore(tmp_path / 'organization.db', settings(member('source', team='general')))
    objective = store.create_objective('Unpinned work and follow-up', idempotency_key='unpinned')
    plan = claim_after_decomposition(store)
    assert store.finish(plan, {'tasks': [
        {'title': 'Prior work', 'description': 'Write the first brief', 'type': 'work.draft'},
        {'title': 'Follow-up', 'description': 'Ask for the audience and continue', 'type': 'work.draft', 'dependsOn': [0]},
    ]})
    hire = store.claim_next()
    assert hire['type'] == 'request.hire'
    assert store.finish(hire, {})
    first = store.claim_next()
    assert first['agent_id'] == 'source'
    assert store.finish(first, {'summary': 'Original authored work', 'deliverable': 'Original source result.',
                                'memory': {'facts': ['Retain the first brief.']}})
    review = store.claim_next()
    assert review['type'] == 'request.review'
    assert store.finish(review, {'approved': True, 'summary': 'Checked the first brief',
                                 'evidenceIds': [item['id'] for item in store.context(review)['evidence']]})
    paused = store.claim_next()
    assert paused['agent_id'] == 'source' and paused['task_id'] != first['task_id']
    assert store.finish(paused, {'requests': [{'type': 'request.question', 'requestedOutcome': 'Who is the audience?'}]})
    assert store.claim_next() is None
    question = next(row for row in store.snapshot()['requests'] if row['parentRequestId'] == paused['id'])
    assert question['status'] == 'pending_intervention'
    with store._connect() as conn:
        assert conn.execute('SELECT agent_id FROM task_assignments WHERE task_id=?', (paused['task_id'],)).fetchone()[0] is None
        assert conn.execute('SELECT agent_id FROM request_continuations WHERE request_id=?', (paused['id'],)).fetchone()[0] == 'source'
    return store, objective, first, paused, question
