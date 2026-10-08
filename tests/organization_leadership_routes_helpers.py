"""Shared real-ledger leadership routes setup and invariants."""
import sqlite3
import time
from eidolon_cli.organization_store import OrganizationStore, _SCHEMA
from tests.organization_management_helpers import configure, member, settings


def _roster(work_team='red'):
    return [member('lead', role='Executive', capabilities=['request.accept']),
            member('planner', role='Manager', manager_id='lead',
                   capabilities=['request.plan', 'request.integrate']),
            member('task-lead', role='Manager', team=work_team, capabilities=['request.plan', 'request.integrate']),
            member('writer', team=work_team, manager_id='task-lead'),
            member('staffer', role='Manager', capabilities=['request.hire'],
                   authority=['staff.manage'], managed_teams=['blue'])]


def _legacy_store(tmp_path, roster):
    path = tmp_path / 'legacy.db'
    with sqlite3.connect(path) as conn:
        conn.executescript(_SCHEMA)
        conn.execute("INSERT INTO objectives VALUES ('old','key','digest','Old brief','Old scope',3,?,0)", (time.time(),))
        conn.execute("INSERT INTO tasks(id,objective_id,title,description,type,team,priority,status,dependencies,result) "
                     "VALUES ('task','old','Brief','Scope','work.draft','red',3,'completed','[]','Retained legacy brief')")
        conn.execute("INSERT INTO requests(id,objective_id,task_id,type,team,priority,status,created) "
                     "VALUES ('request','old','task','work.draft','red',3,'completed',?)", (time.time(),))
    store = OrganizationStore(path, settings(*roster))
    assert store.snapshot()['objectives'][0]['acceptance']['status'] == 'legacy_completed'
    return store


def _finish_stage(store, claim, *, approved=True):
    context = store.context(claim)
    if claim['type'] == 'request.plan':
        worker = next(row for row in store.configuration_snapshot()['roster'] if row['id'] == 'writer')
        result = {'tasks': [{'title': 'Brief', 'description': 'Write a scoped brief', 'type': 'work.draft',
                             'team': worker['team'], 'agentId': 'writer', 'managerId': 'task-lead'}]}
    elif claim['type'] == 'request.hire':
        result = {}
    elif claim['type'] in {'request.review', 'request.accept'}:
        ids = [row['id'] for row in context['evidence']]
        result = {'approved': approved, 'summary': 'Checked the exact brief', 'evidenceIds': ids}
        if claim['type'] == 'request.accept':
            result.update(conflicts=[], criteriaResults=[{'criterion': criterion, 'satisfied': approved,
                          'evidenceIds': ids, 'reason': 'The brief is sufficient.' if approved else 'Clarify the final brief.'}
                          for criterion in context['objective']['acceptanceCriteria']])
    else:
        result = {'summary': 'Prepared the brief', 'deliverable': 'The complete scoped brief.',
                  'memory': {'facts': ['Retain this authored brief.']}}
    assert store.finish(claim, result)


def _advance_to(store, stage):
    while True:
        with store._connect() as conn:
            target = conn.execute("SELECT * FROM requests WHERE type=? AND status='queued'", (stage,)).fetchone()
        if target:
            return dict(target)
        claim = store.claim_next()
        assert claim is not None
        _finish_stage(store, claim)


def _retained(store):
    with store._connect() as conn:
        return {table: [tuple(row) for row in conn.execute('SELECT * FROM ' + table + ' ORDER BY 1')]
                for table in ('agent_history', 'evidence', 'objective_deliverables', 'objective_acceptances',
                              'objective_assignments', 'task_assignments')}


def _pause(store, *, proposal=None):
    claim = store.claim_next()
    requests = [{'type': 'request.question', 'team': 'red', 'requestedOutcome': 'Who is the audience?'}]
    if proposal:
        requests.insert(0, {'type': 'request.hire', 'team': 'red', 'requestedOutcome': 'Reorganize these leaders.',
                            'managementProposal': proposal})
    assert store.finish(claim, {'requests': requests})
    return claim


def assert_same_identity_team_change_retains_current_and_future_leadership(tmp_path, actor, stage, paused):
    roster = _roster()
    store = OrganizationStore(tmp_path / 'organization.db', settings(*roster))
    goal = store.create_objective('Retain objective leadership', idempotency_key='goal',
                                  manager_id='planner', executive_id='lead')
    target = _advance_to(store, stage)
    if actor == 'owner' and stage == 'request.plan' and not paused:
        # Startup policy changes deliberately leave existing routes fenced.
        # An unrelated audited capacity edit must not silently repair them.
        startup_roster = [{**row, 'team': 'green'} if row['id'] == 'planner' else row for row in roster]
        store.reload_configuration(settings(*startup_roster))
        configure(store, max_inflight=1)
        with store._connect() as conn:
            assert conn.execute('SELECT team FROM requests WHERE id=?', (target['id'],)).fetchone()[0] == 'red'
        configure(store, roster=roster)
    changed = [{**row, 'team': 'blue'} if row['id'] in {'planner', 'lead'} else row for row in roster]
    proposal = {'members': [row for row in changed if row['id'] in {'planner', 'lead'}]}
    old_claim = _pause(store, proposal=proposal if actor == 'agent' else None) if paused else None
    if paused and actor == 'owner':
        assert store.claim_next() is None
    before = _retained(store)
    identities = {row['id']: (row['identityId'], row['context']) for row in store.snapshot()['agents']
                  if row['id'] in {'planner', 'lead'}}
    if actor == 'owner':
        configure(store, roster=changed)
    else:
        hire = store.claim_next()
        assert hire['type'] == 'request.hire' and hire['agent_id'] == 'staffer'
        assert store.finish(hire, {})
    after = _retained(store)
    assert all(set(rows).issubset(after[table]) for table, rows in before.items())
    assert {row['id']: (row['identityId'], row['context']) for row in store.snapshot()['agents']
            if row['id'] in identities} == identities
    store = OrganizationStore(store.path)
    if paused:
        assert store.claim_next() is None
        question = next(row for row in store.snapshot()['requests'] if row['type'] == 'request.question')
        assert question['team'] == 'red' and question['requesterId'] == old_claim['agent_id']
        assert store.respond(question['id'], 'Engineering', idempotency_key='answer')
    claim = store.claim_next()
    assert claim and claim['id'] == target['id'] and claim['team'] == 'blue'
    assert claim['agent_id'] == ('lead' if stage == 'request.accept' else 'planner')
    if paused:
        assert any(row['response']['text'] == 'Engineering' for row in store.context(claim)['requestResponses'])
        assert not store.finish(old_claim, {'summary': 'Stale', 'deliverable': 'Do not accept.'})
    # Rejection exercises the future plan/integrate/accept routes as well as the
    # immediately pending route, with prior authors and team-scoped work intact.
    rejected = False
    while claim:
        reject = claim['type'] == 'request.accept' and not rejected
        if claim['type'] in {'request.plan', 'request.integrate', 'request.accept'}:
            assert claim['team'] == 'blue'
        _finish_stage(store, claim, approved=not reject)
        rejected |= reject
        store = OrganizationStore(store.path)
        claim = store.claim_next()
    objective = next(row for row in store.snapshot()['objectives'] if row['id'] == goal['id'])
    assert objective['status'] == 'completed' and rejected
    assert all(task['team'] == 'red' and task['managingAgentId'] == 'task-lead' for task in store.snapshot()['tasks'])
    # Final acceptance releases route obligations without rewriting past work.
    configure(store, roster=[{**row, 'capabilities': []} if row['id'] in identities else row for row in changed])
    after = _retained(store)
    assert all(set(rows).issubset(after[table]) for table, rows in before.items())
