"""Real catalog transactions and synthetic inputs; no native credential APIs."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import json
import sqlite3
import threading
from types import SimpleNamespace
import uuid

import pytest

from eidolon_cli.organization_credential_handoff import ReplacementHandoffs
from eidolon_cli.organization_credential_security import (
    BackendState, CredentialError, Principal, SecureInputs,
)
from eidolon_cli.organization_credentials import CredentialCatalog


SECRET = 'synthetic-replacement-only-8b25dd'


def fields(**extra):
    return {'service': 'example', 'label': 'worker', 'type': 'token',
            'description': 'Synthetic credential', 'kind': 'secret', 'expiresAt': None, **extra}


class Backend:
    def __init__(self):
        self.writes = []
        self.state = 'available'

    def status(self):
        return BackendState('synthetic', self.state)

    def put(self, binding, value):
        self.writes.append((binding, value))
        return 'synthetic-reference-' + binding.operation_id


class Capsule:
    def __init__(self, catalog, principal, *, request_id='request-a', generation=None):
        self.catalog, self.principal = catalog, principal
        self.request_id, self.generation = request_id, generation or str(uuid.uuid4())
        self.retired = threading.Event()
        self.live = True
        self.lock = threading.Lock()

    def revalidate(self):
        if not self.live:
            raise CredentialError('lease_invalid')

    @contextmanager
    def lease_scope(self):
        with self.lock:
            self.revalidate()
            yield self.revalidate


@pytest.fixture
def setup(tmp_path):
    clock = [1000.0]
    backend = Backend()
    catalog = CredentialCatalog(tmp_path / 'credentials.db', 'org-a', backend=backend,
                                inputs=SecureInputs(clock=lambda: clock[0]), clock=lambda: clock[0])
    owner = Principal('org-a', 'owner-a', owner=True)
    worker = Principal('org-a', 'worker-a')
    entry = catalog.create(owner, fields(), operation_id='create')
    catalog.configure_grant(owner, actor_id=worker.actor_id, scope=entry['entryId'],
        permissions=['replace_value'], bindings=[], expected_revision=0, operation_id='grant')
    capsule = Capsule(catalog, worker)
    handoffs = ReplacementHandoffs()
    handoffs.register(capsule)
    transport = object()
    route = [True]
    def transport_guard():
        if not route[0]:
            raise CredentialError('owner_transport_unavailable')
    env = SimpleNamespace(clock=clock, backend=backend, catalog=catalog, owner=owner, worker=worker,
        entry=entry, capsule=capsule, handoffs=handoffs, transport=transport, route=route,
        transport_guard=transport_guard)
    yield env
    handoffs.retire_capsule(capsule)
    catalog.inputs.cancel_transport(transport)


def prepare(env, **overrides):
    args = dict(catalog=env.catalog, owner=env.owner, entry_id=env.entry['entryId'],
        expected_revision=1, actor_id=env.worker.actor_id, request_id=env.capsule.request_id,
        generation=env.capsule.generation, operation_id='prepare', transport=env.transport,
        value=SECRET, transport_guard=env.transport_guard, expected_grant_revision=1)
    return env.handoffs.prepare(**{**args, **overrides})


def replace(env, receipt, *, capsule=None, guard=None):
    capsule = capsule or env.capsule
    with capsule.lease_scope() as lease_guard:
        return env.handoffs.replace(capsule, receipt['handoffId'], guard or lease_guard)


def discover(env):
    with env.capsule.lease_scope() as guard:
        return env.handoffs.discover(env.capsule, guard)


def status(env):
    return env.handoffs.status(env.catalog, env.owner, 'prepare')


def cancel(env):
    return env.handoffs.cancel(env.catalog, env.owner, 'prepare')


def grant(env, permissions, revision, operation):
    return env.catalog.configure_grant(env.owner, actor_id=env.worker.actor_id,
        scope=env.entry['entryId'], permissions=permissions, bindings=[],
        expected_revision=revision, operation_id=operation)


@pytest.mark.parametrize('expected', [True, None, 0, 2])
def test_preparation_requires_exact_owner_reviewed_grant_revision(setup, expected):
    env = setup
    with pytest.raises(CredentialError, match='grant_revision_conflict'):
        prepare(env, expected_grant_revision=expected)
    assert not env.catalog.inputs._pending and not env.backend.writes
    assert status(env)['state'] == 'absent'


def test_grant_change_after_target_listing_requires_fresh_owner_review(setup):
    env = setup
    targets = env.handoffs.targets(env.catalog, env.owner, env.entry['entryId'], 1)
    assert targets[0]['grantRevision'] == 1
    grant(env, ['replace_value', 'discover'], 1, 'grant-change')
    with pytest.raises(CredentialError, match='grant_revision_conflict'):
        prepare(env, expected_grant_revision=targets[0]['grantRevision'])
    assert not env.catalog.inputs._pending and not env.backend.writes
    prepared = prepare(env, expected_grant_revision=2)
    assert prepared['grantRevision'] == 2
    with pytest.raises(CredentialError, match='idempotency_conflict'):
        prepare(env, expected_grant_revision=1)
    assert len(env.catalog.inputs._pending) == 1


def test_exact_live_target_success_once_and_no_value_or_handle_leaks(setup, caplog):
    env = setup
    assert status(env) == {'operationId': 'prepare', 'state': 'absent', 'receipt': None}
    targets = env.handoffs.targets(env.catalog, env.owner, env.entry['entryId'], 1)
    assert [(item['actorId'], item['requestId'], item['generation']) for item in targets] == [
        (env.worker.actor_id, env.capsule.request_id, env.capsule.generation)]
    prepared = prepare(env)
    handle = next(iter(env.catalog.inputs._pending))
    assert prepared['workerOperationId'] != prepared['operationId']
    assert handle not in (prepared['handoffId'], prepared['workerOperationId'])
    assert status(env) == {'operationId': 'prepare', 'state': 'pending', 'receipt': prepared}
    public = discover(env)
    assert public == [{key: prepared[key] for key in ('handoffId', 'entryId', 'expectedRevision', 'expiresAt')}]
    committed = replace(env, prepared)
    assert replace(env, prepared) == committed
    assert env.backend.writes[0][1] == SECRET.encode()
    assert len(env.backend.writes) == 1
    assert env.backend.writes[0][0].actor_id == env.worker.actor_id
    assert env.backend.writes[0][0].version == prepared['nextVersion']
    assert discover(env) == []
    assert status(env) == {'operationId': 'prepare', 'state': 'committed', 'receipt': committed}
    dumped = json.dumps([targets, prepared, public, committed, status(env), env.catalog.owner_details(env.owner)])
    forbidden = (SECRET, SECRET[:15], SECRET[-15:], hashlib.sha256(SECRET.encode()).hexdigest(), handle)
    for value in forbidden:
        assert value not in dumped + caplog.text
        assert value.encode() not in env.catalog.path.read_bytes()
    assert 'replacement_hash' not in dumped and 'inputHandle' not in dumped


def test_duplicate_does_not_process_new_value_or_refill_expired_input(setup):
    env = setup
    prepared = prepare(env)
    handle = next(iter(env.catalog.inputs._pending))
    original_expiry = env.catalog.inputs._pending[handle].expires
    env.clock[0] += 30
    assert prepare(env, value='different synthetic bytes') == prepared
    assert prepare(env, value=object()) == prepared
    assert env.catalog.inputs._pending[handle].expires == original_expiry
    committed = replace(env, prepared)
    assert env.backend.writes[0][1] == SECRET.encode()
    assert prepare(env, value='another synthetic input') == prepared
    assert status(env)['receipt'] == committed
    assert not env.catalog.inputs._pending


def test_duplicate_after_expiry_is_historical_and_cannot_refill(setup):
    env = setup
    prepared = prepare(env)
    env.clock[0] += 91
    assert prepare(env, value=object()) == prepared
    assert status(env)['state'] == 'unavailable'
    assert discover(env) == []
    with pytest.raises(CredentialError, match='handoff_expired'):
        replace(env, prepared)
    assert not env.backend.writes


@pytest.mark.parametrize('when', ['before_prepare', 'after_prepare', 'after_commit'])
def test_cancellation_fences_before_prepare_and_preserves_committed_truth(setup, when):
    env = setup
    prepared = prepare(env) if when != 'before_prepare' else None
    committed = replace(env, prepared) if when == 'after_commit' else None
    env.capsule.live = False
    grant(env, [], 1, 'revoke-grant')
    result = cancel(env)
    assert cancel(env) == result == status(env)
    if committed:
        assert result == {'operationId': 'prepare', 'state': 'committed', 'receipt': committed}
    else:
        assert result == {'operationId': 'prepare', 'state': 'cancelled', 'receipt': prepared}
        assert not env.catalog.inputs._pending
        env.capsule.live = True
        with pytest.raises(CredentialError, match='operation_cancelled'):
            prepare(env)
        assert not env.backend.writes
    audits = env.catalog.owner_details(env.owner)['audit']
    assert len([row for row in audits if row['action'] == 'cancel_replacement']) == (0 if committed else 1)


def test_cancelled_preparation_receipt_is_not_a_completed_replacement(setup):
    env = setup
    prepared = prepare(env)
    cancelled = cancel(env)
    assert cancelled['state'] == 'cancelled'
    assert env.catalog.receipt(env.owner, 'prepare') == prepared
    assert cancelled['receipt']['action'] == 'prepare_replacement'
    with pytest.raises(CredentialError, match='operation_cancelled'):
        replace(env, prepared)
    with env.catalog._connection() as conn:
        assert env.catalog._cancelled(conn, env.worker, prepared['workerOperationId'])


@pytest.mark.parametrize('change', ['grant_revoke', 'grant_regrant', 'cas', 'entry_revoke',
                                  'entry_expiry', 'lease', 'transport', 'retired', 'input_expiry'])
def test_pending_authority_changes_fail_closed(setup, change):
    env = setup
    if change == 'entry_expiry':
        env.entry = env.catalog.create(env.owner, fields(label='expires', expiresAt=1010), operation_id='expires')
        grant(env, ['replace_value'], 0, 'expires-grant')
    prepared = prepare(env)
    if change.startswith('grant_'):
        grant(env, [], 1, 'revoke-grant')
        if change == 'grant_regrant':
            grant(env, ['replace_value'], 2, 'regrant')
    elif change == 'cas':
        env.catalog.edit_metadata(env.owner, env.entry['entryId'], fields(description='Changed'),
                                  expected_revision=1, operation_id='edit')
    elif change == 'entry_revoke':
        env.catalog.revoke(env.owner, env.entry['entryId'], expected_revision=1, operation_id='revoke')
    elif change == 'entry_expiry':
        env.clock[0] += 11
    elif change == 'lease':
        env.capsule.live = False
    elif change == 'transport':
        env.route[0] = False
    elif change == 'retired':
        env.handoffs.retire_capsule(env.capsule)
    elif change == 'input_expiry':
        # Input loss is independent of durable authorization expiry.
        env.catalog.inputs.retire(next(iter(env.catalog.inputs._pending)))
    assert status(env)['state'] == 'unavailable'
    with pytest.raises(CredentialError):
        replace(env, prepared)
    assert not env.backend.writes
    assert cancel(env)['state'] == 'cancelled'
    assert not env.catalog.inputs._pending


def test_restart_and_forged_or_reassigned_capsules_do_not_inherit_input(setup):
    env = setup
    prepared = prepare(env)
    forged = Capsule(env.catalog, env.worker, generation=env.capsule.generation)
    with pytest.raises(CredentialError, match='worker_unavailable'):
        env.handoffs.register(forged)
    with pytest.raises(CredentialError, match='worker_unavailable'):
        replace(env, prepared, capsule=forged)
    restarted = ReplacementHandoffs()
    restarted.register(forged)
    assert restarted.status(env.catalog, env.owner, 'prepare')['state'] == 'unavailable'
    with forged.lease_scope() as guard:
        assert restarted.discover(forged, guard) == []
        with pytest.raises(CredentialError, match='handoff_unavailable'):
            restarted.replace(forged, prepared['handoffId'], guard)
    assert not env.backend.writes
    assert restarted.cancel(env.catalog, env.owner, 'prepare')['state'] == 'cancelled'
    with pytest.raises(CredentialError, match='operation_cancelled'):
        replace(env, prepared)


def test_committed_truth_survives_restart_retirement_and_grant_loss(setup):
    env = setup
    prepared = prepare(env)
    committed = replace(env, prepared)
    env.handoffs.retire_capsule(env.capsule)
    grant(env, [], 1, 'revoked')
    restarted = ReplacementHandoffs()
    assert restarted.status(env.catalog, env.owner, 'prepare') == {
        'operationId': 'prepare', 'state': 'committed', 'receipt': committed}
    assert restarted.cancel(env.catalog, env.owner, 'prepare')['receipt'] == committed
    assert len(env.backend.writes) == 1


def test_new_generation_retires_old_authority_without_old_cleanup_retiring_new(setup):
    env = setup
    prepared = prepare(env)
    next_capsule = Capsule(env.catalog, env.worker, request_id=env.capsule.request_id)
    env.handoffs.register(next_capsule)
    assert env.capsule.retired.is_set()
    assert not env.catalog.inputs._pending
    assert status(env)['state'] == 'unavailable'
    with pytest.raises(CredentialError, match='worker_unavailable'):
        replace(env, prepared)
    with pytest.raises(CredentialError, match='handoff_unavailable'):
        replace(env, prepared, capsule=next_capsule)
    env.handoffs.retire_capsule(env.capsule)
    targets = env.handoffs.targets(env.catalog, env.owner, env.entry['entryId'], 1)
    assert [item['generation'] for item in targets] == [next_capsule.generation]
    env.handoffs.retire_capsule(next_capsule)


def test_expired_and_consumed_memory_is_pruned_without_losing_durable_receipts(setup):
    env = setup
    first = prepare(env)
    env.clock[0] += 91
    second = prepare(env, operation_id='second')
    assert len(env.handoffs._prepared) == 1
    assert status(env)['state'] == 'unavailable'
    assert env.catalog.receipt(env.owner, 'prepare') == first
    committed = replace(env, second)
    assert not env.handoffs._prepared
    assert env.handoffs.status(env.catalog, env.owner, 'second')['receipt'] == committed


@pytest.mark.parametrize('committed', [False, True])
def test_lease_is_rechecked_after_waiting_for_catalog_writer(setup, monkeypatch, committed):
    env = setup
    prepared = prepare(env)
    if committed:
        replace(env, prepared)
    attempting = threading.Event()
    original = env.catalog._connection
    @contextmanager
    def observed(*, write=False):
        if write:
            attempting.set()
        with original(write=write) as conn:
            yield conn
    monkeypatch.setattr(env.catalog, '_connection', observed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with sqlite3.connect(env.catalog.path, isolation_level=None) as locked:
            locked.execute('BEGIN IMMEDIATE')
            replacing = pool.submit(replace, env, prepared)
            assert attempting.wait(10)
            env.capsule.live = False
            locked.commit()
        with pytest.raises(CredentialError, match='lease_invalid'):
            replacing.result(timeout=10)
    assert len(env.backend.writes) == int(committed)


def test_retirement_does_not_wait_for_catalog_writer(setup, monkeypatch):
    env = setup
    prepared = prepare(env)
    attempting, retired = threading.Event(), threading.Event()
    original = env.catalog._connection
    @contextmanager
    def observed(*, write=False):
        if write:
            attempting.set()
        with original(write=write) as conn:
            yield conn
    monkeypatch.setattr(env.catalog, '_connection', observed)
    def retire():
        env.handoffs.retire_capsule(env.capsule)
        retired.set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        with sqlite3.connect(env.catalog.path, isolation_level=None) as locked:
            locked.execute('BEGIN IMMEDIATE')
            replacing = pool.submit(replace, env, prepared)
            assert attempting.wait(10)
            retiring = pool.submit(retire)
            assert retired.wait(10)
            locked.commit()
        retiring.result(timeout=10)
        with pytest.raises(CredentialError, match='worker_unavailable'):
            replacing.result(timeout=10)
    assert not env.backend.writes


def test_agent_receipt_replay_requires_original_current_grant(setup):
    env = setup
    prepared = prepare(env)
    committed = replace(env, prepared)
    grant(env, [], 1, 'revoke')
    with pytest.raises(CredentialError, match='permission_denied'):
        replace(env, prepared)
    grant(env, ['replace_value'], 2, 'regrant')
    with pytest.raises(CredentialError, match='grant_revision_conflict'):
        replace(env, prepared)
    assert status(env)['receipt'] == committed
    assert cancel(env)['receipt'] == committed


def test_operation_collisions_and_cross_organization_are_rejected(setup, tmp_path):
    env = setup
    for action in (lambda: prepare(env, operation_id='create'),
                   lambda: env.handoffs.status(env.catalog, env.owner, 'create'),
                   lambda: env.handoffs.cancel(env.catalog, env.owner, 'create')):
        with pytest.raises(CredentialError, match='idempotency_conflict'):
            action()
    prepared = prepare(env)
    for changes in ({'entry_id': 'different'}, {'expected_revision': 2}, {'operation_id': prepared['workerOperationId']}):
        with pytest.raises(CredentialError, match='idempotency_conflict'):
            prepare(env, **changes)
    for action in (lambda: prepare(env, owner=env.worker),
                   lambda: env.handoffs.cancel(env.catalog, env.worker, 'prepare'),
                   lambda: env.handoffs.targets(env.catalog, env.worker, env.entry['entryId'], 1)):
        with pytest.raises(CredentialError, match='owner_required'):
            action()
    with pytest.raises(CredentialError, match='permission_denied'):
        cancel_other = Principal('org-a', 'other-owner', owner=True)
        env.handoffs.cancel(env.catalog, cancel_other, 'prepare')
    wrong = Principal('org-b', 'owner-a', owner=True)
    with pytest.raises(CredentialError, match='scope_denied'):
        prepare(env, owner=wrong)
    foreign = CredentialCatalog(tmp_path / 'foreign.db', 'org-b', backend=env.backend)
    with pytest.raises(CredentialError, match='worker_unavailable'):
        prepare(env, catalog=foreign, owner=wrong)
    aliased = CredentialCatalog(env.catalog.path, 'org-b', backend=env.backend)
    with pytest.raises(CredentialError, match='scope_denied'):
        prepare(env, catalog=aliased, owner=wrong)


def test_same_shape_colliding_worker_receipt_is_not_our_committed_result(setup):
    env = setup
    prepared = prepare(env)
    binding = env.catalog.value_binding(env.worker, env.entry['entryId'], expected_revision=1,
                                       operation_id=prepared['workerOperationId'])
    handle = env.catalog.inputs.submit(binding, env.transport, 'unrelated synthetic input')
    env.catalog.replace_value(env.worker, env.entry['entryId'], expected_revision=1,
        operation_id=prepared['workerOperationId'], input_handle=handle, transport=env.transport)
    for action in (lambda: replace(env, prepared), lambda: status(env), lambda: cancel(env)):
        with pytest.raises(CredentialError, match='idempotency_conflict'):
            action()


def test_immutable_mapping_and_rollback_discard_provisional_input(setup, monkeypatch):
    env = setup
    original = env.catalog._finish
    def fail(*args, **kwargs):
        raise CredentialError('storage_unavailable')
    monkeypatch.setattr(env.catalog, '_finish', fail)
    with pytest.raises(CredentialError, match='storage_unavailable'):
        prepare(env)
    assert not env.catalog.inputs._pending
    assert status(env)['state'] == 'absent'
    monkeypatch.setattr(env.catalog, '_finish', original)
    prepare(env)
    with sqlite3.connect(env.catalog.path) as conn:
        for sql in ('UPDATE credential_replacement_handoffs SET expires=999999',
                    'DELETE FROM credential_replacement_handoffs'):
            with pytest.raises(sqlite3.IntegrityError, match='Immutable credential handoff'):
                conn.execute(sql)


@pytest.mark.parametrize('phase', ['publication', 'consume'])
@pytest.mark.parametrize('fence', ['lease', 'transport', 'retired'])
def test_guards_run_at_actual_input_boundary(setup, monkeypatch, phase, fence):
    env = setup
    prepared = prepare(env) if phase == 'consume' else None
    method = 'submit' if phase == 'publication' else 'consume'
    original = getattr(env.catalog.inputs, method)
    def fenced(*args, **kwargs):
        if fence == 'lease':
            env.capsule.live = False
        elif fence == 'transport':
            env.route[0] = False
        else:
            env.capsule.retired.set()
        return original(*args, **kwargs)
    monkeypatch.setattr(env.catalog.inputs, method, fenced)
    with pytest.raises(CredentialError):
        replace(env, prepared) if prepared else prepare(env)
    assert not env.backend.writes
    if phase == 'publication':
        assert not env.catalog.inputs._pending
        assert status(env)['state'] == 'absent'


def test_entry_expiry_is_rechecked_at_consume_boundary(setup, monkeypatch):
    env = setup
    env.entry = env.catalog.create(env.owner, fields(label='expires', expiresAt=1010), operation_id='expires')
    grant(env, ['replace_value'], 0, 'expires-grant')
    prepared = prepare(env)
    original = env.catalog.inputs.consume
    def expire(*args, **kwargs):
        env.clock[0] = 1011
        return original(*args, **kwargs)
    monkeypatch.setattr(env.catalog.inputs, 'consume', expire)
    with pytest.raises(CredentialError, match='entry_expired'):
        replace(env, prepared)
    assert not env.backend.writes


def test_cancel_waits_for_started_put_and_reports_commit_without_native_rollback(setup, monkeypatch):
    env = setup
    prepared = prepare(env)
    started, release = threading.Event(), threading.Event()
    original = env.backend.put
    def paused(binding, value):
        started.set()
        assert release.wait(10)
        return original(binding, value)
    monkeypatch.setattr(env.backend, 'put', paused)
    with ThreadPoolExecutor(max_workers=2) as pool:
        replacing = pool.submit(replace, env, prepared)
        try:
            assert started.wait(10)
            cancelling = pool.submit(cancel, env)
            env.capsule.retired.set()
        finally:
            release.set()
        committed = replacing.result(timeout=10)
        assert cancelling.result(timeout=10) == {
            'operationId': 'prepare', 'state': 'committed', 'receipt': committed}
    assert len(env.backend.writes) == 1


def test_failed_started_put_is_not_retried_and_input_cannot_be_replenished(setup, monkeypatch):
    env = setup
    prepared = prepare(env)
    original = env.backend.put
    def orphan(binding, value):
        original(binding, value)
        raise RuntimeError(SECRET)
    monkeypatch.setattr(env.backend, 'put', orphan)
    with pytest.raises(CredentialError, match='backend_unavailable'):
        replace(env, prepared)
    assert status(env)['state'] == 'unavailable'
    assert prepare(env, value='another value') == prepared
    with pytest.raises(CredentialError, match='handoff_unavailable'):
        replace(env, prepared)
    assert len(env.backend.writes) == 1
    assert env.catalog.discover(env.owner)['entries'][0]['version'] == 0
    assert cancel(env)['state'] == 'cancelled'
