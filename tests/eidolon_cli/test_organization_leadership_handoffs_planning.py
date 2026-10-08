"""Real-ledger leadership handoffs planning invariants."""
import pytest
from tests.organization_leadership_handoffs_helpers import (
    assert_objective_leadership_handoff_without_open_tasks_survives_restart,
)


@pytest.mark.parametrize(('role', 'stage', 'paused'), [
    ('Manager', 'request.plan', False),
    ('Manager', 'request.plan', True),
    ('Executive', 'request.plan', False),
    ('Executive', 'request.plan', True),
    ('Both', 'request.plan', False),
    ('Both', 'request.plan', True),
])
def test_objective_leadership_handoff_without_open_tasks_survives_restart(tmp_path, stage, role, paused):
    assert_objective_leadership_handoff_without_open_tasks_survives_restart(tmp_path, stage, role, paused)
