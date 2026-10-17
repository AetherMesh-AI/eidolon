"""Registered setup RPCs with real served profile/source and synthetic service I/O."""
import json
from queue import Queue
from types import SimpleNamespace

import pytest

import tui_gateway.server as server
from tui_gateway import methods_organization_setup_subscription as methods
from tui_gateway import organization_credential_transport as credentials
from eidolon_cli import organization_team_setup as team
from eidolon_cli.organization_setup_subscription_host import HostAuthority
from tests.eidolon_cli.test_organization_team_setup import profile, dump  # noqa: F401


class Transport:
    closed = False

    def __init__(self):
        self.responses = Queue()

    def write(self, value):
        self.responses.put(value)


def call(method, params, transport):
    result = server.dispatch({'jsonrpc': '2.0', 'id': 31,
        'method': 'organization.' + method, 'params': params}, transport=transport)
    return result if result is not None else transport.responses.get(timeout=15)


@pytest.fixture
def served(profile, monkeypatch):
    monkeypatch.setattr(server, '_eidolon_home', profile.home)
    monkeypatch.setattr(server, '_setup_subscription_authority',
        HostAuthority.capture(lambda: profile.home, lambda: profile.home))
    transport = Transport()
    credentials.register_owner(transport, profile.home)
    yield profile, transport
    credentials.retire_owner(transport)


def scoped(profile):
    return {'expectedOrganizationId': profile.identity['organizationId'],
            'sourceRevision': team.team_setup(profile.home, profile.identity['organizationId'])['sourceRevision']}


def test_registered_status_is_passive_and_closed(served, monkeypatch):
    profile, transport = served
    captured = []
    def service(owner_transport, context, authority):
        assert owner_transport is transport
        assert context.owner_guard() is True
        captured.append(context)
        return SimpleNamespace(status=lambda: {'stage': 'idle', 'connectionRevision': 0,
            'connectionState': 'absent', 'cleanupPending': False})
    monkeypatch.setattr(methods, '_service', service)
    before = dump(profile)
    value = scoped(profile)
    result = call('setupSubscriptionStatus', value, transport)
    assert result['result']['connectionState'] == 'absent'
    assert len(captured) == 1
    assert call('setupSubscriptionStatus', {**value, 'token': 'forbidden'}, transport)['error']['code'] == 5077
    assert call('setupSubscriptionStatus', value, Transport())['error']['code'] == 5077
    assert dump(profile) == before


def test_registered_consent_checks_precede_service_mutation(served, monkeypatch):
    profile, transport = served
    calls = []
    monkeypatch.setattr(methods, '_service', lambda *args: SimpleNamespace(
        begin=lambda revision: calls.append(revision) or {'stage': 'pending'}))
    value = {**scoped(profile), 'expectedRevision': 0, 'confirmed': True, 'consentVersion': 1}
    assert call('beginSetupSubscription', {**value, 'confirmed': False}, transport)['error']['code'] == 5077
    assert calls == []
    assert call('beginSetupSubscription', value, transport)['result']['stage'] == 'pending'
    assert calls == [0]


def test_context_turnover_retires_old_catalog_without_network(served, monkeypatch):
    profile, transport = served
    monkeypatch.setattr(methods, '_installation_authority', lambda: server._setup_subscription_authority)
    first, service = methods._current_context(transport, profile.home, profile.identity['organizationId'])
    catalog = methods._catalog(service, first)
    monkeypatch.setattr(catalog, 'load', lambda *args: pytest.fail('turnover fetched catalog'))
    first_revision = first.source_digest
    content = json.loads((profile.home / 'config.yaml').read_text(encoding='utf-8'))
    content['display'] = {'compact': True}
    (profile.home / 'config.yaml').write_text(json.dumps(content), encoding='utf-8')
    result = methods.turnover_subscription_context(transport, profile.home, profile.identity['organizationId'])
    assert result['sourceRevision'] != first_revision
    assert result['catalogState'] == 'not_requested'
    assert service._retired and first.owner_guard() is False
    second, successor = methods._current_context(transport, profile.home, profile.identity['organizationId'])
    assert second.source_digest == result['sourceRevision']
    assert successor is not service
    assert not hasattr(successor, '_setup_model_catalog')


def test_rpc_passes_transport_without_client_route_discriminant(served, monkeypatch):
    profile, transport = served
    seen = []
    def generate(home, params, *, cancel, subscription_transport):
        seen.append((home, subscription_transport, cancel.is_set()))
        return {'state': 'synthetic'}
    monkeypatch.setattr(team, 'generate_team', generate)
    value = {**scoped(profile), 'jobs': 'Reports', 'goals': 'Clarity',
             'namingStyle': '', 'idempotencyKey': 'synthetic'}
    assert call('generateTeam', {**value, 'mode': 'subscription_calls'}, transport)['error']['code'] == -32602
    assert seen == []
    assert call('generateTeam', value, transport)['result']['state'] == 'synthetic'
    assert seen == [(profile.home, transport, False)]


@pytest.mark.linux_only
def test_linux_subscription_capability_is_truthful_and_passive(profile):
    before = dump(profile)
    view = team.team_setup(profile.home, profile.identity['organizationId'])
    assert view['setupSubscriptionState'] == 'unsupported_platform'
    assert view['state'] == 'available'
    assert dump(profile) == before


@pytest.mark.windows_only
def test_windows_subscription_capability_is_truthful_and_passive(profile):
    before = dump(profile)
    view = team.team_setup(profile.home, profile.identity['organizationId'])
    assert view['setupSubscriptionState'] == 'unsupported_platform'
    assert view['state'] == 'available'
    assert dump(profile) == before


@pytest.mark.macos_only
def test_mac_capability_requires_exact_registered_owner(served):
    profile, transport = served
    assert team.team_setup(profile.home, profile.identity['organizationId'])['setupSubscriptionState'] == 'local_owner_required'
    assert team.team_setup(profile.home, profile.identity['organizationId'],
                           subscription_transport=transport)['setupSubscriptionState'] == 'available'


def test_policy_rpc_keeps_stable_safe_blocker_reason(served, monkeypatch):
    from eidolon_cli import organization_setup_subscription_policy as policy
    profile, transport = served
    def blocked(*args):
        raise policy.SubscriptionPolicyError('unsupported_managed_scope')
    monkeypatch.setattr(policy, 'readiness_subscription_policy', blocked)
    result = call('setupSubscriptionPolicy',
        {'expectedOrganizationId': profile.identity['organizationId']}, transport)
    assert result['error']['code'] == 5077
    assert result['error']['data']['reason'] == 'unsupported_managed_scope'
