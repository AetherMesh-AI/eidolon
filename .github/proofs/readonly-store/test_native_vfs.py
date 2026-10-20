"""Host-neutral store/VFS proof; never claims Windows subscription eligibility."""
from contextlib import closing
import ctypes as C
import json
import os
from pathlib import Path
import sqlite3
import threading

import pytest

from eidolon_cli.organization_store import OrganizationStore
from native_sqlite import I, P, LOCK, CLOSE, Methods, VFS, checked_methods, checked_vfs, runtime
from vfs_probe import ClosePause


def test_registered_vfs_witness_uses_python_sqlite_instance():
    value = runtime().receipt
    assert value['sameInstanceWitness']['pythonOpenObserved'] is True
    assert value['sameInstanceWitness']['defaultUnchanged'] is True
    assert value['sameInstanceWitness']['restored'] is True
    assert value['library']['sha256'] and value['sourceId']
    assert value['vfs'] == ('win32' if os.name == 'nt' else 'unix')


@pytest.mark.parametrize('version', [0, 1, 2, 4])
def test_method_version_refused_before_full_table_copy(version, monkeypatch):
    # Deliberately allocate only the header. Reading the full table is forbidden.
    header = I(version)
    def forbidden(*args):
        pytest.fail('Unsupported method header reached full table copy')
    monkeypatch.setattr(Methods, 'from_buffer_copy', forbidden)
    with pytest.raises(RuntimeError, match='before full table copy'):
        checked_methods(C.addressof(header))


def test_null_method_pointer_is_refused():
    with pytest.raises(RuntimeError, match='Missing native I/O'):
        checked_methods(None)


@pytest.mark.parametrize('version', [1, 2, 4])
def test_vfs_version_refused_before_full_table_copy(version, monkeypatch):
    header = I(version)
    def forbidden(*args):
        pytest.fail('Unsupported VFS header reached full table copy')
    monkeypatch.setattr(VFS, 'from_buffer_copy', forbidden)
    with pytest.raises(RuntimeError, match='before full table copy'):
        checked_vfs(C.cast(C.pointer(header), C.POINTER(VFS)))


def test_store_readonly_close_comparison_requires_writable_positive(tmp_path):
    """One test owns both phases, so a negative cannot pass without its positive."""
    results = []
    for readonly in (False, True):
        path = tmp_path / ('readonly.db' if readonly else 'writable.db')
        keeper = sqlite3.connect(path, isolation_level=None)
        keeper.execute('PRAGMA journal_mode=WAL')
        keeper.execute('CREATE TABLE t(a INTEGER)')
        keeper.execute('INSERT INTO t VALUES(7)')
        store = object.__new__(OrganizationStore)
        store.path = path
        ready, close_now = threading.Event(), threading.Event()
        failures, mutation_errors = [], []
        with ClosePause(pause_readonly_refusal=readonly) as pause:
            def holder():
                try:
                    with store._connect(write=not readonly) as conn:
                        assert conn.execute('SELECT a FROM t').fetchone()[0] == 7
                        if readonly:
                            assert conn.execute('PRAGMA query_only').fetchone()[0] == 0
                            try:
                                conn.execute('DELETE FROM t WHERE 0')
                            except sqlite3.OperationalError as error:
                                mutation_errors.append(error.sqlite_errorcode)
                            else:
                                raise AssertionError('Readonly main-file handle accepted mutation')
                        ready.set()
                        assert close_now.wait(5), 'Holder close was not scheduled'
                        # Filesystem identity, not string equality, selects the file.
                        alias_dir = path.parent / 'alias-component'
                        alias_dir.mkdir(exist_ok=True)
                        pause.arm_close(alias_dir / '..' / path.name)
                    pause.disarm_close()
                except BaseException as error:
                    failures.append(type(error).__name__ + ': ' + str(error))
                    ready.set()
            thread = threading.Thread(target=holder, name='native-store-close', daemon=True)
            thread.start()
            try:
                assert ready.wait(5) and not failures, failures
                keeper.close()
                close_now.set()
                observed = pause.readonly_refused if readonly else pause.acquired
                assert observed.wait(5), pause.events
                read_error = None
                rows = None
                try:
                    with closing(sqlite3.connect(path.absolute().as_uri() + '?mode=ro',
                                                 uri=True, timeout=0, isolation_level=None)) as reader:
                        assert reader.execute('PRAGMA busy_timeout').fetchone()[0] == 0
                        rows = reader.execute('SELECT a FROM t').fetchall()
                except sqlite3.OperationalError as error:
                    read_error = error.sqlite_errorcode
                if readonly:
                    assert results and results[0]['positiveExclusiveObserved'] is True
                    assert pause.hits == 0 and pause.refusal_hits == 1
                    assert pause.events == [{'phase': 'inside_connection_close',
                                             'lock': 'exclusive', 'result': sqlite3.SQLITE_IOERR_LOCK}]
                    assert read_error is None and rows == [(7,)]
                    assert mutation_errors == [sqlite3.SQLITE_READONLY]
                    assert pause.target_open_flags == {
                        'requestedReadonly': True, 'requestedReadwrite': False,
                        'returnedReadonly': True, 'returnedFlagsPresent': True,
                        'methodVersion': 3}
                else:
                    assert pause.hits == 1 and pause.refusal_hits == 0
                    assert pause.events == [{'phase': 'inside_connection_close',
                                             'lock': 'exclusive', 'result': 0}]
                    assert read_error == sqlite3.SQLITE_BUSY and rows is None
                    assert pause.target_open_flags == {
                        'requestedReadonly': False, 'requestedReadwrite': True,
                        'returnedReadonly': False, 'returnedFlagsPresent': True,
                        'methodVersion': 3}
                results.append({'readonly': readonly, 'positiveExclusiveObserved': pause.hits == 1,
                                'events': list(pause.events), 'readError': read_error,
                                'readSucceeded': rows == [(7,)], 'openFlags': pause.target_open_flags,
                                'fileIdentity': pause.target_identity,
                                'mutationErrors': mutation_errors, 'identity': pause.identity()})
            finally:
                close_now.set()
                pause.release.set()
                thread.join(5)
                keeper.close()
                assert not thread.is_alive() and not failures, failures
        assert pause.restored
        with closing(sqlite3.connect(path.absolute().as_uri() + '?mode=ro', uri=True,
                                     timeout=0, isolation_level=None)) as after:
            assert after.execute('SELECT a FROM t').fetchall() == [(7,)]
    assert [row['readonly'] for row in results] == [False, True]
    (Path(os.environ['EIDOLON_PROOF_OUTPUT']) / 'store-close-comparison.json').write_text(
        json.dumps({'scope': 'store_level_native_sqlite_not_subscription_eligibility',
                    'results': results}, indent=2) + '\n', encoding='utf-8')


def test_main_open_is_delegated_once_when_observation_fails(tmp_path):
    pause = ClosePause()
    original = pause.open_original
    delegations = []
    def delegate(*args):
        delegations.append(True)
        return original(*args)
    pause.open_original = delegate
    pause.fault = lambda stage: (_ for _ in ()).throw(RuntimeError('observer error'))
    with pytest.raises(AssertionError, match='Probe observation incomplete'):
        with pause:
            with closing(sqlite3.connect(tmp_path / 'state.db')) as conn:
                assert conn.execute('SELECT 7').fetchone() == (7,)
    assert delegations == [True] and pause.delegations['open'] == 1
    assert pause.restored and not pause.files


@pytest.mark.parametrize('operation,result', [('lock', 0), ('lock', 5),
                                           ('lock', sqlite3.SQLITE_IOERR_LOCK),
                                           ('close', 0), ('close', sqlite3.SQLITE_IOERR)])
def test_callback_unit_preserves_one_delegate_and_exact_return_on_error(tmp_path, operation, result):
    """Adapter unit evidence only: fixed C delegates, not a native-lock proof."""
    path = tmp_path / 'callback-unit.db'
    path.touch()
    calls = {'open': [], 'lock': [], 'close': []}
    @LOCK
    def native_lock(ptr, level):
        calls['lock'].append((ptr, level))
        return result if operation == 'lock' else 0
    @CLOSE
    def native_close(ptr):
        calls['close'].append(ptr)
        return result if operation == 'close' else 0
    native = Methods()
    native.iVersion = 3
    native.xLock, native.xClose = C.cast(native_lock, P).value, C.cast(native_close, P).value
    header = P(C.addressof(native))
    file = C.addressof(header)
    filename = C.create_string_buffer(str(path).encode('utf-8') + b'\0mode\0ro\0\0')
    name = C.addressof(filename)
    outflags = I()
    def original(vfs, actual_name, actual_file, flags, actual_outflags):
        calls['open'].append((actual_name, actual_file))
        assert actual_name == name and actual_file == file
        actual_outflags[0] = flags
        return 0
    pause = ClosePause()
    pause.open_original = original
    with pytest.raises(AssertionError, match='Probe observation incomplete'):
        with pause:
            assert pause._open(None, name, file, 0x102, C.pointer(outflags)) == 0
            pause.arm_close(path)
            def fail(stage):
                if stage == ('lock_observation' if operation == 'lock' else 'close_tracking'):
                    raise RuntimeError('unit observer failure')
            pause.fault = fail
            pause.release.set()
            proxy = Methods.from_address(header.value)
            if operation == 'lock':
                assert LOCK(proxy.xLock)(file, 4) == result
                assert calls['lock'] == [(file, 4)]
            assert CLOSE(proxy.xClose)(file) == (result if operation == 'close' else 0)
    assert calls['open'] == [(name, file)] and calls['close'] == [file]
    assert pause.restored and not pause.files
