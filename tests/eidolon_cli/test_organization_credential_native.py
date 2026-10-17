"""Native OS vault proof. Fresh synthetic targets/keychains only; no enumeration."""
from dataclasses import replace
import uuid

import pytest

from eidolon_cli.organization_credential_security import CredentialError, ValueBinding


def binding():
    return ValueBinding('synthetic-org-' + uuid.uuid4().hex, str(uuid.uuid4()), 1, 'synthetic-owner', str(uuid.uuid4()))


@pytest.mark.windows_only
def test_windows_credential_manager_roundtrip_immutable_scope_and_fresh_process(tmp_path):
    import ctypes
    from ctypes import wintypes
    import json
    from dataclasses import asdict
    import subprocess
    import sys
    from eidolon_cli.organization_credential_native import WindowsCredentialBackend, _WindowsCredentials
    backend = WindowsCredentialBackend(namespace=str(tmp_path / 'isolated-native-test'))
    item = binding()
    value = b'SYNTHETIC_WINDOWS_CREDENTIAL_83b1'
    target = backend._target(item)
    created = False
    try:
        assert backend.status().state == 'available'
        reference = backend.put(item, value); created = True
        observed = []
        backend.consume(reference, item, observed.append)
        assert observed == [value]
        with pytest.raises(CredentialError, match='backend_version_exists'):
            backend.put(item, b'SYNTHETIC_REPLACEMENT_DENIED')
        with pytest.raises(CredentialError, match='scope_denied'):
            backend.consume(reference, replace(item, organization_id='foreign'), observed.append)
        code = '''import json,sys
from eidolon_cli.organization_credential_native import WindowsCredentialBackend
from eidolon_cli.organization_credential_security import ValueBinding
p=json.loads(sys.stdin.read()); observed=[]
WindowsCredentialBackend(namespace=p['namespace']).consume(p['reference'],ValueBinding(**p['binding']),observed.append)
assert observed==[b'SYNTHETIC_WINDOWS_CREDENTIAL_83b1']
print('verified')
'''
        result = subprocess.run([sys.executable, '-c', code], input=json.dumps({'namespace': str(tmp_path / 'isolated-native-test'),
            'reference': reference, 'binding': asdict(item)}), capture_output=True, text=True, timeout=20)
        assert result.returncode == 0 and result.stdout.strip() == 'verified'
        assert value.decode() not in result.stdout + result.stderr
        with pytest.raises(CredentialError, match='scope_denied'):
            backend.delete(reference, replace(item, organization_id='foreign'))
        backend.delete(reference, item)
        backend.delete(reference, item)
        created = False
        with pytest.raises(CredentialError, match='value_missing'):
            backend.consume(reference, item, observed.append)
    finally:
        if created:
            api = _WindowsCredentials().api
            api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
            api.CredDeleteW.restype = wintypes.BOOL
            assert api.CredDeleteW(target, 1, 0)


@pytest.mark.macos_only
def test_macos_isolated_keychain_roundtrip_locking_and_scope(tmp_path):
    import ctypes
    from eidolon_cli.organization_credential_keychain import MacKeychainBackend, _MacAPI
    api = _MacAPI()
    pointer = ctypes.c_void_p
    api.sec.SecKeychainCreate.argtypes = [ctypes.c_char_p, ctypes.c_uint32, pointer, ctypes.c_ubyte, pointer, ctypes.POINTER(pointer)]
    api.sec.SecKeychainCreate.restype = ctypes.c_int32
    api.sec.SecKeychainDelete.argtypes = [pointer]
    api.sec.SecKeychainDelete.restype = ctypes.c_int32
    api.sec.SecKeychainLock.argtypes = [pointer]
    api.sec.SecKeychainLock.restype = ctypes.c_int32
    api.sec.SecKeychainUnlock.argtypes = [pointer, ctypes.c_uint32, pointer, ctypes.c_ubyte]
    api.sec.SecKeychainUnlock.restype = ctypes.c_int32
    import subprocess
    def global_configuration():
        # Read paths only, never enumerate credential items or restore settings.
        return tuple(subprocess.run(['/usr/bin/security', command, '-d', 'user'],
            check=True, capture_output=True, timeout=10).stdout
            for command in ('default-keychain', 'list-keychains'))
    configuration_before = global_configuration()
    path = tmp_path / 'synthetic-only.keychain-db'
    password = ctypes.create_string_buffer(b'SYNTHETIC_KEYCHAIN_PASSWORD')
    keychain = pointer()
    api.check(api.sec.SecKeychainCreate(str(path).encode(), len(password.value), password, False, None, ctypes.byref(keychain)))
    try:
        assert global_configuration() == configuration_before
        backend = MacKeychainBackend(namespace=str(tmp_path), keychain_path=path)
        item = binding()
        value = b'SYNTHETIC_MACOS_CREDENTIAL_952c'
        assert backend.status().state == 'available'
        reference = backend.put(item, value)
        observed = []
        backend.consume(reference, item, observed.append)
        assert observed == [value]
        with pytest.raises(CredentialError):
            backend.put(item, b'SYNTHETIC_OVERWRITE_DENIED')
        with pytest.raises(CredentialError, match='scope_denied'):
            backend.consume(reference, replace(item, organization_id='foreign'), observed.append)
        other_namespace = MacKeychainBackend(namespace=str(tmp_path / 'other'), keychain_path=path)
        with pytest.raises(CredentialError):
            other_namespace.consume(reference, item, observed.append)
        api.check(api.sec.SecKeychainLock(keychain))
        assert backend.status().state == 'locked'
        with pytest.raises(CredentialError, match='backend_locked'):
            backend.consume(reference, item, observed.append)
        api.check(api.sec.SecKeychainUnlock(keychain, len(password.value), password, True))
        backend.consume(reference, item, observed.append)
        assert observed == [value, value]
        with pytest.raises(CredentialError, match='scope_denied'):
            backend.delete(reference, replace(item, organization_id='foreign'))
        backend.delete(reference, item)
        backend.delete(reference, item)
        with pytest.raises(CredentialError):
            backend.consume(reference, item, observed.append)
    finally:
        # Only the exact new temporary keychain from this test is deleted.
        api.check(api.sec.SecKeychainDelete(keychain))
        api.cf.CFRelease(keychain)
        ctypes.memset(password, 0, len(password))
        assert global_configuration() == configuration_before


@pytest.mark.linux_only
def test_linux_is_explicitly_unavailable_without_environment_fallback(tmp_path, monkeypatch):
    from eidolon_cli.organization_credential_native import native_backend
    monkeypatch.setenv('github_eidolonReleases_token', 'SYNTHETIC_HOST_SECRET')
    backend = native_backend(tmp_path / 'empty')
    assert backend.status().public() == {'provider': 'none', 'state': 'unavailable'}
    with pytest.raises(CredentialError, match='backend_unavailable'):
        backend.put(binding(), b'SYNTHETIC')
    assert not (tmp_path / 'empty').exists()


def test_windows_exact_delete_is_idempotent_and_never_hides_other_errors():
    from types import SimpleNamespace
    from eidolon_cli.organization_credential_native import _WindowsCredentials
    calls = []
    api = object.__new__(_WindowsCredentials)
    api.api = SimpleNamespace(CredDeleteW=lambda *args: calls.append(args) or False)
    api.ctypes = SimpleNamespace(get_last_error=lambda: 1168)
    api.delete('synthetic.exact.target')
    assert calls == [('synthetic.exact.target', 1, 0)]
    api.ctypes = SimpleNamespace(get_last_error=lambda: 5)
    with pytest.raises(CredentialError, match='backend_delete_failed'):
        api.delete('synthetic.exact.target')


def test_native_delete_rejects_foreign_binding_before_native_access(monkeypatch):
    import eidolon_cli.organization_credential_native as native
    from eidolon_cli.organization_credential_keychain import MacKeychainBackend
    item = binding()
    reference = native.binding_reference(item)
    def forbidden():
        pytest.fail('Foreign binding must not touch any native API')
    monkeypatch.setattr(native, '_WindowsCredentials', forbidden)
    windows = native.WindowsCredentialBackend(namespace='synthetic-delete')
    mac = MacKeychainBackend(namespace='synthetic-delete')
    monkeypatch.setattr(mac, '_require', forbidden)
    for backend in (windows, mac):
        with pytest.raises(CredentialError, match='scope_denied'):
            backend.delete(reference, replace(item, organization_id='foreign'))


def test_mac_delete_uses_exact_query_and_only_missing_is_success(monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from eidolon_cli.organization_credential_keychain import MacKeychainBackend
    from eidolon_cli.organization_credential_native import binding_reference
    calls = []
    @contextmanager
    def keychain(path):
        assert path == 'synthetic-only'
        yield 'synthetic-keychain'
    @contextmanager
    def query(**kwargs):
        calls.append(kwargs)
        yield 'exact-query'
    errors = []
    api = SimpleNamespace(keychain=keychain, query=query,
        sec=SimpleNamespace(SecItemDelete=lambda q: -25300), check=errors.append)
    backend = MacKeychainBackend(namespace='synthetic-delete', keychain_path='synthetic-only')
    monkeypatch.setattr(backend, '_require', lambda: api)
    item = binding()
    reference = binding_reference(item)
    backend.delete(reference, item)
    assert calls == [{'service': backend.service, 'account': reference,
                      'keychain': 'synthetic-keychain', 'deleting': True}]
    assert errors == []
    api.sec.SecItemDelete = lambda q: -25308
    def check(result):
        assert result == -25308
        raise CredentialError('backend_locked')
    api.check = check
    with pytest.raises(CredentialError, match='backend_locked'):
        backend.delete(reference, item)
