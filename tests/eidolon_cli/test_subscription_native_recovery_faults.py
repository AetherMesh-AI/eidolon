"""Portable local fault mechanics; whole native recovery stays host-gated."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import threading

import pytest

from eidolon_cli import organization_setup_subscription_admission as admission
from eidolon_cli import organization_setup_subscription_store as store
from eidolon_cli import organization_setup_subscription_filesystem as filesystem
from eidolon_cli import organization_setup_subscription_transport as transport
from eidolon_cli import organization_team_generation as generation
from tests.eidolon_cli import subscription_native_fixture as fixture
from tests.eidolon_cli.subscription_native_fixture import RecoveryFaults, SyntheticEdges


@pytest.fixture
def home(tmp_path):
    value = (tmp_path / 'profile').resolve()
    value.mkdir(mode=0o700)
    (value / 'organization').mkdir()
    with closing(sqlite3.connect(value / 'organization' / 'state.db')) as conn:
        conn.execute('CREATE TABLE synthetic_probe(value INTEGER)')
    return value


def test_sqlite_fault_uses_real_writer_releases_before_cleanup_and_is_one_shot(home, monkeypatch):
    faults = RecoveryFaults(home, 'sqlite_dispatch_once')
    body, errors = [], []

    def complete(_):
        try:
            with admission._ledger_connection(home, write=True):
                body.append(True)
        except sqlite3.OperationalError as error:
            errors.append(error.sqlite_errorcode & 0xff)
            raise
        return 'completed'

    monkeypatch.setattr(generation, 'complete_subscription', complete)
    faults.install(monkeypatch.setattr)
    # Scope-only objects never reach production inference/admission. The real
    # SQLite context above independently proves the lock and cleanup boundary.
    bound = object.__new__(admission._Admission)
    bound.home = home
    prepared = object.__new__(transport.PreparedSubscriptionSend)
    prepared._admission = bound
    with pytest.raises(sqlite3.OperationalError):
        generation.complete_subscription(prepared)
    assert errors == [sqlite3.SQLITE_BUSY] and body == []
    assert faults.triggered == faults.restored == 1
    with admission._ledger_connection(home, write=True) as conn:
        conn.execute('INSERT INTO synthetic_probe VALUES (1)')
    assert generation.complete_subscription(prepared) == 'completed'
    assert body == [True] and faults.triggered == 1
    faults.close()


def test_sqlite_fault_does_not_bind_wrong_home_or_wrong_prepared_type(home, tmp_path, monkeypatch):
    faults = RecoveryFaults(home, 'sqlite_dispatch_once')
    monkeypatch.setattr(generation, 'complete_subscription', lambda value: 'unchanged')
    faults.install(monkeypatch.setattr)
    assert generation.complete_subscription(object()) == 'unchanged'
    bound = object.__new__(admission._Admission)
    bound.home = tmp_path
    prepared = object.__new__(transport.PreparedSubscriptionSend)
    prepared._admission = bound
    assert generation.complete_subscription(prepared) == 'unchanged'
    assert faults.triggered == faults.restored == 0


requires_descriptor = pytest.mark.skipif(
    not hasattr(os, 'fchmod') or not hasattr(os, 'O_NOFOLLOW'),
    reason='Real descriptor/mode fault requires POSIX; no Windows ACL support claim',
)


def acl_rig(home, monkeypatch, *, target=None, worker=False, ancestor=False, wrong_code=False):
    target = target or home
    outcomes = []

    def decrypt(instance, state):
        def inspect():
            if wrong_code:
                fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
                try:
                    assert stat.S_IMODE(os.fstat(fd).st_mode) == 0o700
                finally:
                    os.close(fd)
            elif ancestor:
                filesystem._check_ancestor_acl(target, None, None)
            else:
                with filesystem._checked_acl_descriptor(target):
                    pass
            outcomes.append('descriptor_checked')
        if worker:
            failures = []
            def run():
                try:
                    inspect()
                except BaseException as error:
                    failures.append(error)
            thread = threading.Thread(target=run)
            thread.start()
            thread.join(5)
            assert not thread.is_alive() and not failures
        else:
            inspect()
        return 'unchanged'

    def connect(instance, binding, secret, state):
        return instance._decrypt(state)

    # Outer fixture-call harness is synthetic. The ancestor-only negative cases
    # substitute the unavailable Darwin C edge after real stable metadata gates.
    # Actual open/fstat/mode comparison/refusal and restoration are unchanged.
    if ancestor:
        monkeypatch.setattr(filesystem, '_read_darwin_acl',
            lambda api, fstatx, fd, before, **kwargs: (os.fstat(fd), None))
    monkeypatch.setattr(store.SubscriptionStore, '_decrypt', decrypt)
    monkeypatch.setattr(store.SubscriptionStore, '_connect_ticket_grant', connect)
    faults = RecoveryFaults(home, 'acl_publication_once')
    faults.install(monkeypatch.setattr)
    instance = object.__new__(store.SubscriptionStore)
    instance.profile = home
    return faults, instance, outcomes


@requires_descriptor
def test_acl_fault_refuses_real_prebinding_mode_mismatch_and_restores_once(home, monkeypatch):
    faults, instance, outcomes = acl_rig(home, monkeypatch)
    with pytest.raises(store.StoreError) as failure:
        instance._connect_ticket_grant(None, b'', {'phase': 'disconnected', 'revision': 0})
    assert failure.value.reason == 'unsafe_filesystem'
    assert not outcomes and faults.triggered == faults.restored == 1
    assert stat.S_IMODE(home.stat().st_mode) == 0o700 and faults._restore_fd is None
    assert instance._connect_ticket_grant(None, b'', {'phase': 'disconnected', 'revision': 0}) == 'unchanged'
    assert outcomes == ['descriptor_checked'] and faults.triggered == 1
    faults.close()
    assert faults.restored == 1


@requires_descriptor
@pytest.mark.parametrize('ancestor', [False, True])
@pytest.mark.parametrize('scope', ['wrong_path', 'wrong_thread', 'wrong_code', 'wrong_store', 'later_consent', 'not_consent'])
def test_acl_fault_is_limited_to_exact_initial_consent_store_home_and_thread(home, monkeypatch, scope, ancestor):
    other = home / 'other'
    other.mkdir(mode=0o700)
    faults, instance, outcomes = acl_rig(
        home, monkeypatch, target=other if scope == 'wrong_path' else None,
        worker=scope == 'wrong_thread', ancestor=ancestor, wrong_code=scope == 'wrong_code')
    if scope == 'wrong_store':
        instance.profile = other
    if scope == 'not_consent':
        assert instance._decrypt({}) == 'unchanged'
    else:
        assert instance._connect_ticket_grant(None, b'', {
            'phase': 'disconnected', 'revision': 1 if scope == 'later_consent' else 0}) == 'unchanged'
    assert outcomes == ['descriptor_checked'] and faults.triggered == faults.restored == 0
    assert stat.S_IMODE(home.stat().st_mode) == 0o700
    faults.close()


def test_recovery_events_export_only_fixed_checkpoint_counts_after_restoration(home):
    edges = SyntheticEdges(home, recovery_fault='sqlite_dispatch_once')
    edges.report_recovery_fault()
    assert edges.events == []
    edges.recovery_fault.triggered = edges.recovery_fault.restored = 1
    edges.vault.put_count = 1
    edges.report_recovery_fault()
    edges.report_recovery_fault()
    edges.vault.delete_count = 1
    edges.report_recovery_fault(checkpoint=True)
    assert edges.events == [
        {'kind': 'recovery-fault', 'physicalNetworkSend': False,
         'fault': 'sqlite_dispatch_once', 'triggered': True, 'restored': True,
         'nativePutCount': 1, 'nativeDeleteCount': 0},
        {'kind': 'recovery-checkpoint', 'physicalNetworkSend': False,
         'fault': 'sqlite_dispatch_once', 'triggered': True, 'restored': True,
         'nativePutCount': 1, 'nativeDeleteCount': 1},
    ]
    for invalid in [True, -1, 1001, 'synthetic-private-value']:
        edges.vault.put_count = invalid
        with pytest.raises(AssertionError):
            edges.report_recovery_fault(checkpoint=True)
    assert len(edges.events) == 2 and str(home) not in json.dumps(edges.events)


@pytest.mark.parametrize('tamper', [None, 'missing', 'duplicate', 'binding', 'nonce', 'request', 'current_body'])
def test_inference_guard_allows_unsent_history_but_requires_every_dispatched_marker(home, monkeypatch, tamper):
    edges = SyntheticEdges(home)
    body = json.dumps({'model': fixture.MODEL, 'instructions': generation.IMPROVE_SYSTEM,
        'input': [{'role': 'user', 'content': json.dumps({'field': 'jobs'})}],
        'stream': True, 'store': False}).encode()
    request = hashlib.sha256(body).hexdigest()
    attempt = {'idempotency_key': 'new', 'binding_digest': 'binding',
        'dispatch_nonce_digest': 'nonce', 'binding_json': json.dumps({'request_sha256': request})}
    marker = {'idempotency_key': 'new', 'binding_digest': 'binding',
        'dispatch_nonce_digest': 'nonce', 'request_sha256': request}
    evidence = {'generations': [
        {'idempotency_key': 'old', 'status': 'failed', 'dispatched': 0},
        {'idempotency_key': 'new', 'status': 'running', 'dispatched': 1, 'source_revision': 'source'}],
        'attempts': [dict(attempt, idempotency_key='old'), attempt],
        'dispatches': [marker], 'teams': [], 'counts': {}}
    if tamper == 'missing':
        evidence['dispatches'] = []
    elif tamper == 'duplicate':
        evidence['dispatches'].append(dict(marker))
    elif tamper in {'binding', 'nonce', 'request'}:
        marker[{'binding': 'binding_digest', 'nonce': 'dispatch_nonce_digest',
                'request': 'request_sha256'}[tamper]] = 'mismatch'
    elif tamper == 'current_body':
        marker['request_sha256'] = 'other-body'
        attempt['binding_json'] = json.dumps({'request_sha256': 'other-body'})
    monkeypatch.setattr(fixture, 'snapshot', lambda value: evidence)
    if tamper is None:
        assert b'response.completed' in edges.inference(body)
        assert len(edges.events) == 1 and edges.events[0]['durableDispatch'] is True
    else:
        with pytest.raises(AssertionError):
            edges.inference(body)
        assert edges.events == []


def ancestor_probe_rig(home, monkeypatch, *, target=None, worker=False, wrong_code=False, fail_renewal=False):
    from tests.eidolon_cli.subscription_native_fixture import AvailabilityProbe
    reads = []
    target = target or home.parent
    def native_edge(api, fstatx, fd, before, *, allow_directory_churn=False):
        reads.append(allow_directory_churn)
        if fail_renewal and not allow_directory_churn:
            raise ValueError('Synthetic read failure')
        return os.fstat(fd), b'synthetic-complete-ACL'
    def decrypt(instance, state):
        def inspect():
            if wrong_code:
                with filesystem._checked_acl_descriptor(target) as (fd, before):
                    filesystem._read_darwin_acl(None, None, fd, before, allow_directory_churn=True)
            else:
                filesystem._check_ancestor_acl(target, None, None)
        if worker:
            errors = []
            def run():
                try:
                    inspect()
                except BaseException as error:
                    errors.append(error)
            thread = threading.Thread(target=run)
            thread.start()
            thread.join(5)
            assert not thread.is_alive() and not errors
        else:
            inspect()
        return 'unchanged'
    def connect(instance, binding, secret, state):
        return instance._decrypt(state)
    # Linux mechanics substitute only the unavailable Darwin C edge. Actual
    # mkdir/open/fstat, production ancestor comparison/revalidation and scope
    # wrappers remain real. Hosted macOS uses the real native ACL reader.
    monkeypatch.setattr(filesystem, '_read_darwin_acl', native_edge)
    monkeypatch.setattr(store.SubscriptionStore, '_decrypt', decrypt)
    monkeypatch.setattr(store.SubscriptionStore, '_connect_ticket_grant', connect)
    probe = AvailabilityProbe(home, 'ancestor_churn_once')
    probe.install(monkeypatch.setattr)
    instance = object.__new__(store.SubscriptionStore)
    instance.profile = home
    return probe, instance, reads


@requires_descriptor
def test_ancestor_probe_renews_real_sibling_change_once_then_cleans_outside_check(home, monkeypatch):
    probe, instance, reads = ancestor_probe_rig(home, monkeypatch)
    try:
        before = home.parent.stat()
        assert instance._connect_ticket_grant(None, b'', {'phase': 'disconnected', 'revision': 0}) == 'unchanged'
        assert reads == [True, False] and probe.triggered and probe.renewed
        assert not probe.restored and probe._child.is_dir()
        after = home.parent.stat()
        assert filesystem._directory_identity(before) == filesystem._directory_identity(after)
        assert before.st_nlink != after.st_nlink
        probe.close()
        assert probe.restored and not probe._child.exists()
        assert instance._connect_ticket_grant(None, b'', {'phase': 'disconnected', 'revision': 0}) == 'unchanged'
        assert reads == [True, False, True] and not probe._child.exists()
    finally:
        probe.close()


@requires_descriptor
@pytest.mark.parametrize('scope', ['wrong_path', 'wrong_thread', 'wrong_code', 'wrong_store', 'later_consent', 'not_consent'])
def test_ancestor_probe_does_not_mutate_outside_exact_initial_consent_scope(home, monkeypatch, scope):
    other = home / 'other'
    other.mkdir(mode=0o700)
    probe, instance, reads = ancestor_probe_rig(home, monkeypatch,
        target=other if scope == 'wrong_path' else None,
        worker=scope == 'wrong_thread', wrong_code=scope == 'wrong_code')
    try:
        if scope == 'wrong_store':
            instance.profile = other
        if scope == 'not_consent':
            assert instance._decrypt({}) == 'unchanged'
        else:
            assert instance._connect_ticket_grant(None, b'', {
                'phase': 'disconnected', 'revision': 1 if scope == 'later_consent' else 0}) == 'unchanged'
        assert reads == [True] and not probe.triggered and not probe._child.exists()
    finally:
        probe.close()


@requires_descriptor
def test_ancestor_probe_removes_real_sibling_after_failed_renewal(home, monkeypatch):
    probe, instance, reads = ancestor_probe_rig(home, monkeypatch, fail_renewal=True)
    try:
        with pytest.raises(ValueError):
            instance._connect_ticket_grant(None, b'', {'phase': 'disconnected', 'revision': 0})
        assert reads == [True, False] and probe.triggered and not probe.renewed
    finally:
        probe.close()
    assert probe.restored and not probe._child.exists()


def test_scheduler_probe_uses_real_same_ledger_writer_and_causal_queue_release(home, monkeypatch):
    from tests.eidolon_cli.subscription_native_fixture import AvailabilityProbe
    body = []
    def dispatch(selected_home):
        with admission._ledger_connection(selected_home, write=True) as connection:
            connection.execute('INSERT INTO synthetic_probe VALUES (1)')
            body.append(True)
        return 'completed'
    def complete(prepared):
        return admission._dispatch_job(prepared._admission.home)
    # Only the outer call harness is synthetic. Both independent real SQLite
    # writers, their shared coordinator, queue and release condition are actual.
    monkeypatch.setattr(admission, '_dispatch_job', dispatch)
    monkeypatch.setattr(generation, 'complete_subscription', complete)
    probe = AvailabilityProbe(home, 'scheduler_writer_once')
    probe.install(monkeypatch.setattr)
    bound = object.__new__(admission._Admission)
    bound.home = home
    prepared = object.__new__(transport.PreparedSubscriptionSend)
    prepared._admission = bound
    try:
        assert generation.complete_subscription(prepared) == 'completed'
        assert probe.triggered and probe.queued and probe.restored and not probe.renewed
        assert probe._holder is not None and not probe._holder.is_alive()
        first_holder = probe._holder
        assert generation.complete_subscription(prepared) == 'completed'
        assert body == [True, True] and probe._holder is first_holder
        with closing(sqlite3.connect(home / 'organization' / 'state.db')) as connection:
            assert connection.execute('SELECT count(*) FROM synthetic_probe').fetchone()[0] == 2
    finally:
        probe.close()


def test_scheduler_probe_wrong_type_and_home_do_not_start_writer(home, tmp_path, monkeypatch):
    from tests.eidolon_cli.subscription_native_fixture import AvailabilityProbe
    monkeypatch.setattr(generation, 'complete_subscription', lambda prepared: 'unchanged')
    probe = AvailabilityProbe(home, 'scheduler_writer_once')
    probe.install(monkeypatch.setattr)
    bound = object.__new__(admission._Admission)
    bound.home = tmp_path
    prepared = object.__new__(transport.PreparedSubscriptionSend)
    prepared._admission = bound
    try:
        assert generation.complete_subscription(object()) == 'unchanged'
        assert generation.complete_subscription(prepared) == 'unchanged'
        assert not probe.triggered and probe._holder is None
    finally:
        probe.close()


def test_prolonged_scheduler_probe_waits_for_actual_cleanup_deadline_before_release(home, monkeypatch):
    import time
    from tests.eidolon_cli.subscription_native_fixture import AvailabilityProbe
    from eidolon_cli import organization_ledger_writer as writers
    binding = object()
    checks, errors = [], []
    def dispatch(selected_home):
        with admission._ledger_connection(selected_home, write=True,
                deadline=time.monotonic() + writers.PUBLICATION_WAIT_SECONDS):
            pytest.fail('Held writer must exclude the dispatch body')
    def complete(prepared):
        return admission._dispatch_job(prepared._admission.home)
    monkeypatch.setattr(admission, '_dispatch_job', dispatch)
    monkeypatch.setattr(generation, 'complete_subscription', complete)
    probe = AvailabilityProbe(home, 'scheduler_cleanup_once')
    probe.on_finished = lambda: checks.append((probe.cleanup_refused, probe.queued, probe.restored))
    probe.install(monkeypatch.setattr)
    bound = object.__new__(admission._Admission)
    bound.home, bound.binding = home, binding
    prepared = object.__new__(transport.PreparedSubscriptionSend)
    prepared._admission = bound
    def cleanup():
        try:
            # The actual accounting function must fail before reading the scope-
            # only binding; its real 5-second deadline is never shortened.
            admission._failed_job(home, binding, 'synthetic-nonce', False)
        except BaseException as error:
            errors.append(error)
    worker = threading.Thread(target=cleanup)
    try:
        with pytest.raises(writers.LedgerWriterUnavailable) as refusal:
            generation.complete_subscription(prepared)
        assert refusal.value.reason == 'deadline'
        assert probe.triggered and probe.queued and not probe.restored
        probe.finish_operation()  # Earlier RPC unwind must not release the gate.
        assert not probe._release.is_set() and not probe.finished.is_set() and not checks
        worker.start()
        assert probe.finished.wait(writers.ACCOUNTING_WAIT_SECONDS + 5)
        worker.join(5)
        assert not worker.is_alive() and len(errors) == 1
        assert type(errors[0]) is writers.LedgerWriterUnavailable and errors[0].reason == 'deadline'
        assert checks == [(True, True, True)]
        probe.finish_operation()
        assert checks == [(True, True, True)]
        with admission._ledger_connection(home, write=True) as connection:
            connection.execute('INSERT INTO synthetic_probe VALUES (1)')
    finally:
        probe.close()
        if worker.ident is not None:
            worker.join(5)
            assert not worker.is_alive()


def test_prolonged_scheduler_probe_teardown_releases_holder_without_fabricating_cleanup(home, monkeypatch):
    from tests.eidolon_cli.subscription_native_fixture import AvailabilityProbe
    monkeypatch.setattr(generation, 'complete_subscription', lambda prepared: 'harness-return')
    probe = AvailabilityProbe(home, 'scheduler_cleanup_once')
    probe.install(monkeypatch.setattr)
    bound = object.__new__(admission._Admission)
    bound.home, bound.binding = home, object()
    prepared = object.__new__(transport.PreparedSubscriptionSend)
    prepared._admission = bound
    try:
        assert generation.complete_subscription(prepared) == 'harness-return'
        assert probe.triggered and not probe.restored
    finally:
        probe.close()
    assert probe.restored and not probe.cleanup_refused and not probe.finished.is_set()
    with admission._ledger_connection(home, write=True) as connection:
        connection.execute('INSERT INTO synthetic_probe VALUES (1)')


def test_scheduler_probe_constructor_preserves_existing_setup_ledger_and_policy(home, monkeypatch):
    from tests.eidolon_cli.subscription_native_fixture import AvailabilityProbe
    (home / 'config.yaml').write_text('model: {}\nupdates:\n  check: false\n')
    fixture.seed_profile(home, legacy=True)
    before = fixture.snapshot(home)
    probe = AvailabilityProbe(home, 'scheduler_writer_once')
    try:
        probe.install(monkeypatch.setattr)
        assert fixture.snapshot(home) == before
        assert not probe.triggered and probe._holder is None
    finally:
        probe.close()


def test_prolonged_probe_ignores_other_cleanup_home_and_binding(home, tmp_path, monkeypatch):
    from tests.eidolon_cli.subscription_native_fixture import AvailabilityProbe
    from eidolon_cli import organization_ledger_writer as writers
    def failed(*args):
        raise writers.LedgerWriterUnavailable('deadline')
    monkeypatch.setattr(admission, '_failed_job', failed)
    monkeypatch.setattr(generation, 'complete_subscription', lambda prepared: None)
    probe = AvailabilityProbe(home, 'scheduler_cleanup_once')
    probe.install(monkeypatch.setattr)
    bound = object.__new__(admission._Admission)
    bound.home, bound.binding = home, object()
    prepared = object.__new__(transport.PreparedSubscriptionSend)
    prepared._admission = bound
    try:
        generation.complete_subscription(prepared)
        for selected_home, binding in [(tmp_path, bound.binding), (home, object())]:
            with pytest.raises(writers.LedgerWriterUnavailable):
                admission._failed_job(selected_home, binding, 'synthetic-nonce', False)
            assert not probe.cleanup_refused and not probe._release.is_set()
            assert not probe.restored and not probe.finished.is_set()
    finally:
        probe.close()


def test_reconciliation_checkpoint_is_scoped_post_return_and_bounded(home, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from eidolon_cli import organization_setup_subscription_reconciliation as reconciliation
    (home / 'config.yaml').write_text('model: {}\nupdates:\n  check: false\n')
    fixture.seed_profile(home, legacy=True)
    edges = SyntheticEdges(home, availability_probe='scheduler_cleanup_once')
    observed = []
    def reconcile(selected_home, params, *, transport):
        observed.append(sum(event['kind'] == 'reconciliation-checkpoint' for event in edges.events))
        if params.get('raise'):
            raise ValueError('Synthetic private exception')
        return {'unchanged': True}
    monkeypatch.setattr(reconciliation, 'reconcile_unsent_request', reconcile)
    edges.install()
    try:
        edges.availability_probe._binding = SimpleNamespace(idempotency_key='synthetic-private-original-key')
        for selected_home, params in [(tmp_path, {'idempotencyKey': 'synthetic-private-original-key'}),
                                      (home, {'idempotencyKey': 'synthetic-private-other-key'})]:
            assert reconciliation.reconcile_unsent_request(selected_home, params, transport=None) == {'unchanged': True}
        assert not any(event['kind'] == 'reconciliation-checkpoint' for event in edges.events)
        with pytest.raises(ValueError):
            reconciliation.reconcile_unsent_request(home,
                {'idempotencyKey': 'synthetic-private-original-key', 'raise': True}, transport=None)
        assert observed == [0, 0, 0]
        edges.vault.put_count, edges.vault.delete_count = 2, 1
        assert reconciliation.reconcile_unsent_request(home,
            {'idempotencyKey': 'synthetic-private-original-key'}, transport=None) == {'unchanged': True}
        checkpoints = [event for event in edges.events if event['kind'] == 'reconciliation-checkpoint']
        assert checkpoints == [{'kind': 'reconciliation-checkpoint', 'physicalNetworkSend': False,
                               'nativePutCount': 2, 'nativeDeleteCount': 1}]
        assert observed[-1] == 0 and 'synthetic-private-' not in json.dumps(checkpoints)
        for invalid in [True, -1, 1001, 'synthetic-private-value']:
            edges.vault.put_count = invalid
            with pytest.raises(AssertionError):
                edges.native_checkpoint_counts()
    finally:
        edges.close()


@requires_descriptor
def test_acl_fault_refuses_real_ancestor_security_change_before_native_acl_read(home, monkeypatch):
    reads = []
    def native(*args, **kwargs):
        reads.append(True)
        pytest.fail('Changed directory security identity must refuse before native ACL access')
    def decrypt(instance, state):
        filesystem._check_ancestor_acl(home, None, None)
    def connect(instance, binding, secret, state):
        return instance._decrypt(state)
    monkeypatch.setattr(filesystem, '_read_darwin_acl', native)
    monkeypatch.setattr(store.SubscriptionStore, '_decrypt', decrypt)
    monkeypatch.setattr(store.SubscriptionStore, '_connect_ticket_grant', connect)
    faults = RecoveryFaults(home, 'acl_publication_once')
    faults.install(monkeypatch.setattr)
    instance = object.__new__(store.SubscriptionStore)
    instance.profile = home
    try:
        with pytest.raises(store.StoreError) as failure:
            instance._connect_ticket_grant(None, b'', {'phase': 'disconnected', 'revision': 0})
        assert failure.value.reason == 'unsafe_filesystem'
        assert reads == [] and faults.triggered == faults.restored == 1
        assert stat.S_IMODE(home.stat().st_mode) == 0o700 and faults._restore_fd is None
    finally:
        faults.close()


@requires_descriptor
@pytest.mark.parametrize('ancestor', [False, True])
def test_acl_fault_rejects_structurally_equal_but_distinct_helper_code(home, monkeypatch, ancestor):
    from contextlib import contextmanager
    from types import FunctionType
    original = (filesystem._check_ancestor_acl.__code__ if ancestor
                else filesystem._checked_acl_descriptor.__wrapped__.__code__)
    copied = original.replace()
    assert copied == original and copied is not original
    duplicate = FunctionType(copied, vars(filesystem))
    if ancestor:
        monkeypatch.setattr(filesystem, '_read_darwin_acl',
            lambda api, fstatx, fd, before, **kwargs: (os.fstat(fd), None))
    def decrypt(instance, state):
        if ancestor:
            duplicate(home, None, None)
        else:
            with contextmanager(duplicate)(home):
                pass
    def connect(instance, binding, secret, state):
        return instance._decrypt(state)
    monkeypatch.setattr(store.SubscriptionStore, '_decrypt', decrypt)
    monkeypatch.setattr(store.SubscriptionStore, '_connect_ticket_grant', connect)
    faults = RecoveryFaults(home, 'acl_publication_once')
    faults.install(monkeypatch.setattr)
    instance = object.__new__(store.SubscriptionStore)
    instance.profile = home
    try:
        instance._connect_ticket_grant(None, b'', {'phase': 'disconnected', 'revision': 0})
        assert faults.triggered == faults.restored == 0
        assert stat.S_IMODE(home.stat().st_mode) == 0o700
    finally:
        faults.close()
