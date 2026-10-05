"""Publish exact reviewed bytes to a new local branch without running Git.

This backend never changes the index, HEAD, or working tree, and never invokes
repository hooks, filters, signing programs, credential helpers, or subprocesses.
Only SHA-1 repositories with a real in-root .git directory are supported. Normal
loose objects and v2 pack indexes (including delta objects) are read as data.
The store owns authorization/review provenance and retains the returned receipt.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import hashlib
import json
import os
import re
import stat
import struct
import time
import uuid
import zlib


MAX_FILES = 128
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_OBJECT_BYTES = 16 * 1024 * 1024
MAX_PACK_BYTES = 256 * 1024 * 1024
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_PACK_NAME = re.compile(r"pack-[0-9a-f]{40}\.idx\Z")
_REF = re.compile(r"refs/heads/[A-Za-z0-9_./-]+\Z")


class SourceIntegrationError(ValueError):
    """A source integration refusal safe to show without exposing source bytes."""


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def source_base_sha256(source_base):
    return _digest(source_base)


def _protected_parts(parts):
    denied = {".git", ".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", ".netrc", ".npmrc",
              ".pypirc", ".pgpass", ".git-credentials", "credentials", "credentials.json",
              "application_default_credentials.json", "id_rsa", "id_ed25519", "id_dsa", "id_ecdsa"}
    normalized = [part.lower() for part in parts]
    env_templates = {".env.example", ".env.sample", ".env.template"}
    if (any(part in denied or part.endswith((".pem", ".key", ".p12", ".pfx"))
            or (part.startswith(".env.") and part not in env_templates) for part in normalized)
            or any(left == ".config" and right in {"gh", "gcloud"}
                   for left, right in zip(normalized, normalized[1:]))):
        raise SourceIntegrationError("Credential and Git-internal paths cannot be integrated.")


def _parts(path):
    from tools.organization_file_read import _path_parts, _check_lexical_path, _profile_credential_prefixes
    parts = _path_parts(path, absolute=False)
    if len(path) > 1024 or len(parts) < 2 or parts[0] != "root0":
        raise SourceIntegrationError("Source integration requires canonical root0 paths.")
    _check_lexical_path(parts[1:], _profile_credential_prefixes())
    _protected_parts(parts[1:])
    return parts[1:]


def _manifest(manifest):
    from tools.organization_file_read import _validate_edit_source
    if not isinstance(manifest, (list, tuple)) or not 1 <= len(manifest) <= MAX_FILES:
        raise SourceIntegrationError("Source integration requires a bounded exact reviewed manifest.")
    result, size, seen = [], 0, set()
    for item in manifest:
        if not isinstance(item, dict):
            raise SourceIntegrationError("Invalid reviewed source manifest entry.")
        path = item.get("path")
        _parts(path)
        if path in seen:
            raise SourceIntegrationError("Reviewed source paths must be unique.")
        seen.add(path)
        operation = item.get("operation", "update")
        if operation not in {"update", "create", "delete"}:
            raise SourceIntegrationError("Unsupported reviewed source operation.")
        before = item.get("base_sha256", item.get("baseSha256"))
        if operation == "create":
            if before is not None:
                raise SourceIntegrationError("A create operation requires an absent source preimage.")
        elif not isinstance(before, str) or not _HEX64.fullmatch(before):
            raise SourceIntegrationError("Each source edit requires its exact original preimage SHA-256.")
        content = item.get("content", item.get("new_content"))
        after = item.get("sha256", item.get("new_sha256", item.get("newSha256")))
        if operation == "delete":
            if content is not None or after is not None:
                raise SourceIntegrationError("A delete operation requires absent proposed content.")
        else:
            content = _validate_edit_source(content)
            raw = content.encode("utf-8")
            if hashlib.sha256(raw).hexdigest() != after:
                raise SourceIntegrationError("Reviewed source bytes do not match their SHA-256.")
            size += len(raw)
        result.append({"path": path, "operation": operation, "baseSha256": before,
                       "sha256": after, "content": content})
    if size > MAX_MANIFEST_BYTES:
        raise SourceIntegrationError("Reviewed source manifest exceeds its byte limit.")
    paths = sorted(seen)
    if any(right.startswith(left + "/") for left, right in zip(paths, paths[1:])):
        raise SourceIntegrationError("Reviewed source paths overlap as files and directories.")
    return sorted(result, key=lambda item: item["path"])


def source_manifest_sha256(manifest):
    """Digest normalized operations, original preimages, and exact reviewed bytes."""
    return _digest(_manifest(manifest))


def _identity(info):
    return [info.st_dev, info.st_ino]


def _directory(parent, name):
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)


def _read(parent, name, limit, *, optional=False):
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
    except FileNotFoundError:
        if optional:
            return None
        raise
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
            raise SourceIntegrationError("Source metadata must be bounded regular files without hard links.")
        data = bytearray()
        while len(data) <= limit:
            chunk = os.read(fd, min(65536, limit + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        after = os.fstat(fd)
        attached = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (len(data) > limit or before.st_size != len(data)
                or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                or after.st_nlink != 1 or _identity(attached) != _identity(after)
                or not stat.S_ISREG(attached.st_mode)):
            raise SourceIntegrationError("Source metadata changed during its exact read.")
        return bytes(data)
    finally:
        os.close(fd)


def _write(fd, data):
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise SourceIntegrationError("Source metadata could not be written completely.")
        view = view[written:]
    os.fsync(fd)


def _inflate(raw, limit=MAX_OBJECT_BYTES):
    decoder = zlib.decompressobj()
    try:
        body = decoder.decompress(raw, limit + 1)
    except zlib.error:
        raise SourceIntegrationError("Git object compression is invalid.") from None
    if len(body) > limit or not decoder.eof:
        raise SourceIntegrationError("Git object is truncated or exceeds the supported object limit.")
    return body


def _object_id(kind, content):
    return hashlib.sha1(kind + b" " + str(len(content)).encode() + b"\0" + content).hexdigest()


def _varint(data, position):
    value, shift = 0, 0
    while position < len(data) and shift < 64:
        byte = data[position]
        position += 1
        value |= (byte & 127) << shift
        if not byte & 128:
            return value, position
        shift += 7
    raise SourceIntegrationError("Git delta contains an invalid size.")


def _apply_delta(base, delta):
    base_size, position = _varint(delta, 0)
    size, position = _varint(delta, position)
    if base_size != len(base) or size > MAX_OBJECT_BYTES:
        raise SourceIntegrationError("Git delta base or output size is invalid.")
    output = bytearray()
    while position < len(delta):
        command = delta[position]
        position += 1
        if command & 128:
            offset, count = 0, 0
            for bit in range(7):
                if command & (1 << bit):
                    if position >= len(delta):
                        raise SourceIntegrationError("Git delta is truncated.")
                    value = delta[position]
                    position += 1
                    if bit < 4:
                        offset |= value << (8 * bit)
                    else:
                        count |= value << (8 * (bit - 4))
            count = count or 65536
            if offset + count > len(base):
                raise SourceIntegrationError("Git delta reads beyond its verified base.")
            output.extend(base[offset:offset + count])
        elif command:
            if position + command > len(delta):
                raise SourceIntegrationError("Git delta is truncated.")
            output.extend(delta[position:position + command])
            position += command
        else:
            raise SourceIntegrationError("Git delta contains an invalid operation.")
        if len(output) > size:
            raise SourceIntegrationError("Git delta exceeds its declared size.")
    if len(output) != size:
        raise SourceIntegrationError("Git delta does not match its declared size.")
    return bytes(output)


class _Repository:
    def __init__(self, roots, cancel=None):
        from tools.organization_file_read import _require_posix_support, _open_root, _path_parts, _profile_credential_prefixes
        _require_posix_support()
        if not isinstance(roots, (list, tuple)) or len(roots) != 1:
            raise SourceIntegrationError("Source integration currently requires one explicitly granted repository root.")
        self.path = roots[0]
        _protected_parts(_path_parts(self.path, absolute=True))
        self.cancel = cancel
        self.prefixes = _profile_credential_prefixes()
        self.stack = ExitStack()
        self.dirs, self.cache, self.packs = {}, {}, None
        self.active_locks = {}
        self.cache_bytes = 0
        self.object_work_remaining = 128 * 1024 * 1024
        try:
            self.root = _open_root(self.path, self.prefixes)
            self.stack.callback(os.close, self.root)
            self.dirs[()] = self.root
            git_info = os.stat(".git", dir_fd=self.root, follow_symlinks=False)
            if not stat.S_ISDIR(git_info.st_mode):
                raise SourceIntegrationError(
                    "Source integration requires a real in-root .git directory; linked worktrees and gitdir redirects are unsupported.")
            self.git = self.dir((".git",))
            self.objects = self.dir((".git", "objects"))
            for redirected in ("commondir", "gitdir"):
                if _read(self.git, redirected, 4096, optional=True) is not None:
                    raise SourceIntegrationError("Git directory redirects and linked worktrees are unsupported.")
            try:
                info = self.dir((".git", "objects", "info"))
            except FileNotFoundError:
                info = None
            if info is not None:
                for redirected in ("alternates", "http-alternates"):
                    if _read(info, redirected, 65536, optional=True):
                        raise SourceIntegrationError("Git alternate object stores are unsupported; use a self-contained repository.")
        except BaseException:
            self.stack.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.stack.close()

    def dir(self, parts, *, create=False):
        if parts in self.dirs:
            return self.dirs[parts]
        parent = self.dir(parts[:-1], create=create)
        if create:
            try:
                os.mkdir(parts[-1], mode=0o700, dir_fd=parent)
                os.fsync(parent)
            except FileExistsError:
                pass
        fd = _directory(parent, parts[-1])
        self.stack.callback(os.close, fd)
        self.dirs[parts] = fd
        return fd

    def assert_attached(self):
        from tools.organization_file_read import _open_root
        current = _open_root(self.path, self.prefixes)
        try:
            if _identity(os.fstat(current)) != _identity(os.fstat(self.root)):
                raise SourceIntegrationError("The granted source root was replaced; no current-root receipt can be issued.")
        finally:
            os.close(current)
        for parts, fd in sorted(self.dirs.items(), key=lambda pair: len(pair[0])):
            if not parts:
                continue
            info = os.stat(parts[-1], dir_fd=self.dirs[parts[:-1]], follow_symlinks=False)
            if not stat.S_ISDIR(info.st_mode) or _identity(info) != _identity(os.fstat(fd)):
                raise SourceIntegrationError("A pinned repository directory changed during integration.")
        for (parent, name), identity in self.active_locks.items():
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if _identity(info) != identity or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise SourceIntegrationError("A source integration lock was replaced unexpectedly.")

    def packed_refs(self):
        raw = _read(self.git, "packed-refs", 16 * 1024 * 1024, optional=True)
        result = {}
        for line in (raw or b"").splitlines():
            if not line or line[:1] in {b"#", b"^"}:
                continue
            try:
                oid, ref = line.decode("ascii").split(" ", 1)
            except (ValueError, UnicodeDecodeError):
                raise SourceIntegrationError("Packed Git refs are invalid.") from None
            if not _HEX40.fullmatch(oid) or ref in result:
                raise SourceIntegrationError("Only unambiguous SHA-1 Git refs are supported.")
            result[ref] = oid
        return result

    def ref(self, name):
        if not _REF.fullmatch(name) or any(part in {"", ".", ".."} or part.endswith(".lock")
                                         for part in name.split("/")) or ".." in name:
            raise SourceIntegrationError("Only ordinary local branch refs are supported.")
        parts = (".git", *name.split("/"))
        try:
            parent = self.dir(parts[:-1])
        except FileNotFoundError:
            return self.packed_refs().get(name)
        raw = _read(parent, parts[-1], 256, optional=True)
        if raw is None:
            return self.packed_refs().get(name)
        try:
            oid = raw.decode("ascii").strip()
        except UnicodeDecodeError:
            raise SourceIntegrationError("Git branch ref is invalid.") from None
        if not _HEX40.fullmatch(oid):
            raise SourceIntegrationError("Symbolic branch chains and non-SHA-1 refs are unsupported.")
        return oid

    def head(self):
        raw = _read(self.git, "HEAD", 1024)
        try:
            text = raw.decode("ascii").strip()
        except UnicodeDecodeError:
            raise SourceIntegrationError("Git HEAD is invalid.") from None
        if text.startswith("ref: "):
            ref = text[5:]
            oid = self.ref(ref)
            if oid is None:
                raise SourceIntegrationError("Source integration requires an existing committed HEAD.")
        else:
            ref, oid = None, text
        if not _HEX40.fullmatch(oid):
            raise SourceIntegrationError("Source integration currently supports only SHA-1 repositories.")
        return {"head": oid, "headRef": ref, "headBytesSha256": hashlib.sha256(raw).hexdigest()}

    def check_cancelled(self):
        if self.cancel is not None and self.cancel.is_set():
            raise SourceIntegrationError("Source integration was cancelled before branch publication.")

    def _load_packs(self):
        self.packs = []
        try:
            directory = self.dir((".git", "objects", "pack"))
        except FileNotFoundError:
            return
        names = os.listdir(directory)
        if len(names) > 1024:
            raise SourceIntegrationError("Git pack directory exceeds the supported limit.")
        total = 0
        for name in sorted(names):
            self.check_cancelled()
            if not _PACK_NAME.fullmatch(name):
                continue
            raw = _read(directory, name, 64 * 1024 * 1024)
            if (len(raw) < 1072 or raw[:8] != b"\xfftOc\0\0\0\x02"
                    or hashlib.sha1(raw[:-20]).digest() != raw[-20:]):
                raise SourceIntegrationError("Git pack index is invalid or unsupported; a standard v2 index is required.")
            fanout = struct.unpack_from(">256I", raw, 8)
            count = fanout[-1]
            minimum = 1032 + count * 28 + 40
            if list(fanout) != sorted(fanout) or len(raw) < minimum:
                raise SourceIntegrationError("Git pack index bounds are invalid.")
            pack = _read(directory, name[:-4] + ".pack", MAX_PACK_BYTES)
            total += len(pack)
            if total > MAX_PACK_BYTES:
                raise SourceIntegrationError("Repository packs exceed the bounded integration limit.")
            if (len(pack) < 32 or pack[:4] != b"PACK" or struct.unpack_from(">I", pack, 4)[0] not in {2, 3}
                    or struct.unpack_from(">I", pack, 8)[0] != count
                    or hashlib.sha1(pack[:-20]).digest() != pack[-20:] or pack[-20:] != raw[-40:-20]):
                raise SourceIntegrationError("Git pack does not match its verified index.")
            self.packs.append((raw, pack, count))

    def _packed(self, oid, depth):
        if self.packs is None:
            self._load_packs()
        target = bytes.fromhex(oid)
        for index, pack, count in self.packs:
            left, right = 0, count
            while left < right:
                middle = (left + right) // 2
                candidate = index[1032 + middle * 20:1052 + middle * 20]
                if candidate < target:
                    left = middle + 1
                else:
                    right = middle
            if left == count or index[1032 + left * 20:1052 + left * 20] != target:
                continue
            offset = struct.unpack_from(">I", index, 1032 + count * 24 + left * 4)[0]
            if offset & 0x80000000:
                position = 1032 + count * 28 + (offset & 0x7fffffff) * 8
                if position + 8 > len(index) - 40:
                    raise SourceIntegrationError("Git pack large-object offset is invalid.")
                offset = struct.unpack_from(">Q", index, position)[0]
            return self._pack_object(pack, offset, depth)
        raise SourceIntegrationError("Required Git object is unavailable locally; no remote fetch was attempted.")

    def _pack_object(self, pack, offset, depth):
        if depth > 64 or not 12 <= offset < len(pack) - 20:
            raise SourceIntegrationError("Git delta depth or pack offset exceeds supported limits.")
        position = offset
        byte = pack[position]
        position += 1
        kind, size, shift = (byte >> 4) & 7, byte & 15, 4
        while byte & 128:
            if position >= len(pack) - 20 or shift > 63:
                raise SourceIntegrationError("Git packed object header is invalid.")
            byte = pack[position]
            position += 1
            size |= (byte & 127) << shift
            shift += 7
        if size > MAX_OBJECT_BYTES:
            raise SourceIntegrationError("Git packed object exceeds the supported object limit.")
        self.object_work_remaining -= size
        if self.object_work_remaining < 0:
            raise SourceIntegrationError("Git delta expansion exceeds the bounded integration work budget.")
        base_offset, base_oid = None, None
        if kind == 6:
            distance = 0
            for step in range(10):
                if position >= len(pack) - 20:
                    raise SourceIntegrationError("Git delta offset is truncated.")
                byte = pack[position]
                position += 1
                distance = ((distance + (1 if step else 0)) << 7) | (byte & 127)
                if not byte & 128:
                    break
            else:
                raise SourceIntegrationError("Git delta offset is invalid.")
            base_offset = offset - distance
            if distance <= 0:
                raise SourceIntegrationError("Git delta offset must reference an earlier object.")
        elif kind == 7:
            if position + 20 > len(pack) - 20:
                raise SourceIntegrationError("Git delta reference is truncated.")
            base_oid = pack[position:position + 20].hex()
            position += 20
        content = _inflate(pack[position:-20])
        if len(content) != size:
            raise SourceIntegrationError("Git packed object differs from its declared size.")
        kinds = {1: b"commit", 2: b"tree", 3: b"blob", 4: b"tag"}
        if kind in kinds:
            return kinds[kind], content
        if kind not in {6, 7}:
            raise SourceIntegrationError("Git packed object type is unsupported.")
        base_kind, base = (self._pack_object(pack, base_offset, depth + 1) if kind == 6
                           else self.object(base_oid, depth=depth + 1))
        _, delta_position = _varint(content, 0)
        output_size, _ = _varint(content, delta_position)
        self.object_work_remaining -= output_size
        if self.object_work_remaining < 0:
            raise SourceIntegrationError("Git delta expansion exceeds the bounded integration work budget.")
        return base_kind, _apply_delta(base, content)

    def object(self, oid, *, depth=0):
        self.check_cancelled()
        if not isinstance(oid, str) or not _HEX40.fullmatch(oid) or depth > 64:
            raise SourceIntegrationError("Invalid Git object identifier or delta chain.")
        if oid in self.cache:
            return self.cache[oid]
        try:
            bucket = self.dir((".git", "objects", oid[:2]))
            raw = _read(bucket, oid[2:], MAX_OBJECT_BYTES + 65536, optional=True)
        except FileNotFoundError:
            raw = None
        if raw is not None:
            unpacked = _inflate(raw, MAX_OBJECT_BYTES + 128)
            self.object_work_remaining -= len(unpacked)
            if self.object_work_remaining < 0:
                raise SourceIntegrationError("Git object reads exceed the bounded integration work budget.")
            try:
                header, content = unpacked.split(b"\0", 1)
                kind, size = header.split(b" ", 1)
            except ValueError:
                raise SourceIntegrationError("Git loose object header is invalid.") from None
            if kind not in {b"commit", b"tree", b"blob", b"tag"} or size != str(len(content)).encode():
                raise SourceIntegrationError("Git loose object type or size is invalid.")
        else:
            kind, content = self._packed(oid, depth)
        if _object_id(kind, content) != oid:
            raise SourceIntegrationError("Git object bytes do not match their content address.")
        if self.cache_bytes + len(content) <= 64 * 1024 * 1024:
            self.cache[oid] = kind, content
            self.cache_bytes += len(content)
        return kind, content

    def commit_tree(self, oid):
        kind, content = self.object(oid)
        first = content.split(b"\n", 1)[0]
        if kind != b"commit" or not first.startswith(b"tree "):
            raise SourceIntegrationError("Pinned source HEAD is not an ordinary commit.")
        tree = first[5:].decode("ascii")
        if not _HEX40.fullmatch(tree):
            raise SourceIntegrationError("Pinned source commit tree is invalid.")
        return tree

    def tree(self, oid):
        kind, content = self.object(oid)
        if kind != b"tree":
            raise SourceIntegrationError("Git source path does not refer to a tree.")
        entries, position = {}, 0
        while position < len(content):
            space = content.find(b" ", position)
            null = content.find(b"\0", space + 1)
            if space < 0 or null < 0 or null + 21 > len(content):
                raise SourceIntegrationError("Git tree contains a malformed entry.")
            mode, name = content[position:space], content[space + 1:null]
            if (mode not in {b"40000", b"100644", b"100755", b"120000", b"160000"}
                    or not name or name in {b".", b".."} or b"/" in name or name in entries):
                raise SourceIntegrationError("Git tree contains an unsupported or duplicate entry.")
            entries[name] = (mode, content[null + 1:null + 21].hex())
            position = null + 21
        return entries

    def entry(self, tree, parts):
        entry = self.tree(tree).get(parts[0].encode("utf-8"))
        if entry is None:
            return None
        if len(parts) == 1:
            return entry
        if entry[0] != b"40000":
            raise SourceIntegrationError("Source path traverses a tracked link, file, or submodule.")
        return self.entry(entry[1], parts[1:])

    def write_object(self, kind, content):
        self.check_cancelled()
        oid = _object_id(kind, content)
        bucket = self.dir((".git", "objects", oid[:2]), create=True)
        existing = _read(bucket, oid[2:], MAX_OBJECT_BYTES + 65536, optional=True)
        if existing is None:
            temporary = ".eidolon-" + uuid.uuid4().hex
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=bucket)
            try:
                try:
                    _write(fd, zlib.compress(kind + b" " + str(len(content)).encode() + b"\0" + content))
                finally:
                    os.close(fd)
                self.assert_attached()
                try:
                    os.link(temporary, oid[2:], src_dir_fd=bucket, dst_dir_fd=bucket, follow_symlinks=False)
                except FileExistsError:
                    pass
            finally:
                os.unlink(temporary, dir_fd=bucket)
                os.fsync(bucket)
        # Bypass prior cache entries so the proof observes what is on disk.
        self.cache.pop(oid, None)
        if self.object(oid) != (kind, content):
            raise SourceIntegrationError("Published Git object differs from the reviewed bytes.")
        return oid

    def updated_tree(self, tree, changes):
        entries = self.tree(tree) if tree else {}
        grouped = {}
        for parts, value in changes:
            grouped.setdefault(parts[0].encode("utf-8"), []).append((parts[1:], value))
        for name, nested in grouped.items():
            if nested[0][0]:
                previous = entries.get(name)
                if previous and previous[0] != b"40000":
                    raise SourceIntegrationError("A reviewed path conflicts with a tracked non-directory.")
                child = self.updated_tree(previous[1] if previous else None, nested)
                if self.tree(child):
                    entries[name] = b"40000", child
                else:
                    entries.pop(name, None)
            else:
                value = nested[0][1]
                if value is None:
                    entries.pop(name, None)
                else:
                    entries[name] = value
        order = sorted(entries, key=lambda name: name + (b"/" if entries[name][0] == b"40000" else b""))
        raw = b"".join(entries[name][0] + b" " + name + b"\0" + bytes.fromhex(entries[name][1]) for name in order)
        return self.write_object(b"tree", raw)


def _index_entries(repo):
    raw = _read(repo.git, "index", 32 * 1024 * 1024, optional=True)
    if raw is None:
        return {}
    if len(raw) < 32 or raw[:4] != b"DIRC" or hashlib.sha1(raw[:-20]).digest() != raw[-20:]:
        raise SourceIntegrationError("Git index is invalid or changed during integration.")
    version, count = struct.unpack_from(">II", raw, 4)
    if version not in {2, 3, 4} or count > 200000:
        raise SourceIntegrationError("Git index format exceeds the supported integration scope.")
    result, position, previous = {}, 12, b""
    for _ in range(count):
        start = position
        if position + 62 > len(raw) - 20:
            raise SourceIntegrationError("Git index entry is truncated.")
        mode = struct.unpack_from(">I", raw, position + 24)[0]
        oid = raw[position + 40:position + 60].hex()
        flags = struct.unpack_from(">H", raw, position + 60)[0]
        position += 62 + (2 if flags & 0x4000 else 0)
        if version == 4:
            strip, steps = 0, 0
            while position < len(raw) - 20 and steps < 10:
                byte = raw[position]
                position += 1
                strip = ((strip + (1 if steps else 0)) << 7) | (byte & 127)
                steps += 1
                if not byte & 128:
                    break
            else:
                raise SourceIntegrationError("Git index path compression is invalid.")
            if strip > len(previous):
                raise SourceIntegrationError("Git index path compression is invalid.")
        end = raw.find(b"\0", position, len(raw) - 20)
        if end < 0:
            raise SourceIntegrationError("Git index path is truncated.")
        path = (previous[:len(previous) - strip] if version == 4 else b"") + raw[position:end]
        previous = path
        position = end + 1 if version == 4 else start + ((end + 1 - start + 7) // 8) * 8
        if not path or path in result or flags & 0x3000:
            raise SourceIntegrationError("Unmerged, split, or duplicate Git index entries are unsupported.")
        result[path] = (format(mode, "o").encode(), oid)
    while position < len(raw) - 20:
        if position + 8 > len(raw) - 20:
            raise SourceIntegrationError("Git index extension is truncated.")
        signature, size = raw[position:position + 4], struct.unpack_from(">I", raw, position + 4)[0]
        position += 8 + size
        if position > len(raw) - 20 or not 65 <= signature[0] <= 90:
            raise SourceIntegrationError("Split, sparse, or required custom Git indexes are unsupported.")
    return result


def _preimages(repo, source_base, manifest):
    index = _index_entries(repo)
    proof = []
    for item in manifest:
        parts = _parts(item["path"])
        entry = repo.entry(source_base["tree"], parts)
        path_bytes = "/".join(parts).encode("utf-8")
        if item["operation"] == "create":
            if entry is not None or path_bytes in index:
                raise SourceIntegrationError("A reviewed create path already exists in the source commit or index.")
            expected, mode = None, b"100644"
        else:
            if entry is None or entry[0] not in {b"100644", b"100755"}:
                raise SourceIntegrationError("A reviewed source preimage is absent or is not a regular tracked file.")
            kind, expected = repo.object(entry[1])
            if kind != b"blob" or hashlib.sha256(expected).hexdigest() != item["baseSha256"]:
                raise SourceIntegrationError("Reviewed preimage differs from the pinned source commit.")
            if index.get(path_bytes) != entry:
                raise SourceIntegrationError("Reviewed source has staged changes; the target index must match pinned HEAD.")
            mode = entry[0]
        try:
            parent = repo.dir(parts[:-1])
            actual = _read(parent, parts[-1], 32768, optional=True)
        except FileNotFoundError:
            actual = None
        if actual != expected:
            raise SourceIntegrationError("Reviewed source has dirty or conflicting bytes; no branch was integrated.")
        if expected is not None:
            info = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            executable = bool(info.st_mode & 0o111)
            if executable != (mode == b"100755"):
                raise SourceIntegrationError("Reviewed source executable mode differs from pinned HEAD.")
        proof.append({"path": item["path"], "operation": item["operation"],
                      "baseSha256": item["baseSha256"], "sha256": item["sha256"],
                      "sourceBlob": entry[1] if entry else None, "mode": mode.decode()})
    return proof


def _base(repo):
    head = repo.head()
    return {"version": 1, "rootAlias": "root0", "rootIdentity": _identity(os.fstat(repo.root)),
            "gitIdentity": _identity(os.fstat(repo.git)), "rootPathSha256": hashlib.sha256(repo.path.encode()).hexdigest(), **head, "tree": repo.commit_tree(head["head"])}


@contextmanager
def _failure_boundary():
    try:
        yield
    except SourceIntegrationError:
        raise
    except (OSError, UnicodeError, struct.error, KeyError, TypeError, RecursionError, OverflowError) as exc:
        raise SourceIntegrationError("Source integration refused unsafe, unavailable, or changed repository metadata.") from exc


def prepare_source_integration(read_roots, manifest=None):
    """Read-only pin of the granted repository identity, HEAD, and parent tree."""
    with _failure_boundary(), _Repository(read_roots) as repo:
        source_base = {**_base(repo), "preparedAt": int(time.time())}
        if manifest is not None:
            _preimages(repo, source_base, _manifest(manifest))
        if _base(repo) != {key: value for key, value in source_base.items() if key != "preparedAt"}:
            raise SourceIntegrationError("Source HEAD changed while the integration base was prepared.")
        repo.assert_attached()
        return source_base


@contextmanager
def _lock(repo, parts):
    parent = repo.dir(parts[:-1], create=True)
    name = parts[-1] + ".lock"
    try:
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=parent)
    except FileExistsError:
        raise SourceIntegrationError("A Git metadata lock already exists; wait for its owner or resolve a stale lock explicitly.") from None
    identity = _identity(os.fstat(fd))
    repo.active_locks[parent, name] = identity
    try:
        yield fd, parent, name
    finally:
        del repo.active_locks[parent, name]
        os.close(fd)
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if _identity(info) != identity or not stat.S_ISREG(info.st_mode):
            raise SourceIntegrationError("A source integration lock was replaced unexpectedly.")
        os.unlink(name, dir_fd=parent)
        os.fsync(parent)


def _check_base(repo, source_base):
    if (not isinstance(source_base, dict) or type(source_base.get("preparedAt")) is not int
            or not 0 <= source_base["preparedAt"] <= 2**40
            or _base(repo) != {key: value for key, value in source_base.items() if key != "preparedAt"}):
        raise SourceIntegrationError("Source HEAD or repository identity drifted from the exact pinned integration base.")
    repo.assert_attached()


def integrate_source(read_roots, source_base, manifest, *, grant, integration_id, before_publish=None, cancel=None):
    """Create an exclusive deterministic branch with exact reviewed content.

    Git-compatible exclusive lockfiles freeze HEAD, its branch, packed refs, and
    the index while validating the compare-and-swap expectation. Existing refs
    are never replaced: a matching deterministic commit permits receipt recovery;
    any other existing branch is refused. Locks are not stolen, including stale
    locks left by a killed process. Repository config is never executed.
    """
    with _failure_boundary():
        reviewed = _manifest(manifest)
        manifest_hash, base_hash = _digest(reviewed), source_base_sha256(source_base)
        if (not isinstance(grant, dict) or grant.get("sourceIntegration") is not True
                or grant.get("sourceBaseSha256") != base_hash or grant.get("manifestSha256") != manifest_hash):
            raise SourceIntegrationError("Source integration lacks the exact user-granted base and reviewed manifest.")
        if not isinstance(integration_id, str) or not 1 <= len(integration_id) <= 256:
            raise SourceIntegrationError("Source integration requires a stable bounded request identifier.")
        request_hash = hashlib.sha256(integration_id.encode()).hexdigest()
        ref = "refs/heads/eidolon/" + request_hash[:32]
        if before_publish is not None and not callable(before_publish):
            raise SourceIntegrationError("Source publication guard must be a trusted callable.")
        with _Repository(read_roots, cancel) as repo, ExitStack() as locks:
            _check_base(repo, source_base)
            lock_paths = [(".git", "HEAD"), (".git", "packed-refs"), (".git", "index")]
            if source_base["headRef"]:
                lock_paths.append((".git", *source_base["headRef"].split("/")))
            if ref == source_base["headRef"]:
                raise SourceIntegrationError("Integration must create a new branch distinct from the current source branch.")
            for path in lock_paths:
                locks.enter_context(_lock(repo, path))
            ref_fd, ref_parent, lock_name = locks.enter_context(_lock(repo, (".git", *ref.split("/"))))
            _check_base(repo, source_base)
            proof = _preimages(repo, source_base, reviewed)
            changes = []
            for item, observed in zip(reviewed, proof):
                oid = (None if item["operation"] == "delete"
                       else repo.write_object(b"blob", item["content"].encode("utf-8")))
                observed.update({"integratedBlob": oid, "matched": True})
                changes.append((_parts(item["path"]), None if oid is None else (observed["mode"].encode(), oid)))
            tree = repo.updated_tree(source_base["tree"], changes)
            identity = b"Eidolon reviewed integration <eidolon@localhost> " + str(source_base["preparedAt"]).encode() + b" +0000"
            commit_bytes = (b"tree " + tree.encode() + b"\nparent " + source_base["head"].encode()
                            + b"\nauthor " + identity + b"\ncommitter " + identity
                            + b"\n\nEidolon reviewed source integration\n\nRequest-SHA256: " + request_hash.encode()
                            + b"\nSource-Base-SHA256: " + base_hash.encode()
                            + b"\nReviewed-Manifest-SHA256: " + manifest_hash.encode() + b"\n")
            commit = repo.write_object(b"commit", commit_bytes)
            # Repeat the exact preconditions immediately before publishing the ref.
            _check_base(repo, source_base)
            if _preimages(repo, source_base, reviewed) != [
                    {key: value for key, value in item.items() if key not in {"integratedBlob", "matched"}} for item in proof]:
                raise SourceIntegrationError("Source preimages changed while preparing the reviewed commit.")
            existing = repo.ref(ref)
            if existing not in {None, commit}:
                raise SourceIntegrationError("The narrowly authorized integration branch already exists with different content.")
            _write(ref_fd, (commit + "\n").encode())
            repo.assert_attached()
            repo.check_cancelled()
            if before_publish is not None:
                before_publish()
            _check_base(repo, source_base)
            _preimages(repo, source_base, reviewed)
            repo.check_cancelled()
            # Revalidation can take time; lease/deadline authority must still
            # hold at the publication boundary, including idempotent recovery.
            if before_publish is not None:
                before_publish()
            _check_base(repo, source_base)
            repo.check_cancelled()
            if existing is None:
                # link is an atomic create-if-absent, unlike rename/replace.
                os.link(lock_name, ref.rsplit("/", 1)[1], src_dir_fd=ref_parent,
                        dst_dir_fd=ref_parent, follow_symlinks=False)
                os.fsync(ref_parent)
            # The link is transiently hard-linked to our lock until this context exits.
            receipt = {"version": 1, "status": "integrated", "scope": "local_source_branch",
                       "rootAlias": "root0", "rootIdentity": source_base["rootIdentity"],
                       "sourceBaseSha256": base_hash, "sourceBaseCommit": source_base["head"],
                       "sourceBaseTree": source_base["tree"], "manifestSha256": manifest_hash,
                       "requestSha256": request_hash, "commit": commit, "tree": tree, "ref": ref,
                       "files": proof, "sourceWritesPerformed": True, "workingTreeWritesPerformed": False,
                       "indexWritesPerformed": False, "remotePushPerformed": False}
            locks.close()
            _check_base(repo, source_base)
            return _verify_receipt(repo, source_base, reviewed, receipt)


def _verify_receipt(repo, source_base, reviewed, receipt):
    repo.cache.clear()
    repo.cache_bytes = 0
    repo.packs = None
    if (not isinstance(receipt, dict) or receipt.get("version") != 1
            or receipt.get("status") != "integrated" or receipt.get("scope") != "local_source_branch"
            or receipt.get("rootAlias") != "root0" or receipt.get("rootIdentity") != source_base["rootIdentity"]
            or receipt.get("sourceBaseSha256") != source_base_sha256(source_base)
            or receipt.get("sourceBaseCommit") != source_base["head"]
            or receipt.get("sourceBaseTree") != source_base["tree"]
            or receipt.get("manifestSha256") != _digest(reviewed)
            or receipt.get("sourceWritesPerformed") is not True
            or receipt.get("workingTreeWritesPerformed") is not False
            or receipt.get("indexWritesPerformed") is not False
            or receipt.get("remotePushPerformed") is not False):
        raise SourceIntegrationError("Source integration receipt differs from its exact retained base and manifest.")
    if (_identity(os.fstat(repo.root)) != source_base["rootIdentity"]
            or _identity(os.fstat(repo.git)) != source_base["gitIdentity"]
            or hashlib.sha256(repo.path.encode()).hexdigest() != source_base["rootPathSha256"]
            or repo.commit_tree(source_base["head"]) != source_base["tree"]):
        raise SourceIntegrationError("Retained source integration repository or parent commit changed.")
    request_hash = receipt.get("requestSha256")
    if not isinstance(request_hash, str) or not _HEX64.fullmatch(request_hash):
        raise SourceIntegrationError("Source integration request identity is invalid.")
    ref = "refs/heads/eidolon/" + request_hash[:32]
    commit, tree = receipt.get("commit"), receipt.get("tree")
    if (receipt.get("ref") != ref or repo.ref(ref) != commit
            or repo.commit_tree(commit) != tree):
        raise SourceIntegrationError("Integrated branch was removed or changed; retained receipt is no longer current.")
    identity = b"Eidolon reviewed integration <eidolon@localhost> " + str(source_base["preparedAt"]).encode() + b" +0000"
    expected_commit = (b"tree " + tree.encode() + b"\nparent " + source_base["head"].encode()
                       + b"\nauthor " + identity + b"\ncommitter " + identity
                       + b"\n\nEidolon reviewed source integration\n\nRequest-SHA256: " + request_hash.encode()
                       + b"\nSource-Base-SHA256: " + receipt["sourceBaseSha256"].encode()
                       + b"\nReviewed-Manifest-SHA256: " + receipt["manifestSha256"].encode() + b"\n")
    if repo.object(commit) != (b"commit", expected_commit):
        raise SourceIntegrationError("Integrated commit does not preserve the exact pinned parent and review identity.")
    proof = []
    expected_changes = []
    for item in reviewed:
        parts = _parts(item["path"])
        original = repo.entry(source_base["tree"], parts)
        if item["operation"] == "create":
            if original is not None:
                raise SourceIntegrationError("Retained create operation has a source preimage.")
            mode, source_blob = b"100644", None
        else:
            if original is None or original[0] not in {b"100644", b"100755"}:
                raise SourceIntegrationError("Retained preimage is not a regular source file.")
            mode, source_blob = original
            kind, content = repo.object(source_blob)
            if kind != b"blob" or hashlib.sha256(content).hexdigest() != item["baseSha256"]:
                raise SourceIntegrationError("Retained source preimage bytes are corrupt.")
        entry = repo.entry(tree, parts)
        blob = None
        if item["operation"] == "delete":
            if entry is not None:
                raise SourceIntegrationError("Integrated deletion did not match the reviewed manifest.")
        else:
            raw = item["content"].encode("utf-8")
            blob = _object_id(b"blob", raw)
            if entry != (mode, blob) or repo.object(blob) != (b"blob", raw):
                raise SourceIntegrationError("Integrated bytes did not match the reviewed manifest.")
        expected_changes.append((parts, None if blob is None else (mode, blob)))
        proof.append({"path": item["path"], "operation": item["operation"], "baseSha256": item["baseSha256"],
                      "sha256": item["sha256"], "sourceBlob": source_blob, "mode": mode.decode(),
                      "integratedBlob": blob, "matched": True})
    # Recompute the complete result tree without writing objects, to catch any
    # unreviewed changes outside the manifest as well as the reviewed path bytes.
    if _expected_tree(repo, source_base["tree"], expected_changes) != tree or receipt.get("files") != proof:
        raise SourceIntegrationError("Integrated tree or byte proof includes unreviewed changes.")
    if repo.ref(ref) != commit:
        raise SourceIntegrationError("Integrated branch changed while verifying its byte proof.")
    repo.assert_attached()
    return dict(receipt)


def _expected_tree(repo, tree, changes):
    entries = repo.tree(tree) if tree else {}
    grouped = {}
    for parts, value in changes:
        grouped.setdefault(parts[0].encode("utf-8"), []).append((parts[1:], value))
    for name, nested in grouped.items():
        if nested[0][0]:
            original = entries.get(name)
            if original and original[0] != b"40000":
                raise SourceIntegrationError("Reviewed source path traverses a non-directory.")
            child = _expected_tree(repo, original[1] if original else None, nested)
            if child == _object_id(b"tree", b""):
                entries.pop(name, None)
            else:
                entries[name] = b"40000", child
        elif nested[0][1] is None:
            entries.pop(name, None)
        else:
            entries[name] = nested[0][1]
    order = sorted(entries, key=lambda name: name + (b"/" if entries[name][0] == b"40000" else b""))
    raw = b"".join(entries[name][0] + b" " + name + b"\0" + bytes.fromhex(entries[name][1]) for name in order)
    return _object_id(b"tree", raw)


def verify_source_integration(read_roots, source_base, manifest, receipt):
    """Read-only check of the still-current local branch and exact byte proof.

    Subsequent user HEAD/worktree changes do not alter this independent branch's
    provenance. Its exact parent, branch ref, complete result tree and reviewed
    blobs must still exist and match the immutable receipt.
    """
    with _failure_boundary(), _Repository(read_roots) as repo:
        return _verify_receipt(repo, source_base, _manifest(manifest), receipt)
