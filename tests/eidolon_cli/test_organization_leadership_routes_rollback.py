"""Real-ledger leadership routes rollback invariants."""
import pytest
from eidolon_cli.organization_store import OrganizationStore
from tests.organization_management_helpers import configure, settings
from tests.organization_leadership_routes_helpers import (
    _advance_to,
    _legacy_store,
    _pause,
    _retained,
    _roster,
)


@pytest.mark.parametrize(('actor', 'stage', 'invalid'), [
    ('owner', 'request.plan', 'request.integrate'), ('owner', 'request.plan', 'request.plan'),
    ('owner', 'request.accept', 'request.plan'), ('agent', 'request.integrate', 'request.accept'),
    ('agent', 'request.accept', 'request.accept'), ('agent', 'request.integrate', 'task-scope'),
    ('agent', 'request.plan', 'project-scope'), ('agent', 'request.integrate', 'retired-task-scope'),
    ('owner', 'legacy', 'legacy-terminal'),
])
def test_leadership_route_or_scope_loss_rolls_back_configuration(tmp_path, actor, stage, invalid):
    roster = _roster(work_team='green' if invalid.endswith('task-scope') else 'red')
    if invalid == 'legacy-terminal':
        store = _legacy_store(tmp_path, roster)
        before, generation = store.configuration_snapshot(), store._policy_generation
        with store._connect() as conn:
            ledger = list(conn.iterdump())
        with pytest.raises(ValueError, match='existing open objectives'):
            configure(store, transfers=[
                {'fromAgentId': 'manager', 'toAgentId': 'planner', 'objectiveIds': ['old']},
                {'fromAgentId': 'executive', 'toAgentId': 'lead', 'objectiveIds': ['old']},
            ])
        assert store.configuration_snapshot() == before and store._policy_generation == generation
        with store._connect() as conn:
            assert list(conn.iterdump()) == ledger
        # Older versions allowed selecting this terminal history for a handoff.
        # Such retained assignments must not create new capability obligations.
        with store._write() as conn:
            conn.execute("UPDATE objective_assignments SET manager_id='planner',executive_id='lead' WHERE objective_id='old'")
        retained = _retained(store)
        configure(store, roster=[{**row, 'capabilities': []} if row['id'] in {'planner', 'lead'} else row for row in roster])
        assert _retained(store) == retained
        assert OrganizationStore(store.path).snapshot()['objectives'][0]['acceptance']['status'] == 'legacy_completed'
        assert store.claim_next() is None
        return
    options = {}
    if invalid == 'project-scope':
        options = {'read_roots': [str(tmp_path)],
                   'project_grants': [{'id': 'green-check', 'files': ['root0/example.txt'], 'execution': {'root': 'root0'}}],
                   'projects': [{'id': 'green-project', 'root': 'root0', 'recipe': 'green-check', 'team': 'green'}]}
    store = OrganizationStore(tmp_path / 'organization.db', settings(*roster, **options))
    goal = store.create_objective('Keep authorized leadership routes', idempotency_key='goal',
                                  manager_id='planner', executive_id='lead',
                                  project_ids=['green-project'] if invalid == 'project-scope' else None)
    _advance_to(store, stage)
    target = 'lead' if invalid == 'request.accept' else 'planner'
    changed = [{**row, **({'team': 'blue'} if invalid.endswith('-scope') else
                         {'capabilities': [kind for kind in row['capabilities'] if kind != invalid]})}
               if row['id'] == target else row for row in roster]
    proposal = {'members': [row for row in changed if row['id'] == target]}
    if actor == 'agent' or invalid == 'request.plan':
        _pause(store, proposal=proposal if actor == 'agent' else None)
    if invalid == 'retired-task-scope':
        store.reload_configuration(settings(*(row for row in roster if row['id'] != target)))
    hire = store.claim_next() if actor == 'agent' else None
    if hire:
        assert hire['type'] == 'request.hire' and hire['agent_id'] == 'staffer'
    before, generation, retained = store.configuration_snapshot(), store._policy_generation, _retained(store)
    with store._connect() as conn:
        ledger = '\n'.join(conn.iterdump())
    message = 'outside the actor managed teams' if invalid.endswith('-scope') else invalid
    with pytest.raises(ValueError, match=message):
        if hire:
            store.finish(hire, {})
        else:
            configure(store, roster=changed)
    assert store.configuration_snapshot() == before and store._policy_generation == generation
    assert _retained(store) == retained
    with store._connect() as conn:
        assert '\n'.join(conn.iterdump()) == ledger
    if hire:
        assert store.heartbeat(hire)
    else:
        store = OrganizationStore(store.path)
        assert store.configuration_snapshot() == before
        store.cancel(goal['id'])
        configure(store, roster=changed)
