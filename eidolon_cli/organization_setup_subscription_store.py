"""Dedicated, fail-closed setup grant storage. No credential catalog or fallback.

The caller must obtain a trusted binding through the separately reviewed owner /
verified-identity gate. This module does not establish authorization. Secrets are
opaque backend-only bytes; callbacks must not retain them or send them to a UI.
Python cannot guarantee erasure of temporary copies. A same-user hostile native
process is outside the OS vault boundary.

Darwin uses owner-only local files and the native keychain. Linux is unavailable.
Profile roots must already be owner-only (0700); never silently chmod an existing
profile. Any permission remedy requires a separate explicit user action.
Windows deliberately fails closed pending an independently verified ACL and
crash-durable directory publication helper. Injected backends are for synthetic
proof, never a product option or a plaintext fallback.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from functools import wraps
import ctypes
import errno
import json
import inspect
import math
import os
from pathlib import Path
import platform
import stat
import sys
import threading
import time
import weakref
import uuid

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from eidolon_cli.organization_credential_native import binding_reference, native_backend
from eidolon_cli.organization_credential_security import ValueBinding

PRIVATE_STORE_NAME = '.setup-subscription-private'
ENVELOPE_VERSION = 1
MAX_ENVELOPE_BYTES = 64 * 1024
MAX_SECRET_BYTES = MAX_ENVELOPE_BYTES - 12 - 16
MAX_METADATA_BYTES = 16 * 1024
_THREAD_USE = threading.local()
_LEASES_LOCK = threading.Lock()
_LEASES = {}
_GRANT_PREFIX = b'EIDOLON_SETUP_GRANT_V1\n'
_GRANT_ISSUER = 'https://auth.openai.com'
_REQUIRED_SCOPES = frozenset({'resource.invoke', 'chatgpt.tokens.use.direct'})
_ERROR_REASONS = frozenset({
    'authentication_failed', 'backend_unavailable', 'connection_unavailable',
    'corrupt_store', 'disconnect_required', 'filesystem_protection_unavailable',
    'invalid_binding', 'invalid_secret', 'native_reference_mismatch',
    'operation_failed', 'publication_failed', 'publication_uncertain', 'recovery_required', 'recursive_use',
    'revision_exhausted', 'revision_exists', 'scope_denied', 'stale_revision',
    'unsafe_filesystem', 'value_too_large', 'invalid_grant', 'grant_expired',
    'typed_grant_requires_handoff', 'auth_attempt_mismatch', 'lease_used',
    'lease_cancelled', 'owner_generation_changed', 'scope_not_authorized', 'invalid_lease_result',
})


class StoreError(RuntimeError):
    """Only fixed reason codes cross the secret boundary."""
    def __init__(self, reason):
        self.reason = reason if type(reason) is str and reason in _ERROR_REASONS else 'operation_failed'
        super().__init__('Setup subscription: ' + self.reason)


@dataclass(frozen=True, repr=False)
class TrustedBinding:
    host_id: str
    profile_locator: str
    organization_id: str
    owner_id: str
    registration_id: str
    issued_client_id: str
    account_id: str

    def __post_init__(self):
        for value in asdict(self).values():
            if not isinstance(value, str) or not value or len(value.encode('utf-8')) > 2048 or any(ord(c) < 32 for c in value):
                raise StoreError('invalid_binding')
        for value in (self.organization_id, self.owner_id):
            try:
                if str(uuid.UUID(value)) != value:
                    raise ValueError
            except ValueError:
                raise StoreError('invalid_binding') from None


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise StoreError('corrupt_store')
        result[key] = value
    return result


class _DarwinTimespec(ctypes.Structure):
    _fields_ = [('tv_sec', ctypes.c_int64), ('tv_nsec', ctypes.c_int64)]


class _DarwinStat64(ctypes.Structure):
    # Apple xnu bsd/sys/stat.h __DARWIN_STRUCT_STAT64, 64-bit macOS only.
    _fields_ = [
        ('st_dev', ctypes.c_int32), ('st_mode', ctypes.c_uint16),
        ('st_nlink', ctypes.c_uint16), ('st_ino', ctypes.c_uint64),
        ('st_uid', ctypes.c_uint32), ('st_gid', ctypes.c_uint32),
        ('st_rdev', ctypes.c_int32), ('st_atimespec', _DarwinTimespec),
        ('st_mtimespec', _DarwinTimespec), ('st_ctimespec', _DarwinTimespec),
        ('st_birthtimespec', _DarwinTimespec), ('st_size', ctypes.c_int64),
        ('st_blocks', ctypes.c_int64), ('st_blksize', ctypes.c_int32),
        ('st_flags', ctypes.c_uint32), ('st_gen', ctypes.c_uint32),
        ('st_lspare', ctypes.c_int32), ('st_qspare', ctypes.c_int64 * 2),
    ]

    @property
    def st_ctime_ns(self):
        return self.st_ctimespec.tv_sec * 1_000_000_000 + self.st_ctimespec.tv_nsec


def _file_identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_ctime_ns)


@contextmanager
def _checked_acl_descriptor(path):
    before = Path(path).lstat()
    if not (stat.S_ISREG(before.st_mode) or stat.S_ISDIR(before.st_mode)):
        raise StoreError('unsafe_filesystem')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        if _file_identity(os.fstat(fd)) != _file_identity(before):
            raise StoreError('unsafe_filesystem')
        yield fd, before
        if (_file_identity(os.fstat(fd)) != _file_identity(before)
                or _file_identity(Path(path).lstat()) != _file_identity(before)):
            raise StoreError('unsafe_filesystem')
    finally:
        os.close(fd)


def _check_darwin_acl(path):
    """Read-only, descriptor-bound ACL inspection; never accept arbitrary NULL.

    Apple Libc sys/statx_np.c populates FILESEC_ACL only when an ACL exists;
    gen/filesec.c exposes this through filesec_query_property. Absence is valid
    ONLY after successful fstatx and explicit successful property-presence query.
    xnu bsd/sys/stat.h and cdefs.h define the stat64 ABI and symbol suffixes.
    """
    architecture = platform.machine().lower()
    if (architecture not in {'arm64', 'x86_64'} or ctypes.sizeof(ctypes.c_void_p) != 8
            or ctypes.sizeof(_DarwinStat64) != 144 or ctypes.alignment(_DarwinStat64) != 8):
        raise StoreError('filesystem_protection_unavailable')
    api = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
    symbol = 'fstatx_np$INODE64' if architecture == 'x86_64' else 'fstatx_np'
    try:
        fstatx = getattr(api, symbol)  # Never fall back to an incompatible ABI.
        fstatx.argtypes = [ctypes.c_int, ctypes.POINTER(_DarwinStat64), ctypes.c_void_p]
        fstatx.restype = ctypes.c_int
        api.filesec_init.argtypes = []
        api.filesec_init.restype = ctypes.c_void_p
        api.filesec_free.argtypes = [ctypes.c_void_p]
        api.filesec_free.restype = None
        api.filesec_query_property.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        api.filesec_query_property.restype = ctypes.c_int
        api.filesec_get_property.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
        api.filesec_get_property.restype = ctypes.c_int
        api.acl_valid.argtypes = [ctypes.c_void_p]
        api.acl_valid.restype = ctypes.c_int
        api.acl_get_entry.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
        api.acl_get_entry.restype = ctypes.c_int
        api.acl_get_tag_type.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
        api.acl_get_tag_type.restype = ctypes.c_int
        api.acl_free.argtypes = [ctypes.c_void_p]
        api.acl_free.restype = ctypes.c_int
    except AttributeError:
        raise StoreError('filesystem_protection_unavailable') from None
    with _checked_acl_descriptor(path) as (fd, before):
        fsec = api.filesec_init()
        if not fsec:
            raise StoreError('filesystem_protection_unavailable')
        acl = ctypes.c_void_p()
        try:
            native_stat = _DarwinStat64()
            if fstatx(fd, ctypes.byref(native_stat), fsec) != 0:
                raise StoreError('filesystem_protection_unavailable')
            if _file_identity(native_stat) != _file_identity(before):
                raise StoreError('unsafe_filesystem')
            present = ctypes.c_int(-1)
            if api.filesec_query_property(fsec, 5, ctypes.byref(present)) != 0 or present.value < 0:
                raise StoreError('filesystem_protection_unavailable')
            if present.value == 0:  # FILESEC_ACL explicitly absent in this snapshot.
                return
            if api.filesec_get_property(fsec, 5, ctypes.byref(acl)) != 0 or not acl.value or acl.value == 1:
                raise StoreError('filesystem_protection_unavailable')
            if api.acl_valid(acl) != 0:
                raise StoreError('filesystem_protection_unavailable')
            entry, index = ctypes.c_void_p(), 0  # ACL_FIRST_ENTRY
            for _ in range(129):  # Darwin ACL_MAX_ENTRIES is 128.
                ctypes.set_errno(0)
                result = api.acl_get_entry(acl, index, ctypes.byref(entry))
                if result == -1 and ctypes.get_errno() == errno.EINVAL:
                    return
                if result != 0:
                    raise StoreError('filesystem_protection_unavailable')
                tag = ctypes.c_int()
                if api.acl_get_tag_type(entry, ctypes.byref(tag)) != 0:
                    raise StoreError('filesystem_protection_unavailable')
                if tag.value != 2:  # Only ACL_EXTENDED_DENY is harmless.
                    raise StoreError('unsafe_filesystem')
                index = -1  # ACL_NEXT_ENTRY
            raise StoreError('filesystem_protection_unavailable')
        finally:
            if acl.value and acl.value != 1:
                api.acl_free(acl)
            api.filesec_free(fsec)


def _guarded(method):
    @wraps(method)
    def guarded(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except StoreError as error:
            # Even callback-created StoreError instances/modified args are untrusted.
            reason = error.reason if type(error) is StoreError else 'operation_failed'
            raise StoreError(reason) from None
        except Exception:
            # Native backend, crypto and caller exceptions can contain secrets.
            raise StoreError('operation_failed') from None
    return guarded


def _is_grant(secret):
    return isinstance(secret, bytes) and secret.startswith(_GRANT_PREFIX)


def _grant_attempt(attempt, prior):
    if (not isinstance(attempt, str) or not 1 <= len(attempt) <= 128
            or any(not (33 <= ord(char) <= 126) for char in attempt)
            or type(prior) is not int or not 0 <= prior < 2**63 - 1):
        raise StoreError('invalid_grant')


def _grant_scopes(scopes):
    if not isinstance(scopes, (tuple, list, frozenset)) or not 0 <= len(scopes) <= 64:
        raise StoreError('invalid_grant')
    if any(not isinstance(scope, str) or not 1 <= len(scope) <= 128
           or any(not 33 <= ord(char) <= 126 for char in scope) for scope in scopes):
        raise StoreError('invalid_grant')
    if len(set(scopes)) != len(scopes):
        raise StoreError('invalid_grant')
    return tuple(sorted(scopes))


def _parse_grant(secret, binding):
    if not _is_grant(secret) or len(secret) > MAX_SECRET_BYTES:
        raise StoreError('invalid_grant')
    payload = json.loads(secret[len(_GRANT_PREFIX):], object_pairs_hook=_pairs)
    if (not isinstance(payload, dict) or set(payload) != {
            'schemaVersion', 'issuer', 'authAttemptId', 'priorStoreRevision', 'verifiedBinding',
            'grantedScopes', 'expiresAt', 'accessToken', 'refreshToken', 'idToken'}
            or type(payload['schemaVersion']) is not int or payload['schemaVersion'] != 1
            or payload['issuer'] != _GRANT_ISSUER
            or payload['verifiedBinding'] != asdict(binding)):
        raise StoreError('invalid_grant')
    _grant_attempt(payload['authAttemptId'], payload['priorStoreRevision'])
    if (not isinstance(payload['grantedScopes'], list)
            or tuple(payload['grantedScopes']) != _grant_scopes(payload['grantedScopes'])):
        raise StoreError('invalid_grant')
    expiry = payload['expiresAt']
    if type(expiry) not in {int, float} or not math.isfinite(expiry) or not 0 < expiry < 253402300800:
        raise StoreError('invalid_grant')
    for key in ('accessToken', 'refreshToken', 'idToken'):
        token = payload[key]
        if (not isinstance(token, str) or not 1 <= len(token) <= 16384
                or any(not 33 <= ord(char) <= 126 for char in token)):
            raise StoreError('invalid_grant')
    return payload


class TokenLease:
    """Server-private, access-token-only, one-admission capability.

    Created only by lease_access_token after authenticated decryption. ``run``
    deliberately transfers a short-lived access-token copy to a trusted request
    callback OUTSIDE the store lock. This is the explicit exception to the old
    opaque consume callback's no-retention contract. Callback must neither retain
    the token after return nor include it in results/logs/UI. ID/refresh tokens
    are never stored on the lease. Python cannot erase temporary immutable copies.

    Admission is single-use, including failed/cancelled admissions. Local
    disconnect invalidates all in-process leases promptly; durable checks also
    detect another process's retirement. A request admitted before cancellation
    can already be remote. Cancellation does not prove upstream work stopped;
    late replies are fenced and rejected, never returned as current results.
    """
    def __init__(self, store, binding, revision, generation, guard, cancel, expiry, access_token, scope):
        self._store, self._binding, self._revision = store, binding, revision
        self._generation, self._guard, self._cancel = generation, guard, cancel
        self._expiry, self._scope = expiry, scope
        self._token = bytearray(access_token, 'ascii')
        self._mutex = threading.Lock()
        self._revoked = threading.Event()
        self._used = False

    def __repr__(self):
        return '<TokenLease server-private>'

    def __reduce__(self):
        raise TypeError('Server-private token lease cannot be serialized')

    def _check_locked(self):
        self._store._fence(self._binding, self._revision)
        if self._guard() is not True:
            raise StoreError('owner_generation_changed')
        if self._revoked.is_set() or self._cancel.is_set():
            raise StoreError('lease_cancelled')
        if self._store._clock() >= self._expiry:
            raise StoreError('grant_expired')

    def is_set(self):
        """Event-like cancellation query, including current durable fencing.

        Request adapters may poll this outside the store lock; there is no
        network operation here. An unreadable fence is cancellation, not consent.
        """
        if self._revoked.is_set():
            return True
        try:
            with self._store._locked() as exists:
                if not exists:
                    raise StoreError('lease_cancelled')
                self._check_locked()
            return False
        except Exception:
            self.close()
            return True

    def close(self):
        self._revoked.set()
        with self._mutex:
            self._token[:] = b'\0' * len(self._token)
            self._token.clear()
        with _LEASES_LOCK:
            leases = _LEASES.get(self._scope)
            if leases is not None:
                leases.discard(self)
                if not leases:
                    _LEASES.pop(self._scope, None)

    @_guarded
    def run(self, request):
        with self._mutex:
            if self._used:
                raise StoreError('lease_used')
            self._used = True
        try:
            with self._store._locked() as exists:
                if not exists:
                    raise StoreError('connection_unavailable')
                self._check_locked()
                with self._mutex:
                    if not self._token:
                        raise StoreError('lease_cancelled')
                    token = self._token.decode('ascii')
            # This explicit handoff runs with NO store lock. Last-moment checks
            # reduce, but cannot eliminate, the post-admission cancellation race.
            if self.is_set():
                raise StoreError('lease_cancelled')
            result = request(token, self)
            if inspect.isawaitable(result) or inspect.isgenerator(result) or inspect.isasyncgen(result):
                # A coroutine/generator would defer work past token ownership and
                # late-reply fences. Adapters must return completed response data.
                if inspect.iscoroutine(result) or inspect.isgenerator(result):
                    result.close()
                raise StoreError('invalid_lease_result')
            with self._store._locked() as exists:
                if not exists:
                    raise StoreError('connection_unavailable')
                self._check_locked()
            return result
        finally:
            self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


class SubscriptionStore:
    """One organization/owner per profile; no cross-organization grant sharing.

    All operations serialize across processes. ``consume`` and ``refresh`` run
    their callback under the same lock as disconnect/publication. Disconnect
    waits for an in-flight callback; this is not cancellation of provider work.
    Inference senders need a separately reviewed short handoff/fencing boundary,
    never an entire streaming request inside ``consume``. Revision is
    fenced before and after decrypt and callback. Callbacks must be bounded and
    must not recursively use this store. Refresh is invoked once: a durable
    ``refreshing`` marker makes any interruption require explicit disconnect and
    reauthorization. Status never touches the vault, reads an envelope or refreshes.
    """
    def __init__(self, profile_root, *, backend=None, clock=time.time):
        self.profile = Path(os.path.abspath(os.fspath(profile_root)))
        self.directory = self.profile / PRIVATE_STORE_NAME
        self._backend = backend
        self._synthetic = backend is not None
        self._clock = clock

    def __repr__(self):
        return '<SubscriptionStore>'

    def _platform(self):
        if self._synthetic:
            if os.name != 'posix':
                raise StoreError('filesystem_protection_unavailable')
            return
        if sys.platform == 'win32':
            raise StoreError('filesystem_protection_unavailable')
        if sys.platform != 'darwin':
            raise StoreError('backend_unavailable')

    def _vault(self):
        return self._backend if self._synthetic else native_backend(self.directory)

    def _check(self, path, *, directory=False, private=True):
        # Reject symlinks in every ancestor, including the caller's profile path.
        for ancestor in (path, *path.parents):
            info = ancestor.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise StoreError('unsafe_filesystem')
            if ancestor != path:
                if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in {0, os.geteuid()}
                        or (stat.S_IMODE(info.st_mode) & 0o022 and not info.st_mode & stat.S_ISVTX)):
                    raise StoreError('unsafe_filesystem')
            if not self._synthetic:
                _check_darwin_acl(ancestor)
        info = path.lstat()
        if directory != stat.S_ISDIR(info.st_mode) or (not directory and not stat.S_ISREG(info.st_mode)):
            raise StoreError('unsafe_filesystem')
        if private and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077):
            raise StoreError('unsafe_filesystem')
        if not directory and info.st_nlink != 1:
            raise StoreError('unsafe_filesystem')
        return info

    def _sync_directory(self):
        self._check(self.directory, directory=True)
        fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _read(self, name, limit=MAX_METADATA_BYTES):
        path = self.directory / name
        before = self._check(path)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            actual = os.fstat(stream.fileno())
            if (actual.st_dev, actual.st_ino) != (before.st_dev, before.st_ino) or actual.st_size > limit:
                raise StoreError('corrupt_store')
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise StoreError('corrupt_store')
        return data

    def _read_json(self, name):
        return json.loads(self._read(name), object_pairs_hook=_pairs)

    def _write(self, name, data, *, replace=False):
        if len(data) > (MAX_ENVELOPE_BYTES if name.endswith('.enc') else MAX_METADATA_BYTES):
            raise StoreError('value_too_large')
        self._check(self.directory, directory=True)
        destination = self.directory / name
        if destination.exists() or destination.is_symlink():
            self._check(destination)
            if not replace:
                raise StoreError('revision_exists')
        temporary = self.directory / (uuid.uuid4().hex + '.tmp')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
            self._sync_directory()
        finally:
            # No secret plaintext is ever written; stale temporary files are inert.
            if temporary.exists():
                temporary.unlink()

    @contextmanager
    def _locked(self, *, create=False):
        self._platform()
        profile_info = self._check(self.profile, directory=True)
        # Thread-wide, inode-scoped guard covers callbacks opening another store
        # object or a differently cased locator for the same physical profile.
        scope = (profile_info.st_dev, profile_info.st_ino)
        active = getattr(_THREAD_USE, 'active', None)
        if active is None:
            active = _THREAD_USE.active = set()
        if scope in active:
            raise StoreError('recursive_use')
        if not self.directory.exists() and not self.directory.is_symlink():
            if not create:
                yield False
                return
            try:
                self.directory.mkdir(mode=0o700)
            except FileExistsError:
                pass
            parent_fd = os.open(self.profile, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        self._check(self.directory, directory=True)
        lock_path = self.directory / 'lock'
        if not lock_path.exists() and not lock_path.is_symlink():
            if not create:
                raise StoreError('corrupt_store')
            try:
                fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                os.fsync(fd)
                os.close(fd)
                self._sync_directory()
            except FileExistsError:
                pass
        before = self._check(lock_path)
        fd = os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW)
        import fcntl
        try:
            actual = os.fstat(fd)
            if (before.st_dev, before.st_ino) != (actual.st_dev, actual.st_ino):
                raise StoreError('unsafe_filesystem')
            fcntl.flock(fd, fcntl.LOCK_EX)
            active.add(scope)
            self._check(self.profile, directory=True)
            self._check(self.directory, directory=True)
            yield True
        finally:
            active.discard(scope)
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _state(self):
        path = self.directory / 'current.json'
        if not path.exists() and not path.is_symlink():
            # Missing publication is NOT evidence that this profile is new. Even
            # a completed disconnect retains identity.json permanently. Do not
            # guess the latest revision, revive an older envelope, or clean up a
            # potentially active target when publication evidence has been lost.
            if any(item.name != 'lock' for item in self.directory.iterdir()):
                raise StoreError('recovery_required')
            return {'version': ENVELOPE_VERSION, 'revision': 0, 'phase': 'absent', 'binding': None, 'target': None}
        value = self._read_json('current.json')
        if (not isinstance(value, dict) or set(value) != {'version', 'revision', 'phase', 'binding', 'target'}
                or type(value['version']) is not int or value['version'] != ENVELOPE_VERSION
                or type(value['revision']) is not int or not 0 <= value['revision'] < 2**63
                or value['phase'] not in {'active', 'refreshing', 'disconnected'}):
            raise StoreError('corrupt_store')
        TrustedBinding(**value['binding'])
        self._check_scope(value['binding'])
        if value['revision'] == 0 and value['phase'] != 'disconnected':
            raise StoreError('corrupt_store')
        if value['phase'] == 'disconnected':
            if value['target'] is not None:
                raise StoreError('corrupt_store')
        else:
            self._target(value['target'])
        return value

    @staticmethod
    def _scope(binding):
        return {key: binding[key] for key in ('host_id', 'profile_locator', 'organization_id', 'owner_id')}

    def _check_scope(self, binding):
        anchor = self._read_json('identity.json')
        if (not isinstance(anchor, dict) or set(anchor) != {'version', 'scope'}
                or type(anchor['version']) is not int or anchor['version'] != ENVELOPE_VERSION
                or anchor['scope'] != self._scope(binding)):
            raise StoreError('recovery_required')

    def _initialize(self, binding):
        # Anchor survives disconnect and cleanup. If initialization crashes
        # between these writes, fail closed rather than manufacture revision0.
        self._write('identity.json', _json({'version': ENVELOPE_VERSION, 'scope': self._scope(asdict(binding))}))
        initial = {'version': ENVELOPE_VERSION, 'revision': 0, 'phase': 'disconnected',
                   'binding': asdict(binding), 'target': None}
        self._write('current.json', _json(initial))
        return initial

    @staticmethod
    def _target(target):
        if not isinstance(target, str) or len(target) != 32 or uuid.UUID(hex=target).hex != target:
            raise StoreError('corrupt_store')
        return target

    def _validate(self, binding):
        if not isinstance(binding, TrustedBinding) or binding.profile_locator != str(self.profile):
            raise StoreError('scope_denied')

    @staticmethod
    def _cas(state, revision):
        if type(revision) is not int or state['revision'] != revision:
            raise StoreError('stale_revision')

    def _fence(self, binding, revision, *, phase='active'):
        self._validate(binding)
        state = self._state()
        self._cas(state, revision)
        if state['phase'] != phase or state['binding'] != asdict(binding):
            raise StoreError('connection_unavailable')
        return state

    def _journal(self, target):
        self._target(target)
        record = self._read_json(target + '.journal')
        if not isinstance(record, dict) or set(record) != {'version', 'revision', 'binding', 'native_binding', 'reference', 'target'}:
            raise StoreError('corrupt_store')
        if type(record['version']) is not int or record['version'] != ENVELOPE_VERSION or type(record['revision']) is not int or not 1 <= record['revision'] < 2**63 or record['target'] != target:
            raise StoreError('corrupt_store')
        binding = TrustedBinding(**record['binding'])
        self._validate(binding)
        self._check_scope(record['binding'])
        expected = ValueBinding(binding.organization_id, binding.registration_id, record['revision'], binding.owner_id, target)
        if (not isinstance(record['native_binding'], dict) or type(record['native_binding'].get('version')) is not int
                or record['native_binding'] != asdict(expected) or record['reference'] != binding_reference(expected)):
            raise StoreError('corrupt_store')
        return record, expected

    @staticmethod
    def _aad(record):
        return _json({'version': record['version'], 'revision': record['revision'], 'binding': record['binding'], 'target': record['target']})

    def _decrypt(self, state):
        record, native = self._journal(state['target'])
        if record['revision'] != state['revision'] or record['binding'] != state['binding']:
            raise StoreError('corrupt_store')
        envelope = self._read(state['target'] + '.enc', MAX_ENVELOPE_BYTES)
        if len(envelope) < 29:
            raise StoreError('corrupt_store')
        result = []
        def decrypt(key):
            if type(key) is not bytes or len(key) != 32 or result:
                raise StoreError('corrupt_store')
            result.append(AESGCM(key).decrypt(envelope[:12], envelope[12:], self._aad(record)))
        self._vault().consume(record['reference'], native, decrypt)
        if len(result) != 1:
            raise StoreError('corrupt_store')
        return result[0]

    def _publish(self, binding, secret, old_state):
        if type(secret) is not bytes or not 0 < len(secret) <= MAX_SECRET_BYTES:
            raise StoreError('invalid_secret')
        revision = old_state['revision'] + 1
        if revision >= 2**63:
            raise StoreError('revision_exhausted')
        target = uuid.uuid4().hex
        native = ValueBinding(binding.organization_id, binding.registration_id, revision, binding.owner_id, target)
        record = {'version': ENVELOPE_VERSION, 'revision': revision, 'binding': asdict(binding),
                  'native_binding': asdict(native), 'reference': binding_reference(native), 'target': target}
        # Persist the EXACT immutable target before any potentially uncertain put.
        self._write(target + '.journal', _json(record))
        key, nonce = os.urandom(32), os.urandom(12)
        reference = self._vault().put(native, key)
        if reference != record['reference']:
            raise StoreError('native_reference_mismatch')
        encrypted = nonce + AESGCM(key).encrypt(nonce, secret, self._aad(record))
        self._write(target + '.enc', encrypted)
        new_state = {'version': ENVELOPE_VERSION, 'revision': revision, 'phase': 'active', 'binding': asdict(binding), 'target': target}
        # Read back both durable ciphertext AND the key through the native backend.
        if self._decrypt(new_state) != secret:
            raise StoreError('authentication_failed')
        if self._state() != old_state:
            raise StoreError('stale_revision')
        try:
            self._write('current.json', _json(new_state), replace=True)
            if self._state() != new_state:
                raise StoreError('publication_failed')
        except Exception:
            # Rename may be visible despite a failed fsync/readback. Do not
            # report success, retry the operation, or fall back to old secrets.
            # Caller must reconcile this exact intent/binding against status.
            raise StoreError('publication_uncertain') from None
        return revision

    @_guarded
    def status(self):
        """Passive, nonsecret status. Never opens the native vault."""
        with self._locked() as exists:
            state = self._state() if exists else {'revision': 0, 'phase': 'absent', 'target': None}
            pending = exists and any(path.name.endswith('.journal') and path.stem != state['target'] for path in self.directory.iterdir())
            return {'revision': state['revision'], 'state': state['phase'], 'cleanup_pending': bool(pending)}

    @_guarded
    def returning_binding(self):
        """Private identity correlation for an explicit returning-owner action.

        A disconnected binding is historical identity, never grant authority or
        permission to restore a reference. Auth must still require the current
        owner and explicit consent, then reverify this issued client/account.
        Missing publication evidence and corrupt scope anchors remain blocking.
        """
        with self._locked() as exists:
            if not exists:
                return None
            state = self._state()
            if state['phase'] == 'absent':
                return None
            binding = TrustedBinding(**state['binding'])
            self._validate(binding)
            return binding

    @_guarded
    def connection_descriptor(self):
        """Server-private restart descriptor; no vault reads and no renderer use.

        Metadata routes an explicit owner action, not proof of grant validity.
        Actual token use and uncertain-publication reconciliation still require
        authenticated decryption. Tombstones never reveal a reusable connection.
        """
        with self._locked() as exists:
            if not exists:
                raise StoreError('connection_unavailable')
            state = self._state()
            if state['phase'] not in {'active', 'refreshing'}:
                raise StoreError('connection_unavailable')
            binding = TrustedBinding(**state['binding'])
            self._validate(binding)
            return binding, state['revision']

    def _connect_payload(self, binding, secret, expected_revision):
        self._validate(binding)
        with self._locked(create=True):
            state = self._state()
            self._cas(state, expected_revision)
            if state['phase'] not in {'absent', 'disconnected'}:
                raise StoreError('disconnect_required')
            if state['binding'] is not None:
                for key in ('host_id', 'profile_locator', 'organization_id', 'owner_id'):
                    if state['binding'][key] != asdict(binding)[key]:
                        raise StoreError('scope_denied')
            if state['phase'] == 'absent':
                state = self._initialize(binding)
            return self._publish(binding, secret, state)

    @_guarded
    def connect(self, binding, secret, *, expected_revision):
        """Legacy opaque fixture boundary. Typed grants use connect_grant only."""
        if _is_grant(secret):
            raise StoreError('typed_grant_requires_handoff')
        return self._connect_payload(binding, secret, expected_revision)

    @_guarded
    def connect_grant(self, binding, *, access_token, refresh_token, id_token,
                      expires_at, granted_scopes, auth_attempt_id, expected_revision):
        """Publish a verified, explicitly consented grant from the auth coordinator.

        Token verification, owner consent and once-only token exchange precede
        this call. ID/refresh tokens stay inside the encrypted payload; no API
        here exposes them. Expiry requires explicit reauthorization in checkpoint2.
        """
        self._validate(binding)
        payload = {
            'schemaVersion': 1, 'issuer': _GRANT_ISSUER, 'authAttemptId': auth_attempt_id,
            'priorStoreRevision': expected_revision, 'verifiedBinding': asdict(binding),
            'grantedScopes': list(_grant_scopes(granted_scopes)), 'expiresAt': expires_at,
            'accessToken': access_token, 'refreshToken': refresh_token, 'idToken': id_token,
        }
        secret = _GRANT_PREFIX + _json(payload)
        _parse_grant(secret, binding)
        return self._connect_payload(binding, secret, expected_revision)

    @_guarded
    def reconcile_auth_attempt(self, binding, *, auth_attempt_id, prior_store_revision, granted_scopes):
        """Authenticate one exact uncertain publication; never replay exchange.

        Requires the original server-side attempt metadata. A matching status
        or revision alone is insufficient. No older envelope is ever consulted.
        The auth coordinator must still fence its current owner generation.
        """
        _grant_attempt(auth_attempt_id, prior_store_revision)
        scopes = _grant_scopes(granted_scopes)
        with self._locked() as exists:
            if not exists:
                raise StoreError('auth_attempt_mismatch')
            state = self._fence(binding, prior_store_revision + 1)
            payload = _parse_grant(self._decrypt(state), binding)
            self._fence(binding, prior_store_revision + 1)
            if (payload['authAttemptId'] != auth_attempt_id
                    or payload['priorStoreRevision'] != prior_store_revision
                    or tuple(payload['grantedScopes']) != scopes):
                raise StoreError('auth_attempt_mismatch')
            return state['revision']

    def _lease_scope(self):
        info = self._check(self.profile, directory=True)
        return info.st_dev, info.st_ino

    def _retire_leases(self):
        scope = self._lease_scope()
        with _LEASES_LOCK:
            leases = list(_LEASES.get(scope, ()))
        for lease in leases:
            lease.close()

    @_guarded
    def lease_access_token(self, binding, revision, *, owner_generation, owner_guard, cancel):
        """Explicit server-private handoff; not a consume callback retaining bytes.

        ``owner_guard`` is a bounded, local zero-arg check that closes over the
        exact transport generation object and home; it must perform no network
        I/O. The cancellation source is server-owned and exposes is_set().
        """
        if (type(owner_generation) is not object or not callable(owner_guard)
                or not callable(getattr(cancel, 'is_set', None))):
            raise StoreError('owner_generation_changed')
        with self._locked() as exists:
            if not exists:
                raise StoreError('connection_unavailable')
            state = self._fence(binding, revision)
            if owner_guard() is not True:
                raise StoreError('owner_generation_changed')
            if cancel.is_set():
                raise StoreError('lease_cancelled')
            payload = _parse_grant(self._decrypt(state), binding)
            self._fence(binding, revision)
            if owner_guard() is not True:
                raise StoreError('owner_generation_changed')
            if cancel.is_set():
                raise StoreError('lease_cancelled')
            if payload['priorStoreRevision'] + 1 != revision:
                raise StoreError('invalid_grant')
            if not _REQUIRED_SCOPES.issubset(payload['grantedScopes']):
                raise StoreError('scope_not_authorized')
            if self._clock() >= payload['expiresAt']:
                raise StoreError('grant_expired')
            lease = TokenLease(self, binding, revision, owner_generation, owner_guard,
                               cancel, payload['expiresAt'], payload['accessToken'], self._lease_scope())
            with _LEASES_LOCK:
                _LEASES.setdefault(lease._scope, weakref.WeakSet()).add(lease)
            return lease

    @_guarded
    def consume(self, binding, revision, consumer):
        """Fenced callback only; do not return or retain secret bytes in consumers."""
        with self._locked() as exists:
            if not exists:
                raise StoreError('connection_unavailable')
            state = self._fence(binding, revision)
            secret = self._decrypt(state)
            self._fence(binding, revision)
            if _is_grant(secret):
                raise StoreError('typed_grant_requires_handoff')
            result = consumer(secret)
            self._fence(binding, revision)
            return result

    @_guarded
    def refresh(self, binding, revision, rotator):
        """Call rotator once with opaque bytes; it returns replacement opaque bytes.

        Identity verification is the auth layer's responsibility; binding cannot
        change. Even a local callback failure leaves a durable reauth requirement.
        """
        with self._locked() as exists:
            if not exists:
                raise StoreError('connection_unavailable')
            state = self._fence(binding, revision)
            secret = self._decrypt(state)
            self._fence(binding, revision)
            if _is_grant(secret):
                raise StoreError('typed_grant_requires_handoff')
            self._retire_leases()
            pending = dict(state, phase='refreshing')
            self._write('current.json', _json(pending), replace=True)
            self._fence(binding, revision, phase='refreshing')
            replacement = rotator(secret)
            if _is_grant(replacement):
                raise StoreError('typed_grant_requires_handoff')
            self._fence(binding, revision, phase='refreshing')
            return self._publish(binding, replacement, pending)

    def _recover(self, state):
        pending = False
        for path in self.directory.iterdir():
            if not path.name.endswith('.journal') or path.stem == state['target']:
                continue
            try:
                record, native = self._journal(path.stem)
                # Tombstone/current reference is authoritative, never infer a new
                # connection from an envelope or enumerate a native credential vault.
                self._vault().delete(record['reference'], native)
                envelope = self.directory / (path.stem + '.enc')
                if envelope.exists() or envelope.is_symlink():
                    self._check(envelope)
                    envelope.unlink()
                    self._sync_directory()
                self._check(path)
                path.unlink()
                self._sync_directory()
            except Exception:
                pending = True
        return pending

    @_guarded
    def recover_orphans(self):
        with self._locked() as exists:
            return self._recover(self._state()) if exists else False

    @_guarded
    def disconnect(self, *, expected_revision):
        """Local retirement only; never claims remote provider grant revocation."""
        with self._locked() as exists:
            if not exists:
                if expected_revision != 0:
                    raise StoreError('stale_revision')
                return {'revision': 0, 'state': 'absent', 'cleanup_pending': False}
            state = self._state()
            self._cas(state, expected_revision)
            self._retire_leases()
            if state['phase'] not in {'absent', 'disconnected'}:
                if state['revision'] + 1 >= 2**63:
                    raise StoreError('revision_exhausted')
                state = dict(state, revision=state['revision'] + 1, phase='disconnected', target=None)
                # Tombstone is durable BEFORE cleanup; failed cleanup cannot revive.
                self._write('current.json', _json(state), replace=True)
            return {'revision': state['revision'], 'state': state['phase'], 'cleanup_pending': self._recover(state)}
