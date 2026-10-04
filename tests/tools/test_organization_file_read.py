"""Real filesystem/registry contracts for the organization-only read backend."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import json
import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from tools import file_tools
from tools import organization_file_read as bounded
from tools.registry import registry


@pytest.fixture
def files(tmp_path, monkeypatch):
    # resolve() also gives native macOS tests a non-symlink /private/... root.
    base = tmp_path.resolve()
    home = base / "profile"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: base)
    monkeypatch.setenv("HERMES_HOME", str(home))
    root = base / "source"
    root.mkdir()
    (root / "notes.txt").write_text("first\nsecond\nthird\n", encoding="utf-8")
    forbidden = Mock(side_effect=AssertionError("The shell-backed file backend must not be initialized"))
    monkeypatch.setattr(file_tools, "_get_file_ops", forbidden)
    resolver = Mock(side_effect=AssertionError("Legacy task paths must not be resolved"))
    monkeypatch.setattr(file_tools, "_resolve_path_for_task", resolver)
    yield root, home
    forbidden.assert_not_called()
    resolver.assert_not_called()


def dispatch(path, **args):
    return registry.dispatch("read_file", {"path": path, **args}, task_id="bounded-read-test")


@pytest.mark.linux_only
def test_registry_read_uses_pinned_roots_and_preserves_line_contract(files):
    root, _ = files
    before = dict(os.environ)
    with bounded.organization_file_read_scope((str(root),), 1000) as scope:
        assert scope.root_aliases == ("root0",)
        assert scope.canonical_arguments({"path": "root0/notes.txt"}) == {
            "path": "root0/notes.txt", "offset": 1, "limit": bounded.MAX_READ_LINES}
        result = json.loads(dispatch("root0/notes.txt", offset=2, limit=1))
        assert result["success"] is True
        assert result["content"] == "2|second"
        assert result["total_lines"] == 3
        assert result["truncated"] is True
        assert result["next_offset"] == 3
        final = json.loads(dispatch("root0/notes.txt", offset=3))
        assert final["content"] == "3|third"
        assert final["truncated"] is False
        # Repeated reads do not touch default read tracking/dedup.
        assert json.loads(dispatch("root0/notes.txt"))["content"] == "1|first\n2|second\n3|third"
        assert json.loads(dispatch("root0/notes.txt"))["success"] is True
    assert bounded.get_organization_file_read_scope() is None
    assert dict(os.environ) == before


@pytest.mark.linux_only
@pytest.mark.parametrize("path", [
    "notes.txt", "root0", "root1/notes.txt", "root00/notes.txt", "/etc/passwd",
    "root0/../notes.txt", "root0/nested/../../notes.txt", "root0/./notes.txt",
    "root0//notes.txt", "root0/notes.txt/", "~/notes.txt", "root0/~user/notes.txt",
    "root0/notes\x00.txt", "root0/notes\n.txt", "root0/..\\notes.txt", 5, None,
])
def test_alias_paths_reject_escapes_and_ambiguous_spelling(files, path):
    root, _ = files
    with bounded.organization_file_read_scope((str(root),), 1000) as scope:
        safe = scope.canonical_arguments({"path": path})
        assert safe == {"path": "[outside configured roots]"}
        with pytest.raises(bounded.OrganizationFileReadError):
            scope.validate_arguments({"path": path})
        result = json.loads(dispatch(path))
        assert result["success"] is False and result["blocked"] is True
        assert "content" not in result
        assert str(root) not in json.dumps(result)


@pytest.mark.linux_only
@pytest.mark.parametrize("args", [
    {"offset": True}, {"limit": False}, {"offset": "2"}, {"limit": 1.0},
    {"offset": 0}, {"limit": 0}, {"limit": bounded.MAX_READ_LINES + 1},
    {"offset": bounded.MAX_FILE_BYTES + 2}, {"limit": None},
])
def test_pagination_validation_is_exact_and_bounded(files, args):
    root, _ = files
    with bounded.organization_file_read_scope((str(root),), 1000) as scope:
        raw = {"path": "root0/notes.txt", **args}
        with pytest.raises(bounded.OrganizationFileReadError):
            scope.validate_arguments(raw)
        assert scope.canonical_arguments(raw) == {"path": "root0/notes.txt"}
        result = json.loads(dispatch("root0/notes.txt", **args))
        assert result["success"] is False and result["blocked"] is True
        assert scope.canonical_arguments({**raw, "other": "private"}) == {}


@pytest.mark.linux_only
def test_receipt_arguments_keep_model_path_and_offset_within_store_bounds(files):
    root, _ = files
    with bounded.organization_file_read_scope((str(root),), 1000) as scope:
        accepted_path = "root0/" + "a" * (1024 - len("root0/"))
        accepted = {"path": accepted_path, "offset": 100_000, "limit": 1}
        assert scope.validate_arguments(accepted) == accepted
        assert scope.canonical_arguments(accepted) == accepted
        rejected_path = {**accepted, "path": accepted_path + "a"}
        assert scope.canonical_arguments(rejected_path) == {
            "path": "[outside configured roots]"}
        rejected_offset = {"path": "root0/notes.txt", "offset": 100_001}
        assert scope.canonical_arguments(rejected_offset) == {"path": "root0/notes.txt"}
        for rejected in (rejected_path, rejected_offset):
            with pytest.raises(bounded.OrganizationFileReadError):
                scope.validate_arguments(rejected)
            result = json.loads(dispatch(**rejected))
            assert result["success"] is False and result["blocked"] is True


@pytest.mark.linux_only
def test_only_explicit_roots_are_addressable_and_absolute_receipts_are_hidden(files):
    root, _ = files
    other = root.parent / "other"
    other.mkdir()
    (other / "notes.txt").write_text("other", encoding="utf-8")
    with bounded.organization_file_read_scope((str(root), str(other)), 1000) as scope:
        assert json.loads(dispatch("root1/notes.txt"))["content"] == "1|other"
        assert json.loads(dispatch(str(other / "notes.txt")))["blocked"] is True
        assert scope.canonical_arguments({"path": str(other / "notes.txt")}) == {
            "path": "[outside configured roots]"}
    with bounded.organization_file_read_scope((str(root),), 1000):
        assert json.loads(dispatch("root1/notes.txt"))["blocked"] is True


@pytest.mark.linux_only
def test_symlink_targets_parents_roots_and_root_ancestors_are_rejected(files):
    root, _ = files
    outside = root.parent / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("outside secret", encoding="utf-8")
    (root / "linked.txt").symlink_to(outside / "secret.txt")
    (root / "linked_dir").symlink_to(outside, target_is_directory=True)
    (root / "inside_link.txt").symlink_to(root / "notes.txt")
    with bounded.organization_file_read_scope((str(root),), 1000):
        for path in ("root0/linked.txt", "root0/linked_dir/secret.txt", "root0/inside_link.txt"):
            result = json.loads(dispatch(path))
            assert result["success"] is False and result["blocked"] is True
            assert "outside secret" not in json.dumps(result)
    root_link = root.parent / "root_link"
    root_link.symlink_to(root, target_is_directory=True)
    parent_link = root.parent / "parent_link"
    parent_link.symlink_to(root.parent, target_is_directory=True)
    for configured in (root_link, parent_link / root.name):
        with pytest.raises(bounded.OrganizationFileReadError, match="without symlinks"):
            with bounded.organization_file_read_scope((str(configured),), 1000):
                pytest.fail("A symlink in configured roots must not be accepted")
        assert bounded.get_organization_file_read_scope() is None


@pytest.mark.linux_only
def test_root_config_is_validated_on_scope_entry_and_descriptors_are_closed(files):
    root, _ = files
    invalid = ((), ("relative",), (str(root / "notes.txt"),),
               (str(root / "missing"),), (str(root), str(root / "missing")))
    for roots in invalid:
        with pytest.raises(bounded.OrganizationFileReadError):
            with bounded.organization_file_read_scope(roots, 1000):
                pytest.fail("Invalid roots must fail closed")
    with bounded.organization_file_read_scope((str(root),), 1000) as scope:
        descriptors = [fd for _, fd in scope._roots.values()]
    for fd in descriptors:
        with pytest.raises(OSError):
            os.fstat(fd)
    assert json.loads(scope.read_file("root0/notes.txt"))["blocked"] is True


@pytest.mark.linux_only
def test_pinned_directory_walk_cannot_follow_a_concurrent_parent_swap(files, monkeypatch):
    root, _ = files
    nested = root / "nested"
    nested.mkdir()
    (nested / "file.txt").write_text("authorized", encoding="utf-8")
    outside = root.parent / "outside"
    outside.mkdir()
    (outside / "file.txt").write_text("outside", encoding="utf-8")
    real_open = os.open
    with bounded.organization_file_read_scope((str(root),), 1000):
        def racing_open(path, flags, *args, **kwargs):
            if path == "file.txt":
                nested.rename(root / "pinned")
                nested.symlink_to(outside, target_is_directory=True)
            return real_open(path, flags, *args, **kwargs)
        monkeypatch.setattr(bounded.os, "open", racing_open)
        result = json.loads(dispatch("root0/nested/file.txt"))
        assert result["success"] is True
        assert result["content"] == "1|authorized"


@pytest.mark.linux_only
def test_final_component_swap_does_not_follow_a_symlink(files, monkeypatch):
    root, _ = files
    outside = root.parent / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    real_open = os.open
    with bounded.organization_file_read_scope((str(root),), 1000):
        def racing_open(path, flags, *args, **kwargs):
            if path == "notes.txt":
                (root / "notes.txt").unlink()
                (root / "notes.txt").symlink_to(outside)
            return real_open(path, flags, *args, **kwargs)
        monkeypatch.setattr(bounded.os, "open", racing_open)
        result = json.loads(dispatch("root0/notes.txt"))
        assert result["success"] is False and result["blocked"] is True


@pytest.mark.linux_only
@pytest.mark.parametrize("kind", ["fifo", "directory", "invalid_utf8", "binary", "pdf", "oversize"])
def test_unsupported_file_kinds_are_bounded_and_never_extracted(files, kind, monkeypatch):
    root, _ = files
    name = "target.pdf" if kind == "pdf" else "target.txt"
    target = root / name
    if kind == "fifo":
        os.mkfifo(target)
    elif kind == "directory":
        target.mkdir()
    elif kind == "oversize":
        with target.open("wb") as stream:
            stream.truncate(bounded.MAX_FILE_BYTES + 1)
    else:
        target.write_bytes({"invalid_utf8": b"\xff", "binary": b"abc\x00def", "pdf": b"%PDF-1.7"}[kind])
    extractor = Mock(side_effect=AssertionError("Document extraction is forbidden"))
    monkeypatch.setattr(file_tools, "_read_extracted_document", extractor)
    read = Mock(wraps=os.read)
    monkeypatch.setattr(bounded.os, "read", read)
    with bounded.organization_file_read_scope((str(root),), 1000):
        encoded = dispatch(f"root0/{name}")
        result = json.loads(encoded)
        assert result["success"] is False and result["blocked"] is True
        assert len(encoded) <= 1000
    if kind in {"fifo", "directory", "pdf", "oversize"}:
        read.assert_not_called()
    extractor.assert_not_called()


@pytest.mark.linux_only
def test_credential_files_block_before_bytes_and_text_is_redacted(files, monkeypatch):
    root, home = files
    secret = "sk-examplecredential12345678901234567890"
    (root / ".env").write_text(f"API_KEY={secret}", encoding="utf-8")
    (home / "auth.json").write_text(json.dumps({"token": secret}), encoding="utf-8")
    (root / "config.txt").write_text(f"credential: {secret}\n", encoding="utf-8")
    read = Mock(wraps=os.read)
    monkeypatch.setattr(bounded.os, "read", read)
    with bounded.organization_file_read_scope((str(root), str(home)), 1000):
        for path in ("root0/.env", "root1/auth.json"):
            result = json.loads(dispatch(path))
            assert result["success"] is False and result["blocked"] is True
            assert secret not in json.dumps(result)
        read.assert_not_called()
        encoded = dispatch("root0/config.txt")
        assert json.loads(encoded)["success"] is True
        assert secret not in encoded
        assert "redacted" in encoded


@pytest.mark.linux_only
def test_environment_basename_blocks_without_resolving_a_mutable_symlink(files, monkeypatch):
    root, _ = files
    target = root / ".env"
    target.symlink_to(root / "notes.txt")
    guard = Mock(wraps=bounded.get_read_block_error)
    monkeypatch.setattr(bounded, "get_read_block_error", guard)
    read = Mock(wraps=os.read)
    monkeypatch.setattr(bounded.os, "read", read)
    with bounded.organization_file_read_scope((str(root),), 1000):
        result = json.loads(dispatch("root0/.env"))
        assert result["success"] is False and result["blocked"] is True
    guard.assert_not_called()
    read.assert_not_called()


@pytest.mark.linux_only
def test_pinned_profile_root_rename_and_symlink_replacement_cannot_reclassify_credentials(files, monkeypatch):
    root, home = files
    (home / "auth.json").write_text('{"opaque":"never-read-these-bytes"}', encoding="utf-8")
    (home / "cache").mkdir()
    (home / "cache" / "notes.txt").write_text("private cached state", encoding="utf-8")
    active = home.parent / "active-profile"
    active.symlink_to(home, target_is_directory=True)
    monkeypatch.setenv("HERMES_HOME", str(active))
    read = Mock(wraps=os.read)
    monkeypatch.setattr(bounded.os, "read", read)
    with bounded.organization_file_read_scope((str(home),), 1000):
        moved = home.parent / "relocated-profile"
        home.rename(moved)
        active.unlink()
        active.symlink_to(moved, target_is_directory=True)
        home.symlink_to(root, target_is_directory=True)
        # The original descriptor still targets the credential-bearing profile,
        # while the mutable absolute name is now an ordinary project location.
        for relative in ("auth.json", "cache/notes.txt"):
            assert bounded.get_read_block_error(str(home / relative)) is None
            result = json.loads(dispatch(f"root0/{relative}"))
            assert result["success"] is False and result["blocked"] is True
            assert "content" not in result
    read.assert_not_called()


@pytest.mark.linux_only
@pytest.mark.parametrize("name", [
    "auth.json", "mcp-tokens", "browser-profile", ".hub",
])
def test_credential_and_internal_components_block_reads_and_nested_root_grants(files, monkeypatch, name):
    root, _ = files
    protected = root / name / "nested"
    protected.mkdir(parents=True)
    (protected / "notes.txt").write_text("never-read-these-bytes", encoding="utf-8")
    read = Mock(wraps=os.read)
    monkeypatch.setattr(bounded.os, "read", read)
    with bounded.organization_file_read_scope((str(root),), 1000):
        result = json.loads(dispatch(f"root0/{name}/nested/notes.txt"))
        assert result["success"] is False and result["blocked"] is True
    with pytest.raises(bounded.OrganizationFileReadError, match="Protected credential or internal"):
        with bounded.organization_file_read_scope((str(protected),), 1000):
            pytest.fail("A root inside an excluded lexical component cannot grant access")
    read.assert_not_called()


@pytest.mark.linux_only
def test_project_auth_and_cache_sources_work_but_profile_internal_roots_do_not(files, monkeypatch):
    root, home = files
    for relative in ("src/auth/login.py", "cache/notes.txt", "src/sessions/handler.py", "pairing/logic.py"):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("ordinary project text", encoding="utf-8")
    for relative in ("auth", "cache", "sessions", "pairing"):
        target = home / relative / "nested"
        target.mkdir(parents=True)
        (target / "notes.txt").write_text("private profile state", encoding="utf-8")
    with bounded.organization_file_read_scope((str(root),), 1000):
        for relative in ("src/auth/login.py", "cache/notes.txt", "src/sessions/handler.py", "pairing/logic.py"):
            result = json.loads(dispatch(f"root0/{relative}"))
            assert result["success"] is True
            assert result["content"] == "1|ordinary project text"
    read = Mock(wraps=os.read)
    monkeypatch.setattr(bounded.os, "read", read)
    with bounded.organization_file_read_scope((str(home),), 1000):
        for relative in ("auth", "cache", "sessions", "pairing"):
            result = json.loads(dispatch(f"root0/{relative}/nested/notes.txt"))
            assert result["success"] is False and result["blocked"] is True
    for relative in ("auth", "cache", "sessions", "pairing"):
        with pytest.raises(bounded.OrganizationFileReadError, match="Protected credential or internal"):
            with bounded.organization_file_read_scope((str(home / relative / "nested"),), 1000):
                pytest.fail("Profile-internal directories cannot be source roots")
    read.assert_not_called()


@pytest.mark.linux_only
@pytest.mark.parametrize("relative", ["auth/google_oauth.json", "cache/bws_cache.json"])
def test_actual_credential_files_stay_blocked_inside_ordinary_project_directories(files, monkeypatch, relative):
    root, _ = files
    target = root / relative
    target.parent.mkdir(parents=True)
    target.write_text("never-read-these-bytes", encoding="utf-8")
    read = Mock(wraps=os.read)
    monkeypatch.setattr(bounded.os, "read", read)
    with bounded.organization_file_read_scope((str(root),), 1000):
        result = json.loads(dispatch(f"root0/{relative}"))
        assert result["success"] is False and result["blocked"] is True
    read.assert_not_called()


@pytest.mark.linux_only
def test_hardlink_from_granted_root_to_credentials_is_rejected_before_bytes(files, monkeypatch):
    root, home = files
    credential = home / "auth.json"
    credential.write_text('{"opaque":"never-read-these-bytes"}', encoding="utf-8")
    os.link(credential, root / "ordinary.txt")
    read = Mock(wraps=os.read)
    monkeypatch.setattr(bounded.os, "read", read)
    with bounded.organization_file_read_scope((str(root),), 1000):
        result = json.loads(dispatch("root0/ordinary.txt"))
        assert result["success"] is False and result["blocked"] is True
        assert "Hard-linked" in result["error"]
        assert "content" not in result
    read.assert_not_called()


@pytest.mark.linux_only
def test_empty_unicode_paginated_and_large_output_preserve_bounded_json(files):
    root, _ = files
    (root / "empty.txt").write_text("", encoding="utf-8")
    (root / "many.txt").write_text("\n".join('quotation " and slash \\ and é雪' for _ in range(5000)), encoding="utf-8")
    (root / "line.txt").write_text("x" * 10000, encoding="utf-8")
    with bounded.organization_file_read_scope((str(root),), 1000):
        empty = json.loads(dispatch("root0/empty.txt"))
        assert empty["success"] is True and empty["content"] == ""
        assert empty["total_lines"] == 0 and empty["file_size"] == 0
        encoded = dispatch("root0/many.txt")
        assert len(encoded) <= 1000
        result = json.loads(encoded)
        assert result["success"] is True and result["truncated"] is True
        assert result["total_lines"] == 5000
        assert len(result["content"].splitlines()) <= bounded.MAX_READ_LINES
        next_page = json.loads(dispatch("root0/many.txt", offset=result["next_offset"]))
        assert next_page["content"].startswith(f'{result["next_offset"]}|')
        oversized_line = dispatch("root0/line.txt")
        assert len(oversized_line) <= 1000
        assert json.loads(oversized_line)["success"] is False


@pytest.mark.linux_only
def test_context_scopes_isolate_threads_and_async_tasks_and_restore_on_exception(files):
    root, _ = files
    other = root.parent / "other"
    other.mkdir()
    (other / "notes.txt").write_text("other", encoding="utf-8")
    assert bounded.get_organization_file_read_scope() is None
    with bounded.organization_file_read_scope((str(root),), 1000) as outer:
        copied = copy_context()
        with pytest.raises(RuntimeError, match="unwind"):
            with bounded.organization_file_read_scope((str(other),), 1000):
                assert "other" in dispatch("root0/notes.txt")
                raise RuntimeError("unwind")
        assert bounded.get_organization_file_read_scope() is outer
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(bounded.get_organization_file_read_scope).result() is None

        async def read_in_scope(path, expected):
            with bounded.organization_file_read_scope((str(path),), 1000):
                await asyncio.sleep(0)
                result = json.loads(dispatch("root0/notes.txt"))
                assert expected in result["content"]

        async def concurrent_reads():
            await asyncio.gather(read_in_scope(root, "first"), read_in_scope(other, "other"))

        asyncio.run(concurrent_reads())
        assert "first" in dispatch("root0/notes.txt")
    assert bounded.get_organization_file_read_scope() is None
    # A saved context cannot turn an expired grant into the default broad backend.
    assert json.loads(copied.run(dispatch, "root0/notes.txt"))["blocked"] is True


def test_unbound_read_retains_the_existing_backend(monkeypatch):
    assert bounded.get_organization_file_read_scope() is None
    backend = Mock(side_effect=RuntimeError("normal backend selected"))
    monkeypatch.setattr(file_tools, "_get_file_ops", backend)
    result = json.loads(file_tools.read_file_tool("/bounded-test-nonexistent.txt"))
    assert "normal backend selected" in result["error"]
    backend.assert_called()


@pytest.mark.windows_only
def test_unsupported_platform_fails_explicitly():
    with pytest.raises(bounded.OrganizationFileReadError, match="POSIX"):
        with bounded.organization_file_read_scope(("C:\\source",), 1000):
            pytest.fail("Native Windows must not fall back to unrestricted reads")


@pytest.mark.macos_only
def test_native_macos_supports_no_follow_directory_reads(files):
    root, _ = files
    with bounded.organization_file_read_scope((str(root),), 1000):
        result = json.loads(dispatch("root0/notes.txt"))
        assert result["success"] is True
        assert result["content"].startswith("1|first")


def _managed_source(content, *, revision=0):
    import hashlib
    return {"content": content, "sourceSha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "workspaceRevision": revision, "workspaceId": "obj_managed"}


@pytest.mark.linux_only
def test_managed_loader_preserves_raw_bytes_and_revision_pagination(files):
    root, _ = files
    original = "\ufeff first\r\nsecond\rthird\n  "
    (root / "notes.txt").write_bytes(original.encode("utf-8"))
    snapshots, loads = {}, []

    def resolve(path, loader):
        if path not in snapshots:
            content = loader()
            loads.append(content)
            snapshots[path] = _managed_source(content)
        return snapshots[path]

    with bounded.organization_file_read_scope((str(root),), 1000, resolve_workspace_source=resolve):
        first = json.loads(dispatch("root0/notes.txt", limit=2))
        assert first["content"] == "\ufeff first\r\nsecond\r"
        assert first["contentFormat"] == "raw" and first["truncated"] is True
        assert first["sourceSha256"] == snapshots["root0/notes.txt"]["sourceSha256"]
        assert first["workspaceRevision"] == 0 and first["workspaceId"] == "obj_managed"
        # A later source edit cannot change an already captured managed revision.
        (root / "notes.txt").write_bytes(b"changed outside workspace")
        second = json.loads(dispatch("root0/notes.txt", offset=first["next_offset"]))
        assert first["content"] + second["content"] == original
        assert second["sourceSha256"] == first["sourceSha256"]
        snapshots["root0/notes.txt"] = _managed_source("managed revision\r\n", revision=1)
        current = json.loads(dispatch("root0/notes.txt"))
        assert current["content"] == "managed revision\r\n" and current["workspaceRevision"] == 1
        assert loads == [original]
    assert (root / "notes.txt").read_bytes() == b"changed outside workspace"


@pytest.mark.linux_only
@pytest.mark.parametrize("source", [
    "credential sk-examplecredential12345678901234567890\n",
    "https://user:password@example.test/source\n", "雪" * 10923,
], ids=["redacted-secret", "url-credential", "utf8-byte-overflow"])
def test_managed_original_rejects_redaction_and_byte_overflow_before_capture(files, source):
    root, _ = files
    (root / "notes.txt").write_bytes(source.encode("utf-8"))
    captured = []

    def resolve(path, loader):
        content = loader()
        captured.append(content)
        return _managed_source(content)

    with bounded.organization_file_read_scope((str(root),), 1000, resolve_workspace_source=resolve):
        result = json.loads(dispatch("root0/notes.txt"))
        assert result["blocked"] is True and not result["success"]
        assert "content" not in result and not captured


@pytest.mark.linux_only
@pytest.mark.parametrize("change", [
    lambda s: s.update(sourceSha256="0" * 64),
    lambda s: s.update(workspaceRevision=True),
    lambda s: s.update(workspaceRevision=-1),
    lambda s: s.update(workspaceId=""),
    lambda s: s.update(content="sk-examplecredential12345678901234567890"),
    lambda s: s.update(content="x" * 32769),
    lambda s: s.update(content="has\0binary"),
])
def test_managed_callback_output_still_requires_exact_bounded_safe_source(files, change):
    root, _ = files
    value = _managed_source("first\n")
    change(value)
    with bounded.organization_file_read_scope((str(root),), 1000, resolve_workspace_source=lambda *_: value):
        result = json.loads(dispatch("root0/notes.txt"))
        assert result["blocked"] is True and not result["success"]
        assert "content" not in result


@pytest.mark.linux_only
def test_managed_paths_keep_no_follow_and_credential_checks_ahead_of_capture(files):
    root, _ = files
    (root / "alias.txt").symlink_to(root / "notes.txt")
    (root / ".env").write_text("not source")
    calls, captured = [], []

    def resolve(path, loader):
        calls.append(path)
        content = loader()
        captured.append(content)
        return _managed_source(content)

    with bounded.organization_file_read_scope((str(root),), 1000, resolve_workspace_source=resolve):
        for path in ("root0/../notes.txt", "root0/.env", "root0/alias.txt"):
            assert json.loads(dispatch(path))["blocked"] is True
    assert calls == ["root0/alias.txt"] and not captured


@pytest.mark.linux_only
def test_managed_maximum_source_keeps_full_hash_in_character_truncated_pages(files):
    root, _ = files
    original = ("x" * 126 + "\r\n") * 256
    assert len(original.encode()) == bounded.MAX_EDIT_BYTES
    (root / "notes.txt").write_bytes(original.encode())
    captured = []

    def resolve(path, loader):
        if not captured:
            captured.append(_managed_source(loader()))
        return captured[0]

    with bounded.organization_file_read_scope((str(root),), 1000, resolve_workspace_source=resolve):
        pieces, offset = [], 1
        while True:
            encoded = dispatch("root0/notes.txt", offset=offset)
            assert len(encoded) <= 1000
            result = json.loads(encoded)
            assert result["success"] is True
            assert result["sourceSha256"] == captured[0]["sourceSha256"]
            assert result["file_size"] == bounded.MAX_EDIT_BYTES
            pieces.append(result["content"])
            if not result["truncated"]:
                break
            assert result["truncated_by"] == "characters"
            assert result["next_offset"] > offset
            offset = result["next_offset"]
        assert "".join(pieces) == original


@pytest.mark.linux_only
def test_scoped_discovery_and_literal_search_never_follow_links_or_expose_credentials(files, tmp_path):
    root, _ = files
    (root / 'nested').mkdir()
    (root / 'nested' / 'code.py').write_text('first match\nsecond match\n', encoding='utf-8')
    (root / '.env').write_text('TOP_SECRET=first', encoding='utf-8')
    outside = tmp_path / 'outside.txt'
    outside.write_text('first outside', encoding='utf-8')
    (root / 'link.txt').symlink_to(outside)
    os.link(outside, root / 'hard.txt')
    (root / 'opaque.pdf').write_bytes(b'%PDF-first')
    with bounded.organization_file_read_scope((str(root),), 3000) as scope:
        listed = json.loads(scope.discover_files({'path': 'root0'}))
        assert listed['success'] and not listed['truncated']
        assert {row['path'] for row in listed['files']} == {'root0/notes.txt', 'root0/nested', 'root0/nested/code.py'}
        assert next(row for row in listed['files'] if row['path'] == 'root0/nested')['kind'] == 'directory'
        searched = json.loads(scope.discover_files({'path': 'root0', 'query': 'first'}, search=True))
        assert searched['success'] and searched['scannedBytes'] > 0
        assert {row['path'] for row in searched['matches']} == {'root0/notes.txt', 'root0/nested/code.py'}
        assert all(row['line'] == 1 for row in searched['matches'])
        assert 'TOP_SECRET' not in json.dumps(searched) and 'outside' not in json.dumps(searched)
        assert json.loads(scope.discover_files({'path': 'root0/../'}))['blocked']
        assert json.loads(scope.discover_files({'path': 'root0/link.txt'}))['blocked']
        assert json.loads(scope.discover_files({'path': 'root0', 'query': 'ghp_' + 'A' * 36}, search=True))['blocked']
    assert json.loads(scope.discover_files({'path': 'root0'}))['blocked']


@pytest.mark.linux_only
def test_discovery_has_real_entry_depth_result_and_search_byte_bounds(files):
    root, _ = files
    for i in range(550):
        (root / f'note-{i:04d}.txt').write_text('needle\n', encoding='utf-8')
    with bounded.organization_file_read_scope((str(root),), 1000) as scope:
        raw = scope.discover_files({'path': 'root0', 'limit': 100})
        assert len(raw) <= 1000
        result = json.loads(raw)
        assert result['success'] and result['truncated'] and 0 < len(result['files']) < 100
        raw = scope.discover_files({'path': 'root0', 'query': 'absent', 'limit': 100}, search=True)
        result = json.loads(raw)
        assert result['truncated'] and result['scannedEntries'] == 500 and result['matches'] == []
        assert result['scannedBytes'] <= bounded.MAX_FILE_BYTES


@pytest.mark.linux_only
def test_exact_source_handoff_reader_preserves_bytes_and_ignores_managed_resolver(files):
    root, _ = files
    original = '\ufeffcafé\r\n  trailing  '
    (root / 'notes.txt').write_bytes(original.encode('utf-8'))
    with bounded.organization_file_read_scope((str(root),), 1000,
            resolve_workspace_source=lambda *_: pytest.fail('Handoff must read original source bytes')) as scope:
        result = scope.read_exact_source('root0/notes.txt')
    import hashlib
    assert result['content'].encode('utf-8') == original.encode('utf-8')
    assert result['sha256'] == hashlib.sha256(original.encode('utf-8')).hexdigest()
