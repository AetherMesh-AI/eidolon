"""Shared real-ledger package handoffs setup and invariants."""
import pytest
from eidolon_cli.organization_store import OrganizationStore
from tests.organization_management_helpers import apply, configure, hire_request, member, settings


def _roster():
    leaders = [member(ident, role='Executive', capabilities=['request.decompose', 'request.accept'])
               for ident in ('lead', 'next-lead')]
    managers = [member(ident, role='Manager', manager_id='lead', team=team,
                       capabilities=['request.plan', 'request.integrate'])
                for ident, team in [('planner', 'red'), ('peer', 'blue'), ('successor', 'green'), ('tasks-red', 'red')]]
    return [*leaders, *managers, member('writer', manager_id='tasks-red'),
            member('staffer', role='Manager', capabilities=['request.hire'], authority=['staff.manage'],
                   managed_teams=['blue', 'green'])]


def _decomposition(project=False):
    return {'workPackages': [
        {'title': 'First brief', 'description': 'Prepare the first brief.', 'managerId': 'planner',
         'criterionIndexes': [0], 'projectIds': ['project'] if project else [], 'dependsOn': [], 'maxTasks': 1},
        {'title': 'Peer brief', 'description': 'Prepare the peer brief.', 'managerId': 'peer',
         'criterionIndexes': [1], 'projectIds': [], 'dependsOn': [], 'maxTasks': 1},
    ]}


def _packages(store):
    with store._connect() as conn:
        return [dict(row) for row in conn.execute('SELECT * FROM manager_work_packages ORDER BY rowid')]


def _finish(store, claim):
    context = store.context(claim)
    if claim['type'] == 'request.plan':
        package = context['workPackage']
        result = {'workPackageId': package['id'], 'managerId': package['managerId'],
                  'tasks': [{'title': 'Scoped brief', 'description': 'Prepare the scoped brief.',
                             'type': 'work.draft', 'team': 'red', 'managerId': 'tasks-red', 'agentId': 'writer'}]}
    elif claim['type'] == 'request.hire':
        result = {}
    elif claim['type'] == 'request.review':
        result = {'approved': True, 'summary': 'Checked the scoped brief.',
                  'evidenceIds': [row['id'] for row in context['evidence']]}
    else:
        assert claim['type'] == 'work.draft'
        result = {'summary': 'Prepared the brief.', 'deliverable': 'A complete scoped brief.'}
    assert store.finish(claim, result)


def _pause(store, claim):
    assert store.finish(claim, {'requests': [
        {'type': 'request.question', 'requestedOutcome': 'Which audience should this brief address?'}]})
    return claim


def _prepared(tmp_path, *, stage='request.plan', project=False, plan_order=None):
    options = {}
    if project:
        options = {'read_roots': [str(tmp_path)],
                   'project_grants': [{'id': 'recipe', 'files': ['root0/brief.txt'], 'execution': {'root': 'root0'}}],
                   'projects': [{'id': 'project', 'root': 'root0', 'recipe': 'recipe', 'team': 'outside'}]}
    store = OrganizationStore(tmp_path / 'organization.db', settings(*_roster(), **options))
    goal = store.create_objective('Two independently managed briefs', idempotency_key='goal',
                                  acceptance_criteria=['Prepare first brief', 'Prepare peer brief'],
                                  executive_id='lead', manager_id='planner', delivery_mode='managed_artifact',
                                  project_ids=['project'] if project else None)
    claim = store.claim_next()
    assert claim['type'] == 'request.decompose' and claim['agent_id'] == 'lead'
    if stage == 'request.decompose':
        _pause(store, claim)
        assert store.claim_next() is None
        return store, goal, claim
    assert store.finish(claim, _decomposition(project))
    if plan_order:
        # Exercise coarse-clock ties and peer-first dispatch without changing
        # the scheduler or relying on randomly generated request IDs.
        with store._write() as conn:
            conn.execute("UPDATE requests SET created=? WHERE type='request.plan'", (claim['created'],))
            if plan_order == 'reversed':
                conn.execute("UPDATE requests SET created=created-1 WHERE type='request.plan' AND team='blue'")
    plans = [store.claim_next(), store.claim_next()]
    assert all(row['type'] == 'request.plan' for row in plans)
    by_manager = {row['agent_id']: row for row in plans}
    assert set(by_manager) == {'planner', 'peer'}
    source = by_manager['planner']
    if stage == 'planned':
        _finish(store, source)
    else:
        _pause(store, source)
    # Leave peer planning paused too, so unrelated leadership changes must
    # preserve its exact route and continuation while scoped work can finish.
    _pause(store, by_manager['peer'])
    for _ in range(20):
        claim = store.claim_next()
        if claim is None:
            break
        _finish(store, claim)
    else:
        pytest.fail('Scoped fixture work did not reach a stable paused state')
    return store, goal, source


def _retained(store):
    with store._connect() as conn:
        return {table: [tuple(row) for row in conn.execute('SELECT * FROM ' + table + ' ORDER BY 1')]
                for table in ('agent_history', 'evidence', 'tasks', 'task_assignments', 'objective_projects')}


def assert_live_package_obligations_and_scopes_fail_atomically_until_objective_terminal(tmp_path, invalid):
    project = invalid.startswith('project-')
    store, goal, _ = _prepared(tmp_path, stage='planned' if invalid == 'planned-capability' else 'request.plan',
                               project=project)
    package = _packages(store)[0]
    roster = store.configuration_snapshot()['roster']
    transfer = {'fromAgentId': 'planner', 'toAgentId': 'successor', 'workPackageIds': [package['id']]}
    transfers, members = [], []
    changes = {
        'executive-capability': ('lead', {'capabilities': ['request.accept']}),
        'package-capability': ('peer', {'capabilities': ['request.integrate']}),
        'planned-capability': ('planner', {'capabilities': ['request.integrate']}),
        'package-disabled': ('peer', {'enabled': False}),
        'reporting-line': ('peer', {'manager_id': 'next-lead'}),
        'destination-capability': ('successor', {'capabilities': ['request.integrate']}),
        'destination-reporting': ('successor', {'manager_id': 'next-lead'}),
        'project-team-scope': ('planner', {'team': 'green'}),
    }
    if invalid in changes:
        ident, values = changes[invalid]
        members = [{**row, **values} for row in roster if row['id'] == ident]
    if invalid not in {'executive-capability', 'package-capability', 'planned-capability', 'package-disabled',
                       'reporting-line', 'project-team-scope'}:
        transfers = [transfer]
    if invalid == 'unowned':
        transfer['fromAgentId'], transfer['toAgentId'] = 'successor', 'planner'
    if invalid == 'duplicate':
        transfer['workPackageIds'] *= 2
    if invalid == 'overlapping':
        transfers.append({'fromAgentId': 'successor', 'toAgentId': 'peer', 'workPackageIds': [package['id']]})
    if invalid == 'missing':
        transfer['workPackageIds'] = ['package_missing']
    if invalid == 'worker':
        members = [member('next-writer', manager_id='tasks-red')]
        transfer['fromAgentId'], transfer['toAgentId'] = 'writer', 'next-writer'
    if invalid == 'cancelled':
        store.cancel(goal['id'])
    if invalid in {'accepted', 'old-round'}:
        with store._write() as conn:
            if invalid == 'accepted':
                conn.execute("UPDATE objective_control SET status='accepted' WHERE objective_id=?", (goal['id'],))
            else:
                conn.execute('UPDATE objective_control SET round=round+1 WHERE objective_id=?', (goal['id'],))
    if invalid == 'objective-package-scope':
        transfer.pop('workPackageIds')
        transfer['objectiveIds'] = [goal['id']]
    proposal = {'members': members, 'transfers': transfers}
    actor = invalid in {'project-transfer-scope', 'project-team-scope', 'objective-package-scope'}
    if invalid == 'objective-package-scope':
        # A red/green actor cannot take over an objective whose unplanned peer
        # package is blue, even before it has produced any task rows.
        configure(store, roster=[{**row, 'managed_teams': ['green']} if row['id'] == 'staffer' else row for row in roster])
    request = hire_request(store, proposal) if actor else None
    before, generation = store.configuration_snapshot(), store._policy_generation
    with store._connect() as conn:
        ledger = list(conn.iterdump())
    with pytest.raises(ValueError):
        if actor:
            apply(store, request, proposal)
        else:
            updated = {row['id']: row for row in before['roster']}
            updated.update({row['id']: row for row in members})
            configure(store, roster=list(updated.values()), transfers=transfers)
    assert store.configuration_snapshot() == before and store._policy_generation == generation
    with store._connect() as conn:
        assert list(conn.iterdump()) == ledger
    if invalid in {'package-capability', 'planned-capability', 'executive-capability'}:
        assert store.cancel(goal['id'])
        updated = {row['id']: row for row in before['roster']}
        updated.update({row['id']: row for row in members})
        configure(store, roster=list(updated.values()))
