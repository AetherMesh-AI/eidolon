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
            cloned_code = handler.__code__.replace()
            assert cloned_code == handler.__code__ and cloned_code is not handler.__code__
            cloned = FunctionType(cloned_code, vars(server))
            assert cloned(None, None) == {'ok': False}
            assert diagnostics.count == before  # Primed cache + exact server globals still requires identity.
        finally:
            diagnostics.close()
    assert sys.modules.get('tui_gateway.server') is previous_server


@pytest.mark.parametrize('change_kind', ['permissions', 'directory'])
@pytest.mark.skipif(importlib.util.find_spec('fcntl') is None,
                    reason='Real POSIX directory metadata probe requires fcntl platform')
def test_identity_diagnostic_observes_real_metadata_change_without_weakening_guard(tmp_path, monkeypatch, change_kind):
    import os
    import stat
    from eidolon_cli import organization_setup_subscription_store as store
    from eidolon_cli import organization_setup_subscription_filesystem as filesystem

    ancestor = tmp_path / 'synthetic-private-ancestor-name'
    home = ancestor / 'profile'
    home.mkdir(parents=True, mode=0o700)
    evidence = tmp_path / 'evidence'
    evidence.mkdir()
    original = os.open
    diagnostics = BoundaryDiagnostics(evidence, home=home)
    before = ancestor.lstat()

    def open_with_metadata_change(path, flags, *args, **kwargs):
        if path == ancestor:
            if change_kind == 'directory':
                sibling = ancestor / 'synthetic-private-sibling-name'
                sibling.mkdir(mode=0o700)
                assert ancestor.lstat().st_nlink != before.st_nlink
            else:
                # A regular-file sibling can leave every identity field unchanged
                # within one filesystem clock tick. Change a mode bit instead;
                # ctime-only observation is covered independently below.
                mode = stat.S_IMODE(before.st_mode) ^ stat.S_IXGRP
                ancestor.chmod(mode)
                assert stat.S_IMODE(ancestor.lstat().st_mode) == mode
        return original(path, flags, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, 'open', open_with_metadata_change)
        diagnostics.install()
        try:
            with pytest.raises(store.StoreError) as error:
                with filesystem._checked_acl_descriptor(ancestor):
                    pytest.fail('A mismatched descriptor must never be yielded')
            assert error.value.reason == 'unsafe_filesystem'
        finally:
            diagnostics.close()
    after = ancestor.lstat()
    changed_fields = dict(zip(
        ('device', 'inode', 'mode', 'owner', 'group', 'links', 'changeTime'),
        (a != b for a, b in zip(filesystem._file_identity(before), filesystem._file_identity(after)))))
    assert not any(changed_fields[name] for name in ('device', 'inode', 'owner', 'group'))
    assert changed_fields['mode'] is (change_kind == 'permissions')
    assert changed_fields['links'] is (change_kind == 'directory')
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
    from eidolon_cli import organization_setup_subscription_filesystem as filesystem

    accesses = []

    class Uninspectable:
        def __getattribute__(self, name):
            accesses.append(name)
            raise AssertionError('Arbitrary local objects must not be inspected')

    home = tmp_path / 'synthetic-private-path'
    home.mkdir(mode=0o700)
    before = home.lstat()
    original = filesystem._file_identity(before)
    assert type(before) is os.stat_result and all(type(value) is int for value in original)
    parent = SimpleNamespace(f_code=filesystem._checked_acl_descriptor.__wrapped__.__code__,
        f_globals=vars(filesystem), f_lineno=52, f_locals={'before': before, 'path': home})
    frame = SimpleNamespace(f_code=filesystem._file_identity.__code__, f_globals=vars(filesystem),
                            f_lineno=39, f_back=parent)
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
        parent.f_lineno = 51
        parent.f_locals = Uninspectable()
        diagnostics.trace(frame, 'return', tuple(changed))
        parent.f_code = (lambda: None).__code__
        parent.f_lineno = 52
        diagnostics.trace(frame, 'return', tuple(changed))
        assert diagnostics.count == count
        parent.f_code = filesystem._checked_acl_descriptor.__wrapped__.__code__
        parent.f_locals = {'before': before, 'path': Uninspectable()}
        diagnostics.trace(frame, 'return', tuple(changed))
        assert diagnostics.rows[-1]['identity']['targetScope'] == 'other'
        parent.f_locals = {'before': before, 'path': home}
        for code, line, comparison in [
                (filesystem._checked_acl_descriptor.__wrapped__.__code__, 55, 'final_fd'),
                (filesystem._checked_acl_descriptor.__wrapped__.__code__, 56, 'final_path'),
                (filesystem._read_darwin_acl.__code__, 151, 'native_acl')]:
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


def use_exact_sqlite_factory(monkeypatch, path):
    """Keep the observer's exact class proof under CI's opt-in connect wrapper.

    The timing plugin substitutes a subclass only when no factory is requested.
    Pass an explicit base factory through that wrapper for these holder proofs;
    do not broaden the observer's trust or bypass connection instrumentation.
    """
    import sqlite3
    connect = sqlite3.connect

    def explicit_factory(*args, **kwargs):
        if (args and type(args[0]) is type(path) and args[0] == path
                and len(args) <= 5 and 'factory' not in kwargs):
            kwargs['factory'] = sqlite3.Connection
        return connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, 'connect', explicit_factory)


def test_real_coordinated_writer_deadline_blocks_dispatch_body_and_records_safe_primary_code(tmp_path, monkeypatch):
    from contextlib import contextmanager
    import sqlite3
    import time
    from eidolon_cli import organization_setup_subscription_admission as admission
    from eidolon_cli.organization_store import OrganizationStore
    from tui_gateway import organization_credential_transport as credential

    home = tmp_path / 'synthetic-private-profile'
    (home / 'organization').mkdir(parents=True)
    evidence = tmp_path / 'evidence'
    evidence.mkdir()
    store = OrganizationStore.__new__(OrganizationStore)
    store.path, store.settings = home / 'organization' / 'state.db', object()
    use_exact_sqlite_factory(monkeypatch, store.path)
    diagnostics = BoundaryDiagnostics(evidence, home=home)
    results, body_calls, failures = [], [], []

    @contextmanager
    def publication(*_):
        yield

    def unexpected_body(*_args, **_kwargs):
        body_calls.append(True)
        raise AssertionError('Dispatch body must not run before ledger acquisition')

    def dispatch():
        try:
            results.append(admission._dispatch_job(home, object(), SimpleNamespace(owner_generation=object()),
                None, None, threading.Event(), time.monotonic() + 5))
        except BaseException as error:
            failures.append(error)

    with monkeypatch.context() as patch:
        patch.setattr(credential, 'owner_publication', publication)
        patch.setattr(admission, '_exact_attempt', unexpected_body)
        diagnostics.install()
        try:
            # Actual OrganizationStore._write is the same transaction helper used
            # by the idle scheduler. Its yielded connection proves acquisition.
            with store._write() as connection:
                assert type(connection) is sqlite3.Connection
                assert len(diagnostics.ledger_holders) == 1
                worker = threading.Thread(target=dispatch)
                worker.start()
                worker.join(5)
                assert not worker.is_alive()
                assert results == [False] and not body_calls and not failures
            assert diagnostics.ledger_holders == {}
            with admission._ledger_connection(home, write=True):
                pass  # The real competing writer was released, without a retry.
        finally:
            diagnostics.close()
    rows = read_trace(evidence)
    errors = [row for row in rows if row.get('stage') == 'ledger_connection'
              and row.get('errorClass') == 'LedgerWriterUnavailable']
    assert len(errors) == 1 and errors[0]['sqlitePrimaryCode'] == 'SQLITE_BUSY'
    assert errors[0]['reason'] == 'deadline'  # Queue refused before any BEGIN/body.
    assert len(errors[0]['ledgerHolders']) == 1
    assert errors[0]['ledgerHolders'][0]['lane'] != errors[0]['lane']
    assert any(row['stage'] == 'dispatch_job' and row['event'] == 'returned_false' for row in rows)
    assert 'synthetic-private-' not in json.dumps(rows)


def test_sqlite_error_diagnostics_use_exact_class_and_closed_primary_codes(tmp_path, monkeypatch):
    import sqlite3
    accesses = []
    secret = 'synthetic-private-error-name-and-message'

    class UnknownError(sqlite3.OperationalError):
        def __getattribute__(self, name):
            if name in {'sqlite_errorcode', 'sqlite_errorname', 'reason'}:
                accesses.append(name)
                raise AssertionError('Unapproved exception attributes must not be inspected')
            return super().__getattribute__(name)

    class AttributeMap(dict):
        def get(self, *args, **kwargs):
            accesses.append('get')
            raise AssertionError('Attribute dictionaries must not execute overridden getters')

    def observed(error):
        raise error

    monkeypatch.setattr(BoundaryDiagnostics, '_STAGES', {__name__: {observed.__qualname__: 'ledger_connection'}})
    diagnostics = BoundaryDiagnostics(tmp_path)
    diagnostics.install()
    expected = []
    try:
        for code, label in [
                (sqlite3.SQLITE_BUSY | (2 << 8), 'SQLITE_BUSY'),
                (sqlite3.SQLITE_LOCKED, 'SQLITE_LOCKED'),
                (sqlite3.SQLITE_CANTOPEN, 'SQLITE_CANTOPEN'),
                (sqlite3.SQLITE_READONLY, 'SQLITE_READONLY'),
                (sqlite3.SQLITE_IOERR, 'SQLITE_IOERR'),
                (sqlite3.SQLITE_FULL, 'SQLITE_FULL'),
                (sqlite3.SQLITE_NOMEM, 'other'),
                (secret, 'other'), (True, 'other'), (-251, 'other'), (2**40, 'other')]:
            error = sqlite3.OperationalError(secret)
            error.sqlite_errorcode, error.sqlite_errorname = code, secret
            with pytest.raises(sqlite3.OperationalError):
                observed(error)
            expected.append(label)
        error = sqlite3.OperationalError(secret)
        error.__dict__ = AttributeMap(sqlite_errorcode=sqlite3.SQLITE_BUSY, reason=secret)
        with pytest.raises(sqlite3.OperationalError):
            observed(error)
        expected.append('other')
        with pytest.raises(UnknownError):
            observed(UnknownError(secret))
        expected.append('other')
        assert accesses == []
    finally:
        diagnostics.close()
    rows = [row for row in read_trace(tmp_path) if row['event'] == 'exception']
    assert [row['sqlitePrimaryCode'] for row in rows] == expected
    assert rows[-1]['errorClass'] == 'other'
    assert secret not in json.dumps(rows)


def test_ledger_holder_observer_requires_acquired_exact_code_and_clears_on_exit(tmp_path, monkeypatch):
    from contextlib import contextmanager, closing
    import sqlite3
    from eidolon_cli import organization_store as module

    store = module.OrganizationStore.__new__(module.OrganizationStore)
    store.path, store.settings = tmp_path / 'synthetic-private-ledger.db', object()
    use_exact_sqlite_factory(monkeypatch, store.path)
    diagnostics = BoundaryDiagnostics(tmp_path)
    diagnostics.install()
    try:
        with store._write() as connection:
            assert type(connection) is sqlite3.Connection
            assert len(diagnostics.ledger_holders) == 1
        assert diagnostics.ledger_holders == {}
        with pytest.raises(RuntimeError):
            with store._write():
                assert len(diagnostics.ledger_holders) == 1
                raise RuntimeError('synthetic-private-error')
        assert diagnostics.ledger_holders == {}

        @contextmanager
        def already_in_transaction():
            with closing(sqlite3.connect(store.path, isolation_level=None)) as connection:
                connection.execute('BEGIN IMMEDIATE')
                yield connection

        with monkeypatch.context() as patch:
            patch.setattr(store, '_connect', already_in_transaction)
            with pytest.raises(sqlite3.OperationalError):
                with store._write():
                    pytest.fail('The second BEGIN must fail before yield')
        assert diagnostics.ledger_holders == {}
        frame = SimpleNamespace(f_code=module.OrganizationStore._write.__wrapped__.__code__,
            f_globals=vars(module), f_lineno=199, f_back=None)
        diagnostics.trace(frame, 'return', object())
        assert diagnostics.ledger_holders == {}
        frame.f_code = (lambda: None).__code__
        with closing(sqlite3.connect(store.path)) as connection:
            diagnostics.trace(frame, 'return', connection)
        assert diagnostics.ledger_holders == {}
    finally:
        diagnostics.close()
    encoded = json.dumps(read_trace(tmp_path))
    assert 'synthetic-private-' not in encoded


def test_ledger_holder_observer_rejects_connection_subclass_at_real_yield(tmp_path, monkeypatch):
    import sqlite3
    from eidolon_cli.organization_store import OrganizationStore

    class CustomConnection(sqlite3.Connection):
        pass

    # A different connection wrapper can return its own subclass. Even at the
    # real acquired _write yield, the observer must not trust that new class.
    connect = sqlite3.connect
    def custom_connect(*args, **kwargs):
        return connect(*args, **{**kwargs, 'factory': CustomConnection})

    monkeypatch.setattr(sqlite3, 'connect', custom_connect)
    store = OrganizationStore.__new__(OrganizationStore)
    store.path, store.settings = tmp_path / 'synthetic-private-ledger.db', object()
    use_exact_sqlite_factory(monkeypatch, store.path)
    diagnostics = BoundaryDiagnostics(tmp_path)
    diagnostics.install()
    try:
        assert diagnostics.enabled
        with store._write() as connection:
            assert type(connection) is CustomConnection
            assert diagnostics.ledger_holders == {}
        assert diagnostics.ledger_holders == {}
    finally:
        diagnostics.close()
    assert 'synthetic-private-' not in json.dumps(read_trace(tmp_path))


def test_exact_sqlite_factory_keeps_timing_and_nonselected_connections_instrumented(tmp_path, monkeypatch):
    from contextlib import closing
    import sqlite3
    from scripts.ci.organization_sqlite_diagnostics import install

    class CustomConnection(sqlite3.Connection):
        pass

    events = []
    restore = install(events.append)
    path = tmp_path / 'selected-ledger.db'
    try:
        with monkeypatch.context() as patch:
            use_exact_sqlite_factory(patch, path)
            with closing(sqlite3.connect(path)) as selected:
                assert type(selected) is sqlite3.Connection
            with closing(sqlite3.connect(tmp_path / 'other-ledger.db')) as other:
                assert isinstance(other, sqlite3.Connection)
                assert type(other) is not sqlite3.Connection
            with closing(sqlite3.connect(path, factory=CustomConnection)) as custom:
                assert type(custom) is CustomConnection
            with closing(sqlite3.connect(path, 5.0, 0, None, True, CustomConnection)) as positional:
                assert type(positional) is CustomConnection
            starts = [row for row in events if row['phase'] == 'connect' and row['boundary'] == 'start']
            ends = [row for row in events if row['phase'] == 'connect' and row['boundary'] == 'end']
            assert len(starts) == len(ends) == 4
            assert all('errorType' not in row for row in ends)
    finally:
        restore()


def test_extracted_identity_source_sites_and_selected_stat_locals_are_exact(tmp_path):
    import ast
    import inspect
    import os
    from eidolon_cli import organization_setup_subscription_filesystem as filesystem

    # These are deliberate closed source sites, not automatic discovery of new
    # locals. Any production refactor must explicitly review this observer map.
    sites = {
        52: ('acl_descriptor', 'initial_fd', 'before'),
        55: ('acl_descriptor', 'final_fd', 'before'),
        56: ('acl_descriptor', 'final_path', 'before'),
        151: ('native_acl_read', 'native_acl', 'before'),
        77: ('ancestor_acl', 'initial_fd', 'before'),
        88: ('ancestor_acl', 'final_path', 'final_fd'),
        91: ('ancestor_acl', 'native_acl', 'before'),
        92: ('ancestor_acl', 'final_fd', 'before'),
        95: ('ancestor_acl', 'final_fd', 'final_fd'),
        101: ('ancestor_acl', 'native_acl', 'before'),
        104: ('ancestor_acl', 'final_fd', 'before'),
        110: ('ancestor_acl', 'final_fd', 'final_fd'),
        111: ('ancestor_acl', 'final_path', 'final_fd'),
    }
    observer_tree = ast.parse(inspect.getsource(BoundaryDiagnostics))
    assignment = next(node for node in ast.walk(observer_tree)
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'sites'
                                               for target in node.targets))
    actual = ast.literal_eval(assignment.value)
    assert actual == {(stage, line): (comparison, baseline)
        for line, (stage, comparison, baseline) in sites.items()}
    source = ast.parse(inspect.getsource(filesystem))
    calls = {node.lineno for node in ast.walk(source) if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name) and node.func.id == '_file_identity'}
    assert set(sites) <= calls
    codes = {'acl_descriptor': filesystem._checked_acl_descriptor.__wrapped__.__code__,
             'native_acl_read': filesystem._read_darwin_acl.__code__,
             'ancestor_acl': filesystem._check_ancestor_acl.__code__}
    accesses = []
    class Private:
        def __getattribute__(self, name):
            accesses.append(name)
            raise AssertionError('Unapproved local access')
    home = tmp_path / 'synthetic-private-home'
    home.mkdir(mode=0o700)
    before = home.lstat()
    value = list(filesystem._file_identity(before))
    value[-1] += 1
    diagnostics = BoundaryDiagnostics(tmp_path, home=home)
    frame = SimpleNamespace(f_code=filesystem._file_identity.__code__, f_globals=vars(filesystem),
        f_back=None, f_lineno=39)
    diagnostics.install()
    try:
        for line, (stage, comparison, baseline) in sites.items():
            parent = SimpleNamespace(f_code=codes[stage], f_globals=vars(filesystem), f_lineno=line,
                f_locals={'before': Private(), 'final_fd': Private(), 'path': home})
            parent.f_locals[baseline] = before
            if stage == 'native_acl_read':
                parent.f_locals.pop('path')  # No caller-stack path lookup is allowed.
            frame.f_back = parent
            diagnostics.trace(frame, 'return', tuple(value))
            row = diagnostics.rows[-1]['identity']
            assert row['comparison'] == comparison
            assert row['targetScope'] == ('other' if stage == 'native_acl_read' else 'profile')
            count = diagnostics.count
            parent.f_locals[baseline] = Private()
            diagnostics.trace(frame, 'return', tuple(value))
            assert diagnostics.count == count
            parent.f_locals[baseline] = before
            parent.f_globals = {'__name__': filesystem.__name__}
            diagnostics.trace(frame, 'return', tuple(value))
            assert diagnostics.count == count
            parent.f_globals = vars(filesystem)
            parent.f_lineno = 1
            diagnostics.trace(frame, 'return', tuple(value))
            assert diagnostics.count == count
    finally:
        diagnostics.close()
    assert accesses == [] and type(before) is os.stat_result
    diagnostics.close()
    assert 'synthetic-private-home' not in json.dumps(read_trace(tmp_path))


def test_real_uncoordinated_sqlite_writer_still_refuses_before_ledger_body(tmp_path):
    from contextlib import closing
    import sqlite3
    from eidolon_cli import organization_setup_subscription_admission as admission

    home = tmp_path / 'profile'
    (home / 'organization').mkdir(parents=True)
    path = home / 'organization' / 'state.db'
    diagnostics = BoundaryDiagnostics(tmp_path, home=home)
    with closing(sqlite3.connect(path, isolation_level=None)) as writer:
        writer.execute('CREATE TABLE probe(value INTEGER)')
        writer.execute('BEGIN IMMEDIATE')  # Deliberately outside the coordinator.
        diagnostics.install()
        try:
            with pytest.raises(sqlite3.OperationalError) as error:
                with admission._ledger_connection(home, write=True):
                    pytest.fail('External writer must exclude transaction body')
            assert type(error.value) is sqlite3.OperationalError
            assert error.value.sqlite_errorcode & 255 == sqlite3.SQLITE_BUSY
        finally:
            writer.rollback()
            diagnostics.close()
    rows = [row for row in read_trace(tmp_path) if row['event'] == 'exception'
            and row['stage'] == 'ledger_connection']
    assert rows and rows[-1]['errorClass'] == 'OperationalError'
    assert rows[-1]['sqlitePrimaryCode'] == 'SQLITE_BUSY'
    with admission._ledger_connection(home, write=True) as connection:
        connection.execute('INSERT INTO probe VALUES (1)')


def test_writer_error_observer_accepts_only_exact_class_plain_attributes_and_closed_reasons(tmp_path, monkeypatch):
    from eidolon_cli import organization_ledger_writer as writers
    accesses = []
    class AttributeMap(dict):
        def get(self, *args):
            accesses.append('get')
            raise AssertionError('No overridden attribute getter')
    class Unknown(writers.LedgerWriterUnavailable):
        def __getattribute__(self, name):
            if name in {'reason', 'sqlite_errorcode', '__dict__'}:
                accesses.append(name)
                raise AssertionError('No subclass attribute access')
            return super().__getattribute__(name)
    def observed(error):
        raise error
    monkeypatch.setattr(BoundaryDiagnostics, '_STAGES', {__name__: {observed.__qualname__: 'ledger_writer_wait'}})
    diagnostics = BoundaryDiagnostics(tmp_path)
    diagnostics.install()
    try:
        for reason in ['cancelled', 'deadline', 'reentrant', 'queue_full', 'synthetic-private-reason']:
            with pytest.raises(writers.LedgerWriterUnavailable):
                observed(writers.LedgerWriterUnavailable(reason))
        error = writers.LedgerWriterUnavailable('deadline')
        error.__dict__ = AttributeMap(reason='deadline', sqlite_errorcode=5)
        with pytest.raises(writers.LedgerWriterUnavailable):
            observed(error)
        with pytest.raises(Unknown):
            observed(Unknown('synthetic-private-reason'))
    finally:
        diagnostics.close()
    rows = [row for row in read_trace(tmp_path) if row['event'] == 'exception']
    assert [row['reason'] for row in rows] == ['cancelled', 'deadline', 'reentrant', 'queue_full', 'other', 'other', 'other']
    assert [row['sqlitePrimaryCode'] for row in rows] == ['SQLITE_BUSY'] * 5 + ['other', 'other']
    assert rows[-1]['errorClass'] == 'other' and accesses == []
    assert 'synthetic-private-reason' not in json.dumps(rows)


def test_observer_code_cache_rejects_equal_but_distinct_identity_and_caller_code(tmp_path):
    from eidolon_cli import organization_setup_subscription_filesystem as filesystem
    home = tmp_path / 'synthetic-private-home'
    home.mkdir(mode=0o700)
    before = home.lstat()
    value = list(filesystem._file_identity(before))
    value[-1] += 1
    identity_code = filesystem._file_identity.__code__
    caller_code = filesystem._checked_acl_descriptor.__wrapped__.__code__
    cloned_identity, cloned_caller = identity_code.replace(), caller_code.replace()
    assert cloned_identity == identity_code and cloned_identity is not identity_code
    assert cloned_caller == caller_code and cloned_caller is not caller_code
    parent = SimpleNamespace(f_code=caller_code, f_globals=vars(filesystem), f_lineno=52,
                             f_locals={'before': before, 'path': home})
    frame = SimpleNamespace(f_code=identity_code, f_globals=vars(filesystem), f_lineno=39, f_back=parent)
    diagnostics = BoundaryDiagnostics(tmp_path, home=home)
    diagnostics.install()
    try:
        diagnostics.trace(frame, 'return', tuple(value))
        assert diagnostics.rows[-1]['event'] == 'identity_mismatch'
        count = diagnostics.count
        # Both original code identities are now cached. Structural equality must
        # not let a distinct executable code object inherit either authority.
        frame.f_code = cloned_identity
        assert diagnostics.stage(frame) is None
        diagnostics.trace(frame, 'return', tuple(value))
        assert diagnostics.count == count
        frame.f_code, parent.f_code = identity_code, cloned_caller
        assert diagnostics.stage(parent) is None
        diagnostics.trace(frame, 'return', tuple(value))
        assert diagnostics.count == count
        assert len(diagnostics.codes) <= 128
    finally:
        diagnostics.close()
    assert 'synthetic-private-home' not in json.dumps(read_trace(tmp_path))
