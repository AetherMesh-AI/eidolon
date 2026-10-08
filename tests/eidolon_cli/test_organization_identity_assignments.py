"""Organization task assignments preserve worker identity and leader authority."""
import pytest

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_identity import task_assignment_view
from eidolon_cli.organization_store import OrganizationStore
from tests.organization_identity_helpers import (
    _domain_settings,
    _memory,
)
from tests.organization_package_helpers import claim_after_decomposition


def test_cross_manager_assignment_preserves_authority_and_exact_worker(tmp_path):
    store = OrganizationStore(tmp_path / 'state.db', _domain_settings())
    store.create_objective('Coordinate domains', idempotency_key='domains', manager_id='engineering')
    planner = claim_after_decomposition(store)
    assert planner['agent_id'] == 'engineering'
    assert store.finish(planner, {'tasks': [
        {'title': 'Research inputs', 'description': 'Develop research inputs.', 'type': 'work.draft',
         'team': 'research', 'managerId': 'research', 'agentId': 'researcher'}], 'workers': 1})
    hire = store.claim_next()
    assert hire['type'] == 'request.hire'
    assert store.finish(hire, {})
    work = store.claim_next()
    assert work['agent_id'] == 'researcher'
    context = store.context(work)
    assert context['agent']['managerId'] == 'research'
    with store._connect() as conn:
        assignment = task_assignment_view(conn, work['task_id'])
    assert assignment == {'agentId': 'researcher', 'managingAgentId': 'research', 'assignedById': 'engineering'}
    assert context['toolPolicy']['tools'] == []


@pytest.mark.parametrize('assignment', [
    {'agentId': 'missing'}, {'agentId': 'research'},
    {'agentId': 'engineer', 'managerId': 'research'},
    {'agentId': 'researcher'}, {'managerId': 'owner'},
])
def test_invalid_assignments_roll_back_entire_plan(tmp_path, assignment):
    store = OrganizationStore(tmp_path / 'state.db', _domain_settings())
    store.create_objective('Scoped work', idempotency_key='invalid-assignment')
    claim = claim_after_decomposition(store)
    with pytest.raises(ValueError, match='Task'):
        store.finish(claim, {'tasks': [{'title': 'Plan', 'description': 'Engineering work',
                                       'type': 'work.draft', 'team': 'engineering', **assignment}]})
    with store._connect() as conn:
        assert conn.execute('SELECT count(*) FROM tasks').fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM task_assignments').fetchone()[0] == 0
    assert _memory(store, claim['agent_id'])['revision'] == 0


def test_unpinned_scope_prefers_planners_existing_reports_over_new_same_team_manager(tmp_path):
    settings = OrganizationSettings.from_config({'organization': {'roster': [
        {'id': 'new-manager', 'name': 'New manager', 'role': 'Manager', 'team': 'general'},
        {'id': 'existing-worker', 'name': 'Existing worker', 'capabilities': ['work.draft']},
        {'id': 'new-worker', 'name': 'New worker', 'manager_id': 'new-manager', 'capabilities': ['work.draft']},
    ]}})
    store = OrganizationStore(tmp_path / 'state.db', settings)
    store.create_objective('Keep appropriate scope', idempotency_key='scope')
    planner = claim_after_decomposition(store)
    assert planner['agent_id'] == 'manager'
    store.finish(planner, {'tasks': [{'title': 'Draft', 'description': 'Work with the established team.', 'type': 'work.draft'}]})
    hire = store.claim_next()
    assert hire['type'] == 'request.hire'
    store.finish(hire, {})
    worker = store.claim_next()
    assert worker['agent_id'] == 'existing-worker'
    assert store.context(worker)['task']['managingAgentId'] == 'manager'


@pytest.mark.parametrize('assignment', [{'manager_id': 'director'}, {'manager_id': 'reviewer'},
                                      {'executive_id': 'manager'}, {'manager_id': 'missing'}])
def test_objective_rejects_wrong_or_incomplete_leader_roles_atomically(tmp_path, assignment):
    store = OrganizationStore(tmp_path / 'state.db')
    with pytest.raises(ValueError, match='Objective'):
        store.create_objective('Invalid ownership', idempotency_key='invalid-owner', **assignment)
    with store._connect() as conn:
        assert conn.execute('SELECT count(*) FROM objectives').fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM objective_assignments').fetchone()[0] == 0
