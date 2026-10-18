"""Host-native registered RPC → protected store/auth → single synthetic TLS send."""
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
from queue import Queue

import pytest

from eidolon_cli.organization_setup_subscription_host import HostAuthority
from tests.eidolon_cli.subscription_native_fixture import (
    ACCESS, MODEL, SUGGESTIONS, TEAM, SyntheticEdges, seed_profile, snapshot,
)
from tui_gateway import organization_credential_transport as credentials


class Transport:
    closed = False

    def __init__(self):
        self.responses = Queue()

    def write(self, value):
        self.responses.put(value)


def call(transport, method, params):
    from tui_gateway import server

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
    # Gateway import starts a passive update daemon. Opt out through the real
    # config before importing it, so unrelated git/HTTP probes cannot race guards.
    (home / 'config.yaml').write_text('updates:\n  check: false\n', encoding='utf-8')
    options = getattr(request, 'param', False)
    options = options if type(options) is dict else {'legacy': options}
    identity = seed_profile(home, legacy=options.get('legacy', False))
    transport = Transport()
    edges = SyntheticEdges(home, recovery_fault=options.get('recovery_fault'),
                           availability_probe=options.get('availability_probe')).install()
    try:
        from tui_gateway import server
        from eidolon_cli import banner

        # Finish the real daemon under this profile before teardown can change
        # HERMES_HOME; no network prewarming or synthetic update result is used.
        assert banner._update_check_done.wait(timeout=10), 'Passive update check did not finish'
        monkeypatch.setattr(server, '_eidolon_home', home)
        monkeypatch.setattr(server, '_setup_subscription_authority', HostAuthority.capture(lambda: home, lambda: home))
        credentials.register_owner(transport, home)
        yield home, identity, transport, edges
    finally:
        credentials.retire_owner(transport)
        edges.close()


def test_synthetic_profile_disables_passive_update_checks(native, monkeypatch):
    """The supported opt-out finishes the real daemon without reaching probes."""
    import threading
    from eidolon_cli import banner
    from eidolon_cli.config import load_config

    home, _, _, edges = native
    assert load_config()['updates']['check'] is False
    probes = []
    monkeypatch.setattr(banner, '_check_via_rev', lambda revision: probes.append(revision))
    monkeypatch.setattr(banner, '_check_via_local_git', lambda directory: probes.append(directory))
    monkeypatch.setattr(banner, '_update_check_done', threading.Event())
    banner.prefetch_update_check()
    assert banner._update_check_done.wait(timeout=10), 'Passive update check did not finish'
    assert probes == []
    assert not (home / '.update_check').exists()
    assert [event for event in edges.events if event['kind'].startswith('denied')] == []


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
@pytest.mark.parametrize('native', [
    {'legacy': False, 'availability_probe': 'ancestor_churn_once'},
    {'legacy': True, 'availability_probe': 'scheduler_writer_once'},
], ids=['fresh', 'legacy-empty'], indirect=True)
def test_registered_native_subscription_full_journey(native):
    home, identity, transport, edges = native
    before = activate(native)
    first = inference(native, 'synthetic-improve', field='jobs')
    improved = ok(transport, 'improveTeamInput', first)
    assert improved['suggestion'] == SUGGESTIONS['jobs']
    probes = [event for event in edges.events if event['kind'] == 'availability-probe']
    assert len(probes) == 1 and probes[0]['triggered'] is True and probes[0]['restored'] is True
    assert probes[0]['renewed'] is (edges.availability_probe.kind == 'ancestor_churn_once')
    assert probes[0]['queued'] is (edges.availability_probe.kind == 'scheduler_writer_once')
    assert improved['generationAllowance']['setupUsage']['costUsd'] is None
    assert ok(transport, 'improveTeamInput', first) == improved
    assert sum(event['kind'] == 'responses' for event in edges.events) == 1
    assert snapshot(home)['teams'] == []
    # The native UI applies the first suggestion, then improves the other field.
    # Exercise that exact second-call boundary for fresh and legacy-empty homes.
    second = inference(native, 'synthetic-improve-goals', field='goals')
    second['jobs'] = SUGGESTIONS['jobs'] + ' Keep scope small.'
    second_improved = ok(transport, 'improveTeamInput', second)
    assert second_improved['suggestion'] == SUGGESTIONS['goals']
    assert second_improved['field'] == 'goals'
    assert second_improved['sourceRevision'] == second['sourceRevision']
    allowance = second_improved['generationAllowance']
    assert allowance['status'] == 'available'
    assert allowance['remainingCalls'] == 5 - len(before['generations'])
    assert allowance['remainingByOperation'] == {'improveTeamInput': 2, 'generateTeam': 3}
    usage = allowance['setupUsage']
    assert usage['tokenStatus'] == 'observed'
    assert (usage['inputTokens'], usage['outputTokens'], usage['totalTokens']) == (20, 40, 60)
    assert usage['costStatus'] == 'unknown' and usage['costUsd'] is None
    assert usage['subscriptionCalls'] == 2
    assert ok(transport, 'improveTeamInput', second) == second_improved
    after_improve = snapshot(home)
    assert after_improve['teams'] == []
    assert len(after_improve['outcomes']) == len(after_improve['dispatches']) == 2
    assert sum(event['kind'] == 'responses' for event in edges.events) == 2
    generated_input = inference(native, 'synthetic-generate')
    generated_input['jobs'] = second['jobs']
    generated = ok(transport, 'generateTeam', generated_input)
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
    assert [event['operation'] for event in sends] == ['improve_jobs', 'improve_goals', 'generate']
    assert all(event['durableDispatch'] for event in sends)
    assert [event for event in edges.events if event['kind'].startswith('denied')] == []
    assert len(final['outcomes']) == len(final['dispatches']) == len(sends)
    observations = json.dumps(final['outcomes'])
    assert ACCESS not in observations and all(text not in observations for text in SUGGESTIONS.values())
    assert 'resp_synthetic' not in observations
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
    assert [event for event in edges.events if event['kind'].startswith('denied')] == []
    setup = ok(transport, 'teamSetup', {'expectedOrganizationId': value['expectedOrganizationId']})
    assert setup['generationAllowance']['status'] == 'uncertain'
    assert setup['generationAllowance']['setupUsage']['costUsd'] is None


@pytest.mark.macos_only
@pytest.mark.parametrize('native', [{'legacy': True, 'recovery_fault': 'sqlite_dispatch_once'}], indirect=True)
def test_native_undispatched_failure_recovers_only_on_explicit_retry(native):
    home, _, transport, edges = native
    before = activate(native)
    first = inference(native, 'synthetic-unsent', field='jobs')
    refused = call(transport, 'improveTeamInput', first)
    assert refused['error']['code'] == 5076
    recovery = {'version': 1, 'disposition': 'retry_unsent',
        'idempotencyKey': first['idempotencyKey'], 'operation': 'improve_jobs',
        'sourceRevision': first['sourceRevision']}
    assert refused['error']['data']['recovery'] == recovery
    assert edges.recovery_fault.triggered == edges.recovery_fault.restored == 1
    failed = snapshot(home)
    old = next(row for row in failed['generations'] if row['idempotency_key'] == first['idempotencyKey'])
    assert old['status'] == 'failed' and old['dispatched'] == 0 and old['result_json'] is None
    assert failed['dispatches'] == failed['outcomes'] == []
    assert not any(event['kind'] == 'responses' for event in edges.events)
    # Passive check and retained-key retry prove the failure; neither creates
    # another intent. Only the next explicit request uses the recovery offer.
    setup = ok(transport, 'teamSetup', {'expectedOrganizationId': first['expectedOrganizationId']})
    assert setup['generationAllowance']['remainingCalls'] == 6
    assert call(transport, 'improveTeamInput', first)['error']['data']['recovery'] == recovery
    assert snapshot(home) == failed
    second = {**first, 'idempotencyKey': 'synthetic-explicit-unsent-retry'}
    improved = ok(transport, 'improveTeamInput', second)
    assert improved['suggestion'] == SUGGESTIONS['jobs']
    assert improved['generationAllowance']['remainingCalls'] == 5
    assert improved['generationAllowance']['setupUsage']['subscriptionCalls'] == 1
    after = snapshot(home)
    assert len(after['generations']) == 3
    assert next(row for row in after['generations'] if row['idempotency_key'] == first['idempotencyKey']) == old
    assert next(row for row in after['generations'] if row['idempotency_key'] == 'synthetic-legacy') == before['generations'][0]
    assert len(after['dispatches']) == len(after['outcomes']) == 1
    assert after['teams'] == [] and not any(after['counts'].values())
    assert ok(transport, 'improveTeamInput', second) == improved
    assert call(transport, 'improveTeamInput', first)['error']['code'] == 5076
    assert snapshot(home) == after
    assert sum(event['kind'] == 'responses' for event in edges.events) == 1
    assert not any(event['kind'].startswith('denied') for event in edges.events)


@pytest.mark.macos_only
@pytest.mark.parametrize('native', [{'recovery_fault': 'acl_publication_once'}], indirect=True)
def test_native_initial_publication_failure_recovers_before_new_consent(native):
    home, _, transport, edges = native
    value = scoped(native)
    before = snapshot(home)
    consent = {**value, 'expectedRevision': 0, 'confirmed': True, 'consentVersion': 1}
    assert ok(transport, 'beginSetupSubscription', consent)['stage'] == 'publication_uncertain'
    assert edges.recovery_fault.triggered == edges.recovery_fault.restored == 1
    initial = ok(transport, 'setupSubscriptionStatus', value)
    assert initial['connectionState'] == 'disconnected' and initial['cleanupPending'] is True
    from tui_gateway import methods_organization_setup_subscription as subscription
    host = subscription._services[transport][1]._host
    pending_attempt = host.inspect()
    assert pending_attempt.stage == 'publishing'
    exchanges = sum(event['kind'] == 'oauth-exchange' for event in edges.events)
    puts = edges.vault.put_count
    assert exchanges == puts == 1
    recovered = ok(transport, 'recoverSetupSubscriptionOperation', {**value, 'confirmed': True})
    assert recovered['state'] == 'local_retired'
    assert recovered['receipt']['remoteRevocation'] == 'unconfirmed'
    current = ok(transport, 'setupSubscriptionStatus', value)
    assert current['connectionState'] == 'disconnected' and current['cleanupPending'] is False
    assert current['stage'] == 'locally_retired'
    retired_attempt = host.inspect()
    assert retired_attempt.stage == 'locally_retired'
    assert retired_attempt.attempt_id == pending_attempt.attempt_id
    assert edges.vault.put_count == puts and edges.vault.values == {}
    assert sum(event['kind'] == 'oauth-exchange' for event in edges.events) == exchanges
    assert not any(event['kind'] in {'models', 'responses'} for event in edges.events)
    assert snapshot(home) == before
    # No restart, expiry advance, exchange replay or implicit reconnect.
    fresh = ok(transport, 'beginSetupSubscription', {**consent, 'expectedRevision': current['connectionRevision']})
    assert fresh['stage'] == 'connected'
    new_attempt = host.inspect()
    assert new_attempt.attempt_id != retired_attempt.attempt_id
    archive = host._io.directory / 'ticket-archive' / (host._name + '.' + retired_attempt.attempt_id + '.retired')
    archived = archive.read_bytes()
    assert json.loads(archived) == asdict(retired_attempt)
    connected = ok(transport, 'setupSubscriptionStatus', value)
    models = ok(transport, 'setupSubscriptionModels', {**value, 'expectedRevision': connected['connectionRevision']})
    assert models['models'] == [{'slug': MODEL, 'displayName': 'Synthetic subscription model'}]
    assert edges.vault.put_count == puts + 1
    assert sum(event['kind'] == 'oauth-exchange' for event in edges.events) == exchanges + 1
    assert sum(event['kind'] == 'models' for event in edges.events) == 1
    assert archive.read_bytes() == archived
    assert not any(event['kind'] == 'responses' or event['kind'].startswith('denied') for event in edges.events)
    assert snapshot(home) == before


def unsupported(native):
    home, identity, transport, edges = native
    before = snapshot(home)
    setup = ok(transport, 'teamSetup', {'expectedOrganizationId': identity['organizationId']})
    assert setup['setupSubscriptionState'] == 'unsupported_platform'
    assert setup['state'] == 'available'
    # No override of sys.platform, no synthetic=True store and no native vault write.
    assert snapshot(home) == before and edges.vault.values == {}
    assert len(edges.events) == 1, edges.events


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


@pytest.mark.macos_only
@pytest.mark.parametrize('native', [{'legacy': True, 'availability_probe': 'scheduler_cleanup_once'}], indirect=True)
def test_native_stranded_unsent_request_reconciles_before_explicit_retry(native):
    home, _, transport, edges = native
    before = activate(native)
    first = inference(native, 'synthetic-stranded-unsent', field='jobs')
    refused = call(transport, 'improveTeamInput', first)
    assert refused['error']['code'] == 5076
    assert refused['error']['data']['reason'] == 'unavailable'
    assert 'recovery' not in refused['error']['data']
    # Real cleanup has its own 5-second accounting deadline after the original
    # RPC can return. Wait for that exact refusal/unwind, not elapsed time proof.
    assert edges.availability_probe.finished.wait(10)
    probes = [event for event in edges.events if event['kind'] == 'availability-probe']
    assert len(probes) == 1
    assert all(probes[0][field] is True for field in ('triggered', 'queued', 'cleanupRefused', 'restored'))
    running = snapshot(home)
    old = next(row for row in running['generations'] if row['idempotency_key'] == first['idempotencyKey'])
    assert old['status'] == 'running' and old['dispatched'] == 0 and old['result_json'] is None
    assert running['dispatches'] == running['outcomes'] == []
    assert next(row for row in running['generations'] if row['idempotency_key'] == 'synthetic-legacy') == before['generations'][0]
    assert not any(event['kind'] == 'responses' for event in edges.events)
    counts = {kind: sum(event['kind'] == kind for event in edges.events)
              for kind in ('oauth-exchange', 'jwks', 'models', 'responses')}
    puts, deletes = edges.vault.put_count, edges.vault.delete_count
    check = {key: value for key, value in first.items() if key != 'field'}
    check.update(operation='improve_jobs', confirmed=True)
    recovery = {'version': 1, 'disposition': 'retry_unsent', 'operation': 'improve_jobs',
                'sourceRevision': first['sourceRevision'], 'idempotencyKey': first['idempotencyKey']}
    assert ok(transport, 'reconcileSetupSubscriptionRequest', check)['recovery'] == recovery
    checked = snapshot(home)
    expected = deepcopy(running)
    next(row for row in expected['generations'] if row['idempotency_key'] == first['idempotencyKey'])['status'] = 'failed'
    assert checked == expected  # Includes exact nonce, typed binding, debt/history.
    assert ok(transport, 'reconcileSetupSubscriptionRequest', check)['recovery'] == recovery
    assert snapshot(home) == checked
    assert (edges.vault.put_count, edges.vault.delete_count) == (puts, deletes)
    assert {kind: sum(event['kind'] == kind for event in edges.events) for kind in counts} == counts
    second = {**first, 'idempotencyKey': 'synthetic-explicit-reconciled-retry'}
    improved = ok(transport, 'improveTeamInput', second)
    assert improved['suggestion'] == SUGGESTIONS['jobs']
    assert improved['generationAllowance']['remainingCalls'] == 5
    assert improved['generationAllowance']['setupUsage']['subscriptionCalls'] == 1
    after = snapshot(home)
    assert len(after['generations']) == 3 and len(after['dispatches']) == len(after['outcomes']) == 1
    assert next(row for row in after['generations'] if row['idempotency_key'] == first['idempotencyKey']) == {**old, 'status': 'failed'}
    assert next(row for row in after['generations'] if row['idempotency_key'] == 'synthetic-legacy') == before['generations'][0]
    assert after['teams'] == [] and not any(after['counts'].values())
    assert ok(transport, 'improveTeamInput', second) == improved
    assert call(transport, 'improveTeamInput', first)['error']['code'] == 5076
    assert snapshot(home) == after
    assert sum(event['kind'] == 'responses' for event in edges.events) == 1
    assert not any(event['kind'].startswith('denied') for event in edges.events)
