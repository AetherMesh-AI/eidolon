"""Real-ledger leadership routes agent invariants."""
import pytest
from tests.organization_leadership_routes_helpers import (
    assert_same_identity_team_change_retains_current_and_future_leadership,
)


@pytest.mark.parametrize(('actor', 'stage', 'paused'), [
    ('agent', 'request.integrate', True),
    ('agent', 'request.accept', True),
])
def test_same_identity_team_change_retains_current_and_future_leadership(tmp_path, actor, stage, paused):
    assert_same_identity_team_change_retains_current_and_future_leadership(tmp_path, actor, stage, paused)
