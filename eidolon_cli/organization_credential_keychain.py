"""macOS file-keychain adapter using Security.framework without interactive unlock.

Only this adapter's namespaced generic-password items are queried. A caller may
supply a dedicated temporary keychain for native tests; the default keychain and
search list are never changed. Existing user credential stores are not imported.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
import hashlib
import sys

from eidolon_cli.organization_credential_security import BackendState, CredentialError
from eidolon_cli.organization_credential_native import binding_reference


class _MacAPI:
    def __init__(self):
        if sys.platform != 'darwin':
            raise CredentialError('backend_unavailable')
        pointer = ctypes.c_void_p
        self.cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        self.sec = ctypes.CDLL('/System/Library/Frameworks/Security.framework/Security')
        signatures = {
            'CFRelease': ([pointer], None),
            'CFStringCreateWithCString': ([pointer, ctypes.c_char_p, ctypes.c_uint32], pointer),
            'CFDataCreate': ([pointer, pointer, ctypes.c_long], pointer),
            'CFDataGetLength': ([pointer], ctypes.c_long),
            'CFDataGetBytePtr': ([pointer], pointer),
            'CFDictionaryCreate': ([pointer, pointer, pointer, ctypes.c_long, pointer, pointer], pointer),
            'CFArrayCreate': ([pointer, pointer, ctypes.c_long, pointer], pointer),
        }
        for name, (args, returns) in signatures.items():
            function = getattr(self.cf, name)
            function.argtypes, function.restype = args, returns
        signatures = {
            'SecKeychainCopyDefault': ([ctypes.POINTER(pointer)], ctypes.c_int32),
            'SecKeychainOpen': ([ctypes.c_char_p, ctypes.POINTER(pointer)], ctypes.c_int32),
            'SecKeychainGetStatus': ([pointer, ctypes.POINTER(ctypes.c_uint32)], ctypes.c_int32),
            'SecItemAdd': ([pointer, ctypes.POINTER(pointer)], ctypes.c_int32),
            'SecItemCopyMatching': ([pointer, ctypes.POINTER(pointer)], ctypes.c_int32),
            'SecItemDelete': ([pointer], ctypes.c_int32),
        }
        for name, (args, returns) in signatures.items():
            function = getattr(self.sec, name)
            function.argtypes, function.restype = args, returns

    def constant(self, name):
        return ctypes.c_void_p.in_dll(self.cf if name.startswith('kCF') else self.sec, name).value

    def check(self, status):
        if status:
            reason = 'backend_locked' if status in {-25308, -25293, -128} else 'backend_unavailable'
            raise CredentialError(reason)

    @contextmanager
    def keychain(self, path=None):
        reference = ctypes.c_void_p()
        result = (self.sec.SecKeychainOpen(str(path).encode(), ctypes.byref(reference)) if path is not None
                  else self.sec.SecKeychainCopyDefault(ctypes.byref(reference)))
        self.check(result)
        try:
            yield reference
        finally:
            self.cf.CFRelease(reference)

    @contextmanager
    def query(self, *, service, account, keychain, value=None, deleting=False):
        owned = []
        def text(value):
            item = self.cf.CFStringCreateWithCString(None, value.encode(), 0x08000100)
            if not item:
                raise CredentialError('backend_unavailable')
            owned.append(item)
            return item
        try:
            pairs = {
                self.constant('kSecClass'): self.constant('kSecClassGenericPassword'),
                self.constant('kSecAttrService'): text(service),
                self.constant('kSecAttrAccount'): text(account),
                self.constant('kSecUseAuthenticationUI'): self.constant('kSecUseAuthenticationUIFail'),
            }
            if value is None:
                items = (ctypes.c_void_p * 1)(keychain.value)
                array = self.cf.CFArrayCreate(None, items, 1, None)
                owned.append(array)
                pairs[self.constant('kSecMatchSearchList')] = array
                if not deleting:
                    pairs[self.constant('kSecReturnData')] = self.constant('kCFBooleanTrue')
                    pairs[self.constant('kSecMatchLimit')] = self.constant('kSecMatchLimitOne')
            else:
                buffer = ctypes.create_string_buffer(value)
                try:
                    data = self.cf.CFDataCreate(None, buffer, len(value))
                finally:
                    ctypes.memset(buffer, 0, len(buffer))
                if not data:
                    raise CredentialError('backend_unavailable')
                owned.append(data)
                pairs[self.constant('kSecValueData')] = data
                pairs[self.constant('kSecUseKeychain')] = keychain.value
            keys = (ctypes.c_void_p * len(pairs))(*pairs)
            values = (ctypes.c_void_p * len(pairs))(*pairs.values())
            query = self.cf.CFDictionaryCreate(None, keys, values, len(pairs), None, None)
            if not query:
                raise CredentialError('backend_unavailable')
            owned.append(query)
            yield query
        finally:
            for item in reversed(owned):
                if item:
                    self.cf.CFRelease(item)


class MacKeychainBackend:
    def __init__(self, *, namespace, keychain_path=None):
        self.service = 'ai.aethermesh.eidolon.organization.' + hashlib.sha256(namespace.encode()).hexdigest()
        self.keychain_path = keychain_path

    def status(self):
        try:
            api = _MacAPI()
            with api.keychain(self.keychain_path) as keychain:
                status = ctypes.c_uint32()
                api.check(api.sec.SecKeychainGetStatus(keychain, ctypes.byref(status)))
                # kSecUnlockStateStatus. Merely reading availability never unlocks.
                return BackendState('macos-keychain', 'available' if status.value & 1 else 'locked')
        except CredentialError as error:
            return BackendState('macos-keychain', 'locked' if error.reason == 'backend_locked' else 'unavailable')
        except (OSError, AttributeError, ValueError):
            return BackendState('macos-keychain', 'unavailable')

    def _require(self):
        state = self.status().state
        if state != 'available':
            raise CredentialError('backend_' + state)
        return _MacAPI()

    def put(self, binding, value):
        api = self._require()
        reference = binding_reference(binding)
        with api.keychain(self.keychain_path) as keychain, api.query(
                service=self.service, account=reference, keychain=keychain, value=value) as query:
            api.check(api.sec.SecItemAdd(query, None))
        return reference

    def consume(self, reference, binding, consumer):
        if reference != binding_reference(binding):
            raise CredentialError('scope_denied')
        api = self._require()
        output = ctypes.c_void_p()
        with api.keychain(self.keychain_path) as keychain, api.query(
                service=self.service, account=reference, keychain=keychain) as query:
            api.check(api.sec.SecItemCopyMatching(query, ctypes.byref(output)))
        try:
            length = api.cf.CFDataGetLength(output)
            if not 0 < length <= 65536:
                raise CredentialError('backend_unavailable')
            consumer(ctypes.string_at(api.cf.CFDataGetBytePtr(output), length))
        finally:
            api.cf.CFRelease(output)


    def delete(self, reference, binding):
        if reference != binding_reference(binding):
            raise CredentialError('scope_denied')
        api = self._require()
        with api.keychain(self.keychain_path) as keychain, api.query(
                service=self.service, account=reference, keychain=keychain, deleting=True) as query:
            result = api.sec.SecItemDelete(query)
            if result != -25300:  # errSecItemNotFound: already cleaned up.
                api.check(result)
