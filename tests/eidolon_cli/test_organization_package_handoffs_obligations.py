"""Real-ledger package handoffs obligations invariants."""
import pytest
from tests.organization_package_handoffs_helpers import (
    assert_live_package_obligations_and_scopes_fail_atomically_until_objective_terminal,
)


@pytest.mark.parametrize('invalid', [
    'executive-capability',
    'package-capability',
    'planned-capability',
    'package-disabled',
    'reporting-line',
    'destination-capability',
    'destination-reporting',
])
def test_live_package_obligations_and_scopes_fail_atomically_until_objective_terminal(tmp_path, invalid):
    assert_live_package_obligations_and_scopes_fail_atomically_until_objective_terminal(tmp_path, invalid)
