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
INSTALLATION = ".setup-subscription-installation"
RESERVED_ROOTS = (PRIVATE, INSTALLATION)


@pytest.fixture(params=RESERVED_ROOTS)
def private_root(request):
    """Exercise the same boundary for grants and host/pending-attempt metadata."""
    return request.param


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


def seed(root, private_root=PRIVATE):
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.yaml").write_text("model: synthetic\n")
    (root / private_root).mkdir()
    (root / private_root / "envelope.bin").write_bytes(b"fake-encrypted-envelope")
    (root / private_root / "activation.json").write_text('{"handle":"fake-local-handle"}')
    if private_root == INSTALLATION:
        (root / private_root / "host.json").write_text(
            '{"host_id":"00000000-0000-4000-8000-000000000001"}')
        (root / private_root / "pending.json").write_text('{"attempt":"fake-opaque-attempt"}')
    (root / RECEIPT).write_text(RECEIPT_TEXT)


def symlink_or_skip(link, target):
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")


@pytest.mark.parametrize("path", [value for store in RESERVED_ROOTS for value in [
    store, f"named/{store}/envelope", f"profiles/team/{store}/activation",
    f".eidolon/profiles/team/{store.upper()}/key",
    f"profiles\\team\\{store}\\key", f"root/{store}. /key",
    f"root/{store}:stream", f"_external/cache/{store}/key",
]])
def test_reserved_component_aliases(path):
    assert profiles._is_setup_subscription_private_path(path)
    assert backup._should_exclude(Path(path))


@pytest.mark.parametrize("name", [
    RECEIPT, "setup-usage.jsonl", "subscription-policy-receipt.json",
    ".setup-subscription-installation-receipt.json",
    ".setup-subscription-private-receipt.json",
])
def test_receipt_paths_are_not_private(name):
    assert not profiles._is_setup_subscription_private_path(name)
    assert not backup._should_exclude(Path(name))


@pytest.mark.parametrize("source_name", ["default", "named"])
def test_clone_all_filter_excludes_store_and_retains_receipt(home, tmp_path, source_name, private_root):
    source = home if source_name == "default" else home / "profiles" / source_name
    seed(source, private_root)
    target = tmp_path / "clone"
    shutil.copytree(source, target, symlinks=True,
                    ignore=profiles._clone_all_copytree_ignore(source))
    assert not (target / private_root).exists()
    assert (target / RECEIPT).read_text() == RECEIPT_TEXT


def test_clone_all_filter_excludes_aliases(home, tmp_path, private_root):
    seed(home, private_root)
    symlink_or_skip(home / "alias.bin", home / private_root / "envelope.bin")
    symlink_or_skip(home / "alias-dir", home / private_root)
    target = tmp_path / "clone"
    shutil.copytree(home, target, symlinks=True,
                    ignore=profiles._clone_all_copytree_ignore(home))
    assert not (target / "alias.bin").is_symlink()
    assert not (target / "alias-dir").is_symlink()


@pytest.mark.parametrize("source_name", ["default", "named"])
def test_export_excludes_private_and_keeps_inert_receipts(home, tmp_path, monkeypatch, source_name, private_root):
    source = home if source_name == "default" else home / "profiles" / source_name
    seed(source, private_root)
    # Default exports are allowlisted; put this inert fixture in a portable directory.
    (source / "memories").mkdir()
    (source / "memories" / RECEIPT).write_text(RECEIPT_TEXT)
    monkeypatch.setattr(profiles, "stage_project_registry", lambda *_: None)
    archive = profiles.export_profile(source_name, str(tmp_path / "export.tgz"))
    with tarfile.open(archive) as tf:
        assert not any(private_root in m.name for m in tf)
        assert tf.extractfile(f"{source_name}/memories/{RECEIPT}").read().decode() == RECEIPT_TEXT


@pytest.mark.parametrize("rel", [value for store in RESERVED_ROOTS for value in [
    f"{store}/activation.json", f"nested/{store.upper()}/key",
    f"nested\\{store}\\key", f"{store}. /key", f"{store}:stream",
]])
def test_export_rejects_reserved_extra_files(home, tmp_path, monkeypatch, rel, private_root):
    source = home / "profiles" / "named"
    seed(source, private_root)
    monkeypatch.setattr(profiles, "stage_project_registry", lambda *_: None)
    with pytest.raises(ValueError, match="private setup subscription"):
        profiles.export_profile("named", str(tmp_path / "export.tgz"), {rel: "fake"})
    assert not (tmp_path / "export.tar.gz").exists()


def test_export_omits_private_symlink_alias_before_redaction(home, tmp_path, monkeypatch, private_root):
    source = home / "profiles" / "named"
    seed(source, private_root)
    symlink_or_skip(source / "alias.json", source / private_root / "activation.json")
    monkeypatch.setattr(profiles, "stage_project_registry", lambda *_: None)
    archive = profiles.export_profile("named", str(tmp_path / "export.tgz"))
    with tarfile.open(archive) as tf:
        assert "named/alias.json" not in tf.getnames()
    assert (source / private_root / "activation.json").read_text() == '{"handle":"fake-local-handle"}'


def write_tar(path, bad_name=None, link=False, private_root=PRIVATE):
    with tarfile.open(path, "w:gz") as tf:
        data = RECEIPT_TEXT.encode()
        receipt = tarfile.TarInfo(f"named/{RECEIPT}")
        receipt.size = len(data)
        tf.addfile(receipt, io.BytesIO(data))
        if bad_name:
            entry = tarfile.TarInfo(bad_name)
            if link:
                entry.type = tarfile.SYMTYPE
                entry.linkname = f"{private_root}/activation.json"
                tf.addfile(entry)
            else:
                entry.size = 4
                tf.addfile(entry, io.BytesIO(b"fake"))


@pytest.mark.parametrize("name", [value for store in RESERVED_ROOTS for value in [
    f"named/{store}/key", f"named\\{store.upper()}\\key",
    f"named/{store}. /key", f"named/{store}:stream",
    f"named/profiles/other/{store}/key",
]])
def test_profile_import_preflights_before_extraction(home, tmp_path, monkeypatch, name):
    path = tmp_path / "crafted.tgz"
    write_tar(path, name)
    monkeypatch.setattr(profiles, "safe_extract_targz", lambda *_: pytest.fail("extraction started"))
    with pytest.raises(ValueError, match="private setup subscription"):
        profiles.import_profile(str(path), "new")
    assert not (home / "profiles").exists()


def test_profile_import_rejects_links_before_extraction(home, tmp_path, monkeypatch, private_root):
    path = tmp_path / "crafted.tgz"
    write_tar(path, "named/alias", link=True, private_root=private_root)
    monkeypatch.setattr(profiles, "safe_extract_targz", lambda *_: pytest.fail("extraction started"))
    with pytest.raises(ValueError, match="Unsupported archive member type"):
        profiles.import_profile(str(path), "new")


def test_profile_import_retains_inert_receipt(home, tmp_path):
    path = tmp_path / "receipt.tgz"
    write_tar(path)
    imported = profiles.import_profile(str(path), "new")
    assert (imported / RECEIPT).read_text() == RECEIPT_TEXT


@pytest.mark.parametrize("name", [value for store in RESERVED_ROOTS for value in [
    f"{store}/key", f"profiles/other/{store}/activation",
    f".eidolon/profiles/team/{store.upper()}/key",
    f"profiles\\other\\{store}\\key", f"{store}. /key",
    f"{store}:stream", f"_external/{store}/key",
]])
def test_backup_import_rejects_before_any_write(home, tmp_path, name):
    path = tmp_path / "crafted.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("config.yaml", "must not be published")
        zf.writestr(name, "fake")
    with pytest.raises(SystemExit):
        backup.run_import(Namespace(zipfile=str(path), force=True))
    assert not (home / "config.yaml").exists()


def test_backup_import_checks_destination_alias_before_any_write(home, tmp_path, private_root):
    seed(home, private_root)
    symlink_or_skip(home / "alias", home / private_root)
    path = tmp_path / "crafted.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("config.yaml", "must not be published")
        zf.writestr("alias/activation.json", "replacement")
    with pytest.raises(SystemExit):
        backup.run_import(Namespace(zipfile=str(path), force=True))
    assert (home / "config.yaml").read_text() == "model: synthetic\n"
    assert (home / private_root / "activation.json").read_text() == '{"handle":"fake-local-handle"}'


def test_backup_import_rejects_zip_symlinks(home, tmp_path, private_root):
    path = tmp_path / "crafted.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("config.yaml", "must not be published")
        link = zipfile.ZipInfo("alias")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(link, f"{private_root}/activation.json")
    with pytest.raises(SystemExit):
        backup.run_import(Namespace(zipfile=str(path), force=True))
    assert not (home / "config.yaml").exists()


def test_backup_export_all_profiles_and_external_paths(home, tmp_path, private_root):
    seed(home, private_root)
    seed(home / "profiles" / "other", private_root)
    symlink_or_skip(home / "alias.bin", home / private_root / "envelope.bin")
    entries = list(backup._iter_backup_files(home, tmp_path / "out.zip"))
    names = {str(rel) for _, rel in entries}
    assert not any(private_root in name or "alias.bin" in name for name in names)
    assert RECEIPT in names
    assert str(Path("profiles/other") / RECEIPT) in names
    assert backup._iter_external_files(home / private_root) == []
    assert backup._iter_external_files(home / private_root / "envelope.bin") == []


def test_backup_receipt_roundtrip(home, tmp_path, private_root):
    seed(home, private_root)
    path = tmp_path / "backup.zip"
    assert backup._write_full_zip_backup(path, home) == path
    with zipfile.ZipFile(path) as zf:
        assert not any(private_root in n for n in zf.namelist())
        assert zf.read(RECEIPT).decode() == RECEIPT_TEXT
    (home / RECEIPT).unlink()
    backup.run_import(Namespace(zipfile=str(path), force=True))
    assert (home / RECEIPT).read_text() == RECEIPT_TEXT


def test_quick_backup_excludes_nested_store_and_aliases(home, monkeypatch, private_root):
    seed(home, private_root)
    memory = home / "memories"
    seed(memory, private_root)
    symlink_or_skip(memory / "alias.json", home / private_root / "activation.json")
    monkeypatch.setattr(backup, "_QUICK_STATE_FILES", ["memories"])
    candidates = list(backup._quick_snapshot_candidates(home))
    names = {rel for _, rel, _ in candidates}
    assert not any(private_root in name or "alias.json" in name for name in names)
    assert f"memories/{RECEIPT}" in names


def test_quick_restore_rejects_private_manifest_before_any_write(home, private_root):
    seed(home, private_root)
    snapshot = home / "state-snapshots" / "synthetic"
    snapshot.mkdir(parents=True)
    (snapshot / "config.yaml").write_text("must not be published")
    (snapshot / "manifest.json").write_text(json.dumps({"files": {
        "config.yaml": 1, f"{private_root}/activation.json": 1,
    }}))
    assert backup.restore_quick_snapshot("synthetic", hermes_home=home) is False
    assert (home / "config.yaml").read_text() == "model: synthetic\n"


def test_both_namespaces_are_excluded_together(home, tmp_path):
    for reserved in RESERVED_ROOTS:
        seed(home, reserved)
    target = tmp_path / "clone"
    shutil.copytree(home, target, symlinks=True,
                    ignore=profiles._clone_all_copytree_ignore(home))
    assert all(not (target / reserved).exists() for reserved in RESERVED_ROOTS)
    assert (target / RECEIPT).read_text() == RECEIPT_TEXT
    entries = list(backup._iter_backup_files(home, tmp_path / "backup.zip"))
    assert all(not any(reserved in str(rel) for reserved in RESERVED_ROOTS)
               for _, rel in entries)


@pytest.mark.parametrize("rel", ["config.yaml", ".env", "SOUL.md", "memories/MEMORY.md", "memories/USER.md"])
def test_config_clone_file_rejects_reserved_symlink_alias(home, tmp_path, private_root, rel):
    seed(home, private_root)
    alias = home / rel
    alias.parent.mkdir(parents=True, exist_ok=True)
    if alias.exists():
        alias.unlink()
    symlink_or_skip(alias, home / private_root / "activation.json")
    target = tmp_path / "clone"
    profiles._bootstrap_profile_dir(target, home)
    assert not (target / rel).exists()
    assert (home / private_root / "activation.json").read_text() == '{"handle":"fake-local-handle"}'


def test_clone_file_rejects_direct_reserved_path(home, tmp_path, private_root):
    seed(home, private_root)
    rel = f"{private_root}/activation.json"
    target = tmp_path / "clone"
    profiles._clone_file(home, target, rel)
    assert not target.exists()


def test_config_clone_preserves_ordinary_files_and_skills(home, tmp_path):
    (home / "memories").mkdir()
    for rel in (*profiles._CLONE_CONFIG_FILES, *profiles._CLONE_SUBDIR_FILES):
        (home / rel).write_text(f"ordinary {rel}")
    skill = home / "skills" / "ordinary"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("ordinary skill")
    target = tmp_path / "clone"
    profiles._bootstrap_profile_dir(target, home)
    for rel in (*profiles._CLONE_CONFIG_FILES, *profiles._CLONE_SUBDIR_FILES):
        assert (target / rel).read_text() == f"ordinary {rel}"
    assert (target / "skills/ordinary/SKILL.md").read_text() == "ordinary skill"


def test_config_clone_skills_excludes_nested_reserved_names_and_aliases(home, tmp_path, private_root):
    seed(home, private_root)
    skills = home / "skills"
    nested = skills / "ordinary"
    nested.mkdir(parents=True)
    (nested / "SKILL.md").write_text("ordinary skill")
    reserved = nested / private_root.upper()
    reserved.mkdir()
    (reserved / "pending.json").write_text("fake pending attempt")
    symlink_or_skip(nested / "alias.json", home / private_root / "activation.json")
    symlink_or_skip(nested / "alias-dir", home / private_root)
    target = tmp_path / "clone"
    profiles._bootstrap_profile_dir(target, home)
    cloned = target / "skills" / "ordinary"
    assert (cloned / "SKILL.md").read_text() == "ordinary skill"
    assert not (cloned / private_root.upper()).exists()
    assert not (cloned / "alias.json").is_symlink()
    assert not (cloned / "alias-dir").is_symlink()


def test_config_clone_rejects_skills_root_alias(home, tmp_path, private_root):
    seed(home, private_root)
    symlink_or_skip(home / "skills", home / private_root)
    target = tmp_path / "clone"
    profiles._bootstrap_profile_dir(target, home)
    assert list((target / "skills").iterdir()) == []
    assert (target / "config.yaml").read_text() == "model: synthetic\n"


def test_config_clone_rejects_memory_parent_alias(home, tmp_path, private_root):
    seed(home, private_root)
    (home / private_root / "MEMORY.md").write_text("fake protected metadata")
    symlink_or_skip(home / "memories", home / private_root)
    target = tmp_path / "clone"
    profiles._bootstrap_profile_dir(target, home)
    assert not (target / "memories/MEMORY.md").exists()
