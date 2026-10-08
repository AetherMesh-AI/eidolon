"""Real-ledger package handoffs validation invariants."""
import pytest
from tests.organization_package_handoffs_helpers import (
    assert_live_package_obligations_and_scopes_fail_atomically_until_objective_terminal,
)


@pytest.mark.parametrize('invalid', [
    'unowned',
    'duplicate',
    'overlapping',
    'missing',
    'worker',
    'cancelled',
    'accepted',
    'old-round',
    'project-transfer-scope',
    'project-team-scope',
    'objective-package-scope',
])
def test_live_package_obligations_and_scopes_fail_atomically_until_objective_terminal(tmp_path, invalid):
    assert_live_package_obligations_and_scopes_fail_atomically_until_objective_terminal(tmp_path, invalid)
