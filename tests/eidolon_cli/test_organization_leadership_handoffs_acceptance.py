"""Real-ledger leadership handoffs acceptance invariants."""
import pytest
from tests.organization_leadership_handoffs_helpers import (
    assert_objective_leadership_handoff_without_open_tasks_survives_restart,
)


@pytest.mark.parametrize(('role', 'stage', 'paused'), [
    ('Manager', 'request.accept', False),
    ('Manager', 'request.accept', True),
    ('Executive', 'request.accept', False),
    ('Executive', 'request.accept', True),
    ('Both', 'request.accept', False),
    ('Both', 'request.accept', True),
])
def test_objective_leadership_handoff_without_open_tasks_survives_restart(tmp_path, stage, role, paused):
    assert_objective_leadership_handoff_without_open_tasks_survives_restart(tmp_path, stage, role, paused)
