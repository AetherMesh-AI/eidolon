"""Real-ledger organization leadership invariants."""
import pytest
from tests.organization_management_helpers import (
    configure, leadership_goal, member,
)


def test_manager_handoff_moves_exact_open_tasks_and_objective_but_preserves_old_authors(tmp_path):
    store, roster, objective, tasks, plan = leadership_goal(tmp_path)
    updated = [entry for entry in roster if entry['id'] != 'team-lead']
    updated = [{**entry, 'manager_id': 'new-lead'} if entry['id'] == 'source' else entry for entry in updated]
    updated.append(member('new-lead', role='Manager', manager_id='lead-one',
                          capabilities=['request.plan', 'request.integrate']))
    result = configure(store, roster=updated, transfers=[{
        'fromAgentId': 'team-lead', 'toAgentId': 'new-lead', 'taskIds': tasks, 'includeMemory': True}])
    with store._connect() as conn:
        assignment = conn.execute('SELECT * FROM objective_assignments WHERE objective_id=?', (objective,)).fetchone()
        assert assignment['manager_id'] == 'new-lead' and assignment['executive_id'] == 'lead-one'
        assert {row[0] for row in conn.execute('SELECT manager_id FROM task_assignments')} == {'new-lead'}
        assert conn.execute('SELECT agent_id FROM requests WHERE id=?', (plan,)).fetchone()[0] == 'team-lead'
    assert any(change['kind'] == 'transfer' for change in result['recentChanges'])


def test_executive_handoff_requires_every_open_task_and_preserves_manager_identity(tmp_path):
    store, roster, objective, tasks, _ = leadership_goal(tmp_path)
    updated = [{**entry, 'manager_id': 'lead-two'} if entry['id'] == 'team-lead' else entry
               for entry in roster if entry['id'] != 'lead-one']
    with pytest.raises(ValueError, match='every open task'):
        configure(store, roster=updated, transfers=[{
            'fromAgentId': 'lead-one', 'toAgentId': 'lead-two', 'taskIds': tasks[:1], 'includeMemory': False}])
    configure(store, roster=updated, transfers=[{
        'fromAgentId': 'lead-one', 'toAgentId': 'lead-two', 'taskIds': tasks, 'includeMemory': False}])
    with store._connect() as conn:
        assert conn.execute('SELECT executive_id FROM objective_assignments WHERE objective_id=?', (objective,)).fetchone()[0] == 'lead-two'
        assert {row[0] for row in conn.execute('SELECT manager_id FROM task_assignments')} == {'team-lead'}


def test_live_objective_leadership_cannot_be_disabled_without_handoff(tmp_path):
    store, roster, _, _, _ = leadership_goal(tmp_path)
    with pytest.raises(ValueError, match='open-objective'):
        configure(store, roster=[{**entry, 'enabled': False} if entry['id'] == 'lead-one' else entry for entry in roster])
    assert next(staff for staff in store.settings.roster if staff.id == 'lead-one').enabled
