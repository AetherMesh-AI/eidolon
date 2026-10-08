"""Real-ledger leadership handoffs integration invariants."""
import pytest
from tests.organization_leadership_handoffs_helpers import (
    assert_objective_leadership_handoff_without_open_tasks_survives_restart,
)


@pytest.mark.parametrize(('role', 'stage', 'paused'), [
    ('Manager', 'request.integrate', False),
    ('Manager', 'request.integrate', True),
    ('Executive', 'request.integrate', False),
    ('Executive', 'request.integrate', True),
    ('Both', 'request.integrate', False),
    ('Both', 'request.integrate', True),
])
def test_objective_leadership_handoff_without_open_tasks_survives_restart(tmp_path, stage, role, paused):
    assert_objective_leadership_handoff_without_open_tasks_survives_restart(tmp_path, stage, role, paused)
