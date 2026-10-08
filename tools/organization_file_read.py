"""Context-local, alias-addressed text reads for explicit organization grants.

Root descriptors are pinned for the scope lifetime. Neither path resolution nor
reads use a terminal environment, document extractor, or process-global config.
This bounds tool access; it does not sandbox arbitrary Python in the process.
"""

from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import os
import stat
import threading

from agent.file_safety import (
    _BLOCKED_PROJECT_ENV_BASENAMES, _CREDENTIAL_FILE_NAMES,
    _HERMES_PROTECTED_SUBPATHS, _READ_DENIED_DIRS, _hermes_dirs, get_read_block_error,
)
from agent.redact import redact_sensitive_text
from tools.binary_extensions import has_binary_extension, has_opaque_document_extension, is_pdf_path


MAX_FILE_BYTES = 1_048_576
MAX_EDIT_BYTES = 32_768
MAX_READ_LINES = 2000
_MAX_PATH_CHARS = 4096
_MAX_ALIAS_PATH_CHARS = 1024
_MAX_READ_OFFSET = 100_000
_OUTSIDE_PATH = "[outside configured roots]"
# Scope grants are for project text. Excluding these shared credential/internal
# basenames and protected directory names anywhere in the lexical path is
# deliberately stricter than generic reads: mutable profile paths cannot
# reclassify a pinned descriptor. Ordinary project auth/cache directories remain
# usable; credential parent directories are excluded only under profile roots.
_DENIED_LEXICAL_COMPONENTS = frozenset(
    os.path.basename(path).lower()
    for path in (*_BLOCKED_PROJECT_ENV_BASENAMES, *_CREDENTIAL_FILE_NAMES,
                 *(row[0] for row in _READ_DENIED_DIRS), ".hub")
)


class OrganizationFileReadError(ValueError):
    """A safe-to-report configuration or read-policy failure."""


_active_scope: ContextVar["OrganizationFileReadScope | None"] = ContextVar(
    "organization_file_read_scope", default=None)


def _require_posix_support() -> None:
    if (os.name != "posix" or os.open not in os.supports_dir_fd
            or not all(hasattr(os, flag) for flag in
                       ("O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK", "O_CLOEXEC"))):
        raise OrganizationFileReadError(
            "Organization file reads require POSIX no-follow directory-descriptor support.")


def _path_parts(path: str, *, absolute: bool) -> tuple[str, ...]:
    if (not isinstance(path, str) or not path or len(path) > _MAX_PATH_CHARS
            or path.startswith("/") != absolute or "\\" in path
            or any(ord(char) < 32 or ord(char) == 127 for char in path)):
        raise OrganizationFileReadError("Invalid organization file path.")
    parts = tuple(path.split("/")[1:] if absolute else path.split("/"))
    if absolute and path == "/":
        return ()
    if any(part in ("", ".", "..") or part.startswith("~") for part in parts):
        raise OrganizationFileReadError("Traversal, tilde, and ambiguous paths are not permitted.")
    return parts


def _open_root(path: str, profile_prefixes: tuple[tuple[str, ...], ...]) -> int:
    parts = _path_parts(path, absolute=True)
    _check_lexical_path(parts, profile_prefixes)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open("/", flags)
    try:
        for part in parts:
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _profile_credential_prefixes() -> tuple[tuple[str, ...], ...]:
    parents = {os.path.dirname(name) for name in _CREDENTIAL_FILE_NAMES if os.path.dirname(name)}
    parents.update(_HERMES_PROTECTED_SUBPATHS)
    return tuple(tuple(part.lower() for part in (root / parent).parts[1:])
                 for root in _hermes_dirs() for parent in sorted(parents))


def _check_lexical_path(parts: tuple[str, ...], profile_prefixes: tuple[tuple[str, ...], ...]) -> None:
    normalized = tuple(part.lower() for part in parts)
    if (any(part in _DENIED_LEXICAL_COMPONENTS for part in normalized)
            or any(normalized[:len(prefix)] == prefix for prefix in profile_prefixes)):
        raise OrganizationFileReadError("Protected credential or internal paths cannot be read.")


def _check_project_write_path(parts):
    denied = {".git", ".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", ".netrc", ".npmrc",
              ".pypirc", ".pgpass", ".git-credentials", "credentials", "credentials.json",
              "application_default_credentials.json", "id_rsa", "id_ed25519", "id_dsa", "id_ecdsa"}
    normalized = [part.lower() for part in parts]
    env_templates = {".env.example", ".env.sample", ".env.template"}
    if (any(part in denied or part.endswith((".pem", ".key", ".p12", ".pfx"))
            or (part.startswith(".env.") and part not in env_templates) for part in normalized)
            or any(left == ".config" and right in {"gh", "gcloud"}
                   for left, right in zip(normalized, normalized[1:]))):
        raise OrganizationFileReadError("Credential and Git-internal paths cannot be created or integrated.")


def _open_file(root_fd: int, parts: tuple[str, ...]) -> int:
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            child = os.open(part, directory_flags, dir_fd=fd)
            os.close(fd)
            fd = child
        # NONBLOCK prevents a malicious FIFO swap hanging before fstat rejects it.
        return os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                       dir_fd=fd)
    finally:
        os.close(fd)


def _read_text(fd: int, max_bytes: int = MAX_FILE_BYTES) -> tuple[str, int]:
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        raise OrganizationFileReadError("Only regular text files may be read.")
    if info.st_nlink > 1:
        raise OrganizationFileReadError("Hard-linked files cannot be read within organization grants.")
    if info.st_size > max_bytes:
        raise OrganizationFileReadError("File exceeds the bounded read byte limit.")
    chunks: list[bytes] = []
    size = 0
    while size <= max_bytes:
        chunk = os.read(fd, min(65536, max_bytes + 1 - size))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    if size > max_bytes:
        raise OrganizationFileReadError("File exceeds the bounded read byte limit.")
    raw = b"".join(chunks)
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise OrganizationFileReadError("Only valid UTF-8 text files may be read.") from None
    if any(ord(char) < 32 and char not in "\n\r\t\f" for char in content):
        raise OrganizationFileReadError("Binary or control-bearing files cannot be read.")
    return content, size


def _validate_edit_source(content: str) -> str:
    if not isinstance(content, str):
        raise OrganizationFileReadError("Managed source must contain exact UTF-8 text.")
    try:
        size = len(content.encode("utf-8"))
    except UnicodeEncodeError:
        raise OrganizationFileReadError("Managed source must contain exact UTF-8 text.") from None
    if size > MAX_EDIT_BYTES:
        raise OrganizationFileReadError("Managed source exceeds the bounded edit byte limit.")
    if any(ord(char) < 32 and char not in "\n\r\t\f" for char in content):
        raise OrganizationFileReadError("Binary or control-bearing files cannot be edited.")
    if redact_sensitive_text(content, force=True, file_read=True, redact_url_credentials=True) != content:
        raise OrganizationFileReadError("A source requiring secret redaction cannot be edited.")
    return content


def _validate_workspace_source(source: dict) -> str:
    if (not isinstance(source, dict)
            or set(source) not in ({"content", "sourceSha256", "workspaceRevision", "workspaceId"},
                                  {"content", "sourceSha256", "workspaceRevision", "workspaceId", "sourceExists"})):
        raise OrganizationFileReadError("Managed source is missing its trusted revision metadata.")
    content = _validate_edit_source(source["content"])
    if (type(source.get("sourceExists", True)) is not bool
            or (source.get("sourceExists") is False and (content or source["workspaceRevision"] != 0))):
        raise OrganizationFileReadError("Managed absence must be an exact empty revision-zero observation.")
    if (source["sourceSha256"] != hashlib.sha256(content.encode("utf-8")).hexdigest()
            or type(source["workspaceRevision"]) is not int or not 0 <= source["workspaceRevision"] <= 2**63 - 1
            or not isinstance(source["workspaceId"], str) or not 1 <= len(source["workspaceId"]) <= 200):
        raise OrganizationFileReadError("Managed source has invalid revision or content-hash metadata.")
    return content


def _json(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))


class OrganizationFileReadScope:
    """A closed-over grant with safe public aliases and no implicit root grants.

    Use ``organization_file_read_scope`` to bind and close this object. Reads and
    close serialize so a copied context cannot race descriptor reuse on teardown.
    """

    def __init__(self, read_roots: tuple[str, ...], max_result_chars: int, *, resolve_workspace_source=None, allowed_root_aliases=None):
        _require_posix_support()
        if not isinstance(read_roots, tuple) or not read_roots:
            raise OrganizationFileReadError("At least one explicit read root is required.")
        if type(max_result_chars) is not int or max_result_chars < 64:
            raise OrganizationFileReadError("The result character budget must be at least 64.")
        if resolve_workspace_source is not None and not callable(resolve_workspace_source):
            raise OrganizationFileReadError("Managed source resolution requires a trusted callable.")
        aliases = tuple(f"root{index}" for index in range(len(read_roots)))
        if allowed_root_aliases is not None:
            if (not isinstance(allowed_root_aliases, (list, tuple)) or not allowed_root_aliases
                    or any(not isinstance(alias, str) or alias not in aliases for alias in allowed_root_aliases)
                    or len(set(allowed_root_aliases)) != len(allowed_root_aliases)):
                raise OrganizationFileReadError("Read-root alias restriction must select configured roots.")
            aliases = tuple(allowed_root_aliases)
        self.resolve_workspace_source = resolve_workspace_source
        self.max_result_chars = max_result_chars
        self._lock = threading.RLock()
        self._closed = False
        self._roots: dict[str, tuple[str, int]] = {}
        try:
            # Snapshot once alongside the pinned roots; later profile renames or
            # symlink changes cannot turn their internal directories into sources.
            self._profile_prefixes = _profile_credential_prefixes()
            for index, root in enumerate(read_roots):
                alias = f"root{index}"
                if alias in aliases:
                    self._roots[alias] = (root, _open_root(root, self._profile_prefixes))
        except OrganizationFileReadError:
            self.close()
            raise
        except (OSError, ValueError):
            self.close()
            raise OrganizationFileReadError(
                "Configured read roots must be existing absolute directories without symlinks.") from None

    @property
    def root_aliases(self) -> tuple[str, ...]:
        return tuple(self._roots)

    def _target(self, path: str) -> tuple[str, int, tuple[str, ...]]:
        if isinstance(path, str) and len(path) > _MAX_ALIAS_PATH_CHARS:
            raise OrganizationFileReadError("Organization file path exceeds the alias character limit.")
        parts = _path_parts(path, absolute=False)
        if len(parts) < 2 or parts[0] not in self._roots:
            raise OrganizationFileReadError("Path is outside configured read-root aliases.")
        root, fd = self._roots[parts[0]]
        return root, fd, parts[1:]

    def validate_arguments(self, args: dict) -> dict:
        """Validate raw model arguments without echoing rejected input."""
        if not isinstance(args, dict) or set(args) - {"path", "offset", "limit"}:
            raise OrganizationFileReadError("Only path, offset, and limit arguments are permitted.")
        path = args.get("path")
        self._target(path)
        offset, limit = args.get("offset", 1), args.get("limit", MAX_READ_LINES)
        if type(offset) is not int or not 1 <= offset <= _MAX_READ_OFFSET:
            raise OrganizationFileReadError("Offset must be a positive bounded integer.")
        if type(limit) is not int or not 1 <= limit <= MAX_READ_LINES:
            raise OrganizationFileReadError("Limit must be an integer within the bounded line limit.")
        return {"path": path, "offset": offset, "limit": limit}

    def canonical_arguments(self, args: dict) -> dict:
        """Return safe receipt arguments even when model input is invalid."""
        if not isinstance(args, dict) or set(args) - {"path", "offset", "limit"}:
            return {}
        try:
            self._target(args.get("path"))
        except OrganizationFileReadError:
            return {"path": _OUTSIDE_PATH}
        try:
            return self.validate_arguments(args)
        except OrganizationFileReadError:
            return {"path": args["path"]}

    def _error(self, message: str, *, blocked: bool) -> str:
        result = {"success": False, "blocked": blocked, "error": message}
        encoded = _json(result)
        if len(encoded) > self.max_result_chars:
            result["error"] = "Read denied." if blocked else "Read failed."
            encoded = _json(result)
        return encoded

    def _result(self, content: str, size: int, offset: int, limit: int, *, source: dict | None = None) -> str:
        # Redact before pagination/truncation so tokens split by either boundary
        # cannot expose an otherwise unrecognizable credential fragment.
        content = redact_sensitive_text(content, force=True, file_read=True,
                                        redact_url_credentials=True)
        managed = source is not None
        lines = content.splitlines(keepends=managed)
        selected = lines[offset - 1:offset - 1 + limit]
        result = {"success": True, "content": "", "total_lines": len(lines),
                  "file_size": size, "truncated": offset - 1 + len(selected) < len(lines)}
        if managed:
            result.update({key: source[key] for key in ("sourceSha256", "workspaceRevision", "workspaceId")})
            result["contentFormat"] = "raw"
            if "sourceExists" in source:
                result["sourceExists"] = source["sourceExists"]
        separator = "" if managed else "\n"
        kept: list[str] = []
        for number, line in enumerate(selected, start=offset):
            displayed = line if managed else f"{number}|{line}"
            candidate = separator.join((*kept, displayed))
            trial = dict(result, content=candidate)
            if number < len(lines):
                trial["next_offset"] = number + 1
            if len(_json(trial)) > self.max_result_chars:
                result.update(truncated=True, truncated_by="characters")
                # Never drop an unretrievable part of a long line silently.
                result["hint"] = "A line exceeds the result budget." if not kept else "Continue at next_offset."
                break
            kept.append(displayed)
        result["content"] = separator.join(kept)
        if result["truncated"] and kept:
            result["next_offset"] = offset + len(kept)
        # Metadata for character truncation also consumes the same JSON budget.
        while kept and len(_json(result)) > self.max_result_chars:
            kept.pop()
            result["content"] = separator.join(kept)
            result["next_offset"] = offset + len(kept)
        if selected and not kept:
            return self._error("A text line exceeds the bounded result character limit.", blocked=True)
        encoded = _json(result)
        if len(encoded) > self.max_result_chars:
            return self._error("Result metadata exceeds the character limit.", blocked=True)
        return encoded

    def _directory_target(self, path):
        parts = _path_parts(path, absolute=False)
        if len(path) > _MAX_ALIAS_PATH_CHARS or parts[0] not in self._roots:
            raise OrganizationFileReadError("Directory is outside configured read-root aliases.")
        root, fd = self._roots[parts[0]]
        _check_lexical_path(_path_parts(root, absolute=True) + parts[1:], self._profile_prefixes)
        return root, fd, parts[1:]

    def validate_discovery_arguments(self, args, *, search=False):
        fields = {"path", "limit", "query"} if search else {"path", "limit"}
        if not isinstance(args, dict) or set(args) - fields:
            raise OrganizationFileReadError("Unsupported discovery arguments.")
        self._directory_target(args.get("path"))
        limit = args.get("limit", 20 if search else 100)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise OrganizationFileReadError("Discovery limit must be between 1 and 100.")
        result = {"path": args["path"], "limit": limit}
        if search:
            query = args.get("query")
            if (not isinstance(query, str) or not query or len(query) > 256
                    or any(ord(char) < 32 for char in query)
                    or redact_sensitive_text(query, force=True, file_read=True, redact_url_credentials=True) != query):
                raise OrganizationFileReadError("Search requires bounded non-sensitive literal text.")
            result["query"] = query
        return result

    def canonical_discovery_arguments(self, args, *, search=False):
        try:
            return self.validate_discovery_arguments(args, search=search)
        except (OrganizationFileReadError, TypeError):
            return {}

    def discover_files(self, args, *, search=False):
        """Bounded no-follow traversal within pinned grants; no implicit read grant."""
        with self._lock:
            try:
                if self._closed:
                    raise OrganizationFileReadError("Organization file read scope has closed.")
                args = self.validate_discovery_arguments(args, search=search)
                root, root_fd, initial = self._directory_target(args["path"])
                alias = args["path"].split('/')[0]
                stack, visited, scanned_bytes, reserved_bytes = [initial], 0, 0, 0
                result = {"success": True, "operation": "search_files" if search else "list_files",
                          "matches" if search else "files": [], "truncated": False}
                rows = result["matches" if search else "files"]
                flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
                while stack and visited < 500 and len(rows) < args["limit"]:
                    parts = stack.pop()
                    directory = os.dup(root_fd)
                    try:
                        for part in parts:
                            child = os.open(part, flags, dir_fd=directory)
                            os.close(directory)
                            directory = child
                        with os.scandir(directory) as entries:
                            for entry in entries:
                                visited += 1
                                if visited > 500:
                                    result["truncated"] = True
                                    break
                                relative = (*parts, entry.name)
                                path = '/'.join((alias, *relative))
                                absolute = os.path.join(root, *relative)
                                try:
                                    _path_parts(path, absolute=False)
                                    if (len(path) > _MAX_ALIAS_PATH_CHARS
                                            or redact_sensitive_text(path, force=True, file_read=True, redact_url_credentials=True) != path):
                                        continue
                                    path.encode('utf-8')
                                    _check_lexical_path(_path_parts(absolute, absolute=True), self._profile_prefixes)
                                    if get_read_block_error(absolute):
                                        continue
                                    info = entry.stat(follow_symlinks=False)
                                    if stat.S_ISDIR(info.st_mode):
                                        if not search:
                                            row = {"path": path, "kind": "directory"}
                                            if len(_json({**result, "files": [*rows, row]})) > self.max_result_chars - 100:
                                                result["truncated"] = True
                                                return _json(result)
                                            rows.append(row)
                                            if len(rows) >= args["limit"]:
                                                result["truncated"] = True
                                                return _json(result)
                                        if len(relative) < 8:
                                            stack.append(relative)
                                        else:
                                            result["truncated"] = True
                                        continue
                                    if (not stat.S_ISREG(info.st_mode) or info.st_nlink > 1
                                            or has_binary_extension(path) or has_opaque_document_extension(path) or is_pdf_path(path)):
                                        continue
                                    if search:
                                        if reserved_bytes + max(1, info.st_size) > MAX_FILE_BYTES:
                                            result["truncated"] = True
                                            continue
                                        # Charge attempted reads as well as successful text. A
                                        # growing or invalid file cannot reset the scan budget.
                                        budget = max(1, info.st_size)
                                        reserved_bytes += budget
                                        fd = _open_file(root_fd, relative)
                                        try:
                                            content, size = _read_text(fd, budget)
                                        finally:
                                            os.close(fd)
                                        scanned_bytes += size
                                        content = redact_sensitive_text(content, force=True, file_read=True, redact_url_credentials=True)
                                        for number, line in enumerate(content.splitlines(), 1):
                                            if args["query"] in line:
                                                position = line.find(args["query"])
                                                start = max(0, position - 120)
                                                row = {"path": path, "line": number, "column": position + 1,
                                                       "text": line[start:start + 1000],
                                                       "truncated": start > 0 or len(line) > start + 1000}
                                                if len(_json({**result, "matches": [*rows, row]})) > self.max_result_chars - 100:
                                                    result["truncated"] = True
                                                    return _json(result)
                                                rows.append(row)
                                                if len(rows) >= args["limit"]:
                                                    result["truncated"] = True
                                                    return _json(result)
                                    else:
                                        row = {"path": path, "kind": "file", "size": info.st_size}
                                        if len(_json({**result, "files": [*rows, row]})) > self.max_result_chars - 100:
                                            result["truncated"] = True
                                            return _json(result)
                                        rows.append(row)
                                        if len(rows) >= args["limit"]:
                                            result["truncated"] = True
                                            return _json(result)
                                except (OrganizationFileReadError, OSError, UnicodeError):
                                    continue
                    except OSError:
                        if parts == initial:
                            raise OrganizationFileReadError("Discovery directory is unavailable or contains a symlink.") from None
                    finally:
                        os.close(directory)
                result["truncated"] = result["truncated"] or bool(stack) or visited >= 500
                result["scannedEntries"] = min(visited, 500)
                if search:
                    result["scannedBytes"] = scanned_bytes
                return _json(result)
            except OrganizationFileReadError as error:
                return self._error(str(error), blocked=True)

    def read_exact_source(self, path: str, *, allow_missing=False) -> dict:
        """Trusted backend observation of bounded source bytes, never model output.

        This always reads the original granted descriptor, bypassing the managed
        workspace resolver. Used only to verify an owner-performed source merge.
        """
        with self._lock:
            if self._closed:
                raise OrganizationFileReadError("Organization file read scope has closed.")
            root, root_fd, parts = self._target(path)
            absolute = os.path.join(root, *parts)
            _check_lexical_path(_path_parts(absolute, absolute=True), self._profile_prefixes)
            if get_read_block_error(absolute):
                raise OrganizationFileReadError("Protected credential or internal files cannot be read.")
            if has_binary_extension(path) or has_opaque_document_extension(path) or is_pdf_path(path):
                raise OrganizationFileReadError("Only plain-text files may be read.")
            try:
                fd = _open_file(root_fd, parts)
                try:
                    content, size = _read_text(fd, MAX_EDIT_BYTES)
                finally:
                    os.close(fd)
            except FileNotFoundError:
                if allow_missing:
                    _check_project_write_path(_path_parts(absolute, absolute=True))
                    return {"content": "", "size": 0, "sha256": hashlib.sha256(b"").hexdigest(), "exists": False}
                raise OrganizationFileReadError("Source is unavailable or contains a symlink or non-directory.") from None
            except OSError:
                raise OrganizationFileReadError("Source is unavailable or contains a symlink or non-directory.") from None
            _validate_edit_source(content)
            return {"content": content, "size": size, "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()}

    def read_file(self, path: str, offset: int = 1, limit: int = MAX_READ_LINES) -> str:
        with self._lock:
            try:
                if self._closed:
                    raise OrganizationFileReadError("Organization file read scope has closed.")
                args = self.validate_arguments({"path": path, "offset": offset, "limit": limit})
                root, root_fd, parts = self._target(args["path"])
                absolute = os.path.join(root, *parts)
                _check_lexical_path(_path_parts(absolute, absolute=True), self._profile_prefixes)
                if get_read_block_error(absolute):
                    raise OrganizationFileReadError("Protected credential or internal files cannot be read.")
                if (has_binary_extension(path) or has_opaque_document_extension(path) or is_pdf_path(path)):
                    raise OrganizationFileReadError("Only plain-text files may be read; document extraction is unavailable.")
                def load_source():
                    # The callback may lazily capture this exact source, but it
                    # cannot bypass the scoped descriptor reader or its bounds.
                    with self._lock:
                        if self._closed:
                            raise OrganizationFileReadError("Organization file read scope has closed.")
                        try:
                            fd = _open_file(root_fd, parts)
                        except FileNotFoundError:
                            if self.resolve_workspace_source is not None:
                                _check_project_write_path(_path_parts(absolute, absolute=True))
                                return None
                            raise
                        try:
                            content, _ = _read_text(fd, MAX_EDIT_BYTES if self.resolve_workspace_source else MAX_FILE_BYTES)
                        finally:
                            os.close(fd)
                        if self.resolve_workspace_source:
                            _validate_edit_source(content)
                        return content

                source = None
                if self.resolve_workspace_source is not None:
                    source = self.resolve_workspace_source(path, load_source)
                    content = _validate_workspace_source(source)
                else:
                    content = load_source()
                return self._result(content, len(content.encode("utf-8")), args["offset"], args["limit"], source=source)
            except OrganizationFileReadError as error:
                return self._error(str(error), blocked=True)
            except OSError:
                return self._error("File is unavailable or its path contains a symlink or non-directory.", blocked=True)
            except Exception:
                return self._error("Organization file read failed.", blocked=False)

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                for _, fd in self._roots.values():
                    os.close(fd)


def get_organization_file_read_scope() -> OrganizationFileReadScope | None:
    return _active_scope.get()


@contextmanager
def organization_file_read_scope(read_roots: tuple[str, ...], max_result_chars: int, *, resolve_workspace_source=None, allowed_root_aliases=None):
    scope = OrganizationFileReadScope(read_roots, max_result_chars, resolve_workspace_source=resolve_workspace_source,
                                     allowed_root_aliases=allowed_root_aliases)
    token = _active_scope.set(scope)
    try:
        yield scope
    finally:
        _active_scope.reset(token)
        scope.close()


def organization_list_files(args):
    scope = get_organization_file_read_scope()
    if scope is None:
        raise OrganizationFileReadError("File discovery requires an organization scope.")
    return scope.discover_files(args)


def organization_search_files(args):
    scope = get_organization_file_read_scope()
    if scope is None:
        raise OrganizationFileReadError("File search requires an organization scope.")
    return scope.discover_files(args, search=True)
