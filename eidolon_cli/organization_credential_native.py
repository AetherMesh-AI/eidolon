"""Native-only organization storage. No env, legacy store, or plaintext fallback."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
import uuid

from eidolon_cli.organization_credential_security import BackendState, CredentialError, UnavailableBackend


def binding_bytes(binding):
    return json.dumps(asdict(binding), sort_keys=True, separators=(',', ':')).encode()


def binding_reference(binding):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, 'eidolon:secure-value:' + binding_bytes(binding).decode()))


class _WindowsCredentials:
    def __init__(self):
        if sys.platform != 'win32':
            raise CredentialError('backend_unavailable')
        import ctypes
        from ctypes import wintypes
        self.ctypes = ctypes
        class Credential(ctypes.Structure):
            _fields_ = [('Flags', wintypes.DWORD), ('Type', wintypes.DWORD), ('TargetName', wintypes.LPWSTR),
                        ('Comment', wintypes.LPWSTR), ('LastWritten', wintypes.FILETIME),
                        ('CredentialBlobSize', wintypes.DWORD), ('CredentialBlob', ctypes.POINTER(ctypes.c_ubyte)),
                        ('Persist', wintypes.DWORD), ('AttributeCount', wintypes.DWORD), ('Attributes', ctypes.c_void_p),
                        ('TargetAlias', wintypes.LPWSTR), ('UserName', wintypes.LPWSTR)]
        self.Credential = Credential
        self.api = ctypes.WinDLL('advapi32', use_last_error=True)
        self.api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(Credential))]
        self.api.CredReadW.restype = wintypes.BOOL
        self.api.CredWriteW.argtypes = [ctypes.POINTER(Credential), wintypes.DWORD]
        self.api.CredWriteW.restype = wintypes.BOOL
        self.api.CredFree.argtypes = [ctypes.c_void_p]
        self.api.CredFree.restype = None

    def read(self, target):
        pointer = self.ctypes.POINTER(self.Credential)()
        if not self.api.CredReadW(target, 1, 0, self.ctypes.byref(pointer)):
            if self.ctypes.get_last_error() == 1168:  # ERROR_NOT_FOUND
                return None
            raise CredentialError('backend_locked')
        try:
            credential = pointer.contents
            if credential.Type != 1 or credential.TargetName != target or not 0 < credential.CredentialBlobSize <= 2560:
                raise CredentialError('backend_unavailable')
            return self.ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
        finally:
            if pointer and pointer.contents.CredentialBlob and pointer.contents.CredentialBlobSize:
                self.ctypes.memset(pointer.contents.CredentialBlob, 0, pointer.contents.CredentialBlobSize)
            self.api.CredFree(pointer)

    def write_new(self, target, value):
        if self.read(target) is not None:
            raise CredentialError('backend_version_exists')
        if not 0 < len(value) <= 2560:
            raise CredentialError('backend_value_too_large')
        buffer = self.ctypes.create_string_buffer(value)
        credential = self.Credential()
        credential.Type = 1  # CRED_TYPE_GENERIC, current user's credential set.
        credential.TargetName = target
        credential.CredentialBlobSize = len(value)
        credential.CredentialBlob = self.ctypes.cast(buffer, self.ctypes.POINTER(self.ctypes.c_ubyte))
        credential.Persist = 2  # Persist for this user on this machine, not enterprise roaming.
        credential.UserName = 'Eidolon organization'
        try:
            if not self.api.CredWriteW(self.ctypes.byref(credential), 0):
                raise CredentialError('backend_write_failed')
        finally:
            self.ctypes.memset(buffer, 0, len(buffer))


class WindowsCredentialBackend:
    """OS-managed current-user generic credentials, with immutable scoped targets.

    Catalog writers hold the per-organization SQLite write transaction while
    creating a target. An existing target is never overwritten, including an
    orphan left by an uncertain prior catalog write. No vault enumeration.
    This is not a security boundary against arbitrary same-user native programs;
    they share the OS vault authority. Python may retain temporary value copies.
    """
    def __init__(self, *, namespace):
        self.prefix = 'Eidolon.Organization.' + hashlib.sha256(namespace.encode()).hexdigest() + '.'

    def _target(self, binding, reference=None):
        expected = binding_reference(binding)
        if reference is not None and reference != expected:
            raise CredentialError('scope_denied')
        return self.prefix + expected

    def status(self):
        try:
            api = _WindowsCredentials()
            # Exact reserved target only; do not enumerate or read any user item.
            api.read(self.prefix + 'availability')
            return BackendState('windows-credential-manager', 'available')
        except CredentialError as error:
            return BackendState('windows-credential-manager', 'locked' if error.reason == 'backend_locked' else 'unavailable')
        except (OSError, AttributeError):
            return BackendState('windows-credential-manager', 'unavailable')

    def put(self, binding, value):
        _WindowsCredentials().write_new(self._target(binding), value)
        return binding_reference(binding)

    def consume(self, reference, binding, consumer):
        value = _WindowsCredentials().read(self._target(binding, reference))
        if value is None:
            raise CredentialError('value_missing')
        consumer(value)


def native_backend(directory):
    namespace = str(Path(directory).absolute())
    if sys.platform == 'win32':
        return WindowsCredentialBackend(namespace=namespace)
    if sys.platform == 'darwin':
        from eidolon_cli.organization_credential_keychain import MacKeychainBackend
        return MacKeychainBackend(namespace=namespace)
    return UnavailableBackend()
