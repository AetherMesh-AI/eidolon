"""Real-ledger package handoffs routes invariants."""
import pytest
from tests.organization_management_helpers import configure
from tests.organization_package_handoffs_helpers import (
    _packages,
    _prepared,
)


@pytest.mark.parametrize('plan_order', ['tied', 'reversed'])
@pytest.mark.parametrize('stage', ['request.plan', 'planned'])
def test_handoff_fixture_preserves_exact_routes_with_independent_plan_orders(tmp_path, stage, plan_order):
    store, _, source = _prepared(tmp_path, stage=stage, plan_order=plan_order)
    assert source['agent_id'] == 'planner'
    with store._connect() as conn:
        assert not conn.execute("SELECT 1 FROM requests WHERE status='running'").fetchone()
        peer = conn.execute("SELECT * FROM requests WHERE type='request.plan' AND agent_id='peer'").fetchone()
        assert peer['status'] == 'waiting_response'
        assert conn.execute('SELECT agent_id FROM request_continuations WHERE request_id=?', (peer['id'],)).fetchone()[0] == 'peer'
    package = next(row for row in _packages(store) if row['manager_id'] == 'planner')
    configure(store, transfers=[{'fromAgentId': 'planner', 'toAgentId': 'successor',
                                'workPackageIds': [package['id']]}])
