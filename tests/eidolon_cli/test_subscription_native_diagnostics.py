"""Synthetic barrier proofs and closed-schema native diagnostics; no live network."""
import http.client
import io
import importlib.util
import json
import threading
from types import SimpleNamespace

import pytest

from eidolon_cli.organization_setup_subscription_auth import OwnerContext, SubscriptionAuthService
from eidolon_cli.organization_setup_subscription_catalog import CatalogError, SubscriptionCatalog
from eidolon_cli.organization_setup_subscription_store import SubscriptionStore
from tests.eidolon_cli.subscription_native_fixture import BoundaryDiagnostics, SyntheticEdges
from tests.eidolon_cli.test_organization_setup_subscription_transport import (
    event, install, prepared, response, transport, wire,  # noqa: F401
)


def passive_status_holder(store, monkeypatch):
    """Pause a real non-mutating status read while its real flock is held."""
    other = SubscriptionStore(store.profile, backend=store._backend, clock=store._clock)
    original = other._state
    held, release = threading.Event(), threading.Event()
    failures = []

    def paused_state():
        value = original()
        held.set()
        assert release.wait(10), 'Synthetic status barrier was not released'
        return value

    monkeypatch.setattr(other, '_state', paused_state)

    def read():
        try:
            assert other.status()['state'] == 'active'
        except BaseException as error:
            failures.append(error)

    worker = threading.Thread(target=read)

    def start():
        worker.start()
        assert held.wait(5), 'Synthetic status read did not acquire the store lock'

    def close():
        release.set()
        if worker.ident is not None:
            worker.join(5)
            assert not worker.is_alive()
        assert not failures

    return start, close


def read_trace(evidence):
    return [json.loads(line) for line in (evidence / 'subscription-boundaries.jsonl').read_text().splitlines()]


def assert_status_contention(rows):
    denials = [row for row in rows if row.get('stage') == 'store_lock'
               and row.get('errorClass') == 'BlockingIOError']
    assert denials, rows
    assert any(any(holder['stage'] == 'store_status' and holder['lane'] != row['lane']
                   for holder in row['holders']) for row in denials), rows


@pytest.mark.skipif(importlib.util.find_spec('fcntl') is None,
                    reason='Real POSIX flock probe requires fcntl; no Windows runtime support claim')
def test_catalog_send_denied_during_real_passive_status_lock(prepared, tmp_path, monkeypatch):
    _, _, original_lease = prepared
    store, binding = original_lease._store, original_lease._binding
    owner = OwnerContext(str(store.profile), binding.organization_id, binding.owner_id, 'a' * 64,
        object(), object(), lambda: True, setup_guard=lambda: True, lease_generation_guard=lambda: True)
    service = SubscriptionAuthService(store, SimpleNamespace(installation_id=lambda: binding.host_id),
                                      owner, synthetic=True, clock=store._clock)
    catalog = SubscriptionCatalog(service, owner, clock=store._clock)
    start, release = passive_status_holder(store, monkeypatch)
    diagnostics = BoundaryDiagnostics(tmp_path)
    guard = SyntheticEdges(store.profile)
    guard.install_network_guards()
    sends = []

    class Socket:
        def sendall(self, value):
            sends.append(True)

        def makefile(self, mode):
            body = json.dumps({'models': []}).encode()
            return io.BytesIO(b'HTTP/1.1 200 OK\r\nContent-Length: ' + str(len(body)).encode()
                              + b'\r\n\r\n' + body)

        def shutdown(self, _):
            pass

        def close(self):
            pass

    def connect(connection):
        connection.sock = Socket()
        start()

    monkeypatch.setattr(http.client.HTTPSConnection, 'connect', connect)
    diagnostics.install()
    try:
        assert diagnostics.enabled
        with pytest.raises(CatalogError):
            catalog.load(1)
        assert sends == []
    finally:
        try:
            release()
        finally:
            diagnostics.close()
            original_lease.close()
            guard.close()
    rows = read_trace(tmp_path)
    assert_status_contention(rows)
    assert any(row['stage'] == 'lease_send_gate' and row['event'] == 'returned_false' for row in rows)
    assert any(row['stage'] == 'catalog_http_send' and row.get('errorClass') == 'HttpError' for row in rows)


@pytest.mark.skipif(importlib.util.find_spec('fcntl') is None,
                    reason='Real POSIX flock probe requires fcntl; no Windows runtime support claim')
def test_terminal_reply_denied_during_real_passive_status_lock(prepared, tmp_path, monkeypatch):
    capability, admission, lease = prepared
    start, release = passive_status_holder(lease._store, monkeypatch)
    diagnostics = BoundaryDiagnostics(tmp_path)
    guard = SyntheticEdges(lease._store.profile)
    guard.install_network_guards()
    install(monkeypatch, wire(event(response=response())))
    admission.on_account = start
    diagnostics.install()
    try:
        assert diagnostics.enabled
        result = transport.send_once(capability)
        assert result.text is None
        assert result.accounting_committed is True
        assert result.observation.terminal_kind == 'completed'
        assert result.delivery_error_code == 'stale_binding'
        assert len(admission.dispatches) == len(admission.observations) == 1
    finally:
        try:
            release()
        finally:
            diagnostics.close()
            guard.close()
    assert_status_contention(read_trace(tmp_path))


def test_boundary_trace_does_not_export_exception_text_or_unapproved_attributes(tmp_path, monkeypatch):
    secret = 'synthetic-private-value-must-not-appear'

    class UnknownError(RuntimeError):
        @property
        def reason(self):
            raise AssertionError('Unapproved error attributes must not be inspected')

    def observed(error):
        raise error

    monkeypatch.setattr(BoundaryDiagnostics, '_STAGES', {__name__: {observed.__qualname__: 'catalog_load'}})
    diagnostics = BoundaryDiagnostics(tmp_path)
    diagnostics.install()
    installed_text = (tmp_path / 'subscription-boundaries.jsonl').read_text()
    try:
        assert diagnostics.enabled
        for error in [ValueError(secret), UnknownError(secret)]:
            with pytest.raises(type(error)):
                observed(error)
        # Observed critical frames buffer only; no persistence until safe flush.
        assert (tmp_path / 'subscription-boundaries.jsonl').read_text() == installed_text
    finally:
        diagnostics.close()
    text = (tmp_path / 'subscription-boundaries.jsonl').read_text()
    assert secret not in text
    rows = read_trace(tmp_path)
    assert {row['errorClass'] for row in rows if row['event'] == 'exception'} == {'ValueError', 'other'}
    assert all(row['reason'] == 'other' for row in rows if row['event'] == 'exception')
    for _ in range(600):
        diagnostics.emit({'stage': 'observer', 'event': 'enter'})
    diagnostics.flush()
    rows = read_trace(tmp_path)
    assert len(rows) == 513 and rows[0]['event'] == 'overflow'


def test_boundary_trace_covers_existing_threads_and_restores(tmp_path, monkeypatch):
    """The gateway can already have workers when the synthetic edge is installed."""
    import sys

    if not hasattr(threading, 'settrace_all_threads'):
        pytest.skip('Existing-thread tracing requires Python 3.12')
    ready, proceed = threading.Event(), threading.Event()
    failures = []

    def observed():
        return False

    def worker():
        ready.set()
        try:
            assert proceed.wait(5)
            assert observed() is False
        except BaseException as error:
            failures.append(error)

    monkeypatch.setattr(BoundaryDiagnostics, '_STAGES', {__name__: {observed.__qualname__: 'lease_send_gate'}})
    previous = sys.gettrace(), threading.gettrace()
    thread = threading.Thread(target=worker)
    thread.start()
    diagnostics = BoundaryDiagnostics(tmp_path)
    try:
        assert ready.wait(5)
        diagnostics.install()
        assert diagnostics.enabled
        proceed.set()
        thread.join(5)
        assert not thread.is_alive() and not failures
    finally:
        proceed.set()
        thread.join(5)
        diagnostics.close()
    assert (sys.gettrace(), threading.gettrace()) == previous
    rows = read_trace(tmp_path)
    assert any(row.get('existingThreadsCovered') is True for row in rows)
    assert any(row.get('stage') == 'lease_send_gate' and row['event'] == 'returned_false' for row in rows)



@pytest.mark.parametrize('stage', ['gateway_rpc', 'team_rpc'])
def test_boundary_trace_flushes_registered_rebound_handlers_before_cleanup(tmp_path, monkeypatch, stage):
    """Match production HandlerRegistry's exact code despite replaced globals."""
    import sys
    from types import FunctionType, ModuleType
    from tui_gateway.method_ctx import HandlerRegistry

    previous_server = sys.modules.get('tui_gateway.server')
    # Restore the module before any autouse fixture finalizer can inspect it.
    with monkeypatch.context() as patch:
        module = ModuleType(__name__ + '.synthetic_split')
        exec('def _method():\n'
             ' def handler(rid, params):\n'
             '  try:\n'
             '   raise ValueError("synthetic-private-error-never-export")\n'
             '  except ValueError:\n'
             '   return {"ok": False}\n'
             ' return handler\n', vars(module))
        patch.setitem(sys.modules, module.__name__, module)
        handler = module._method()
        patch.setattr(BoundaryDiagnostics, '_STAGES', {
            module.__name__: {handler.__qualname__: stage}})
        registry = HandlerRegistry()
        registry.method('synthetic')(handler)
        server = ModuleType('tui_gateway.server')
        server._methods = {}
        patch.setitem(sys.modules, server.__name__, server)
        registry.install(server)
        rebound = server._methods['synthetic']
        assert rebound.__code__ is handler.__code__
        assert rebound.__globals__ is vars(server)
        diagnostics = BoundaryDiagnostics(tmp_path)
        diagnostics.install()
        try:
            assert diagnostics.enabled
            results = []
            worker = threading.Thread(target=lambda: results.append(rebound(None, None)))
            worker.start()
            worker.join(5)
            assert not worker.is_alive() and results == [{'ok': False}]
            # Native afterEach reads while the gateway still runs, before close().
            rows = read_trace(tmp_path)
            assert any(row['stage'] == stage and row.get('errorClass') == 'ValueError' for row in rows)
            assert 'synthetic-private-error-never-export' not in json.dumps(rows)
            before = diagnostics.count
            impostor = FunctionType(handler.__code__, {'__name__': 'tui_gateway.server'})
            assert impostor(None, None) == {'ok': False}
            assert diagnostics.count == before  # Same name/code, wrong globals: ignored.
        finally:
            diagnostics.close()
    assert sys.modules.get('tui_gateway.server') is previous_server


@pytest.mark.parametrize('child_kind', ['file', 'directory'])
@pytest.mark.skipif(importlib.util.find_spec('fcntl') is None,
                    reason='Real POSIX directory metadata probe requires fcntl platform')
def test_identity_diagnostic_observes_real_sibling_change_without_weakening_guard(tmp_path, monkeypatch, child_kind):
    import os
    from eidolon_cli import organization_setup_subscription_store as store

    ancestor = tmp_path / 'synthetic-private-ancestor-name'
    home = ancestor / 'profile'
    home.mkdir(parents=True, mode=0o700)
    evidence = tmp_path / 'evidence'
    evidence.mkdir()
    original = os.open
    diagnostics = BoundaryDiagnostics(evidence, home=home)
    before = ancestor.lstat()

    def open_with_sibling(path, flags, *args, **kwargs):
        if path == ancestor:
            sibling = ancestor / 'synthetic-private-sibling-name'
            if child_kind == 'directory':
                sibling.mkdir(mode=0o700)
            else:
                sibling.write_bytes(b'synthetic')
        return original(path, flags, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, 'open', open_with_sibling)
        diagnostics.install()
        try:
            with pytest.raises(store.StoreError) as error:
                with store._checked_acl_descriptor(ancestor):
                    pytest.fail('A mismatched descriptor must never be yielded')
            assert error.value.reason == 'unsafe_filesystem'
        finally:
            diagnostics.close()
    after = ancestor.lstat()
    changed_fields = dict(zip(
        ('device', 'inode', 'mode', 'owner', 'group', 'links', 'changeTime'),
        (a != b for a, b in zip(store._file_identity(before), store._file_identity(after)))))
    assert not any(changed_fields[name] for name in ('device', 'inode', 'mode', 'owner', 'group'))
    assert any(changed_fields.values())
    rows = read_trace(evidence)
    identities = [row['identity'] for row in rows if row['event'] == 'identity_mismatch']
    assert len(identities) == 1
    assert identities[0] == {
        'comparison': 'initial_fd', 'targetKind': 'directory', 'targetScope': 'ancestor',
        'changedFields': changed_fields}
    assert 'synthetic-private-' not in json.dumps(rows)


def test_identity_observer_rejects_wrong_types_sites_and_arbitrary_local_objects(tmp_path):
    import os
    from eidolon_cli import organization_setup_subscription_store as store

    accesses = []

    class Uninspectable:
        def __getattribute__(self, name):
            accesses.append(name)
            raise AssertionError('Arbitrary local objects must not be inspected')

    home = tmp_path / 'synthetic-private-path'
    home.mkdir(mode=0o700)
    before = home.lstat()
    original = store._file_identity(before)
    assert type(before) is os.stat_result and all(type(value) is int for value in original)
    parent = SimpleNamespace(f_code=store._checked_acl_descriptor.__wrapped__.__code__,
        f_globals=vars(store), f_lineno=148, f_locals={'before': before, 'path': home})
    frame = SimpleNamespace(f_code=store._file_identity.__code__, f_globals=vars(store),
                            f_lineno=137, f_back=parent)
    diagnostics = BoundaryDiagnostics(tmp_path, home=home)
    diagnostics.install()
    try:
        fields = ['device', 'inode', 'mode', 'owner', 'group', 'links', 'changeTime']
        for index, field in enumerate(fields):
            changed = list(original)
            changed[index] += 1
            # Exercise the actual observer dispatch, code checks and serialization.
            diagnostics.trace(frame, 'return', tuple(changed))
            identity = diagnostics.rows[-1]['identity']
            assert identity['comparison'] == 'initial_fd'
            assert identity['targetScope'] == 'profile'
            assert identity['changedFields'] == {name: name == field for name in fields}
        count = diagnostics.count
        diagnostics.trace(frame, 'return', original)  # No stale prior operand is retained.
        diagnostics.trace(frame, 'return', (*original[:6], Uninspectable()))
        parent.f_locals = {'before': Uninspectable(), 'path': Uninspectable()}
        diagnostics.trace(frame, 'return', tuple(changed))
        parent.f_lineno = 147
        parent.f_locals = Uninspectable()
        diagnostics.trace(frame, 'return', tuple(changed))
        parent.f_code = (lambda: None).__code__
        parent.f_lineno = 148
        diagnostics.trace(frame, 'return', tuple(changed))
        assert diagnostics.count == count
        parent.f_code = store._checked_acl_descriptor.__wrapped__.__code__
        parent.f_locals = {'before': before, 'path': Uninspectable()}
        diagnostics.trace(frame, 'return', tuple(changed))
        assert diagnostics.rows[-1]['identity']['targetScope'] == 'other'
        parent.f_locals = {'before': before, 'path': home}
        for code, line, comparison in [
                (store._checked_acl_descriptor.__wrapped__.__code__, 151, 'final_fd'),
                (store._checked_acl_descriptor.__wrapped__.__code__, 152, 'final_path'),
                (store._check_darwin_acl.__code__, 203, 'native_acl')]:
            parent.f_code, parent.f_lineno = code, line
            diagnostics.trace(frame, 'return', tuple(changed))
            assert diagnostics.rows[-1]['identity']['comparison'] == comparison
        assert not hasattr(diagnostics, 'identity_pairs')
        # Scope classification is lexical and uses concrete Path objects only.
        assert diagnostics.identity_scope(home / '.setup-subscription-private' / 'entry') == 'private_store'
        assert diagnostics.identity_scope(home.parent) == 'ancestor'
        assert diagnostics.identity_scope(home.parent / 'unrelated') == 'other'
        assert diagnostics.identity_scope(Uninspectable()) == 'other'
        assert accesses == []
    finally:
        diagnostics.close()
    encoded = json.dumps(read_trace(tmp_path))
    assert 'synthetic-private-' not in encoded
    assert str(home) not in encoded
