"""Synthetic-only storage proof: never accesses an actual native credential vault."""
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import threading
import uuid

import pytest

from eidolon_cli import organization_setup_subscription_store as module
from eidolon_cli.organization_credential_native import binding_reference
from eidolon_cli.organization_setup_subscription_store import (
    MAX_ENVELOPE_BYTES, MAX_SECRET_BYTES, PRIVATE_STORE_NAME,
    StoreError, SubscriptionStore, TrustedBinding,
)


class FakeVault:
    def __init__(self):
        self.values = {}
        self.calls = []
        self.before_put = None
        self.fail_delete = False
        self.fail_put_after_write = False
        self.after_consume = None

    def put(self, binding, value):
        reference = binding_reference(binding)
        if self.before_put:
            self.before_put(binding, reference)
        assert reference not in self.values
        assert type(value) is bytes and len(value) == 32
        self.values[reference] = value
        self.calls.append(('put', reference))
        if self.fail_put_after_write:
            raise RuntimeError('synthetic-secret-in-native-error')
        return reference

    def consume(self, reference, binding, consumer):
        assert reference == binding_reference(binding)
        self.calls.append(('consume', reference))
        consumer(self.values[reference])
        if self.after_consume:
            self.after_consume()

    def delete(self, reference, binding):
        assert reference == binding_reference(binding)
        self.calls.append(('delete', reference))
        if self.fail_delete:
            raise RuntimeError('synthetic-secret-in-delete-error')
        self.values.pop(reference, None)


@pytest.fixture
def sample(tmp_path):
    if os.name != 'posix':
        pytest.skip('Positive synthetic store proof requires the POSIX filesystem adapter')
    profile = tmp_path / 'profile'
    profile.mkdir(mode=0o700)
    binding = TrustedBinding('synthetic-host', str(profile), str(uuid.uuid4()), str(uuid.uuid4()),
                             'opaque-registration', 'verified-client', 'verified-account')
    vault = FakeVault()
    store = SubscriptionStore(profile, backend=vault)
    return store, binding, vault


def state(store):
    value = json.loads((store.directory / 'current.json').read_bytes())
    return value.get('body', value)


def connect(sample, secret=b'synthetic-grant'):
    store, binding, _ = sample
    return store.connect(binding, secret, expected_revision=0)


def test_passive_absence_does_not_create_or_read_vault(sample):
    store, _, vault = sample
    assert store.status() == {'revision': 0, 'state': 'absent', 'cleanup_pending': False}
    assert not store.directory.exists()
    assert not vault.calls


def test_journal_precedes_native_put_and_only_ciphertext_is_on_disk(sample):
    store, binding, vault = sample
    def verify_journal(native, reference):
        journal = json.loads((store.directory / (native.operation_id + '.journal')).read_bytes())
        assert journal['reference'] == reference
        assert journal['native_binding'] == asdict(native)
        assert journal['binding'] == asdict(binding)
        assert not (store.directory / (native.operation_id + '.enc')).exists()
        assert state(store)['phase'] == 'disconnected'
        assert state(store)['revision'] == 0
        assert (store.directory / 'identity.json').exists()
    vault.before_put = verify_journal
    assert connect(sample) == 1
    assert [call[0] for call in vault.calls] == ['put', 'consume']
    assert store.directory.name == PRIVATE_STORE_NAME
    for path in store.directory.iterdir():
        assert b'synthetic-grant' not in path.read_bytes()
        assert path.stat().st_mode & 0o077 == 0
    assert store.consume(binding, 1, lambda value: value == b'synthetic-grant') is True


def test_status_never_decrypts_even_when_active(sample):
    store, _, vault = sample
    connect(sample)
    vault.calls.clear()
    assert store.status()['state'] == 'active'
    assert vault.calls == []


def test_every_identity_field_fences_use(sample):
    store, binding, _ = sample
    connect(sample)
    for name in asdict(binding):
        changed = str(uuid.uuid4()) if name in {'organization_id', 'owner_id'} else 'other-value'
        with pytest.raises(StoreError):
            store.consume(replace(binding, **{name: changed}), 1, lambda value: pytest.fail('must not expose secret'))
    with pytest.raises(StoreError, match='stale_revision'):
        store.consume(binding, 0, lambda value: pytest.fail('must not expose secret'))


def test_connect_cannot_replace_live_account_and_cas_is_exact(sample):
    store, binding, vault = sample
    connect(sample)
    calls = list(vault.calls)
    with pytest.raises(StoreError, match='disconnect_required'):
        store.connect(replace(binding, account_id='other'), b'new', expected_revision=1)
    with pytest.raises(StoreError, match='stale_revision'):
        store.connect(binding, b'new', expected_revision=True)
    assert calls == vault.calls


def test_disconnect_tombstone_wins_cleanup_failure_and_restart(sample):
    store, binding, vault = sample
    connect(sample)
    vault.fail_delete = True
    result = store.disconnect(expected_revision=1)
    assert result == {'revision': 2, 'state': 'disconnected', 'cleanup_pending': True}
    assert vault.values
    restarted = SubscriptionStore(store.profile, backend=vault)
    with pytest.raises(StoreError):
        restarted.consume(binding, 1, lambda value: pytest.fail('retired'))
    with pytest.raises(StoreError):
        restarted.consume(binding, 2, lambda value: pytest.fail('retired'))
    assert restarted.status() == result
    vault.fail_delete = False
    assert restarted.recover_orphans() is False
    assert vault.values == {}
    assert restarted.disconnect(expected_revision=2) == dict(result, cleanup_pending=False)
    assert restarted.status()['revision'] == 2


def test_disconnect_then_reauthorize_keeps_revision_and_scope(sample):
    store, binding, _ = sample
    connect(sample)
    store.disconnect(expected_revision=1)
    with pytest.raises(StoreError, match='scope_denied'):
        store.connect(replace(binding, organization_id=str(uuid.uuid4())), b'new', expected_revision=2)
    new_binding = replace(binding, registration_id='new-registration', account_id='new-account', issued_client_id='new-client')
    assert store.connect(new_binding, b'new', expected_revision=2) == 3
    with pytest.raises(StoreError):
        store.consume(binding, 3, lambda value: pytest.fail('wrong identity'))
    assert store.consume(new_binding, 3, lambda value: value == b'new')


def test_uncertain_native_put_is_recoverable_by_exact_journal(sample):
    store, _, vault = sample
    vault.fail_put_after_write = True
    with pytest.raises(StoreError) as error:
        connect(sample)
    assert 'synthetic-secret' not in str(error.value)
    assert store.status() == {'revision': 0, 'state': 'disconnected', 'cleanup_pending': True}
    reference = next(iter(vault.values))
    assert store.recover_orphans() is False
    assert vault.calls[-1] == ('delete', reference)
    assert not vault.values
    assert store.recover_orphans() is False


def test_refresh_is_immutable_and_old_revision_never_falls_back(sample):
    store, binding, vault = sample
    connect(sample)
    original = state(store)['target']
    observed = []
    def rotate(value):
        assert value == b'synthetic-grant'
        observed.append(state(store)['phase'])
        return b'rotated-synthetic-grant'
    assert store.refresh(binding, 1, rotate) == 2
    assert observed == ['refreshing']
    assert state(store)['target'] != original
    assert len(vault.values) == 2
    with pytest.raises(StoreError, match='stale_revision'):
        store.consume(binding, 1, lambda value: pytest.fail('stale'))
    assert store.consume(binding, 2, lambda value: value == b'rotated-synthetic-grant')
    assert store.recover_orphans() is False
    assert len(vault.values) == 1


@pytest.mark.parametrize('exception', [RuntimeError('secret-in-error'), KeyboardInterrupt()])
def test_uncertain_refresh_survives_restart_and_never_replays(sample, exception):
    store, binding, vault = sample
    connect(sample)
    calls = []
    def rotate(value):
        calls.append(1)
        raise exception
    with pytest.raises((StoreError, KeyboardInterrupt)):
        store.refresh(binding, 1, rotate)
    restarted = SubscriptionStore(store.profile, backend=vault)
    assert restarted.status()['state'] == 'refreshing'
    for operation in (lambda: restarted.consume(binding, 1, rotate),
                      lambda: restarted.refresh(binding, 1, rotate),
                      lambda: restarted.connect(binding, b'new', expected_revision=1)):
        with pytest.raises(StoreError):
            operation()
    assert calls == [1]
    assert restarted.disconnect(expected_revision=1)['revision'] == 2


def test_every_decrypted_use_is_fenced_again_after_native_read(sample):
    store, binding, vault = sample
    connect(sample)
    def invalidate():
        current = state(store)
        current.update(revision=2, phase='disconnected', target=None)
        store._write('current.json', module._json(current), replace=True)
    vault.after_consume = invalidate
    with pytest.raises(StoreError, match='stale_revision'):
        store.consume(binding, 1, lambda value: pytest.fail('fence must precede consumer'))


def test_aad_authenticates_all_binding_fields(sample):
    store, binding, _ = sample
    connect(sample)
    current = state(store)
    target = current['target']
    journal_path = store.directory / (target + '.journal')
    journal = json.loads(journal_path.read_bytes())
    changed = replace(binding, account_id='changed-account')
    journal['binding'] = asdict(changed)
    current['binding'] = asdict(changed)
    store._write(target + '.journal', module._json(journal), replace=True)
    store._write('current.json', module._json(current), replace=True)
    with pytest.raises(StoreError):
        store.consume(changed, 1, lambda value: pytest.fail('AAD mismatch'))


def test_ciphertext_corruption_and_oversize_never_expose_plaintext(sample):
    store, binding, _ = sample
    connect(sample)
    path = store.directory / (state(store)['target'] + '.enc')
    original = path.read_bytes()
    for value in (original[:-1] + bytes([original[-1] ^ 1]), b'x' * (MAX_ENVELOPE_BYTES + 1)):
        path.write_bytes(value)
        with pytest.raises(StoreError):
            store.consume(binding, 1, lambda secret: pytest.fail('corrupt ciphertext'))


def test_exact_envelope_limit_and_oversized_input_preflight(sample):
    store, binding, vault = sample
    with pytest.raises(StoreError, match='invalid_secret'):
        connect(sample, b'x' * (MAX_SECRET_BYTES + 1))
    assert not vault.calls
    assert connect(sample, b'x' * MAX_SECRET_BYTES) == 1
    assert (store.directory / (state(store)['target'] + '.enc')).stat().st_size == MAX_ENVELOPE_BYTES


def test_no_secret_in_repr_or_callback_error(sample):
    store, binding, _ = sample
    connect(sample)
    assert binding.account_id not in repr(binding)
    assert str(store.profile) not in repr(store)
    def fail(value):
        raise RuntimeError(value.decode())
    with pytest.raises(StoreError) as error:
        store.consume(binding, 1, fail)
    assert 'synthetic-grant' not in str(error.value)
    assert error.value.__cause__ is None


def test_recursive_use_is_refused(sample):
    store, binding, _ = sample
    connect(sample)
    with pytest.raises(StoreError, match='recursive_use'):
        store.consume(binding, 1, lambda value: store.status())


@pytest.mark.parametrize('target', ['profile', 'store', 'lock', 'current', 'envelope'])
def test_rejects_permissive_filesystem(sample, target):
    store, binding, _ = sample
    connect(sample)
    paths = {'profile': store.profile, 'store': store.directory, 'lock': store.directory / 'lock',
             'current': store.directory / 'current.json', 'envelope': store.directory / (state(store)['target'] + '.enc')}
    path = paths[target]
    path.chmod(0o755 if path.is_dir() else 0o644)
    with pytest.raises(StoreError, match='unsafe_filesystem'):
        store.consume(binding, 1, lambda value: pytest.fail('unsafe path'))


def test_symlink_and_hardlink_envelope_refused(sample, tmp_path):
    store, binding, _ = sample
    connect(sample)
    path = store.directory / (state(store)['target'] + '.enc')
    saved = tmp_path / 'ciphertext'
    path.rename(saved)
    path.symlink_to(saved)
    with pytest.raises(StoreError, match='unsafe_filesystem'):
        store.consume(binding, 1, lambda value: pytest.fail('symlink'))
    path.unlink()
    os.link(saved, path)
    with pytest.raises(StoreError, match='unsafe_filesystem'):
        store.consume(binding, 1, lambda value: pytest.fail('hardlink'))


def test_production_platforms_fail_closed_without_native_read(tmp_path, monkeypatch):
    profile = tmp_path / 'profile'
    profile.mkdir(mode=0o700)
    for platform, reason in [('linux', 'backend_unavailable'), ('win32', 'filesystem_protection_unavailable')]:
        monkeypatch.setattr(module.sys, 'platform', platform)
        monkeypatch.setattr(module, 'native_backend', lambda directory: pytest.fail('must not access native credentials'))
        with pytest.raises(StoreError, match=reason):
            SubscriptionStore(profile).status()


def test_disconnect_serializes_with_decrypted_use_across_instances(sample):
    store, binding, vault = sample
    connect(sample)
    second = SubscriptionStore(store.profile, backend=vault)
    inside, release, disconnected = threading.Event(), threading.Event(), threading.Event()
    failures = []
    def consumer(value):
        inside.set()
        assert release.wait(5)
        return value == b'synthetic-grant'
    def use():
        try:
            assert store.consume(binding, 1, consumer)
        except BaseException as error:
            failures.append(error)
    def disconnect():
        try:
            second.disconnect(expected_revision=1)
            disconnected.set()
        except BaseException as error:
            failures.append(error)
    reader = threading.Thread(target=use)
    remover = threading.Thread(target=disconnect)
    reader.start()
    try:
        assert inside.wait(5)
        remover.start()
        assert not disconnected.wait(0.05)
    finally:
        release.set()
        reader.join(5)
        if remover.ident is not None:
            remover.join(5)
    assert not reader.is_alive() and not remover.is_alive()
    assert not failures
    assert disconnected.is_set()
    assert second.status()['state'] == 'disconnected'


@pytest.mark.parametrize('location,field,value', [
    ('current', 'version', True), ('journal', 'version', True),
    ('journal', 'revision', True), ('current', 'revision', True),
    ('journal', 'unexpected', 'field'), ('current', 'unexpected', 'field'),
    ('native_binding', 'version', True), ('native_binding', 'unexpected', 'field'),
    ('binding', 'unexpected', 'field'),
])
def test_closed_metadata_schemas_reject_booleans_and_unknown_fields(sample, location, field, value):
    store, binding, _ = sample
    connect(sample)
    name = 'current.json' if location == 'current' else state(store)['target'] + '.journal'
    data = json.loads((store.directory / name).read_bytes())
    target = data[location] if location in {'native_binding', 'binding'} else data
    target[field] = value
    store._write(name, module._json(data), replace=True)
    with pytest.raises(StoreError):
        store.consume(binding, 1, lambda value: pytest.fail('invalid metadata'))


def test_duplicate_metadata_key_refused(sample):
    store, binding, _ = sample
    connect(sample)
    path = store.directory / 'current.json'
    value = path.read_bytes().replace(b'"revision":1', b'"revision":1,"revision":1')
    path.write_bytes(value)
    with pytest.raises(StoreError, match='corrupt_store'):
        store.consume(binding, 1, lambda value: pytest.fail('duplicate key'))


def test_world_writable_nonsticky_ancestor_refused(sample):
    store, _, _ = sample
    store.profile.parent.chmod(0o777)
    with pytest.raises(StoreError, match='unsafe_filesystem'):
        store.status()


@pytest.fixture
def darwin_acl_api(monkeypatch):
    """Pure synthetic C-API surface; no native library, ACL or vault calls."""
    import ctypes
    import errno
    import stat
    from contextlib import contextmanager
    from types import SimpleNamespace
    config = {'tags': [], 'present': 32, 'init': 77, 'stat_result': 0,
              'query_result': 0, 'get_result': 0, 'acl': 123, 'valid_result': 0,
              'stat_mismatch': False, 'entry_error': False}
    calls = []
    before = SimpleNamespace(st_dev=1, st_ino=2, st_mode=stat.S_IFDIR | 0o700,
                             st_uid=501, st_gid=20, st_nlink=1, st_ctime_ns=3_000_000_004)
    @contextmanager
    def descriptor(path):
        calls.append(('open', str(path)))
        try:
            yield 7, before
        finally:
            calls.append(('close', 7))
    def native_stat(fd, pointer, fsec):
        calls.append(('stat', fd, fsec))
        result = pointer._obj
        for key in ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_nlink'):
            setattr(result, key, getattr(before, key))
        result.st_ctimespec.tv_sec, result.st_ctimespec.tv_nsec = 3, 4
        if config['stat_mismatch']:
            result.st_ino += 1
        return config['stat_result']
    def query(fsec, property_id, pointer):
        calls.append(('query', property_id))
        pointer._obj.value = config['present']
        return config['query_result']
    def get(fsec, property_id, pointer):
        calls.append(('get', property_id))
        pointer._obj.value = config['acl']
        return config['get_result']
    position = [-1]
    def entry(acl, index, pointer):
        position[0] = 0 if index == 0 else position[0] + 1
        if config['entry_error']:
            ctypes.set_errno(errno.EIO)
            return -1
        if position[0] >= len(config['tags']):
            ctypes.set_errno(errno.EINVAL)
            return -1
        return 0
    def tag(entry, pointer):
        pointer._obj.value = config['tags'][position[0]]
        return 0
    api = SimpleNamespace(
        filesec_init=lambda: config['init'],
        filesec_free=lambda fsec: calls.append(('free_fsec', fsec)),
        filesec_query_property=query, filesec_get_property=get,
        acl_valid=lambda acl: config['valid_result'], acl_get_entry=entry,
        acl_get_tag_type=tag, acl_free=lambda acl: calls.append(('free_acl', acl.value)),
        fstatx_np=native_stat,
    )
    setattr(api, 'fstatx_np$INODE64', native_stat)
    monkeypatch.setattr(module.ctypes, 'CDLL', lambda *args, **kwargs: api)
    monkeypatch.setattr(module.platform, 'machine', lambda: 'arm64')
    monkeypatch.setattr(module, '_checked_acl_descriptor', descriptor)
    return config, calls, api


@pytest.mark.parametrize('tags,expected', [([], None), ([2], None), ([1], 'unsafe_filesystem'),
                                          ([2, 1], 'unsafe_filesystem'), ([99], 'unsafe_filesystem')])
def test_darwin_acl_boundary_with_synthetic_c_api(darwin_acl_api, tags, expected):
    config, calls, _ = darwin_acl_api
    config['tags'] = tags
    if expected:
        with pytest.raises(StoreError, match=expected):
            module._check_darwin_acl(Path('/synthetic-only'))
    else:
        module._check_darwin_acl(Path('/synthetic-only'))
    assert ('free_acl', 123) in calls
    assert ('free_fsec', 77) in calls
    assert calls[-1] == ('close', 7)


def test_darwin_acl_explicit_absence_requires_successful_inspection(darwin_acl_api):
    config, calls, _ = darwin_acl_api
    config['present'] = 0
    module._check_darwin_acl(Path('/synthetic-only'))
    assert ('stat', 7, 77) in calls and ('query', 5) in calls
    assert not any(call[0] in {'get', 'free_acl'} for call in calls)
    assert calls[-2:] == [('free_fsec', 77), ('close', 7)]


@pytest.mark.parametrize('change', [
    {'stat_result': -1, 'present': 0}, {'query_result': -1, 'present': 0},
    {'present': -1}, {'get_result': -1, 'acl': None}, {'acl': None}, {'acl': 1},
    {'valid_result': -1}, {'init': None}, {'entry_error': True},
    {'stat_mismatch': True, 'present': 0},
])
def test_darwin_acl_absence_never_masks_errors_or_identity_mismatch(darwin_acl_api, change):
    config, calls, _ = darwin_acl_api
    config.update(change)
    with pytest.raises(StoreError):
        module._check_darwin_acl(Path('/synthetic-only'))
    assert calls[-1] == ('close', 7)
    assert (('free_fsec', 77) in calls) == bool(config['init'])
    if config['stat_result'] or config['stat_mismatch']:
        assert ('query', 5) not in calls


@pytest.mark.parametrize('architecture,symbol', [('arm64', 'fstatx_np'), ('x86_64', 'fstatx_np$INODE64')])
def test_darwin_acl_exact_supported_symbol_without_fallback(darwin_acl_api, monkeypatch, architecture, symbol):
    config, calls, api = darwin_acl_api
    config['present'] = 0
    monkeypatch.setattr(module.platform, 'machine', lambda: architecture)
    module._check_darwin_acl(Path('/synthetic-only'))
    calls.clear()
    delattr(api, symbol)
    with pytest.raises(StoreError, match='filesystem_protection_unavailable'):
        module._check_darwin_acl(Path('/synthetic-only'))
    assert calls == []


@pytest.mark.parametrize('unsupported', ['architecture', 'pointer_size', 'stat_size', 'stat_alignment'])
def test_darwin_acl_unsupported_abi_fails_before_open(darwin_acl_api, monkeypatch, unsupported):
    _, calls, _ = darwin_acl_api
    real_sizeof, real_alignment = module.ctypes.sizeof, module.ctypes.alignment
    if unsupported == 'architecture':
        monkeypatch.setattr(module.platform, 'machine', lambda: 'unknown')
    elif unsupported == 'pointer_size':
        monkeypatch.setattr(module.ctypes, 'sizeof', lambda value: 4 if value is module.ctypes.c_void_p else real_sizeof(value))
    elif unsupported == 'stat_size':
        monkeypatch.setattr(module.ctypes, 'sizeof', lambda value: 128 if value is module._DarwinStat64 else real_sizeof(value))
    else:
        monkeypatch.setattr(module.ctypes, 'alignment', lambda value: 4 if value is module._DarwinStat64 else real_alignment(value))
    with pytest.raises(StoreError, match='filesystem_protection_unavailable'):
        module._check_darwin_acl(Path('/synthetic-only'))
    assert calls == []


@pytest.mark.parametrize('when', ['before', 'after'])
@pytest.mark.parametrize('field', ['st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_nlink', 'st_ctime_ns'])
def test_acl_descriptor_rejects_identity_or_metadata_changes(tmp_path, monkeypatch, when, field):
    if os.name != 'posix':
        pytest.skip('Descriptor proof requires POSIX nofollow support')
    from types import SimpleNamespace
    path = tmp_path / 'synthetic-file'
    path.write_bytes(b'')
    real_fstat = os.fstat
    calls = []
    def fstat(fd):
        value = real_fstat(fd)
        calls.append(fd)
        if len(calls) == (1 if when == 'before' else 2):
            result = SimpleNamespace(**{key: getattr(value, key) for key in (
                'st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_nlink', 'st_ctime_ns')})
            setattr(result, field, getattr(result, field) + 1)
            return result
        return value
    with monkeypatch.context() as patch:
        patch.setattr(module.os, 'fstat', fstat)
        with pytest.raises(StoreError, match='unsafe_filesystem'):
            with module._checked_acl_descriptor(path):
                assert when == 'after'
    with pytest.raises(OSError):
        real_fstat(calls[-1])  # Descriptor was closed on refusal.


def test_acl_descriptor_rejects_symlink_and_post_read_path_replacement(tmp_path):
    if os.name != 'posix':
        pytest.skip('Descriptor proof requires POSIX nofollow support')
    path = tmp_path / 'synthetic-file'
    path.write_bytes(b'')
    link = tmp_path / 'synthetic-link'
    link.symlink_to(path)
    with pytest.raises(StoreError, match='unsafe_filesystem'):
        with module._checked_acl_descriptor(link):
            pytest.fail('symlink')
    with pytest.raises(StoreError, match='unsafe_filesystem'):
        with module._checked_acl_descriptor(path):
            path.rename(tmp_path / 'original')
            path.write_bytes(b'')


@pytest.mark.parametrize('phase', ['connected', 'refreshed', 'disconnected'])
def test_lost_current_never_resets_revision_or_cleans_up_active_target(sample, phase):
    store, binding, vault = sample
    connect(sample)
    if phase == 'refreshed':
        store.refresh(binding, 1, lambda value: b'rotated')
    elif phase == 'disconnected':
        store.disconnect(expected_revision=1)
    previous_revision = state(store)['revision']
    (store.directory / 'current.json').unlink()
    restarted = SubscriptionStore(store.profile, backend=vault)
    calls, keys = list(vault.calls), dict(vault.values)
    captured_before_connection = 0
    other_org = replace(binding, organization_id=str(uuid.uuid4()))
    for operation in (
        restarted.status,
        lambda: restarted.connect(binding, b'new', expected_revision=captured_before_connection),
        lambda: restarted.connect(other_org, b'new', expected_revision=captured_before_connection),
        lambda: restarted.consume(binding, previous_revision, lambda value: pytest.fail('lost fence')),
        lambda: restarted.refresh(binding, previous_revision, lambda value: pytest.fail('lost fence')),
        lambda: restarted.disconnect(expected_revision=previous_revision),
        restarted.recover_orphans,
    ):
        with pytest.raises(StoreError, match='recovery_required'):
            operation()
    assert vault.calls == calls and vault.values == keys
    assert (store.directory / 'identity.json').exists()
    assert not (store.directory / 'current.json').exists()


def test_uncertain_initial_put_preserves_original_scope_and_revision_floor(sample):
    store, binding, vault = sample
    vault.fail_put_after_write = True
    with pytest.raises(StoreError):
        connect(sample)
    with pytest.raises(StoreError, match='scope_denied'):
        store.connect(replace(binding, organization_id=str(uuid.uuid4())), b'new', expected_revision=0)
    assert store.disconnect(expected_revision=0)['cleanup_pending'] is False
    assert state(store)['binding'] == asdict(binding)
    assert (store.directory / 'identity.json').exists()
    vault.fail_put_after_write = False
    assert store.connect(binding, b'new', expected_revision=0) == 1


def test_partial_initialization_is_unavailable_without_reset(sample, monkeypatch):
    store, binding, vault = sample
    original = store._write
    def interrupted(name, data, **kwargs):
        if name == 'current.json':
            raise OSError('synthetic interruption')
        return original(name, data, **kwargs)
    monkeypatch.setattr(store, '_write', interrupted)
    with pytest.raises(StoreError):
        connect(sample)
    assert vault.calls == []
    restarted = SubscriptionStore(store.profile, backend=vault)
    with pytest.raises(StoreError, match='recovery_required'):
        restarted.connect(binding, b'new', expected_revision=0)
    with pytest.raises(StoreError, match='recovery_required'):
        restarted.recover_orphans()


@pytest.mark.parametrize('operation', ['consume', 'refresh'])
@pytest.mark.parametrize('alter_args', [False, True])
def test_callback_store_errors_cannot_leak_secrets(sample, operation, alter_args):
    import traceback
    store, binding, _ = sample
    connect(sample)
    def fail(value):
        error = StoreError(value.decode())
        if alter_args:
            error.args = (value.decode(),)
            error.reason = value.decode()
        raise error
    with pytest.raises(StoreError) as captured:
        getattr(store, operation)(binding, 1, fail)
    exposed = str(captured.value) + repr(captured.value) + ''.join(traceback.format_exception(captured.value))
    assert 'synthetic-grant' not in exposed
    assert captured.value.reason == 'operation_failed'
    assert store.status()['state'] == ('refreshing' if operation == 'refresh' else 'active')


@pytest.mark.parametrize('operation', ['consume', 'refresh'])
def test_two_instances_same_thread_cannot_reenter_store(sample, operation):
    store, binding, vault = sample
    connect(sample)
    second = SubscriptionStore(store.profile, backend=vault)
    def reenter(value):
        second.status()
        pytest.fail('same-thread second-instance use must fail before flock')
    with pytest.raises(StoreError, match='recursive_use'):
        getattr(store, operation)(binding, 1, reenter)
    assert store.status()['state'] == ('refreshing' if operation == 'refresh' else 'active')


@pytest.mark.parametrize('operation', ['connect', 'refresh'])
@pytest.mark.parametrize('boundary', ['journal', 'envelope', 'publication'])
@pytest.mark.parametrize('failure', ['write', 'file_fsync', 'directory_fsync'])
def test_bounded_publication_faults_never_report_success_or_replay_old_revision(
        sample, monkeypatch, operation, boundary, failure):
    import stat
    store, binding, vault = sample
    if operation == 'refresh':
        connect(sample)
    vault.calls.clear()
    original_write, original_fsync = store._write, os.fsync
    tracking = {'target': False, 'injected': False}
    rotations = []
    def matches(name, data):
        if boundary == 'journal':
            return name.endswith('.journal')
        if boundary == 'envelope':
            return name.endswith('.enc')
        return name == 'current.json' and json.loads(data).get('body', json.loads(data))['phase'] == 'active'
    def write(name, data, **kwargs):
        tracking['target'] = matches(name, data)
        try:
            if tracking['target'] and failure == 'write' and not tracking['injected']:
                tracking['injected'] = True
                raise OSError('synthetic write fault')
            return original_write(name, data, **kwargs)
        finally:
            tracking['target'] = False
    def fsync(fd):
        directory = stat.S_ISDIR(os.fstat(fd).st_mode)
        wanted = (failure == 'directory_fsync' and directory) or (failure == 'file_fsync' and not directory)
        if tracking['target'] and wanted and not tracking['injected']:
            tracking['injected'] = True
            raise OSError('synthetic fsync fault')
        return original_fsync(fd)
    def rotate(value):
        rotations.append(1)
        return b'new-synthetic-grant'
    monkeypatch.setattr(store, '_write', write)
    monkeypatch.setattr(module.os, 'fsync', fsync)
    with pytest.raises(StoreError) as captured:
        if operation == 'connect':
            store.connect(binding, b'new-synthetic-grant', expected_revision=0)
        else:
            store.refresh(binding, 1, rotate)
    assert tracking['injected']
    assert captured.value.reason == ('publication_uncertain' if boundary == 'publication' else 'operation_failed')
    assert rotations == ([1] if operation == 'refresh' else [])
    current = state(store)
    visible_commit_uncertain = boundary == 'publication' and failure == 'directory_fsync'
    if visible_commit_uncertain:
        # Rename is visible, but the failing method did not claim durable success.
        assert current['phase'] == 'active'
        assert current['revision'] == (2 if operation == 'refresh' else 1)
    else:
        assert current['phase'] == ('refreshing' if operation == 'refresh' else 'disconnected')
        assert current['revision'] == (1 if operation == 'refresh' else 0)
    with pytest.raises(StoreError):
        store.consume(binding, 1 if operation == 'refresh' else 0,
                      lambda value: pytest.fail('old revision must never be replayed'))
    calls_before_cleanup = len(vault.calls)
    orphan_references = {
        json.loads(path.read_bytes())['reference']
        for path in store.directory.glob('*.journal') if path.stem != current['target']
    }
    assert store.recover_orphans() is False
    deleted = [reference for verb, reference in vault.calls[calls_before_cleanup:] if verb == 'delete']
    assert set(deleted) == orphan_references
    assert len(deleted) == len(orphan_references)
    assert state(store) == current
    assert store.recover_orphans() is False
    assert len(vault.values) == (1 if current['target'] else 0)


@pytest.mark.parametrize('after_rename', [False, True])
def test_disconnect_publication_fault_never_cleans_before_confirmed_tombstone(sample, monkeypatch, after_rename):
    store, binding, vault = sample
    connect(sample)
    old_target = state(store)['target']
    old_reference = next(iter(vault.values))
    old_cipher = (store.directory / (old_target + '.enc')).read_bytes()
    old_calls = list(vault.calls)
    original_write = store._write
    def fail(name, data, **kwargs):
        if name == 'current.json' and json.loads(data)['phase'] == 'disconnected':
            if after_rename:
                original_write(name, data, **kwargs)
            raise OSError('synthetic tombstone publication fault')
        return original_write(name, data, **kwargs)
    monkeypatch.setattr(store, '_write', fail)
    with pytest.raises(StoreError):
        store.disconnect(expected_revision=1)
    # Explicit retirement authenticates possible V1 provenance first. No
    # native put/delete or ciphertext cleanup may precede a durable tombstone.
    assert vault.calls == old_calls + [('consume', old_reference)]
    assert len(vault.values) == 1
    assert (store.directory / (old_target + '.enc')).read_bytes() == old_cipher
    current = state(store)
    assert current['revision'] == (2 if after_rename else 1)
    assert current['phase'] == ('disconnected' if after_rename else 'active')
    assert (store.directory / (old_target + '.journal')).exists()
    if after_rename:
        with pytest.raises(StoreError):
            store.consume(binding, 1, lambda value: pytest.fail('tombstone is authoritative'))
    monkeypatch.setattr(store, '_write', original_write)
    assert store.disconnect(expected_revision=current['revision'])['cleanup_pending'] is False
    assert vault.values == {}


GRANT_SCOPES = ('chatgpt.tokens.use.direct', 'openid', 'resource.invoke')


def grant_fields(**changes):
    values = dict(access_token='synthetic-access-only', refresh_token='synthetic-refresh-private',
                  id_token='synthetic-id-private', expires_at=2_000_000_000,
                  granted_scopes=GRANT_SCOPES, auth_attempt_id='synthetic-auth-attempt', expected_revision=0,
                  identity_evidence={'subject': 'verified-account', 'audience': 'verified-client',
                                     'nonce': 'synthetic-consent-nonce', 'authTime': None,
                                     'authorizedParty': None})
    values.update(changes)
    return values


def connect_grant(sample, **changes):
    store, binding, _ = sample
    store._clock = lambda: 1_900_000_000
    return store.connect_grant(binding, **grant_fields(**changes))


def new_lease(sample, **changes):
    store, binding, _ = sample
    values = dict(owner_generation=object(), owner_guard=lambda: True, cancel=threading.Event())
    values.update(changes)
    return store.lease_access_token(binding, 1, **values)


def test_typed_grant_requires_dedicated_handoff_and_never_writes_tokens_to_disk(sample):
    store, binding, vault = sample
    assert connect_grant(sample) == 1
    for path in store.directory.rglob('*'):
        if not path.is_file():
            continue
        data = path.read_bytes()
        assert all(value.encode() not in data for value in (
            'synthetic-access-only', 'synthetic-refresh-private', 'synthetic-id-private'))
    calls = []
    for operation in (store.consume, store.refresh):
        with pytest.raises(StoreError, match='typed_grant_requires_handoff'):
            operation(binding, 1, lambda value: calls.append(value))
    assert calls == []
    assert store.status()['state'] == 'active'
    assert len(vault.values) == 1


def test_typed_grant_cannot_be_injected_through_legacy_connect_or_refresh(sample):
    store, binding, _ = sample
    with pytest.raises(StoreError, match='typed_grant_requires_handoff'):
        store.connect(binding, module._GRANT_PREFIX + b'{}', expected_revision=0)
    connect(sample)
    with pytest.raises(StoreError, match='typed_grant_requires_handoff'):
        store.refresh(binding, 1, lambda value: module._GRANT_PREFIX + b'{}')
    assert store.status()['state'] == 'refreshing'


def test_access_only_lease_runs_outside_lock_once_and_clears_owned_buffer(sample):
    store, _, _ = sample
    connect_grant(sample)
    lease = new_lease(sample)
    def request(token, cancellation):
        assert token == 'synthetic-access-only'
        assert store.status()['state'] == 'active'  # Would fail recursion if locked.
        assert cancellation.is_set() is False
        assert not hasattr(cancellation, 'refresh_token')
        assert not hasattr(cancellation, 'id_token')
        return {'models': ['synthetic-model']}
    assert lease.run(request) == {'models': ['synthetic-model']}
    assert lease._token == bytearray()
    assert lease.is_set() is True
    with pytest.raises(StoreError, match='lease_used'):
        lease.run(lambda *_: pytest.fail('single-use capability'))


def test_lease_is_opaque_and_cannot_be_serialized(sample):
    import pickle
    connect_grant(sample)
    lease = new_lease(sample)
    assert repr(lease) == '<TokenLease server-private>'
    assert all(value not in repr(lease) for value in ('synthetic-access-only', 'synthetic-refresh-private', 'synthetic-id-private'))
    with pytest.raises(TypeError, match='cannot be serialized'):
        pickle.dumps(lease)
    lease.close()


@pytest.mark.parametrize('change', ['owner', 'cancel', 'expiry', 'disconnect'])
def test_lease_pre_admission_fences_prevent_request(sample, change):
    store, _, _ = sample
    connect_grant(sample)
    generation = object()
    current = [generation]
    cancel = threading.Event()
    lease = new_lease(sample, owner_generation=generation,
                      owner_guard=lambda: current[0] is generation, cancel=cancel)
    if change == 'owner':
        current[0] = object()
    elif change == 'cancel':
        cancel.set()
    elif change == 'expiry':
        store._clock = lambda: 2_000_000_000
    else:
        SubscriptionStore(store.profile, backend=sample[2]).disconnect(expected_revision=1)
    with pytest.raises(StoreError):
        lease.run(lambda *_: pytest.fail('preflight must prevent request'))
    assert lease._token == bytearray()
    with pytest.raises(StoreError, match='lease_used'):
        lease.run(lambda *_: pytest.fail('failed admission is consumed'))


@pytest.mark.parametrize('change', ['owner', 'cancel', 'expiry', 'disconnect', 'refreshing'])
def test_lease_late_reply_is_rejected_after_retirement(sample, change):
    store, _, _ = sample
    connect_grant(sample)
    generation, cancel = object(), threading.Event()
    current = [generation]
    lease = new_lease(sample, owner_generation=generation,
                      owner_guard=lambda: current[0] is generation, cancel=cancel)
    requests = []
    def request(token, cancellation):
        requests.append(1)
        if change == 'owner':
            current[0] = object()
        elif change == 'cancel':
            cancel.set()
        elif change == 'expiry':
            store._clock = lambda: 2_000_000_000
        elif change == 'disconnect':
            SubscriptionStore(store.profile, backend=sample[2]).disconnect(expected_revision=1)
        else:
            # Simulate a durable refresh fence from a future coordinator/process.
            pending = dict(state(store), phase='refreshing')
            store._write('current.json', module._json(pending), replace=True)
        assert cancellation.is_set()
        return {'models': ['late-result-must-not-escape']}
    with pytest.raises(StoreError):
        lease.run(request)
    assert requests == [1]
    assert lease._token == bytearray()


def test_inflight_request_does_not_block_disconnect_and_late_result_is_discarded(sample):
    store, _, vault = sample
    connect_grant(sample)
    lease = new_lease(sample)
    started, release, disconnected = threading.Event(), threading.Event(), threading.Event()
    errors, results, removals = [], [], []
    def request(token, cancellation):
        started.set()
        assert release.wait(10)
        assert cancellation.is_set()
        return 'late'
    def run():
        try:
            results.append(lease.run(request))
        except StoreError as error:
            errors.append(error.reason)
    def remove():
        try:
            removals.append(SubscriptionStore(store.profile, backend=vault).disconnect(expected_revision=1))
        finally:
            disconnected.set()
    worker = threading.Thread(target=run)
    remover = threading.Thread(target=remove)
    worker.start()
    try:
        assert started.wait(5)
        remover.start()
        # Request remains deliberately blocked. Disconnect must finish first,
        # rather than passing only because the request eventually times out.
        assert disconnected.wait(5)
        assert removals[0]['state'] == 'disconnected'
    finally:
        release.set()
        worker.join(5)
        if remover.ident is not None:
            remover.join(5)
    assert not worker.is_alive() and not remover.is_alive()
    assert results == [] and len(errors) == 1


def test_lease_callback_errors_remain_secret_free(sample):
    import traceback
    connect_grant(sample)
    lease = new_lease(sample)
    def request(token, cancellation):
        raise StoreError(token)
    with pytest.raises(StoreError) as captured:
        lease.run(request)
    exposed = str(captured.value) + repr(captured.value) + ''.join(traceback.format_exception(captured.value))
    assert 'synthetic-access-only' not in exposed
    assert lease._token == bytearray()


@pytest.mark.parametrize('fields', [
    {'access_token': ''}, {'refresh_token': ''}, {'id_token': ''},
    {'access_token': 'bad token'}, {'expires_at': True}, {'expires_at': float('nan')},
    {'granted_scopes': ('openid', 'openid')}, {'auth_attempt_id': 'has spaces'},
])
def test_invalid_typed_grants_fail_before_native_put(sample, fields):
    store, binding, vault = sample
    with pytest.raises(StoreError):
        store.connect_grant(binding, **grant_fields(**fields))
    assert vault.calls == []
    assert not store.directory.exists()


def test_missing_inference_scopes_and_expiry_prevent_lease(sample):
    store, binding, _ = sample
    connect_grant(sample, granted_scopes=('openid',))
    with pytest.raises(StoreError, match='scope_not_authorized'):
        new_lease(sample)
    store.disconnect(expected_revision=1)
    store.connect_grant(binding, **grant_fields(expected_revision=2, expires_at=1_800_000_000))
    with pytest.raises(StoreError, match='grant_expired'):
        store.lease_access_token(binding, 3, owner_generation=object(), owner_guard=lambda: True, cancel=threading.Event())


def test_exact_authenticated_auth_attempt_reconciliation(sample):
    store, binding, vault = sample
    connect_grant(sample)
    reference = next(iter(vault.values))
    vault.calls.clear()
    assert store.reconcile_auth_attempt(binding, auth_attempt_id='synthetic-auth-attempt',
                                        prior_store_revision=0, granted_scopes=GRANT_SCOPES) == 1
    # Transition reconciliation authenticates the active envelope, then the
    # exact auth-attempt check independently decrypts that same current target.
    assert vault.calls == [('consume', reference), ('consume', reference)]
    for changes in ({'auth_attempt_id': 'wrong-attempt'}, {'prior_store_revision': 1},
                    {'granted_scopes': ('openid',)}):
        args = dict(auth_attempt_id='synthetic-auth-attempt', prior_store_revision=0, granted_scopes=GRANT_SCOPES)
        args.update(changes)
        with pytest.raises(StoreError):
            store.reconcile_auth_attempt(binding, **args)
    with pytest.raises(StoreError):
        store.reconcile_auth_attempt(replace(binding, account_id='other'), auth_attempt_id='synthetic-auth-attempt',
                                     prior_store_revision=0, granted_scopes=GRANT_SCOPES)


def test_typed_grant_authenticates_fixed_issuer(sample):
    store, binding, _ = sample
    connect_grant(sample)
    with store._locked():
        payload = module._parse_grant(store._decrypt(state(store)), binding)
    assert payload['issuer'] == 'https://auth.openai.com'


@pytest.mark.parametrize('issuer', [None, False, 1, [], {}, '', 'https://foreign.invalid'])
def test_authenticated_malformed_issuer_refuses_handoff(sample, monkeypatch, issuer):
    store, binding, _ = sample
    connect_grant(sample)
    with store._locked():
        payload = module._parse_grant(store._decrypt(state(store)), binding)
    payload['issuer'] = issuer
    monkeypatch.setattr(store, '_decrypt', lambda state: module._GRANT_PREFIX + module._json(payload))
    with pytest.raises(StoreError, match='invalid_grant'):
        new_lease(sample)
    with pytest.raises(StoreError):
        store.reconcile_auth_attempt(binding, auth_attempt_id='synthetic-auth-attempt',
                                     prior_store_revision=0, granted_scopes=GRANT_SCOPES)


def test_uncertain_committed_grant_reconciles_without_republishing_or_exchanging(sample, monkeypatch):
    store, binding, vault = sample
    original_write = store._write
    def uncertain(name, data, **kwargs):
        original_write(name, data, **kwargs)
        if name == 'current.json' and json.loads(data).get('body', json.loads(data))['phase'] == 'active':
            raise OSError('synthetic post-publication fsync error')
    monkeypatch.setattr(store, '_write', uncertain)
    with pytest.raises(StoreError, match='publication_uncertain'):
        connect_grant(sample)
    assert store.status()['state'] == 'active'
    before = list(vault.calls)
    assert store.reconcile_auth_attempt(binding, auth_attempt_id='synthetic-auth-attempt',
                                        prior_store_revision=0, granted_scopes=GRANT_SCOPES) == 1
    assert vault.calls[len(before):] and all(verb == 'consume' for verb, _ in vault.calls[len(before):])


def test_reconciliation_requires_authentication_not_status_match(sample):
    store, binding, _ = sample
    connect_grant(sample)
    path = store.directory / module.TICKET_ARCHIVE / (state(store)['target'] + '.enc')
    value = path.read_bytes()
    path.write_bytes(value[:-1] + bytes([value[-1] ^ 1]))
    assert store.status()['state'] == 'active'
    with pytest.raises(StoreError):
        store.reconcile_auth_attempt(binding, auth_attempt_id='synthetic-auth-attempt',
                                     prior_store_revision=0, granted_scopes=GRANT_SCOPES)


def test_legacy_payload_cannot_mint_lease_or_reconcile(sample):
    store, binding, _ = sample
    connect(sample)
    with pytest.raises(StoreError, match='invalid_grant'):
        new_lease(sample)
    with pytest.raises(StoreError, match='invalid_grant'):
        store.reconcile_auth_attempt(binding, auth_attempt_id='synthetic-auth-attempt',
                                     prior_store_revision=0, granted_scopes=GRANT_SCOPES)


def test_private_restart_descriptor_never_reads_vault_and_rejects_tombstone(sample):
    store, binding, vault = sample
    with pytest.raises(StoreError, match='connection_unavailable'):
        store.connection_descriptor()
    connect_grant(sample)
    vault.calls.clear()
    restarted = SubscriptionStore(store.profile, backend=vault)
    assert restarted.connection_descriptor() == (binding, 1)
    assert vault.calls == []
    restarted.disconnect(expected_revision=1)
    with pytest.raises(StoreError, match='connection_unavailable'):
        restarted.connection_descriptor()



def test_returning_binding_preserves_only_identity_after_disconnect_restart(sample):
    store, binding, vault = sample
    assert store.returning_binding() is None
    assert not store.directory.exists() and vault.calls == []
    connect_grant(sample)
    store.disconnect(expected_revision=1)
    vault.calls.clear()
    restarted = SubscriptionStore(store.profile, backend=vault)
    assert restarted.returning_binding() == binding
    assert vault.calls == []
    with pytest.raises(StoreError, match='connection_unavailable'):
        restarted.connection_descriptor()
    with pytest.raises(StoreError):
        restarted.lease_access_token(binding, 2, owner_generation=object(),
                                     owner_guard=lambda: True, cancel=threading.Event())
    assert vault.calls == []


@pytest.mark.parametrize('corruption', ['lost_pointer', 'scope_anchor', 'profile_locator'])
def test_private_identity_reads_refuse_corrupt_or_lost_scope_without_vault(sample, corruption):
    store, _, vault = sample
    connect_grant(sample)
    vault.calls.clear()
    if corruption == 'lost_pointer':
        (store.directory / 'current.json').unlink()
    elif corruption == 'scope_anchor':
        anchor = json.loads((store.directory / 'identity.json').read_bytes())
        anchor['scope']['organization_id'] = str(uuid.uuid4())
        store._write('identity.json', module._json(anchor), replace=True)
    else:
        current = state(store)
        current['binding']['profile_locator'] = '/different/profile'
        anchor = json.loads((store.directory / 'identity.json').read_bytes())
        anchor['scope']['profile_locator'] = '/different/profile'
        store._write('identity.json', module._json(anchor), replace=True)
        store._write('current.json', module._json(current), replace=True)
    for operation in (store.returning_binding, store.connection_descriptor):
        with pytest.raises(StoreError):
            operation()
    assert vault.calls == []


def test_lease_rechecks_owner_and_cancel_after_decryption(sample):
    store, _, vault = sample
    connect_grant(sample)
    cancellation = threading.Event()
    vault.after_consume = cancellation.set
    with pytest.raises(StoreError, match='lease_cancelled'):
        new_lease(sample, cancel=cancellation)


@pytest.mark.parametrize('lazy', ['coroutine', 'generator'])
def test_lease_rejects_deferred_network_work(sample, lazy):
    connect_grant(sample)
    lease = new_lease(sample)
    executed = []
    async def deferred(token):
        executed.append(token)
        return []
    def generated(token):
        executed.append(token)
        yield []
    with pytest.raises(StoreError, match='invalid_lease_result'):
        lease.run(lambda token, cancellation: deferred(token) if lazy == 'coroutine' else generated(token))
    assert executed == []
    assert lease._token == bytearray()


def test_first_consent_prehead_final_journal_reconciles_without_second_put(sample, monkeypatch):
    store, binding, vault = sample
    original = store._write
    def before_publication(name, data, **kwargs):
        if name == 'current.json' and json.loads(data).get('body', json.loads(data))['phase'] == 'active':
            raise OSError('synthetic before-rename publication fault')
        return original(name, data, **kwargs)
    monkeypatch.setattr(store, '_write', before_publication)
    with pytest.raises(StoreError, match='publication_uncertain'):
        connect_grant(sample)
    assert state(store)['candidate']['state'] == 'reserved'
    monkeypatch.setattr(store, '_write', original)
    puts = [call for call in vault.calls if call[0] == 'put']
    assert store.reconcile_auth_attempt(binding, auth_attempt_id='synthetic-auth-attempt',
                                        prior_store_revision=0, granted_scopes=GRANT_SCOPES) == 1
    assert [call for call in vault.calls if call[0] == 'put'] == puts
    assert state(store)['phase'] == 'active'


@pytest.mark.macos_only
def test_native_darwin_empty_acl_is_accepted_without_permission_changes(tmp_path):
    """Read-only OS proof on a new synthetic path; never inspect user grants."""
    from eidolon_cli.organization_setup_subscription_store import _check_darwin_acl
    if module.sys.platform != 'darwin':
        pytest.skip('Read-only native Darwin ACL proof requires macOS')
    before = tmp_path.stat()
    _check_darwin_acl(tmp_path)
    after = tmp_path.stat()
    assert (after.st_mode, after.st_uid, after.st_gid) == (before.st_mode, before.st_uid, before.st_gid)


@pytest.mark.parametrize('path,value', [
    (('schemaVersion',), True), (('authorizationEpoch',), str(uuid.uuid1())),
    (('tokenRevision',), True), (('tokenRevision',), 2), (('unexpected',), None),
    (('consent', 'consentVersion'), True), (('consent', 'priorStoreRevision'), True),
    (('consent', 'extra'), 'field'), (('consent', 'grantedScopes'), ['openid']),
    (('identityEvidence', 'subject'), 'different-account'),
    (('identityEvidence', 'audience'), 'different-client'),
    (('identityEvidence', 'authTime'), True), (('identityEvidence', 'nonce'), ''),
    (('identityEvidence', 'authorizedParty'), 'different-client'),
    (('rotation',), {'refreshAttemptId': 'x', 'priorTokenRevision': 0}),
])
def test_v2_strict_authenticated_schema(sample, path, value):
    store, binding, _ = sample
    connect_grant(sample)
    with store._locked():
        payload = module._parse_grant(store._decrypt(state(store)), binding)
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(StoreError):
        module._parse_grant(module._GRANT_PREFIX + module._json(payload), binding)


def test_v2_duplicate_fields_and_token_size_refused(sample):
    store, binding, _ = sample
    connect_grant(sample)
    with store._locked():
        raw = store._decrypt(state(store))
    duplicate = raw.replace(b'"schemaVersion":2', b'"schemaVersion":2,"schemaVersion":2')
    with pytest.raises(StoreError):
        module._parse_grant(duplicate, binding)
    payload = module._parse_grant(raw, binding)
    payload['accessToken'] = 'x' * 16385
    with pytest.raises(StoreError):
        module._parse_grant(module._GRANT_PREFIX + module._json(payload), binding)


@pytest.mark.parametrize('secret', [b'EIDOLON_SETUP_GRANT_V1', b'EIDOLON_SETUP_GRANT_V2',
                                    b'EIDOLON_SETUP_GRANT_V1\n{}', b'EIDOLON_SETUP_GRANT_V2\n{}'])
def test_generic_paths_never_handle_typed_prefixes(sample, secret):
    store, binding, _ = sample
    with pytest.raises(StoreError):
        store.connect(binding, secret, expected_revision=0)
    with store._locked(create=True):
        initial = store._initialize(binding)
        store._publish(binding, secret, initial)
    with pytest.raises(StoreError):
        store.consume(binding, 1, lambda value: pytest.fail('typed secret escaped'))
    with pytest.raises(StoreError):
        store.refresh(binding, 1, lambda value: pytest.fail('typed secret escaped'))
    assert store.disconnect(expected_revision=1)['state'] == 'disconnected'


@pytest.mark.parametrize('kind', ['access', 'refresh', 'revoke'])
def test_real_lease_network_predicates_never_wait_for_another_store_flock(sample, kind):
    store, _, vault = sample
    connect_grant(sample)
    lease = (new_lease(sample) if kind == 'access' else refresh_lease(sample)
             if kind == 'refresh' else disconnect_v2(sample)[1])
    second = SubscriptionStore(store.profile, backend=vault)
    held, release, checked = threading.Event(), threading.Event(), threading.Event()
    result, errors = [], []
    def hold():
        try:
            with second._locked():
                held.set()
                assert release.wait(10)
        except BaseException as error:
            errors.append(error)
    def probe():
        try:
            result.append((lease.network_cancelled(), lease.try_send_allowed()))
        finally:
            checked.set()
    holder = threading.Thread(target=hold)
    reader = threading.Thread(target=probe)
    holder.start()
    try:
        assert held.wait(5)
        reader.start()
        assert checked.wait(1), 'Network cancellation or send fence waited for flock'
        assert result == [(False, False)]
        assert not release.is_set() and holder.is_alive()
    finally:
        release.set()
        holder.join(5)
        if reader.ident is not None:
            reader.join(5)
        lease.close()
    assert not errors and not holder.is_alive() and not reader.is_alive()


@pytest.mark.parametrize('kind', ['access', 'refresh', 'revoke'])
def test_real_lease_nonblocking_send_fence_rechecks_generation_and_cancellation(sample, kind):
    connect_grant(sample)
    allowed, cancellation = [True], threading.Event()
    def guard():
        return allowed[0]
    lease = (new_lease(sample, owner_guard=guard, cancel=cancellation) if kind == 'access'
             else refresh_lease(sample, owner_guard=guard, cancel=cancellation) if kind == 'refresh'
             else disconnect_v2(sample, owner_guard=guard, cancel=cancellation)[1])
    assert lease.try_send_allowed() is True
    allowed[0] = False
    assert lease.network_cancelled() is False  # Pure event check does no owner work.
    assert lease.try_send_allowed() is False
    allowed[0] = True
    cancellation.set()
    assert lease.network_cancelled() is True and lease.try_send_allowed() is False
    lease.close()


@pytest.mark.parametrize('kind', ['access', 'refresh', 'revoke'])
@pytest.mark.parametrize('delayed_connect', [False, True])
def test_http_real_lease_held_store_lock_is_bounded_and_never_sends(sample, monkeypatch, kind, delayed_connect):
    """Actual lease predicates plus actual adapter; synthetic socket only."""
    from eidolon_cli import organization_setup_subscription_http as http_adapter
    store, _, vault = sample
    connect_grant(sample)
    lease = (new_lease(sample) if kind == 'access' else refresh_lease(sample)
             if kind == 'refresh' else disconnect_v2(sample)[1])
    second = SubscriptionStore(store.profile, backend=vault)
    held, release_store = threading.Event(), threading.Event()
    connect_entered, release_connect = threading.Event(), threading.Event()
    connect_returned, late_closed, returned = threading.Event(), threading.Event(), threading.Event()
    sends, outcomes, holder_errors = [], [], []
    class Socket:
        def shutdown(self, how):
            pass
    class Connection:
        def __init__(self, host, timeout):
            self.sock = None
        def connect(self):
            connect_entered.set()
            if delayed_connect:
                assert release_connect.wait(5)
            self.sock = Socket()
            connect_returned.set()
        def request(self, method, path, body, headers):
            self.send(b'synthetic-headers')
            if body is not None:
                self.send(body)
        def send(self, data):
            sends.append(data)
        def getresponse(self):
            pytest.fail('No physical send was authorized under store contention')
        def close(self):
            self.sock = None
            if connect_returned.is_set():
                late_closed.set()
    monkeypatch.setattr(http_adapter.http.client, 'HTTPSConnection', Connection)
    def hold():
        try:
            with second._locked():
                held.set()
                assert release_store.wait(5)
        except BaseException as error:
            holder_errors.append(error)
    slots = threading.BoundedSemaphore(1)
    def call():
        try:
            http_adapter.request(http_adapter.MODELS, bearer='synthetic', cancel=lease,
                                 _timeout=.1, _slots=slots)
            outcomes.append('unexpected-success')
        except http_adapter.HttpError:
            outcomes.append('failed-closed')
        finally:
            returned.set()
    holder, caller = threading.Thread(target=hold), threading.Thread(target=call)
    holder.start()
    try:
        assert held.wait(2)
        assert lease.network_cancelled() is False
        caller.start()
        assert returned.wait(1), 'HTTP caller exceeded its bounded deadline on store flock'
        assert outcomes == ['failed-closed'] and sends == []
        assert holder.is_alive() and not release_store.is_set()
        if delayed_connect:
            assert connect_entered.is_set() and not connect_returned.is_set()
            assert slots.acquire(blocking=False) is False
            release_connect.set()
            assert late_closed.wait(2), 'Late connector did not close its socket'
            assert sends == []
    finally:
        release_connect.set()
        release_store.set()
        holder.join(5)
        if caller.ident is not None:
            caller.join(5)
        lease.close()
    assert not holder_errors and not holder.is_alive() and not caller.is_alive()
    assert sends == []


def v1_secret(binding, **changes):
    payload = {'schemaVersion': 1, 'issuer': module._GRANT_ISSUER,
               'authAttemptId': 'original-v1-consent', 'priorStoreRevision': 0,
               'verifiedBinding': asdict(binding), 'grantedScopes': sorted(GRANT_SCOPES),
               'expiresAt': 2_000_000_000, 'accessToken': 'v1-access',
               'refreshToken': 'v1-refresh', 'idToken': 'v1-id'}
    payload.update(changes)
    return module._OLD_GRANT_PREFIX + module._json(payload)



def guarded():
    return {'owner_guard': lambda: True, 'cancel': threading.Event()}


def prepare(sample, kind='refresh', revision=None, source='a' * 64):
    store, binding, _ = sample
    return store.prepare_ticket(binding, state(store)['revision'] if revision is None else revision,
                                kind=kind, source_revision=source, **guarded())


def refresh_lease(sample, **changes):
    store, binding, _ = sample
    args = dict(ticket=prepare(sample), owner_generation=object(), **guarded())
    args.update(changes)
    return store.admit_refresh(binding, args['ticket']['priorStoreRevision'], **args)


def refresh_commit(sample, lease, **changes):
    store, binding, _ = sample
    args = dict(ticket=lease._ticket, access_token='rotated-access', refresh_token='rotated-refresh',
                id_token=None, expires_at=2_100_000_000, granted_scopes=GRANT_SCOPES,
                identity_evidence=lease.identity_evidence, **guarded())
    args.update(changes)
    return store.commit_refresh(binding, **args)


def disconnect_v2(sample, **changes):
    store, binding, _ = sample
    ticket = changes.pop('ticket', None) or prepare(sample, 'revoke')
    args = dict(expected_revision=ticket['priorStoreRevision'], ticket=ticket, **guarded())
    args.update(changes)
    return store.disconnect_grant(binding, **args)


def restart(sample):
    store, binding, vault = sample
    other = SubscriptionStore(store.profile, backend=vault)
    other._clock = store._clock
    return other, binding, vault


def test_ticket_more_than_64_rotations_preserve_epoch_and_consent(sample, monkeypatch):
    store, binding, _ = sample
    connect_grant(sample)
    original = store.grant_descriptor(binding, 1)
    tickets = []
    for index in range(70):
        lease = refresh_lease(sample)
        tickets.append(lease._ticket)
        assert lease.run(lambda token, cancel: refresh_commit(sample, lease)) == index + 2
        descriptor = store.grant_descriptor(binding, index + 2)
        assert descriptor['authorizationEpoch'] == original['authorizationEpoch']
        assert descriptor['consent'] == original['consent']
        assert descriptor['identityEvidence'] == original['identityEvidence']
    assert [t['sequence'] for t in tickets] == [str(i) for i in range(1, 71)]
    assert store.history_status() == {'state': 'available', 'issuedThrough': '70', 'retiredThrough': '69'}
    archive = store.directory / module.TICKET_ARCHIVE
    assert len(list(archive.glob('window-*.json'))) == 69
    # Neither growing grant journals nor immutable receipts may be scanned.
    monkeypatch.setattr(Path, 'iterdir', lambda path: pytest.fail('unbounded directory scan'))
    monkeypatch.setattr(Path, 'glob', lambda path, pattern: pytest.fail('unbounded glob'))
    assert store.status()['state'] == 'active'
    assert store.ticket_status()['state'] == 'completed'
    assert store.history_status()['issuedThrough'] == '70'
    assert store.grant_descriptor(binding, 71)['tokenRevision'] == 71
    ticket = prepare(sample, revision=71)
    assert ticket['sequence'] == '71'
    lease = store.admit_refresh(binding, 71, ticket=ticket, owner_generation=object(), **guarded())
    lease.close()


def test_ticket_prepare_ack_loss_recovery_and_restart_cancel(sample):
    store, binding, vault = sample
    connect_grant(sample)
    ticket = prepare(sample)
    restarted, _, _ = restart(sample)
    calls = list(vault.calls)
    hint = restarted.ticket_status()
    assert hint == {'state': 'prepared', 'ticket': ticket, 'revision': 1, 'verification': 'metadata_only'}
    assert vault.calls == calls
    assert prepare((restarted, binding, vault)) == ticket
    result = restarted.cancel_ticket(binding, ticket=ticket, **guarded())
    assert result['state'] == 'none'
    assert restarted.history_status()['retiredThrough'] == '1'
    with pytest.raises(StoreError, match='ticket_retired'):
        restarted.admit_refresh(binding, 1, ticket=ticket, owner_generation=object(), **guarded())
    assert prepare((restarted, binding, vault))['sequence'] == '2'


def test_ticket_single_cross_instance_admission_and_no_restart_lease(sample):
    store, binding, _ = sample
    connect_grant(sample)
    ticket = prepare(sample)
    other, _, _ = restart(sample)
    gate = threading.Barrier(2)
    leases, errors = [], []
    def admit(target):
        gate.wait()
        try:
            leases.append(target.admit_refresh(binding, 1, ticket=ticket, owner_generation=object(), **guarded()))
        except StoreError as error:
            errors.append(error.reason)
    threads = [threading.Thread(target=admit, args=(target,)) for target in (store, other)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert all(not thread.is_alive() for thread in threads)
    assert len(leases) == 1 and errors == ['ticket_conflict']
    leases[0].close()
    result = other.reconcile_transition(binding, **guarded())
    assert result['state'] == 'admitted' and result['ticket'] == ticket
    with pytest.raises(StoreError):
        new_lease(sample)
    with pytest.raises(StoreError):
        other.cancel_ticket(binding, ticket=ticket, **guarded())


@pytest.mark.parametrize('kind', ['refresh', 'revoke'])
@pytest.mark.parametrize('boundary', ['pending', 'head', 'archive', 'empty'])
@pytest.mark.parametrize('after', [False, True])
def test_admission_fault_recovery_never_mints_capability(sample, monkeypatch, kind, boundary, after):
    store, binding, _ = sample
    connect_grant(sample)
    ticket = prepare(sample, kind)
    write, archive = store._write, store._archive_put
    fired = []
    def fail(action, callback):
        if not fired:
            fired.append(action)
            if after:
                callback()
            raise OSError('synthetic capacity failure')
        return callback()
    def writing(name, data, **kwargs):
        value = json.loads(data)
        body = value.get('body', value)
        hit = ((boundary == 'pending' and name == 'ticket-pending.json' and body.get('state') == 'transition')
               or (boundary == 'head' and name == 'current.json')
               or (boundary == 'empty' and name == 'ticket-pending.json' and body.get('state') == 'empty'))
        return fail(name, lambda: write(name, data, **kwargs)) if hit else write(name, data, **kwargs)
    def archiving(name, data):
        return fail(name, lambda: archive(name, data)) if boundary == 'archive' else archive(name, data)
    monkeypatch.setattr(store, '_write', writing)
    monkeypatch.setattr(store, '_archive_put', archiving)
    with pytest.raises(StoreError, match='publication_uncertain'):
        if kind == 'refresh':
            store.admit_refresh(binding, 1, ticket=ticket, owner_generation=object(), **guarded())
        else:
            disconnect_v2(sample, ticket=ticket)
    assert fired
    monkeypatch.setattr(store, '_write', write)
    monkeypatch.setattr(store, '_archive_put', archive)
    result = store.reconcile_transition(binding, **guarded())
    # A failed write before any pending bytes leaves the prepared authority;
    # all durably journaled admissions recover as metadata, never capability.
    assert result['state'] == ('prepared' if boundary == 'pending' and not after else 'admitted')
    assert set(result) == {'state', 'ticket', 'receipt', 'revision', 'verification'}
    if result['state'] == 'admitted':
        if kind == 'refresh':
            with pytest.raises(StoreError):
                store.admit_refresh(binding, 1, ticket=ticket, owner_generation=object(), **guarded())
        else:
            receipt, lease = disconnect_v2(sample, ticket=ticket)
            assert lease is None and receipt['remoteRevocation'] == 'unconfirmed'


def test_refresh_commit_exact_proof_and_uncertain_publication(sample, monkeypatch):
    store, binding, _ = sample
    connect_grant(sample)
    lease = refresh_lease(sample)
    ticket = lease._ticket
    original = store._write
    def fail(name, data, **kwargs):
        original(name, data, **kwargs)
        if name == 'current.json' and json.loads(data)['body']['phase'] == 'active':
            raise OSError('after rename')
    monkeypatch.setattr(store, '_write', fail)
    with pytest.raises(StoreError):
        refresh_commit(sample, lease)
    monkeypatch.setattr(store, '_write', original)
    lease.close()
    result = store.reconcile_transition(binding, **guarded())
    assert result['state'] == 'completed' and result['revision'] == 2
    assert store.reconcile_ticket(binding, ticket=ticket, **guarded()) == result
    with pytest.raises(StoreError):
        store.reconcile_ticket(binding, ticket=dict(ticket, nonce='f' * 64), **guarded())
    with store._locked():
        payload = module._parse_grant(store._decrypt(state(store)), binding)
    assert payload['rotation']['refreshAttemptId'] == store._ticket_fingerprint(ticket)
    assert payload['idToken'] == 'synthetic-id-private'


def test_refresh_close_after_commit_discards_reply(sample):
    connect_grant(sample)
    lease = refresh_lease(sample)
    def request(token, cancel):
        result = refresh_commit(sample, lease)
        lease.close()
        return result
    with pytest.raises(StoreError, match='lease_cancelled'):
        lease.run(request)


def test_revoke_replay_reauthorization_and_retired_ticket_never_post(sample):
    store, binding, _ = sample
    connect_grant(sample)
    ticket = prepare(sample, 'revoke')
    receipt, lease = disconnect_v2(sample, ticket=ticket)
    assert lease.run(lambda token, cancel: True)['remoteRevocation'] == 'confirmed'
    other, _, _ = restart(sample)
    receipt, again = other.disconnect_grant(binding, expected_revision=1, ticket=ticket, **guarded())
    assert again is None and receipt['remoteRevocation'] == 'confirmed'
    connect_grant(sample, expected_revision=2, auth_attempt_id='fresh-consent')
    assert store.grant_descriptor(binding, 3)['authorizationEpoch'] != ticket['authorizationEpoch']
    with pytest.raises(StoreError, match='ticket_retired'):
        disconnect_v2(sample, ticket=ticket)
    assert prepare(sample, 'refresh')['sequence'] == '2'


@pytest.mark.parametrize('artifact', ['ticket-anchor.json', 'ticket-pending.json', 'ticket-barrier.json', 'intent'])
@pytest.mark.parametrize('corrupt', [False, True])
def test_missing_active_provenance_never_restored_from_archives(sample, artifact, corrupt):
    store, binding, vault = sample
    connect_grant(sample)
    ticket = prepare(sample)
    path = (store.directory / module.TICKET_ARCHIVE / ('intent-' + store._ticket_fingerprint(ticket) + '.json')
            if artifact == 'intent' else store.directory / artifact)
    if corrupt:
        path.write_bytes(b'{}')
    else:
        path.unlink()
    before = list(vault.calls)
    assert store.ticket_status()['state'] == 'recovery_required'
    assert store.history_status()['state'] == 'recovery_required'
    assert vault.calls == before
    with pytest.raises(StoreError):
        new_lease(sample)
    with pytest.raises(StoreError):
        store.admit_refresh(binding, 1, ticket=ticket, owner_generation=object(), **guarded())
    with pytest.raises(StoreError):
        store.reconcile_transition(binding, **guarded())


def test_unknown_files_and_passive_bootstrap_never_initialize(sample):
    store, binding, vault = sample
    assert store.history_status()['state'] == 'available'
    assert store.ticket_status()['state'] == 'none'
    assert not store.directory.exists() and not vault.calls
    connect(sample)
    assert store.history_status()['state'] == 'legacy'
    store.local_disconnect(binding, expected_revision=1, **guarded())
    before = {p.name: p.read_bytes() for p in store.directory.iterdir() if p.is_file()}
    calls = list(vault.calls)
    assert store.history_status()['state'] == 'legacy'
    assert before == {p.name: p.read_bytes() for p in store.directory.iterdir() if p.is_file()}
    assert vault.calls == calls
    with pytest.raises(StoreError):
        connect_grant(sample, expected_revision=2)


def test_verified_v1_explicit_retirement_permits_fresh_consent(sample):
    store, binding, vault = sample
    store._connect_payload(binding, v1_secret(binding), 0)
    assert store.history_status()['state'] == 'legacy'
    with pytest.raises(StoreError, match='reauthorization_required'):
        new_lease(sample)
    receipt = store.local_disconnect(binding, expected_revision=1, **guarded())
    assert receipt['localRetired'] is True
    calls = list(vault.calls)
    files = {p.name: p.read_bytes() for p in store.directory.iterdir() if p.is_file()}
    assert store.history_status() == {'state': 'available', 'issuedThrough': '0', 'retiredThrough': '0'}
    assert calls == vault.calls and files == {p.name: p.read_bytes() for p in store.directory.iterdir() if p.is_file()}
    assert connect_grant(sample, expected_revision=2) == 3
    assert prepare(sample)['sequence'] == '1'


@pytest.mark.parametrize('changes', [{'issuer': 'foreign'}, {'schemaVersion': True}, {'unexpected': 1},
                                     {'grantedScopes': ['openid', 'openid']}, {'accessToken': '\n'}])
def test_v1_bootstrap_requires_exact_original_schema(sample, changes):
    store, binding, _ = sample
    store._connect_payload(binding, v1_secret(binding, **changes), 0)
    store.local_disconnect(binding, expected_revision=1, **guarded())
    assert not (store.directory / 'history-v1-bootstrap.json').exists()
    assert store.history_status()['state'] == 'legacy'


def test_v1_empty_scopes_only_establish_retirement_provenance(sample):
    store, binding, vault = sample
    store._connect_payload(binding, v1_secret(binding, grantedScopes=[]), 0)
    with pytest.raises(StoreError, match='reauthorization_required'):
        new_lease(sample)
    receipt = store.local_disconnect(binding, expected_revision=1, **guarded())
    assert receipt['localRetired'] is True
    assert (store.directory / 'history-v1-bootstrap.json').exists()
    assert store.history_status() == {'state': 'available', 'issuedThrough': '0', 'retiredThrough': '0'}
    assert vault.values == {}
    with pytest.raises(StoreError):
        new_lease(sample)
    # Provenance permits a separately consented replacement, never reuse of
    # the retired empty-scope grant or access without fresh authorization.
    assert connect_grant(sample, expected_revision=2, auth_attempt_id='fresh-consent') == 3
    assert prepare(sample)['sequence'] == '1'


def test_unreadable_v1_still_locally_retires_without_bootstrap(sample, monkeypatch):
    store, binding, _ = sample
    store._connect_payload(binding, v1_secret(binding), 0)
    monkeypatch.setattr(store, '_decrypt', lambda state: (_ for _ in ()).throw(StoreError('authentication_failed')))
    assert store.local_disconnect(binding, expected_revision=1, **guarded())['localRetired']
    assert store.history_status()['state'] == 'legacy'


@pytest.mark.parametrize('stage', ['prepared', 'admitted'])
@pytest.mark.parametrize('boundary', ['barrier_before', 'barrier_after', 'head', 'empty', 'clear'])
def test_local_barrier_capacity_recovery_never_returns_active_lease(sample, monkeypatch, stage, boundary):
    store, binding, _ = sample
    connect_grant(sample)
    ticket = prepare(sample)
    lease = (store.admit_refresh(binding, 1, ticket=ticket, owner_generation=object(), **guarded())
             if stage == 'admitted' else new_lease(sample))
    original = store._write
    fired = []
    def fail(name, data, **kwargs):
        body = json.loads(data).get('body', {})
        hit = ((boundary.startswith('barrier_') and name == 'ticket-barrier.json' and body.get('state') == 'active')
               or (boundary == 'head' and name == 'current.json')
               or (boundary == 'empty' and name == 'ticket-pending.json' and body.get('state') == 'empty')
               or (boundary == 'clear' and name == 'ticket-barrier.json' and body.get('state') == 'clear'))
        if hit and not fired:
            fired.append(name)
            if boundary == 'barrier_after':
                original(name, data, **kwargs)
            raise OSError('synthetic storage capacity')
        return original(name, data, **kwargs)
    monkeypatch.setattr(store, '_write', fail)
    with pytest.raises(StoreError):
        store.local_disconnect(binding, expected_revision=1, **guarded())
    assert fired and lease.network_cancelled()
    if boundary != 'barrier_before':
        with pytest.raises(StoreError):
            new_lease(sample)
    monkeypatch.setattr(store, '_write', original)
    if boundary == 'barrier_before':
        receipt = store.local_disconnect(binding, expected_revision=1, **guarded())
    else:
        result = store.reconcile_transition(binding, **guarded())
        receipt = result['receipt']
    assert receipt['kind'] == 'local' and receipt['localRetired']
    assert store.status()['state'] == 'disconnected'
    assert store.history_status()['retiredThrough'] == ticket['sequence']
    assert list((store.directory / module.TICKET_ARCHIVE).glob('window-*.json'))


def test_local_retirement_refuses_foreign_binding_and_stale_revision(sample):
    store, binding, _ = sample
    connect_grant(sample)
    with pytest.raises(StoreError):
        store.local_disconnect(replace(binding, owner_id=str(uuid.uuid4())), expected_revision=1, **guarded())
    with pytest.raises(StoreError):
        store.local_disconnect(binding, expected_revision=0, **guarded())
    assert store.status()['state'] == 'active'


def test_prepared_ticket_source_change_allows_local_cancel_only(sample):
    store, binding, _ = sample
    connect_grant(sample)
    ticket = prepare(sample, source='a' * 64)
    assert store.reconcile_ticket(binding, ticket=ticket, **guarded())['state'] == 'prepared'
    store.cancel_ticket(binding, ticket=ticket, **guarded())
    assert prepare(sample, source='b' * 64)['sequence'] == '2'


@pytest.mark.parametrize('name', ['ticket-anchor.json', 'ticket-barrier.json', 'ticket-pending.json'])
@pytest.mark.parametrize('after', [False, True])
def test_initialization_interruption_requires_exact_anchor_preimage(sample, monkeypatch, name, after):
    store, binding, _ = sample
    original = store._write
    fired = []
    def fail(candidate, data, **kwargs):
        if candidate == name and not fired:
            fired.append(candidate)
            if after:
                original(candidate, data, **kwargs)
            raise OSError('initialization capacity')
        return original(candidate, data, **kwargs)
    monkeypatch.setattr(store, '_write', fail)
    with pytest.raises(StoreError):
        connect_grant(sample)
    monkeypatch.setattr(store, '_write', original)
    assert fired
    if name == 'ticket-anchor.json' and not after:
        assert connect_grant(sample) == 1
    else:
        assert store.reconcile_transition(binding, **guarded())['state'] == 'none'
        assert connect_grant(sample) == 1
    # Once a real publication has happened, a missing slot is never init.
    (store.directory / 'ticket-pending.json').unlink()
    with pytest.raises(StoreError):
        store.reconcile_transition(binding, **guarded())


def test_allocating_ack_loss_can_reconcile_then_cancel_without_guessing_ticket(sample, monkeypatch):
    store, binding, _ = sample
    connect_grant(sample)
    original = store._finish_prepare
    monkeypatch.setattr(store, '_finish_prepare', lambda *args: (_ for _ in ()).throw(OSError('lost prepare')))
    with pytest.raises(StoreError):
        prepare(sample)
    hint = store.ticket_status()
    assert hint['state'] == 'allocating' and hint['ticket']['sequence'] == '1'
    monkeypatch.setattr(store, '_finish_prepare', original)
    result = store.reconcile_transition(binding, **guarded())
    assert result['state'] == 'prepared' and result['ticket'] == hint['ticket']
    assert store.cancel_ticket(binding, ticket=result['ticket'], **guarded())['state'] == 'none'


def test_ticket_sequence_and_closed_schemas_reject_bool_paths_and_overflow(sample):
    store, binding, _ = sample
    connect_grant(sample)
    ticket = prepare(sample)
    for changes in ({'sequence': True}, {'sequence': '01'}, {'sequence': str(2**63)},
                    {'nonce': '../file'}, {'path': 'current.json'}, {'priorStoreRevision': True}):
        with pytest.raises(StoreError):
            store.reconcile_ticket(binding, ticket=dict(ticket, **changes), **guarded())


@pytest.mark.parametrize('boundary', ['pending', 'head', 'archive', 'empty'])
def test_admission_readback_failure_requires_exact_local_reconcile(sample, monkeypatch, boundary):
    store, binding, _ = sample
    connect_grant(sample)
    ticket = prepare(sample)
    original_read, original_write = store._read_capsule, store._write
    armed, failed = [], []
    def write(name, data, **kwargs):
        result = original_write(name, data, **kwargs)
        body = json.loads(data).get('body', {})
        if ((boundary == 'pending' and name == 'ticket-pending.json' and body.get('state') == 'transition')
                or (boundary == 'head' and name == 'current.json')
                or (boundary == 'empty' and name == 'ticket-pending.json' and body.get('state') == 'empty')):
            armed.append(name)
        return result
    def read(name, *args, **kwargs):
        if armed and name == armed[-1] and not failed:
            failed.append(name)
            raise OSError('synthetic readback failure')
        return original_read(name, *args, **kwargs)
    archive = store._archive_read
    def archive_read(name, *args, **kwargs):
        if boundary == 'archive' and name.startswith('transition-') and not failed:
            failed.append(name)
            raise OSError('archive readback failure')
        return archive(name, *args, **kwargs)
    monkeypatch.setattr(store, '_write', write)
    monkeypatch.setattr(store, '_read_capsule', read)
    monkeypatch.setattr(store, '_archive_read', archive_read)
    with pytest.raises(StoreError):
        store.admit_refresh(binding, 1, ticket=ticket, owner_generation=object(), **guarded())
    assert failed
    monkeypatch.setattr(store, '_write', original_write)
    monkeypatch.setattr(store, '_read_capsule', original_read)
    monkeypatch.setattr(store, '_archive_read', archive)
    assert store.reconcile_transition(binding, **guarded())['state'] == 'admitted'
    with pytest.raises(StoreError):
        store.admit_refresh(binding, 1, ticket=ticket, owner_generation=object(), **guarded())


@pytest.mark.parametrize('boundary', ['file', 'directory'])
@pytest.mark.parametrize('after', [False, True])
def test_prepare_fsync_failure_recovers_only_exact_reserved_ticket(sample, monkeypatch, boundary, after):
    import stat
    store, binding, _ = sample
    connect_grant(sample)
    original = module.os.fsync
    failed = []
    def sync(fd):
        is_directory = stat.S_ISDIR(module.os.fstat(fd).st_mode)
        if not failed and (boundary == 'directory') == is_directory:
            failed.append(fd)
            if after:
                original(fd)
            raise OSError('synthetic fsync capacity')
        return original(fd)
    monkeypatch.setattr(module.os, 'fsync', sync)
    with pytest.raises(StoreError):
        prepare(sample)
    assert failed
    monkeypatch.setattr(module.os, 'fsync', original)
    result = store.reconcile_transition(binding, **guarded())
    assert result['state'] in {'none', 'prepared'}
    if result['ticket'] is not None:
        assert result['ticket']['sequence'] == '1'
        assert prepare(sample) == result['ticket']
        store.cancel_ticket(binding, ticket=result['ticket'], **guarded())
    assert store.status()['state'] == 'active'


def test_barrier_survives_pending_admission_recovery_and_only_clears_after_tombstone(sample, monkeypatch):
    store, binding, _ = sample
    connect_grant(sample)
    ticket = prepare(sample)
    original = store._write
    def fail_head(name, data, **kwargs):
        if name == 'current.json':
            raise OSError('head publication capacity')
        return original(name, data, **kwargs)
    monkeypatch.setattr(store, '_write', fail_head)
    with pytest.raises(StoreError):
        store.admit_refresh(binding, 1, ticket=ticket, owner_generation=object(), **guarded())
    with pytest.raises(StoreError):
        store.local_disconnect(binding, expected_revision=1, **guarded())
    barrier = json.loads((store.directory / 'ticket-barrier.json').read_bytes())
    assert barrier['body']['state'] == 'active'
    monkeypatch.setattr(store, '_write', original)
    result = store.reconcile_transition(binding, **guarded())
    assert result['state'] == 'local_retired' and result['receipt']['remoteRevocation'] == 'unconfirmed'
    assert store.status()['state'] == 'disconnected'
    assert json.loads((store.directory / 'ticket-barrier.json').read_bytes())['body']['state'] == 'clear'
    with pytest.raises(StoreError):
        store.admit_refresh(binding, 1, ticket=ticket, owner_generation=object(), **guarded())


def test_missing_pending_local_barrier_waits_for_exact_restoration(sample):
    store, binding, _ = sample
    connect_grant(sample)
    prepare(sample)
    path = store.directory / 'ticket-pending.json'
    exact = path.read_bytes()
    path.unlink()
    with pytest.raises(StoreError):
        store.local_disconnect(binding, expected_revision=1, **guarded())
    assert store.ticket_status()['state'] == 'recovery_required'
    with pytest.raises(StoreError):
        new_lease(sample)
    path.write_bytes(exact)
    path.chmod(0o600)
    assert store.reconcile_transition(binding, **guarded())['state'] == 'local_retired'


def test_sequence_exhaustion_never_wraps_or_resets_on_consent(sample):
    store, binding, _ = sample
    connect_grant(sample)
    with store._locked():
        head = store._authority(binding)
        maximum = str(module.MAX_SEQUENCE)
        new = store._next_head(head, issuedThrough=maximum, retiredThrough=maximum)
        store._write('current.json', module._json(new), replace=True)
        store._write('ticket-pending.json', module._json(store._empty_pending(new)), replace=True)
    with pytest.raises(StoreError, match='ticket_sequence_exhausted'):
        prepare(sample)
    assert store.local_disconnect(binding, expected_revision=1, **guarded())['localRetired']
    connect_grant(sample, expected_revision=2)
    assert store.history_status()['issuedThrough'] == maximum
    with pytest.raises(StoreError, match='ticket_sequence_exhausted'):
        prepare(sample)


def test_absent_local_disconnect_is_passive_and_no_ticket(sample):
    store, _, vault = sample
    assert store.local_disconnect(None, expected_revision=0, **guarded()) == {
        'kind': 'local', 'revision': 0, 'localRetired': True, 'remoteRevocation': 'unconfirmed'}
    assert not store.directory.exists() and not vault.calls


def test_anchor_loss_refuses_local_authority_but_closes_memory_leases(sample):
    store, binding, _ = sample
    connect_grant(sample)
    lease = new_lease(sample)
    (store.directory / 'ticket-anchor.json').unlink()
    with pytest.raises(StoreError, match='storage_recovery_required'):
        store.local_disconnect(binding, expected_revision=1, **guarded())
    assert lease.network_cancelled()
    assert store.ticket_status()['state'] == 'recovery_required'
    assert state(store)['phase'] == 'active'  # No false durable-disconnected claim.


def test_corrupt_intent_still_permits_exact_local_retirement_without_remote_capability(sample):
    store, binding, _ = sample
    connect_grant(sample)
    ticket = prepare(sample)
    path = store.directory / module.TICKET_ARCHIVE / ('intent-' + store._ticket_fingerprint(ticket) + '.json')
    path.write_bytes(b'{}')
    assert store.local_disconnect(binding, expected_revision=1, **guarded())['kind'] == 'local'
    assert path.read_bytes() == b'{}'  # Broken evidence is preserved, never replaced.
    with pytest.raises(StoreError):
        store.admit_refresh(binding, 1, ticket=ticket, owner_generation=object(), **guarded())


def test_retained_archives_cannot_recover_a_missing_later_head(sample):
    store, binding, _ = sample
    connect_grant(sample)
    lease = refresh_lease(sample)
    lease.run(lambda token, cancel: refresh_commit(sample, lease))
    assert list((store.directory / module.TICKET_ARCHIVE).glob('transition-*.json'))
    (store.directory / 'current.json').unlink()
    with pytest.raises(StoreError):
        store.reconcile_transition(binding, **guarded())
    with pytest.raises(StoreError):
        connect_grant(sample, expected_revision=0)


@pytest.mark.parametrize('kind', ['consent', 'refresh'])
@pytest.mark.parametrize('fault', ['before_put', 'after_put', 'cipher_before', 'cipher_after', 'before_final_plan'])
def test_reserved_candidate_is_bounded_and_abandoned_without_repeating_put(sample, monkeypatch, kind, fault):
    store, binding, vault = sample
    lease = None
    if kind == 'refresh':
        connect_grant(sample)
        lease = refresh_lease(sample)
    original_put, original_archive, original_transition = vault.put, store._archive_put, store._transition
    def put(native, value):
        head = state(store)
        assert head['candidate']['state'] == 'reserved'
        assert head['candidate']['record']['native_binding'] == asdict(native)
        assert json.loads((store.directory / 'ticket-pending.json').read_bytes())['body']['state'] == 'empty'
        if fault == 'before_put':
            raise OSError('before native put')
        result = original_put(native, value)
        if fault == 'after_put':
            raise OSError('ambiguous native put')
        return result
    def archive(name, data):
        if name.endswith('.enc') and fault == 'cipher_before':
            raise OSError('before ciphertext')
        result = original_archive(name, data)
        if name.endswith('.enc') and fault == 'cipher_after':
            raise OSError('after ciphertext')
        return result
    def transition(head, new, action, **kwargs):
        if action in {'supersede', 'complete'} and fault == 'before_final_plan':
            raise OSError('no final commit journal')
        return original_transition(head, new, action, **kwargs)
    monkeypatch.setattr(vault, 'put', put)
    monkeypatch.setattr(store, '_archive_put', archive)
    monkeypatch.setattr(store, '_transition', transition)
    with pytest.raises(StoreError):
        connect_grant(sample) if kind == 'consent' else refresh_commit(sample, lease)
    candidate = state(store)['candidate']
    assert candidate['state'] == 'reserved'
    assert store.status()['cleanup_pending'] is True
    assert store.history_status()['state'] == 'recovery_required'
    count = len([call for call in vault.calls if call[0] == 'put'])
    monkeypatch.setattr(vault, 'put', original_put)
    monkeypatch.setattr(store, '_archive_put', original_archive)
    monkeypatch.setattr(store, '_transition', original_transition)
    result = store.reconcile_transition(binding, **guarded())
    assert result['state'] == ('none' if kind == 'consent' else 'admitted')
    assert len([call for call in vault.calls if call[0] == 'put']) == count
    assert candidate['record']['reference'] not in vault.values
    assert not (store.directory / module.TICKET_ARCHIVE / (candidate['record']['target'] + '.enc')).exists()
    assert (store.directory / module.TICKET_ARCHIVE / (candidate['record']['target'] + '.journal')).exists()
    assert store.status()['cleanup_pending'] is False
    if lease is not None:
        lease.close()
    with pytest.raises(StoreError):
        store.reconcile_auth_attempt(binding, auth_attempt_id='synthetic-auth-attempt',
                                     prior_store_revision=0, granted_scopes=GRANT_SCOPES)


def test_completed_refresh_deletes_superseded_cipher_and_native_key_but_retains_proof(sample):
    store, binding, vault = sample
    connect_grant(sample)
    old = state(store)['target']
    old_reference = next(iter(vault.values))
    lease = refresh_lease(sample)
    ticket = lease._ticket
    assert lease.run(lambda token, cancel: refresh_commit(sample, lease)) == 2
    assert old_reference not in vault.values and len(vault.values) == 1
    archive = store.directory / module.TICKET_ARCHIVE
    assert not (archive / (old + '.enc')).exists()
    assert (archive / (old + '.journal')).exists()
    assert (archive / ('admission-' + store._ticket_fingerprint(ticket) + '.json')).exists()
    assert store.reconcile_ticket(binding, ticket=ticket, **guarded())['state'] == 'completed'


def test_revoke_replay_needs_no_deleted_credential_and_never_recreates_lease(sample):
    store, binding, vault = sample
    connect_grant(sample)
    ticket = prepare(sample, 'revoke')
    receipt, lease = disconnect_v2(sample, ticket=ticket)
    assert not vault.values
    assert not list((store.directory / module.TICKET_ARCHIVE).glob('*.enc'))
    assert lease.run(lambda token, cancel: True)['remoteRevocation'] == 'confirmed'
    calls = list(vault.calls)
    other, _, _ = restart(sample)
    assert other.reconcile_ticket(binding, ticket=ticket, **guarded())['receipt']['remoteRevocation'] == 'confirmed'
    assert other.disconnect_grant(binding, expected_revision=1, ticket=ticket, **guarded())[1] is None
    assert vault.calls == calls
    assert receipt['localRetired']


@pytest.mark.parametrize('kind', ['refresh', 'revoke', 'local'])
def test_native_cleanup_failure_keeps_receipt_obligation_and_blocks_begin(sample, kind):
    store, binding, vault = sample
    connect_grant(sample)
    vault.fail_delete = True
    if kind == 'refresh':
        lease = refresh_lease(sample)
        ticket = lease._ticket
        assert refresh_commit(sample, lease) == 2
        lease.close()
    elif kind == 'revoke':
        ticket = prepare(sample, 'revoke')
        receipt, lease = disconnect_v2(sample, ticket=ticket)
        assert receipt['localRetired'] and lease is None
    else:
        receipt = store.local_disconnect(binding, expected_revision=1, **guarded())
        assert receipt['localRetired']
    assert store.status()['cleanup_pending'] is True
    before = list(vault.calls)
    assert store.history_status()['state'] == 'recovery_required'
    assert vault.calls == before
    with pytest.raises(StoreError):
        prepare(sample)
    result = store.reconcile_transition(binding, **guarded())
    assert result['receipt'] is not None and result['receipt']['kind'] == kind
    assert store.status()['cleanup_pending'] is True
    vault.fail_delete = False
    assert store.reconcile_transition(binding, **guarded())['receipt'] == result['receipt']
    assert store.status()['cleanup_pending'] is False
    assert len(vault.values) == (1 if kind == 'refresh' else 0)


@pytest.mark.parametrize('field,value', [('registration_id', 'other-registration'), ('issued_client_id', 'other-client'),
                                       ('account_id', 'other-account')])
def test_full_binding_change_refused_before_any_journal_or_native_write(sample, field, value):
    store, binding, vault = sample
    connect_grant(sample)
    store.local_disconnect(binding, expected_revision=1, **guarded())
    before = {str(path.relative_to(store.directory)): path.read_bytes()
              for path in store.directory.rglob('*') if path.is_file()}
    calls = list(vault.calls)
    changed = replace(binding, **{field: value})
    evidence = dict(grant_fields()['identity_evidence'], subject=changed.account_id, audience=changed.issued_client_id)
    with pytest.raises(StoreError, match='scope_denied'):
        store.connect_grant(changed, **grant_fields(expected_revision=2, identity_evidence=evidence))
    assert calls == vault.calls
    assert before == {str(path.relative_to(store.directory)): path.read_bytes()
                      for path in store.directory.rglob('*') if path.is_file()}


@pytest.mark.parametrize('role', ['intent', 'grant', 'cipher', 'transition'])
def test_exact_two_link_archive_stage_recovers_without_scan(sample, monkeypatch, role):
    store, binding, vault = sample
    if role in {'intent', 'transition'}:
        connect_grant(sample)
    original = module.os.link
    failed = []
    def link(source, destination, **kwargs):
        result = original(source, destination, **kwargs)
        name = Path(destination).name
        hit = ((role == 'intent' and name.startswith('intent-'))
               or (role == 'grant' and name.endswith('.journal'))
               or (role == 'cipher' and name.endswith('.enc'))
               or (role == 'transition' and name.startswith('transition-')))
        if hit and not failed:
            failed.append((Path(source), Path(destination)))
            raise OSError('crash after link before unlink')
        return result
    monkeypatch.setattr(module.os, 'link', link)
    with pytest.raises(StoreError):
        prepare(sample) if role in {'intent', 'transition'} else connect_grant(sample)
    assert len(failed) == 1
    stage, final = failed[0]
    assert stage.stat().st_ino == final.stat().st_ino
    assert final.stat().st_nlink == 2
    monkeypatch.setattr(module.os, 'link', original)
    puts = len([call for call in vault.calls if call[0] == 'put'])
    monkeypatch.setattr(Path, 'iterdir', lambda path: pytest.fail('recovery scanned archive'))
    result = store.reconcile_transition(binding, **guarded())
    assert result['state'] == ('prepared' if role in {'intent', 'transition'} else 'none')
    assert not stage.exists()
    if role == 'cipher':
        assert not final.exists()
    else:
        assert final.stat().st_nlink == 1
    assert len([call for call in vault.calls if call[0] == 'put']) == puts


@pytest.mark.parametrize('role', ['intent', 'grant', 'transition'])
def test_torn_prelink_metadata_stage_rebuilds_only_exact_pending_bytes(sample, monkeypatch, role):
    store, binding, _ = sample
    if role != 'grant':
        connect_grant(sample)
    original = module.os.fsync
    failed = []
    def sync(fd):
        info = module.os.fstat(fd)
        # Synthetic fault points are correlated to the exact stage inode, not
        # a filesystem scan in the production recovery algorithm.
        archive = store.directory / module.TICKET_ARCHIVE
        for path in archive.glob('*.staging') if archive.exists() else ():
            if path.stat().st_ino != info.st_ino:
                continue
            raw = path.read_bytes()
            try:
                value = json.loads(raw)
            except ValueError:
                continue
            hit = ((role == 'intent' and value.get('role') == 'intent')
                   or (role == 'grant' and 'native_binding' in value)
                   or (role == 'transition' and value.get('body', {}).get('kind') == 'allocate'))
            if hit and not failed:
                failed.append(path)
                # Model a process loss with a short regular staging file.
                module.os.ftruncate(fd, max(1, len(raw) // 2))
                raise OSError('partial stage fsync failure')
        return original(fd)
    monkeypatch.setattr(module.os, 'fsync', sync)
    with pytest.raises(StoreError):
        connect_grant(sample) if role == 'grant' else prepare(sample)
    assert failed and failed[0].exists() and failed[0].stat().st_nlink == 1
    monkeypatch.setattr(module.os, 'fsync', original)
    result = store.reconcile_transition(binding, **guarded())
    assert result['state'] == ('none' if role == 'grant' else 'prepared')
    assert not failed[0].exists()


def test_torn_cipher_stage_is_abandoned_not_recreated(sample, monkeypatch):
    store, binding, vault = sample
    original = store._archive_put
    failed = []
    def archive(name, data):
        if name.endswith('.enc'):
            path = store.directory / module.TICKET_ARCHIVE / store._stage_name(name, module.hashlib.sha256(data).hexdigest())
            path.write_bytes(data[:8])
            path.chmod(0o600)
            failed.append(path)
            raise OSError('torn ciphertext before link')
        return original(name, data)
    monkeypatch.setattr(store, '_archive_put', archive)
    with pytest.raises(StoreError):
        connect_grant(sample)
    puts = len([call for call in vault.calls if call[0] == 'put'])
    monkeypatch.setattr(store, '_archive_put', original)
    assert store.reconcile_transition(binding, **guarded())['state'] == 'none'
    assert not vault.values and not failed[0].exists()
    assert len([call for call in vault.calls if call[0] == 'put']) == puts


@pytest.mark.parametrize('attack', ['archive_symlink', 'file_symlink', 'arbitrary_hardlink', 'stage_foreign_inode'])
def test_nested_archive_refuses_unrelated_filesystem_aliases(sample, tmp_path, attack):
    store, binding, _ = sample
    connect_grant(sample)
    ticket = prepare(sample)
    archive = store.directory / module.TICKET_ARCHIVE
    path = archive / ('intent-' + store._ticket_fingerprint(ticket) + '.json')
    raw = path.read_bytes()
    if attack == 'archive_symlink':
        moved = tmp_path / 'moved'
        archive.rename(moved)
        archive.symlink_to(moved, target_is_directory=True)
    elif attack == 'file_symlink':
        foreign = tmp_path / 'foreign'
        foreign.write_bytes(raw)
        foreign.chmod(0o600)
        path.unlink()
        path.symlink_to(foreign)
    elif attack == 'arbitrary_hardlink':
        module.os.link(path, tmp_path / 'unrelated-link')
    else:
        digest = module.hashlib.sha256(raw).hexdigest()
        stage = archive / store._stage_name(path.name, digest)
        stage.write_bytes(raw)
        stage.chmod(0o600)
        module.os.link(path, tmp_path / 'unrelated-link')
        with store._locked():
            with pytest.raises(StoreError):
                store._archive_put(path.name, raw)
    with pytest.raises(StoreError):
        store.reconcile_ticket(binding, ticket=ticket, **guarded())
    with pytest.raises(StoreError):
        new_lease(sample)


@pytest.mark.parametrize('same_intent', [False, True])
def test_concurrent_prepare_returns_one_identical_ticket_or_conflict(sample, same_intent):
    store, binding, _ = sample
    connect_grant(sample)
    other, _, _ = restart(sample)
    gate = threading.Barrier(2)
    tickets, errors = [], []
    def reserve(target, source):
        gate.wait()
        try:
            tickets.append(target.prepare_ticket(binding, 1, kind='refresh', source_revision=source, **guarded()))
        except StoreError as error:
            errors.append(error.reason)
    threads = [threading.Thread(target=reserve, args=(store, 'a' * 64)),
               threading.Thread(target=reserve, args=(other, ('a' if same_intent else 'b') * 64))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert all(not thread.is_alive() for thread in threads)
    if same_intent:
        assert len(tickets) == 2 and tickets[0] == tickets[1] and errors == []
    else:
        assert len(tickets) == 1 and errors == ['ticket_conflict']
    assert store.history_status()['issuedThrough'] == '1'


@pytest.mark.parametrize('boundary', ['prepare_head', 'prepare_empty', 'prepare_archive'])
@pytest.mark.parametrize('point', ['after_write', 'readback'])
def test_prepare_targeted_postpublication_faults_reconcile_same_ticket(sample, monkeypatch, boundary, point):
    store, binding, _ = sample
    connect_grant(sample)
    write, read, archive = store._write, store._read_capsule, store._archive_put
    failed, armed = [], []
    def writing(name, data, **kwargs):
        body = json.loads(data).get('body', {})
        hit = ((boundary == 'prepare_head' and name == 'current.json'
                and body.get('ticketWindow', {}).get('stage') == 'prepared')
               or (boundary == 'prepare_empty' and name == 'ticket-pending.json'
                   and body.get('state') == 'empty' and state(store)['ticketWindow']['stage'] == 'prepared'))
        result = write(name, data, **kwargs)
        if hit and not failed:
            if point == 'after_write':
                failed.append(name)
                raise OSError('after rename and fsync')
            armed.append(name)
        return result
    def reading(name, *args, **kwargs):
        if armed and name == armed[-1] and not failed:
            failed.append(name)
            raise OSError('readback')
        return read(name, *args, **kwargs)
    def archiving(name, data):
        result = archive(name, data)
        if boundary == 'prepare_archive' and name.startswith('intent-') and not failed:
            failed.append(name)
            raise OSError('after exact materialization and readback')
        return result
    monkeypatch.setattr(store, '_write', writing)
    monkeypatch.setattr(store, '_read_capsule', reading)
    monkeypatch.setattr(store, '_archive_put', archiving)
    with pytest.raises(StoreError):
        prepare(sample)
    assert failed
    monkeypatch.setattr(store, '_write', write)
    monkeypatch.setattr(store, '_read_capsule', read)
    monkeypatch.setattr(store, '_archive_put', archive)
    result = store.reconcile_transition(binding, **guarded())
    assert result['state'] == 'prepared' and prepare(sample) == result['ticket']


@pytest.mark.parametrize('boundary', ['head', 'empty', 'clear'])
@pytest.mark.parametrize('point', ['after_write', 'readback'])
def test_barrier_postpublication_faults_never_return_an_active_grant(sample, monkeypatch, boundary, point):
    store, binding, _ = sample
    connect_grant(sample)
    prepare(sample)
    write, read = store._write, store._read_capsule
    failed, armed = [], []
    def writing(name, data, **kwargs):
        body = json.loads(data).get('body', {})
        hit = ((boundary == 'head' and name == 'current.json' and body.get('phase') == 'disconnected')
               or (boundary == 'empty' and name == 'ticket-pending.json' and body.get('state') == 'empty')
               or (boundary == 'clear' and name == 'ticket-barrier.json' and body.get('state') == 'clear'))
        result = write(name, data, **kwargs)
        if hit and not failed:
            if point == 'after_write':
                failed.append(name)
                raise OSError('postpublication fault')
            armed.append(name)
        return result
    def reading(name, *args, **kwargs):
        if armed and name == armed[-1] and not failed:
            failed.append(name)
            raise OSError('readback fault')
        return read(name, *args, **kwargs)
    monkeypatch.setattr(store, '_write', writing)
    monkeypatch.setattr(store, '_read_capsule', reading)
    with pytest.raises(StoreError):
        store.local_disconnect(binding, expected_revision=1, **guarded())
    assert failed
    monkeypatch.setattr(store, '_write', write)
    monkeypatch.setattr(store, '_read_capsule', read)
    with pytest.raises(StoreError):
        new_lease(sample)
    result = store.reconcile_transition(binding, **guarded())
    assert result['state'] == 'local_retired'
    assert result['receipt']['localRetired'] is True
    assert store.status()['state'] == 'disconnected'


@pytest.mark.parametrize('local_barrier', [False, True])
def test_initial_head_with_later_candidate_pending_dispatches_without_reinitialization(sample, monkeypatch, local_barrier):
    store, binding, vault = sample
    original = store._write
    def fail(name, data, **kwargs):
        body = json.loads(data).get('body', {})
        if name == 'current.json' and body.get('candidate', {}).get('state') == 'reserved':
            raise OSError('candidate head rename unavailable')
        return original(name, data, **kwargs)
    monkeypatch.setattr(store, '_write', fail)
    with pytest.raises(StoreError):
        connect_grant(sample)
    assert not vault.values
    assert json.loads((store.directory / 'ticket-pending.json').read_bytes())['body']['kind'] == 'candidate'
    if local_barrier:
        with pytest.raises(StoreError):
            store.local_disconnect(binding, expected_revision=0, **guarded())
        assert json.loads((store.directory / 'ticket-barrier.json').read_bytes())['body']['state'] == 'active'
    monkeypatch.setattr(store, '_write', original)
    result = store.reconcile_transition(binding, **guarded())
    assert result['state'] == ('local_retired' if local_barrier else 'none')
    assert not [call for call in vault.calls if call[0] == 'put']
    assert state(store)['candidate'] == {'state': 'idle'}


@pytest.mark.parametrize('slot', ['ticket-barrier.json', 'ticket-pending.json'])
def test_authenticated_v1_initialization_slot_loss_recovers_only_initial_boundary(sample, monkeypatch, slot):
    store, binding, _ = sample
    store._connect_payload(binding, v1_secret(binding), 0)
    store.local_disconnect(binding, expected_revision=1, **guarded())
    original = store._write
    def fail(name, data, **kwargs):
        if name == slot:
            raise OSError('initial slot unavailable')
        return original(name, data, **kwargs)
    monkeypatch.setattr(store, '_write', fail)
    with pytest.raises(StoreError):
        connect_grant(sample, expected_revision=2)
    monkeypatch.setattr(store, '_write', original)
    assert store.reconcile_transition(binding, **guarded())['state'] == 'local_retired'
    assert connect_grant(sample, expected_revision=2) == 3
    (store.directory / slot).unlink()
    with pytest.raises(StoreError):
        store.reconcile_transition(binding, **guarded())


@pytest.mark.parametrize('boundary', ['cipher_unlink', 'cipher_dirsync', 'cleanup_head', 'cleanup_empty'])
def test_cleanup_fault_keeps_exact_obligation_and_committed_receipt(sample, monkeypatch, boundary):
    store, binding, vault = sample
    connect_grant(sample)
    old = state(store)['target']
    lease = refresh_lease(sample)
    unlink, sync, write = Path.unlink, store._archive_sync, store._write
    failed, deleted_cipher = [], []
    def removing(path, *args, **kwargs):
        if path.name == old + '.enc':
            if boundary == 'cipher_unlink' and not failed:
                failed.append('unlink')
                raise OSError('cipher unlink denied')
            result = unlink(path, *args, **kwargs)
            deleted_cipher.append(True)
            return result
        return unlink(path, *args, **kwargs)
    def syncing():
        if boundary == 'cipher_dirsync' and deleted_cipher and not failed:
            failed.append('dirsync')
            raise OSError('cipher directory fsync')
        return sync()
    def writing(name, data, **kwargs):
        body = json.loads(data).get('body', {})
        is_cleanup = ((name == 'current.json' and body.get('phase') == 'active' and body.get('cleanup') == []
                       and body.get('revision') == 2)
                      if boundary == 'cleanup_head' else
                      (name == 'ticket-pending.json' and body.get('state') == 'empty'
                       and state(store)['revision'] == 2 and state(store)['cleanup'] == []))
        result = write(name, data, **kwargs)
        if boundary in {'cleanup_head', 'cleanup_empty'} and is_cleanup and not failed:
            failed.append(name)
            raise OSError('cleanup postpublication failure')
        return result
    monkeypatch.setattr(Path, 'unlink', removing)
    monkeypatch.setattr(store, '_archive_sync', syncing)
    monkeypatch.setattr(store, '_write', writing)
    assert refresh_commit(sample, lease) == 2
    lease.close()
    assert failed
    assert store.status()['cleanup_pending'] is (boundary != 'cleanup_empty')
    assert len(vault.values) == 1  # Exact old native deletion happened first.
    assert store.history_status()['state'] == ('available' if boundary == 'cleanup_empty' else 'recovery_required')
    monkeypatch.setattr(Path, 'unlink', unlink)
    monkeypatch.setattr(store, '_archive_sync', sync)
    monkeypatch.setattr(store, '_write', write)
    result = store.reconcile_transition(binding, **guarded())
    assert result['state'] == 'completed' and result['receipt']['revision'] == 2
    assert store.status()['cleanup_pending'] is False
    assert not (store.directory / module.TICKET_ARCHIVE / (old + '.enc')).exists()


def test_abandoned_candidate_and_current_grant_fit_two_local_cleanup_obligations(sample, monkeypatch):
    store, binding, vault = sample
    connect_grant(sample)
    lease = refresh_lease(sample)
    original = store._archive_put
    def fail(name, data):
        if name.endswith('.enc'):
            raise OSError('candidate cipher unavailable')
        return original(name, data)
    monkeypatch.setattr(store, '_archive_put', fail)
    with pytest.raises(StoreError):
        refresh_commit(sample, lease)
    lease.close()
    assert len(vault.values) == 2
    monkeypatch.setattr(store, '_archive_put', original)
    vault.fail_delete = True
    receipt = store.local_disconnect(binding, expected_revision=1, **guarded())
    assert receipt['localRetired'] and len(state(store)['cleanup']) == 2
    assert state(store)['candidate'] == {'state': 'idle'}
    assert store.status()['cleanup_pending'] is True
    vault.fail_delete = False
    assert store.reconcile_transition(binding, **guarded())['receipt'] == receipt
    assert not vault.values and state(store)['cleanup'] == []


def test_candidate_pending_never_exposes_previous_refresh_token_again(sample, monkeypatch):
    store, binding, _ = sample
    connect_grant(sample)
    lease = refresh_lease(sample)
    original = store._archive_put
    def fail(name, data):
        if name.endswith('.enc'):
            raise OSError('cipher unavailable')
        return original(name, data)
    monkeypatch.setattr(store, '_archive_put', fail)
    with pytest.raises(StoreError):
        refresh_commit(sample, lease)
    assert lease.try_send_allowed() is False
    with pytest.raises(StoreError):
        store.admit_refresh(binding, 1, ticket=lease._ticket, owner_generation=object(), **guarded())
    lease.close()


@pytest.mark.parametrize('action,boundary', [('prepare', 'head'), ('prepare', 'archive'),
                                           ('retire', 'head'), ('retire', 'empty'), ('retire', 'clear')])
@pytest.mark.parametrize('after_sync', [False, True])
def test_targeted_directory_fsync_failures_preserve_exact_local_recovery(sample, monkeypatch, action, boundary, after_sync):
    store, binding, _ = sample
    connect_grant(sample)
    if action == 'retire':
        prepare(sample)
    write, archive = store._write, store._archive_put
    root_sync, archive_sync = store._sync_directory, store._archive_sync
    selected, failed = [False], []
    def writing(name, data, **kwargs):
        body = json.loads(data).get('body', {})
        selected[0] = ((boundary == 'head' and name == 'current.json'
                        and (body.get('ticketWindow', {}).get('stage') == 'prepared' if action == 'prepare'
                             else body.get('phase') == 'disconnected'))
                       or (action == 'retire' and boundary == 'empty' and name == 'ticket-pending.json'
                           and body.get('state') == 'empty')
                       or (action == 'retire' and boundary == 'clear' and name == 'ticket-barrier.json'
                           and body.get('state') == 'clear'))
        try:
            return write(name, data, **kwargs)
        finally:
            selected[0] = False
    def archiving(name, data):
        selected[0] = action == 'prepare' and boundary == 'archive' and name.startswith('intent-')
        try:
            return archive(name, data)
        finally:
            selected[0] = False
    def syncing(original):
        if selected[0] and not failed:
            failed.append(boundary)
            if after_sync:
                original()
            raise OSError('targeted directory fsync failure')
        return original()
    monkeypatch.setattr(store, '_write', writing)
    monkeypatch.setattr(store, '_archive_put', archiving)
    monkeypatch.setattr(store, '_sync_directory', lambda: syncing(root_sync))
    monkeypatch.setattr(store, '_archive_sync', lambda: syncing(archive_sync))
    with pytest.raises(StoreError):
        prepare(sample) if action == 'prepare' else store.local_disconnect(binding, expected_revision=1, **guarded())
    assert failed
    monkeypatch.setattr(store, '_write', write)
    monkeypatch.setattr(store, '_archive_put', archive)
    monkeypatch.setattr(store, '_sync_directory', root_sync)
    monkeypatch.setattr(store, '_archive_sync', archive_sync)
    result = store.reconcile_transition(binding, **guarded())
    assert result['state'] == ('prepared' if action == 'prepare' else 'local_retired')
