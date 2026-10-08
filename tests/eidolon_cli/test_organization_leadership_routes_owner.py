"""Real-ledger leadership routes owner invariants."""
import pytest
from tests.organization_leadership_routes_helpers import (
    assert_same_identity_team_change_retains_current_and_future_leadership,
)


@pytest.mark.parametrize(('actor', 'stage', 'paused'), [
    ('owner', 'request.plan', False),
    ('owner', 'request.plan', True),
    ('owner', 'request.integrate', True),
    ('owner', 'request.accept', False),
])
def test_same_identity_team_change_retains_current_and_future_leadership(tmp_path, actor, stage, paused):
    assert_same_identity_team_change_retains_current_and_future_leadership(tmp_path, actor, stage, paused)
