"""Objective handoffs reject invalid ownership and scope changes atomically."""
import pytest

from tests.organization_package_helpers import claim_after_decomposition
from tests.eidolon_cli.test_organization_management import (
    apply, configure, hire_request, member, settings, staffing_store,
)


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
