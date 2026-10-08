"""Project identity portability must not turn a shareable profile into a ledger backup."""

import json
import sqlite3
import tarfile
import zipfile
from contextlib import closing
from pathlib import Path

import pytest

from eidolon_cli import backup, profiles


@pytest.fixture
def profile_home(tmp_path, monkeypatch):
    home = tmp_path / ".eidolon"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


@pytest.fixture
def project_ledger():
    connections = []

    def create(home):
        path = home / "organization" / "state.db"
        path.parent.mkdir(parents=True)
        conn = sqlite3.connect(path)
        connections.append(conn)
        conn.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA wal_autocheckpoint=0;
            CREATE TABLE organization_project_registry (
                id TEXT PRIMARY KEY, project TEXT NOT NULL,
                binding_hash TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE private_ledger (secret TEXT);
            INSERT INTO private_ledger VALUES ('private policy and request history');
        """)
        # The registry row exists only in the live WAL, not the main DB file.
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        project = {"id": "service", "root": "root0", "recipe": "service-tests", "team": "backend"}
        conn.execute("INSERT INTO organization_project_registry VALUES (?, ?, ?, ?)",
                     (project["id"], json.dumps(project), "private-binding-hash", 1.0))
        conn.commit()
        return path, conn, project

    yield create
    for conn in connections:
        conn.close()


@pytest.mark.parametrize("name", ["default", "work"])
def test_profile_transfer_preserves_identities_without_activating_or_leaking_ledger(
    tmp_path, profile_home, project_ledger, name,
):
    home = profile_home if name == "default" else profile_home / "profiles" / name
    home.mkdir(parents=True, exist_ok=True)
    config = "model: fixture\n"
    (home / "config.yaml").write_text(config)
    path, conn, project = project_ledger(home)
    schema = conn.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
    # Managed backups must not smuggle a raw ledger into a shareable profile.
    for rel in ("backups/retained.zip", "state-snapshots/retained/organization/state.db"):
        artifact = home / rel
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(b"private policy and request history")

    archive = profiles.export_profile(name, str(tmp_path / f"{name}.tar.gz"))
    with tarfile.open(archive, "r:gz") as tf:
        names = tf.getnames()
        payload = json.load(tf.extractfile(f"{name}/organization-projects.json"))
        assert payload["projects"] == [project]
        assert payload["requiresOwnerReview"] is True
        assert not any(member.startswith(f"{name}/{directory}/")
                       for member in names for directory in ("organization", "backups", "state-snapshots"))
        files = b"\n".join(tf.extractfile(member).read() for member in tf.getmembers() if member.isfile())
        assert b"private-binding-hash" not in files
        assert b"private policy and request history" not in files

    imported = profiles.import_profile(str(archive), name="restored")
    assert json.loads((imported / "organization-projects.json").read_text()) == payload
    assert not (imported / "organization" / "state.db").exists()
    assert (imported / "config.yaml").read_text() == config
    assert (home / "config.yaml").read_text() == config
    assert conn.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall() == schema
    assert not (home / "organization-projects.json").exists()
    assert path.exists()
    # Import and re-export must not lose definitions while owner review is pending.
    transferred = profiles.export_profile("restored", str(tmp_path / "transferred.tar.gz"))
    with tarfile.open(transferred, "r:gz") as tf:
        assert json.load(tf.extractfile("restored/organization-projects.json")) == payload
    for reserved in ("organization/state.db", "backups/ledger.zip", "organization-projects.json"):
        with pytest.raises(ValueError, match="generated from the registry"):
            profiles.export_profile(name, str(tmp_path / "injected.tar.gz"), extra_files={reserved: "injected"})
        assert not (tmp_path / "injected.tar.gz").exists()


@pytest.mark.parametrize("state", ["old_schema", "empty_registry", "invalid_entry", "corrupt_database"])
def test_export_never_initializes_or_silently_drops_an_unreadable_registry(
    tmp_path, profile_home, project_ledger, state,
):
    path, conn, _ = project_ledger(profile_home)
    if state == "old_schema":
        conn.execute("DROP TABLE organization_project_registry")
    elif state == "empty_registry":
        conn.execute("DELETE FROM organization_project_registry")
    elif state == "invalid_entry":
        conn.execute("UPDATE organization_project_registry SET project='not json'")
    conn.commit()
    schema = conn.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
    if state == "corrupt_database":
        conn.close()
        path.write_bytes(b"not a SQLite database")

    archive = tmp_path / "export.tar.gz"
    if state in {"invalid_entry", "corrupt_database"}:
        with pytest.raises(ValueError, match="Cannot export organization project identities"):
            profiles.export_profile("default", str(archive))
        assert not archive.exists()
    else:
        profiles.export_profile("default", str(archive))
        with tarfile.open(archive, "r:gz") as tf:
            assert "default/organization-projects.json" not in tf.getnames()
        assert conn.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall() == schema


@pytest.mark.parametrize("kind", ["quick", "full"])
def test_recovery_backups_preserve_committed_project_ledger_wal(
    tmp_path, profile_home, project_ledger, kind,
):
    _, conn, project = project_ledger(profile_home)
    if kind == "quick":
        snapshot = backup.create_quick_snapshot(hermes_home=profile_home)
        captured = profile_home / "state-snapshots" / snapshot / "organization" / "state.db"
    else:
        archive = backup._write_full_zip_backup(tmp_path / "recovery.zip", profile_home)
        with zipfile.ZipFile(archive) as zf:
            assert "organization/state.db-wal" not in zf.namelist()
            captured = tmp_path / "captured.db"
            captured.write_bytes(zf.read("organization/state.db"))

    with closing(sqlite3.connect(captured.as_uri() + "?mode=ro", uri=True)) as saved:
        stored = json.loads(saved.execute("SELECT project FROM organization_project_registry").fetchone()[0])
        assert stored == project
        assert saved.execute("SELECT secret FROM private_ledger").fetchone() == (
            "private policy and request history",)
    assert conn.execute("SELECT count(*) FROM organization_project_registry").fetchone() == (1,)
