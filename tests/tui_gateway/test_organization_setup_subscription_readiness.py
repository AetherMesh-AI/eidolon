"""Actual registered teamSetup: v3 form readiness is separate from execution."""
import json
from queue import Queue
from types import SimpleNamespace
import time

import pytest

import tui_gateway.server as server
from tui_gateway import methods_organization_setup_subscription as gateway
from tui_gateway import organization_credential_transport as credentials
from eidolon_cli import organization_team_setup as team
from eidolon_cli.organization_setup_subscription_catalog import SubscriptionCatalog
from eidolon_cli.organization_setup_subscription_store import SubscriptionStore
from tests.eidolon_cli.test_organization_setup_subscription_policy_integration import ledger, apply  # noqa: F401


def rpc(ledger, method='teamSetup', extra=None):
    replies = Queue()
    ledger.transport.write = replies.put
    response = server.dispatch({'jsonrpc': '2.0', 'id': 83, 'method': 'organization.' + method,
        'params': {'expectedOrganizationId': ledger.identity['organizationId'], **(extra or {})}}, transport=ledger.transport)
    response = response if response is not None else replies.get(timeout=15)
    assert 'error' not in response, response
    return response['result']


@pytest.mark.macos_only
def test_v3_models_discovery_unblocks_setup_without_waiving_execution(ledger, monkeypatch):
    # The ordinary Codex route remains unsupported for execution/API generation.
    (ledger.home / 'config.yaml').write_text(json.dumps({'model': {
        'provider': 'openai-codex', 'default': 'synthetic-model'}}), encoding='utf-8')
    apply(ledger)
    monkeypatch.setattr(server, '_eidolon_home', ledger.home)
    monkeypatch.setattr(server, '_setup_subscription_authority', object())
    initial = rpc(ledger)
    assert initial['generationAllowance']['policyVersion'] == 3
    assert initial['readiness']['status'] == 'blocked'
    assert initial['readiness']['blockers'] == ['subscription_models_required']
    assert team._readiness(ledger.home)['status'] == 'blocked'
    revision = initial['sourceRevision']
    context = gateway._context(ledger.transport,
        {'expectedOrganizationId': ledger.identity['organizationId'], 'sourceRevision': revision}, object())
    state = {'revision': 1, 'active': True, 'models': 0}
    expires_at = time.time() + 3600
    class Lease:
        def run(self, callback):
            return callback('synthetic-access', self)
        def close(self):
            pass
    store = SimpleNamespace(connection_descriptor=lambda: (ledger.evidence.binding, state['revision']),
                            _binding_digest=SubscriptionStore._binding_digest)
    service = SimpleNamespace(_store=store, _binding=lambda binding: None,
        status=lambda: {'connectionRevision': state['revision'],
            'connectionState': 'active' if state['active'] else 'disconnected', 'cleanupPending': False},
        current_descriptor=lambda revision: {'binding': ledger.evidence.binding, 'revision': revision,
            'authorizationEpoch': ledger.evidence.epoch, 'expiresAt': expires_at},
        lease_access_current=lambda revision: Lease(), release_lease=lambda lease: lease.close(),
        _lease_guard=context.lease_generation_guard)
    def models(*args, **kwargs):
        state['models'] += 1
        return {'models': [{'slug': ledger.candidate['modelSlug'], 'display_name': 'Synthetic', 'visibility': 'list'}]}
    catalog = SubscriptionCatalog(service, context, http=models)
    service._setup_model_catalog = catalog
    key = (str(ledger.home.resolve()), ledger.identity['organizationId'], ledger.identity['owner']['ownerId'],
           revision, credentials.owner_generation(ledger.transport))
    monkeypatch.setitem(gateway._services, ledger.transport, (key, service))
    discovered = rpc(ledger, 'setupSubscriptionModels', {'sourceRevision': revision, 'expectedRevision': 1})
    assert discovered['models'][0]['slug'] == ledger.candidate['modelSlug']
    def forbidden(*args, **kwargs):
        pytest.fail('passive teamSetup reached grant authentication or provider')
    service.current_descriptor = forbidden
    service.lease_access_current = forbidden
    catalog._http = forbidden
    ready = rpc(ledger)
    assert ready['readiness']['status'] == 'unchecked'
    assert ready['readiness']['blockers'] == []
    assert ready['readiness']['modelAvailability'] == 'cached'
    assert ready['readiness']['connectionReadiness'] == 'metadata_current'
    assert ready['readiness']['executionVerified'] is False
    assert ready['sourceRevision'] == revision and state['models'] == 1
    assert team._readiness(ledger.home)['status'] == 'blocked'
    state['revision'] = 2
    assert rpc(ledger)['readiness']['blockers'] == ['subscription_models_required']
    state['revision'] = 1
    state['active'] = False
    assert rpc(ledger)['readiness']['blockers'] == ['subscription_connection_required']
    state['active'] = True
    catalog.invalidate()
    assert rpc(ledger)['readiness']['blockers'] == ['subscription_models_required']
    ledger.conn.execute('INSERT INTO organization_team_setup VALUES (1,?,?,?,?,?)',
                        (ledger.identity['organizationId'], 'created-team', 'input', '{}', time.time()))
    configured = rpc(ledger)
    assert configured['state'] == 'configured'
    assert configured['readiness'] == team._readiness(ledger.home)
