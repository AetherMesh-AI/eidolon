"""Descriptor and ACL proof uses synthetic APIs unless explicitly macOS-marked."""
import os
from pathlib import Path
import sys

import pytest

from eidolon_cli import organization_setup_subscription_filesystem as module
from eidolon_cli.organization_setup_subscription_store import StoreError


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
              'stat_mismatch': False, 'entry_error': False,
              'checked_descriptor': module._checked_acl_descriptor}
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
    def copy_ext(buffer, acl, size):
        data = _encoded_acl([(bytes(16), tag, 0) for tag in config['tags']])
        ctypes.memmove(buffer, data, len(data))
        return len(data)
    api = SimpleNamespace(
        filesec_init=lambda: config['init'],
        filesec_free=lambda fsec: calls.append(('free_fsec', fsec)),
        filesec_query_property=query, filesec_get_property=get,
        acl_valid=lambda acl: config['valid_result'], acl_get_entry=entry,
        acl_size=lambda acl: 44 + 24 * len(config['tags']),
        acl_copy_ext=copy_ext,
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
            module.check_darwin_acl(Path('/synthetic-only'))
    else:
        module.check_darwin_acl(Path('/synthetic-only'))
    assert ('free_acl', 123) in calls
    assert ('free_fsec', 77) in calls
    assert calls[-1] == ('close', 7)


def test_darwin_acl_explicit_absence_requires_successful_inspection(darwin_acl_api):
    config, calls, _ = darwin_acl_api
    config['present'] = 0
    module.check_darwin_acl(Path('/synthetic-only'))
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
        module.check_darwin_acl(Path('/synthetic-only'))
    assert calls[-1] == ('close', 7)
    assert (('free_fsec', 77) in calls) == bool(config['init'])
    if config['stat_result'] or config['stat_mismatch']:
        assert ('query', 5) not in calls


@pytest.mark.parametrize('architecture,symbol', [('arm64', 'fstatx_np'), ('x86_64', 'fstatx_np$INODE64')])
def test_darwin_acl_exact_supported_symbol_without_fallback(darwin_acl_api, monkeypatch, architecture, symbol):
    config, calls, api = darwin_acl_api
    config['present'] = 0
    monkeypatch.setattr(module.platform, 'machine', lambda: architecture)
    module.check_darwin_acl(Path('/synthetic-only'))
    calls.clear()
    delattr(api, symbol)
    with pytest.raises(StoreError, match='filesystem_protection_unavailable'):
        module.check_darwin_acl(Path('/synthetic-only'))
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
        module.check_darwin_acl(Path('/synthetic-only'))
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


@pytest.mark.macos_only
def test_native_darwin_empty_acl_is_accepted_without_permission_changes(tmp_path):
    """Read-only OS proof on a new synthetic path; never inspect user grants."""
    from eidolon_cli.organization_setup_subscription_filesystem import check_darwin_acl
    if sys.platform != 'darwin':
        pytest.skip('Read-only native Darwin ACL proof requires macOS')
    before = tmp_path.stat()
    check_darwin_acl(tmp_path)
    after = tmp_path.stat()
    assert (after.st_mode, after.st_uid, after.st_gid) == (before.st_mode, before.st_uid, before.st_gid)




@pytest.fixture
def pinned_acl_api(darwin_acl_api, monkeypatch):
    """Real descriptor/path observations, with only the Darwin C API synthetic."""
    if os.name != 'posix':
        pytest.skip('Descriptor proof requires POSIX nofollow support')
    config, calls, api = darwin_acl_api
    monkeypatch.setattr(module, '_checked_acl_descriptor', config['checked_descriptor'])
    real_fstat = os.fstat

    def native_stat(fd, pointer, fsec):
        calls.append(('stat', fd, fsec))
        if config.get('before_native'):
            config['before_native'](fd)
        info = real_fstat(fd)
        result = pointer._obj
        for key in ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_nlink'):
            setattr(result, key, getattr(info, key))
        result.st_ctimespec.tv_sec, result.st_ctimespec.tv_nsec = divmod(info.st_ctime_ns, 1_000_000_000)
        if config.get('native_field'):
            field = config['native_field']
            setattr(result, field, getattr(result, field) + 1)
        return config['stat_result']

    def copy_ext(buffer, acl, size):
        import ctypes
        data = config.get('snapshot')
        if data is None:
            data = _encoded_acl([(bytes(16), tag, 0) for tag in config['tags']])
        calls.append(('copy', acl.value, size))
        ctypes.memmove(buffer, data, min(len(data), size))
        return config.get('copy_result', len(data))

    original_read = module._read_darwin_acl
    config['reads'] = []

    def read(api, fstatx, fd, before, **kwargs):
        result = original_read(api, fstatx, fd, before, **kwargs)
        config['reads'].append((fd, result))
        if config.get('after_read'):
            config['after_read'](fd, len(config['reads']))
        return result

    api.fstatx_np = native_stat
    setattr(api, 'fstatx_np$INODE64', native_stat)
    api.acl_copy_ext = copy_ext
    monkeypatch.setattr(module, '_read_darwin_acl', read)
    return config, calls, api


def _encoded_acl(entries, *, flags=0):
    return (b'\x01\x2c\xc1\x6d' + bytes(32) + len(entries).to_bytes(4, 'big') + flags.to_bytes(4, 'big')
            + b''.join(qualifier + tag_flags.to_bytes(4, 'big') + rights.to_bytes(4, 'big')
                       for qualifier, tag_flags, rights in entries))


def _before_first_final_path(monkeypatch, path, action):
    """Run one real mutation after the pinned FD read, before its path read."""
    original_lstat = Path.lstat
    calls = []

    def lstat(target, *args, **kwargs):
        caller = sys._getframe(1)
        if (target == path and caller.f_code is module._check_ancestor_acl.__code__
                and 'final_fd' in caller.f_locals and 'final_path' not in caller.f_locals
                and not calls):
            calls.append(True)
            action()
        return original_lstat(target, *args, **kwargs)

    monkeypatch.setattr(Path, 'lstat', lstat)
    return calls


@pytest.mark.parametrize('present,tags', [(0, []), (32, []), (32, [2]), (32, [2] * 128)])
@pytest.mark.parametrize('when', ['initial_fd', 'native_acl', 'final_fd', 'final_path'])
def test_ancestor_sibling_churn_renews_complete_acl_once_on_same_fd(
        tmp_path, monkeypatch, pinned_acl_api, present, tags, when):
    config, calls, _ = pinned_acl_api
    config.update(present=present, tags=tags)
    path = tmp_path / 'ancestor'
    path.mkdir(mode=0o700)
    before = path.stat()
    sibling = path / 'sibling'
    opened = []
    real_open = os.open

    def open_with_churn(target, flags, *args, **kwargs):
        fd = real_open(target, flags, *args, **kwargs)
        opened.append(fd)
        if when == 'initial_fd':
            sibling.mkdir()
        return fd

    monkeypatch.setattr(module.os, 'open', open_with_churn)
    if when == 'native_acl':
        config['before_native'] = lambda fd: sibling.mkdir() if not sibling.exists() else None
    if when == 'final_fd':
        config['after_read'] = lambda fd, count: sibling.mkdir() if count == 1 else None
    if when == 'final_path':
        _before_first_final_path(monkeypatch, path, sibling.mkdir)
    module.check_darwin_acl(path, ancestor=True)
    after = path.stat()
    assert module._directory_identity(after) == module._directory_identity(before)
    assert after.st_nlink != before.st_nlink or after.st_ctime_ns != before.st_ctime_ns
    assert len(opened) == 1
    observations = 1 if when == 'initial_fd' else 2
    assert [fd for fd, _ in config['reads']] == opened * observations
    assert len([call for call in calls if call[0] == 'stat']) == observations
    assert len([call for call in calls if call[0] == 'query']) == observations
    assert len([call for call in calls if call[0] == 'free_fsec']) == observations
    assert len([call for call in calls if call[0] == 'free_acl']) == (observations if present else 0)
    assert config['reads'][0][1][1] == config['reads'][-1][1][1]
    with pytest.raises(OSError):
        os.fstat(opened[0])


def test_stable_ancestor_needs_only_one_complete_acl_read(tmp_path, pinned_acl_api):
    config, calls, _ = pinned_acl_api
    module.check_darwin_acl(tmp_path, ancestor=True)
    assert len(config['reads']) == 1
    assert len([call for call in calls if call[0] == 'query']) == 1


@pytest.mark.parametrize('first', ['final_fd', 'final_path'])
@pytest.mark.parametrize('when', ['native_acl', 'final_fd', 'final_path'])
def test_second_ancestor_churn_fails_closed_without_third_observation(
        tmp_path, monkeypatch, pinned_acl_api, when, first):
    config, calls, _ = pinned_acl_api
    path = tmp_path / 'ancestor'
    path.mkdir()
    config['after_read'] = lambda fd, count: (path / f'sibling-{count}').mkdir() if (
        count == 1 and first == 'final_fd' or count == 2 and when == 'final_fd') else None
    if when == 'native_acl':
        config['before_native'] = lambda fd: (path / 'second').mkdir() if len(config['reads']) == 1 else None
    if when == 'final_path':
        original_lstat = Path.lstat
        count = [0]

        def lstat(self, *args, **kwargs):
            if self == path:
                count[0] += 1
                if count[0] == 3:
                    (path / 'second').mkdir()
            return original_lstat(self, *args, **kwargs)

        monkeypatch.setattr(Path, 'lstat', lstat)
    if first == 'final_path':
        _before_first_final_path(monkeypatch, path, lambda: (path / 'sibling-1').mkdir())
    with pytest.raises(StoreError, match='unsafe_filesystem'):
        module.check_darwin_acl(path, ancestor=True)
    assert len([call for call in calls if call[0] == 'stat']) == 2
    assert len([call for call in calls if call[0] == 'query']) <= 2
    with pytest.raises(OSError):
        os.fstat(config['reads'][0][0])


@pytest.mark.parametrize('field', ['st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid'])
@pytest.mark.parametrize('when', ['initial_fd', 'native_acl', 'final_fd', 'renewed_fd'])
def test_ancestor_never_renews_security_identity_change(tmp_path, monkeypatch, pinned_acl_api, field, when):
    from types import SimpleNamespace
    config, calls, _ = pinned_acl_api
    original_fstat = os.fstat
    count = [0]
    if when == 'native_acl':
        config['native_field'] = field
    else:
        target_call = {'initial_fd': 1, 'final_fd': 2, 'renewed_fd': 3}[when]

        def fstat(fd):
            info = original_fstat(fd)
            count[0] += 1
            if count[0] == target_call:
                values = {key: getattr(info, key) for key in (
                    'st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_nlink', 'st_ctime_ns')}
                values[field] += 1
                return SimpleNamespace(**values)
            return info

        monkeypatch.setattr(module.os, 'fstat', fstat)
    if when == 'renewed_fd':
        config['after_read'] = lambda fd, count: (tmp_path / 'sibling').mkdir() if count == 1 else None
    with pytest.raises(StoreError, match='unsafe_filesystem'):
        module.check_darwin_acl(tmp_path, ancestor=True)
    assert len([call for call in calls if call[0] == 'stat']) <= (2 if when == 'renewed_fd' else 1)


@pytest.mark.parametrize('when', ['final_fd', 'final_path'])
@pytest.mark.parametrize('change', ['replacement', 'symlink', 'mode', 'allow_acl', 'deny_rights', 'deny_qualifier', 'deny_inheritance', 'acl_flags', 'order', 'remove_acl', 'empty_acl'])
def test_ancestor_revalidation_rejects_path_security_and_complete_acl_changes(
        tmp_path, monkeypatch, pinned_acl_api, change, when):
    config, calls, _ = pinned_acl_api
    path = tmp_path / 'ancestor'
    path.mkdir(mode=0o700)
    original_entries = [(bytes(16), 2, 2), (b'\x01' * 16, 2, 4)]
    config.update(tags=[2, 2], snapshot=_encoded_acl(original_entries))

    def mutate(fd, count):
        if count != 1:
            return
        if change == 'replacement':
            path.rename(tmp_path / 'old')
            path.mkdir(mode=0o700)
        elif change == 'symlink':
            path.rename(tmp_path / 'old')
            path.symlink_to(tmp_path / 'old', target_is_directory=True)
        elif change == 'mode':
            path.chmod(0o500)
        else:
            (path / 'sibling').mkdir()
            entries = list(original_entries)
            if change == 'allow_acl':
                config['tags'] = [1, 2]
            elif change == 'deny_rights':
                entries[0] = (entries[0][0], 2, 4)
            elif change == 'deny_qualifier':
                entries[0] = (b'\x02' * 16, 2, 2)
            elif change == 'deny_inheritance':
                entries[0] = (entries[0][0], 2 | (1 << 5), 2)
            elif change == 'order':
                entries.reverse()
            elif change == 'remove_acl':
                config['present'] = 0
            elif change == 'empty_acl':
                config['tags'] = []
                entries = []
            config['snapshot'] = _encoded_acl(entries, flags=(1 << 16) if change == 'acl_flags' else 0)

    if when == 'final_fd':
        config['after_read'] = mutate
    else:
        _before_first_final_path(monkeypatch, path, lambda: mutate(config['reads'][0][0], 1))
    try:
        with pytest.raises(StoreError, match='unsafe_filesystem'):
            module.check_darwin_acl(path, ancestor=True)
        assert len([call for call in calls if call[0] == 'stat']) <= 2
    finally:
        if change == 'mode':
            path.chmod(0o700)
    with pytest.raises(OSError):
        os.fstat(config['reads'][0][0])


@pytest.mark.parametrize('when', ['final_fd', 'final_path'])
def test_ancestor_revalidation_distinguishes_absence_from_present_empty_acl(tmp_path, monkeypatch, pinned_acl_api, when):
    config, _, _ = pinned_acl_api
    config['present'] = 0

    def present_after_read(fd, count):
        if count == 1:
            (tmp_path / 'sibling').mkdir()
            config['present'] = 32

    if when == 'final_fd':
        config['after_read'] = present_after_read
    else:
        _before_first_final_path(monkeypatch, tmp_path, lambda: present_after_read(config['reads'][0][0], 1))
    with pytest.raises(StoreError, match='unsafe_filesystem'):
        module.check_darwin_acl(tmp_path, ancestor=True)
    assert config['reads'][0][1][1] is None
    assert config['reads'][1][1][1] == _encoded_acl([])


@pytest.mark.parametrize('change', ['size_zero', 'size_negative', 'size_too_large', 'size_wrong_count', 'copy_short', 'copy_error', 'magic', 'count', 'reserved'])
def test_acl_snapshot_rejects_malformed_native_evidence(tmp_path, pinned_acl_api, change):
    config, calls, api = pinned_acl_api
    size = 44
    if change.startswith('size_'):
        values = {'size_zero': 0, 'size_negative': -1, 'size_too_large': 2**62, 'size_wrong_count': size + 24}
        api.acl_size = lambda acl: values[change]
    elif change.startswith('copy_'):
        config['copy_result'] = size - 1 if change == 'copy_short' else -1
    else:
        data = bytearray(_encoded_acl([]))
        data[{'magic': 0, 'count': 39, 'reserved': 4}[change]] ^= 1
        config['snapshot'] = bytes(data)
    with pytest.raises(StoreError, match='filesystem_protection_unavailable'):
        module.check_darwin_acl(tmp_path, ancestor=True)
    assert len([call for call in calls if call[0] == 'stat']) == 1
    assert ('free_acl', 123) in calls and ('free_fsec', 77) in calls


@pytest.mark.parametrize('function', ['acl_size', 'acl_copy_ext'])
def test_acl_snapshot_missing_api_fails_before_open(darwin_acl_api, function):
    _, calls, api = darwin_acl_api
    delattr(api, function)
    with pytest.raises(StoreError, match='filesystem_protection_unavailable'):
        module.check_darwin_acl(Path('/synthetic-only'), ancestor=True)
    assert calls == []


@pytest.mark.parametrize('kind', ['regular', 'symlink'])
def test_ancestor_exception_never_accepts_other_path_kinds(tmp_path, pinned_acl_api, kind):
    _, calls, _ = pinned_acl_api
    path = tmp_path / 'target'
    if kind == 'regular':
        path.write_bytes(b'')
    else:
        path.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(StoreError, match='unsafe_filesystem'):
        module.check_darwin_acl(path, ancestor=True)
    assert not any(call[0] == 'stat' for call in calls)


@pytest.mark.parametrize('windows', [('initial_fd', 'native_acl'), ('initial_fd', 'final_fd'), ('native_acl', 'final_fd'),
                                   ('initial_fd', 'final_path'), ('native_acl', 'final_path'), ('final_fd', 'final_path')])
def test_each_first_drift_consumes_the_only_renewal(tmp_path, monkeypatch, pinned_acl_api, windows):
    config, calls, api = pinned_acl_api
    real_open = os.open

    def open_with_churn(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        (tmp_path / 'initial').mkdir()
        return fd

    if 'initial_fd' in windows:
        monkeypatch.setattr(module.os, 'open', open_with_churn)
    if 'native_acl' in windows:
        config['before_native'] = lambda fd: (tmp_path / 'native').mkdir()
    if 'final_fd' in windows:
        config['after_read'] = lambda fd, count: (tmp_path / 'final').mkdir()
    if 'final_path' in windows:
        _before_first_final_path(monkeypatch, tmp_path, lambda: (tmp_path / 'path').mkdir())
    with pytest.raises(StoreError, match='unsafe_filesystem'):
        module.check_darwin_acl(tmp_path, ancestor=True)
    assert len([call for call in calls if call[0] == 'stat']) == 1


def test_native_metadata_drift_returning_to_original_is_still_second_drift(tmp_path, pinned_acl_api):
    config, calls, api = pinned_acl_api
    original_native = api.fstatx_np

    def native_drift(fd, pointer, fsec):
        result = original_native(fd, pointer, fsec)
        pointer._obj.st_nlink += 1
        pointer._obj.st_ctimespec.tv_sec += 1
        return result

    api.fstatx_np = native_drift
    with pytest.raises(StoreError, match='unsafe_filesystem'):
        module.check_darwin_acl(tmp_path, ancestor=True)
    assert len(config['reads']) == 1
    assert len([call for call in calls if call[0] == 'stat']) == 1


@pytest.mark.parametrize('kind', ['directory', 'regular'])
def test_target_metadata_remains_strict_after_complete_acl_read(tmp_path, pinned_acl_api, kind):
    config, calls, _ = pinned_acl_api
    target = tmp_path / 'target'
    if kind == 'directory':
        target.mkdir()
        config['after_read'] = lambda fd, count: (target / 'sibling').mkdir()
    else:
        target.write_bytes(b'')
        config['after_read'] = lambda fd, count: os.link(target, tmp_path / 'hard-link')
    with pytest.raises(StoreError, match='unsafe_filesystem'):
        module.check_darwin_acl(target)
    assert len(config['reads']) == 1
    assert len([call for call in calls if call[0] == 'stat']) == 1
    with pytest.raises(OSError):
        os.fstat(config['reads'][0][0])


@pytest.mark.parametrize('when', ['final_fd', 'final_path'])
@pytest.mark.parametrize('change', [
    {'stat_result': -1, 'present': 0}, {'query_result': -1, 'present': 0},
    {'present': -1}, {'get_result': -1, 'acl': None}, {'acl': None}, {'acl': 1},
    {'valid_result': -1}, {'init': None}, {'entry_error': True},
    {'tags': [2] * 129}, {'copy_result': -1},
])
def test_ancestor_renewal_api_failure_never_reuses_first_acl(tmp_path, monkeypatch, pinned_acl_api, change, when):
    config, calls, _ = pinned_acl_api

    def fail_second(fd, count):
        if count == 1:
            (tmp_path / 'sibling').mkdir()
            config.update(change)

    if when == 'final_fd':
        config['after_read'] = fail_second
    else:
        _before_first_final_path(monkeypatch, tmp_path, lambda: fail_second(config['reads'][0][0], 1))
    with pytest.raises(StoreError, match='filesystem_protection_unavailable'):
        module.check_darwin_acl(tmp_path, ancestor=True)
    assert len(config['reads']) == 1
    assert len([call for call in calls if call[0] == 'stat']) <= 2
    with pytest.raises(OSError):
        os.fstat(config['reads'][0][0])


def test_first_unsafe_acl_fails_without_renewal_even_with_sibling_churn(tmp_path, pinned_acl_api):
    config, calls, _ = pinned_acl_api
    config['tags'] = [1]
    config['before_native'] = lambda fd: (tmp_path / 'sibling').mkdir()
    with pytest.raises(StoreError, match='unsafe_filesystem'):
        module.check_darwin_acl(tmp_path, ancestor=True)
    assert len([call for call in calls if call[0] == 'stat']) == 1
    assert config['reads'] == []


def _assert_store_opts_in_only_ancestor_directories(tmp_path, monkeypatch):
    from eidolon_cli.organization_setup_subscription_store import SubscriptionStore
    profile = tmp_path / 'profile'
    profile.mkdir(mode=0o700)
    store = SubscriptionStore(profile)
    calls = []
    monkeypatch.setattr(module, 'check_darwin_acl', lambda path, *, ancestor=False: calls.append((path, ancestor)))
    store._check(profile, directory=True)
    assert calls[0] == (profile, False)
    assert calls[1:] == [(parent, True) for parent in profile.parents]


@pytest.mark.linux_only
def test_store_opts_in_only_ancestor_directories(tmp_path, monkeypatch):
    _assert_store_opts_in_only_ancestor_directories(tmp_path, monkeypatch)


@pytest.mark.macos_only
def test_native_darwin_store_opts_in_only_ancestor_directories(tmp_path, monkeypatch):
    _assert_store_opts_in_only_ancestor_directories(tmp_path, monkeypatch)


@pytest.mark.parametrize('kind', [0, 1, 3, 15])
def test_exported_acl_kind_must_agree_with_validated_deny_entry(tmp_path, pinned_acl_api, kind):
    config, calls, _ = pinned_acl_api
    config.update(tags=[2], snapshot=_encoded_acl([(bytes(16), kind, 0)]))
    with pytest.raises(StoreError, match='filesystem_protection_unavailable'):
        module.check_darwin_acl(tmp_path, ancestor=True)
    assert len([call for call in calls if call[0] == 'stat']) == 1


@pytest.mark.macos_only
@pytest.mark.parametrize('change_deny_rights', [False, True], ids=['unchanged-deny', 'changed-deny-rights'])
def test_native_darwin_ancestor_revalidates_complete_deny_acl(tmp_path, monkeypatch, change_deny_rights):
    """Only a new disposable directory is changed; all ACL reads use Darwin."""
    import stat
    import subprocess
    if sys.platform != 'darwin':
        pytest.skip('Native ACL serialization proof requires macOS')
    chmod = Path('/bin/chmod')
    binary = chmod.stat()
    assert stat.S_ISREG(binary.st_mode) and binary.st_uid == 0 and not binary.st_mode & 0o022
    path = tmp_path / 'native-acl-ancestor'
    path.mkdir(mode=0o700)
    root = tmp_path.resolve(strict=True)
    captured = path.lstat()
    snapshots = []
    original_read = module._read_darwin_acl

    def chmod_fixture(*arguments):
        assert not path.is_symlink() and path.resolve(strict=True).parent == root
        current = path.lstat()
        assert (current.st_dev, current.st_ino) == (captured.st_dev, captured.st_ino)
        subprocess.run([str(chmod), *arguments, str(path)], check=True, capture_output=True, timeout=10)

    def read_then_churn(api, fstatx, fd, before, **kwargs):
        assert module._directory_identity(os.fstat(fd)) == module._directory_identity(captured)
        result = original_read(api, fstatx, fd, before, **kwargs)
        snapshots.append((fd, result[1]))
        if len(snapshots) == 1:
            (path / 'sibling').mkdir(mode=0o700)
            if change_deny_rights:
                chmod_fixture('=a#', '0', 'everyone deny write')
        return result

    try:
        # A newly created temp dir can inherit ACLs from the test runner's temp
        # root. Clear only this fresh fixture so its ACL is exactly our one ACE.
        chmod_fixture('-N')
        chmod_fixture('+a', 'everyone deny delete')
        monkeypatch.setattr(module, '_read_darwin_acl', read_then_churn)
        if change_deny_rights:
            with pytest.raises(StoreError, match='unsafe_filesystem'):
                module.check_darwin_acl(path, ancestor=True)
        else:
            module.check_darwin_acl(path, ancestor=True)
        assert len(snapshots) == 2 and snapshots[0][0] == snapshots[1][0]
        for _, snapshot in snapshots:
            assert snapshot is not None and len(snapshot) == 44 + 24
            assert int.from_bytes(snapshot[36:40], 'big') == 1
            assert int.from_bytes(snapshot[60:64], 'big') & 0xf == 2
        assert (snapshots[0][1] != snapshots[1][1]) == change_deny_rights
        assert module._directory_identity(path.lstat()) == module._directory_identity(captured)
    finally:
        chmod_fixture('-N')


@pytest.mark.parametrize('when', ['final_fd', 'final_path'])
@pytest.mark.parametrize('change_acl', [False, True])
def test_ctime_only_ancestor_drift_requires_same_complete_acl(tmp_path, monkeypatch, pinned_acl_api, change_acl, when):
    """Synthetic ctime-only metadata; real sibling churn is covered separately."""
    from types import SimpleNamespace
    config, calls, api = pinned_acl_api
    config.update(tags=[2], snapshot=_encoded_acl([(bytes(16), 2, 2)]))
    before = tmp_path.lstat()
    identity = module._file_identity(before)
    real_fstat, real_lstat = os.fstat, Path.lstat
    native_stat = api.fstatx_np
    changed = False

    def ctime_view(info):
        assert module._file_identity(info) == identity
        fields = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_nlink', 'st_ctime_ns')
        values = {field: getattr(info, field) for field in fields}
        values['st_ctime_ns'] += int(changed)
        return SimpleNamespace(**values)

    def fstat(fd):
        info = real_fstat(fd)
        return ctime_view(info) if (info.st_dev, info.st_ino) == identity[:2] else info

    def lstat(path, *args, **kwargs):
        info = real_lstat(path, *args, **kwargs)
        return ctime_view(info) if path == tmp_path else info

    def fstatx(fd, pointer, fsec):
        result = native_stat(fd, pointer, fsec)
        info = ctime_view(real_fstat(fd))
        pointer._obj.st_ctimespec.tv_sec, pointer._obj.st_ctimespec.tv_nsec = divmod(info.st_ctime_ns, 1_000_000_000)
        return result

    def change_ctime(fd, count):
        nonlocal changed
        assert module._file_identity(real_fstat(fd)) == identity
        if count == 1:
            changed = True
            if change_acl:
                config['snapshot'] = _encoded_acl([(bytes(16), 2, 4)])

    monkeypatch.setattr(module.os, 'fstat', fstat)
    monkeypatch.setattr(Path, 'lstat', lstat)
    for symbol in ('fstatx_np', 'fstatx_np$INODE64'):
        monkeypatch.setattr(api, symbol, fstatx)
    if when == 'final_fd':
        config['after_read'] = change_ctime
    else:
        _before_first_final_path(monkeypatch, tmp_path, lambda: change_ctime(config['reads'][0][0], 1))
    if change_acl:
        with pytest.raises(StoreError, match='unsafe_filesystem'):
            module.check_darwin_acl(tmp_path, ancestor=True)
    else:
        module.check_darwin_acl(tmp_path, ancestor=True)
    after = module._file_identity(tmp_path.lstat())
    assert tuple(new - old for old, new in zip(identity, after)) == (0, 0, 0, 0, 0, 0, 1)
    assert len(config['reads']) == 2
    assert config['reads'][0][0] == config['reads'][1][0]
    assert [module._file_identity(result[0]) for _, result in config['reads']] == [identity, after]
    assert (config['reads'][0][1][1] != config['reads'][1][1][1]) == change_acl
    for operation in ('stat', 'query', 'get', 'copy', 'free_acl', 'free_fsec'):
        assert sum(call[0] == operation for call in calls) == 2


@pytest.mark.parametrize('when,field', [
    (when, field)
    for when, fields in (
        ('final_path', ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid')),
        ('fresh_fd', ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_nlink', 'st_ctime_ns')),
    )
    for field in fields
] + [('fresh_fd', 'original')])
def test_final_path_renewal_requires_fresh_descriptor_and_original_security(
        tmp_path, monkeypatch, pinned_acl_api, when, field):
    """Synthetic hostile metadata cannot select or change a fresh baseline."""
    from types import SimpleNamespace
    config, calls, _ = pinned_acl_api
    real_fstat, real_lstat = os.fstat, Path.lstat
    original = tmp_path.lstat()
    changed = []
    churn = []

    def altered(info):
        values = {key: getattr(info, key) for key in (
            'st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_nlink', 'st_ctime_ns')}
        if field == 'original':
            values = {key: getattr(original, key) for key in values}
        else:
            values[field] += 1
        changed.append(True)
        return SimpleNamespace(**values)

    def fstat(fd):
        info = real_fstat(fd)
        caller = sys._getframe(1)
        if churn and not changed and caller.f_code is module._check_ancestor_acl.__code__:
            return altered(info)
        return info

    def lstat(path, *args, **kwargs):
        info = real_lstat(path, *args, **kwargs)
        caller = sys._getframe(1)
        if (path == tmp_path and caller.f_code is module._check_ancestor_acl.__code__
                and 'final_fd' in caller.f_locals and 'final_path' not in caller.f_locals):
            return altered(info)
        return info

    if when == 'final_path':
        monkeypatch.setattr(Path, 'lstat', lstat)
    else:
        churn = _before_first_final_path(monkeypatch, tmp_path, lambda: (tmp_path / 'sibling').mkdir())
        monkeypatch.setattr(module.os, 'fstat', fstat)
    with pytest.raises(StoreError, match='unsafe_filesystem'):
        module.check_darwin_acl(tmp_path, ancestor=True)
    assert changed == [True]
    assert len(config['reads']) == 1
    assert sum(call[0] == 'stat' for call in calls) == 1
    with pytest.raises(OSError):
        real_fstat(config['reads'][0][0])


@pytest.mark.macos_only
def test_native_darwin_ancestor_revalidates_final_path_sibling_change(tmp_path, monkeypatch):
    """Real Darwin ACL snapshots around one final-FD-to-path sibling mkdir."""
    if sys.platform != 'darwin':
        pytest.skip('Native final-path ACL proof requires macOS')
    path = tmp_path / 'native-final-path-ancestor'
    path.mkdir(mode=0o700)
    before = path.lstat()
    real_read = module._read_darwin_acl
    reads = []

    def read(*args, **kwargs):
        result = real_read(*args, **kwargs)
        reads.append((args[2], result))
        return result

    monkeypatch.setattr(module, '_read_darwin_acl', read)
    mutations = _before_first_final_path(monkeypatch, path, lambda: (path / 'sibling').mkdir(mode=0o700))
    module.check_darwin_acl(path, ancestor=True)
    after = path.lstat()
    assert mutations == [True] and len(reads) == 2 and reads[0][0] == reads[1][0]
    assert module._directory_identity(before) == module._directory_identity(after)
    assert (before.st_nlink, before.st_ctime_ns) != (after.st_nlink, after.st_ctime_ns)
    assert [module._file_identity(result[0]) for _, result in reads] == [
        module._file_identity(before), module._file_identity(after)]
    assert reads[0][1][1] == reads[1][1][1]
    with pytest.raises(OSError):
        os.fstat(reads[0][0])
