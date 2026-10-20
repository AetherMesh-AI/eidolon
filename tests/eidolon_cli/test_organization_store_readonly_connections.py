"""Real SQLite observation permissions, lifecycle and compatibility contracts."""
from contextlib import closing
import hashlib
import os
from pathlib import Path, PureWindowsPath
import sqlite3
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from eidolon_cli.organization_store import OrganizationStore
from eidolon_cli import organization_ledger_writer as writers


def readonly_observation(store):
    # Exercise the exact production default connection with a path-only holder.
    return OrganizationStore._connect(store)


@pytest.fixture
def db_root(tmp_path):
    root = tmp_path / 'connection-ledger'
    root.mkdir()
    return root


def seed(path, *, wal=True):
    with closing(sqlite3.connect(path, isolation_level=None)) as conn:
        if wal:
            assert conn.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
        conn.execute('CREATE TABLE t(a INTEGER)')
        conn.execute('INSERT INTO t VALUES(1)')


@pytest.mark.parametrize('statement', [
    'INSERT INTO t VALUES(2)', 'UPDATE t SET a=2', 'DELETE FROM t',
    'CREATE TABLE extra(a)', 'DROP TABLE t', 'PRAGMA user_version=7',
])
def test_actual_readonly_handle_refuses_mutation_with_query_only_off(db_root, statement):
    path = db_root / 'state.db'
    seed(path)
    with readonly_observation(SimpleNamespace(path=path)) as conn:
        assert conn.row_factory is sqlite3.Row
        assert conn.isolation_level is None and not conn.in_transaction
        assert conn.execute('PRAGMA busy_timeout').fetchone()[0] == 10000
        assert conn.execute('PRAGMA foreign_keys').fetchone()[0] == 1
        assert conn.execute('PRAGMA query_only').fetchone()[0] == 0
        with pytest.raises(sqlite3.OperationalError) as caught:
            conn.execute(statement)
        assert caught.value.sqlite_errorcode == sqlite3.SQLITE_READONLY
        assert [tuple(row) for row in conn.execute('SELECT * FROM t')] == [(1,)]
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM sqlite_schema WHERE name='extra'").fetchone()[0] == 0


@pytest.mark.parametrize('name', ['state.db', 'space # % café.db', pytest.param('literal?mode=rw.db', marks=pytest.mark.skipif(os.name == 'nt', reason='Question mark is not a Windows filename'))])
def test_uri_encoding_selects_the_exact_existing_database(db_root, name, monkeypatch):
    path = db_root / name
    seed(path)
    identity = (path.stat().st_dev, path.stat().st_ino)
    monkeypatch.chdir(db_root)
    with readonly_observation(SimpleNamespace(path=Path(name))) as conn:
        assert [tuple(row) for row in conn.execute('SELECT * FROM t')] == [(1,)]
        assert Path(conn.execute('PRAGMA database_list').fetchone()[2]) == path
        assert (path.stat().st_dev, path.stat().st_ino) == identity
    assert {p.name for p in db_root.iterdir()} <= {name, name+'-wal', name+'-shm'}


def test_windows_uri_encoding_is_explicit_static_evidence_only():
    # Actual Windows native open/ACL behavior remains a separate hosted gate.
    assert PureWindowsPath('C:/space # % ? café/state.db').as_uri() == \
        'file:///C:/space%20%23%20%25%20%3F%20caf%C3%A9/state.db'
    assert PureWindowsPath('//server/share/space #/state.db').as_uri() == \
        'file://server/share/space%20%23/state.db'


@pytest.mark.parametrize('existing_parent', [True, False])
def test_missing_reader_never_creates_ledger_or_directory(db_root, existing_parent):
    path = db_root / ('state.db' if existing_parent else 'absent/state.db')
    with pytest.raises(sqlite3.OperationalError) as caught:
        with readonly_observation(SimpleNamespace(path=path)):
            pytest.fail('A reader must not provision its ledger')
    assert caught.value.sqlite_errorcode == sqlite3.SQLITE_CANTOPEN
    assert not path.exists() and list(db_root.iterdir()) == []


def test_non_wal_reader_never_converts_or_mutates_database(db_root):
    path = db_root / 'state.db'
    seed(path, wal=False)
    before = hashlib.sha256(path.read_bytes()).hexdigest()  # No SQLite handle is live.
    with readonly_observation(SimpleNamespace(path=path)) as conn:
        assert conn.execute('PRAGMA journal_mode').fetchone()[0] == 'delete'
        assert [tuple(row) for row in conn.execute('SELECT * FROM t')] == [(1,)]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before  # All handles closed.
    assert list(db_root.iterdir()) == [path]
    with closing(sqlite3.connect(path, isolation_level=None)) as conn:
        assert conn.execute('PRAGMA journal_mode').fetchone()[0] == 'delete'
        assert conn.execute('SELECT * FROM t').fetchall() == [(1,)]


def test_reader_outliving_writer_retains_sidecars_and_next_guarded_writer_cleans(db_root):
    path = db_root / 'state.db'
    store = object.__new__(OrganizationStore)
    store.path = path
    wal, shm = Path(str(path)+'-wal'), Path(str(path)+'-shm')
    writer = sqlite3.connect(path, isolation_level=None)
    try:
        assert writer.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
        writer.execute('CREATE TABLE t(a)')
        writer.execute('INSERT INTO t VALUES(1)')
        with readonly_observation(store) as reader:
            assert [tuple(row) for row in reader.execute('SELECT * FROM t')] == [(1,)]
            writer.close()
            assert wal.exists() and shm.exists()
        assert wal.exists() and shm.exists()
        # Reopening must recover the durable row from the ordinary WAL files.
        with readonly_observation(store) as reader:
            assert [tuple(row) for row in reader.execute('SELECT * FROM t')] == [(1,)]
        assert wal.exists() and shm.exists()
        # Existing write/init lifetimes already coordinate through close.
        with writers.ledger_writer(path, deadline=time.monotonic()+10), store._connect(write=True) as cleanup:
            assert [tuple(row) for row in cleanup.execute('SELECT * FROM t')] == [(1,)]
        assert not wal.exists() and not shm.exists()
        with readonly_observation(store) as reader:
            assert [tuple(row) for row in reader.execute('SELECT * FROM t')] == [(1,)]
    finally:
        writer.close()


def test_read_snapshot_and_autocommit_freshness_are_preserved(db_root):
    path = db_root / 'state.db'
    seed(path)
    with readonly_observation(SimpleNamespace(path=path)) as reader:
        reader.execute('BEGIN')
        assert reader.execute('SELECT a FROM t').fetchone()[0] == 1
        with closing(sqlite3.connect(path, isolation_level=None)) as writer:
            writer.execute('UPDATE t SET a=2')
        assert reader.execute('SELECT a FROM t').fetchone()[0] == 1
        reader.rollback()
        assert not reader.in_transaction
        assert reader.execute('SELECT a FROM t').fetchone()[0] == 2


def test_missing_wal_sidecars_can_be_created_only_when_directory_allows(db_root):
    path = db_root / 'state.db'
    seed(path)
    wal, shm = Path(str(path)+'-wal'), Path(str(path)+'-shm')
    assert not wal.exists() and not shm.exists()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    with readonly_observation(SimpleNamespace(path=path)) as conn:
        assert [tuple(row) for row in conn.execute('SELECT * FROM t')] == [(1,)]
        assert wal.exists() and shm.exists()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


@pytest.mark.skipif(os.name != 'posix' or getattr(os, 'geteuid', lambda: 0)() == 0, reason='Real unprivileged POSIX permission control')
def test_missing_sidecar_permission_failure_stays_visible_without_fallback(db_root):
    path = db_root / 'state.db'
    seed(path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    path.chmod(0o400)
    db_root.chmod(0o500)
    try:
        with pytest.raises(sqlite3.OperationalError) as caught:
            with readonly_observation(SimpleNamespace(path=path)) as conn:
                conn.execute('SELECT * FROM t').fetchall()
        assert caught.value.sqlite_errorcode & 255 in {sqlite3.SQLITE_READONLY, sqlite3.SQLITE_CANTOPEN}
        assert set(db_root.iterdir()) == {path}
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    finally:
        db_root.chmod(0o700)
        path.chmod(0o600)


@pytest.mark.skipif(os.name != 'posix' or getattr(os, 'geteuid', lambda: 0)() == 0, reason='Real unprivileged POSIX permission control')
def test_readable_existing_sidecars_allow_readonly_reopen(db_root):
    path = db_root / 'state.db'
    writer = sqlite3.connect(path, isolation_level=None)
    try:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('CREATE TABLE t(a)')
        writer.execute('INSERT INTO t VALUES(1)')
        with readonly_observation(SimpleNamespace(path=path)) as conn:
            assert conn.execute('SELECT a FROM t').fetchone()[0] == 1
            writer.close()
    finally:
        writer.close()
    files = [path, Path(str(path)+'-wal'), Path(str(path)+'-shm')]
    assert all(p.exists() for p in files)
    # Every SQLite handle is closed before taking any raw-byte snapshot.
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    for p in files:
        p.chmod(0o400)
    db_root.chmod(0o500)
    try:
        with readonly_observation(SimpleNamespace(path=path)) as conn:
            assert conn.execute('SELECT a FROM t').fetchone()[0] == 1
        assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files} == before
    finally:
        db_root.chmod(0o700)
        for p in files:
            p.chmod(0o600)


def test_hot_rollback_journal_requires_writable_recovery_and_is_not_hidden(db_root):
    path = db_root / 'state.db'
    with closing(sqlite3.connect(path, isolation_level=None)) as conn:
        conn.execute('CREATE TABLE t(a INTEGER,b BLOB)')
        conn.executemany('INSERT INTO t VALUES(?,?)', [(i, b'a'*4096) for i in range(64)])
    script = """
import os, sqlite3, sys
def deny(event,args):
    if event in {'socket.connect','socket.getaddrinfo','socket.sendto'}:
        raise RuntimeError('Diagnostic child forbids network')
sys.addaudithook(deny)
c=sqlite3.connect(sys.argv[1],isolation_level=None)
c.execute('PRAGMA cache_size=2')
c.execute('BEGIN IMMEDIATE')
c.execute("UPDATE t SET b=zeroblob(4096)")
os._exit(0)
"""
    completed = subprocess.run([sys.executable, '-B', '-c', script, str(path)],
                               capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0 and completed.stdout == completed.stderr == ''
    journal = Path(str(path)+'-journal')
    assert journal.exists() and journal.stat().st_size > 512
    before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in (path, journal)]
    with pytest.raises(sqlite3.OperationalError) as caught:
        with readonly_observation(SimpleNamespace(path=path)) as conn:
            conn.execute('SELECT count(*) FROM t').fetchone()
    assert caught.value.sqlite_errorcode == sqlite3.SQLITE_READONLY_ROLLBACK
    assert [hashlib.sha256(p.read_bytes()).hexdigest() for p in (path, journal)] == before
    with closing(sqlite3.connect(path, isolation_level=None)) as recovery:
        assert recovery.execute('SELECT count(*) FROM t WHERE b=?', (b'a'*4096,)).fetchone()[0] == 64
    assert not journal.exists()


@pytest.mark.parametrize('kind', ['sqlite', 'product'])
def test_backup_captures_committed_wal_after_readonly_last_close(db_root, kind):
    path, destination = db_root/'state.db', db_root/'snapshot.db'
    writer = sqlite3.connect(path, isolation_level=None)
    try:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('CREATE TABLE t(a)')
        writer.execute('INSERT INTO t VALUES(1)')
        with readonly_observation(SimpleNamespace(path=path)) as source:
            assert source.execute('SELECT a FROM t').fetchone()[0] == 1
            writer.close()
    finally:
        writer.close()
    assert Path(str(path)+'-wal').exists()
    if kind == 'product':
        from eidolon_cli.backup import _safe_copy_db
        assert _safe_copy_db(path, destination)
    else:
        with readonly_observation(SimpleNamespace(path=path)) as source:
            with closing(sqlite3.connect(destination)) as target:
                source.backup(target)
    with closing(sqlite3.connect(destination)) as check:
        assert check.execute('SELECT a FROM t').fetchall() == [(1,)]


def test_clone_all_preserves_quiescent_retained_wal(db_root, monkeypatch):
    from eidolon_cli import profiles
    source, destination = db_root/'source', db_root/'clone'
    path = source/'organization/state.db'
    path.parent.mkdir(parents=True)
    writer = sqlite3.connect(path, isolation_level=None)
    try:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('CREATE TABLE retained(value TEXT)')
        writer.execute("INSERT INTO retained VALUES('committed in WAL')")
        with readonly_observation(SimpleNamespace(path=path)) as reader:
            assert reader.execute('SELECT value FROM retained').fetchone()[0] == 'committed in WAL'
            writer.close()
    finally:
        writer.close()
    files = [path, Path(str(path)+'-wal'), Path(str(path)+'-shm')]
    assert all(p.exists() for p in files)
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    monkeypatch.setattr(profiles, '_get_default_eidolon_home', lambda: db_root/'default')
    profiles._clone_all_into(source, destination, 'clone')
    copied = destination/'organization/state.db'
    assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in copied.parent.iterdir()} == before
    with readonly_observation(SimpleNamespace(path=copied)) as reader:
        assert reader.execute('SELECT value FROM retained').fetchone()[0] == 'committed in WAL'
    assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files} == before


@pytest.mark.parametrize('value', [None, 0, 1, '', 'true', [], {}])
def test_write_mode_requires_exact_boolean_before_open(db_root, monkeypatch, value):
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError('Invalid write mode must not open SQLite')
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    with pytest.raises(TypeError, match='write mode must be boolean'):
        with OrganizationStore._connect(SimpleNamespace(path=db_root/'state.db'), write=value):
            pytest.fail('Invalid connection mode yielded')
    assert calls == []


@pytest.mark.parametrize('kind', ['observation', 'owner_policy'])
@pytest.mark.parametrize('write', [False, True])
def test_restricted_views_cannot_open_any_connection(db_root, monkeypatch, kind, write):
    from eidolon_cli.organization_observation import _ObservationView
    from eidolon_cli.organization_owner_policy import _writer
    if kind == 'observation':
        view = object.__new__(_ObservationView)
    else:
        view = _writer(object(), db_root, object(), {'generation': 1, 'fingerprint': 'test'})
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError('Restricted view must not open SQLite')
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    if write:
        with pytest.raises(TypeError, match='unexpected keyword argument'):
            view._connect(write=True)
    else:
        with pytest.raises(RuntimeError):
            view._connect()
    assert calls == []


def test_constructor_and_write_remain_explicitly_writable_and_coordinated(db_root, monkeypatch):
    from contextlib import contextmanager
    from eidolon_cli import organization_store as module
    held, modes = [], []
    real_gate, real_connect = module.ledger_writer, OrganizationStore._connect
    @contextmanager
    def coordinated(path, *, deadline, cancelled=None):
        with real_gate(path, deadline=deadline, cancelled=cancelled):
            held.append(path)
            try:
                yield
            finally:
                held.pop()
    @contextmanager
    def observed(self, *, write=False):
        modes.append(write)
        if write:
            assert held == [self.path]
        with real_connect(self, write=write) as conn:
            try:
                yield conn
            finally:
                if write:
                    assert held == [self.path]
        if write:
            assert held == [self.path]  # Native close also stayed inside the gate.
    monkeypatch.setattr(module, 'ledger_writer', coordinated)
    monkeypatch.setattr(OrganizationStore, '_connect', observed)
    store = OrganizationStore(db_root/'organization/state.db')
    assert modes and all(modes)
    with store._write() as conn:
        conn.execute("INSERT INTO events(kind,text,created) VALUES('system','kept',0)")
    with store._connect() as conn:
        assert conn.execute("SELECT count(*) FROM events WHERE text='kept'").fetchone()[0] == 1
        assert conn.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
    assert modes[-1] is False and not held
    with pytest.raises(RuntimeError, match='rollback fixture'):
        with store._write() as conn:
            conn.execute("INSERT INTO events(kind,text,created) VALUES('system','rolled back',0)")
            raise RuntimeError('rollback fixture')
    with store._connect() as conn:
        assert conn.execute("SELECT count(*) FROM events WHERE text='rolled back'").fetchone()[0] == 0


def test_metadata_only_ledger_is_initialized_by_writer_not_reader(db_root):
    from eidolon_cli.organization_metadata import read_descriptor, save_metadata
    path = db_root/'state.db'
    descriptor = save_metadata(path, display_name='Retained organization',
        owner_display_name='Retained owner', expected_organization_id=None,
        expected_revision=None, idempotency_key='before-execution')
    with readonly_observation(SimpleNamespace(path=path)) as conn:
        assert conn.execute("SELECT count(*) FROM sqlite_schema WHERE name='agents'").fetchone()[0] == 0
        assert conn.execute('PRAGMA journal_mode').fetchone()[0] == 'delete'
        tables = ('organization_metadata', 'organization_metadata_audit', 'organization_metadata_receipts')
        retained = {table: [tuple(row) for row in conn.execute('SELECT * FROM ' + table)] for table in tables}
        assert all(retained.values())
    assert read_descriptor(path) == descriptor
    store = OrganizationStore(path)
    with store._connect() as conn:
        assert {table: [tuple(row) for row in conn.execute('SELECT * FROM ' + table)] for table in tables} == retained
        assert conn.execute('SELECT count(*) FROM agents').fetchone()[0] > 0
        assert conn.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
    assert read_descriptor(path) == descriptor


def test_missing_deleted_ledger_is_not_recreated_by_existing_store(db_root):
    store = OrganizationStore(db_root/'state.db')
    store.path.unlink()
    with pytest.raises(sqlite3.OperationalError) as caught:
        with store._connect():
            pytest.fail('Deleted ledger was recreated')
    assert caught.value.sqlite_errorcode == sqlite3.SQLITE_CANTOPEN
    assert not store.path.exists()
