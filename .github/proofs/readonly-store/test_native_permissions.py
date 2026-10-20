"""Native filesystem controls against the exact production read-only helper.

These tests do not emulate Windows on POSIX, skip permission failures, bypass
admission/vault support, patch sqlite3.connect, or use SQLite immutable mode.
The source under test is selected externally by the runner/PYTHONPATH.
"""
from contextlib import closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

import pytest

from eidolon_cli.organization_store import OrganizationStore
from native_permissions import owned_permission_directory


@contextmanager
def observed_sqlite_targets():
    """Passive native audit recording, without replacing any connect function."""
    targets = []
    active = True

    def record(event, args):
        if active and event == "sqlite3.connect":
            targets.append(str(args[0]))

    sys.addaudithook(record)
    try:
        yield targets
    finally:
        active = False  # Audit hooks cannot be removed; inactive hooks do nothing.


def readonly(path):
    # Avoid OrganizationStore.__init__ provisioning; this path-only holder is
    # sufficient for the unmodified production default connection helper.
    return OrganizationStore._connect(SimpleNamespace(path=path))


def closed_hashes(files):
    # Callers take snapshots only while every SQLite connection is closed.
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in files}


def seed_checkpointed_wal(path):
    with closing(sqlite3.connect(path, isolation_level=None)) as writer:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("CREATE TABLE t(a INTEGER)")
        writer.execute("INSERT INTO t VALUES(1)")
    assert set(path.parent.iterdir()) == {path}


def seed_retained_sidecars(path):
    writer = sqlite3.connect(path, isolation_level=None)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("CREATE TABLE t(a INTEGER)")
        writer.execute("INSERT INTO t VALUES(1)")
        with readonly(path) as reader:
            assert reader.execute("SELECT a FROM t").fetchall()[0][0] == 1
            writer.close()
    finally:
        writer.close()
    files = (path, Path(str(path) + "-wal"), Path(str(path) + "-shm"))
    assert set(path.parent.iterdir()) == set(files)
    assert all(file.stat().st_size > 0 for file in files)
    return files


def record_permissions(record_property, fixture):
    assert fixture.restored
    assert fixture.denial_evidence and fixture.restoration_evidence
    assert all(item["exact"] for item in fixture.restoration_evidence)
    record_property("native_permission_platform", sys.platform)
    record_property("native_permission_pid", os.getpid())
    record_property("native_permission_euid", getattr(os, "geteuid", lambda: None)())
    record_property("native_permission_denials", json.dumps(fixture.denial_evidence, sort_keys=True))
    record_property("native_permission_restoration", json.dumps(fixture.restoration_evidence, sort_keys=True))


def test_native_missing_sidecars_refuse_without_fallback(tmp_path, record_property):
    with owned_permission_directory(tmp_path) as fixture:
        path = fixture.root / "state.db"
        seed_checkpointed_wal(path)
        files = (path,)
        fixture.prove_writable(files)
        before = closed_hashes(files)
        with fixture.restrict_readonly(files):
            with observed_sqlite_targets() as targets:
                with pytest.raises(sqlite3.OperationalError) as caught:
                    with readonly(path) as reader:
                        reader.execute("SELECT a FROM t").fetchall()
            assert caught.value.sqlite_errorcode & 255 in {
                sqlite3.SQLITE_READONLY, sqlite3.SQLITE_CANTOPEN,
            }
            # Exactly one real default connection: no rw/create/immutable retry.
            assert targets == [path.absolute().as_uri() + "?mode=ro"]
            assert set(fixture.root.iterdir()) == {path}
            assert closed_hashes(files) == before
        assert closed_hashes(files) == before
        assert set(fixture.root.iterdir()) == {path}
        fixture.prove_writable(files)
        record_property("sqlite_permission_error_code", caught.value.sqlite_errorcode)
        record_property("sqlite_connection_targets", json.dumps(targets))
        record_property("closed_file_sha256", json.dumps(before, sort_keys=True))
        record_permissions(record_property, fixture)


def test_native_readable_sidecars_reopen_with_identical_closed_bytes(tmp_path, record_property):
    with owned_permission_directory(tmp_path) as fixture:
        path = fixture.root / "state.db"
        files = seed_retained_sidecars(path)
        fixture.prove_writable(files)
        before = closed_hashes(files)
        reopens = []
        with fixture.restrict_readonly(files):
            # Two independent reopen/close cycles establish a repeatable read,
            # with all file hashes sampled only after the handle has closed.
            for _ in range(2):
                with observed_sqlite_targets() as targets:
                    with readonly(path) as reader:
                        assert reader.execute("PRAGMA query_only").fetchone()[0] == 0
                        assert reader.execute("SELECT a FROM t").fetchone()[0] == 1
                        with pytest.raises(sqlite3.OperationalError) as caught:
                            reader.execute("UPDATE t SET a=2")
                        assert caught.value.sqlite_errorcode & 255 == sqlite3.SQLITE_READONLY
                assert targets == [path.absolute().as_uri() + "?mode=ro"]
                reopens.append(targets)
                assert set(fixture.root.iterdir()) == set(files)
                assert closed_hashes(files) == before
        assert closed_hashes(files) == before
        assert set(fixture.root.iterdir()) == set(files)
        fixture.prove_writable(files)
        record_property("sqlite_connection_targets", json.dumps(reopens))
        record_property("closed_file_sha256", json.dumps(before, sort_keys=True))
        record_permissions(record_property, fixture)
