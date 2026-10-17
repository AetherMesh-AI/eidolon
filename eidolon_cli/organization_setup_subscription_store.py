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
import os
from pathlib import Path
import stat
import sys
import threading
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
_ERROR_REASONS = frozenset({
    'authentication_failed', 'backend_unavailable', 'connection_unavailable',
    'corrupt_store', 'disconnect_required', 'filesystem_protection_unavailable',
    'invalid_binding', 'invalid_secret', 'native_reference_mismatch',
    'operation_failed', 'publication_failed', 'publication_uncertain', 'recovery_required', 'recursive_use',
    'revision_exhausted', 'revision_exists', 'scope_denied', 'stale_revision',
    'unsafe_filesystem', 'value_too_large',
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


def _check_darwin_acl(path):
    """Reject extended ALLOW entries, including inherited grants; never edit ACLs.

    Darwin ACL APIs return -1/EINVAL at the end, unlike Linux's API. Constants
    and semantics: Apple's Libc include/sys/acl.h and posix1e/acl_entry.c.
    Missing/unsupported ACL inspection fails closed, even on a mode-0700 path.
    """
    api = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
    api.acl_get_link_np.argtypes = [ctypes.c_char_p, ctypes.c_int]
    api.acl_get_link_np.restype = ctypes.c_void_p
    api.acl_valid.argtypes = [ctypes.c_void_p]
    api.acl_valid.restype = ctypes.c_int
    api.acl_get_entry.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
    api.acl_get_entry.restype = ctypes.c_int
    api.acl_get_tag_type.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
    api.acl_get_tag_type.restype = ctypes.c_int
    api.acl_free.argtypes = [ctypes.c_void_p]
    api.acl_free.restype = ctypes.c_int
    acl = api.acl_get_link_np(os.fsencode(path), 0x100)  # ACL_TYPE_EXTENDED
    if not acl:
        raise StoreError('filesystem_protection_unavailable')
    try:
        if api.acl_valid(acl) != 0:
            raise StoreError('filesystem_protection_unavailable')
        entry, index = ctypes.c_void_p(), 0  # ACL_FIRST_ENTRY
        for _ in range(129):  # Darwin's published ACL_MAX_ENTRIES is 128.
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
        api.acl_free(acl)


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
    def __init__(self, profile_root, *, backend=None):
        self.profile = Path(os.path.abspath(os.fspath(profile_root)))
        self.directory = self.profile / PRIVATE_STORE_NAME
        self._backend = backend
        self._synthetic = backend is not None

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
    def connect(self, binding, secret, *, expected_revision):
        """Explicitly consented connection, after the external owner receipt gate."""
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
    def consume(self, binding, revision, consumer):
        """Fenced callback only; do not return or retain secret bytes in consumers."""
        with self._locked() as exists:
            if not exists:
                raise StoreError('connection_unavailable')
            state = self._fence(binding, revision)
            secret = self._decrypt(state)
            self._fence(binding, revision)
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
            pending = dict(state, phase='refreshing')
            self._write('current.json', _json(pending), replace=True)
            self._fence(binding, revision, phase='refreshing')
            replacement = rotator(secret)
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
            if state['phase'] not in {'absent', 'disconnected'}:
                if state['revision'] + 1 >= 2**63:
                    raise StoreError('revision_exhausted')
                state = dict(state, revision=state['revision'] + 1, phase='disconnected', target=None)
                # Tombstone is durable BEFORE cleanup; failed cleanup cannot revive.
                self._write('current.json', _json(state), replace=True)
            return {'revision': state['revision'], 'state': state['phase'], 'cleanup_pending': self._recover(state)}
