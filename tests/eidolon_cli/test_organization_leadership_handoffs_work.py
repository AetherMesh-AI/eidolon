"""Real-ledger leadership handoffs work invariants."""
import pytest
from tests.organization_leadership_handoffs_helpers import (
    assert_objective_leadership_handoff_without_open_tasks_survives_restart,
)


@pytest.mark.parametrize(('role', 'stage', 'paused'), [
    ('Manager', 'work.draft', False),
    ('Split', 'work.draft', False),
    ('Split', 'work.draft', True),
])
def test_objective_leadership_handoff_without_open_tasks_survives_restart(tmp_path, stage, role, paused):
    assert_objective_leadership_handoff_without_open_tasks_survives_restart(tmp_path, stage, role, paused)
