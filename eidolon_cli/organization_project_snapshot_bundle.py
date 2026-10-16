"""Bounded, inert source bytes for an explicitly selected project workspace.

This is separate from the small text/edit snapshot contract. Selection is an
owner-reviewed input, not inferred from Git or recursive discovery. Capturing
bytes grants no execution authority and does not claim a filesystem-wide atomic
snapshot: concurrent source changes observed during capture invalidate it.
"""
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
import hashlib
import json
import math
import os
import re
import stat
import sys
import time
import unicodedata

from agent.redact import redact_sensitive_text
from tools.organization_file_read import (
    _check_lexical_path, _check_project_write_path, _open_root, _path_parts,
    _profile_credential_prefixes, _require_posix_support,
)


MAX_FILES = 10_000
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_CAPTURE_SECONDS = 300
_CHUNK_BYTES = 65_536
_EXCLUDED_COMPONENTS = frozenset({
    '.git', '.hg', '.svn', 'node_modules', 'bower_components', '.pnp.cjs',
    '.pnp.loader.mjs', '.venv', 'venv', '__pycache__', '.pytest_cache',
    '.mypy_cache', '.ruff_cache', '.cache', '.parcel-cache', '.turbo',
    'build', 'dist', 'out', 'target', 'release', 'coverage', '.next', '.nuxt',
    '.output', '.svelte-kit', '.vite', 'playwright-report', 'test-results',
    '.envrc', '.secrets', 'secrets', 'secrets.json', 'secrets.yaml',
    'secrets.yml', 'credentials', 'credentials.json', '.yarnrc.yml',
})
_WINDOWS_RESERVED = re.compile(r'(?i)(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?')


class ProjectSnapshotError(ValueError):
    """The complete selected source could not be safely captured."""


@dataclass(frozen=True)
class ProjectSnapshotLimits:
    """Explicit owner policy; ceilings are bounds, not measured runtime capacity."""

    max_files: int
    max_file_bytes: int
    max_total_bytes: int
    max_path_bytes: int = 1024
    max_depth: int = 32

    def __post_init__(self):
        for name, ceiling in (
            ('max_files', MAX_FILES), ('max_file_bytes', MAX_FILE_BYTES),
            ('max_total_bytes', MAX_TOTAL_BYTES), ('max_path_bytes', 1024),
            ('max_depth', 32),
        ):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= ceiling:
                raise ProjectSnapshotError('Project snapshot limits require bounded positive integers')
        if self.max_file_bytes > self.max_total_bytes:
            raise ProjectSnapshotError('Per-file snapshot limit exceeds the total byte limit')


def _excluded(parts):
    _check_project_write_path(parts)
    if any(part.casefold() in _EXCLUDED_COMPONENTS
           or part.casefold() == '.env' or part.casefold().startswith('.env.')
           or re.fullmatch(r'(?:secrets?|credentials)(?:\..+)?', part.casefold())
           or part.casefold().endswith(('.pyc', '.pyo', '.tsbuildinfo'))
           for part in parts):
        raise ProjectSnapshotError('Selected source contains a default-excluded path')


def _relative_parts(path, max_path_bytes=1024, max_depth=32):
    if type(path) is not str:
        raise ProjectSnapshotError('Source selection requires exact relative file paths')
    try:
        encoded = path.encode('utf-8')
    except UnicodeError:
        raise ProjectSnapshotError('Source paths require valid UTF-8') from None
    parts = _path_parts(path, absolute=False)
    if (len(encoded) > max_path_bytes or len(parts) > max_depth
            or unicodedata.normalize('NFC', path) != path
            or any(unicodedata.category(char).startswith('C') for char in path)
            or any(char in path for char in ':*?[]')
            or any(len(part.encode('utf-8')) > 255 or part.endswith((' ', '.'))
                   or _WINDOWS_RESERVED.fullmatch(part) for part in parts)):
        raise ProjectSnapshotError('Source path exceeds its bound or has ambiguous spelling')
    _excluded(parts)
    return parts


def _validate_selection(paths, limits, check=lambda: None):
    if type(paths) not in (list, tuple) or not 1 <= len(paths) <= limits.max_files:
        raise ProjectSnapshotError('Complete source selection exceeds its file-count bound or is empty')
    selected = tuple(paths)
    if len(selected) > limits.max_files:
        raise ProjectSnapshotError('Source selection changed beyond its file-count bound')
    spellings, files, directories = {}, set(), set()
    for path in selected:
        check()
        parts = _relative_parts(path, limits.max_path_bytes, limits.max_depth)
        key = path.casefold()
        parents = []
        for index in range(1, len(parts) + 1):
            prefix = '/'.join(parts[:index])
            folded = prefix.casefold()
            if spellings.setdefault(folded, prefix) != prefix:
                raise ProjectSnapshotError('Source selection has case-fold path aliases')
            if index < len(parts):
                parents.append(folded)
        if key in files or key in directories or any(parent in files for parent in parents):
            raise ProjectSnapshotError('Source selection has duplicate or file/directory prefix collisions')
        files.add(key)
        directories.update(parents)
    return tuple(sorted(selected))


def _validate_root_identity(alias, identity):
    if type(alias) is not str or not re.fullmatch(r'root(?:0|[1-9][0-9]{0,7})', alias):
        raise ProjectSnapshotError('Snapshot requires an exact configured root alias')
    if (type(identity) is not tuple or len(identity) != 2
            or any(type(value) is not int or not 0 <= value <= 2**64 - 1 for value in identity)):
        raise ProjectSnapshotError('Snapshot requires the caller-approved device/inode identity')


def _execution_only_policy(value, paths, check):
    if value is None:
        return {}
    if type(value) is not dict or len(value) > len(paths):
        raise ProjectSnapshotError('Reviewed execution-only hashes require a bounded exact path mapping')
    approved, selected = dict(value), set(paths)
    for path, digest in approved.items():
        check()
        _relative_parts(path)
        if path not in selected:
            raise ProjectSnapshotError('Reviewed execution-only hashes must name selected source files')
        if type(digest) is not str or not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise ProjectSnapshotError('Reviewed execution-only source requires an exact lowercase SHA-256')
    return approved


@dataclass(frozen=True)
class ProjectSnapshotFile:
    path: str
    content: bytes = field(repr=False)
    mode: int = 0o444
    execution_only: bool = False
    sha256: str = field(init=False)

    def __post_init__(self):
        _relative_parts(self.path)
        if type(self.content) is not bytes or len(self.content) > MAX_FILE_BYTES:
            raise ProjectSnapshotError('Snapshot files require bounded immutable bytes')
        if type(self.mode) is not int or self.mode not in (0o444, 0o555):
            raise ProjectSnapshotError('Snapshot file mode must be read-only and normalized')
        if type(self.execution_only) is not bool:
            raise ProjectSnapshotError('Snapshot execution-only marker must be an exact boolean')
        object.__setattr__(self, 'sha256', hashlib.sha256(self.content).hexdigest())

    def manifest(self):
        return {'path': self.path, 'bytes': len(self.content), 'mode': self.mode,
                'sha256': self.sha256, 'executionOnly': self.execution_only}


@dataclass(frozen=True)
class ProjectSnapshotBundle:
    root_alias: str
    root_identity: tuple[int, int]
    files: tuple[ProjectSnapshotFile, ...]
    limits: ProjectSnapshotLimits
    snapshot_sha256: str = field(init=False)
    execution_authorized: bool = field(default=False, init=False)

    def __post_init__(self):
        _validate_root_identity(self.root_alias, self.root_identity)
        if (type(self.limits) is not ProjectSnapshotLimits or type(self.files) is not tuple
                or any(type(item) is not ProjectSnapshotFile for item in self.files)):
            raise ProjectSnapshotError('Snapshot bundle requires immutable exact file records and limits')
        paths = _validate_selection(tuple(item.path for item in self.files), self.limits)
        if paths != tuple(item.path for item in self.files):
            raise ProjectSnapshotError('Snapshot file records must have canonical path order')
        if (any(len(item.content) > self.limits.max_file_bytes for item in self.files)
                or self.byte_count > self.limits.max_total_bytes):
            raise ProjectSnapshotError('Complete snapshot exceeds its byte bounds')
        encoded = json.dumps(self.manifest(), sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')
        object.__setattr__(self, 'snapshot_sha256', hashlib.sha256(encoded).hexdigest())

    @property
    def byte_count(self):
        return sum(len(item.content) for item in self.files)

    def manifest(self):
        """Fresh metadata only; binary bytes never become model prompt content."""
        return {'version': 1, 'rootAlias': self.root_alias,
                'rootIdentity': {'device': self.root_identity[0], 'inode': self.root_identity[1]},
                'files': [item.manifest() for item in self.files], 'totalBytes': self.byte_count}


def _fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _record_directory(fd, prefix, observed):
    current = _fingerprint(os.fstat(fd))
    if observed.setdefault(prefix, current) != current:
        raise ProjectSnapshotError('Source directory identity or entries changed during capture')


@contextmanager
def _selected_file(root_fd, parts, directories, check):
    """Pin and check each traversed parent while the leaf descriptor is read."""
    with ExitStack() as opened:
        parent, edges = root_fd, []
        _record_directory(parent, '', directories)
        for index, part in enumerate(parts[:-1], 1):
            check()
            before = os.stat(part, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISDIR(before.st_mode):
                raise ProjectSnapshotError('Selected source parents must be real directories')
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
            opened.callback(os.close, child)
            if _fingerprint(os.fstat(child)) != _fingerprint(before):
                raise ProjectSnapshotError('Source directory changed while opening')
            _record_directory(child, '/'.join(parts[:index]), directories)
            edges.append((parent, part, child, _fingerprint(before)))
            parent = child
        before = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX)):
            raise ProjectSnapshotError('Selected source must be regular single-link files without special modes')
        identity = _fingerprint(before)
        # O_PATH pins a name without invoking a device driver's open operation.
        # A raced-in special file must be rejected before any read-open occurs.
        pinned = os.open(parts[-1], os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        opened.callback(os.close, pinned)
        if _fingerprint(os.fstat(pinned)) != identity:
            raise ProjectSnapshotError('Source file changed while opening')
        fd = os.open(f'/proc/self/fd/{pinned}', os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        opened.callback(os.close, fd)
        if _fingerprint(os.fstat(fd)) != identity:
            raise ProjectSnapshotError('Pinned source file identity changed before reading')
        yield fd, before
        check()
        if (_fingerprint(os.fstat(fd)) != identity
                or _fingerprint(os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)) != identity):
            raise ProjectSnapshotError('Source file identity or bytes changed during capture')
        for parent, name, child, expected in reversed(edges):
            if (_fingerprint(os.fstat(child)) != expected
                    or _fingerprint(os.stat(name, dir_fd=parent, follow_symlinks=False)) != expected):
                raise ProjectSnapshotError('Source parent changed during capture')
        _record_directory(root_fd, '', directories)


def _read_bounded(fd, expected_size, check, *, retain):
    total, chunks, digest = 0, [], hashlib.sha256()
    while True:
        check()
        block = os.read(fd, min(_CHUNK_BYTES, expected_size - total + 1))
        if not block:
            break
        total += len(block)
        if total > expected_size:
            raise ProjectSnapshotError('Selected source grew beyond its exact byte bound')
        digest.update(block)
        if retain:
            chunks.append(block)
    if total != expected_size:
        raise ProjectSnapshotError('Selected source changed size during capture')
    return b''.join(chunks) if retain else None, digest.hexdigest()


def _check_no_secrets(content):
    # The forced existing credential detector is an additional refusal, never a
    # redaction or a claim that arbitrary binary data is provably secret-free.
    text = content.decode('utf-8', errors='replace')
    if redact_sensitive_text(text, force=True, file_read=True, redact_url_credentials=True) != text:
        raise ProjectSnapshotError('Selected source contains recognized secret-bearing content')


def capture_project_snapshot(source, files, *, root_alias, expected_root_identity,
                             check_authorized, limits, deadline_monotonic, cancel=None,
                             reviewed_execution_only_hashes=None):
    """Capture every selected file or fail, retaining exact immutable binary bytes.

    ``check_authorized`` is a trusted caller callback returning exactly True only
    while this root/selection grant and reviewed execution-only hash policy are
    current. It must include revocation checks and return promptly. Callers must
    recheck authority before later dispatch or acceptance. The optional hash
    policy only permits exact reviewed bytes through content-redaction refusal;
    protected paths remain denied. Every listed file is identity-bound as
    execution-only, even if its bytes would otherwise pass the content check.
    This module exposes no model-context content API. ``deadline_monotonic`` is
    an absolute time.monotonic deadline. Capture requires Linux O_PATH/procfs.
    No runtime, installation, filesystem staging or workspace links are enabled.
    """
    _require_posix_support()
    if sys.platform != 'linux' or not hasattr(os, 'O_PATH'):
        raise ProjectSnapshotError('Project snapshot capture requires Linux O_PATH and proc descriptor reopening')
    _validate_root_identity(root_alias, expected_root_identity)
    if type(limits) is not ProjectSnapshotLimits or not callable(check_authorized):
        raise ProjectSnapshotError('Snapshot capture requires explicit limits and a current-grant checker')
    now = time.monotonic()
    if (type(deadline_monotonic) not in (int, float)
            or not now < deadline_monotonic <= now + MAX_CAPTURE_SECONDS
            or not math.isfinite(deadline_monotonic)):
        raise ProjectSnapshotError('Snapshot deadline must be current, finite and within the capture time bound')
    if cancel is not None and not callable(getattr(cancel, 'is_set', None)):
        raise ProjectSnapshotError('Snapshot cancellation requires a cancellation event')

    def check():
        if cancel is not None and cancel.is_set():
            raise ProjectSnapshotError('Project snapshot capture cancelled')
        if time.monotonic() >= deadline_monotonic:
            raise ProjectSnapshotError('Project snapshot capture deadline exceeded')
        if check_authorized() is not True:
            raise ProjectSnapshotError('Project snapshot root or selection grant was revoked')
        if cancel is not None and cancel.is_set():
            raise ProjectSnapshotError('Project snapshot capture cancelled')
        if time.monotonic() >= deadline_monotonic:
            raise ProjectSnapshotError('Project snapshot capture deadline exceeded')

    check()
    paths = _validate_selection(files, limits, check)
    execution_only = _execution_only_policy(reviewed_execution_only_hashes, paths, check)
    source = os.fspath(source)
    parts = _path_parts(source, absolute=True)
    if not parts or len(parts) > 64:
        raise ProjectSnapshotError('Snapshot source must be a bounded explicit project directory')
    _excluded(parts)
    profile_prefixes = _profile_credential_prefixes()
    for path in paths:
        check()
        _check_lexical_path(parts + tuple(path.split('/')), profile_prefixes)

    captured, identities, directories, total = [], {}, {}, 0
    try:
        with ExitStack() as opened:
            root_fd = _open_root(source, profile_prefixes)
            opened.callback(os.close, root_fd)

            def check_root():
                check()
                current_fd = _open_root(source, profile_prefixes)
                try:
                    current = os.fstat(current_fd)
                    if (current.st_dev, current.st_ino) != expected_root_identity:
                        raise ProjectSnapshotError('Approved source root identity changed')
                    _record_directory(current_fd, '', directories)
                    _record_directory(root_fd, '', directories)
                finally:
                    os.close(current_fd)
                check()

            check_root()
            for path in paths:
                check_root()
                with _selected_file(root_fd, path.split('/'), directories, check) as (fd, before):
                    if before.st_size > limits.max_file_bytes or total + before.st_size > limits.max_total_bytes:
                        raise ProjectSnapshotError('Complete selected source exceeds its byte bounds')
                    content, digest = _read_bounded(fd, before.st_size, check, retain=True)
                    if path in execution_only:
                        if digest != execution_only[path]:
                            raise ProjectSnapshotError('Reviewed execution-only source hash changed')
                    else:
                        _check_no_secrets(content)
                    check()
                    item = ProjectSnapshotFile(path, content, 0o555 if before.st_mode & 0o111 else 0o444,
                                               execution_only=path in execution_only)
                    if item.sha256 != digest:
                        raise ProjectSnapshotError('Captured bytes do not match their descriptor digest')
                    captured.append(item)
                    identities[path] = _fingerprint(before)
                    total += len(content)
            # Reopen the selected names to detect replacements or earlier files
            # mutated while a later file was copied. Retain no second byte tree.
            for item in captured:
                check_root()
                with _selected_file(root_fd, item.path.split('/'), directories, check) as (fd, before):
                    if _fingerprint(before) != identities[item.path]:
                        raise ProjectSnapshotError('Selected source changed before snapshot verification')
                    _, digest = _read_bounded(fd, len(item.content), check, retain=False)
                    if digest != item.sha256:
                        raise ProjectSnapshotError('Selected source bytes changed before snapshot verification')
            result = ProjectSnapshotBundle(root_alias, expected_root_identity, tuple(captured), limits)
            check_root()
            return result
    except OSError:
        raise ProjectSnapshotError('Selected source is missing, inaccessible or contains an unsafe filesystem entry') from None
