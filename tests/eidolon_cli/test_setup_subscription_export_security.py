"""Synthetic-only portability boundary for dedicated setup subscription private state."""

import io
import json
import shutil
import stat
import tarfile
import zipfile
from argparse import Namespace
from pathlib import Path

import pytest

from eidolon_cli import backup, profiles


PRIVATE = ".setup-subscription-private"
RECEIPT = "setup-budget-receipt.json"
RECEIPT_TEXT = '{"grant_revision":"opaque-revision-7","calls":2}'


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "eidolon"
    root.mkdir()
    monkeypatch.setattr(profiles, "_get_default_eidolon_home", lambda: root)
    monkeypatch.setattr(profiles, "_get_profiles_root", lambda: root / "profiles")
    monkeypatch.setattr(backup, "get_eidolon_home", lambda: root)
    monkeypatch.setattr(backup, "display_eidolon_home", lambda: str(root))
    monkeypatch.setattr(backup, "_restore_profile_wrappers", lambda _: [])
    monkeypatch.setattr(backup, "_revive_gateway_after_import", lambda _: None)
    return root


def seed(root):
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.yaml").write_text("model: synthetic\n")
    (root / PRIVATE).mkdir()
    (root / PRIVATE / "envelope.bin").write_bytes(b"fake-encrypted-envelope")
    (root / PRIVATE / "activation.json").write_text('{"handle":"fake-local-handle"}')
    (root / RECEIPT).write_text(RECEIPT_TEXT)


def symlink_or_skip(link, target):
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")


@pytest.mark.parametrize("path", [
    PRIVATE, f"named/{PRIVATE}/envelope", f"profiles/team/{PRIVATE}/activation",
    f".eidolon/profiles/team/{PRIVATE.upper()}/key",
    f"profiles\\team\\{PRIVATE}\\key", f"root/{PRIVATE}. /key",
    f"root/{PRIVATE}:stream", f"_external/cache/{PRIVATE}/key",
])
def test_reserved_component_aliases(path):
    assert profiles._is_setup_subscription_private_path(path)
    assert backup._should_exclude(Path(path))


@pytest.mark.parametrize("name", [RECEIPT, "setup-usage.jsonl", "subscription-policy-receipt.json"])
def test_receipt_paths_are_not_private(name):
    assert not profiles._is_setup_subscription_private_path(name)
    assert not backup._should_exclude(Path(name))


@pytest.mark.parametrize("source_name", ["default", "named"])
def test_clone_all_filter_excludes_store_and_retains_receipt(home, tmp_path, source_name):
    source = home if source_name == "default" else home / "profiles" / source_name
    seed(source)
    target = tmp_path / "clone"
    shutil.copytree(source, target, symlinks=True,
                    ignore=profiles._clone_all_copytree_ignore(source))
    assert not (target / PRIVATE).exists()
    assert (target / RECEIPT).read_text() == RECEIPT_TEXT


def test_clone_all_filter_excludes_aliases(home, tmp_path):
    seed(home)
    symlink_or_skip(home / "alias.bin", home / PRIVATE / "envelope.bin")
    symlink_or_skip(home / "alias-dir", home / PRIVATE)
    target = tmp_path / "clone"
    shutil.copytree(home, target, symlinks=True,
                    ignore=profiles._clone_all_copytree_ignore(home))
    assert not (target / "alias.bin").is_symlink()
    assert not (target / "alias-dir").is_symlink()


@pytest.mark.parametrize("source_name", ["default", "named"])
def test_export_excludes_private_and_keeps_inert_receipts(home, tmp_path, monkeypatch, source_name):
    source = home if source_name == "default" else home / "profiles" / source_name
    seed(source)
    # Default exports are allowlisted; put this inert fixture in a portable directory.
    (source / "memories").mkdir()
    (source / "memories" / RECEIPT).write_text(RECEIPT_TEXT)
    monkeypatch.setattr(profiles, "stage_project_registry", lambda *_: None)
    archive = profiles.export_profile(source_name, str(tmp_path / "export.tgz"))
    with tarfile.open(archive) as tf:
        assert not any(PRIVATE in m.name for m in tf)
        assert tf.extractfile(f"{source_name}/memories/{RECEIPT}").read().decode() == RECEIPT_TEXT


@pytest.mark.parametrize("rel", [
    f"{PRIVATE}/activation.json", f"nested/{PRIVATE.upper()}/key",
    f"nested\\{PRIVATE}\\key", f"{PRIVATE}. /key", f"{PRIVATE}:stream",
])
def test_export_rejects_reserved_extra_files(home, tmp_path, monkeypatch, rel):
    source = home / "profiles" / "named"
    seed(source)
    monkeypatch.setattr(profiles, "stage_project_registry", lambda *_: None)
    with pytest.raises(ValueError, match="private setup subscription"):
        profiles.export_profile("named", str(tmp_path / "export.tgz"), {rel: "fake"})
    assert not (tmp_path / "export.tar.gz").exists()


def test_export_omits_private_symlink_alias_before_redaction(home, tmp_path, monkeypatch):
    source = home / "profiles" / "named"
    seed(source)
    symlink_or_skip(source / "alias.json", source / PRIVATE / "activation.json")
    monkeypatch.setattr(profiles, "stage_project_registry", lambda *_: None)
    archive = profiles.export_profile("named", str(tmp_path / "export.tgz"))
    with tarfile.open(archive) as tf:
        assert "named/alias.json" not in tf.getnames()
    assert (source / PRIVATE / "activation.json").read_text() == '{"handle":"fake-local-handle"}'


def write_tar(path, bad_name=None, link=False):
    with tarfile.open(path, "w:gz") as tf:
        data = RECEIPT_TEXT.encode()
        receipt = tarfile.TarInfo(f"named/{RECEIPT}")
        receipt.size = len(data)
        tf.addfile(receipt, io.BytesIO(data))
        if bad_name:
            entry = tarfile.TarInfo(bad_name)
            if link:
                entry.type = tarfile.SYMTYPE
                entry.linkname = f"{PRIVATE}/activation.json"
                tf.addfile(entry)
            else:
                entry.size = 4
                tf.addfile(entry, io.BytesIO(b"fake"))


@pytest.mark.parametrize("name", [
    f"named/{PRIVATE}/key", f"named\\{PRIVATE.upper()}\\key",
    f"named/{PRIVATE}. /key", f"named/{PRIVATE}:stream",
    f"named/profiles/other/{PRIVATE}/key",
])
def test_profile_import_preflights_before_extraction(home, tmp_path, monkeypatch, name):
    path = tmp_path / "crafted.tgz"
    write_tar(path, name)
    monkeypatch.setattr(profiles, "safe_extract_targz", lambda *_: pytest.fail("extraction started"))
    with pytest.raises(ValueError, match="private setup subscription"):
        profiles.import_profile(str(path), "new")
    assert not (home / "profiles").exists()


def test_profile_import_rejects_links_before_extraction(home, tmp_path, monkeypatch):
    path = tmp_path / "crafted.tgz"
    write_tar(path, "named/alias", link=True)
    monkeypatch.setattr(profiles, "safe_extract_targz", lambda *_: pytest.fail("extraction started"))
    with pytest.raises(ValueError, match="Unsupported archive member type"):
        profiles.import_profile(str(path), "new")


def test_profile_import_retains_inert_receipt(home, tmp_path):
    path = tmp_path / "receipt.tgz"
    write_tar(path)
    imported = profiles.import_profile(str(path), "new")
    assert (imported / RECEIPT).read_text() == RECEIPT_TEXT


@pytest.mark.parametrize("name", [
    f"{PRIVATE}/key", f"profiles/other/{PRIVATE}/activation",
    f".eidolon/profiles/team/{PRIVATE.upper()}/key",
    f"profiles\\other\\{PRIVATE}\\key", f"{PRIVATE}. /key",
    f"{PRIVATE}:stream", f"_external/{PRIVATE}/key",
])
def test_backup_import_rejects_before_any_write(home, tmp_path, name):
    path = tmp_path / "crafted.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("config.yaml", "must not be published")
        zf.writestr(name, "fake")
    with pytest.raises(SystemExit):
        backup.run_import(Namespace(zipfile=str(path), force=True))
    assert not (home / "config.yaml").exists()


def test_backup_import_checks_destination_alias_before_any_write(home, tmp_path):
    seed(home)
    symlink_or_skip(home / "alias", home / PRIVATE)
    path = tmp_path / "crafted.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("config.yaml", "must not be published")
        zf.writestr("alias/activation.json", "replacement")
    with pytest.raises(SystemExit):
        backup.run_import(Namespace(zipfile=str(path), force=True))
    assert (home / "config.yaml").read_text() == "model: synthetic\n"
    assert (home / PRIVATE / "activation.json").read_text() == '{"handle":"fake-local-handle"}'


def test_backup_import_rejects_zip_symlinks(home, tmp_path):
    path = tmp_path / "crafted.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("config.yaml", "must not be published")
        link = zipfile.ZipInfo("alias")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(link, f"{PRIVATE}/activation.json")
    with pytest.raises(SystemExit):
        backup.run_import(Namespace(zipfile=str(path), force=True))
    assert not (home / "config.yaml").exists()


def test_backup_export_all_profiles_and_external_paths(home, tmp_path):
    seed(home)
    seed(home / "profiles" / "other")
    symlink_or_skip(home / "alias.bin", home / PRIVATE / "envelope.bin")
    entries = list(backup._iter_backup_files(home, tmp_path / "out.zip"))
    names = {str(rel) for _, rel in entries}
    assert not any(PRIVATE in name or "alias.bin" in name for name in names)
    assert RECEIPT in names
    assert str(Path("profiles/other") / RECEIPT) in names
    assert backup._iter_external_files(home / PRIVATE) == []
    assert backup._iter_external_files(home / PRIVATE / "envelope.bin") == []


def test_backup_receipt_roundtrip(home, tmp_path):
    seed(home)
    path = tmp_path / "backup.zip"
    assert backup._write_full_zip_backup(path, home) == path
    with zipfile.ZipFile(path) as zf:
        assert not any(PRIVATE in n for n in zf.namelist())
        assert zf.read(RECEIPT).decode() == RECEIPT_TEXT
    (home / RECEIPT).unlink()
    backup.run_import(Namespace(zipfile=str(path), force=True))
    assert (home / RECEIPT).read_text() == RECEIPT_TEXT


def test_quick_backup_excludes_nested_store_and_aliases(home, monkeypatch):
    seed(home)
    memory = home / "memories"
    seed(memory)
    symlink_or_skip(memory / "alias.json", home / PRIVATE / "activation.json")
    monkeypatch.setattr(backup, "_QUICK_STATE_FILES", ["memories"])
    candidates = list(backup._quick_snapshot_candidates(home))
    names = {rel for _, rel, _ in candidates}
    assert not any(PRIVATE in name or "alias.json" in name for name in names)
    assert f"memories/{RECEIPT}" in names


def test_quick_restore_rejects_private_manifest_before_any_write(home):
    seed(home)
    snapshot = home / "state-snapshots" / "synthetic"
    snapshot.mkdir(parents=True)
    (snapshot / "config.yaml").write_text("must not be published")
    (snapshot / "manifest.json").write_text(json.dumps({"files": {
        "config.yaml": 1, f"{PRIVATE}/activation.json": 1,
    }}))
    assert backup.restore_quick_snapshot("synthetic", hermes_home=home) is False
    assert (home / "config.yaml").read_text() == "model: synthetic\n"
