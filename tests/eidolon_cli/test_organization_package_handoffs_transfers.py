"""Real-ledger package handoffs transfers invariants."""
import json
import pytest
from eidolon_cli.organization_store import OrganizationStore
from tests.organization_management_helpers import configure, hire_request, settings
from tests.organization_package_handoffs_helpers import (
    _decomposition,
    _packages,
    _prepared,
    _retained,
)


@pytest.mark.parametrize(('stage', 'change'), [
    ('request.decompose', 'executive'), ('request.plan', 'objective'), ('request.plan', 'package'),
    ('request.plan', 'team'), ('request.plan', 'package-agent'), ('planned', 'package'), ('request.plan', 'both'), ('request.plan', 'both-reversed'),
])
def test_package_handoffs_preserve_independent_routes_continuations_and_provenance(tmp_path, stage, change):
    store, goal, old_claim = _prepared(tmp_path, stage=stage)
    packages = _packages(store)
    retained = _retained(store)
    roster = store.configuration_snapshot()['roster']
    transfers, expected = [], old_claim['agent_id']
    if change in {'objective', 'both', 'both-reversed'}:
        transfers.append({'fromAgentId': 'planner', 'toAgentId': 'successor', 'objectiveIds': [goal['id']]})
    if change in {'package', 'package-agent', 'both', 'both-reversed'}:
        if transfers:
            transfers[0]['workPackageIds'] = [packages[0]['id']]
        else:
            transfers.append({'fromAgentId': 'planner', 'toAgentId': 'successor',
                              'workPackageIds': [packages[0]['id']]})
        expected = 'successor'
    if change in {'executive', 'both', 'both-reversed'}:
        transfers.append({'fromAgentId': 'lead', 'toAgentId': 'next-lead', 'objectiveIds': [goal['id']]})
        roster = [{**row, 'manager_id': 'next-lead'} if row['role'] == 'Manager' and row['manager_id'] == 'lead'
                  else {**row, 'enabled': False} if row['id'] == 'lead' else row for row in roster]
        if change == 'executive':
            expected = 'next-lead'
    if change == 'both-reversed':
        transfers.reverse()
    if change == 'team':
        # Startup changes alone are deliberately fenced. An unrelated audited
        # capacity edit must not silently repair that stale package route.
        startup = [{**row, 'team': 'orange'} if row['id'] == 'planner' else row for row in roster]
        store.reload_configuration(settings(*startup))
        configure(store, max_inflight=1)
        with store._connect() as conn:
            assert conn.execute('SELECT team FROM requests WHERE id=?', (old_claim['id'],)).fetchone()[0] == 'red'
        roster = [{**row, 'team': 'green'} if row['id'] == 'planner' else row for row in roster]
    if change == 'package-agent':
        proposal = {'transfers': transfers}
        request = hire_request(store, proposal)
        assert store.finish(request, {})
    else:
        configure(store, roster=roster, transfers=transfers)
    after = _retained(store)
    assert all(set(rows).issubset(after[table]) for table, rows in retained.items())
    store = OrganizationStore(store.path)
    with store._connect() as conn:
        if packages:
            peer = conn.execute('SELECT * FROM requests WHERE id=?', (packages[1]['plan_request_id'],)).fetchone()
            assert peer['team'] == 'blue' and peer['agent_id'] == 'peer'
            assert conn.execute('SELECT agent_id FROM request_continuations WHERE request_id=?', (peer['id'],)).fetchone()[0] == 'peer'
            current = conn.execute('SELECT * FROM manager_work_packages WHERE id=?', (packages[0]['id'],)).fetchone()
            assert current['manager_id'] == ('successor' if change in {'package', 'package-agent', 'both', 'both-reversed'} else 'planner')
            assert current['plan'] == packages[0]['plan']
            if stage == 'planned':
                assert conn.execute('SELECT agent_id FROM requests WHERE id=?', (old_claim['id'],)).fetchone()[0] == 'planner'
                assert json.loads(current['plan'])['managerId'] == 'planner'
                assert conn.execute('SELECT agent_id FROM agent_history WHERE request_id=?', (old_claim['id'],)).fetchone()[0] == 'planner'
                assert conn.execute('SELECT manager_id FROM task_assignments').fetchone()[0] == 'tasks-red'
        audits = [json.loads(row[0]) for row in conn.execute("SELECT after_state FROM organization_management_audit WHERE kind='transfer'")]
        if change in {'package', 'package-agent', 'both', 'both-reversed'}:
            assert any(row['workPackageIds'] == [packages[0]['id']] for row in audits)
    if stage != 'planned':
        question = next(row for row in store.snapshot()['requests'] if row['parentRequestId'] == old_claim['id'])
        assert store.respond(question['id'], 'Engineering leads.', idempotency_key='answer')
        resumed = store.claim_next()
        assert resumed['id'] == old_claim['id'] and resumed['agent_id'] == expected
        assert resumed['team'] == ('green' if change in {'team', 'package', 'package-agent', 'both', 'both-reversed'} else 'red')
        assert store.context(resumed)['requestResponses'][0]['response']['text'] == 'Engineering leads.'
        assert not store.finish(old_claim, {'summary': 'Stale completion', 'deliverable': 'Must never commit.'})
        if stage == 'request.decompose':
            assert store.finish(resumed, _decomposition())
        else:
            assert store.finish(resumed, {'intervention': 'Fixture ends after verifying exact resumed ownership.'})
