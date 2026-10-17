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
    return json.loads((store.directory / 'current.json').read_bytes())


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
        return name == 'current.json' and json.loads(data)['phase'] == 'active'
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
    assert vault.calls == old_calls
    assert len(vault.values) == 1
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
