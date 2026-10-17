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
import hashlib
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
MAX_HEAD_BYTES = 32 * 1024
MAX_TRANSITION_BYTES = 128 * 1024
TICKET_ARCHIVE = 'ticket-archive'
MAX_SEQUENCE = 2**63 - 1
_THREAD_USE = threading.local()
_LEASES_LOCK = threading.Lock()
_LEASES = {}
_OLD_GRANT_PREFIX = b'EIDOLON_SETUP_GRANT_V1\n'
_GRANT_PREFIX = b'EIDOLON_SETUP_GRANT_V2\n'
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
    'ticket_conflict', 'ticket_retired', 'transition_pending', 'storage_recovery_required',
    'ticket_sequence_exhausted', 'operation_history_exhausted', 'reauthorization_required', 'operation_mismatch', 'lease_cancelled', 'owner_generation_changed', 'scope_not_authorized', 'invalid_lease_result',
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
    return isinstance(secret, bytes) and secret.startswith((b'EIDOLON_SETUP_GRANT_V1', b'EIDOLON_SETUP_GRANT_V2'))


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


def _parse_v1_grant(secret, binding):
    """Recognize only the original authenticated V1 schema for local migration."""
    if (type(secret) is not bytes or not secret.startswith(_OLD_GRANT_PREFIX)
            or len(secret) > MAX_SECRET_BYTES):
        raise StoreError('invalid_grant')
    value = json.loads(secret[len(_OLD_GRANT_PREFIX):], object_pairs_hook=_pairs)
    if (type(value) is not dict or set(value) != {
            'schemaVersion', 'issuer', 'authAttemptId', 'priorStoreRevision', 'verifiedBinding',
            'grantedScopes', 'expiresAt', 'accessToken', 'refreshToken', 'idToken'}
            or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
            or value['issuer'] != _GRANT_ISSUER or value['verifiedBinding'] != asdict(binding)):
        raise StoreError('invalid_grant')
    _grant_attempt(value['authAttemptId'], value['priorStoreRevision'])
    if (type(value['grantedScopes']) is not list
            or tuple(value['grantedScopes']) != _grant_scopes(value['grantedScopes'])):
        raise StoreError('invalid_grant')
    expiry = value['expiresAt']
    if type(expiry) not in {int, float} or not math.isfinite(expiry) or not 0 < expiry < 253402300800:
        raise StoreError('invalid_grant')
    for key in ('accessToken', 'refreshToken', 'idToken'):
        token = value[key]
        if (type(token) is not str or not 1 <= len(token) <= 16384
                or any(not 33 <= ord(c) <= 126 for c in token)):
            raise StoreError('invalid_grant')
    return value


def _canonical_epoch(value):
    if type(value) is not str:
        raise StoreError('invalid_grant')
    try:
        parsed = uuid.UUID(value)
        if str(parsed) != value or parsed.version != 4:
            raise ValueError
    except ValueError:
        raise StoreError('invalid_grant') from None
    return value


def _identity_evidence(value, binding):
    if (type(value) is not dict or set(value) != {
            'subject', 'audience', 'nonce', 'authTime', 'authorizedParty'}
            or value['subject'] != binding.account_id
            or value['audience'] != binding.issued_client_id):
        raise StoreError('invalid_grant')
    nonce = value['nonce']
    if (type(nonce) is not str or not 1 <= len(nonce) <= 2048
            or any(not 33 <= ord(c) <= 126 for c in nonce)):
        raise StoreError('invalid_grant')
    stamp = value['authTime']
    if stamp is not None and (type(stamp) is not int or not 0 <= stamp < 253402300800):
        raise StoreError('invalid_grant')
    party = value['authorizedParty']
    if party is not None and (type(party) is not str or party != binding.issued_client_id):
        raise StoreError('invalid_grant')
    return dict(value)


def _parse_grant(secret, binding):
    if isinstance(secret, bytes) and secret.startswith(b'EIDOLON_SETUP_GRANT_V1'):
        raise StoreError('reauthorization_required')
    if (not isinstance(secret, bytes) or not secret.startswith(_GRANT_PREFIX)
            or len(secret) > MAX_SECRET_BYTES):
        raise StoreError('invalid_grant')
    payload = json.loads(secret[len(_GRANT_PREFIX):], object_pairs_hook=_pairs)
    if (type(payload) is not dict or set(payload) != {
            'schemaVersion', 'issuer', 'verifiedBinding', 'authorizationEpoch',
            'consent', 'identityEvidence', 'tokenRevision', 'rotation',
            'grantedScopes', 'expiresAt', 'accessToken', 'refreshToken', 'idToken'}
            or type(payload['schemaVersion']) is not int or payload['schemaVersion'] != 2
            or payload['issuer'] != _GRANT_ISSUER
            or payload['verifiedBinding'] != asdict(binding)):
        raise StoreError('invalid_grant')
    _canonical_epoch(payload['authorizationEpoch'])
    _identity_evidence(payload['identityEvidence'], binding)
    consent = payload['consent']
    if (type(consent) is not dict or set(consent) != {
            'authAttemptId', 'priorStoreRevision', 'consentVersion', 'grantedScopes'}
            or type(consent['consentVersion']) is not int or consent['consentVersion'] != 1):
        raise StoreError('invalid_grant')
    _grant_attempt(consent['authAttemptId'], consent['priorStoreRevision'])
    for scopes in (payload['grantedScopes'], consent['grantedScopes']):
        if type(scopes) is not list or tuple(scopes) != _grant_scopes(scopes):
            raise StoreError('invalid_grant')
    if payload['grantedScopes'] != consent['grantedScopes']:
        raise StoreError('invalid_grant')
    revision, rotation = payload['tokenRevision'], payload['rotation']
    if type(revision) is not int or not 1 <= revision < 2**63:
        raise StoreError('invalid_grant')
    if rotation is None:
        if revision != consent['priorStoreRevision'] + 1:
            raise StoreError('invalid_grant')
    else:
        if type(rotation) is not dict or set(rotation) != {'refreshAttemptId', 'priorTokenRevision'}:
            raise StoreError('invalid_grant')
        _grant_attempt(rotation['refreshAttemptId'], rotation['priorTokenRevision'])
        if (revision != rotation['priorTokenRevision'] + 1
                or rotation['priorTokenRevision'] < consent['priorStoreRevision'] + 1):
            raise StoreError('invalid_grant')
    expiry = payload['expiresAt']
    if type(expiry) not in {int, float} or not math.isfinite(expiry) or not 0 < expiry < 253402300800:
        raise StoreError('invalid_grant')
    for key in ('accessToken', 'refreshToken', 'idToken'):
        token = payload[key]
        if (type(token) is not str or not 1 <= len(token) <= 16384
                or any(not 33 <= ord(char) <= 126 for char in token)):
            raise StoreError('invalid_grant')
    return payload


@dataclass(frozen=True, repr=False)
class LeaseAttestation:
    """Store-minted nonsecret binding; not a durable or transferable send grant."""
    account_binding_digest: str
    authorization_epoch: str
    store_revision: int
    owner_generation: object

    def __repr__(self):
        return '<LeaseAttestation server-private>'

    def __reduce__(self):
        raise TypeError('Server-private lease attestation cannot be serialized')


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
        self._attestation = None

    @property
    def attestation(self):
        if type(self._attestation) is not LeaseAttestation:
            raise StoreError('invalid_binding')
        return self._attestation

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

    def network_cancelled(self):
        """HTTP watchdog predicate: local Events only, never durable I/O or locks."""
        return self._revoked.is_set() or self._cancel.is_set()

    def try_send_allowed(self):
        """Nonblocking durable admission immediately before each physical send.

        Contention is denial, not a wait or permission. The caller rechecks its
        absolute deadline and network_cancelled after return, then sends with
        no store lock held. Admission cannot eliminate the final cancellation
        race; reply fences still discard retired results.
        """
        if self.network_cancelled():
            return False
        try:
            with self._store._locked(nonblocking=True) as exists:
                if not exists:
                    return False
                self._check_locked()
            return not self.network_cancelled()
        except Exception:
            return False

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

    @_guarded
    def run_nonblocking(self, request):
        """Sender-only variant: contention denies; original run is unchanged."""
        with self._mutex:
            if self._used:
                raise StoreError('lease_used')
            self._used = True
        try:
            with self._store._locked(nonblocking=True) as exists:
                if not exists:
                    raise StoreError('connection_unavailable')
                self._check_locked()
                with self._mutex:
                    if not self._token:
                        raise StoreError('lease_cancelled')
                    token = self._token.decode('ascii')
            # This explicit handoff runs with NO store lock. Last-moment checks
            # reduce, but cannot eliminate, the post-admission cancellation race.
            if not self.try_send_allowed():
                raise StoreError('lease_cancelled')
            result = request(token, self)
            if inspect.isawaitable(result) or inspect.isgenerator(result) or inspect.isasyncgen(result):
                # A coroutine/generator would defer work past token ownership and
                # late-reply fences. Adapters must return completed response data.
                if inspect.iscoroutine(result) or inspect.isgenerator(result):
                    result.close()
                raise StoreError('invalid_lease_result')
            with self._store._locked(nonblocking=True) as exists:
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


class _OperationLease:
    """Private one-use refresh/revoke capability. HTTP never holds store locks."""
    def __init__(self, store, binding, ticket, payload, guard, cancel):
        self._store, self._binding = store, binding
        self._ticket = dict(ticket)
        self._operation = {'kind': 'refresh' if ticket['kind'] == 'refresh' else 'disconnect',
                           'authorizationEpoch': ticket['authorizationEpoch'],
                           'priorRevision': ticket['priorStoreRevision']}
        self._token = bytearray(payload['refreshToken'], 'ascii')
        self._consent = _json(payload['consent'])
        self._identity = _json(payload['identityEvidence'])
        self._scopes = tuple(payload['grantedScopes'])
        self._scope = store._lease_scope()
        self._guard, self._cancel = guard, cancel
        self._mutex = threading.Lock()
        self._used = False
        self._revoked = threading.Event()
        with _LEASES_LOCK:
            _LEASES.setdefault(self._scope, weakref.WeakSet()).add(self)

    def __repr__(self):
        return '<OperationLease server-private>'

    def __reduce__(self):
        raise TypeError('Server-private operation lease cannot be serialized')

    @property
    def authorization_epoch(self):
        return self._operation['authorizationEpoch']

    @property
    def prior_revision(self):
        return self._operation['priorRevision']

    @property
    def expected_revision(self):
        return self.prior_revision + 1

    @property
    def consent(self):
        return json.loads(self._consent)

    @property
    def identity_evidence(self):
        return json.loads(self._identity)

    @property
    def granted_scopes(self):
        return self._scopes

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

    def _check_locked(self):
        self._store._check_ticket_lease(self._binding, self._ticket)
        self._store._check_guard(self._guard, self._cancel)
        if self._revoked.is_set():
            raise StoreError('lease_cancelled')

    def network_cancelled(self):
        """HTTP watchdog predicate: local Events only, never durable I/O or locks."""
        return self._revoked.is_set() or self._cancel.is_set()

    def try_send_allowed(self):
        """Nonblocking durable admission immediately before each physical send.

        Contention is denial, not a wait or permission. The caller rechecks its
        absolute deadline and network_cancelled after return, then sends with
        no store lock held. Admission cannot eliminate the final cancellation
        race; reply fences still discard retired results.
        """
        if self.network_cancelled():
            return False
        try:
            with self._store._locked(nonblocking=True) as exists:
                if not exists:
                    return False
                self._check_locked()
            return not self.network_cancelled()
        except Exception:
            return False

    def is_set(self):
        try:
            with self._store._locked() as exists:
                if not exists:
                    return True
                self._check_locked()
            return False
        except Exception:
            return True

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
                    token = self._token.decode('ascii')
            if self.is_set():
                raise StoreError('lease_cancelled')
            result = request(token, self)
            if inspect.isawaitable(result) or inspect.isgenerator(result) or inspect.isasyncgen(result):
                if inspect.iscoroutine(result) or inspect.isgenerator(result):
                    result.close()
                raise StoreError('invalid_lease_result')
            with self._store._locked() as exists:
                if not exists:
                    raise StoreError('connection_unavailable')
                if self._operation['kind'] == 'refresh':
                    if self._revoked.is_set():
                        raise StoreError('lease_cancelled')
                    self._store._check_guard(self._guard, self._cancel)
                    current = self._store._state()
                    if current['phase'] == 'active':
                        self._store._verify_refresh_commit(self._binding, self._ticket)
                        self._store._check_guard(self._guard, self._cancel)
                        if self._revoked.is_set():
                            raise StoreError('lease_cancelled')
                    else:
                        self._check_locked()
                else:
                    self._check_locked()
                    return self._store._finish_revocation(self._binding, self._ticket, result is True)
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
        limit = (MAX_ENVELOPE_BYTES if name.endswith('.enc') else MAX_TRANSITION_BYTES
                 if name in {'ticket-anchor.json', 'ticket-pending.json', 'ticket-barrier.json'} else MAX_HEAD_BYTES
                 if name in {'current.json', 'ticket-barrier.json'} else MAX_METADATA_BYTES)
        if len(data) > limit:
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
    def _locked(self, *, create=False, nonblocking=False):
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
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0))
            except BlockingIOError:
                raise StoreError('connection_unavailable') from None
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
        value = json.loads(self._read('current.json', MAX_HEAD_BYTES), object_pairs_hook=_pairs)
        if type(value) is dict and set(value) == {'body', 'digest'}:
            return self._validate_head(value)
        if (not isinstance(value, dict) or set(value) not in ({'version', 'revision', 'phase', 'binding', 'target'},
                                                        {'version', 'revision', 'phase', 'binding', 'target', 'operation'},
                                                        {'version', 'revision', 'phase', 'binding', 'target', 'localRetirement'})
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
        if 'operation' in value:
            raise StoreError('storage_recovery_required')
        if 'localRetirement' in value:
            self._validate_local_retirement(value)
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
        if state['version'] == 2:
            state = self._authority(binding)['body']
        self._cas(state, revision)
        if state['phase'] != phase or state['binding'] != asdict(binding):
            raise StoreError('connection_unavailable')
        return state

    def _journal(self, target, *, ticket=False):
        self._target(target)
        record = (json.loads(self._archive_read(target + '.journal', MAX_HEAD_BYTES), object_pairs_hook=_pairs)
                  if ticket else self._read_json(target + '.journal'))
        if not isinstance(record, dict) or set(record) not in ({'version', 'revision', 'binding', 'native_binding', 'reference', 'target'},
                                                               {'version', 'revision', 'binding', 'native_binding', 'reference', 'target', 'operation'}):
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
        value = {key: record[key] for key in ('version', 'revision', 'binding', 'target')}
        if 'operation' in record:
            value['operation'] = record['operation']
        return _json(value)

    def _decrypt(self, state):
        record, native = self._journal(state['target'], ticket=state['version'] == 2)
        if record['revision'] != state['revision'] or record['binding'] != state['binding']:
            raise StoreError('corrupt_store')
        if state['version'] != 2 and state['phase'] == 'active' and state.get('operation') != record.get('operation'):
            raise StoreError('operation_mismatch')
        envelope = (self._archive_read(state['target'] + '.enc', MAX_ENVELOPE_BYTES)
                    if state['version'] == 2 else self._read(state['target'] + '.enc', MAX_ENVELOPE_BYTES))
        if state['version'] == 2 and hashlib.sha256(envelope).hexdigest() != state.get('grantEnvelopeDigest'):
            raise StoreError('authentication_failed')
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

    def _archive_operation(self, state):
        # Preserve nonsecret audit evidence even across reauthorization/cleanup.
        operation = state.get('operation')
        if operation is None:
            return
        data = _json({'revision': state['revision'], 'phase': state['phase'],
                      'operation': operation})
        name = 'operation-' + hashlib.sha256(data).hexdigest() + '.receipt'
        path = self.directory / name
        if path.exists():
            if self._read(name, MAX_METADATA_BYTES) != data:
                raise StoreError('corrupt_store')
        else:
            self._write(name, data)
        if self._read(name, MAX_METADATA_BYTES) != data:
            raise StoreError('publication_failed')

    def _publish(self, binding, secret, old_state, *, operation=None, publication_guard=None):
        if type(secret) is not bytes or not 0 < len(secret) <= MAX_SECRET_BYTES:
            raise StoreError('invalid_secret')
        revision = old_state['revision'] + 1
        if revision >= 2**63:
            raise StoreError('revision_exhausted')
        target = uuid.uuid4().hex
        native = ValueBinding(binding.organization_id, binding.registration_id, revision, binding.owner_id, target)
        record = {'version': ENVELOPE_VERSION, 'revision': revision, 'binding': asdict(binding),
                  'native_binding': asdict(native), 'reference': binding_reference(native), 'target': target}
        if operation is not None:
            record['operation'] = operation
        # Persist the EXACT immutable target before any potentially uncertain put.
        self._write(target + '.journal', _json(record))
        key, nonce = os.urandom(32), os.urandom(12)
        reference = self._vault().put(native, key)
        if reference != record['reference']:
            raise StoreError('native_reference_mismatch')
        encrypted = nonce + AESGCM(key).encrypt(nonce, secret, self._aad(record))
        self._write(target + '.enc', encrypted)
        new_state = {'version': ENVELOPE_VERSION, 'revision': revision, 'phase': 'active', 'binding': asdict(binding), 'target': target}
        if operation is not None:
            new_state['operation'] = operation
        # Read back both durable ciphertext AND the key through the native backend.
        if self._decrypt(new_state) != secret:
            raise StoreError('authentication_failed')
        if self._state() != old_state:
            raise StoreError('stale_revision')
        self._archive_operation(old_state)
        if publication_guard is not None:
            publication_guard()
        try:
            self._write('current.json', _json(new_state), replace=True)
            if self._state() != new_state:
                raise StoreError('publication_failed')
            if publication_guard is not None:
                publication_guard()
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
            if exists and state.get('version') == 2:
                self._authority(TrustedBinding(**state['binding']), allow_barrier=True, allow_pending=True)
                barrier = self._barrier(state)
                if barrier['state'] == 'active':
                    raise StoreError('storage_recovery_required')
                return {'revision': state['revision'],
                        'state': state['phase'],
                        'cleanup_pending': bool(state['cleanup'] or state['candidate']['state'] != 'idle' or
                            self._read_capsule('ticket-pending.json', 'pending')['body']['state'] != 'empty')}
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
            if state['phase'] not in {'absent', 'disconnected'} and not (
                    secret.startswith(_GRANT_PREFIX) and state['version'] == 2 and state['phase'] == 'refreshing'):
                raise StoreError('disconnect_required')
            if state['binding'] is not None and secret.startswith(_GRANT_PREFIX) and state['binding'] != asdict(binding):
                raise StoreError('scope_denied')
            if state['binding'] is not None:
                for key in ('host_id', 'profile_locator', 'organization_id', 'owner_id'):
                    if state['binding'][key] != asdict(binding)[key]:
                        raise StoreError('scope_denied')
            if state['phase'] == 'absent':
                state = self._initialize(binding)
            if secret.startswith(_GRANT_PREFIX):
                return self._connect_ticket_grant(binding, secret, state)
            if state['version'] == 2:
                raise StoreError('typed_grant_requires_handoff')
            self._retire_leases()
            return self._publish(binding, secret, state)

    @_guarded
    def connect(self, binding, secret, *, expected_revision):
        """Legacy opaque fixture boundary. Typed grants use connect_grant only."""
        if _is_grant(secret):
            raise StoreError('typed_grant_requires_handoff')
        return self._connect_payload(binding, secret, expected_revision)

    @_guarded
    def connect_grant(self, binding, *, access_token, refresh_token, id_token,
                      expires_at, granted_scopes, auth_attempt_id, expected_revision,
                      identity_evidence):
        """Publish a verified consent; evidence and epoch are never inferred from V1."""
        self._validate(binding)
        epoch = str(uuid.uuid4())
        scopes = list(_grant_scopes(granted_scopes))
        payload = {
            'schemaVersion': 2, 'issuer': _GRANT_ISSUER,
            'verifiedBinding': asdict(binding), 'authorizationEpoch': epoch,
            'consent': {'authAttemptId': auth_attempt_id, 'priorStoreRevision': expected_revision,
                        'consentVersion': 1, 'grantedScopes': scopes},
            'identityEvidence': _identity_evidence(identity_evidence, binding),
            'tokenRevision': expected_revision + 1, 'rotation': None,
            'grantedScopes': scopes, 'expiresAt': expires_at,
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
            if (self.directory / 'ticket-anchor.json').exists():
                self._reconcile_transition_locked(binding)
            state = self._fence(binding, prior_store_revision + 1)
            payload = _parse_grant(self._decrypt(state), binding)
            self._fence(binding, prior_store_revision + 1)
            if (payload['consent']['authAttemptId'] != auth_attempt_id
                    or payload['consent']['priorStoreRevision'] != prior_store_revision
                    or tuple(payload['grantedScopes']) != scopes):
                raise StoreError('auth_attempt_mismatch')
            return state['revision']

    @_guarded
    def reconcile_auth_publication(self, binding, *, auth_attempt_id, prior_store_revision,
                                   owner_guard, cancel):
        """Resolve only this host-fenced local publication; never exchange or put.

        The coordinator supplies a durable publishing binding digest and holds
        the exact host attempt lock through this proof and its terminal save.
        Local retirement retains authenticated history and remote uncertainty.
        """
        _grant_attempt(auth_attempt_id, prior_store_revision)
        with self._locked() as exists:
            if not exists:
                raise StoreError('auth_attempt_mismatch')
            self._check_guard(owner_guard, cancel)
            anchor = self._anchor(binding)['body']
            raw = json.loads(self._read('current.json', MAX_HEAD_BYTES), object_pairs_hook=_pairs)
            if (raw == anchor['initialHead']
                    or self._digest_value('bootstrap', raw) == anchor['bootstrapCurrentDigest']):
                # Only the authenticated initialization preimage is recoverable.
                # Do not mistake unrelated/missing later heads for a fresh store.
                body = anchor['initialHead']['body']
                if body['binding'] != asdict(binding):
                    raise StoreError('auth_attempt_mismatch')
                if raw != anchor['initialHead']:
                    self._bootstrap_eligible_for_anchor(binding, raw)
            else:
                body = self._authority(binding, allow_pending=True)['body']
            if not ((body['phase'] == 'disconnected' and body['revision'] == prior_store_revision)
                    or (body['phase'] == 'active' and body['revision'] == prior_store_revision + 1)):
                raise StoreError('auth_attempt_mismatch')
            if body['candidate']['state'] != 'idle' and body['candidate']['purpose'] != 'consent':
                raise StoreError('auth_attempt_mismatch')
            def exact_grant(head):
                payload = self._grant_payload(head)
                if (head['body']['binding'] != asdict(binding)
                        or payload['consent']['authAttemptId'] != auth_attempt_id
                        or payload['consent']['priorStoreRevision'] != prior_store_revision
                        or payload['tokenRevision'] != prior_store_revision + 1
                        or payload['rotation'] is not None):
                    raise StoreError('auth_attempt_mismatch')

            # Reject an unrelated active grant before cleanup or finishing a
            # pending commit. Its authenticated metadata alone is not consent.
            if body['phase'] == 'active':
                exact_grant(self._authority(binding, allow_pending=True))
            pending_path = self.directory / 'ticket-pending.json'
            if pending_path.exists() or pending_path.is_symlink():
                pending = self._read_capsule('ticket-pending.json', 'pending')
                plan = self._validate_pending(pending, body)
                if plan['state'] == 'transition' and plan['newHead']['body']['phase'] == 'active':
                    exact_grant(plan['newHead'])
            self._reconcile_transition_locked(binding)
            head = self._authority(binding)
            body = head['body']
            if body['candidate']['state'] != 'idle' or body['cleanup']:
                raise StoreError('storage_recovery_required')
            if body['phase'] == 'active' and body['revision'] == prior_store_revision + 1:
                exact_grant(head)
                result = self._result(head)
            elif (body['phase'] == 'disconnected' and body['revision'] == prior_store_revision
                    and body['target'] is None):
                result = self._result(head, local=True)
            else:
                raise StoreError('auth_attempt_mismatch')
            self._check_guard(owner_guard, cancel)
            return result

    @staticmethod
    def _binding_digest(binding):
        return hashlib.sha256(_json(asdict(binding))).hexdigest()


    @staticmethod
    def _check_guard(guard, cancel):
        # Never acquire service/owner/attempt/publication locks or touch SQLite.
        # The coordinator already did durable checks outside this store lock.
        if not callable(guard) or guard() is not True:
            raise StoreError('owner_generation_changed')
        if not callable(getattr(cancel, 'is_set', None)) or cancel.is_set():
            raise StoreError('lease_cancelled')


    def _validate_v1_bootstrap(self, binding, state):
        marker = self._read_json('history-v1-bootstrap.json')
        if (type(marker) is not dict or set(marker) != {
                'version', 'scopeDigest', 'bindingDigest', 'priorRevision', 'priorTarget', 'retiredRevision'}
                or type(marker['version']) is not int or marker['version'] != 1
                or marker['scopeDigest'] != self._history_scope(binding)
                or marker['bindingDigest'] != self._binding_digest(TrustedBinding(**state['binding']))
                or type(marker['priorRevision']) is not int or not 1 <= marker['priorRevision'] < 2**63 - 1
                or type(marker['retiredRevision']) is not int
                or marker['retiredRevision'] != marker['priorRevision'] + 1
                or state['phase'] != 'disconnected' or state['revision'] != marker['retiredRevision']):
            raise StoreError('recovery_required')
        self._target(marker['priorTarget'])
        retired = state.get('localRetirement')
        if (retired is None or retired['historyReason'] != 'reauthorization_required'
                or retired['priorRevision'] != marker['priorRevision']
                or retired['priorTarget'] != marker['priorTarget']):
            raise StoreError('recovery_required')
        return marker


    def _mark_verified_v1(self, binding, state, secret):
        if self._history_artifacts():
            return False
        payload = _parse_v1_grant(secret, binding)
        if payload['priorStoreRevision'] + 1 != state['revision']:
            raise StoreError('invalid_grant')
        marker = {'version': 1, 'scopeDigest': self._history_scope(binding),
                  'bindingDigest': self._binding_digest(binding), 'priorRevision': state['revision'],
                  'priorTarget': state['target'], 'retiredRevision': state['revision'] + 1}
        self._write('history-v1-bootstrap.json', _json(marker))
        if self._read_json('history-v1-bootstrap.json') != marker:
            raise StoreError('publication_failed')
        return True


    def _validate_local_retirement(self, state):
        value = state['localRetirement']
        if (type(value) is not dict or set(value) != {
                'priorRevision', 'priorTarget', 'localRetiredAt', 'remoteRevocation', 'historyReason'}
                or state['phase'] != 'disconnected'
                or type(value['priorRevision']) is not int
                or not 1 <= value['priorRevision'] < 2**63 - 1
                or state['revision'] != value['priorRevision'] + 1
                or value['remoteRevocation'] != 'unconfirmed'
                or value['historyReason'] not in {
                    'recovery_required', 'ticket_conflict', 'ticket_retired', 'transition_pending', 'storage_recovery_required',
    'ticket_sequence_exhausted', 'operation_history_exhausted', 'reauthorization_required'}
                or type(value['localRetiredAt']) not in {int, float}
                or not math.isfinite(value['localRetiredAt']) or value['localRetiredAt'] < 0):
            raise StoreError('corrupt_store')
        self._target(value['priorTarget'])


    # Ticket metadata is owner-protected and integrity-checked, not a signed
    # authority or hardware counter. Exact journals cover crash/loss/concurrency;
    # restoring a complete older protected-store snapshot is outside this model.
    # Immutable nonsecret evidence is retained; obsolete DEKs/ciphers are not.

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
    def attest_subscription_binding_nonblocking(self, binding, *, expected_revision,
            expected_epoch, expected_account_digest, lease_generation_guard):
        """Store-only fence for captured preflight evidence under a ledger writer.

        The guard is registry/Event-only. No service/catalog/SQLite lookup and no
        refresh or lease issuance occurs here. Contention fails closed.
        """
        if (type(binding) is not TrustedBinding or not callable(lease_generation_guard)
                or expected_account_digest != self._binding_digest(binding)):
            raise StoreError('invalid_binding')
        _canonical_epoch(expected_epoch)
        with self._locked(nonblocking=True) as exists:
            if not exists:
                raise StoreError('connection_unavailable')
            state = self._fence(binding, expected_revision)
            if (state.get('version') != 2 or state.get('authorizationEpoch') != expected_epoch
                    or state.get('cleanup') or state.get('candidate') != {'state': 'idle'}):
                raise StoreError('stale_revision')
            if lease_generation_guard() is not True:
                raise StoreError('owner_generation_changed')
            return {'account_binding_digest': expected_account_digest,
                    'authorization_epoch': expected_epoch, 'store_revision': expected_revision}

    @_guarded
    def lease_access_token(self, binding, revision, *, owner_generation, owner_guard, cancel):
        """Explicit server-private handoff; not a consume callback retaining bytes.

        ``owner_guard`` is a bounded, local zero-arg check that closes over the
        exact transport generation object and home; it must perform no network
        I/O, SQLite, or owner/service/publication/attempt locks. Only a cheap
        registry generation check is allowed. The cancellation source is server-owned and exposes is_set().
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
            if (payload['tokenRevision'] != revision or state.get('authorizationEpoch', payload['authorizationEpoch']) != payload['authorizationEpoch']):
                raise StoreError('invalid_grant')
            if not _REQUIRED_SCOPES.issubset(payload['grantedScopes']):
                raise StoreError('scope_not_authorized')
            if self._clock() >= payload['expiresAt']:
                raise StoreError('grant_expired')
            lease = TokenLease(self, binding, revision, owner_generation, owner_guard,
                               cancel, payload['expiresAt'], payload['accessToken'], self._lease_scope())
            lease._attestation = LeaseAttestation(self._binding_digest(binding),
                payload['authorizationEpoch'], revision, owner_generation)
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
                if secret.startswith(b'EIDOLON_SETUP_GRANT_V1'):
                    raise StoreError('reauthorization_required')
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
                if secret.startswith(b'EIDOLON_SETUP_GRANT_V1'):
                    raise StoreError('reauthorization_required')
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
        if state['version'] == 2:
            return bool(state['cleanup'] or state['candidate']['state'] != 'idle')
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
            if not exists:
                return False
            state = self._state()
            if state.get('version') == 2:
                self._reconcile_transition_locked(TrustedBinding(**state['binding']))
                state = self._state()
                return bool(state['cleanup'] or state['candidate']['state'] != 'idle' or
                            self._read_capsule('ticket-pending.json', 'pending')['body']['state'] != 'empty')
            return self._recover(state)

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
            if state['version'] == 2:
                result = self._local_disconnect_locked(TrustedBinding(**state['binding']), state)
                return {'revision': result['revision'], 'state': 'disconnected',
                        'cleanup_pending': bool(self._state()['cleanup'] or self._state()['candidate']['state'] != 'idle' or
                            self._read_capsule('ticket-pending.json', 'pending')['body']['state'] != 'empty')}
            self._retire_leases()
            if state['phase'] not in {'absent', 'disconnected'}:
                if state['revision'] + 1 >= 2**63:
                    raise StoreError('revision_exhausted')
                if not self._history_artifacts():
                    try:
                        binding = TrustedBinding(**state['binding'])
                        secret = self._decrypt(state)
                        if self._state() != state:
                            raise StoreError('stale_revision')
                        marked = self._mark_verified_v1(binding, state, secret)
                    except Exception:
                        marked = False
                    if self._state() != state:
                        raise StoreError('stale_revision')
                    if marked:
                        result = self._local_retire(state, 'reauthorization_required')
                        return {key: result[key] for key in ('revision', 'state', 'cleanup_pending')}
                self._archive_operation(state)
                state = dict(state, revision=state['revision'] + 1, phase='disconnected', target=None)
                state.pop('operation', None)
                # Tombstone is durable BEFORE cleanup; failed cleanup cannot revive.
                self._write('current.json', _json(state), replace=True)
            return {'revision': state['revision'], 'state': state['phase'], 'cleanup_pending': self._recover(state)}

    @staticmethod
    def _digest_value(domain, value):
        return hashlib.sha256(b'EIDOLON_TICKET_V1\0' + domain.encode('ascii') + b'\0' + _json(value)).hexdigest()

    @classmethod
    def _capsule(cls, domain, body):
        return {'body': body, 'digest': cls._digest_value(domain, body)}

    @classmethod
    def _uncapsule(cls, domain, value):
        if (type(value) is not dict or set(value) != {'body', 'digest'}
                or type(value['body']) is not dict
                or value['digest'] != cls._digest_value(domain, value['body'])):
            raise StoreError('storage_recovery_required')
        return value['body']

    def _read_capsule(self, name, domain, limit=MAX_TRANSITION_BYTES):
        try:
            value = json.loads(self._read(name, limit), object_pairs_hook=_pairs)
            self._uncapsule(domain, value)
            return value
        except Exception:
            raise StoreError('storage_recovery_required') from None

    @staticmethod
    def _hex64(value):
        return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)

    @staticmethod
    def _sequence(value):
        if (type(value) is not str or not 1 <= len(value) <= 19 or not value.isascii()
                or not value.isdecimal() or str(int(value)) != value or int(value) > MAX_SEQUENCE):
            raise StoreError('storage_recovery_required')
        return int(value)

    @classmethod
    def _ticket(cls, value):
        if (type(value) is not dict or set(value) != {'version', 'sequence', 'nonce', 'kind',
                'bindingDigest', 'authorizationEpoch', 'priorStoreRevision', 'sourceRevision'}
                or type(value['version']) is not int or value['version'] != 1
                or cls._sequence(value['sequence']) == 0 or not cls._hex64(value['nonce'])
                or value['kind'] not in {'refresh', 'revoke'} or not cls._hex64(value['bindingDigest'])
                or not cls._hex64(value['sourceRevision'])
                or type(value['priorStoreRevision']) is not int
                or not 1 <= value['priorStoreRevision'] < MAX_SEQUENCE):
            raise StoreError('ticket_conflict')
        _canonical_epoch(value['authorizationEpoch'])
        return value

    @classmethod
    def _ticket_fingerprint(cls, ticket):
        cls._ticket(ticket)
        return cls._digest_value('ticket', ticket)

    @staticmethod
    def _receipt(kind, revision, *, remote='not_applicable'):
        return {'kind': kind, 'revision': revision, 'localRetired': kind in {'revoke', 'local'},
                'remoteRevocation': remote}

    @staticmethod
    def _validate_receipt(receipt):
        if (type(receipt) is not dict or set(receipt) != {'kind', 'revision', 'localRetired', 'remoteRevocation'}
                or receipt['kind'] not in {'refresh', 'revoke', 'local'}
                or type(receipt['revision']) is not int or not 0 <= receipt['revision'] <= MAX_SEQUENCE
                or type(receipt['localRetired']) is not bool
                or receipt['localRetired'] != (receipt['kind'] in {'revoke', 'local'})
                or receipt['remoteRevocation'] not in {'not_applicable', 'unconfirmed', 'confirmed'}
                or (receipt['kind'] == 'refresh' and receipt['remoteRevocation'] != 'not_applicable')
                or (receipt['kind'] != 'refresh' and receipt['remoteRevocation'] == 'not_applicable')):
            raise StoreError('storage_recovery_required')

    def _validate_window(self, window):
        if (type(window) is not dict or set(window) != {'ticket', 'stage', 'intentDigest', 'outcome', 'admissionProofDigest'}
                or window['stage'] not in {'allocating', 'prepared', 'admitted', 'completed', 'uncertain'}
                or not self._hex64(window['intentDigest'])):
            raise StoreError('storage_recovery_required')
        self._ticket(window['ticket'])
        if (window['stage'] in {'allocating', 'prepared'} and window['admissionProofDigest'] is not None
                or window['stage'] not in {'allocating', 'prepared'} and not self._hex64(window['admissionProofDigest'])):
            raise StoreError('storage_recovery_required')
        if window['outcome'] is not None:
            self._validate_receipt(window['outcome'])
        if window['stage'] in {'allocating', 'prepared'} and window['outcome'] is not None:
            raise StoreError('storage_recovery_required')
        if window['stage'] == 'completed' and window['outcome'] is None:
            raise StoreError('storage_recovery_required')

    def _validate_head(self, envelope):
        body = self._uncapsule('head', envelope)
        if (set(body) != {'version', 'authorityId', 'scopeDigest', 'headId', 'previousHeadDigest',
                'binding', 'revision', 'phase', 'target', 'authorizationEpoch',
                'issuedThrough', 'retiredThrough', 'ticketWindow', 'candidate', 'cleanup', 'grantEnvelopeDigest'}
                or type(body['version']) is not int or body['version'] != 2
                or not self._hex64(body['scopeDigest']) or not self._hex64(body['previousHeadDigest'])
                or type(body['revision']) is not int or not 0 <= body['revision'] <= MAX_SEQUENCE
                or body['phase'] not in {'active', 'refreshing', 'disconnected'}):
            raise StoreError('storage_recovery_required')
        _canonical_epoch(body['authorityId'])
        _canonical_epoch(body['headId'])
        binding = TrustedBinding(**body['binding'])
        self._validate(binding)
        self._check_scope(body['binding'])
        if body['authorizationEpoch'] is not None:
            _canonical_epoch(body['authorizationEpoch'])
        if body['phase'] == 'disconnected':
            if body['target'] is not None:
                raise StoreError('storage_recovery_required')
        else:
            self._target(body['target'])
            if body['authorizationEpoch'] is None or body['revision'] == 0:
                raise StoreError('storage_recovery_required')
        self._validate_credentials(body)
        issued, retired = self._sequence(body['issuedThrough']), self._sequence(body['retiredThrough'])
        if retired > issued:
            raise StoreError('storage_recovery_required')
        window = body['ticketWindow']
        if window is None:
            if retired != issued or body['phase'] == 'refreshing':
                raise StoreError('storage_recovery_required')
        else:
            self._validate_window(window)
            ticket = window['ticket']
            if (self._sequence(ticket['sequence']) != issued or issued <= retired
                    or ticket['bindingDigest'] != self._binding_digest(binding)
                    or ticket['authorizationEpoch'] != body['authorizationEpoch']):
                raise StoreError('storage_recovery_required')
            if window['outcome'] is not None and (window['outcome']['kind'] != ticket['kind'] or window['outcome']['revision'] != body['revision']):
                raise StoreError('storage_recovery_required')
            prior, stage = ticket['priorStoreRevision'], window['stage']
            if stage in {'allocating', 'prepared'}:
                valid = body['revision'] == prior and body['phase'] == 'active'
            elif ticket['kind'] == 'refresh':
                valid = ((stage in {'admitted', 'uncertain'} and body['revision'] == prior
                          and body['phase'] == 'refreshing') or
                         (stage == 'completed' and body['revision'] == prior + 1 and body['phase'] == 'active'))
            else:
                valid = body['revision'] == prior + 1 and body['phase'] == 'disconnected'
            if not valid or (window['outcome'] is not None and window['outcome']['revision'] != body['revision']):
                raise StoreError('storage_recovery_required')
        return body

    def _scope_digest(self, binding, authority_id):
        return self._digest_value('scope', {'authorityId': authority_id, 'scope': self._scope(asdict(binding))})

    def _anchor(self, binding):
        value = self._read_capsule('ticket-anchor.json', 'anchor')
        body = value['body']
        if (set(body) != {'version', 'purpose', 'authorityId', 'scope', 'bootstrapCurrentDigest', 'initialHead'}
                or type(body['version']) is not int or body['version'] != 1
                or body['purpose'] != 'eidolon.setup.subscription.ticket-authority'
                or body['scope'] != self._scope(asdict(binding)) or not self._hex64(body['bootstrapCurrentDigest'])):
            raise StoreError('storage_recovery_required')
        _canonical_epoch(body['authorityId'])
        initial = self._validate_head(body['initialHead'])
        if (initial['authorityId'] != body['authorityId']
                or initial['scopeDigest'] != self._scope_digest(binding, body['authorityId'])
                or initial['previousHeadDigest'] != body['bootstrapCurrentDigest']
                or initial['issuedThrough'] != '0' or initial['retiredThrough'] != '0'
                or initial['ticketWindow'] is not None or initial['phase'] != 'disconnected'):
            raise StoreError('storage_recovery_required')
        return value

    def _archive_directory(self, *, create=False):
        path = self.directory / TICKET_ARCHIVE
        if create and not path.exists() and not path.is_symlink():
            path.mkdir(mode=0o700)
            self._sync_directory()
        self._check(path, directory=True)
        return path

    def _archive_read(self, name, limit=MAX_TRANSITION_BYTES):
        self._archive_directory()
        if '/' in name or '\\' in name or not name:
            raise StoreError('storage_recovery_required')
        return self._read(TICKET_ARCHIVE + '/' + name, limit)

    @staticmethod
    def _stage_name(name, digest):
        return 'stage-' + hashlib.sha256((name + '\0' + digest).encode('ascii')).hexdigest() + '.staging'

    def _archive_sync(self):
        directory = self._archive_directory()
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _stage_read(self, path, *, links):
        # The only two-link exception is scoped to a separately verified exact
        # staging/final inode pair. Ordinary readers still require nlink==1.
        self._archive_directory()
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) & 0o077 or info.st_nlink not in links
                or info.st_size > MAX_TRANSITION_BYTES):
            raise StoreError('unsafe_filesystem')
        if not self._synthetic:
            _check_darwin_acl(path)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            actual = os.fstat(stream.fileno())
            if (actual.st_dev, actual.st_ino, actual.st_nlink) != (info.st_dev, info.st_ino, info.st_nlink):
                raise StoreError('unsafe_filesystem')
            data = stream.read(MAX_TRANSITION_BYTES + 1)
            os.fsync(stream.fileno())
        after = path.lstat()
        if (after.st_dev, after.st_ino, after.st_nlink, after.st_size) != (info.st_dev, info.st_ino, info.st_nlink, info.st_size):
            raise StoreError('unsafe_filesystem')
        return info, data

    def _repair_stage(self, name, digest, *, material=None):
        directory = self._archive_directory()
        destination, stage = directory / name, directory / self._stage_name(name, digest)
        final_exists = destination.exists() or destination.is_symlink()
        stage_exists = stage.exists() or stage.is_symlink()
        if final_exists:
            final_info, data = self._stage_read(destination, links={1, 2})
            if hashlib.sha256(data).hexdigest() != digest:
                raise StoreError('storage_recovery_required')
            if final_info.st_nlink == 2:
                if not stage_exists:
                    raise StoreError('unsafe_filesystem')
                stage_info, staged = self._stage_read(stage, links={2})
                if ((stage_info.st_dev, stage_info.st_ino) != (final_info.st_dev, final_info.st_ino)
                        or staged != data):
                    raise StoreError('unsafe_filesystem')
                stage.unlink()
            elif stage_exists:
                raise StoreError('unsafe_filesystem')
            self._archive_sync()
            if self._archive_read(name) != data:
                raise StoreError('publication_failed')
            return True
        if stage_exists:
            _, staged = self._stage_read(stage, links={1})
            if hashlib.sha256(staged).hexdigest() != digest:
                if material is None:
                    # A candidate cipher cannot be recreated after loss. Its
                    # exact native handle must instead be retired by cleanup.
                    return False
                stage.unlink()
                self._archive_sync()
                return False
            os.link(stage, destination, follow_symlinks=False)
            stage.unlink()
            self._archive_sync()
            if self._archive_read(name) != staged:
                raise StoreError('publication_failed')
            return True
        return False

    def _archive_put(self, name, data):
        directory = self._archive_directory()
        if '/' in name or '\\' in name or not name or len(data) > MAX_TRANSITION_BYTES:
            raise StoreError('storage_recovery_required')
        digest = hashlib.sha256(data).hexdigest()
        if self._repair_stage(name, digest, material=data):
            return
        temporary = directory / self._stage_name(name, digest)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, directory / name, follow_symlinks=False)
        temporary.unlink()
        self._archive_sync()
        if self._archive_read(name) != data:
            raise StoreError('publication_failed')

    def _intent_value(self, body, ticket, prior_target):
        return {'version': 1, 'authorityId': body['authorityId'], 'scopeDigest': body['scopeDigest'],
                'ticket': ticket, 'priorTarget': prior_target}

    def _materialization(self, role, value):
        return {'role': role, 'value': value, 'digest': self._digest_value(role, value)}

    def _material_name(self, item, authority):
        if (type(item) is not dict or set(item) != {'role', 'value', 'digest'}
                or item['role'] not in {'intent', 'window', 'grant', 'admission'} or type(item['value']) is not dict
                or item['digest'] != self._digest_value(item['role'], item['value'])):
            raise StoreError('storage_recovery_required')
        value, role = item['value'], item['role']
        if role == 'grant':
            self._validate_record(value)
            if self._scope(value['binding']) != self._scope(authority['binding']):
                raise StoreError('scope_denied')
            return value['target'] + '.journal'
        if role == 'admission':
            if (set(value) != {'version', 'authorityId', 'scopeDigest', 'ticket', 'record',
                              'journalDigest', 'envelopeDigest', 'authorizationEpoch'}
                    or type(value['version']) is not int or value['version'] != 1
                    or value['authorityId'] != authority['authorityId'] or value['scopeDigest'] != authority['scopeDigest']):
                raise StoreError('storage_recovery_required')
            ticket = self._ticket(value['ticket'])
            self._validate_obligation({key: value[key] for key in ('record', 'journalDigest', 'envelopeDigest')}, authority)
            if (value['authorizationEpoch'] != ticket['authorizationEpoch']
                    or value['record']['revision'] != ticket['priorStoreRevision']
                    or self._binding_digest(TrustedBinding(**value['record']['binding'])) != ticket['bindingDigest']):
                raise StoreError('storage_recovery_required')
            return 'admission-' + self._ticket_fingerprint(ticket) + '.json'
        common = {'version', 'authorityId', 'scopeDigest'}
        extra = ({'ticket', 'priorTarget'} if role == 'intent' else
                 {'window', 'revision', 'phase', 'target', 'authorizationEpoch', 'disposition'})
        if (set(value) != common | extra or type(value['version']) is not int or value['version'] != 1
                or value['authorityId'] != authority['authorityId'] or value['scopeDigest'] != authority['scopeDigest']):
            raise StoreError('storage_recovery_required')
        if role == 'intent':
            ticket = self._ticket(value['ticket'])
            self._target(value['priorTarget'])
        else:
            self._validate_window(value['window'])
            ticket = value['window']['ticket']
            if (type(value['revision']) is not int or not 1 <= value['revision'] <= MAX_SEQUENCE
                    or value['phase'] not in {'active', 'refreshing', 'disconnected'}
                    or value['disposition'] not in {'cancelled', 'retired', 'superseded', 'local_retired'}):
                raise StoreError('storage_recovery_required')
            if value['target'] is not None:
                self._target(value['target'])
            _canonical_epoch(value['authorizationEpoch'])
        return role + '-' + self._ticket_fingerprint(ticket) + '.json'

    def _window_archive(self, body, disposition):
        if body['ticketWindow'] is None:
            return []
        value = {key: body[key] for key in ('revision', 'phase', 'target', 'authorizationEpoch')}
        value.update(version=1, authorityId=body['authorityId'], scopeDigest=body['scopeDigest'],
                     window=body['ticketWindow'], disposition=disposition)
        return [self._materialization('window', value)]

    def _current_intent(self, body):
        window = body['ticketWindow']
        if window is None or window['stage'] == 'allocating':
            return None
        name = 'intent-' + self._ticket_fingerprint(window['ticket']) + '.json'
        item = json.loads(self._archive_read(name, 8192), object_pairs_hook=_pairs)
        if self._material_name(item, body) != name or item['role'] != 'intent' or item['digest'] != window['intentDigest']:
            raise StoreError('storage_recovery_required')
        if (item['value']['ticket'] != window['ticket']
                or (window['stage'] in {'prepared', 'allocating'} or
                    (window['ticket']['kind'] == 'refresh' and window['stage'] in {'admitted', 'uncertain'}))
                and item['value']['priorTarget'] != body['target']):
            raise StoreError('storage_recovery_required')
        return item['value']

    def _empty_pending(self, head):
        body = head['body']
        return self._capsule('pending', {'version': 1, 'authorityId': body['authorityId'],
                'scopeDigest': body['scopeDigest'], 'state': 'empty', 'headDigest': head['digest']})

    def _validate_pending(self, value, authority):
        body = self._uncapsule('pending', value)
        common = {'version', 'authorityId', 'scopeDigest', 'state'}
        if (type(body.get('version')) is not int or body['version'] != 1
                or body.get('authorityId') != authority['authorityId']
                or body.get('scopeDigest') != authority['scopeDigest']):
            raise StoreError('storage_recovery_required')
        if body.get('state') == 'empty':
            if set(body) != common | {'headDigest'} or not self._hex64(body['headDigest']):
                raise StoreError('storage_recovery_required')
        elif body.get('state') == 'transition':
            if (set(body) != common | {'transitionId', 'kind', 'oldHeadDigest', 'newHead',
                                      'materializations', 'grantDependency'}
                    or body['kind'] not in {'initialize', 'allocate', 'prepare', 'admit', 'complete',
                                            'cancel', 'supersede', 'local-retire', 'candidate', 'abandon', 'cleanup'}
                    or not self._hex64(body['oldHeadDigest'])
                    or type(body['materializations']) is not list or len(body['materializations']) > 2):
                raise StoreError('storage_recovery_required')
            _canonical_epoch(body['transitionId'])
            new = self._validate_head(body['newHead'])
            if (new['authorityId'] != authority['authorityId'] or new['scopeDigest'] != authority['scopeDigest']
                    or new['previousHeadDigest'] != body['oldHeadDigest']):
                raise StoreError('storage_recovery_required')
            names = [self._material_name(item, authority) for item in body['materializations']]
            if len(set(names)) != len(names):
                raise StoreError('storage_recovery_required')
            dep = body['grantDependency']
            if dep is not None:
                if (type(dep) is not dict or set(dep) != {'target', 'revision', 'binding', 'epoch', 'proof'}
                        or dep['target'] != new['target'] or dep['revision'] != new['revision']
                        or dep['binding'] != new['binding'] or dep['epoch'] != new['authorizationEpoch']
                        or (dep['proof'] is not None and not self._hex64(dep['proof']))):
                    raise StoreError('storage_recovery_required')
                self._target(dep['target'])
        else:
            raise StoreError('storage_recovery_required')
        return body

    def _clear_barrier(self, authority):
        return self._capsule('barrier', {'version': 1, 'authorityId': authority['authorityId'],
                                        'scopeDigest': authority['scopeDigest'], 'state': 'clear'})

    def _barrier(self, authority):
        value = self._read_capsule('ticket-barrier.json', 'barrier')
        body = value['body']
        common = {'version', 'authorityId', 'scopeDigest', 'state'}
        if (type(body.get('version')) is not int or body['version'] != 1
                or body.get('authorityId') != authority['authorityId']
                or body.get('scopeDigest') != authority['scopeDigest']):
            raise StoreError('storage_recovery_required')
        if body['state'] == 'clear':
            if set(body) != common:
                raise StoreError('storage_recovery_required')
        elif body['state'] == 'active':
            if (set(body) != common | {'observedHeadDigest', 'bindingDigest', 'priorRevision',
                    'referencedPending', 'retirementHead', 'materializations'}
                    or not self._hex64(body['observedHeadDigest'])
                    or not self._hex64(body['bindingDigest']) or type(body['priorRevision']) is not int
                    or not 0 <= body['priorRevision'] <= MAX_SEQUENCE
                    or type(body['materializations']) is not list or len(body['materializations']) > 1):
                raise StoreError('storage_recovery_required')
            retired = self._validate_head(body['retirementHead'])
            if (retired['phase'] != 'disconnected' or retired['ticketWindow'] is not None
                    or retired['authorityId'] != authority['authorityId']
                    or retired['scopeDigest'] != authority['scopeDigest']
                    or self._binding_digest(TrustedBinding(**retired['binding'])) != body['bindingDigest']):
                raise StoreError('storage_recovery_required')
            if body['referencedPending'] is not None:
                self._validate_pending(body['referencedPending'], authority)
            for item in body['materializations']:
                if item.get('role') != 'window':
                    raise StoreError('storage_recovery_required')
                self._material_name(item, authority)
        else:
            raise StoreError('storage_recovery_required')
        return body

    def _authority(self, binding, *, allow_pending=False, allow_barrier=False):
        self._validate(binding)
        try:
            head = self._read_capsule('current.json', 'head', MAX_HEAD_BYTES)
            body = self._validate_head(head)
            anchor = self._anchor(binding)['body']
            if (body['authorityId'] != anchor['authorityId'] or body['scopeDigest'] != self._scope_digest(binding, anchor['authorityId'])
                    or body['binding'] != asdict(binding)):
                raise StoreError('scope_denied')
            pending = self._validate_pending(self._read_capsule('ticket-pending.json', 'pending'), body)
            barrier = self._barrier(body)
            if barrier['state'] == 'active' and not allow_barrier:
                raise StoreError('storage_recovery_required')
            if pending['state'] == 'empty':
                if pending['headDigest'] != head['digest']:
                    raise StoreError('storage_recovery_required')
            elif head['digest'] not in {pending['oldHeadDigest'], pending['newHead']['digest']}:
                raise StoreError('storage_recovery_required')
            elif not allow_pending:
                raise StoreError('transition_pending')
            if pending['state'] == 'empty' and barrier['state'] == 'clear':
                self._current_intent(body)
                if body['ticketWindow'] is not None and body['ticketWindow']['admissionProofDigest'] is not None:
                    self._admission_proof(head)
            return head
        except StoreError:
            raise
        except Exception:
            raise StoreError('storage_recovery_required') from None

    def _next_head(self, head, **changes):
        body = dict(head['body'], headId=str(uuid.uuid4()), previousHeadDigest=head['digest'])
        body.update(changes)
        value = self._capsule('head', body)
        self._validate_head(value)
        return value

    def _grant_payload(self, head):
        body = head['body']
        binding = TrustedBinding(**body['binding'])
        if body['phase'] not in {'active', 'refreshing'}:
            raise StoreError('connection_unavailable')
        payload = _parse_grant(self._decrypt(body), binding)
        if payload['tokenRevision'] != body['revision'] or payload['authorizationEpoch'] != body['authorizationEpoch']:
            raise StoreError('authentication_failed')
        return payload

    def _check_grant_dependency(self, plan):
        dep = plan['grantDependency']
        if dep is None:
            return
        payload = self._grant_payload(plan['newHead'])
        if dep['proof'] is None:
            if payload['rotation'] is not None:
                raise StoreError('authentication_failed')
        elif payload['rotation'] != {'refreshAttemptId': dep['proof'], 'priorTokenRevision': dep['revision'] - 1}:
            raise StoreError('authentication_failed')

    def _check_transition_evidence(self, plan):
        new = plan['newHead']['body']
        kind, window = plan['kind'], new['ticketWindow']
        expected = {'allocate': 'allocating', 'prepare': 'prepared', 'admit': 'admitted'}
        if kind in expected and (window is None or window['stage'] != expected[kind]):
            raise StoreError('storage_recovery_required')
        if kind in {'cancel', 'supersede', 'local-retire', 'initialize'} and window is not None:
            raise StoreError('storage_recovery_required')
        if kind == 'complete' and (window is None or window['stage'] not in {'completed', 'uncertain'}):
            raise StoreError('storage_recovery_required')
        if kind in {'supersede'} or (kind == 'complete' and window['ticket']['kind'] == 'refresh'):
            if plan['grantDependency'] is None:
                raise StoreError('storage_recovery_required')
        elif plan['grantDependency'] is not None:
            raise StoreError('storage_recovery_required')
        if kind in {'candidate', 'abandon', 'cleanup'}:
            return
        if kind in {'initialize', 'local-retire'}:
            if new['phase'] != 'disconnected':
                raise StoreError('storage_recovery_required')
            return
        if new['phase'] in {'active', 'refreshing'}:
            self._grant_payload(plan['newHead'])
        elif window is not None:
            self._admission_proof(plan['newHead'])

    def _finish_transition(self, pending, *, guard=None, cancel=None):
        plan = self._validate_pending(pending, pending['body']['newHead']['body'])
        if plan['state'] != 'transition':
            raise StoreError('storage_recovery_required')
        current = json.loads(self._read('current.json', MAX_HEAD_BYTES), object_pairs_hook=_pairs)
        digest = (current['digest'] if type(current) is dict and set(current) == {'body', 'digest'}
                  else self._digest_value('bootstrap', current))
        if digest not in {plan['oldHeadDigest'], plan['newHead']['digest']}:
            raise StoreError('storage_recovery_required')
        if guard is not None:
            self._check_guard(guard, cancel)
        for item in plan['materializations']:
            name = self._material_name(item, plan['newHead']['body'])
            self._archive_put(name, _json(item['value'] if item['role'] == 'grant' else item))
        self._check_transition_evidence(plan)
        self._check_grant_dependency(plan)
        if guard is not None:
            self._check_guard(guard, cancel)
        if digest == plan['oldHeadDigest']:
            self._write('current.json', _json(plan['newHead']), replace=True)
        if self._read_capsule('current.json', 'head', MAX_HEAD_BYTES) != plan['newHead']:
            raise StoreError('publication_failed')
        self._archive_put('transition-' + pending['digest'] + '.json', _json(pending))
        empty = self._empty_pending(plan['newHead'])
        self._write('ticket-pending.json', _json(empty), replace=True)
        if self._read_capsule('ticket-pending.json', 'pending') != empty:
            raise StoreError('publication_failed')
        return plan['newHead']

    def _transition(self, head, new, kind, *, materials=(), grant=None, guard=None, cancel=None):
        if self._read_capsule('ticket-pending.json', 'pending') != self._empty_pending(head):
            raise StoreError('transition_pending')
        if self._read_capsule('current.json', 'head', MAX_HEAD_BYTES) != head:
            raise StoreError('stale_revision')
        body = new['body']
        pending = self._capsule('pending', {'version': 1, 'authorityId': body['authorityId'],
            'scopeDigest': body['scopeDigest'], 'state': 'transition', 'transitionId': str(uuid.uuid4()),
            'kind': kind, 'oldHeadDigest': head['digest'], 'newHead': new,
            'materializations': list(materials), 'grantDependency': grant})
        self._validate_pending(pending, body)
        if guard is not None:
            self._check_guard(guard, cancel)
        try:
            self._write('ticket-pending.json', _json(pending), replace=True)
            if self._read_capsule('ticket-pending.json', 'pending') != pending:
                raise StoreError('publication_failed')
            return self._finish_transition(pending, guard=guard, cancel=cancel)
        except Exception:
            raise StoreError('publication_uncertain') from None

    def _history_scope(self, binding):
        return hashlib.sha256(_json(self._scope(asdict(binding)))).hexdigest()

    def _history_artifacts(self):
        # Legacy bootstrap only. Never enumerate growing ticket archives.
        names = ('history-anchor.json', 'history-index.json', 'history-v1-bootstrap.json', 'ticket-anchor.json')
        return {name for name in names if (self.directory / name).exists() or (self.directory / name).is_symlink()}

    def _bootstrap_eligible(self, binding, state, *, initial=False):
        if state['phase'] != 'disconnected':
            raise StoreError('storage_recovery_required')
        if state['revision'] == 0:
            if not initial:
                names = {path.name for path in self.directory.iterdir()}
                if names != {'lock', 'identity.json', 'current.json'}:
                    raise StoreError('storage_recovery_required')
            return
        if initial:
            marker = self._read_json('history-v1-bootstrap.json')
            proxy = dict(state, localRetirement={
                'historyReason': 'reauthorization_required', 'priorRevision': marker.get('priorRevision'),
                'priorTarget': marker.get('priorTarget')})
            self._validate_v1_bootstrap(binding, proxy)
        else:
            self._validate_v1_bootstrap(binding, state)
            if self._history_artifacts() != {'history-v1-bootstrap.json'}:
                raise StoreError('storage_recovery_required')

    def _initialize_authority(self, binding, state):
        anchor_path = self.directory / 'ticket-anchor.json'
        if anchor_path.exists() or anchor_path.is_symlink():
            return self._recover_initialization(binding)
        self._bootstrap_eligible(binding, state)
        authority_id = str(uuid.uuid4())
        prior = self._digest_value('bootstrap', state)
        body = {'version': 2, 'authorityId': authority_id,
                'scopeDigest': self._scope_digest(binding, authority_id), 'headId': str(uuid.uuid4()),
                'previousHeadDigest': prior, 'binding': state['binding'], 'revision': state['revision'],
                'phase': 'disconnected', 'target': None, 'authorizationEpoch': None,
                'issuedThrough': '0', 'retiredThrough': '0', 'ticketWindow': None,
                'candidate': {'state': 'idle'}, 'cleanup': [], 'grantEnvelopeDigest': None}
        initial = self._capsule('head', body)
        anchor = self._capsule('anchor', {'version': 1,
            'purpose': 'eidolon.setup.subscription.ticket-authority', 'authorityId': authority_id,
            'scope': self._scope(asdict(binding)), 'bootstrapCurrentDigest': prior, 'initialHead': initial})
        self._write('ticket-anchor.json', _json(anchor))
        if self._read_capsule('ticket-anchor.json', 'anchor') != anchor:
            raise StoreError('publication_failed')
        return self._recover_initialization(binding)

    def _recover_initialization(self, binding):
        anchor = self._anchor(binding)['body']
        initial = anchor['initialHead']
        current = json.loads(self._read('current.json', MAX_HEAD_BYTES), object_pairs_hook=_pairs)
        if current == initial:
            self._bootstrap_eligible(TrustedBinding(**initial['body']['binding']), initial['body'], initial=True)
        elif self._digest_value('bootstrap', current) == anchor['bootstrapCurrentDigest']:
            self._bootstrap_eligible_for_anchor(binding, current)
        else:
            raise StoreError('storage_recovery_required')
        self._archive_directory(create=True)
        clear = self._clear_barrier(initial['body'])
        barrier_path = self.directory / 'ticket-barrier.json'
        if not barrier_path.exists() and not barrier_path.is_symlink():
            self._write('ticket-barrier.json', _json(clear))
        if self._read_capsule('ticket-barrier.json', 'barrier') != clear:
            raise StoreError('storage_recovery_required')
        pending_path = self.directory / 'ticket-pending.json'
        if pending_path.exists() or pending_path.is_symlink():
            pending = self._read_capsule('ticket-pending.json', 'pending')
            plan = self._validate_pending(pending, initial['body'])
            if plan['state'] == 'empty':
                if current != initial or pending != self._empty_pending(initial):
                    raise StoreError('storage_recovery_required')
                return initial
            if (plan['kind'] != 'initialize' or plan['newHead'] != initial
                    or plan['oldHeadDigest'] != anchor['bootstrapCurrentDigest']
                    or plan['materializations'] or plan['grantDependency'] is not None):
                raise StoreError('storage_recovery_required')
        else:
            pending = self._capsule('pending', {'version': 1, 'authorityId': initial['body']['authorityId'],
                'scopeDigest': initial['body']['scopeDigest'], 'state': 'transition',
                'transitionId': str(uuid.uuid4()), 'kind': 'initialize',
                'oldHeadDigest': anchor['bootstrapCurrentDigest'], 'newHead': initial,
                'materializations': [], 'grantDependency': None})
            self._write('ticket-pending.json', _json(pending))
        if self._read_capsule('ticket-pending.json', 'pending') != pending:
            raise StoreError('publication_failed')
        return self._finish_transition(pending)

    def _bootstrap_eligible_for_anchor(self, binding, state):
        # Exact anchor preimage was already compared; still repeat trusted scope
        # and the explicit V1 retirement proof instead of inferring from a floor.
        if (type(state) is not dict or state.get('binding') != asdict(binding)
                or state.get('phase') != 'disconnected' or state.get('target') is not None
                or type(state.get('revision')) is not int):
            raise StoreError('storage_recovery_required')
        self._check_scope(state['binding'])
        if state['revision'] != 0:
            self._validate_v1_bootstrap(binding, state)

    def _publish_candidate(self, binding, secret, head, purpose, *, guard=None, cancel=None):
        body = head['body']
        if body['binding'] != asdict(binding):
            raise StoreError('scope_denied')
        if body['candidate']['state'] != 'idle' or body['cleanup']:
            raise StoreError('storage_recovery_required')
        revision = body['revision'] + 1
        payload = _parse_grant(secret, binding)
        if payload['tokenRevision'] != revision:
            raise StoreError('invalid_grant')
        target = uuid.uuid4().hex
        native = ValueBinding(binding.organization_id, binding.registration_id, revision, binding.owner_id, target)
        record = {'version': ENVELOPE_VERSION, 'revision': revision, 'binding': asdict(binding),
                  'native_binding': asdict(native), 'reference': binding_reference(native), 'target': target}
        key, nonce = os.urandom(32), os.urandom(12)
        encrypted = nonce + AESGCM(key).encrypt(nonce, secret, self._aad(record))
        digest = hashlib.sha256(encrypted).hexdigest()
        candidate = {'state': 'reserved', 'purpose': purpose, 'record': record,
                     'authorizationEpoch': payload['authorizationEpoch'], 'envelopeDigest': digest}
        reserved = self._next_head(head, candidate=candidate)
        # This exact target is in fixed pending/current authority before the
        # sole native put. Recovery never calls put, even after an ambiguous put.
        self._transition(head, reserved, 'candidate', materials=[self._materialization('grant', record)], guard=guard, cancel=cancel)
        if guard is not None:
            self._check_guard(guard, cancel)
        if self._vault().put(native, key) != record['reference']:
            raise StoreError('native_reference_mismatch')
        self._archive_put(target + '.enc', encrypted)
        check = dict(reserved['body'], revision=revision, target=target, phase='active', grantEnvelopeDigest=digest)
        if self._decrypt(check) != secret:
            raise StoreError('authentication_failed')
        return reserved, target, digest

    def _connect_ticket_grant(self, binding, secret, state):
        if state['version'] != 2:
            head = self._initialize_authority(TrustedBinding(**state['binding']), state)
        else:
            head = self._authority(TrustedBinding(**state['binding']))
        body = head['body']
        if body['phase'] not in {'disconnected', 'refreshing'}:
            raise StoreError('disconnect_required')
        payload = _parse_grant(secret, binding)
        if payload['tokenRevision'] != body['revision'] + 1:
            raise StoreError('stale_revision')
        head, target, digest = self._publish_candidate(binding, secret, head, 'consent')
        new = self._next_head(head, binding=asdict(binding), revision=body['revision'] + 1,
                phase='active', target=target, authorizationEpoch=payload['authorizationEpoch'],
                retiredThrough=body['issuedThrough'], ticketWindow=None, candidate={'state': 'idle'},
                cleanup=self._grant_obligation(body), grantEnvelopeDigest=digest)
        dep = {'target': target, 'revision': new['body']['revision'], 'binding': asdict(binding),
               'epoch': payload['authorizationEpoch'], 'proof': None}
        self._retire_leases()
        self._transition(head, new, 'supersede', materials=self._window_archive(body, 'superseded'), grant=dep)
        self._try_cleanup(new)
        return new['body']['revision']

    def _result(self, head, *, local=False):
        body, window = head['body'], head['body']['ticketWindow']
        return {'state': 'local_retired' if local else window['stage'] if window else 'none',
                'ticket': None if local or window is None else window['ticket'],
                'receipt': self._receipt('local', body['revision'], remote='unconfirmed') if local else
                           window['outcome'] if window else None,
                'revision': body['revision'], 'verification': 'verified_current'}

    def _matching_ticket(self, head, ticket):
        self._ticket(ticket)
        body, window = head['body'], head['body']['ticketWindow']
        if self._sequence(ticket['sequence']) <= self._sequence(body['retiredThrough']):
            raise StoreError('ticket_retired')
        if window is None or window['ticket'] != ticket:
            raise StoreError('ticket_conflict')
        return window

    def _check_ticket_lease(self, binding, ticket):
        head = self._authority(binding)
        window = self._matching_ticket(head, ticket)
        if (window['stage'] != 'admitted' or head['body']['candidate']['state'] != 'idle'
                or head['body']['cleanup']):
            raise StoreError('lease_cancelled')
        return head

    @_guarded
    def grant_descriptor(self, binding, revision):
        with self._locked() as exists:
            if not exists:
                raise StoreError('connection_unavailable')
            state = self._fence(binding, revision)
            if state['version'] != 2:
                _parse_grant(self._decrypt(state), binding)
                raise StoreError('storage_recovery_required')
            head = self._authority(binding)
            payload = self._grant_payload(head)
            self._fence(binding, revision)
            return {key: payload[key] for key in ('authorizationEpoch', 'tokenRevision', 'expiresAt',
                                                'consent', 'identityEvidence', 'grantedScopes')}

    @_guarded
    def prepare_ticket(self, binding, revision, *, kind, source_revision, owner_guard, cancel):
        if kind not in {'refresh', 'revoke'} or not self._hex64(source_revision):
            raise StoreError('ticket_conflict')
        with self._locked() as exists:
            if not exists:
                raise StoreError('connection_unavailable')
            self._check_guard(owner_guard, cancel)
            head = self._authority(binding)
            body, window = head['body'], head['body']['ticketWindow']
            if body['cleanup'] or body['candidate']['state'] != 'idle':
                raise StoreError('storage_recovery_required')
            if window is not None:
                prior = window['ticket']
                if (prior['kind'] == kind and prior['priorStoreRevision'] == revision
                        and prior['sourceRevision'] == source_revision
                        and prior['bindingDigest'] == self._binding_digest(binding)):
                    if window['stage'] == 'allocating':
                        head = self._finish_prepare(head, owner_guard, cancel)
                    if head['body']['phase'] in {'active', 'refreshing'}:
                        self._grant_payload(head)
                    elif prior['kind'] == 'revoke':
                        self._verify_revoke_evidence(head)
                    return dict(head['body']['ticketWindow']['ticket'])
                if window['stage'] != 'completed':
                    raise StoreError('ticket_conflict')
            self._cas(body, revision)
            if body['phase'] != 'active':
                raise StoreError('connection_unavailable')
            payload = self._grant_payload(head)
            self._check_guard(owner_guard, cancel)
            issued = self._sequence(body['issuedThrough'])
            if issued >= MAX_SEQUENCE:
                raise StoreError('ticket_sequence_exhausted')
            ticket = {'version': 1, 'sequence': str(issued + 1), 'nonce': os.urandom(32).hex(),
                      'kind': kind, 'bindingDigest': self._binding_digest(binding),
                      'authorizationEpoch': payload['authorizationEpoch'], 'priorStoreRevision': revision,
                      'sourceRevision': source_revision}
            intent = self._intent_value(body, ticket, body['target'])
            window = {'ticket': ticket, 'stage': 'allocating',
                      'intentDigest': self._digest_value('intent', intent), 'outcome': None, 'admissionProofDigest': None}
            new = self._next_head(head, issuedThrough=str(issued + 1), retiredThrough=body['issuedThrough'],
                                  ticketWindow=window)
            self._transition(head, new, 'allocate', materials=self._window_archive(body, 'retired'),
                             guard=owner_guard, cancel=cancel)
            return dict(self._finish_prepare(new, owner_guard, cancel)['body']['ticketWindow']['ticket'])

    def _finish_prepare(self, head, guard, cancel):
        body, window = head['body'], head['body']['ticketWindow']
        if window is None or window['stage'] != 'allocating':
            raise StoreError('ticket_conflict')
        payload = self._grant_payload(head)
        if payload['authorizationEpoch'] != window['ticket']['authorizationEpoch']:
            raise StoreError('authentication_failed')
        intent = self._intent_value(body, window['ticket'], body['target'])
        material = self._materialization('intent', intent)
        if material['digest'] != window['intentDigest']:
            raise StoreError('storage_recovery_required')
        new = self._next_head(head, ticketWindow=dict(window, stage='prepared'))
        return self._transition(head, new, 'prepare', materials=[material], guard=guard, cancel=cancel)

    @_guarded
    def admit_refresh(self, binding, revision, *, ticket, owner_generation, owner_guard, cancel):
        if type(owner_generation) is not object:
            raise StoreError('owner_generation_changed')
        with self._locked() as exists:
            if not exists:
                raise StoreError('connection_unavailable')
            self._check_guard(owner_guard, cancel)
            head = self._authority(binding)
            window = self._matching_ticket(head, ticket)
            self._cas(head['body'], revision)
            if ticket['kind'] != 'refresh' or window['stage'] != 'prepared':
                raise StoreError('ticket_conflict')
            payload = self._grant_payload(head)
            self._check_guard(owner_guard, cancel)
            proof = self._admission_material(head, ticket)
            new = self._next_head(head, phase='refreshing', ticketWindow=dict(window, stage='admitted', admissionProofDigest=proof['digest']))
            self._retire_leases()
            self._transition(head, new, 'admit', materials=[proof], guard=owner_guard, cancel=cancel)
            self._check_guard(owner_guard, cancel)
            return _OperationLease(self, binding, ticket, payload, owner_guard, cancel)

    @_guarded
    def commit_refresh(self, binding, *, ticket, access_token, refresh_token, id_token, expires_at,
                       granted_scopes, identity_evidence, owner_guard, cancel):
        with self._locked() as exists:
            if not exists:
                raise StoreError('connection_unavailable')
            self._check_guard(owner_guard, cancel)
            head = self._check_ticket_lease(binding, ticket)
            if ticket['kind'] != 'refresh':
                raise StoreError('ticket_conflict')
            original = self._grant_payload(head)
            if (_identity_evidence(identity_evidence, binding) != original['identityEvidence']
                    or list(_grant_scopes(granted_scopes)) != original['grantedScopes']):
                raise StoreError('invalid_grant')
            revision = head['body']['revision'] + 1
            replacement = dict(original, tokenRevision=revision,
                rotation={'refreshAttemptId': self._ticket_fingerprint(ticket), 'priorTokenRevision': revision - 1},
                accessToken=access_token, refreshToken=refresh_token,
                idToken=original['idToken'] if id_token is None else id_token, expiresAt=expires_at)
            secret = _GRANT_PREFIX + _json(replacement)
            _parse_grant(secret, binding)
            prior = head['body']
            head, target, digest = self._publish_candidate(binding, secret, head, 'refresh', guard=owner_guard, cancel=cancel)
            receipt = self._receipt('refresh', revision)
            new = self._next_head(head, phase='active', target=target, revision=revision,
                                  candidate={'state': 'idle'}, cleanup=self._grant_obligation(prior), grantEnvelopeDigest=digest,
                                  ticketWindow=dict(head['body']['ticketWindow'], stage='completed', outcome=receipt))
            dep = {'target': target, 'revision': revision, 'binding': asdict(binding),
                   'epoch': ticket['authorizationEpoch'], 'proof': self._ticket_fingerprint(ticket)}
            self._transition(head, new, 'complete', grant=dep, guard=owner_guard, cancel=cancel)
            self._try_cleanup(new)
            return revision

    def _verify_revoke_evidence(self, head):
        self._admission_proof(head)

    def _verify_refresh_commit(self, binding, ticket):
        head = self._authority(binding)
        window = self._matching_ticket(head, ticket)
        if ticket['kind'] != 'refresh' or window['stage'] != 'completed':
            raise StoreError('ticket_conflict')
        payload = self._grant_payload(head)
        if payload['rotation'] != {'refreshAttemptId': self._ticket_fingerprint(ticket),
                                   'priorTokenRevision': ticket['priorStoreRevision']}:
            raise StoreError('authentication_failed')
        return head['body']['revision']

    @_guarded
    def disconnect_grant(self, binding, *, expected_revision, ticket, owner_guard, cancel):
        with self._locked() as exists:
            if not exists:
                raise StoreError('connection_unavailable')
            self._check_guard(owner_guard, cancel)
            head = self._authority(binding)
            window = self._matching_ticket(head, ticket)
            if ticket['kind'] != 'revoke' or ticket['priorStoreRevision'] != expected_revision:
                raise StoreError('ticket_conflict')
            if window['stage'] in {'admitted', 'uncertain', 'completed'}:
                self._verify_revoke_evidence(head)
                return window['outcome'], None
            if window['stage'] != 'prepared':
                raise StoreError('ticket_conflict')
            self._cas(head['body'], expected_revision)
            payload = self._grant_payload(head)
            proof = self._admission_material(head, ticket)
            receipt = self._receipt('revoke', expected_revision + 1, remote='unconfirmed')
            new = self._next_head(head, phase='disconnected', target=None, revision=expected_revision + 1,
                                  grantEnvelopeDigest=None, cleanup=self._grant_obligation(head['body']),
                                  ticketWindow=dict(window, stage='admitted', outcome=receipt, admissionProofDigest=proof['digest']))
            self._retire_leases()
            self._transition(head, new, 'admit', materials=[proof], guard=owner_guard, cancel=cancel)
            self._check_guard(owner_guard, cancel)
            cleaned = self._try_cleanup(new)
            if cleaned['body']['cleanup'] or self._read_capsule('ticket-pending.json', 'pending') != self._empty_pending(cleaned):
                return receipt, None
            return receipt, _OperationLease(self, binding, ticket, payload, owner_guard, cancel)

    def _finish_revocation(self, binding, ticket, confirmed):
        head = self._check_ticket_lease(binding, ticket)
        receipt = self._receipt('revoke', head['body']['revision'], remote='confirmed' if confirmed else 'unconfirmed')
        new = self._next_head(head, ticketWindow=dict(head['body']['ticketWindow'],
                             stage='completed' if confirmed else 'uncertain', outcome=receipt))
        self._transition(head, new, 'complete')
        return receipt

    @_guarded
    def reconcile_ticket(self, binding, *, ticket, owner_guard, cancel):
        with self._locked() as exists:
            if not exists:
                raise StoreError('connection_unavailable')
            self._check_guard(owner_guard, cancel)
            head = self._authority(binding)
            window = self._matching_ticket(head, ticket)
            if head['body']['phase'] in {'active', 'refreshing'}:
                self._grant_payload(head)
            if ticket['kind'] == 'revoke' and head['body']['phase'] == 'disconnected':
                self._verify_revoke_evidence(head)
            if ticket['kind'] == 'refresh' and window['stage'] == 'completed':
                self._verify_refresh_commit(binding, ticket)
            self._check_guard(owner_guard, cancel)
            return self._result(head)

    @_guarded
    def cancel_ticket(self, binding, *, ticket, owner_guard, cancel):
        with self._locked() as exists:
            if not exists:
                raise StoreError('connection_unavailable')
            self._check_guard(owner_guard, cancel)
            head = self._authority(binding)
            window = self._matching_ticket(head, ticket)
            if window['stage'] != 'prepared':
                raise StoreError('ticket_conflict')
            self._grant_payload(head)
            new = self._next_head(head, retiredThrough=head['body']['issuedThrough'], ticketWindow=None)
            self._transition(head, new, 'cancel', materials=self._window_archive(head['body'], 'cancelled'),
                             guard=owner_guard, cancel=cancel)
            self._retire_leases()
            return self._result(new)

    def _local_retire(self, state, reason):
        retired = dict(state, revision=state['revision'] + 1, phase='disconnected', target=None)
        retired.pop('operation', None)
        retired['localRetirement'] = {'priorRevision': state['revision'], 'priorTarget': state['target'],
            'localRetiredAt': self._clock(), 'remoteRevocation': 'unconfirmed', 'historyReason': reason}
        self._write('current.json', _json(retired), replace=True)
        if self._state() != retired:
            raise StoreError('publication_uncertain')
        return {'revision': retired['revision'], 'state': 'disconnected', 'cleanup_pending': self._recover(retired)}

    def _recover_barrier(self, binding, barrier):
        body = barrier['body']
        current = self._read_capsule('current.json', 'head', MAX_HEAD_BYTES)
        self._validate_head(current)
        retired = body['retirementHead']
        pending = self._read_capsule('ticket-pending.json', 'pending')
        self._validate_pending(pending, current['body'])
        if current != retired:
            referenced = body['referencedPending']
            if referenced is None:
                if (current['digest'] != body['observedHeadDigest']
                        or pending != self._empty_pending(current)):
                    raise StoreError('storage_recovery_required')
            if pending['body']['state'] == 'transition':
                if pending != referenced:
                    # The local retirement itself may already be journaled.
                    if (pending['body']['kind'] != 'local-retire'
                            or pending['body']['newHead'] != retired):
                        raise StoreError('storage_recovery_required')
                current = self._finish_transition(pending)
            if current != retired:
                if (current['digest'] != retired['body']['previousHeadDigest']
                        or self._read_capsule('ticket-pending.json', 'pending') != self._empty_pending(current)):
                    raise StoreError('storage_recovery_required')
                current = self._transition(current, retired, 'local-retire', materials=body['materializations'])
        elif pending != self._empty_pending(retired):
            if pending['body']['state'] != 'transition' or pending['body']['newHead'] != retired:
                raise StoreError('storage_recovery_required')
            self._finish_transition(pending)
        if (self._read_capsule('current.json', 'head', MAX_HEAD_BYTES) != retired
                or self._read_capsule('ticket-pending.json', 'pending') != self._empty_pending(retired)):
            raise StoreError('publication_uncertain')
        clear = self._clear_barrier(retired['body'])
        self._write('ticket-barrier.json', _json(clear), replace=True)
        if self._read_capsule('ticket-barrier.json', 'barrier') != clear:
            raise StoreError('publication_uncertain')
        self._try_cleanup(retired)
        return self._receipt('local', retired['body']['revision'], remote='unconfirmed')

    def _local_disconnect_locked(self, binding, state):
        # Current pointer and binding are independently checked even when the
        # pending slot is unavailable. A barrier never grants remote authority.
        self._validate(binding)
        head = self._read_capsule('current.json', 'head', MAX_HEAD_BYTES)
        body = self._validate_head(head)
        if body != state or body['binding'] != asdict(binding):
            raise StoreError('stale_revision')
        self._retire_leases()
        anchor = self._anchor(binding)['body']
        if body['authorityId'] != anchor['authorityId'] or body['scopeDigest'] != self._scope_digest(binding, anchor['authorityId']):
            raise StoreError('scope_denied')
        self._retire_leases()
        try:
            existing = self._barrier(body)
        except StoreError:
            existing = None
        if existing is not None and existing['state'] == 'active':
            return self._recover_barrier(binding, self._capsule('barrier', existing))
        try:
            pending = self._read_capsule('ticket-pending.json', 'pending')
            self._validate_pending(pending, body)
            if pending['body']['state'] == 'empty':
                if pending != self._empty_pending(head):
                    raise StoreError('storage_recovery_required')
                base = head
            else:
                if head['digest'] not in {pending['body']['oldHeadDigest'], pending['body']['newHead']['digest']}:
                    raise StoreError('storage_recovery_required')
                base = pending['body']['newHead']
        except StoreError:
            # Missing provenance is never reconstructed. Persist a local block
            # with no guessed pending reference; recovery requires exact repair.
            pending, base = None, head
        revision = base['body']['revision'] + (base['body']['phase'] != 'disconnected')
        if revision > MAX_SEQUENCE:
            raise StoreError('revision_exhausted')
        retired = self._next_head(base, phase='disconnected', target=None, revision=revision,
                                  retiredThrough=base['body']['issuedThrough'], ticketWindow=None,
                                  candidate={'state': 'idle'}, grantEnvelopeDigest=None,
                                  cleanup=self._retirement_obligations(base['body']))
        barrier = self._capsule('barrier', {'version': 1, 'authorityId': body['authorityId'],
            'scopeDigest': body['scopeDigest'], 'state': 'active', 'observedHeadDigest': head['digest'],
            'bindingDigest': self._binding_digest(binding), 'priorRevision': body['revision'],
            'referencedPending': pending, 'retirementHead': retired,
            'materializations': self._window_archive(base['body'], 'local_retired')})
        try:
            path = self.directory / 'ticket-barrier.json'
            self._write('ticket-barrier.json', _json(barrier), replace=path.exists())
            if self._read_capsule('ticket-barrier.json', 'barrier') != barrier:
                raise StoreError('publication_uncertain')
            self._barrier(body)
            if pending is None:
                # A durable barrier blocks use, but no disconnected successor
                # exists yet. Do not report durable disconnected success.
                raise StoreError('storage_recovery_required')
            return self._recover_barrier(binding, barrier)
        except StoreError:
            raise
        except Exception:
            raise StoreError('publication_uncertain') from None

    @_guarded
    def local_disconnect(self, binding, *, expected_revision, owner_guard, cancel):
        with self._locked() as exists:
            self._check_guard(owner_guard, cancel)
            if not exists:
                if binding is not None or expected_revision != 0:
                    raise StoreError('stale_revision')
                return self._receipt('local', 0, remote='unconfirmed')
            state = self._state()
            self._cas(state, expected_revision)
            if binding is None or state['binding'] != asdict(binding):
                raise StoreError('scope_denied')
            self._validate(binding)
            if state['version'] == 2:
                return self._local_disconnect_locked(binding, state)
            self._retire_leases()
            if state['phase'] != 'disconnected':
                try:
                    self._mark_verified_v1(binding, state, self._decrypt(state))
                except Exception:
                    pass
                if self._state() != state:
                    raise StoreError('stale_revision')
                result = self._local_retire(state, 'reauthorization_required')
                return self._receipt('local', result['revision'], remote='unconfirmed')
            return self._receipt('local', state['revision'], remote='unconfirmed')

    def _reconcile_transition_locked(self, binding):
        # Initialization recovery is allowed only at the anchor's exact initial
        # preimage or initial head. It cannot repair later missing slots.
        anchor = self._anchor(binding)['body']
        raw = json.loads(self._read('current.json', MAX_HEAD_BYTES), object_pairs_hook=_pairs)
        at_initial = raw == anchor['initialHead']
        at_preimage = self._digest_value('bootstrap', raw) == anchor['bootstrapCurrentDigest']
        pending_path = self.directory / 'ticket-pending.json'
        existing = None
        if pending_path.exists() or pending_path.is_symlink():
            existing = self._read_capsule('ticket-pending.json', 'pending')
            self._validate_pending(existing, anchor['initialHead']['body'])
        barrier_path = self.directory / 'ticket-barrier.json'
        active_barrier = False
        if barrier_path.exists() or barrier_path.is_symlink():
            active_barrier = self._barrier(anchor['initialHead']['body'])['state'] == 'active'
        later_pending = (existing is not None and existing['body']['state'] == 'transition'
                         and existing['body']['kind'] != 'initialize')
        if (at_initial or at_preimage) and not later_pending and not active_barrier:
            head = self._recover_initialization(binding)
        else:
            head = self._authority(binding, allow_pending=True, allow_barrier=True)
        barrier = self._read_capsule('ticket-barrier.json', 'barrier')
        if self._barrier(head['body'])['state'] == 'active':
            receipt = self._recover_barrier(binding, barrier)
            return {'state': 'local_retired', 'ticket': None, 'receipt': receipt,
                    'revision': receipt['revision'], 'verification': 'verified_current'}
        pending = self._read_capsule('ticket-pending.json', 'pending')
        if pending['body']['state'] == 'transition':
            head = self._finish_transition(pending)
        head = self._authority(binding)
        head = self._abandon_candidate(head)
        head = self._try_cleanup(head)
        window = head['body']['ticketWindow']
        if window is not None and window['stage'] == 'allocating':
            head = self._finish_prepare(head, lambda: True, threading.Event())
        if head['body']['phase'] in {'active', 'refreshing'}:
            self._grant_payload(head)
        elif window is not None and window['ticket']['kind'] == 'revoke':
            self._verify_revoke_evidence(head)
        # A recovered admission is only evidence. This path never constructs a
        # lease, even when called in the process which originally admitted it.
        return self._result(head, local=head['body']['phase'] == 'disconnected' and window is None and head['body']['revision'] > 0)

    @_guarded
    def reconcile_transition(self, binding, *, owner_guard, cancel):
        with self._locked() as exists:
            if not exists:
                raise StoreError('storage_recovery_required')
            self._check_guard(owner_guard, cancel)
            result = self._reconcile_transition_locked(binding)
            self._check_guard(owner_guard, cancel)
            return result

    @_guarded
    def ticket_status(self):
        with self._locked() as exists:
            result = {'state': 'none', 'ticket': None, 'revision': 0, 'verification': 'metadata_only'}
            if not exists:
                return result
            try:
                state = self._state()
                result['revision'] = state['revision']
                if state['version'] != 2:
                    return result
                head = self._authority(TrustedBinding(**state['binding']), allow_pending=True, allow_barrier=True)
                if (self._barrier(head['body'])['state'] == 'active' or head['body']['cleanup']
                        or head['body']['candidate']['state'] != 'idle'):
                    result['state'] = 'recovery_required'
                    return result
                pending = self._read_capsule('ticket-pending.json', 'pending')
                if pending['body']['state'] != 'empty':
                    result['state'] = 'recovery_required'
                    return result
                view = self._result(head)
                return {key: ('metadata_only' if key == 'verification' else view[key]) for key in ('state', 'ticket', 'revision', 'verification')}
            except Exception:
                result['state'] = 'recovery_required'
                return result

    @_guarded
    def history_status(self):
        with self._locked() as exists:
            if not exists:
                return {'state': 'available', 'issuedThrough': '0', 'retiredThrough': '0'}
            try:
                state = self._state()
                binding = TrustedBinding(**state['binding'])
                if state['version'] == 2:
                    head = self._authority(binding)
                    if head['body']['cleanup'] or head['body']['candidate']['state'] != 'idle':
                        raise StoreError('storage_recovery_required')
                    return {'state': 'available', 'issuedThrough': head['body']['issuedThrough'],
                            'retiredThrough': head['body']['retiredThrough']}
                if state['phase'] != 'disconnected' or (state['revision'] != 0 and not (self.directory / 'history-v1-bootstrap.json').exists()):
                    return {'state': 'legacy', 'issuedThrough': None, 'retiredThrough': None}
                self._bootstrap_eligible(binding, state)
                return {'state': 'available', 'issuedThrough': '0', 'retiredThrough': '0'}
            except Exception:
                return {'state': 'recovery_required', 'issuedThrough': None, 'retiredThrough': None}

    def _validate_record(self, record):
        if (type(record) is not dict or set(record) != {'version', 'revision', 'binding', 'native_binding', 'reference', 'target'}
                or type(record['version']) is not int or record['version'] != ENVELOPE_VERSION
                or type(record['revision']) is not int or not 1 <= record['revision'] <= MAX_SEQUENCE):
            raise StoreError('storage_recovery_required')
        binding = TrustedBinding(**record['binding'])
        self._validate(binding)
        self._check_scope(record['binding'])
        self._target(record['target'])
        native = ValueBinding(binding.organization_id, binding.registration_id, record['revision'], binding.owner_id, record['target'])
        if (type(record['native_binding']) is not dict or type(record['native_binding'].get('version')) is not int
                or record['native_binding'] != asdict(native) or record['reference'] != binding_reference(native)):
            raise StoreError('storage_recovery_required')
        return native

    def _validate_obligation(self, item, body):
        if (type(item) is not dict or set(item) != {'record', 'journalDigest', 'envelopeDigest'}
                or not self._hex64(item['envelopeDigest'])
                or item['journalDigest'] != self._digest_value('grant', item['record'])):
            raise StoreError('storage_recovery_required')
        self._validate_record(item['record'])
        if item['record']['binding'] != body['binding']:
            raise StoreError('scope_denied')

    def _validate_credentials(self, body):
        candidate = body['candidate']
        if type(candidate) is not dict or candidate.get('state') not in {'idle', 'reserved'}:
            raise StoreError('storage_recovery_required')
        if candidate['state'] == 'idle':
            if set(candidate) != {'state'}:
                raise StoreError('storage_recovery_required')
        else:
            if (set(candidate) != {'state', 'purpose', 'record', 'authorizationEpoch', 'envelopeDigest'}
                    or candidate['purpose'] not in {'consent', 'refresh'}
                    or not self._hex64(candidate['envelopeDigest'])):
                raise StoreError('storage_recovery_required')
            self._validate_record(candidate['record'])
            _canonical_epoch(candidate['authorizationEpoch'])
            if (candidate['record']['binding'] != body['binding']
                    or candidate['record']['revision'] != body['revision'] + 1
                    or candidate['record']['target'] == body['target']
                    or candidate['purpose'] == 'refresh' and (body['phase'] != 'refreshing' or candidate['authorizationEpoch'] != body['authorizationEpoch'])
                    or candidate['purpose'] == 'consent' and (body['phase'] not in {'disconnected', 'refreshing'} or candidate['authorizationEpoch'] == body['authorizationEpoch'])):
                raise StoreError('storage_recovery_required')
        if (body['phase'] == 'disconnected' and body['grantEnvelopeDigest'] is not None
                or body['phase'] != 'disconnected' and not self._hex64(body['grantEnvelopeDigest'])
                or type(body['cleanup']) is not list or len(body['cleanup']) > 2
                or candidate['state'] == 'reserved' and body['cleanup']):
            raise StoreError('storage_recovery_required')
        targets = []
        for item in body['cleanup']:
            self._validate_obligation(item, body)
            target = item['record']['target']
            if target == body['target'] or target in targets:
                raise StoreError('storage_recovery_required')
            targets.append(target)

    def _grant_obligation(self, body):
        if body['target'] is None:
            return []
        binding = TrustedBinding(**body['binding'])
        native = ValueBinding(binding.organization_id, binding.registration_id, body['revision'], binding.owner_id, body['target'])
        record = {'version': ENVELOPE_VERSION, 'revision': body['revision'], 'binding': body['binding'],
                  'native_binding': asdict(native), 'reference': binding_reference(native), 'target': body['target']}
        return [{'record': record, 'journalDigest': self._digest_value('grant', record),
                 'envelopeDigest': body['grantEnvelopeDigest']}]

    def _retirement_obligations(self, body):
        items = list(body['cleanup']) + self._grant_obligation(body)
        candidate = body['candidate']
        if candidate['state'] == 'reserved':
            record = candidate['record']
            items.append({'record': record, 'journalDigest': self._digest_value('grant', record),
                          'envelopeDigest': candidate['envelopeDigest']})
        unique = {item['record']['target']: item for item in items}
        if len(unique) > 2:
            raise StoreError('storage_recovery_required')
        return list(unique.values())

    def _admission_material(self, head, ticket):
        body = head['body']
        obligation = self._grant_obligation(body)[0]
        value = dict(obligation, version=1, authorityId=body['authorityId'], scopeDigest=body['scopeDigest'],
                     ticket=ticket, authorizationEpoch=body['authorizationEpoch'])
        return self._materialization('admission', value)

    def _admission_proof(self, head):
        body, window = head['body'], head['body']['ticketWindow']
        if window is None or window['admissionProofDigest'] is None:
            raise StoreError('storage_recovery_required')
        name = 'admission-' + self._ticket_fingerprint(window['ticket']) + '.json'
        item = json.loads(self._archive_read(name), object_pairs_hook=_pairs)
        if (self._material_name(item, body) != name or item['digest'] != window['admissionProofDigest']
                or item['value']['ticket'] != window['ticket']):
            raise StoreError('storage_recovery_required')
        # The proof is protected, integrity-checked retirement evidence. It is
        # never token authority and can never reconstruct a lease after restart.
        return item['value']

    def _drain_cleanup(self, head):
        while head['body']['cleanup']:
            if self._read_capsule('ticket-pending.json', 'pending') != self._empty_pending(head):
                raise StoreError('transition_pending')
            item = head['body']['cleanup'][0]
            record = item['record']
            native = self._validate_record(record)
            if record['target'] == head['body']['target']:
                raise StoreError('storage_recovery_required')
            self._archive_put(record['target'] + '.journal', _json(record))
            # Delete is exact and idempotent by the native backend contract.
            # Never unlink ciphertext first or discard the durable handle.
            self._vault().delete(record['reference'], native)
            name = record['target'] + '.enc'
            directory = self._archive_directory()
            self._repair_stage(name, item['envelopeDigest'])
            final = directory / name
            stage = directory / self._stage_name(name, item['envelopeDigest'])
            for path in (final, stage):
                if path.exists() or path.is_symlink():
                    self._check(path)
                    path.unlink()
                    self._archive_sync()
                if path.exists() or path.is_symlink():
                    raise StoreError('publication_failed')
            new = self._next_head(head, cleanup=head['body']['cleanup'][1:])
            head = self._transition(head, new, 'cleanup')
        return head

    def _abandon_candidate(self, head):
        candidate = head['body']['candidate']
        if candidate['state'] == 'idle':
            return head
        record = candidate['record']
        obligation = {'record': record, 'journalDigest': self._digest_value('grant', record),
                      'envelopeDigest': candidate['envelopeDigest']}
        new = self._next_head(head, candidate={'state': 'idle'}, cleanup=[obligation])
        return self._transition(head, new, 'abandon')

    def _try_cleanup(self, head):
        try:
            return self._drain_cleanup(head)
        except Exception:
            # Publication/retirement is already durable. Keep its receipt and
            # exact obligations visible; failure is not proof of cleanup.
            return head
