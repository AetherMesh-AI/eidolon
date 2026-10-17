"""Passive model evidence never opens a native grant or discovers providers."""
from copy import deepcopy

import pytest

from eidolon_cli.organization_setup_subscription_catalog import CatalogError
from tests.eidolon_cli.test_organization_setup_subscription_catalog import fixture  # noqa: F401


def test_cached_read_requires_existing_current_account_evidence_without_secret_access(fixture):
    catalog, state, descriptor, _, leases = fixture
    with pytest.raises(CatalogError):
        catalog.cached_evidence(2, 'unavailable', descriptor['authorizationEpoch'], 'synthetic-a')
    assert state['calls'] == 0 and not leases
    evidence = catalog.load(2)
    def forbidden(*args, **kwargs):
        pytest.fail('passive cache read reached grant authentication or provider')
    catalog._service.current_descriptor = forbidden
    catalog._service.lease_access_current = forbidden
    catalog._http = forbidden
    result = catalog.cached_evidence(2, evidence['connectionId'], evidence['authorizationEpoch'], 'synthetic-a')
    assert result == evidence
    result['models'].clear()
    assert catalog.cached_evidence(2, evidence['connectionId'], evidence['authorizationEpoch'], 'synthetic-a')['models']
    assert state['calls'] == 1


@pytest.mark.parametrize('change', ['account', 'epoch', 'revision', 'source', 'generation', 'owner',
                                    'wall_expiry', 'monotonic_expiry', 'model'])
def test_cached_read_fences_binding_owner_and_both_expiry_clocks(fixture, change):
    catalog, state, _, owner, _ = fixture
    evidence = catalog.load(2)
    args = [2, evidence['connectionId'], evidence['authorizationEpoch'], 'synthetic-a']
    if change == 'account':
        args[1] = 'f' * 64
    elif change == 'epoch':
        args[2] = '00000000-0000-4000-8000-000000000099'
    elif change == 'revision':
        args[0] += 1
    elif change == 'source':
        owner.source_digest = 'new-source'
    elif change == 'generation':
        owner.owner_generation = object()
    elif change == 'owner':
        state['valid'] = False
    elif change == 'wall_expiry':
        state['time'] = evidence['expiresAt']
    elif change == 'monotonic_expiry':
        state['mono'] += 61
    else:
        args[3] = 'not-in-account-catalog'
    original = deepcopy(catalog._entry[1])
    with pytest.raises(CatalogError):
        catalog.cached_evidence(*args)
    assert catalog._entry[1] == original
    assert state['calls'] == 1
