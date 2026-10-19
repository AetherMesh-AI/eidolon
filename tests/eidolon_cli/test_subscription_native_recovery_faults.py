"""Portable local fault mechanics; whole native recovery stays host-gated."""
from contextlib import closing, contextmanager
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


def acl_rig(home, monkeypatch, *, target=None, worker=False, ancestor=False, wrong_code=False,
            fault_kind='acl_publication_once', native_calls=None):
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
        def native_read(api, fstatx, fd, before, **kwargs):
            if native_calls is not None:
                native_calls.append(True)
            return os.fstat(fd), None
        monkeypatch.setattr(filesystem, '_read_darwin_acl', native_read)
    monkeypatch.setattr(store.SubscriptionStore, '_decrypt', decrypt)
    monkeypatch.setattr(store.SubscriptionStore, '_connect_ticket_grant', connect)
    faults = RecoveryFaults(home, fault_kind)
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


def ancestor_probe_rig(home, monkeypatch, *, target=None, worker=False, wrong_code=False, fail_renewal=False, kind='ancestor_churn_once', read_fds=None):
    from tests.eidolon_cli.subscription_native_fixture import AvailabilityProbe
    reads = []
    target = target or home.parent
    def native_edge(api, fstatx, fd, before, *, allow_directory_churn=False):
        reads.append(allow_directory_churn)
        if read_fds is not None:
            read_fds.append(fd)
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
    probe = AvailabilityProbe(home, kind)
    probe.install(monkeypatch.setattr)
    instance = object.__new__(store.SubscriptionStore)
    instance.profile = home
    return probe, instance, reads


@requires_descriptor
@pytest.mark.parametrize('kind', ['ancestor_churn_once', 'ancestor_final_path_once'])
def test_ancestor_probe_renews_real_sibling_change_once_then_cleans_before_connect_returns(home, monkeypatch, kind):
    fds = []
    probe, instance, reads = ancestor_probe_rig(home, monkeypatch, kind=kind, read_fds=fds)
    try:
        before = home.parent.stat()
        assert instance._connect_ticket_grant(None, b'', {'phase': 'disconnected', 'revision': 0}) == 'unchanged'
        assert reads == [True, False] and probe.triggered and probe.renewed
        assert len(fds) == 2 and fds[0] == fds[1]
        assert probe._local.churn_pending is None and probe._local.injected_fd is None
        assert probe.restored and not probe._child.exists()
        after = home.parent.stat()
        assert filesystem._directory_identity(before) == filesystem._directory_identity(after)
        assert before.st_nlink == after.st_nlink
        probe.close()
        assert probe.restored and not probe._child.exists()
        assert instance._connect_ticket_grant(None, b'', {'phase': 'disconnected', 'revision': 0}) == 'unchanged'
        assert reads == [True, False, True] and not probe._child.exists()
    finally:
        probe.close()


@requires_descriptor
@pytest.mark.parametrize('scope', ['wrong_path', 'wrong_thread', 'wrong_code', 'wrong_store', 'later_consent', 'not_consent'])
@pytest.mark.parametrize('kind', ['ancestor_churn_once', 'ancestor_final_path_once'])
def test_ancestor_probe_does_not_mutate_outside_exact_initial_consent_scope(home, monkeypatch, scope, kind):
    other = home / 'other'
    other.mkdir(mode=0o700)
    probe, instance, reads = ancestor_probe_rig(home, monkeypatch,
        target=other if scope == 'wrong_path' else None,
        worker=scope == 'wrong_thread', wrong_code=scope == 'wrong_code', kind=kind)
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
@pytest.mark.parametrize('kind', ['ancestor_churn_once', 'ancestor_final_path_once'])
def test_ancestor_probe_removes_real_sibling_after_failed_renewal(home, monkeypatch, kind):
    probe, instance, reads = ancestor_probe_rig(home, monkeypatch, fail_renewal=True, kind=kind)
    try:
        with pytest.raises(ValueError):
            instance._connect_ticket_grant(None, b'', {'phase': 'disconnected', 'revision': 0})
        assert reads == [True, False] and probe.triggered and not probe.renewed
        assert probe.restored and not probe._child.exists()
    finally:
        probe.close()
    assert probe.restored and not probe._child.exists()


@requires_descriptor
def test_ancestor_probe_reporting_cannot_mutate_later_real_acl_observation(home, monkeypatch):
    import sys
    probe, instance, reads = ancestor_probe_rig(home, monkeypatch)
    edges = SyntheticEdges(home)
    edges.availability_probe = probe
    ready, finished = threading.Event(), threading.Event()
    failures, observed = [], []

    def report():
        try:
            assert ready.wait(5)
            edges.report_availability_probe()
        except BaseException as error:
            failures.append(error)
        finally:
            finished.set()

    original_lstat = Path.lstat
    expected_code = filesystem._check_ancestor_acl.__code__
    def lstat(path, *args, **kwargs):
        frame = sys._getframe(1)
        if (not observed and path == home.parent and frame.f_code is expected_code
                and 'final_fd' in frame.f_locals and 'final_path' not in frame.f_locals):
            before = frame.f_locals['final_fd']
            ready.set()
            assert finished.wait(5) and not failures
            after = original_lstat(path, *args, **kwargs)
            observed.append(tuple(a != b for a, b in zip(
                filesystem._file_identity(before), filesystem._file_identity(after))))
            return after
        return original_lstat(path, *args, **kwargs)

    worker = threading.Thread(target=report)
    try:
        assert instance._connect_ticket_grant(None, b'', {'phase': 'disconnected', 'revision': 0}) == 'unchanged'
        assert reads == [True, False]
        worker.start()
        with monkeypatch.context() as patch:
            patch.setattr(Path, 'lstat', lstat)
            filesystem._check_ancestor_acl(home.parent, None, None)
        assert observed == [(False,) * 7]
        assert reads == [True, False, True]  # No second renewal was needed.
        assert probe.restored and not probe._child.exists()
        assert len(edges.events) == 1 and edges.events[0]['restored'] is True
        edges.report_availability_probe()
        assert len(edges.events) == 1
    finally:
        ready.set()
        if worker.ident is not None:
            worker.join(5)
            assert not worker.is_alive() and not failures
        probe.close()


@requires_descriptor
@pytest.mark.parametrize('mutation', ['replacement', 'nonempty'])
def test_ancestor_probe_cleanup_refuses_changed_child_without_recursive_deletion(home, monkeypatch, mutation):
    probe, instance, _ = ancestor_probe_rig(home, monkeypatch)
    original_cleanup = probe._cleanup_ancestor
    displaced = home.parent / 'preserved-original-sibling'

    def change_then_cleanup():
        if probe.triggered and not probe.restored:
            if mutation == 'replacement':
                probe._child.rename(displaced)
                probe._child.mkdir(mode=0o700)
            else:
                (probe._child / 'preserved-child').mkdir(mode=0o700)
        original_cleanup()

    monkeypatch.setattr(probe, '_cleanup_ancestor', change_then_cleanup)
    try:
        with pytest.raises(AssertionError):
            instance._connect_ticket_grant(None, b'', {'phase': 'disconnected', 'revision': 0})
        assert probe.triggered and not probe.restored and probe._child.is_dir()
        assert (displaced if mutation == 'replacement' else probe._child / 'preserved-child').is_dir()
    finally:
        # Explicit test-owned empty directories only; the fixture never recurses.
        import errno
        pinned_fd = probe._parent_fd
        monkeypatch.setattr(probe, '_cleanup_ancestor', original_cleanup)
        with pytest.raises(AssertionError):
            probe.close()
        assert probe._parent_fd is None
        with pytest.raises(OSError) as closed:
            os.fstat(pinned_fd)
        assert closed.value.errno == errno.EBADF
        if mutation == 'nonempty':
            (probe._child / 'preserved-child').rmdir()
        probe._child.rmdir()
        if displaced.exists():
            displaced.rmdir()


@requires_descriptor
def test_ancestor_probe_cleanup_refuses_rebound_parent_path_and_closes_pinned_fd(home, monkeypatch):
    probe, instance, _ = ancestor_probe_rig(home, monkeypatch)
    original_cleanup = probe._cleanup_ancestor
    parent = home.parent
    displaced = parent.with_name(parent.name + '-preserved')
    def replace_parent():
        parent.rename(displaced)
        parent.mkdir(mode=0o700)
        original_cleanup()
    monkeypatch.setattr(probe, '_cleanup_ancestor', replace_parent)
    try:
        with pytest.raises(AssertionError):
            instance._connect_ticket_grant(None, b'', {'phase': 'disconnected', 'revision': 0})
        assert probe.triggered and not probe.restored
        assert (displaced / probe._child.name).is_dir()
        assert not (parent / probe._child.name).exists()
    finally:
        parent.rmdir()  # The replacement is deliberately empty and test-owned.
        displaced.rename(parent)
        monkeypatch.setattr(probe, '_cleanup_ancestor', original_cleanup)
        probe.close()
    assert probe.restored and probe._parent_fd is None


@requires_descriptor
def test_ancestor_probe_cleanup_finishes_inside_real_outer_store_lock(home, monkeypatch):
    probe, instance, _ = ancestor_probe_rig(home, monkeypatch)
    store.SubscriptionStore.__init__(instance, home, backend=object())
    # Isolate grant/state parsing only. _connect_payload and its real flock
    # remain intact, including the wrapper's exact placement before release.
    monkeypatch.setattr(instance, '_validate', lambda *_: None)
    monkeypatch.setattr(instance, '_cas', lambda *_: None)
    monkeypatch.setattr(instance, '_state', lambda: {'phase': 'disconnected', 'revision': 0, 'binding': None})
    ready, finished = threading.Event(), threading.Event()
    failures, outcomes = [], []
    other = store.SubscriptionStore(home, backend=object())

    def inspect():
        try:
            assert ready.wait(5)
            with pytest.raises(store.StoreError) as error:
                with other._locked(nonblocking=True):
                    pytest.fail('Initial consent must still hold the real store lock')
            outcomes.append(error.value.reason)
        except BaseException as error:
            failures.append(error)
        finally:
            finished.set()

    original_cleanup = probe._cleanup_ancestor
    def checked_cleanup():
        original_cleanup()
        assert probe.restored and not probe._child.exists()
        ready.set()
        assert finished.wait(5) and not failures

    monkeypatch.setattr(probe, '_cleanup_ancestor', checked_cleanup)
    worker = threading.Thread(target=inspect)
    worker.start()
    try:
        assert instance._connect_payload(None, store._GRANT_PREFIX + b'synthetic', 0) == 'unchanged'
        assert outcomes == ['connection_unavailable']
        with other._locked():
            assert probe.restored and not probe._child.exists()
    finally:
        ready.set()
        worker.join(5)
        assert not worker.is_alive() and not failures
        original_cleanup()
        monkeypatch.setattr(probe, '_cleanup_ancestor', original_cleanup)
        probe.close()


@requires_descriptor
@pytest.mark.parametrize('authority', ['code_clone', 'globals_copy'])
def test_final_path_probe_rejects_nonexact_helper_authority(home, monkeypatch, authority):
    from types import FunctionType
    probe, instance, reads = ancestor_probe_rig(home, monkeypatch, kind='ancestor_final_path_once')
    original = filesystem._check_ancestor_acl
    code = original.__code__.replace() if authority == 'code_clone' else original.__code__
    namespace = dict(vars(filesystem)) if authority == 'globals_copy' else vars(filesystem)
    monkeypatch.setattr(filesystem, '_check_ancestor_acl', FunctionType(code, namespace))
    try:
        assert instance._connect_ticket_grant(None, b'', {'phase': 'disconnected', 'revision': 0}) == 'unchanged'
        assert reads == [True] and not probe.triggered and not probe._child.exists()
        assert probe._local.churn_pending is None and probe._local.injected_fd is None
    finally:
        probe.close()


@requires_descriptor
def test_final_path_probe_clears_pending_fd_when_observation_exits_before_path_read(home, monkeypatch):
    import sys
    probe, instance, reads = ancestor_probe_rig(home, monkeypatch, kind='ancestor_final_path_once')
    original_fstat = os.fstat
    expected_code = filesystem._check_ancestor_acl.__code__
    interrupted = []
    def fstat(fd):
        caller = sys._getframe(1)
        if (caller.f_code is expected_code and caller.f_globals is vars(filesystem)
                and caller.f_lineno == 83 and getattr(probe._local, 'churn_pending', None) is not None):
            interrupted.append(True)
            raise OSError('Synthetic interruption before final path')
        return original_fstat(fd)
    monkeypatch.setattr(os, 'fstat', fstat)
    try:
        with pytest.raises(OSError):
            instance._connect_ticket_grant(None, b'', {'phase': 'disconnected', 'revision': 0})
        assert interrupted == [True] and reads == [True]
        assert not probe.triggered and not probe._child.exists()
        assert probe._local.churn_pending is None and probe._local.injected_fd is None
        home.parent.lstat()  # A later unrelated read cannot reuse the old FD.
        assert not probe.triggered
    finally:
        probe.close()


def test_final_path_probe_source_site_is_first_path_observation_after_final_fd():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(filesystem))
    helper = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == '_check_ancestor_acl')
    assignments = [node for node in ast.walk(helper) if isinstance(node, ast.Assign)]
    first_fd = next(node for node in assignments if any(isinstance(target, ast.Name) and target.id == 'final_fd' for target in node.targets))
    first_path = next(node for node in assignments if any(isinstance(target, ast.Name) and target.id == 'final_path' for target in node.targets))
    assert first_fd.lineno == 83 and first_path.lineno == 84
    assert ast.unparse(first_fd.value) == 'os.fstat(fd)'
    assert ast.unparse(first_path.value) == 'Path(path).lstat()'


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


@requires_descriptor
def test_open_recheck_fault_refuses_second_real_change_before_acl_and_restores_once(home, monkeypatch):
    import errno
    import inspect
    native_calls = []
    faults, instance, outcomes = acl_rig(home, monkeypatch, target=home.parent, ancestor=True,
        fault_kind='acl_open_recheck_once', native_calls=native_calls)
    pinned_fd = inspect.getclosurevars(faults._close_open_recheck).nonlocals['parent_fd']
    with pytest.raises(store.StoreError) as failure:
        instance._connect_ticket_grant(None, b'', {'phase': 'disconnected', 'revision': 0})
    assert failure.value.reason == 'unsafe_filesystem'
    assert faults.triggered == faults.restored == 1 and native_calls == [] and outcomes == []
    assert not (home.parent / 'synthetic-open-recheck-sibling').exists()
    with pytest.raises(OSError) as closed:
        os.fstat(pinned_fd)
    assert closed.value.errno == errno.EBADF
    assert instance._connect_ticket_grant(None, b'', {'phase': 'disconnected', 'revision': 0}) == 'unchanged'
    assert native_calls == [True] and outcomes == ['descriptor_checked']
    faults.close()
    faults.close()
    assert faults.triggered == faults.restored == 1


@requires_descriptor
@pytest.mark.parametrize('scope', ['wrong_path', 'wrong_thread', 'wrong_code', 'wrong_store', 'later_consent', 'not_consent'])
def test_open_recheck_fault_is_limited_to_exact_initial_consent(home, monkeypatch, scope):
    other = home / 'other'
    other.mkdir(mode=0o700)
    faults, instance, outcomes = acl_rig(home, monkeypatch, ancestor=True,
        target=other if scope == 'wrong_path' else home.parent, worker=scope == 'wrong_thread',
        wrong_code=scope == 'wrong_code', fault_kind='acl_open_recheck_once')
    if scope == 'wrong_store':
        instance.profile = other
    if scope == 'not_consent':
        assert instance._decrypt({}) == 'unchanged'
    else:
        assert instance._connect_ticket_grant(None, b'', {
            'phase': 'disconnected', 'revision': 1 if scope == 'later_consent' else 0}) == 'unchanged'
    assert faults.triggered == faults.restored == 0 and outcomes == ['descriptor_checked']
    faults.close()


@requires_descriptor
@pytest.mark.parametrize('authority', ['code_clone', 'globals_copy'])
def test_open_recheck_fault_rejects_nonexact_helper_identity(home, monkeypatch, authority):
    from types import FunctionType
    faults, instance, outcomes = acl_rig(home, monkeypatch, target=home.parent,
        ancestor=True, fault_kind='acl_open_recheck_once')
    original = filesystem._check_ancestor_acl
    altered = FunctionType(original.__code__.replace() if authority == 'code_clone' else original.__code__,
        dict(vars(filesystem)) if authority == 'globals_copy' else vars(filesystem))
    monkeypatch.setattr(filesystem, '_check_ancestor_acl', altered)
    assert instance._connect_ticket_grant(None, b'', {'phase': 'disconnected', 'revision': 0}) == 'unchanged'
    assert faults.triggered == faults.restored == 0 and outcomes == ['descriptor_checked']
    faults.close()


@requires_descriptor
@pytest.mark.parametrize('mutation', ['skip_recheck', 'replacement', 'nonempty'])
def test_open_recheck_fault_finally_restores_or_refuses_changed_child_and_closes_fd(home, monkeypatch, mutation):
    import errno
    import inspect
    import sys
    faults, instance, _ = acl_rig(home, monkeypatch, target=home.parent,
        ancestor=True, fault_kind='acl_open_recheck_once')
    pinned_fd = inspect.getclosurevars(faults._close_open_recheck).nonlocals['parent_fd']
    child = home.parent / 'synthetic-open-recheck-sibling'
    saved = home.parent / 'saved-open-recheck-sibling'
    original_lstat = Path.lstat
    code = filesystem._check_ancestor_acl.__code__
    def interrupted(path, *args, **kwargs):
        caller = sys._getframe(1)
        if path == home.parent and caller.f_code is code and caller.f_lineno == 79:
            if mutation == 'skip_recheck':
                raise RuntimeError('Synthetic pre-recheck interruption')
            if mutation == 'replacement':
                child.rename(saved)
                child.mkdir()
            else:
                (child / 'retained-child').write_text('synthetic')
            raise RuntimeError('Synthetic changed child')
        return original_lstat(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'lstat', interrupted)
    with pytest.raises(RuntimeError if mutation == 'skip_recheck' else AssertionError):
        instance._connect_ticket_grant(None, b'', {'phase': 'disconnected', 'revision': 0})
    assert faults.triggered == 1
    assert faults.restored == (1 if mutation == 'skip_recheck' else 0)
    assert child.exists() is (mutation != 'skip_recheck')
    if mutation == 'replacement':
        assert saved.exists()
    if mutation == 'nonempty':
        assert (child / 'retained-child').read_text() == 'synthetic'
    with pytest.raises(OSError) as closed:
        os.fstat(pinned_fd)
    assert closed.value.errno == errno.EBADF
    faults.close()


def test_open_recheck_fault_sites_are_exact_before_first_native_acl_read():
    import errno
    import inspect
    source = inspect.getsource(filesystem).splitlines()
    assert 'fd = os.open(path,' in source[71]
    assert 'renewed_at_open = ' in source[76]
    assert 'Path(path).lstat()' in source[78] and '_file_identity(baseline)' in source[78]
    assert "raise StoreError('unsafe_filesystem')" in source[79]
    assert '_read_darwin_acl(' in source[80]


def test_host_migration_checkpoint_accepts_only_closed_boolean_evidence(home):
    value = {'version': 1, **dict.fromkeys(fixture._HOST_MIGRATION_EVIDENCE, True)}
    path = home / '.synthetic-host-migration-evidence.json'
    path.write_text(json.dumps(value))
    assert fixture.migration_evidence(home) == value
    for changed in ({**value, 'token': 'must-not-export'}, {**value, 'changedEpoch': 'true'},
                    {**value, 'version': True}, {key: val for key, val in value.items() if key != 'sameOwner'}):
        path.write_text(json.dumps(changed))
        with pytest.raises(ValueError):
            fixture.migration_evidence(home)


@pytest.mark.parametrize('mutation', [None, 'host', 'prompt', 'client', 'id_token_hint', 'login_hint', 'nonce'])
def test_host_migration_request_requires_fresh_forced_consent_and_defers_only_once(home, mutation):
    from types import SimpleNamespace
    import uuid
    edges = SyntheticEdges(home, host_migration='cancel_once')
    archive = home / 'ticket-archive'
    archive.mkdir()
    (archive / 'receipt').write_bytes(b'synthetic immutable retired receipt')
    installation = str(uuid.uuid4())
    edges._host_seed = {'binding': SimpleNamespace(host_id=installation),
        'store': SimpleNamespace(directory=home, status=lambda: {'state': 'disconnected'})}
    query = {'ext_agent_host_id': uuid.UUID(installation).urn, 'prompt': 'consent',
             'client_id': fixture.CLIENT, 'nonce': 'synthetic-fresh-nonce'}
    if mutation == 'host':
        query['ext_agent_host_id'] = installation
    elif mutation == 'prompt':
        query.pop('prompt')
    elif mutation == 'client':
        query['client_id'] = 'dynamic_agent_client'
    elif mutation in {'id_token_hint', 'login_hint'}:
        query[mutation] = 'must-not-forward'
    elif mutation == 'nonce':
        edges._migration_nonces.add(query['nonce'])
    if mutation is not None:
        with pytest.raises(AssertionError):
            edges._migration_request(query)
        assert edges._migration_ticket_archive is None
        return
    first = edges._migration_request(query)
    assert first and all(type(value) is bool and value for value in first.values())
    second = edges._migration_request({**query, 'nonce': 'synthetic-other-nonce'})
    assert second == {**first, 'callbackDeferred': False}
    assert edges._migration_ticket_archive == {'receipt': b'synthetic immutable retired receipt'}
    assert (archive / 'receipt').read_bytes() == b'synthetic immutable retired receipt'
    assert not edges.codes and not edges.events


@pytest.mark.parametrize('mutation', [None, 'mode', 'unseeded', 'active', 'client', 'token', 'hint',
    'extra', 'duplicate', 'missing', 'blank', 'unknown_path'])
def test_host_migration_revoke_requires_exact_seeded_request_and_empty_response(home, monkeypatch, mutation):
    import http.client
    from types import SimpleNamespace
    from urllib.parse import urlencode
    from eidolon_cli import organization_setup_subscription_http as subscription_http
    edges = SyntheticEdges(home, host_migration='cancel_once')
    edges._host_seed = {'binding': SimpleNamespace(issued_client_id=fixture.CLIENT),
        'store': SimpleNamespace(status=lambda: {'state': 'disconnected'})}
    form = {'token': 'synthetic-refresh-never-real', 'token_type_hint': 'refresh_token',
        'client_id': fixture.CLIENT}
    path = '/api/accounts/oauth/revoke'
    if mutation == 'mode':
        edges.host_migration = None
    elif mutation == 'unseeded':
        edges._host_seed = None
    elif mutation == 'active':
        edges._host_seed['store'] = SimpleNamespace(status=lambda: {'state': 'active'})
    elif mutation in {'client', 'token', 'hint'}:
        form[{'client': 'client_id', 'token': 'token', 'hint': 'token_type_hint'}[mutation]] = 'wrong'
    elif mutation == 'extra':
        form['extra'] = 'forbidden'
    elif mutation == 'missing':
        del form['token']
    elif mutation == 'blank':
        form['token'] = ''
    elif mutation == 'unknown_path':
        path += '/unknown'
    body = urlencode(form).encode('ascii')
    if mutation == 'duplicate':
        body += b'&client_id=' + fixture.CLIENT.encode('ascii')
    sock = fixture.SyntheticSocket(edges, 'auth.openai.com')
    sock.sendall(b'POST ' + path.encode('ascii') + b' HTTP/1.1\r\nHost: auth.openai.com\r\n'
        + b'Content-Length: ' + str(len(body)).encode('ascii') + b'\r\n\r\n' + body)
    if mutation is not None:
        with pytest.raises(AssertionError):
            http.client.HTTPResponse(sock)
        assert not edges.events and not edges.codes
        return
    response = http.client.HTTPResponse(sock)
    response.begin()
    assert response.status == 200 and response.getheader('Content-Length') == '0'
    assert response.read() == b''
    response.close()
    assert edges.events == [{'kind': 'oauth-revoke', 'physicalNetworkSend': False}]
    edges.events.clear()

    def connect(connection):
        assert connection.host == 'auth.openai.com'
        connection.sock = fixture.SyntheticSocket(edges, connection.host)
    monkeypatch.setattr(http.client.HTTPSConnection, 'connect', connect)
    # The production HTTP parser accepts only an empty 200 response for revoke.
    assert subscription_http.request(subscription_http.REVOKE, form) is True
    assert edges.events == [{'kind': 'oauth-revoke', 'physicalNetworkSend': False}]
    assert not edges.codes and edges.vault.put_count == edges.vault.delete_count == 0


@contextmanager
def registered_migration_owner(root, organization_id, authority):
    """Use the gateway's actual durable scope and transport publication fences."""
    from tui_gateway import organization_credential_transport as owners
    from tui_gateway import methods_organization_setup_subscription as gateway
    class LocalTransport:
        closed = False
    local_transport = LocalTransport()
    owners.register_owner(local_transport, root)
    try:
        _, _, revision = gateway._durable_scope(root, organization_id, True)
        yield gateway._context(local_transport, {'expectedOrganizationId': organization_id,
            'sourceRevision': revision}, authority, require_fresh=True)
    finally:
        owners.retire_owner(local_transport)


def test_native_migration_owner_context_uses_real_authority_and_rejects_incomplete_or_retired(tmp_path, monkeypatch):
    from dataclasses import replace
    from eidolon_cli.organization_setup_subscription_auth import AuthError, SubscriptionAuthService
    from eidolon_cli.organization_setup_subscription_host import HostAuthority
    from tui_gateway import organization_credential_transport as owners
    root = (tmp_path / 'migration-context-profile').resolve()
    root.mkdir(mode=0o700)
    (root / 'config.yaml').write_text('{}')
    monkeypatch.setenv('HERMES_HOME', str(root))
    identity = fixture.seed_profile(root, legacy=True)
    before = fixture.snapshot(root)
    authority = HostAuthority.capture(lambda: root, lambda: root)
    with registered_migration_owner(root, identity['organizationId'], authority) as context:
        # Constructor-only proof uses no native storage, HTTP, listener or browser.
        # All context guards are the real gateway functions, not constant lambdas.
        service = SubscriptionAuthService(object(), object(), context)
        assert context.owner_guard() and context.setup_guard() and context.lease_generation_guard()
        assert owners.owner_generation(context.transport) is context.owner_generation
        with context.publication_authority():
            service._guard()
        for field in ('publication_authority', 'setup_guard', 'lease_generation_guard'):
            with pytest.raises(AuthError, match='invalid_request'):
                SubscriptionAuthService(object(), object(), replace(context, **{field: None}))
        # Matches the originally incomplete native test context and remains refused.
        with pytest.raises(AuthError, match='invalid_request'):
            SubscriptionAuthService(object(), object(), replace(context,
                publication_authority=None, lease_generation_guard=None))
        owners.register_owner(context.transport, root)
        assert not context.owner_guard() and not context.lease_generation_guard()
        assert not service._lease_guard()
        with pytest.raises(AuthError, match='stale_owner'):
            service._guard()
        with pytest.raises(RuntimeError, match='retired'):
            with context.publication_authority():
                pytest.fail('Retired generation acquired publication authority')
    assert owners.owner_generation(context.transport) is None
    assert fixture.snapshot(root) == before


@pytest.mark.macos_only
@pytest.mark.parametrize('revoke_supported', [True, False])
def test_native_host_migration_seed_is_legacy_without_provider_consent(tmp_path, monkeypatch, revoke_supported):
    from eidolon_cli.organization_setup_subscription_auth import SubscriptionAuthService
    from eidolon_cli.organization_setup_subscription_host import INSTALLATION_NAME
    root = (tmp_path / 'legacy-migration-profile').resolve()
    root.mkdir(mode=0o700)
    (root / 'config.yaml').write_text('{}')
    monkeypatch.setenv('HERMES_HOME', str(root))
    identity = fixture.seed_profile(root, legacy=True)
    before = fixture.snapshot(root)
    edges = SyntheticEdges(root, host_migration='cancel_once').install()
    try:
        seed = edges._host_seed
        with registered_migration_owner(root, identity['organizationId'], seed['host'].authority) as context:
            service = SubscriptionAuthService(seed['store'], seed['host'], context)
            status = service.status()
        assert status['connectionState'] == 'active' and status['hostIdentityState'] == 'migration_required'
        assert status['canMigrateHostIdentity'] is False
        assert not (root / INSTALLATION_NAME / 'oauth-host.json').exists()
        assert not list((root / INSTALLATION_NAME).glob('oauth-consent-*'))
        evidence = fixture.migration_evidence(root)
        assert evidence['legacySeeded'] and evidence['sameBinding'] and evidence['sameInstallation']
        assert not evidence['changedEpoch'] and not evidence['provenanceCommitted']
        assert fixture.snapshot(root) == before
        assert not any(event['kind'] in {'consent', 'oauth-exchange', 'responses'} for event in edges.events)
        if not revoke_supported:
            edges.host_migration = None
        with registered_migration_owner(root, identity['organizationId'], seed['host'].authority) as context:
            service = SubscriptionAuthService(seed['store'], seed['host'], context)
            ticket = service.prepare_operation('revoke', status['connectionRevision'])['ticket']
            result = service.execute_operation(ticket)
            assert result['state'] == ('completed' if revoke_supported else 'admitted')
            assert result['receipt']['localRetired'] is True
            assert result['receipt']['remoteRevocation'] == ('confirmed' if revoke_supported else 'unconfirmed')
            retired = service.status()
            assert retired['connectionState'] == 'disconnected' and retired['cleanupPending'] is False
            assert retired['hostIdentityState'] == 'migration_required'
            assert retired['canMigrateHostIdentity'] is revoke_supported
            if not revoke_supported:
                # Reconciliation reads evidence; it cannot invent remote success.
                assert service.reconcile_operation(ticket)['state'] == 'admitted'
                assert service.status()['canMigrateHostIdentity'] is False
        assert fixture.snapshot(root) == before
        assert len([event for event in edges.events if event['kind'] == 'oauth-revoke']) == int(revoke_supported)
        assert not any(event['kind'] in {'consent', 'oauth-exchange', 'responses'} for event in edges.events)
    finally:
        edges.close()


# Diagnostic-only controls: no provider, production budget or retry changes.
def injector_rows(diagnostics):
    return [row for row in diagnostics.rows if row.get('stage') == 'fault_injector']


def test_injector_trace_sites_match_unchanged_source():
    import inspect
    source = inspect.getsource(RecoveryFaults._call_locked).splitlines()
    expected = {3: "with closing(sqlite3.connect(", 4: "writer.execute('BEGIN IMMEDIATE')",
        5: 'self.triggered += 1', 7: 'return function(prepared)',
        9: 'writer.rollback()', 10: 'self.restored += 1'}
    assert set(expected) == set(fixture.BoundaryDiagnostics._INJECTOR_LINES)
    for offset, fragment in expected.items():
        assert fragment in source[offset]


@pytest.mark.parametrize('cooperating', [False, True])
def test_injector_acquisition_contention_is_observed_without_callback_retry_or_trigger(
        home, tmp_path, monkeypatch, cooperating):
    import time
    from contextlib import nullcontext
    from eidolon_cli import organization_ledger_writer as writers
    diagnostics = fixture.BoundaryDiagnostics(tmp_path, home=home)
    faults = RecoveryFaults(home, 'sqlite_dispatch_once')
    held, release = threading.Event(), threading.Event()
    failures, calls = [], []
    path = home / 'organization' / 'state.db'
    def contender():
        try:
            with (writers.ledger_writer(path, deadline=time.monotonic() + 5)
                  if cooperating else nullcontext()):
                with closing(sqlite3.connect(path, timeout=0)) as conn:
                    conn.execute('BEGIN IMMEDIATE')
                    held.set()
                    assert release.wait(5)
                    conn.rollback()
        except BaseException as error:
            failures.append(error)
            held.set()
    monkeypatch.setattr(generation, 'complete_subscription', lambda value: calls.append(value) or 'returned')
    faults.install(monkeypatch.setattr)
    bound = object.__new__(admission._Admission)
    bound.home = home
    prepared = object.__new__(transport.PreparedSubscriptionSend)
    prepared._admission = bound
    worker = threading.Thread(target=contender)
    diagnostics.install()
    installed = diagnostics.path.read_bytes()
    try:
        worker.start()
        assert held.wait(5) and not failures
        with pytest.raises(sqlite3.OperationalError) as failed:
            generation.complete_subscription(prepared)
        assert failed.value.sqlite_errorcode & 255 == sqlite3.SQLITE_BUSY
        assert calls == [] and faults.triggered == faults.restored == 0
        assert faults._claimed is True  # No implicit re-arm after acquisition failure.
        assert diagnostics.path.read_bytes() == installed  # No critical-frame I/O.
        rows = injector_rows(diagnostics)
        assert [r['phase'] for r in rows if r['event'] == 'progress'] == [
            'before_connect', 'before_begin', 'before_close', 'attempt_exit']
        refused = [r for r in rows if r['event'] == 'exception']
        assert len(refused) == 1 and refused[0]['phase'] == 'before_begin'
        assert refused[0]['errorClass'] == 'OperationalError'
        assert refused[0]['sqlitePrimaryCode'] == 'SQLITE_BUSY'
        holders = refused[0]['coordinatorHolders']
        assert bool(holders) is cooperating
        if cooperating:
            assert len(holders) == 1 and holders[0]['targetScope'] == 'profile_ledger'
            assert holders[0]['lane'] != refused[0]['lane']
        assert not refused[0]['coordinatorHoldersTruncated']
    finally:
        release.set()
        worker.join(5)
        diagnostics.close()
    assert not worker.is_alive() and not failures and not diagnostics.coordinator_holders
    assert generation.complete_subscription(prepared) == 'returned'
    assert calls == [prepared] and faults.triggered == faults.restored == 0
    assert not diagnostics.observation_loss
    text = diagnostics.path.read_text()
    assert str(home) not in text and 'database is locked' not in text
    assert all(type(r['elapsedUs']) is int and 0 <= r['elapsedUs'] <= 60_000_000
               and type(r['elapsedCapped']) is bool for r in injector_rows(diagnostics))


@pytest.mark.parametrize('failure', [None, 'callback', 'rollback', 'close', 'connect'])
def test_injector_trace_preserves_result_exception_and_cleanup_order(home, tmp_path, monkeypatch, failure):
    diagnostics = fixture.BoundaryDiagnostics(tmp_path, home=home)
    faults = RecoveryFaults(home, 'sqlite_dispatch_once')
    sentinel, result, order = ValueError('synthetic-secret-must-not-export'), object(), []
    class Writer:
        def execute(self, sql):
            assert sql == 'BEGIN IMMEDIATE'
            order.append('begin')
        def rollback(self):
            order.append('rollback')
            if failure == 'rollback':
                raise sentinel
        def close(self):
            order.append('close')
            if failure == 'close':
                raise sentinel
    def connect(path, *, timeout):
        assert path == home / 'organization' / 'state.db' and timeout == 0
        order.append('connect')
        if failure == 'connect':
            raise sentinel
        return Writer()
    def callback(value):
        assert value is result
        order.append('callback')
        if failure == 'callback':
            raise sentinel
        return result
    monkeypatch.setattr(fixture.sqlite3, 'connect', connect)
    diagnostics.install()
    try:
        if failure is None:
            assert faults._call_locked(callback, result) is result
        else:
            with pytest.raises(ValueError) as failed:
                faults._call_locked(callback, result)
            assert failed.value is sentinel
    finally:
        diagnostics.close()
    assert order == (['connect'] if failure == 'connect' else ['connect', 'begin', 'callback', 'rollback', 'close'])
    assert faults.triggered == (0 if failure == 'connect' else 1)
    assert faults.restored == (0 if failure in {'connect', 'rollback'} else 1)
    rows = injector_rows(diagnostics)
    assert rows[-1]['phase'] == 'attempt_exit'
    errors = [row for row in rows if row['event'] == 'exception']
    assert len(errors) == (0 if failure is None else 1)
    if errors:
        assert errors[0]['phase'] == {'connect': 'before_connect', 'callback': 'before_completion',
            'rollback': 'before_rollback', 'close': 'before_close'}[failure]
    assert 'synthetic-secret' not in diagnostics.path.read_text()
    assert not diagnostics.injectors and not diagnostics.observation_loss


def test_injector_trace_failure_never_changes_result_or_original_error(home, tmp_path, monkeypatch):
    faults = RecoveryFaults(home, 'sqlite_dispatch_once')
    for error in (None, ValueError('synthetic-private-original')):
        diagnostics = fixture.BoundaryDiagnostics(tmp_path, home=home)
        def fail_observer(*args, **kwargs):
            raise RuntimeError('synthetic-private-observer')
        monkeypatch.setattr(diagnostics, 'observe_injector', fail_observer)
        def callback(value):
            if error is not None:
                raise error
            return value
        expected = object()
        diagnostics.install()
        try:
            if error is None:
                assert faults._call_locked(callback, expected) is expected
            else:
                with pytest.raises(ValueError) as failed:
                    faults._call_locked(callback, expected)
                assert failed.value is error
        finally:
            diagnostics.close()
        rows = [json.loads(line) for line in diagnostics.path.read_text().splitlines()]
        assert {'sequence': 0, 'stage': 'observer', 'event': 'observation_loss',
                'category': 'trace_exception'} in rows
        assert 'synthetic-private' not in diagnostics.path.read_text()
    assert faults.triggered == faults.restored == 2


def test_injector_trace_rejects_copied_code_identity(home, tmp_path):
    from types import FunctionType
    original = RecoveryFaults._call_locked
    duplicate = FunctionType(original.__code__.replace(), original.__globals__)
    diagnostics = fixture.BoundaryDiagnostics(tmp_path, home=home)
    diagnostics.install()
    try:
        assert duplicate(RecoveryFaults(home, 'sqlite_dispatch_once'), lambda value: value, 42) == 42
    finally:
        diagnostics.close()
    assert not injector_rows(diagnostics)


def test_coordinator_trace_requires_actual_ownership_and_preserves_nested_gates(home, tmp_path):
    import time
    from eidolon_cli import organization_ledger_writer as writers
    diagnostics = fixture.BoundaryDiagnostics(tmp_path, home=home)
    gate = writers._Gate()
    diagnostics.install()
    try:
        with pytest.raises(writers.LedgerWriterUnavailable):
            gate.acquire(time.monotonic() - 1, None)
        assert not diagnostics.coordinator_holders
        gate.acquire(time.monotonic() + 5, None)
        assert list(diagnostics.coordinator_holders.values()) == [
            {'lane': 1, 'stage': 'other', 'targetScope': 'unknown'}]
        prior = dict(diagnostics.coordinator_holders)
        with pytest.raises(writers.LedgerWriterUnavailable, match='reentrant'):
            gate.acquire(time.monotonic() + 5, None)
        assert diagnostics.coordinator_holders == prior
        errors = []
        def wrong_release():
            try:
                gate.release()
            except RuntimeError as error:
                errors.append(error)
        worker = threading.Thread(target=wrong_release)
        worker.start()
        worker.join(5)
        assert not worker.is_alive() and len(errors) == 1
        assert diagnostics.coordinator_holders == prior
        with writers.ledger_writer(home / 'organization' / 'state.db', deadline=time.monotonic() + 5):
            with writers.ledger_writer(tmp_path / 'other.db', deadline=time.monotonic() + 5):
                assert {v['targetScope'] for v in diagnostics.coordinator_holders.values()} == {
                    'unknown', 'profile_ledger', 'other_ledger'}
                assert len(diagnostics.coordinator_holders) == 3
            assert len(diagnostics.coordinator_holders) == 2
        assert diagnostics.coordinator_holders == prior
        gate.release()
        assert not diagnostics.coordinator_holders
    finally:
        diagnostics.close()
    assert not diagnostics.coordinator_failures and not diagnostics.observation_loss


def test_coordinator_trace_observer_failure_preserves_acquire_release(home, tmp_path, monkeypatch):
    import time
    from eidolon_cli import organization_ledger_writer as writers
    diagnostics = fixture.BoundaryDiagnostics(tmp_path, home=home)
    def fail(*args):
        raise ValueError('synthetic-observation-failure')
    monkeypatch.setattr(diagnostics, 'observe_coordinator', fail)
    gate = writers._Gate()
    diagnostics.install()
    try:
        gate.acquire(time.monotonic() + 5, None)
        assert gate.owner == threading.get_ident()
        gate.release()
        assert gate.owner is None
    finally:
        diagnostics.close()
    assert diagnostics.observation_loss == {'trace_exception'}


def test_coordinator_diagnostic_capacity_is_bounded_and_explicit(home, tmp_path):
    import time
    from eidolon_cli import organization_ledger_writer as writers
    diagnostics = fixture.BoundaryDiagnostics(tmp_path, home=home)
    gates = [writers._Gate() for _ in range(66)]
    diagnostics.install()
    try:
        for gate in gates:
            gate.acquire(time.monotonic() + 5, None)
        assert len(diagnostics.coordinator_holders) == 64
        sample = diagnostics.coordinator_snapshot()
        assert len(sample['coordinatorHolders']) == 8 and sample['coordinatorHoldersTruncated']
        assert diagnostics.observation_loss == {'coordinator_capacity'}
    finally:
        for gate in gates:
            if gate.owner == threading.get_ident():
                gate.release()
        diagnostics.close()
    assert not diagnostics.coordinator_holders
    assert 'coordinator_capacity' in diagnostics.path.read_text()


def test_injector_elapsed_cap_and_success_phases_are_explicit(home, tmp_path, monkeypatch):
    diagnostics = fixture.BoundaryDiagnostics(tmp_path, home=home)
    ticks = iter([0] + [61_000_000_000] * 100)
    monkeypatch.setattr(fixture.time, 'monotonic_ns', lambda: next(ticks))
    faults = RecoveryFaults(home, 'sqlite_dispatch_once')
    diagnostics.install()
    try:
        assert faults._call_locked(lambda value: value, 'returned') == 'returned'
    finally:
        diagnostics.close()
    rows = injector_rows(diagnostics)
    assert [r['phase'] for r in rows] == ['before_connect', 'before_begin', 'acquired',
        'before_completion', 'before_rollback', 'after_rollback', 'before_close', 'attempt_exit']
    assert all(r['elapsedUs'] == 60_000_000 and r['elapsedCapped'] for r in rows)
    assert faults.triggered == faults.restored == 1 and not diagnostics.observation_loss


@pytest.mark.parametrize('capacity', ['lane', 'injector'])
def test_injector_missing_capacity_marks_observation_loss_without_changing_result(home, tmp_path, capacity):
    diagnostics = fixture.BoundaryDiagnostics(tmp_path, home=home)
    if capacity == 'lane':
        diagnostics.lanes = {-index - 1: index + 1 for index in range(32)}
    else:
        diagnostics.injectors = {-index - 1: {} for index in range(32)}
    diagnostics.install()
    try:
        assert RecoveryFaults(home, 'sqlite_dispatch_once')._call_locked(lambda value: value, 42) == 42
    finally:
        diagnostics.close()
    assert capacity + '_capacity' in diagnostics.observation_loss
    rows = injector_rows(diagnostics)
    if capacity == 'lane':
        assert rows and all(r['lane'] == 0 for r in rows)
    else:
        assert rows == [] and len(diagnostics.injectors) == 32
    assert capacity + '_capacity' in diagnostics.path.read_text()


def test_observer_loss_survives_bounded_tail_projection(tmp_path):
    diagnostics = fixture.BoundaryDiagnostics(tmp_path)
    for _ in range(600):
        diagnostics.emit({'stage': 'observer', 'event': 'enter'})
    diagnostics.observation_loss.update({'trace_exception', 'coordinator_capacity', 'injector_capacity', 'lane_capacity'})
    diagnostics.flush()
    rows = [json.loads(line) for line in diagnostics.path.read_text().splitlines()]
    assert len(rows) == 517 and rows[-5]['event'] == 'overflow'
    assert {row.get('category') for row in rows[-4:]} == diagnostics.observation_loss
    assert all(row['event'] == 'observation_loss' for row in rows[-4:])
