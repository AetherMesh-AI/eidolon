"""Inert prepared dependency ingestion against real adversarial filesystem trees."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from eidolon_cli import organization_project_dependency_bundle as bundle


def digest(data):
    return hashlib.sha256(data).hexdigest()


def raw(value):
    return json.dumps(value).encode()


@pytest.fixture
def prepared(tmp_path):
    source = tmp_path / 'dependencies'
    source.mkdir()
    package_manifests = {
        'package.json': raw({'name': 'root', 'workspaces': ['apps/*']}),
        'apps/desktop/package.json': raw({'name': 'desktop', 'dependencies': {'checker': '1.2.3'}}),
        'apps/shared/package.json': raw({'name': '@example/shared', 'version': '1.0.0'}),
    }
    lock = {
        'lockfileVersion': 3,
        'packages': {
            '': {'name': 'root'},
            'apps/desktop': {'name': 'desktop'},
            'apps/shared': {'name': '@example/shared', 'version': '1.0.0'},
            'node_modules/desktop': {'link': True, 'resolved': 'apps/desktop'},
            'node_modules/@example/shared': {'link': True, 'resolved': 'apps/shared'},
            'node_modules/checker': {'version': '1.2.3',
                                     'resolved': 'https://registry.npmjs.org/checker/-/checker-1.2.3.tgz',
                                     'integrity': 'sha512-' + 'A' * 86 + '=='},
        },
    }
    lockfile_bytes = raw(lock)
    content = {
        'node_modules/checker/package.json': (raw({'name': 'checker', 'version': '1.2.3'}), 0o644, 'file'),
        'node_modules/checker/index.js': (b'export const inert = true;\n', 0o644, 'file'),
        'node_modules/checker/bin/check': (b'\x7fELF\x00inert-native-fixture', 0o755, 'native'),
        'node_modules/.package-lock.json': (raw({'lockfileVersion': 3}), 0o644, 'file'),
    }
    entries = []
    for path, (data, mode, kind) in content.items():
        target = source / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(mode)
        entries.append({'path': path, 'bytes': len(data), 'sha256': digest(data), 'mode': mode, 'kind': kind})
    toolchain = {'platform': 'linux', 'arch': 'x64', 'libc': 'glibc', 'nodeVersion': '24.11.0',
                 'npmVersion': '11.17.0', 'nodeAbi': '137', 'tools': {'checker': '1.2.3'}}
    manifest = {
        'schemaVersion': 1,
        'lockfile': {'path': 'package-lock.json', 'sha256': digest(lockfile_bytes)},
        'packageManifests': [{'path': path, 'sha256': digest(data)} for path, data in package_manifests.items()],
        'selectedWorkspaces': ['apps/desktop', 'apps/shared'],
        'workspaceLinks': [{'path': 'node_modules/desktop', 'target': 'apps/desktop'},
                           {'path': 'node_modules/@example/shared', 'target': 'apps/shared'}],
        'toolchain': toolchain,
        'provenance': {'kind': 'owner-supplied', 'preparationRecordSha256': digest(b'owner preparation record'),
                       'registryPackages': []},
        'files': entries,
    }
    return {'source': source, 'destination': tmp_path / 'verified', 'manifest': manifest,
            'inputs': {'lockfile_bytes': lockfile_bytes, 'package_manifests': package_manifests,
                       'expected_toolchain': deepcopy(toolchain)}}


def normalize(prepared, **overrides):
    return bundle.normalize_prepared_dependency_manifest(prepared['manifest'], **(prepared['inputs'] | overrides))


def stage(prepared, **overrides):
    return bundle.copy_verified_dependency_bundle(prepared['source'], prepared['destination'],
                                                  prepared['manifest'], **(prepared['inputs'] | overrides))


def assert_clean(prepared):
    assert not prepared['destination'].exists()
    assert not list(prepared['destination'].parent.glob('.dependency-*'))


def change_file(prepared, path, data):
    (prepared['source'] / path).write_bytes(data)
    item = next(item for item in prepared['manifest']['files'] if item['path'] == path)
    item.update(bytes=len(data), sha256=digest(data))


@pytest.mark.linux_only
def test_copy_binds_complete_bytes_modes_graph_and_never_confers_execution(prepared):
    receipt = stage(prepared)
    assert receipt == normalize(prepared)
    assert receipt['executionAuthorized'] is False
    assert receipt['runtimeCompatibilityVerified'] is False
    assert receipt['provenanceAuthenticated'] is False
    assert receipt['lockfileSha256'] == digest(prepared['inputs']['lockfile_bytes'])
    assert receipt['inventorySha256'] == digest(bundle._canonical(receipt['manifest']['files']))
    assert receipt['bundleSha256'] == digest(bundle._canonical(receipt['manifest']))
    paths = {str(path.relative_to(prepared['destination'])) for path in prepared['destination'].rglob('*') if path.is_file()}
    assert paths == {item['path'] for item in prepared['manifest']['files']}
    for item in receipt['manifest']['files']:
        target = prepared['destination'] / item['path']
        assert target.read_bytes() == (prepared['source'] / item['path']).read_bytes()
        assert stat.S_IMODE(target.stat().st_mode) == (0o555 if item['mode'] & 0o111 else 0o444)
    for link in receipt['manifest']['workspaceLinks']:
        assert not (prepared['destination'] / link['path']).exists()
    assert stat.S_IMODE(prepared['destination'].stat().st_mode) == 0o555


def test_identity_is_canonical_but_raw_lock_and_manifest_bytes_are_exact(prepared):
    receipt = normalize(prepared)
    prepared['manifest']['files'].reverse()
    prepared['manifest']['selectedWorkspaces'].reverse()
    prepared['manifest']['packageManifests'].reverse()
    prepared['manifest']['workspaceLinks'].reverse()
    assert normalize(prepared) == receipt
    assert normalize(prepared, lockfile_bytes=prepared['inputs']['lockfile_bytes']) == receipt
    with pytest.raises(ValueError, match='raw identity'):
        normalize(prepared, lockfile_bytes=prepared['inputs']['lockfile_bytes'] + b'\n')
    packages = dict(prepared['inputs']['package_manifests'])
    packages['package.json'] += b'\n'
    with pytest.raises(ValueError, match='raw identity'):
        normalize(prepared, package_manifests=packages)
    prepared['manifest']['provenance']['preparationRecordSha256'] = digest(b'different owner preparation')
    assert normalize(prepared)['bundleSha256'] != receipt['bundleSha256']
    assert normalize(prepared)['inventorySha256'] == receipt['inventorySha256']


def test_registry_metadata_is_bound_to_lock_but_not_independent_attestation(prepared):
    package = json.loads(prepared['inputs']['lockfile_bytes'])['packages']['node_modules/checker']
    prepared['manifest']['provenance'] = {
        'kind': 'registry-prepared', 'preparationRecordSha256': digest(b'record'),
        'registryPackages': [{'path': 'node_modules/checker', **{key: package[key] for key in ('resolved', 'integrity')}}],
    }
    receipt = normalize(prepared)
    assert receipt['manifest']['provenance']['kind'] == 'registry-prepared'
    assert receipt['provenanceAuthenticated'] is False
    assert receipt['executionAuthorized'] is False
    prepared['manifest']['provenance']['registryPackages'][0]['integrity'] = 'sha512-' + 'B' * 85 + 'A=='
    with pytest.raises(ValueError, match='exact lockfile'):
        normalize(prepared)


@pytest.mark.parametrize('mutation', ['version', 'lock_version', 'toolchain', 'package_extra', 'package_missing',
                                      'workspace_missing', 'link_missing', 'link_escape', 'link_target',
                                      'registry_missing', 'authority', 'empty_files', 'package_not_locked'])
def test_invalid_bindings_fail_before_creating_destination(prepared, mutation):
    manifest = prepared['manifest']
    if mutation == 'version':
        manifest['schemaVersion'] = True
    elif mutation == 'lock_version':
        lock = json.loads(prepared['inputs']['lockfile_bytes'])
        lock['lockfileVersion'] = 2
        prepared['inputs']['lockfile_bytes'] = raw(lock)
        manifest['lockfile']['sha256'] = digest(raw(lock))
    elif mutation == 'toolchain':
        manifest['toolchain']['nodeAbi'] = '138'
    elif mutation == 'package_extra':
        prepared['inputs']['package_manifests']['elsewhere/package.json'] = b'{}'
    elif mutation == 'package_missing':
        del prepared['inputs']['package_manifests']['apps/shared/package.json']
    elif mutation == 'workspace_missing':
        manifest['selectedWorkspaces'].append('apps/missing')
    elif mutation == 'link_missing':
        manifest['workspaceLinks'].pop()
    elif mutation == 'link_escape':
        manifest['workspaceLinks'][0]['target'] = '../outside'
    elif mutation == 'link_target':
        manifest['workspaceLinks'][0]['target'] = 'apps/shared'
    elif mutation == 'registry_missing':
        manifest['provenance']['kind'] = 'registry-prepared'
    elif mutation == 'authority':
        manifest['executionAuthorized'] = True
    elif mutation == 'empty_files':
        manifest['files'].clear()
    else:
        manifest['files'][0]['path'] = 'node_modules/unlocked/package.json'
    with pytest.raises(ValueError):
        normalize(prepared)
    assert_clean(prepared)


@pytest.mark.parametrize('field,value', [('bytes', True), ('bytes', -1), ('bytes', 2**40), ('mode', True),
                                         ('mode', 0o4755), ('mode', 0o777), ('sha256', 'F' * 64),
                                         ('kind', 'symlink'), ('path', '../outside'), ('path', '/absolute'),
                                         ('path', 'node_modules//x'), ('path', 'node_modules/x/../y'),
                                         ('path', 'node_modules\\x'), ('path', 'node_modules/x.'),
                                         ('path', 'node_modules/x\x00'), ('path', 'node_modules/x:y')])
def test_invalid_inventory_descriptor_is_rejected(prepared, field, value):
    prepared['manifest']['files'][0][field] = value
    with pytest.raises(ValueError):
        normalize(prepared)
    assert_clean(prepared)


@pytest.mark.parametrize('path', ['node_modules/checker/package.json', 'node_modules/CHECKER/package.json',
                                  'node_modules/checker', 'node_modules/Checker/new.js',
                                  'node_modules/desktop/package.json'])
def test_duplicate_prefix_and_workspace_shadow_collisions_are_rejected(prepared, path):
    item = dict(prepared['manifest']['files'][0], path=path)
    prepared['manifest']['files'].append(item)
    with pytest.raises(ValueError):
        normalize(prepared)


@pytest.mark.parametrize('attack', ['missing', 'extra', 'empty_dir', 'same_size', 'size', 'mode',
                                    'symlink', 'hardlink', 'fifo', 'directory_symlink', 'workspace_symlink'])
@pytest.mark.linux_only
def test_real_filesystem_attacks_leave_no_staged_tree(prepared, tmp_path, attack):
    target = prepared['source'] / 'node_modules/checker/index.js'
    outside = tmp_path / 'outside'
    outside.write_bytes(target.read_bytes())
    if attack == 'missing':
        target.unlink()
    elif attack == 'extra':
        target.with_name('extra.js').write_bytes(b'extra')
    elif attack == 'empty_dir':
        target.with_name('empty').mkdir()
    elif attack == 'same_size':
        target.write_bytes(b'x' * target.stat().st_size)
    elif attack == 'size':
        target.write_bytes(b'oversized replacement file' * 100)
    elif attack == 'mode':
        target.chmod(0o755)
    elif attack == 'directory_symlink':
        directory = target.parent
        moved = tmp_path / 'moved'
        directory.rename(moved)
        directory.symlink_to(moved, target_is_directory=True)
    elif attack == 'workspace_symlink':
        (prepared['source'] / 'node_modules/desktop').symlink_to(outside)
    else:
        target.unlink()
        actions = {'symlink': lambda: target.symlink_to(outside),
                   'hardlink': lambda: os.link(outside, target), 'fifo': lambda: os.mkfifo(target)}
        actions[attack]()
    with pytest.raises((ValueError, OSError)):
        stage(prepared)
    assert_clean(prepared)
    assert outside.read_bytes() != b''


@pytest.mark.linux_only
def test_installed_package_version_must_match_lock_even_with_matching_file_digest(prepared):
    change_file(prepared, 'node_modules/checker/package.json', raw({'name': 'checker', 'version': '9.9.9'}))
    with pytest.raises(ValueError, match='version differs'):
        stage(prepared)
    assert_clean(prepared)


@pytest.mark.parametrize('limit,value', [('max_files', 2), ('max_bytes', 4), ('max_file_bytes', 4),
                                        ('max_manifest_bytes', 50), ('max_lockfile_bytes', 50),
                                        ('max_package_manifests', 2), ('max_package_manifest_bytes', 2),
                                        ('max_depth', 2), ('max_path_bytes', 12)])
def test_each_explicit_ingestion_bound_is_enforced(prepared, limit, value):
    limits = replace(bundle.DependencyBundleLimits(), **{limit: value})
    with pytest.raises(ValueError):
        normalize(prepared, limits=limits)
    assert_clean(prepared)


@pytest.mark.parametrize('value', [True, 0, -1, 100_001, float('inf')])
def test_limits_cannot_disable_ceiling_or_use_boolean(prepared, value):
    with pytest.raises(ValueError):
        normalize(prepared, limits=replace(bundle.DependencyBundleLimits(), max_files=value))


@pytest.mark.parametrize('document', [b'{"schemaVersion":1,"schemaVersion":1}',
                                      b'{"x":' + b'[' * 65 + b'0' + b']' * 65 + b'}',
                                      b'{"x":NaN}', b'[]', b'\xff', b'{} trailing'])
def test_raw_manifest_json_is_bounded_and_unambiguous(prepared, document):
    prepared['manifest'] = document
    with pytest.raises(ValueError):
        normalize(prepared)
    assert_clean(prepared)


def test_raw_manifest_form_is_equivalent_and_external_object_mutation_cannot_change_receipt(prepared):
    receipt = normalize(prepared)
    assert bundle.normalize_prepared_dependency_manifest(raw(prepared['manifest']), **prepared['inputs']) == receipt
    prepared['manifest']['files'][0]['sha256'] = '0' * 64
    prepared['inputs']['expected_toolchain']['tools']['checker'] = '9.9.9'
    assert receipt['manifest']['files'][0]['sha256'] != '0' * 64
    assert receipt['manifest']['toolchain']['tools']['checker'] == '1.2.3'


@pytest.mark.parametrize('when', ['before', 'read', 'seal', 'deadline'])
@pytest.mark.linux_only
def test_cancel_and_deadline_cleanup_cover_read_and_seal(prepared, monkeypatch, when):
    cancel = threading.Event()
    if when == 'before':
        cancel.set()
    if when == 'read':
        original = os.read
        def read(descriptor, count):
            data = original(descriptor, count)
            cancel.set()
            return data
        monkeypatch.setattr(os, 'read', read)
    if when == 'seal':
        original = os.fchmod
        def chmod(descriptor, mode):
            original(descriptor, mode)
            if mode == 0o555:
                cancel.set()
        monkeypatch.setattr(os, 'fchmod', chmod)
    with pytest.raises(ValueError, match='cancelled|deadline'):
        stage(prepared, cancel=cancel, deadline=time.monotonic() - 1 if when == 'deadline' else None)
    assert_clean(prepared)


@pytest.mark.parametrize('deadline', [True, 'tomorrow', float('inf'), float('nan')])
@pytest.mark.linux_only
def test_invalid_deadline_is_rejected_before_staging(prepared, deadline):
    with pytest.raises(ValueError, match='deadline'):
        stage(prepared, deadline=deadline)
    assert_clean(prepared)


@pytest.mark.linux_only
def test_same_size_mid_read_mutation_cannot_receive_verified_receipt(prepared, monkeypatch):
    original = os.read
    changed = False
    def read(descriptor, count):
        nonlocal changed
        data = original(descriptor, count)
        if not changed and data:
            changed = True
            target = Path('/proc/self/fd') / str(descriptor)
            path = Path(os.readlink(target))
            path.write_bytes(b'x' * path.stat().st_size)
        return data
    monkeypatch.setattr(os, 'read', read)
    with pytest.raises(ValueError, match='identity changed'):
        stage(prepared)
    assert_clean(prepared)


@pytest.mark.linux_only
def test_entry_replacement_after_open_is_detected(prepared, monkeypatch):
    original = os.read
    changed = False
    def read(descriptor, count):
        nonlocal changed
        data = original(descriptor, count)
        if not changed and data:
            changed = True
            path = Path(os.readlink('/proc/self/fd/' + str(descriptor)))
            replacement = path.with_name('replacement')
            replacement.write_bytes(path.read_bytes())
            replacement.chmod(stat.S_IMODE(path.stat().st_mode))
            replacement.replace(path)
        return data
    monkeypatch.setattr(os, 'read', read)
    with pytest.raises(ValueError, match='identity changed'):
        stage(prepared)
    assert_clean(prepared)


@pytest.mark.linux_only
def test_new_directory_entry_during_copy_is_detected(prepared, monkeypatch):
    original = os.read
    changed = False
    def read(descriptor, count):
        nonlocal changed
        data = original(descriptor, count)
        if not changed and data:
            changed = True
            (prepared['source'] / 'new-unlisted').write_bytes(b'new')
        return data
    monkeypatch.setattr(os, 'read', read)
    with pytest.raises(ValueError, match='undeclared'):
        stage(prepared)
    assert_clean(prepared)


@pytest.mark.linux_only
def test_source_ancestor_links_and_swapped_directories_are_not_followed(prepared, tmp_path, monkeypatch):
    alias = tmp_path / 'alias'
    alias.symlink_to(prepared['source'], target_is_directory=True)
    source = prepared['source']
    prepared['source'] = alias
    with pytest.raises(OSError):
        stage(prepared)
    assert_clean(prepared)
    prepared['source'] = source
    original = os.open
    swapped = False
    def opening(name, flags, *args, **kwargs):
        nonlocal swapped
        if name == 'checker' and not swapped:
            swapped = True
            real = source / 'node_modules/checker'
            moved = tmp_path / 'moved'
            real.rename(moved)
            real.symlink_to(moved, target_is_directory=True)
        return original(name, flags, *args, **kwargs)
    monkeypatch.setattr(os, 'open', opening)
    with pytest.raises(OSError):
        stage(prepared)
    assert_clean(prepared)


@pytest.mark.linux_only
def test_unstable_enumeration_is_rejected(prepared, monkeypatch):
    original = os.scandir
    injected = False
    @contextmanager
    def duplicate():
        yield iter([SimpleNamespace(name='node_modules'), SimpleNamespace(name='node_modules')])
    def scan(path):
        nonlocal injected
        if isinstance(path, int) and not injected:
            injected = True
            return duplicate()
        return original(path)
    monkeypatch.setattr(os, 'scandir', scan)
    with pytest.raises(ValueError, match='unstable'):
        stage(prepared)
    assert_clean(prepared)


@pytest.mark.linux_only
def test_existing_destination_is_never_replaced(prepared):
    prepared['destination'].mkdir()
    marker = prepared['destination'] / 'keep'
    marker.write_bytes(b'untouched')
    with pytest.raises(FileExistsError):
        stage(prepared)
    assert marker.read_bytes() == b'untouched'


@pytest.mark.parametrize('kind', ['public_parent', 'linked_parent', 'linked_destination', 'relative_destination'])
@pytest.mark.linux_only
def test_destination_requires_private_unlinked_exact_parent(prepared, tmp_path, kind):
    private = tmp_path / 'private'
    private.mkdir(mode=0o700)
    if kind == 'public_parent':
        private.chmod(0o755)
        prepared['destination'] = private / 'out'
    elif kind == 'linked_parent':
        alias = tmp_path / 'alias'
        alias.symlink_to(private, target_is_directory=True)
        prepared['destination'] = alias / 'out'
    elif kind == 'linked_destination':
        prepared['destination'].symlink_to(private, target_is_directory=True)
    else:
        prepared['destination'] = Path('relative-output')
    with pytest.raises((ValueError, OSError)):
        stage(prepared)
    assert not list(private.iterdir())


@pytest.mark.linux_only
def test_cleanup_failure_keeps_original_error_and_reports_uncertain_cleanup(prepared, monkeypatch):
    original = bundle._cleanup
    def fail(*args):
        raise OSError('injected removal failure')
    monkeypatch.setattr(bundle, '_cleanup', fail)
    (prepared['source'] / 'extra').write_bytes(b'extra')
    with pytest.raises(ValueError, match='undeclared') as error:
        stage(prepared)
    assert any('cleanup failed' in note for note in error.value.__notes__)
    parent = os.open(prepared['destination'].parent, bundle._DIR_FLAGS)
    try:
        info = prepared['destination'].stat()
        original(parent, prepared['destination'].name, (info.st_dev, info.st_ino))
    finally:
        os.close(parent)
    assert_clean(prepared)


@pytest.mark.linux_only
def test_short_writes_are_completed_and_source_bytes_are_not_aliased(prepared, monkeypatch):
    original = os.write
    monkeypatch.setattr(os, 'write', lambda descriptor, block: original(descriptor, block[:3]))
    receipt = stage(prepared)
    source = prepared['source'] / 'node_modules/checker/index.js'
    original_bytes = source.read_bytes()
    source.write_bytes(b'changed later')
    assert (prepared['destination'] / 'node_modules/checker/index.js').read_bytes() == original_bytes
    assert receipt['executionAuthorized'] is False


@pytest.mark.linux_only
def test_zero_progress_write_fails_and_cleans(prepared, monkeypatch):
    monkeypatch.setattr(os, 'write', lambda descriptor, block: 0)
    with pytest.raises(OSError, match='no progress'):
        stage(prepared)
    assert_clean(prepared)


@pytest.mark.linux_only
def test_larger_inventory_stages_without_legacy_compiler_file_ceiling(prepared):
    for index in range(300):
        path = f'node_modules/checker/lib/part-{index}.js'
        data = f'export const part = {index};'.encode()
        target = prepared['source'] / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(0o644)
        prepared['manifest']['files'].append({'path': path, 'bytes': len(data), 'sha256': digest(data),
                                              'mode': 0o644, 'kind': 'file'})
    receipt = stage(prepared)
    assert len(receipt['manifest']['files']) == len(prepared['manifest']['files'])
    assert all((prepared['destination'] / item['path']).is_file() for item in receipt['manifest']['files'])


@pytest.mark.linux_only
def test_fifo_substitution_is_rejected_without_opening_it_for_read(prepared, monkeypatch):
    original = os.open
    changed = False
    def opening(name, flags, *args, **kwargs):
        nonlocal changed
        if name == 'index.js' and flags & os.O_PATH and not changed:
            changed = True
            target = prepared['source'] / 'node_modules/checker/index.js'
            target.unlink()
            os.mkfifo(target)
        if isinstance(name, str) and name.startswith('/proc/self/fd/'):
            assert stat.S_ISREG(os.fstat(int(name.rsplit('/', 1)[1])).st_mode)
        return original(name, flags, *args, **kwargs)
    monkeypatch.setattr(os, 'open', opening)
    with pytest.raises(ValueError, match='file type'):
        stage(prepared)
    assert changed
    assert_clean(prepared)


@pytest.mark.linux_only
def test_replaced_source_root_fails_even_when_opened_old_tree_is_unchanged(prepared, tmp_path, monkeypatch):
    original = os.read
    changed = False
    moved = tmp_path / 'moved-source'
    def read(descriptor, count):
        nonlocal changed
        data = original(descriptor, count)
        if not changed and data:
            changed = True
            prepared['source'].rename(moved)
            prepared['source'].mkdir()
        return data
    monkeypatch.setattr(os, 'read', read)
    with pytest.raises(ValueError, match='changed'):
        stage(prepared)
    assert_clean(prepared)


@pytest.mark.linux_only
def test_replaced_destination_is_preserved_and_uncertain_cleanup_is_reported(prepared, tmp_path, monkeypatch):
    original = os.read
    changed = False
    moved = tmp_path / 'moved-stage'
    def read(descriptor, count):
        nonlocal changed
        data = original(descriptor, count)
        if not changed and data:
            changed = True
            prepared['destination'].rename(moved)
            prepared['destination'].mkdir()
            (prepared['destination'] / 'keep').write_bytes(b'preserve replacement')
        return data
    monkeypatch.setattr(os, 'read', read)
    with pytest.raises(ValueError, match='root identity changed') as error:
        stage(prepared)
    assert (prepared['destination'] / 'keep').read_bytes() == b'preserve replacement'
    assert any('cleanup failed' in note and 'refusing to remove' in note for note in error.value.__notes__)
    parent = os.open(tmp_path, bundle._DIR_FLAGS)
    try:
        info = moved.stat()
        bundle._cleanup(parent, moved.name, (info.st_dev, info.st_ino))
    finally:
        os.close(parent)


def test_raw_lock_and_package_duplicate_keys_are_rejected(prepared):
    lock = prepared['inputs']['lockfile_bytes']
    duplicate_lock = lock[:-1] + b', "lockfileVersion": 3}'
    prepared['manifest']['lockfile']['sha256'] = digest(duplicate_lock)
    with pytest.raises(ValueError, match='unambiguous JSON'):
        normalize(prepared, lockfile_bytes=duplicate_lock)
    prepared['manifest']['lockfile']['sha256'] = digest(lock)
    package = b'{"name":"root","name":"other"}'
    prepared['inputs']['package_manifests']['package.json'] = package
    next(item for item in prepared['manifest']['packageManifests'] if item['path'] == 'package.json')['sha256'] = digest(package)
    with pytest.raises(ValueError, match='unambiguous JSON'):
        normalize(prepared)


@pytest.mark.parametrize('tools', [{'checker': '9.9.9'}, {'missing-tool': '1.2.3'}])
@pytest.mark.linux_only
def test_matching_tool_declarations_cannot_invent_installed_tool_identity(prepared, tools):
    prepared['manifest']['toolchain']['tools'] = tools
    prepared['inputs']['expected_toolchain']['tools'] = deepcopy(tools)
    with pytest.raises(ValueError, match='Required tool is absent'):
        normalize(prepared)
    with pytest.raises(ValueError, match='Required tool is absent'):
        stage(prepared)
    assert_clean(prepared)


@pytest.mark.linux_only
def test_matching_tool_version_cannot_alias_a_different_package_name(prepared):
    change_file(prepared, 'node_modules/checker/package.json', raw({'name': 'impostor', 'version': '1.2.3'}))
    with pytest.raises(ValueError, match='tool package name differs'):
        stage(prepared)
    assert_clean(prepared)


def test_import_and_metadata_normalization_need_no_linux_descriptor_capabilities(prepared):
    program = """
import json
import os
import sys
for name in ('O_PATH', 'O_DIRECTORY', 'O_NOFOLLOW', 'O_CLOEXEC', 'O_NONBLOCK'):
    if hasattr(os, name):
        delattr(os, name)
from eidolon_cli.organization_project_dependency_bundle import (
    normalize_prepared_dependency_manifest, copy_verified_dependency_bundle,
)
data = json.load(sys.stdin)
inputs = {'lockfile_bytes': bytes.fromhex(data['lockfile']),
          'package_manifests': {key: bytes.fromhex(value) for key, value in data['packages'].items()},
          'expected_toolchain': data['toolchain']}
receipt = normalize_prepared_dependency_manifest(data['manifest'], **inputs)
assert receipt['executionAuthorized'] is False
try:
    copy_verified_dependency_bundle('/must-not-open', '/must-not-create', data['manifest'], **inputs)
except ValueError as error:
    assert 'requires Linux O_PATH' in str(error)
else:
    raise AssertionError('Staging must fail without descriptor capabilities')
print(receipt['bundleSha256'])
"""
    data = {'manifest': prepared['manifest'], 'lockfile': prepared['inputs']['lockfile_bytes'].hex(),
            'packages': {key: value.hex() for key, value in prepared['inputs']['package_manifests'].items()},
            'toolchain': prepared['inputs']['expected_toolchain']}
    result = subprocess.run([sys.executable, '-c', program], input=raw(data), capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr.decode('utf-8', 'replace')
    assert result.stdout.decode().strip() == normalize(prepared)['bundleSha256']
