"""Real Git interoperability and fail-closed reviewed-source boundaries."""
import hashlib
import os
from pathlib import Path
import subprocess
import threading

import pytest

from eidolon_cli import organization_source_integration as source


pytestmark = pytest.mark.linux_only


def git(repo, *args, input=None):
    # Fixture setup must not race its own read-only metadata assertions.
    return subprocess.run(["git", "-c", "maintenance.auto=false", "-c", "gc.auto=0", "-C", str(repo), *args], input=input, capture_output=True,
                          check=True, env={"PATH": os.environ["PATH"], "HOME": str(repo.parent),
                                           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                                           "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@localhost",
                                           "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@localhost"}).stdout


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def edit(path="sample.txt", before=b"before\n", after=b"after\n"):
    return {"path": "root0/" + path, "base_sha256": sha(before),
            "content": after.decode(), "sha256": sha(after)}


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    git(root, "init", "-b", "main")
    (root / "sample.txt").write_bytes(b"before\n")
    (root / "untouched.txt").write_bytes(b"preserved\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "Initial source")
    return root


def prepared(repo, manifest=None):
    manifest = manifest or [edit()]
    roots = (str(repo),)
    base = source.prepare_source_integration(roots, manifest)
    grant = {"sourceIntegration": True, "sourceBaseSha256": source.source_base_sha256(base),
             "manifestSha256": source.source_manifest_sha256(manifest)}
    return roots, base, manifest, grant


def integrate(inputs, identifier="request-1"):
    roots, base, manifest, grant = inputs
    return source.integrate_source(roots, base, manifest, grant=grant, integration_id=identifier)


def test_integrates_exact_branch_preserves_live_worktree_and_index(repo):
    inputs = prepared(repo)
    index = (repo / ".git/index").read_bytes()
    head = git(repo, "rev-parse", "HEAD").decode().strip()
    receipt = integrate(inputs)
    assert receipt["status"] == "integrated"
    assert receipt["sourceBaseCommit"] == head
    assert receipt["manifestSha256"] == inputs[3]["manifestSha256"]
    assert receipt["sourceWritesPerformed"] is True
    assert receipt["workingTreeWritesPerformed"] is False
    assert receipt["indexWritesPerformed"] is False
    assert receipt["remotePushPerformed"] is False
    assert git(repo, "rev-parse", "HEAD").decode().strip() == head
    assert (repo / "sample.txt").read_bytes() == b"before\n"
    assert (repo / ".git/index").read_bytes() == index
    assert git(repo, "show", receipt["ref"] + ":sample.txt") == b"after\n"
    assert git(repo, "show", receipt["ref"] + ":untouched.txt") == b"preserved\n"
    assert git(repo, "rev-parse", receipt["commit"] + "^").decode().strip() == head
    assert git(repo, "rev-parse", receipt["commit"] + "^{tree}").decode().strip() == receipt["tree"]
    assert receipt["files"][0]["matched"] is True
    assert receipt["files"][0]["sha256"] == sha(git(repo, "show", receipt["ref"] + ":sample.txt"))
    git(repo, "fsck", "--strict", "--no-reflogs")


def test_preparation_is_read_only(repo):
    before = sorted((str(path.relative_to(repo)), path.read_bytes())
                    for path in repo.rglob("*") if path.is_file())
    source.prepare_source_integration((str(repo),))
    after = sorted((str(path.relative_to(repo)), path.read_bytes())
                   for path in repo.rglob("*") if path.is_file())
    assert before == after


def test_exact_repeat_recovers_same_receipt_without_another_commit(repo):
    inputs = prepared(repo)
    first = integrate(inputs)
    assert integrate(inputs) == first
    assert git(repo, "rev-list", "--count", first["ref"]).strip() == b"2"
    assert not list((repo / ".git").rglob("*.lock"))


def test_can_recover_after_receipt_failure(repo, monkeypatch):
    inputs = prepared(repo)
    original = source._Repository.commit_tree
    calls = 0

    def fail_after_publishing(self, oid):
        nonlocal calls
        if oid != inputs[1]["head"]:
            calls += 1
            if calls == 1:
                raise RuntimeError("Simulated receipt persistence interruption")
        return original(self, oid)

    with monkeypatch.context() as patch:
        patch.setattr(source._Repository, "commit_tree", fail_after_publishing)
        with pytest.raises(RuntimeError, match="receipt"):
            integrate(inputs)
    receipt = integrate(inputs)
    assert git(repo, "show", receipt["ref"] + ":sample.txt") == b"after\n"


def test_existing_different_branch_is_not_forced(repo):
    inputs = prepared(repo)
    ref = "refs/heads/eidolon/" + sha(b"request-1")[:32]
    git(repo, "update-ref", ref, inputs[1]["head"])
    with pytest.raises(source.SourceIntegrationError, match="already exists"):
        integrate(inputs)
    assert git(repo, "rev-parse", ref).decode().strip() == inputs[1]["head"]


@pytest.mark.parametrize("change", ["missing", "boolean", "base", "manifest"])
def test_exact_grant_is_required_before_metadata_writes(repo, change):
    roots, base, manifest, grant = prepared(repo)
    if change == "missing":
        grant = {}
    else:
        key = {"boolean": "sourceIntegration", "base": "sourceBaseSha256", "manifest": "manifestSha256"}[change]
        grant[key] = 1 if change == "boolean" else "0" * 64
    before = set((repo / ".git").rglob("*"))
    with pytest.raises(source.SourceIntegrationError, match="user-granted"):
        source.integrate_source(roots, base, manifest, grant=grant, integration_id="request")
    assert set((repo / ".git").rglob("*")) == before


def test_rejects_head_drift_even_if_target_preimage_unchanged(repo):
    inputs = prepared(repo)
    git(repo, "commit", "--allow-empty", "-m", "Changed HEAD")
    with pytest.raises(source.SourceIntegrationError, match="drifted"):
        integrate(inputs)
    assert not (repo / ".git/refs/heads/eidolon").exists()


@pytest.mark.parametrize("staged", [False, True])
def test_rejects_target_dirty_working_or_index_bytes(repo, staged):
    inputs = prepared(repo)
    (repo / "sample.txt").write_bytes(b"concurrent user edit\n")
    if staged:
        git(repo, "add", "sample.txt")
        (repo / "sample.txt").write_bytes(b"before\n")
    with pytest.raises(source.SourceIntegrationError, match="dirty|staged"):
        integrate(inputs)


def test_unrelated_dirty_files_are_preserved_and_not_integrated(repo):
    inputs = prepared(repo)
    (repo / "untouched.txt").write_bytes(b"user draft\n")
    git(repo, "add", "untouched.txt")
    (repo / "untracked.txt").write_bytes(b"private scratch\n")
    receipt = integrate(inputs)
    assert (repo / "untouched.txt").read_bytes() == b"user draft\n"
    assert (repo / "untracked.txt").read_bytes() == b"private scratch\n"
    assert git(repo, "show", receipt["ref"] + ":untouched.txt") == b"preserved\n"


def test_rejects_ref_drift_at_publish_boundary(repo, monkeypatch):
    inputs = prepared(repo)
    original = source._Repository.updated_tree

    def swap_head(self, *args):
        result = original(self, *args)
        (repo / ".git/HEAD").write_text(inputs[1]["head"] + "\n")
        return result

    monkeypatch.setattr(source._Repository, "updated_tree", swap_head)
    with pytest.raises(source.SourceIntegrationError, match="drifted"):
        integrate(inputs)
    assert not list((repo / ".git/refs/heads/eidolon").glob("*"))


def test_rejects_source_byte_race_before_publishing(repo, monkeypatch):
    inputs = prepared(repo)
    original = source._Repository.updated_tree

    def edit_during_build(self, *args):
        result = original(self, *args)
        (repo / "sample.txt").write_bytes(b"concurrent edit\n")
        return result

    monkeypatch.setattr(source._Repository, "updated_tree", edit_during_build)
    with pytest.raises(source.SourceIntegrationError, match="dirty"):
        integrate(inputs)
    assert not list((repo / ".git/refs/heads/eidolon").glob("*"))


def test_rejects_replaced_granted_root_during_object_write(repo, monkeypatch):
    inputs = prepared(repo)
    original = source._Repository.updated_tree
    moved = repo.with_name("old-project")

    def replace_root(self, *args):
        result = original(self, *args)
        repo.rename(moved)
        repo.mkdir()
        (repo / "sample.txt").write_bytes(b"replacement root\n")
        return result

    monkeypatch.setattr(source._Repository, "updated_tree", replace_root)
    with pytest.raises(source.SourceIntegrationError, match="replaced|unsafe"):
        integrate(inputs)
    assert (repo / "sample.txt").read_bytes() == b"replacement root\n"
    assert not (repo / ".git").exists()
    assert not list((moved / ".git/refs/heads/eidolon").glob("*"))


@pytest.mark.parametrize("target", ["sample.txt", ".git", ".git/objects", ".git/refs"])
def test_rejects_symlink_escape(repo, tmp_path, target):
    inputs = prepared(repo)
    original = repo / target
    external = tmp_path / "outside"
    original.rename(external)
    original.symlink_to(external, target_is_directory=external.is_dir())
    before = sorted(str(p.relative_to(external)) for p in external.rglob("*")) if external.is_dir() else external.read_bytes()
    with pytest.raises(source.SourceIntegrationError):
        integrate(inputs)
    after = sorted(str(p.relative_to(external)) for p in external.rglob("*")) if external.is_dir() else external.read_bytes()
    assert before == after


@pytest.mark.parametrize("target", ["sample.txt", ".git/HEAD", ".git/index", ".git/refs/heads/main"])
def test_rejects_hardlinked_source_or_metadata(repo, tmp_path, target):
    inputs = prepared(repo)
    os.link(repo / target, tmp_path / "external-link")
    with pytest.raises(source.SourceIntegrationError, match="hard links"):
        integrate(inputs)


def test_rejects_special_source_file_without_blocking(repo):
    inputs = prepared(repo)
    (repo / "sample.txt").unlink()
    os.mkfifo(repo / "sample.txt")
    with pytest.raises(source.SourceIntegrationError, match="regular"):
        integrate(inputs)


def test_rejects_gitdir_redirect_and_multiple_roots(repo, tmp_path):
    original = repo / ".git"
    external = tmp_path / "gitdir"
    original.rename(external)
    original.write_text("gitdir: " + str(external) + "\n")
    with pytest.raises(source.SourceIntegrationError):
        source.prepare_source_integration((str(repo),))
    with pytest.raises(source.SourceIntegrationError, match="one explicitly"):
        source.prepare_source_integration((str(repo), str(tmp_path)))


@pytest.mark.parametrize("path", ["../escape", ".git/config", "nested/.git/hooks/pre-commit", ".env",
                                  ".ssh/config", "auth.json", "sub/secret.pem", ".git-credentials"])
def test_credential_internal_and_traversal_paths_are_refused(repo, path):
    with pytest.raises(ValueError):
        source.prepare_source_integration((str(repo),), [edit(path)])


def test_repository_hooks_config_filters_and_credentials_cannot_execute(repo, tmp_path, monkeypatch):
    sentinel = tmp_path / "executed"
    hook = repo / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\necho compromised > " + str(sentinel) + "\n")
    hook.chmod(0o700)
    with (repo / ".git/config").open("a") as stream:
        stream.write("\n[core]\n\tfsmonitor = " + str(hook) + "\n[commit]\n\tgpgsign = true\n"
                     "[gpg]\n\tprogram = " + str(hook) + "\n[credential]\n\thelper = !" + str(hook) +
                     "\n[filter \"hostile\"]\n\tclean = " + str(hook) + "\n\tsmudge = " + str(hook) + "\n")
    (repo / ".gitattributes").write_text("* filter=hostile\n")

    def no_process(*args, **kwargs):
        raise AssertionError("Source integration must never start a subprocess")

    monkeypatch.setattr(subprocess, "Popen", no_process)
    receipt = integrate(prepared(repo))
    assert receipt["status"] == "integrated"
    assert not sentinel.exists()


def test_standard_packed_repository_and_packed_current_ref(repo):
    # Similar blobs force Git's ordinary offset-delta representation.
    for index in range(20):
        (repo / "sample.txt").write_text("line\n" * 1000 + f"revision {index}\n")
        git(repo, "add", "sample.txt")
        git(repo, "commit", "-m", f"Revision {index}")
    git(repo, "gc", "--aggressive", "--prune=now")
    git(repo, "pack-refs", "--all", "--prune")
    assert list((repo / ".git/objects/pack").glob("*.pack"))
    before = (repo / "sample.txt").read_bytes()
    receipt = integrate(prepared(repo, [edit(before=before)]))
    assert git(repo, "show", receipt["ref"] + ":sample.txt") == b"after\n"
    git(repo, "fsck", "--strict", "--no-reflogs")


@pytest.mark.parametrize("version", [2, 3, 4])
def test_supported_git_index_formats(repo, version):
    git(repo, "update-index", "--index-version", str(version))
    receipt = integrate(prepared(repo))
    assert git(repo, "show", receipt["ref"] + ":sample.txt") == b"after\n"


def test_create_delete_and_nested_paths_are_exact_and_worktree_untouched(repo):
    manifest = [{"path": "root0/new/nested.txt", "operation": "create", "content": "created\n",
                 "sha256": sha(b"created\n")},
                {"path": "root0/sample.txt", "operation": "delete", "base_sha256": sha(b"before\n")}]
    receipt = integrate(prepared(repo, manifest))
    assert git(repo, "show", receipt["ref"] + ":new/nested.txt") == b"created\n"
    files = git(repo, "ls-tree", "-r", "--name-only", receipt["ref"]).splitlines()
    assert b"sample.txt" not in files
    assert not (repo / "new").exists()
    assert (repo / "sample.txt").read_bytes() == b"before\n"


def test_create_refuses_untracked_conflict(repo):
    manifest = [{"path": "root0/new.txt", "operation": "create", "content": "created\n",
                 "sha256": sha(b"created\n")}]
    inputs = prepared(repo, manifest)
    (repo / "new.txt").write_text("user draft")
    with pytest.raises(source.SourceIntegrationError, match="dirty|conflicting"):
        integrate(inputs)
    assert (repo / "new.txt").read_text() == "user draft"


def test_existing_git_lock_is_never_removed(repo):
    inputs = prepared(repo)
    lock = repo / ".git/HEAD.lock"
    lock.write_bytes(b"another process owns this")
    with pytest.raises(source.SourceIntegrationError, match="lock already exists"):
        integrate(inputs)
    assert lock.read_bytes() == b"another process owns this"


def test_preserves_executable_mode_and_exact_utf8_newlines(repo):
    before, after = b"#!/bin/sh\r\necho before\r\n", b"\xef\xbb\xbf#!/bin/sh\r\necho after"
    path = repo / "script.sh"
    path.write_bytes(before)
    path.chmod(0o755)
    git(repo, "add", "script.sh")
    git(repo, "commit", "-m", "Executable source")
    receipt = integrate(prepared(repo, [edit("script.sh", before, after)]))
    assert git(repo, "show", receipt["ref"] + ":script.sh") == after
    assert git(repo, "ls-tree", receipt["ref"], "script.sh").startswith(b"100755 ")


def test_alternate_git_object_store_is_refused_without_reading_it(repo, tmp_path):
    (repo / ".git/objects/info/alternates").write_text(str(tmp_path / "outside-objects") + "\n")
    with pytest.raises(source.SourceIntegrationError, match="alternate"):
        source.prepare_source_integration((str(repo),))


def test_fresh_receipt_verification_is_read_only_and_survives_independent_head_change(repo):
    roots, base, manifest, grant = inputs = prepared(repo)
    receipt = integrate(inputs)
    git(repo, "commit", "--allow-empty", "-m", "Independent user work")
    (repo / "sample.txt").write_bytes(b"user now editing\n")
    before = sorted((str(path.relative_to(repo)), path.read_bytes())
                    for path in repo.rglob("*") if path.is_file())
    assert source.verify_source_integration(roots, base, manifest, receipt) == receipt
    after = sorted((str(path.relative_to(repo)), path.read_bytes())
                   for path in repo.rglob("*") if path.is_file())
    assert before == after


@pytest.mark.parametrize("change", ["delete_branch", "change_branch", "corrupt_blob", "corrupt_tree", "corrupt_commit"])
def test_fresh_receipt_verification_rejects_missing_changed_or_corrupt_proof(repo, change):
    roots, base, manifest, grant = inputs = prepared(repo)
    receipt = integrate(inputs)
    if change == "delete_branch":
        git(repo, "update-ref", "-d", receipt["ref"])
    elif change == "change_branch":
        git(repo, "update-ref", receipt["ref"], base["head"])
    else:
        oid = {"corrupt_blob": receipt["files"][0]["integratedBlob"],
               "corrupt_tree": receipt["tree"], "corrupt_commit": receipt["commit"]}[change]
        (repo / ".git/objects" / oid[:2] / oid[2:]).write_bytes(b"corrupt object bytes")
    with pytest.raises(source.SourceIntegrationError):
        source.verify_source_integration(roots, base, manifest, receipt)


def test_final_publication_guard_blocks_ref_effect_and_can_retry(repo):
    roots, base, manifest, grant = inputs = prepared(repo)

    def lost_lease():
        raise ValueError("Objective lease expired")

    with pytest.raises(ValueError, match="lease expired"):
        source.integrate_source(roots, base, manifest, grant=grant, integration_id="request-1",
                                before_publish=lost_lease)
    assert not list((repo / ".git/refs/heads/eidolon").glob("*"))
    assert integrate(inputs)["status"] == "integrated"


def test_cancellation_after_preparing_objects_blocks_branch_effect(repo, monkeypatch):
    roots, base, manifest, grant = prepared(repo)
    cancelled = threading.Event()
    original = source._Repository.updated_tree

    def cancel_during_build(self, *args):
        result = original(self, *args)
        cancelled.set()
        return result

    monkeypatch.setattr(source._Repository, "updated_tree", cancel_during_build)
    with pytest.raises(source.SourceIntegrationError, match="cancelled"):
        source.integrate_source(roots, base, manifest, grant=grant, integration_id="request-1", cancel=cancelled)
    assert not list((repo / ".git/refs/heads/eidolon").glob("*"))


def test_current_head_lock_fences_other_git_writers_during_publication(repo):
    roots, base, manifest, grant = prepared(repo)
    checked = []

    def try_concurrent_commit():
        with pytest.raises(subprocess.CalledProcessError):
            git(repo, "commit", "--allow-empty", "-m", "Concurrent writer")
        checked.append(True)

    receipt = source.integrate_source(roots, base, manifest, grant=grant, integration_id="request-1",
                                      before_publish=try_concurrent_commit)
    assert checked and all(checked)
    assert receipt["sourceBaseCommit"] == base["head"]


def test_non_force_publication_rejects_last_moment_existing_ref(repo):
    roots, base, manifest, grant = prepared(repo)
    branch = repo / ".git/refs/heads/eidolon" / sha(b"request-1")[:32]

    def add_ref_outside_git_lock_protocol():
        branch.write_text(base["head"] + "\n")

    with pytest.raises(source.SourceIntegrationError):
        source.integrate_source(roots, base, manifest, grant=grant, integration_id="request-1",
                                before_publish=add_ref_outside_git_lock_protocol)
    assert branch.read_text() == base["head"] + "\n"


def test_locks_replaced_by_other_actor_are_not_deleted(repo):
    roots, base, manifest, grant = prepared(repo)
    lock = repo / ".git/HEAD.lock"

    def swap_lock():
        lock.rename(repo / ".git/moved-lock")
        lock.write_bytes(b"different owner's lock")

    with pytest.raises(source.SourceIntegrationError, match="lock was replaced"):
        source.integrate_source(roots, base, manifest, grant=grant, integration_id="request-1", before_publish=swap_lock)
    assert lock.read_bytes() == b"different owner's lock"
    assert not list((repo / ".git/refs/heads/eidolon").glob("*"))


@pytest.mark.parametrize("option,expected_kind", [("--no-delta-base-offset", 7), ("--delta-base-offset", 6)])
def test_delta_packs_are_read_as_data(repo, monkeypatch, option, expected_kind):
    for index in range(10):
        (repo / "sample.txt").write_text("long repeated content\n" * 500 + str(index))
        git(repo, "add", "sample.txt")
        git(repo, "commit", "-m", str(index))
    objects = git(repo, "rev-list", "--objects", "--all")
    git(repo, "pack-objects", option, str(repo / ".git/objects/pack/pack"), input=objects)
    git(repo, "prune-packed")
    before = (repo / "sample.txt").read_bytes()
    observed_kinds = []
    original = source._Repository._pack_object

    def observe_ref_delta(self, pack, offset, depth):
        observed_kinds.append((pack[offset] >> 4) & 7)
        return original(self, pack, offset, depth)

    monkeypatch.setattr(source._Repository, "_pack_object", observe_ref_delta)
    with source._Repository((str(repo),)) as repository:
        for line in objects.splitlines():
            oid = line.split(b" ")[0].decode()
            kind, content = repository.object(oid)
            assert git(repo, "cat-file", kind.decode(), oid) == content
    receipt = integrate(prepared(repo, [edit(before=before)]))
    assert git(repo, "show", receipt["ref"] + ":sample.txt") == b"after\n"
    assert expected_kind in observed_kinds


def test_refuses_split_index_with_actionable_error(repo):
    git(repo, "update-index", "--split-index")
    with pytest.raises(source.SourceIntegrationError, match="Split|split"):
        prepared(repo)


def test_plain_detached_head_is_supported_without_modifying_it(repo):
    git(repo, "checkout", "--detach")
    inputs = prepared(repo)
    head = (repo / ".git/HEAD").read_bytes()
    receipt = integrate(inputs)
    assert inputs[1]["headRef"] is None
    assert (repo / ".git/HEAD").read_bytes() == head
    assert git(repo, "show", receipt["ref"] + ":sample.txt") == b"after\n"


def test_symlink_in_granted_root_ancestor_is_rejected(repo, tmp_path):
    alias = tmp_path / "alias"
    alias.symlink_to(repo, target_is_directory=True)
    with pytest.raises(source.SourceIntegrationError):
        source.prepare_source_integration((str(alias),))


def test_credential_directory_cannot_become_a_granted_source_root(repo, tmp_path):
    protected = tmp_path / ".ssh"
    repo.rename(protected)
    with pytest.raises(source.SourceIntegrationError, match="Credential"):
        source.prepare_source_integration((str(protected),))


def test_changed_preimage_during_publication_guard_blocks_ref(repo):
    roots, base, manifest, grant = prepared(repo)

    def edit_during_guard():
        (repo / "sample.txt").write_bytes(b"concurrent owner edit\n")

    with pytest.raises(source.SourceIntegrationError, match="dirty"):
        source.integrate_source(roots, base, manifest, grant=grant, integration_id="request-1",
                                before_publish=edit_during_guard)
    assert not list((repo / ".git/refs/heads/eidolon").glob("*"))


def test_replacing_read_path_cannot_hide_different_current_bytes(repo, monkeypatch):
    inputs = prepared(repo)
    original = source.os.read
    swapped = False

    def replace_after_read(fd, size):
        nonlocal swapped
        data = original(fd, size)
        if data == b"before\n" and not swapped:
            swapped = True
            (repo / "sample.txt").rename(repo / "saved-sample.txt")
            (repo / "sample.txt").write_bytes(b"replacement\n")
        return data

    monkeypatch.setattr(source.os, "read", replace_after_read)
    with pytest.raises(source.SourceIntegrationError, match="changed"):
        integrate(inputs)
    assert not list((repo / ".git/refs/heads/eidolon").glob("*"))


def test_lease_expiring_during_final_preimage_check_still_blocks_publication(repo):
    roots, base, manifest, grant = prepared(repo)
    checks = 0

    def expires_at_boundary():
        nonlocal checks
        checks += 1
        if checks > 1:
            raise ValueError("Lease expired at final publication boundary")

    with pytest.raises(ValueError, match="final publication"):
        source.integrate_source(roots, base, manifest, grant=grant, integration_id="request-1",
                                before_publish=expires_at_boundary)
    assert not list((repo / ".git/refs/heads/eidolon").glob("*"))
