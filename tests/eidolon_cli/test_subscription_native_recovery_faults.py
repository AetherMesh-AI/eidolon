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
    bound._cancel, bound._abandoned = threading.Event(), threading.Event()
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


def scope_only_prepared(home):
    """Mechanics-only capability shape; never supplies production send authority."""
    bound = object.__new__(admission._Admission)
    bound.home = home
    bound._cancel, bound._abandoned = threading.Event(), threading.Event()
    prepared = object.__new__(transport.PreparedSubscriptionSend)
    prepared._admission = bound
    return prepared


def test_sqlite_fault_drains_cooperating_writer_before_separate_completion_worker(home, monkeypatch):
    import time
    from eidolon_cli import organization_ledger_writer as writers
    faults = RecoveryFaults(home, 'sqlite_dispatch_once')
    held, queued = threading.Event(), threading.Event()
    failures, callback_calls, acquired, deadlines = [], [], [], []
    path = home / 'organization' / 'state.db'
    caller = threading.get_ident()
    original_wait = writers._Gate._wait_for_turn
    def observe_wait(gate, ticket, deadline, cancelled):
        if threading.get_ident() == caller:
            deadlines.append(deadline)
            queued.set()
        return original_wait(gate, ticket, deadline, cancelled)
    monkeypatch.setattr(writers._Gate, '_wait_for_turn', observe_wait)
    def contender():
        try:
            with writers.ledger_writer(path, deadline=time.monotonic() + 5):
                with closing(sqlite3.connect(path, timeout=0)) as conn:
                    conn.execute('BEGIN IMMEDIATE')
                    held.set()
                    assert queued.wait(5)
                    conn.rollback()
        except BaseException as error:
            failures.append(error)
            held.set()
    def completion(_):
        callback_calls.append(True)
        outcomes = []
        def worker():
            try:
                with writers.ledger_writer(path, deadline=time.monotonic() + writers.PUBLICATION_WAIT_SECONDS):
                    acquired.append(True)
                    with closing(sqlite3.connect(path, timeout=0)) as conn:
                        conn.execute('BEGIN IMMEDIATE')
            except BaseException as error:
                outcomes.append(error)
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(5)
        assert not thread.is_alive() and len(outcomes) == 1
        raise outcomes[0]
    thread = threading.Thread(target=contender)
    thread.start()
    try:
        assert held.wait(5) and not failures
        before = time.monotonic()
        with pytest.raises(sqlite3.OperationalError) as failed:
            faults._call_locked(completion, scope_only_prepared(home))
        after = time.monotonic()
        assert type(failed.value) is sqlite3.OperationalError
        assert failed.value.sqlite_errorcode & 255 == sqlite3.SQLITE_BUSY
        assert len(deadlines) == 1 and before + .25 <= deadlines[0] <= after + .25
    finally:
        queued.set()
        thread.join(5)
    assert not thread.is_alive() and not failures
    assert callback_calls == acquired == [True]
    assert faults.triggered == faults.restored == 1
    with admission._ledger_connection(home, write=True) as conn:
        assert conn.execute('SELECT count(*) FROM synthetic_probe').fetchone()[0] == 0


@pytest.mark.parametrize('failure', ['deadline', 'cancelled', 'reentrant', 'external'])
def test_sqlite_fault_setup_refusals_do_not_connect_complete_or_rearm(home, monkeypatch, failure):
    import time
    from eidolon_cli import organization_ledger_writer as writers
    from contextlib import nullcontext
    faults = RecoveryFaults(home, 'sqlite_dispatch_once')
    prepared = scope_only_prepared(home)
    calls, connections = [], []
    real_connect = sqlite3.connect
    def connect(*args, **kwargs):
        connections.append(kwargs['timeout'])
        return real_connect(*args, **kwargs)
    monkeypatch.setattr(generation, 'complete_subscription', lambda value: calls.append(value) or 'returned')
    faults.install(monkeypatch.setattr)
    external = real_connect(home / 'organization' / 'state.db', timeout=0) if failure == 'external' else None
    release, held, errors = threading.Event(), threading.Event(), []
    def occupy():
        try:
            with writers.ledger_writer(home / 'organization' / 'state.db', deadline=time.monotonic() + 5):
                held.set()
                assert release.wait(5)
        except BaseException as error:
            errors.append(error)
    worker = threading.Thread(target=occupy) if failure == 'deadline' else None
    if worker:
        worker.start()
        assert held.wait(5)
    if external:
        external.execute('BEGIN IMMEDIATE')
    if failure == 'cancelled':
        prepared._admission._cancel.set()
    try:
        with (writers.ledger_writer(home / 'organization' / 'state.db', deadline=time.monotonic() + 5)
              if failure == 'reentrant' else nullcontext()):
            monkeypatch.setattr(fixture.sqlite3, 'connect', connect)
            with pytest.raises(sqlite3.OperationalError) as failed:
                generation.complete_subscription(prepared)
        if failure != 'external':
            assert type(failed.value) is writers.LedgerWriterUnavailable and failed.value.reason == failure
        else:
            assert type(failed.value) is sqlite3.OperationalError and failed.value.sqlite_errorcode & 255 == sqlite3.SQLITE_BUSY
        assert connections == ([0] if failure == 'external' else [])
        assert calls == [] and faults.triggered == faults.restored == 0 and faults._claimed
    finally:
        if external:
            external.rollback()
            external.close()
        release.set()
        if worker:
            worker.join(5)
            assert not worker.is_alive()
    assert not errors
    prepared._admission._cancel.clear()
    assert generation.complete_subscription(prepared) == 'returned'
    assert calls == [prepared] and faults.triggered == faults.restored == 0


@pytest.mark.parametrize('moment', ['acquired', 'before_callback'])
@pytest.mark.parametrize('reason', ['cancelled', 'deadline'])
def test_sqlite_fault_late_setup_stop_never_invokes_completion(home, monkeypatch, moment, reason):
    from eidolon_cli import organization_ledger_writer as writers
    faults = RecoveryFaults(home, 'sqlite_dispatch_once')
    prepared = scope_only_prepared(home)
    real = writers.ledger_writer
    clock, deadlines, callbacks = [100.0], [], []
    monkeypatch.setattr(fixture.time, 'monotonic', lambda: clock[0])
    def stop():
        if reason == 'cancelled':
            prepared._admission._cancel.set()
        else:
            clock[0] = 100.25
    @contextmanager
    def controlled(*args, **kwargs):
        deadlines.append(kwargs['deadline'])
        with real(*args, **kwargs):
            if moment == 'acquired':
                stop()
            yield
        if moment == 'before_callback':
            stop()
    monkeypatch.setattr(writers, 'ledger_writer', controlled)
    with pytest.raises(writers.LedgerWriterUnavailable) as failed:
        faults._call_locked(lambda _: callbacks.append(True), prepared)
    assert failed.value.reason == reason and deadlines == [100.25]
    assert callbacks == []
    assert faults.triggered == faults.restored == (0 if moment == 'acquired' else 1)
    # A new independent writer proves no leaked transaction or coordinator.
    with closing(sqlite3.connect(home / 'organization' / 'state.db', timeout=0)) as conn:
        conn.execute('BEGIN IMMEDIATE')
        conn.rollback()


@pytest.mark.parametrize('failure', [None, 'connect', 'begin', 'callback', 'rollback', 'close',
    'transfer_pop', 'transfer_before', 'transfer_after', 'coordinator_exit'])
@pytest.mark.parametrize('cleanup_failure', [None, 'rollback', 'close'])
def test_sqlite_fault_cleanup_transfer_preserves_order_and_exception_precedence(
        home, monkeypatch, failure, cleanup_failure):
    from contextlib import ExitStack as RealExitStack
    from eidolon_cli import organization_ledger_writer as writers
    faults = RecoveryFaults(home, 'sqlite_dispatch_once')
    prepared = scope_only_prepared(home)
    primary, rollback_error, close_error = ValueError('primary'), RuntimeError('rollback'), OSError('close')
    order, coordinated, callbacks = [], [False], []
    real_gate = writers.ledger_writer
    @contextmanager
    def gate(*args, **kwargs):
        with real_gate(*args, **kwargs):
            coordinated[0] = True
            try:
                yield
            finally:
                coordinated[0] = False
        if failure == 'coordinator_exit':
            raise primary
    class Writer:
        def execute(self, statement):
            assert coordinated[0] and statement == 'BEGIN IMMEDIATE'
            order.append('begin')
            if failure == 'begin':
                raise primary
        def rollback(self):
            order.append('rollback')
            if failure == 'rollback' or cleanup_failure == 'rollback':
                raise rollback_error
        def close(self):
            order.append('close')
            if failure == 'begin':
                assert coordinated[0]  # Failed acquisition closes while gated.
            if failure == 'close' or cleanup_failure == 'close':
                raise close_error
    def connect(*args, **kwargs):
        assert coordinated[0] and kwargs == {'timeout': 0}
        order.append('connect')
        if failure == 'connect':
            raise primary
        return Writer()
    class TransferStack(RealExitStack):
        def pop_all(self):
            if failure == 'transfer_pop':
                raise primary
            return super().pop_all()
        def enter_context(self, manager):
            if isinstance(manager, RealExitStack):
                if failure == 'transfer_before':
                    raise primary
                value = super().enter_context(manager)
                if failure == 'transfer_after':
                    raise primary
                return value
            return super().enter_context(manager)
    def complete(value):
        assert not coordinated[0] and value is prepared
        callbacks.append(True)
        order.append('callback')
        if failure == 'callback':
            raise primary
        return 'returned'
    monkeypatch.setattr(writers, 'ledger_writer', gate)
    monkeypatch.setattr(fixture.sqlite3, 'connect', connect)
    monkeypatch.setattr(fixture, 'ExitStack', TransferStack)
    acquired = failure not in {'connect', 'begin'}
    expected = (primary if failure == 'connect' else
        close_error if failure == 'close' or cleanup_failure == 'close' else
        rollback_error if acquired and (failure == 'rollback' or cleanup_failure == 'rollback') else
        primary if failure in {'begin', 'callback', 'transfer_pop', 'transfer_before', 'transfer_after', 'coordinator_exit'} else None)
    if expected is None:
        assert faults._call_locked(complete, prepared) == 'returned'
    else:
        with pytest.raises(type(expected)) as failed:
            faults._call_locked(complete, prepared)
        assert failed.value is expected
    invoked = failure not in {'connect', 'begin', 'transfer_pop', 'transfer_before', 'transfer_after', 'coordinator_exit'}
    assert len(callbacks) == int(invoked)
    assert order == (['connect'] if failure == 'connect' else ['connect', 'begin']
        + (['callback'] if invoked else []) + (['rollback'] if acquired else []) + ['close'])
    assert faults.triggered == int(acquired)
    assert faults.restored == int(acquired and failure != 'rollback' and cleanup_failure != 'rollback')


def test_sqlite_fault_setup_preserves_all_real_ledger_rows_and_publication_authority(tmp_path, monkeypatch):
    from tui_gateway import organization_credential_transport as owners
    root = tmp_path / 'seeded'
    root.mkdir(mode=0o700)
    (root / 'config.yaml').write_text('{}')
    fixture.seed_profile(root, legacy=True)
    path = root / 'organization' / 'state.db'
    with closing(sqlite3.connect(path)) as conn:
        before = list(conn.iterdump())
    def forbidden(*args, **kwargs):
        raise AssertionError('Fixture setup must not acquire publication authority')
    monkeypatch.setattr(owners, 'owner_publication', forbidden)
    faults = RecoveryFaults(root, 'sqlite_dispatch_once')
    assert faults._call_locked(lambda _: 'returned', scope_only_prepared(root)) == 'returned'
    with closing(sqlite3.connect(path)) as conn:
        assert list(conn.iterdump()) == before
        assert conn.execute('SELECT dispatched,reserved_tokens FROM organization_team_generations').fetchall() == [(1, 200)]
    assert faults.triggered == faults.restored == 1


# Reuse the explicit synthetic private-store rig for a real registered RPC/ledger
# proof. This substitutes native storage/TLS and OS admission only; no send or
# publication authority is invented by the new fixture setup.
from tests.eidolon_cli.test_organization_setup_subscription_policy_integration import ledger  # noqa: E402,F401
from tests.eidolon_cli.test_organization_setup_subscription_admission import activated  # noqa: E402,F401
from tests.eidolon_cli.test_organization_setup_subscription_recovery import recovery_ledger  # noqa: E402,F401


@pytest.mark.skipif(os.name != 'posix', reason='Synthetic private-store proof requires POSIX')
def test_coordinated_fault_registered_5076_no_send_retains_debt_and_requires_new_intent(recovery_ledger, monkeypatch):
    from queue import Queue
    import tui_gateway.server as server
    from tests.tui_gateway.test_organization_setup_subscription_flow import call
    from tests.eidolon_cli.test_organization_setup_subscription_admission import request as make_request
    from tests.eidolon_cli.test_organization_setup_subscription_transport import install, wire, event, response
    from eidolon_cli import organization_team_budget as budget
    rig = recovery_ledger
    # A valid retained legacy charge is not refunded by an unsent new attempt.
    rig.conn.execute('INSERT INTO organization_team_generations '
        '(idempotency_key,organization_id,input_hash,source_revision,status,dispatched,reserved_tokens,created) '
        'VALUES (?,?,?,?,?,?,?,0)', ('prior-charge', rig.identity['organizationId'],
        'prior-input', 'prior-source', 'failed', 1, 200))
    policy_generation = rig.conn.execute('SELECT generation FROM organization_policy WHERE id=1').fetchone()[0]
    rig.conn.execute('INSERT INTO organization_team_generation_operations VALUES (?,?,?,?,?)',
        ('prior-charge', rig.identity['organizationId'], 'prior-input', 'legacy', policy_generation))
    old_row = dict(rig.conn.execute("SELECT * FROM organization_team_generations WHERE idempotency_key='prior-charge'").fetchone())
    before_counts = budget.operation_counts(rig.conn)
    before_policy = [tuple(row) for row in rig.conn.execute('SELECT * FROM organization_owner_budget_policy')]
    assert before_counts['total'] == before_counts['legacy'] == 1
    monkeypatch.setattr(server, '_eidolon_home', rig.home)
    rig.transport.responses = Queue()
    rig.transport.write = rig.transport.responses.put
    socket, connects = install(monkeypatch, wire(event(response=response())))
    faults = RecoveryFaults(rig.home, 'sqlite_dispatch_once')
    faults.install(monkeypatch.setattr)
    value = make_request(rig, key='coordinated-unsent')
    error = call('improveTeamInput', value, rig.transport)['error']
    offer = {'version': 1, 'disposition': 'retry_unsent', 'idempotencyKey': value['idempotencyKey'],
        'operation': 'improve_jobs', 'sourceRevision': value['sourceRevision']}
    assert error == {'code': 5076, 'message': 'Team setup is unavailable',
        'data': {'reason': 'unavailable', 'recovery': offer}}
    assert not socket.sent and len(connects) == 1
    assert faults.triggered == faults.restored == 1
    unsent = dict(rig.conn.execute('SELECT * FROM organization_team_generations WHERE idempotency_key=?',
                                  (value['idempotencyKey'],)).fetchone())
    assert unsent['status'] == 'failed' and unsent['dispatched'] == 0
    assert budget.operation_counts(rig.conn) == before_counts
    assert dict(rig.conn.execute("SELECT * FROM organization_team_generations WHERE idempotency_key='prior-charge'").fetchone()) == old_row
    assert [tuple(row) for row in rig.conn.execute('SELECT * FROM organization_owner_budget_policy')] == before_policy
    assert rig.conn.execute('SELECT count(*) FROM organization_setup_subscription_dispatches').fetchone()[0] == 0
    assert rig.conn.execute('SELECT count(*) FROM organization_setup_subscription_outcomes').fetchone()[0] == 0
    before = list(rig.conn.iterdump())
    assert call('improveTeamInput', value, rig.transport)['error']['data'] == {'reason': 'generation_failed', 'recovery': offer}
    assert list(rig.conn.iterdump()) == before and len(connects) == 1 and not socket.sent
    # A genuinely new explicit request can use the unchanged sender once.
    sent_socket, sent_connects = install(monkeypatch, wire(event(response=response())))
    result = call('improveTeamInput', {**value, 'idempotencyKey': 'explicit-after-unsent'}, rig.transport)
    assert result['result']['suggestion'] == 'Ready'
    assert len(sent_connects) == 1 and sent_socket.sent
    assert faults.triggered == faults.restored == 1
    assert budget.operation_counts(rig.conn)['total'] == 2
    assert dict(rig.conn.execute("SELECT * FROM organization_team_generations WHERE idempotency_key='prior-charge'").fetchone()) == old_row
    assert dict(rig.conn.execute('SELECT * FROM organization_team_generations WHERE idempotency_key=?',
                                (value['idempotencyKey'],)).fetchone()) == unsent


@pytest.mark.skipif(os.name != 'posix', reason='Synthetic private-store proof requires POSIX')
@pytest.mark.parametrize('retirement', ['cancel', 'owner'])
def test_coordinated_fault_late_cancel_or_owner_retirement_keeps_production_no_send_fence(
        recovery_ledger, monkeypatch, retirement):
    from queue import Queue
    import tui_gateway.server as server
    from tui_gateway import organization_credential_transport as owners
    from tests.tui_gateway.test_organization_setup_subscription_flow import call
    from tests.eidolon_cli.test_organization_setup_subscription_admission import request as make_request
    from tests.eidolon_cli.test_organization_setup_subscription_transport import install, wire, event, response
    rig = recovery_ledger
    monkeypatch.setattr(server, '_eidolon_home', rig.home)
    rig.transport.responses = Queue()
    rig.transport.write = rig.transport.responses.put
    socket, connects = install(monkeypatch, wire(event(response=response())))
    original, completed = generation.complete_subscription, []
    def retire_then_complete(prepared):
        completed.append(True)
        if retirement == 'cancel':
            prepared._admission.abandon()
        else:
            owners.retire_owner(rig.transport)
        return original(prepared)
    monkeypatch.setattr(generation, 'complete_subscription', retire_then_complete)
    faults = RecoveryFaults(rig.home, 'sqlite_dispatch_once')
    faults.install(monkeypatch.setattr)
    value = make_request(rig, key='retired-after-setup')
    result = call('improveTeamInput', value, rig.transport)
    assert result['error']['code'] == 5076
    assert result['error']['data']['reason'] == 'unavailable'
    assert completed == [True] and not socket.sent
    assert len(connects) == (0 if retirement == 'cancel' else 1)
    assert faults.triggered == faults.restored == 1
    row = rig.conn.execute('SELECT status,dispatched FROM organization_team_generations WHERE idempotency_key=?',
                           (value['idempotencyKey'],)).fetchone()
    assert tuple(row) == ('failed', 0)
    assert rig.conn.execute('SELECT count(*) FROM organization_setup_subscription_dispatches').fetchone()[0] == 0
    assert rig.conn.execute('SELECT count(*) FROM organization_setup_subscription_outcomes').fetchone()[0] == 0
    if retirement == 'owner':
        assert owners.owner_generation(rig.transport) is None
        assert 'recovery' not in result['error']['data']


@pytest.mark.parametrize('release_after', [False, True])
def test_sqlite_fault_real_coordinator_exit_exception_still_closes_owned_transaction(home, monkeypatch, release_after):
    from eidolon_cli import organization_ledger_writer as writers
    faults = RecoveryFaults(home, 'sqlite_dispatch_once')
    primary, gates, called = RuntimeError('synthetic coordinator exit'), [], []
    release = writers._Gate.release
    def failed_release(gate):
        gates.append(gate)
        if release_after:
            release(gate)
        raise primary
    monkeypatch.setattr(writers._Gate, 'release', failed_release)
    try:
        with pytest.raises(RuntimeError) as failure:
            faults._call_locked(lambda _: called.append(True), scope_only_prepared(home))
        assert failure.value is primary and not called
        assert faults.triggered == faults.restored == 1 and len(gates) == 1
        assert (gates[0].owner is None) is release_after
        with closing(sqlite3.connect(home / 'organization' / 'state.db', timeout=0)) as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.rollback()
    finally:
        if gates and gates[0].owner == threading.get_ident():
            release(gates[0])
