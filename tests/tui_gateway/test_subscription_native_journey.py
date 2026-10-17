"""Host-native registered RPC → protected store/auth → single synthetic TLS send."""
from copy import deepcopy
import json
from pathlib import Path
from queue import Queue

import pytest

from eidolon_cli.organization_setup_subscription_host import HostAuthority
from tests.eidolon_cli.subscription_native_fixture import (
    ACCESS, MODEL, SUGGESTIONS, TEAM, SyntheticEdges, seed_profile, snapshot,
)
from tui_gateway import organization_credential_transport as credentials
import tui_gateway.server as server


class Transport:
    closed = False

    def __init__(self):
        self.responses = Queue()

    def write(self, value):
        self.responses.put(value)


def call(transport, method, params):
    result = server.dispatch({'jsonrpc': '2.0', 'id': 71,
        'method': 'organization.' + method, 'params': params}, transport=transport)
    return result if result is not None else transport.responses.get(timeout=30)


def ok(transport, method, params):
    response = call(transport, method, params)
    assert 'error' not in response, response
    return response['result']


@pytest.fixture
def native(tmp_path, monkeypatch, request):
    home = (tmp_path / 'profile').resolve()
    home.mkdir(mode=0o700)
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    (home / 'config.yaml').write_text('{}', encoding='utf-8')
    identity = seed_profile(home, legacy=getattr(request, 'param', False))
    monkeypatch.setattr(server, '_eidolon_home', home)
    monkeypatch.setattr(server, '_setup_subscription_authority', HostAuthority.capture(lambda: home, lambda: home))
    transport = Transport()
    credentials.register_owner(transport, home)
    edges = SyntheticEdges(home).install()
    try:
        yield home, identity, transport, edges
    finally:
        credentials.retire_owner(transport)
        edges.close()


def scoped(native):
    home, identity, transport, _ = native
    setup = ok(transport, 'teamSetup', {'expectedOrganizationId': identity['organizationId']})
    return {'expectedOrganizationId': identity['organizationId'], 'sourceRevision': setup['sourceRevision']}


def activate(native):
    home, identity, transport, edges = native
    value = scoped(native)
    before = snapshot(home)
    assert ok(transport, 'setupSubscriptionStatus', value)['connectionState'] == 'absent'
    assert snapshot(home) == before and len(edges.events) == 1
    consent = {**value, 'expectedRevision': 0, 'confirmed': True, 'consentVersion': 1}
    assert 'error' in call(transport, 'beginSetupSubscription', {**consent, 'confirmed': False})
    assert len(edges.events) == 1
    assert ok(transport, 'beginSetupSubscription', consent)['stage'] == 'connected'
    from tui_gateway import methods_organization_setup_subscription as subscription
    from eidolon_cli.organization_setup_subscription_auth import SubscriptionAuthService, TokenVerifier
    from eidolon_cli.organization_setup_subscription_store import SubscriptionStore
    service = subscription._services[transport][1]
    assert type(service) is SubscriptionAuthService and type(service._verifier) is TokenVerifier
    assert type(service._store) is SubscriptionStore and service._store._synthetic is False
    assert service._host._io._synthetic is False
    assert ok(transport, 'setupSubscriptionStatus', value)['connectionState'] == 'active'
    models = ok(transport, 'setupSubscriptionModels', {**value, 'expectedRevision': 1})
    assert models['models'] == [{'slug': MODEL, 'displayName': 'Synthetic subscription model'}]
    policy = ok(transport, 'setupSubscriptionPolicy', {'expectedOrganizationId': identity['organizationId']})
    candidate = {'version': 3, 'mode': 'subscription_calls', 'improveCallLimit': 4,
        'generationCallLimit': 3, 'totalCallLimit': 7, 'requestTimeoutSeconds': 120,
        'connectionId': models['connectionId'], 'authorizationEpoch': models['authorizationEpoch'], 'modelSlug': MODEL}
    params = {**value, 'expectedGeneration': policy['policyGeneration'], 'configuration': candidate}
    before_review = snapshot(home)
    preview = ok(transport, 'previewSetupSubscriptionPolicy', params)
    assert snapshot(home) == before_review
    assert preview['hardTokenGuarantee'] is False and preview['hardUsdGuarantee'] is False
    assert preview['retainedCallCounts']['legacy'] == len(before['generations'])
    apply = {**params, 'previewVersion': preview['previewVersion'],
             'idempotencyKey': 'synthetic-policy', 'confirmed': True,
             'acknowledgement': preview['acknowledgement']}
    assert 'error' in call(transport, 'applySetupSubscriptionPolicy', {**apply, 'confirmed': False})
    result = ok(transport, 'applySetupSubscriptionPolicy', apply)
    assert result['receipt']['appliedGeneration'] > result['receipt']['priorGeneration']
    after = snapshot(home)
    prior_execution, current_execution = before['executionPolicy'][0], after['executionPolicy'][0]
    assert current_execution['generation'] == prior_execution['generation'] + 1
    assert {key: value for key, value in current_execution.items() if key != 'generation'} == {
        key: value for key, value in prior_execution.items() if key != 'generation'}
    assert after['generations'] == before['generations']
    assert not any(event['kind'] == 'responses' for event in edges.events)
    assert scoped(native)['sourceRevision'] != value['sourceRevision']
    # Policy source turnover requires fresh account evidence; status never fetches it.
    model_reads = sum(event['kind'] == 'models' for event in edges.events)
    ok(transport, 'setupSubscriptionStatus', scoped(native))
    assert sum(event['kind'] == 'models' for event in edges.events) == model_reads
    ok(transport, 'setupSubscriptionModels', {**scoped(native), 'expectedRevision': 1})
    assert sum(event['kind'] == 'models' for event in edges.events) == model_reads + 1 == 2
    return before


def inference(native, key, *, field=None):
    params = {**scoped(native), 'jobs': 'Prepare studio drafts', 'goals': 'Deliver reviewed work',
              'namingStyle': 'Ash, Ember, Flint', 'idempotencyKey': key}
    return {**params, 'field': field} if field else params


@pytest.mark.macos_only
@pytest.mark.parametrize('native', [False, True], ids=['fresh', 'legacy-empty'], indirect=True)
def test_registered_native_subscription_full_journey(native):
    home, identity, transport, edges = native
    before = activate(native)
    first = inference(native, 'synthetic-improve', field='jobs')
    improved = ok(transport, 'improveTeamInput', first)
    assert improved['suggestion'] == SUGGESTIONS['jobs']
    assert improved['generationAllowance']['setupUsage']['costUsd'] is None
    assert ok(transport, 'improveTeamInput', first) == improved
    assert sum(event['kind'] == 'responses' for event in edges.events) == 1
    assert snapshot(home)['teams'] == []
    generated = ok(transport, 'generateTeam', inference(native, 'synthetic-generate'))
    assert generated['team'] == TEAM
    assert snapshot(home)['teams'] == []
    edited = deepcopy(generated['team'])
    edited['executive']['name'] = 'Ash Reviewed'
    review = {**scoped(native), 'team': edited}
    preview = ok(transport, 'previewTeam', review)
    assert snapshot(home)['teams'] == []
    create = {**review, 'previewVersion': preview['previewVersion'],
              'idempotencyKey': 'synthetic-create', 'confirmed': True}
    assert 'error' in call(transport, 'createTeam', {**create, 'confirmed': False})
    created = ok(transport, 'createTeam', create)
    assert created['state'] == 'created'
    assert ok(transport, 'createTeam', create) == created
    final = snapshot(home)
    assert len(final['teams']) == 1
    # Policy activation only advanced generation; explicit Create now changes roster settings.
    assert final['executionPolicy'][0]['settings'] != before['executionPolicy'][0]['settings']
    assert not any(final['counts'].values())
    assert any(row['name'] == 'Ash Reviewed' for row in final['agents'])
    sends = [event for event in edges.events if event['kind'] == 'responses']
    assert [event['operation'] for event in sends] == ['improve_jobs', 'generate']
    assert all(event['durableDispatch'] for event in sends)
    assert not any(event['kind'].startswith('denied') for event in edges.events)
    assert len(final['outcomes']) == len(final['dispatches']) == len(sends)
    observations = json.dumps(final['outcomes'])
    assert ACCESS not in observations and SUGGESTIONS['jobs'] not in observations and 'resp_synthetic' not in observations
    if before['generations']:
        assert next(row for row in final['generations'] if row['idempotency_key'] == 'synthetic-legacy') == before['generations'][0]
    # Tokens exist only in encrypted envelopes; never plaintext at rest.
    from eidolon_cli.organization_setup_subscription_store import PRIVATE_STORE_NAME
    private_store = home / PRIVATE_STORE_NAME
    assert private_store.is_dir()
    for path in private_store.rglob('*'):
        if path.is_file():
            assert ACCESS.encode() not in path.read_bytes()


@pytest.mark.macos_only
def test_native_uncertain_result_blocks_replay_and_new_intent(native):
    home, _, transport, edges = native
    activate(native)
    edges.mode = 'uncertain'
    value = inference(native, 'synthetic-uncertain', field='jobs')
    assert 'error' in call(transport, 'improveTeamInput', value)
    state = snapshot(home)
    assert len(state['generations']) == 1
    assert state['generations'][0]['status'] == 'uncertain'
    assert state['generations'][0]['dispatched'] == 1 and not state['outcomes']
    assert 'error' in call(transport, 'improveTeamInput', value)
    assert 'error' in call(transport, 'generateTeam', inference(native, 'synthetic-new-intent'))
    assert sum(event['kind'] == 'responses' for event in edges.events) == 1
    assert snapshot(home) == state
    assert not any(event['kind'].startswith('denied') for event in edges.events)
    setup = ok(transport, 'teamSetup', {'expectedOrganizationId': value['expectedOrganizationId']})
    assert setup['generationAllowance']['status'] == 'uncertain'
    assert setup['generationAllowance']['setupUsage']['costUsd'] is None


def unsupported(native):
    home, identity, transport, edges = native
    before = snapshot(home)
    setup = ok(transport, 'teamSetup', {'expectedOrganizationId': identity['organizationId']})
    assert setup['setupSubscriptionState'] == 'unsupported_platform'
    assert setup['state'] == 'available'
    # No override of sys.platform, no synthetic=True store and no native vault write.
    assert snapshot(home) == before and edges.vault.values == {} and len(edges.events) == 1


@pytest.mark.linux_only
def test_linux_native_store_remains_truthfully_unsupported(native):
    unsupported(native)


@pytest.mark.windows_only
def test_windows_native_store_remains_truthfully_unsupported(native):
    unsupported(native)


def test_synthetic_fixture_denies_nonlocal_sdk_socket_and_dns_paths(tmp_path):
    """Even accidental ordinary-provider fallback fails before external I/O."""
    import http.client
    import socket
    import httpx
    edges = SyntheticEdges(tmp_path)
    edges.install_network_guards()
    try:
        with pytest.raises(AssertionError, match='non-loopback'):
            socket.getaddrinfo('synthetic-provider.invalid', 443)
        with pytest.raises(AssertionError, match='non-loopback'):
            socket.gethostbyname('synthetic-provider.invalid')
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            with pytest.raises(AssertionError, match='non-loopback'):
                sock.connect(('203.0.113.1', 443))
            with pytest.raises(AssertionError, match='non-loopback'):
                sock.connect_ex(('203.0.113.1', 443))
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            with pytest.raises(AssertionError, match='non-loopback'):
                sock.sendto(b'synthetic', ('203.0.113.1', 443))
        connection = http.client.HTTPSConnection('synthetic-provider.invalid')
        try:
            with pytest.raises(AssertionError, match='non-loopback'):
                connection.request('GET', '/')
        finally:
            connection.close()
        with httpx.Client(trust_env=False) as client:
            with pytest.raises(AssertionError, match='non-loopback'):
                client.get('https://synthetic-provider.invalid/')
        assert len(edges.events) == 7
        assert all(event['kind'] == 'denied-network' and not event['physicalNetworkSend'] for event in edges.events)
    finally:
        edges.close()
