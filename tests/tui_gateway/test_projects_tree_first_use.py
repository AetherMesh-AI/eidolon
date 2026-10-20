"""Background trees skip first-use provisioning; existing-store opens remain writable."""
import contextlib
import sqlite3
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from eidolon_cli import projects_db as pdb
from eidolon_constants import reset_eidolon_home_override, set_eidolon_home_override
from tui_gateway import server


@pytest.fixture
def tree(tmp_path, monkeypatch):
    home = tmp_path.resolve() / 'profile'
    home.mkdir(mode=0o700)
    token = set_eidolon_home_override(home)
    repo = tmp_path.resolve() / 'session-repo'
    repo.mkdir()
    policy = {'enabled': True, 'roots': [], 'exclude_paths': []}
    monkeypatch.setattr(server, '_repo_discovery_policy', lambda: policy)
    monkeypatch.setattr(server, '_repo_discovery_policy_key', lambda p: 'current')
    monkeypatch.setattr(server, '_repo_discovery_policy_is_default', lambda p: True)
    monkeypatch.setattr(server, '_is_repo_junk', lambda p: False)
    monkeypatch.setattr(server.git_probe, 'warm_roots', lambda *a: None)
    monkeypatch.setattr(server.git_probe, 'common_repo_root', lambda p: p)
    db = SimpleNamespace(
        list_sessions_rich=lambda **kw: [{'id': 'session', 'title': 'Retained chat', 'cwd': str(repo)}],
        distinct_session_cwds=lambda: [{'cwd': str(repo), 'sessions': 1, 'last_active': 4}])
    try:
        yield home, db, policy, repo
    finally:
        reset_eidolon_home_override(token)


def inputs(db, **kwargs):
    return server._project_tree_inputs(db, 100, include_discovered=kwargs.get('include_discovered', True))


@pytest.mark.parametrize('include_discovered', [False, True])
def test_missing_store_retains_sessions_without_provisioning(tree, monkeypatch, include_discovered):
    home, db, _, repo = tree
    before = home.stat()
    monkeypatch.setattr(pdb, 'connect_closing', lambda *a, **k: pytest.fail('missing store opened'))
    sessions, projects, discovered, active = inputs(db, include_discovered=include_discovered)
    assert sessions[0]['id'] == 'session'
    assert projects == [] and active is None
    assert ([r['root'] for r in discovered] == [str(repo)]) if include_discovered else discovered == []
    assert list(home.iterdir()) == []
    assert (home.stat().st_nlink, home.stat().st_ctime_ns) == (before.st_nlink, before.st_ctime_ns)


@pytest.mark.parametrize('stored,default,enabled,visible', [
    ('current', True, True, True), ('old', True, True, False),
    (None, True, True, True), (None, False, True, False),
    ('current', False, True, True), ('current', True, False, False),
])
def test_policy_filter_is_in_memory_and_retains_declared_projects_and_sessions(
        tree, monkeypatch, stored, default, enabled, visible):
    home, db, policy, repo = tree
    policy['enabled'] = enabled
    monkeypatch.setattr(server, '_repo_discovery_policy_is_default', lambda p: default)
    cached = str(repo.parent / 'cached-repo')
    with pdb.connect_closing() as conn:
        pid = pdb.create_project(conn, name='Existing project', folders=[str(repo)])
        pdb.set_active(conn, pid)
        pdb.record_discovered_repos(conn, [(cached, 'Cached')], policy_key=stored)
        before = list(conn.iterdump())
    _, projects, discovered, active = inputs(db)
    assert projects[0]['id'] == pid and active == pid
    assert str(repo) in {r['root'] for r in discovered}
    assert (cached in {r['root'] for r in discovered}) is visible
    with sqlite3.connect(home / 'projects.db') as conn:
        assert list(conn.iterdump()) == before


def test_existing_only_open_does_not_recreate_concurrently_removed_store(tree, monkeypatch):
    home, db, _, _ = tree
    with pdb.connect_closing():
        pass
    original = pdb.connect_closing
    @contextlib.contextmanager
    def removed(*args, **kwargs):
        assert kwargs['must_exist'] is True
        (home / 'projects.db').unlink()
        with original(*args, **kwargs) as conn:
            yield conn
    monkeypatch.setattr(pdb, 'connect_closing', removed)
    with pytest.raises(sqlite3.OperationalError):
        inputs(db)
    assert not (home / 'projects.db').exists()


def test_existing_corrupt_store_surfaces_error(tree):
    home, db, _, _ = tree
    path = home / 'projects.db'
    path.write_bytes(b'not a sqlite database')
    with pytest.raises(sqlite3.DatabaseError):
        inputs(db)
    assert path.read_bytes() == b'not a sqlite database'


def test_existing_legacy_store_keeps_current_migration_and_visible_projects(tree):
    home, db, _, repo = tree
    with sqlite3.connect(home / 'projects.db') as conn:
        conn.executescript('''CREATE TABLE projects(id TEXT PRIMARY KEY,slug TEXT,name TEXT,
            description TEXT,created_at INTEGER,archived INTEGER DEFAULT 0);
            CREATE TABLE project_folders(project_id TEXT,path TEXT,label TEXT,is_primary INTEGER,added_at INTEGER);''')
        conn.execute('INSERT INTO projects VALUES(?,?,?,?,?,?)', ('legacy', 'legacy', 'Legacy project', None, 1, 0))
        conn.execute('INSERT INTO project_folders VALUES(?,?,?,?,?)', ('legacy', str(repo), None, 1, 1))
    _, projects, _, _ = inputs(db)
    assert projects[0]['name'] == 'Legacy project'
    assert projects[0]['icon'] is None
    # Existing-store migration is intentionally retained, not claimed read-only.
    with sqlite3.connect(home / 'projects.db') as conn:
        assert 'icon' in {r[1] for r in conn.execute('PRAGMA table_info(projects)')}


def test_explicit_create_still_initializes_storage_after_absent_tree(tree):
    home, db, _, repo = tree
    inputs(db)
    assert not (home / 'projects.db').exists()
    with pdb.connect_closing() as conn:
        pid = pdb.create_project(conn, name='Explicit', folders=[str(repo)])
        pdb.record_discovered_repos(conn, [(str(repo), 'Repo')], policy_key='current')
    assert inputs(db)[1][0]['id'] == pid


@pytest.mark.skipif(sys.platform != 'darwin', reason='native Darwin ACL proof')
def test_delayed_first_tree_does_not_churn_profile_during_native_acl_check(tree, monkeypatch):
    from eidolon_cli import organization_setup_subscription_filesystem as fs
    home, db, _, _ = tree
    go, done = threading.Event(), threading.Event()
    failures = []
    def actor():
        try:
            assert go.wait(5)
            inputs(db)
        except BaseException as exc:
            failures.append(exc)
        finally:
            done.set()
    # ContextVars do not propagate to raw worker threads; scope this actor explicitly.
    def scoped_actor():
        token = set_eidolon_home_override(home)
        try:
            actor()
        finally:
            reset_eidolon_home_override(token)
    worker = threading.Thread(target=scoped_actor)
    worker.start()
    read = fs._read_darwin_acl
    def interleaved(*args, **kwargs):
        result = read(*args, **kwargs)
        go.set()
        assert done.wait(5)
        assert not failures
        return result
    monkeypatch.setattr(fs, '_read_darwin_acl', interleaved)
    before = home.stat()
    try:
        fs.check_darwin_acl(home, ancestor=True)
    finally:
        go.set()
        worker.join(5)
    assert not worker.is_alive() and not failures
    assert not (home / 'projects.db').exists()
    assert home.stat().st_ctime_ns == before.st_ctime_ns


def test_busy_existing_store_is_not_reported_as_empty(tree, monkeypatch):
    home, db, _, _ = tree
    with pdb.connect_closing():
        pass
    connect = sqlite3.connect
    blocker = connect(home / 'projects.db')
    blocker.execute('BEGIN EXCLUSIVE')
    def immediate(*args, **kwargs):
        kwargs['timeout'] = 0
        return connect(*args, **kwargs)
    monkeypatch.setattr(sqlite3, 'connect', immediate)
    try:
        with pytest.raises(sqlite3.OperationalError):
            inputs(db)
    finally:
        blocker.rollback()
        blocker.close()
    assert (home / 'projects.db').is_file()
