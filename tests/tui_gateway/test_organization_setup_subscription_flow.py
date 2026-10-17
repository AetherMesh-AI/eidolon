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
from tests.eidolon_cli.test_organization_setup_subscription_admission import activated  # noqa: F401
from tests.eidolon_cli.test_organization_setup_subscription_policy_integration import ledger  # noqa: F401


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


@pytest.mark.parametrize('method, operation', [
    ('improveTeamInput', 'improve_jobs'), ('generateTeam', 'generate'),
])
@pytest.mark.parametrize('outcome, reason', [
    ('unknown', 'unavailable'), ('completed', 'rejected'), ('failed', 'rejected'),
    ('incomplete', 'rejected'), ('malformed', 'rejected'), ('exception', 'unavailable'),
])
def test_subscription_execution_errors_preserve_parameter_and_api_contracts(
        served, monkeypatch, method, operation, outcome, reason):
    from eidolon_cli import organization_setup_subscription_admission as admission
    from eidolon_cli import organization_setup_subscription_transport as sender
    from eidolon_cli import organization_team_budget as budget
    from eidolon_cli import organization_team_generation as generation
    from eidolon_cli.organization_executor import OrganizationExecutionError

    profile, transport = served
    value = {**scoped(profile), 'jobs': 'Reports', 'goals': 'Clarity',
             'namingStyle': '', 'idempotencyKey': 'execution-error'}
    if method == 'improveTeamInput':
        value['field'] = 'jobs'
    before, calls = dump(profile), []
    prepared = SimpleNamespace(request=SimpleNamespace(operation=operation))

    def send(actual):
        assert actual is prepared
        calls.append('send')
        if outcome == 'exception':
            raise OrganizationExecutionError('synthetic-private token and response text')
        return SimpleNamespace(text='synthetic-private response text' if outcome == 'malformed' else None,
                               observation=SimpleNamespace(terminal_kind=outcome))

    def infer(home, params, key, requested_operation, finish, *, transport, cancel):
        assert home == profile.home and requested_operation == method
        try:
            return generation.complete_subscription(prepared)
        finally:
            calls.append('cleanup')

    read_policy = budget.read_policy
    monkeypatch.setattr(budget, 'read_policy', lambda conn: {'version': 3})
    monkeypatch.setattr(admission, 'infer_subscription', infer)
    monkeypatch.setattr(sender, 'send_once', send)
    for malformed in ({**value, 'jobs': []}, {**value, 'expectedOrganizationId': 'invalid'},
                      {**value, 'mode': 'subscription_calls'}):
        assert call(method, malformed, transport)['error']['code'] == -32602
    assert calls == [] and dump(profile) == before
    result = call(method, value, transport)
    assert result['error'] == {'code': 5076, 'message': 'Team setup is unavailable',
                               'data': {'reason': reason}}
    assert calls == ['send', 'cleanup'] and dump(profile) == before

    # The same existing API-route exception keeps its original wire contract.
    monkeypatch.setattr(budget, 'read_policy', read_policy)
    api_calls = []
    def api_failure(*args, **kwargs):
        api_calls.append('call')
        raise OrganizationExecutionError('Existing API execution failure')
    monkeypatch.setattr(generation, 'improve' if method == 'improveTeamInput' else 'generate', api_failure)
    assert call(method, value, transport)['error'] == {
        'code': -32602, 'message': 'Existing API execution failure'}
    assert api_calls == ['call'] and calls == ['send', 'cleanup']


@pytest.mark.macos_only
@pytest.mark.parametrize('improve', [True, False])
@pytest.mark.parametrize('outcome, reason', [('refused', 'unavailable'), ('invalid_output', 'rejected')])
def test_subscription_rpc_classification_follows_real_ledger_cleanup(
        activated, monkeypatch, improve, outcome, reason):
    from dataclasses import replace
    from eidolon_cli import organization_setup_subscription_admission as admission
    from tests.eidolon_cli.test_organization_setup_subscription_admission import request, state
    from tests.eidolon_cli.test_organization_setup_subscription_transport import install, wire, event, response

    ledger = activated
    monkeypatch.setattr(server, '_eidolon_home', ledger.home)
    ledger.transport.responses = Queue()
    ledger.transport.write = ledger.transport.responses.put
    socket, connects = install(monkeypatch, wire(event(response=response('synthetic-private invalid output'))))
    if outcome == 'refused':
        monkeypatch.setattr(admission, '_dispatch_job', lambda *args: False)
    cleanup = []
    preflight = methods.subscription_preflight
    def tracked_preflight(*args, **kwargs):
        evidence = preflight(*args, **kwargs)
        def release():
            evidence.release()
            cleanup.append('released')
        return replace(evidence, release=release)
    monkeypatch.setattr(methods, 'subscription_preflight', tracked_preflight)
    failed_job = admission._failed_job
    def record_failure(*args):
        result = failed_job(*args)
        assert result is True
        cleanup.append('persisted')
        return result
    monkeypatch.setattr(admission, '_failed_job', record_failure)
    original_error = server._err
    def after_cleanup(rid, code, message, data=None):
        if data == {'reason': reason}:
            assert cleanup == ['persisted', 'released']
            assert state(ledger)[0]['status'] == 'failed'
        return original_error(rid, code, message, data)
    monkeypatch.setattr(server, '_err', after_cleanup)
    value = request(ledger, improve=improve)
    method = 'improveTeamInput' if improve else 'generateTeam'
    result = call(method, value, ledger.transport)
    assert result['error'] == {'code': 5076, 'message': 'Team setup is unavailable',
                               'data': {'reason': reason}}
    row = state(ledger)[0]
    assert row['dispatched'] == int(outcome != 'refused') and row['result_json'] is None
    assert len(connects) == 1 and bool(socket.sent) is (outcome != 'refused')
    assert call(method, value, ledger.transport)['error']['code'] == 5076
    assert len(connects) == 1 and cleanup == ['persisted', 'released']
