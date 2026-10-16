"""Real durable catalog/broker paths; all value material is synthetic."""
from dataclasses import asdict
import hashlib
import json
import logging
from pathlib import Path
import threading

import pytest

from eidolon_cli.organization_credential_security import BackendState, CredentialError, Principal, SecureInputs
from eidolon_cli.organization_credentials import CredentialCatalog
from eidolon_cli.organization_credential_broker import CredentialBroker, TrustedAdapter

SECRET = 'synthetic-only-private-value-7cf093'


class MemoryBackend:
    def __init__(self):
        self.values = {}
        self.state = 'available'
    def status(self):
        return BackendState('test-memory', self.state)
    def put(self, binding, value):
        reference = 'opaque-' + binding.operation_id
        self.values[reference] = (binding, value)
        return reference
    def consume(self, reference, binding, consumer):
        if self.state != 'available':
            raise CredentialError('backend_' + self.state)
        stored, value = self.values[reference]
        assert stored == binding
        consumer(value)


def fields(**overrides):
    return {'service': 'github', 'label': 'eidolonReleases', 'type': 'apiKey',
            'description': 'Publish the selected repository release', 'kind': 'secret', 'expiresAt': None, **overrides}


@pytest.fixture
def setup(tmp_path):
    backend = MemoryBackend()
    catalog = CredentialCatalog(tmp_path / 'catalog.db', 'org-a', backend=backend)
    owner = Principal('org-a', 'owner-a', owner=True)
    worker = Principal('org-a', 'worker-a')
    entry = catalog.create(owner, fields(), operation_id='create')
    return catalog, backend, owner, worker, entry


def grant(catalog, owner, worker, entry, permissions, *, revision=0, bindings=None, operation='grant'):
    return catalog.configure_grant(owner, actor_id=worker.actor_id, scope=entry['entryId'],
        permissions=permissions, bindings=bindings or [], expected_revision=revision, operation_id=operation)


def put(catalog, principal, entry, *, operation='value', value=SECRET, revision=1, transport=None):
    transport = transport or object()
    binding = catalog.value_binding(principal, entry['entryId'], expected_revision=revision, operation_id=operation)
    handle = catalog.inputs.submit(binding, transport, value)
    result = catalog.replace_value(principal, entry['entryId'], expected_revision=revision,
        operation_id=operation, input_handle=handle, transport=transport)
    return result, handle, transport


def test_metadata_is_durable_scoped_named_and_never_a_value_interface(setup, monkeypatch):
    catalog, backend, owner, worker, entry = setup
    monkeypatch.setenv('github_eidolonReleases_apiKey', 'host-value-must-not-be-used')
    assert catalog.discover(owner)['entries'][0]['status'] == 'missing'
    assert catalog.discover(worker)['entries'] == []
    grant(catalog, owner, worker, entry, ['discover'])
    row = catalog.discover(worker)['entries'][0]
    assert row['name'] == 'github_eidolonReleases_apiKey'
    assert row['version'] == 0
    assert row['id'] == entry['entryId']
    assert catalog.create(owner, fields(), operation_id='create') == entry
    with pytest.raises(CredentialError, match='idempotency_conflict'):
        catalog.create(owner, fields(description='Different'), operation_id='create')
    with pytest.raises(CredentialError, match='scope_denied'):
        catalog.discover(Principal('other-org', 'owner-a', owner=True))
    with pytest.raises(CredentialError, match='scope_denied'):
        CredentialCatalog(catalog.path, 'other-org').discover(Principal('other-org', 'owner-a', owner=True))
    with pytest.raises(CredentialError, match='invalid_metadata'):
        catalog.create(owner, {**fields(), 'value': SECRET}, operation_id='raw')
    with pytest.raises(CredentialError, match='invalid_metadata'):
        catalog.create(owner, {**fields(), 'environmentMapping': 'LD_PRELOAD'}, operation_id='env')
    assert CredentialCatalog(catalog.path, 'org-a').discover(worker)['entries'][0]['id'] == row['id']


def test_management_permissions_are_independent_no_self_grant_and_cas(setup):
    catalog, backend, owner, worker, entry = setup
    for action in (
        lambda: catalog.create(worker, fields(), operation_id='worker-create'),
        lambda: catalog.edit_metadata(worker, entry['entryId'], fields(), expected_revision=1, operation_id='edit'),
        lambda: catalog.value_binding(worker, entry['entryId'], expected_revision=1, operation_id='value'),
        lambda: catalog.revoke(worker, entry['entryId'], expected_revision=1, operation_id='revoke'),
        lambda: grant(catalog, worker, worker, entry, ['discover']),
    ):
        with pytest.raises(CredentialError):
            action()
    grant(catalog, owner, worker, entry, ['edit_metadata'])
    result = catalog.edit_metadata(worker, entry['entryId'], fields(label='repositoryRead'), expected_revision=1, operation_id='rename')
    assert result['entryId'] == entry['entryId']
    assert catalog.discover(worker)['entries'] == []
    assert catalog.discover(owner)['entries'][0]['name'] == 'github_repositoryRead_apiKey'
    with pytest.raises(CredentialError, match='revision_conflict'):
        catalog.edit_metadata(worker, entry['entryId'], fields(), expected_revision=1, operation_id='stale')
    with pytest.raises(CredentialError, match='classification_change_denied'):
        catalog.edit_metadata(worker, entry['entryId'], fields(kind='config'), expected_revision=2, operation_id='downgrade')
    with pytest.raises(CredentialError, match='permission_denied'):
        catalog.revoke(worker, entry['entryId'], expected_revision=2, operation_id='revoke')


def test_secure_input_exact_binding_single_use_expiry_and_no_value_derived_leaks(setup, caplog):
    catalog, backend, owner, worker, entry = setup
    binding = catalog.value_binding(owner, entry['entryId'], expected_revision=1, operation_id='value')
    transport = object()
    handle = catalog.inputs.submit(binding, transport, SECRET)
    with pytest.raises(CredentialError, match='secure_input_invalid'):
        catalog.replace_value(owner, entry['entryId'], expected_revision=1, operation_id='value', input_handle=handle, transport=object())
    result = catalog.replace_value(owner, entry['entryId'], expected_revision=1, operation_id='value', input_handle=handle, transport=transport)
    assert catalog.replace_value(owner, entry['entryId'], expected_revision=1, operation_id='value', input_handle=handle, transport=transport) == result
    with pytest.raises(CredentialError, match='secure_input_invalid'):
        catalog.inputs.consume(handle, binding, transport, lambda value: None)
    dumped = json.dumps(catalog.owner_details(owner)) + repr(result) + caplog.text
    for forbidden in (SECRET, SECRET[:12], SECRET[-12:], hashlib.sha256(SECRET.encode()).hexdigest(), handle, 'opaque-value'):
        assert forbidden not in dumped
        if forbidden != 'opaque-value':
            assert forbidden.encode() not in catalog.path.read_bytes()
    clock = [0]
    inputs = SecureInputs(clock=lambda: clock[0])
    expired = inputs.submit(binding, transport, SECRET)
    clock[0] = 91
    with pytest.raises(CredentialError, match='secure_input_invalid'):
        inputs.consume(expired, binding, transport, lambda value: None)
    cancelled = inputs.submit(binding, transport, SECRET)
    inputs.cancel_transport(transport)
    with pytest.raises(CredentialError):
        inputs.consume(cancelled, binding, transport, lambda value: None)


def test_broker_exact_scope_rechecks_rotation_revocation_and_sanitizes_errors(setup, caplog):
    catalog, backend, owner, worker, entry = setup
    put(catalog, owner, entry)
    seen = []
    broker = CredentialBroker(catalog, [TrustedAdapter('github.release', 'repo-a', lambda value: seen.append(value))])
    grant(catalog, owner, worker, entry, ['use'], bindings=[{'tool': 'github.release', 'destination': 'repo-a'}])
    assert catalog.discover(worker)['entries'] == []
    for options in ({'tool': 'ssh.exec', 'destination': 'repo-a'}, {'tool': 'github.release', 'destination': 'repo-b'}):
        with pytest.raises(CredentialError):
            broker.queue(worker, entry['entryId'], version=1, operation_id='bad', **options)
    queued = broker.queue(worker, entry['entryId'], version=1, tool='github.release', destination='repo-a', operation_id='queued')
    put(catalog, owner, entry, revision=2, operation='rotate', value='synthetic-second')
    with pytest.raises(CredentialError, match='version_conflict'):
        broker.run(worker, queued['useId'])
    next_use = broker.queue(worker, entry['entryId'], version=2, tool='github.release', destination='repo-a', operation_id='next')
    revoked = catalog.revoke(owner, entry['entryId'], expected_revision=3, operation_id='revoke')
    assert revoked['queuedCancelled'] == 2
    assert broker.run(worker, next_use['useId'])['state'] == 'cancelled'
    assert not seen
    with pytest.raises(CredentialError, match='entry_revoked'):
        broker.queue(worker, entry['entryId'], version=2, tool='github.release', destination='repo-a', operation_id='after-revoke')
    removed = catalog.revoke(owner, entry['entryId'], expected_revision=4, operation_id='remove', remove=True)
    assert removed['storedValueDeleted'] is False
    assert catalog.discover(owner)['entries'] == []
    assert catalog.owner_details(owner)['tombstones'][0]['id'] == entry['entryId']


def test_inflight_use_is_not_claimed_cancelled_and_adapter_output_is_discarded(setup, caplog):
    catalog, backend, owner, worker, entry = setup
    put(catalog, owner, entry)
    grant(catalog, owner, worker, entry, ['use'], bindings=[{'tool': 'test.call', 'destination': 'exact'}])
    started, finish = threading.Event(), threading.Event()
    def invoke(value):
        assert value == SECRET.encode()
        started.set()
        assert finish.wait(10)
        raise RuntimeError(SECRET)
    broker = CredentialBroker(catalog, [TrustedAdapter('test.call', 'exact', invoke)])
    use = broker.queue(worker, entry['entryId'], version=1, tool='test.call', destination='exact', operation_id='use')
    result = []
    thread = threading.Thread(target=lambda: result.append(broker.run(worker, use['useId'])))
    thread.start()
    try:
        assert started.wait(10)
        assert broker.run(worker, use['useId'])['state'] == 'started'
        revoked = catalog.revoke(owner, entry['entryId'], expected_revision=2, operation_id='revoke')
        assert revoked['inFlight'] == 1 and revoked['providerRevoked'] is False
    finally:
        finish.set(); thread.join(10)
    assert result == [{'useId': use['useId'], 'state': 'failed', 'revokedDuringUse': True, 'providerRevoked': False}]
    assert SECRET not in caplog.text + repr(result)
    assert catalog.discover(owner)['entries'][0]['inFlight'] == 0


def test_unavailable_locked_and_expired_fail_closed(setup):
    catalog, backend, owner, worker, entry = setup
    for state in ('locked', 'unavailable'):
        backend.state = state
        with pytest.raises(CredentialError, match='backend_' + state):
            put(catalog, owner, entry)
        assert catalog.discover(owner)['backend']['state'] == state
    unavailable = CredentialCatalog(catalog.path, 'org-a')
    with pytest.raises(CredentialError, match='backend_unavailable'):
        unavailable.value_binding(owner, entry['entryId'], expected_revision=1, operation_id='value')
    backend.state = 'available'
    expired = catalog.create(owner, fields(label='expired', expiresAt=1), operation_id='expired')
    put(catalog, owner, expired, operation='expired-value')
    broker = CredentialBroker(catalog, [TrustedAdapter('test.call', 'exact', lambda value: None)])
    with pytest.raises(CredentialError, match='entry_expired'):
        broker.queue(owner, expired['entryId'], version=1, tool='test.call', destination='exact', operation_id='expired-use')


def test_fenced_owner_cancellation_blocks_delayed_mutation_and_fresh_input(setup):
    catalog, backend, owner, worker, entry = setup
    cancelled = catalog.cancel_operation(owner, 'delayed')
    assert cancelled == {'operationId': 'delayed', 'state': 'cancelled', 'receipt': None}
    assert catalog.cancel_operation(owner, 'delayed') == cancelled
    with pytest.raises(CredentialError, match='operation_cancelled'):
        catalog.edit_metadata(owner, entry['entryId'], fields(description='Late reply'), expected_revision=1, operation_id='delayed')
    with pytest.raises(CredentialError, match='operation_cancelled'):
        catalog.value_binding(owner, entry['entryId'], expected_revision=1, operation_id='delayed')
    with pytest.raises(CredentialError, match='operation_cancelled'):
        grant(catalog, owner, worker, entry, ['discover'], operation='delayed')
    with pytest.raises(CredentialError, match='owner_required'):
        catalog.cancel_operation(worker, 'worker-cancel')
    committed = catalog.cancel_operation(owner, 'create')
    assert committed == {'operationId': 'create', 'state': 'committed', 'receipt': entry}
    assert catalog.discover(owner)['entries'][0]['revision'] == 1
    assert len([row for row in catalog.owner_details(owner)['audit'] if row['action'] == 'cancel_operation']) == 1


def test_cancellation_waits_for_native_write_and_reports_committed_truth(setup):
    from concurrent.futures import ThreadPoolExecutor
    catalog, backend, owner, worker, entry = setup
    started, release = threading.Event(), threading.Event()
    original = backend.put
    def paused_put(binding, value):
        started.set()
        assert release.wait(10)
        return original(binding, value)
    backend.put = paused_put
    with ThreadPoolExecutor(max_workers=2) as pool:
        replacing = pool.submit(put, catalog, owner, entry, operation='in-flight')
        assert started.wait(10)
        cancelling = pool.submit(catalog.cancel_operation, owner, 'in-flight')
        release.set()
        receipt, _, _ = replacing.result(timeout=10)
        assert cancelling.result(timeout=10) == {'operationId': 'in-flight', 'state': 'committed', 'receipt': receipt}
    assert len(backend.values) == 1


def test_cancelled_operation_cannot_consume_an_already_prepared_handle(setup):
    catalog, backend, owner, worker, entry = setup
    operation = 'prepared-then-cancelled'
    binding = catalog.value_binding(owner, entry['entryId'], expected_revision=1, operation_id=operation)
    transport = object()
    handle = catalog.inputs.submit(binding, transport, SECRET)
    catalog.cancel_operation(owner, operation)
    with pytest.raises(CredentialError, match='operation_cancelled'):
        catalog.replace_value(owner, entry['entryId'], expected_revision=1, operation_id=operation,
            input_handle=handle, transport=transport)
    assert not backend.values
    observed = []
    catalog.inputs.consume(handle, binding, transport, lambda value: observed.append(value == SECRET.encode()))
    assert observed == [True]  # The delayed operation never consumed the input.
    assert catalog.discover(owner)['entries'][0]['version'] == 0
