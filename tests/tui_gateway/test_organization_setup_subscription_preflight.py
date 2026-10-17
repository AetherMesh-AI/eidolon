"""Server-side catalog evidence and context turnover before ledger writer entry."""
from copy import deepcopy
from types import SimpleNamespace
import threading

import pytest

from eidolon_cli.organization_setup_subscription_store import SubscriptionStore
from tui_gateway import methods_organization_setup_subscription as methods
from tests.eidolon_cli.test_organization_setup_subscription_catalog import fixture  # noqa: F401


def setup_preflight(fixture, monkeypatch):
    catalog, state, descriptor, owner, leases = fixture
    service = catalog._service
    service._store = SimpleNamespace(_binding_digest=SubscriptionStore._binding_digest)
    service.status = lambda: {'connectionRevision': state['revision'],
                              'connectionState': 'active', 'cleanupPending': False}
    service._lease_cancel = threading.Event()
    owner.lease_generation_guard = lambda: state['valid']
    monkeypatch.setattr(methods, '_current_context', lambda *args: (owner, service))
    monkeypatch.setattr(methods, '_catalog', lambda *args: catalog)
    candidate = {'connectionId': SubscriptionStore._binding_digest(descriptor['binding']),
                 'authorizationEpoch': descriptor['authorizationEpoch'], 'modelSlug': 'synthetic-a'}
    return catalog, state, descriptor, owner, service, candidate, leases


def test_policy_preflight_requires_explicit_catalog_and_does_not_fetch(fixture, monkeypatch):
    catalog, state, _, owner, service, candidate, leases = setup_preflight(fixture, monkeypatch)
    with pytest.raises(methods.BoundaryError):
        methods.subscription_preflight(object(), '/synthetic', candidate)
    assert state['calls'] == 0 and leases == []
    models = methods._load_catalog(service, owner, 2)
    models['models'].clear()
    evidence = methods.subscription_preflight(object(), '/synthetic', candidate)
    assert evidence.catalog_evidence['models']
    assert evidence.lease is None and evidence.owner_generation is owner.owner_generation
    assert state['calls'] == 1 and len(leases) == 1
    catalog.invalidate()
    with pytest.raises(RuntimeError):
        methods.subscription_preflight(object(), '/synthetic', candidate)
    assert state['calls'] == 1


def test_explicit_inference_recaptures_catalog_and_releases_exact_lease(fixture, monkeypatch):
    _, state, _, _, _, candidate, leases = setup_preflight(fixture, monkeypatch)
    evidence = methods.subscription_preflight(object(), '/synthetic', candidate, inference=True)
    assert state['calls'] == 1 and len(leases) == 2
    assert leases[0].closed and evidence.lease is leases[1]
    assert not leases[1].closed
    evidence.release()
    assert leases[1].closed
    state['valid'] = False
    assert evidence.cheap_guard() is False


@pytest.mark.parametrize('field,value', [('connectionId', 'f' * 64), ('authorizationEpoch', 'foreign')])
def test_account_policy_swap_rejects_before_catalog_or_lease(fixture, monkeypatch, field, value):
    _, state, _, _, _, candidate, leases = setup_preflight(fixture, monkeypatch)
    wrong = {**candidate, field: value}
    with pytest.raises(methods.BoundaryError):
        methods.subscription_preflight(object(), '/synthetic', wrong, inference=True)
    assert state['calls'] == 0 and leases == []


def test_returned_catalog_evidence_cannot_mutate_private_cache(fixture, monkeypatch):
    _, _, _, owner, service, candidate, _ = setup_preflight(fixture, monkeypatch)
    methods._load_catalog(service, owner, 2)
    prior = deepcopy(service._setup_catalog_evidence)
    evidence = methods.subscription_preflight(object(), '/synthetic', candidate)
    evidence.catalog_evidence['models'].clear()
    assert service._setup_catalog_evidence == prior
