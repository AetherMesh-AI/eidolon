"""Read-only prepared dependency freshness over real adversarial source trees."""
from copy import deepcopy
from dataclasses import replace
import os
from pathlib import Path
import shutil
import stat
import threading
import time

import pytest

from eidolon_cli import organization_project_dependency_bundle as bundle
from eidolon_cli import organization_project_dependency_verification as verification
from tests.eidolon_cli.test_organization_project_dependency_bundle import (
    add_dependency_file, add_locked_nested_package, change_file, normalize, prepared, raw,  # noqa: F401
)

pytestmark = pytest.mark.linux_only


def pin(prepared):
    root = prepared['source'].stat()
    prepared['ownerPins'] = {'expected_source_identity': (root.st_dev, root.st_ino),
                            'expected_bundle_sha256': normalize(prepared)['bundleSha256']}


@pytest.fixture(autouse=True)
def owner_pins(prepared):
    # Pins precede test mutations; malformed inputs must reach the new public
    # helper rather than fail in the test's own normalization wrapper.
    pin(prepared)


def verify(prepared, **changes):
    args = {**prepared['inputs'], **prepared['ownerPins']}
    args.update(changes)
    return verification.verify_dependency_bundle(prepared['source'], prepared['manifest'], **args)


def fingerprints(root):
    return {str(path.relative_to(root)): bundle._identity(path.lstat()) for path in [root, *root.rglob('*')]}


def test_read_only_verification_matches_copy_identity_without_writes_or_byte_retention(prepared, monkeypatch):
    before = fingerprints(prepared['source'])
    real_open = os.open
    def opening(path, flags, *args, **kwargs):
        assert not flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
        return real_open(path, flags, *args, **kwargs)
    def forbidden(*args, **kwargs):
        pytest.fail('Read-only verification attempted a filesystem write or copy')
    monkeypatch.setattr(os, 'open', opening)
    for name in ('mkdir', 'write', 'fchmod', 'rename', 'unlink'):
        monkeypatch.setattr(os, name, forbidden)
    monkeypatch.setattr(bundle, 'copy_verified_dependency_bundle', forbidden)
    expected = normalize(prepared)
    result = verify(prepared)
    assert {key: result[key] for key in ('bundleSha256', 'inventorySha256', 'lockfileSha256')} == {
        key: expected[key] for key in ('bundleSha256', 'inventorySha256', 'lockfileSha256')}
    assert result['fileCount'] == len(prepared['manifest']['files'])
    assert result['totalBytes'] == sum(item['bytes'] for item in prepared['manifest']['files'])
    assert result['verificationScope'] == 'observed-at-check'
    assert result['writesPerformed'] is result['executionAuthorized'] is result['provenanceAuthenticated'] is False
    assert result['runtimeCompatibilityVerified'] is False
    assert not {'manifest', 'files', 'content', 'packageManifests'} & result.keys()
    assert fingerprints(prepared['source']) == before
    assert not prepared['destination'].exists()


def test_large_payload_uses_bounded_stream_reads_and_no_copy(prepared, monkeypatch):
    add_dependency_file(prepared, 'node_modules/checker/dist/large.js', b'x' * (8 * 1024**2))
    pin(prepared)
    original = os.read
    reads = []
    def read(fd, count):
        assert 0 < count <= 65536
        reads.append(count)
        return original(fd, count)
    monkeypatch.setattr(os, 'read', read)
    result = verify(prepared)
    assert result['totalBytes'] > 8 * 1024**2 and len(reads) > 128
    assert not prepared['destination'].exists()


def test_current_root_inode_alone_cannot_hide_same_inode_changed_bytes(prepared):
    target = prepared['source'] / 'node_modules/checker/index.js'
    root_identity = prepared['source'].stat()
    file_identity = target.stat()
    target.write_bytes(b'x' * file_identity.st_size)
    assert target.stat().st_ino == file_identity.st_ino
    assert prepared['source'].stat().st_ino == root_identity.st_ino
    with pytest.raises(ValueError, match='bytes or identity'):
        verify(prepared)


@pytest.mark.parametrize('change', ['omitted', 'additional-file', 'additional-empty-directory', 'mode', 'truncated', 'grown'])
def test_exact_source_tree_rejects_omissions_additions_and_metadata_changes(prepared, change):
    target = prepared['source'] / 'node_modules/checker/index.js'
    if change == 'omitted':
        target.unlink()
    elif change == 'additional-file':
        target.with_name('extra.js').write_bytes(b'not declared')
    elif change == 'additional-empty-directory':
        target.with_name('empty').mkdir()
    elif change == 'mode':
        target.chmod(0o600)
    elif change == 'truncated':
        target.write_bytes(b'')
    else:
        with target.open('ab') as stream:
            stream.write(b'x')
    with pytest.raises(ValueError):
        verify(prepared)


@pytest.mark.parametrize('change', ['file-symlink', 'directory-symlink', 'root-symlink', 'hardlink', 'fifo'])
def test_no_link_or_special_file_can_substitute_for_declared_regular_bytes(prepared, tmp_path, change):
    target = prepared['source'] / 'node_modules/checker/index.js'
    if change == 'file-symlink':
        target.unlink()
        target.symlink_to(tmp_path / 'outside')
    elif change == 'directory-symlink':
        directory = target.parent
        moved = tmp_path / 'moved'
        directory.rename(moved)
        directory.symlink_to(moved, target_is_directory=True)
    elif change == 'root-symlink':
        alias = tmp_path / 'alias'
        alias.symlink_to(prepared['source'], target_is_directory=True)
        prepared['source'] = alias
    elif change == 'hardlink':
        os.link(target, tmp_path / 'extra-hardlink')
    else:
        target.unlink()
        os.mkfifo(target)
    with pytest.raises((ValueError, OSError)):
        verify(prepared)


@pytest.mark.parametrize('when', ['during-read', 'after-file-hash'])
def test_same_size_source_mutation_during_verification_is_detected(prepared, monkeypatch, when):
    changed = False
    if when == 'during-read':
        original = os.read
        def read(fd, count):
            nonlocal changed
            data = original(fd, count)
            if data and not changed:
                changed = True
                path = Path(os.readlink('/proc/self/fd/' + str(fd)))
                path.write_bytes(b'x' * path.stat().st_size)
            return data
        monkeypatch.setattr(os, 'read', read)
    else:
        original = verification._verify_file
        def checked(parent, name, item, identity, check):
            nonlocal changed
            result = original(parent, name, item, identity, check)
            if not changed:
                changed = True
                path = prepared['source'] / item['path']
                path.write_bytes(b'x' * path.stat().st_size)
            return result
        monkeypatch.setattr(verification, '_verify_file', checked)
    with pytest.raises(ValueError, match='changed'):
        verify(prepared)
    assert changed


def test_file_path_substitution_after_descriptor_open_is_detected(prepared, monkeypatch):
    original = os.read
    changed = False
    def read(fd, count):
        nonlocal changed
        data = original(fd, count)
        if data and not changed:
            changed = True
            path = Path(os.readlink('/proc/self/fd/' + str(fd)))
            replacement = path.with_name('replacement')
            replacement.write_bytes(path.read_bytes())
            replacement.chmod(stat.S_IMODE(path.stat().st_mode))
            replacement.replace(path)
        return data
    monkeypatch.setattr(os, 'read', read)
    with pytest.raises(ValueError, match='identity changed'):
        verify(prepared)


@pytest.mark.parametrize('substitution', ['directory', 'root'])
def test_identical_replacement_path_does_not_inherit_opened_identity(prepared, tmp_path, monkeypatch, substitution):
    original = verification._verify_file
    changed = False
    def checked(*args):
        nonlocal changed
        result = original(*args)
        if not changed:
            changed = True
            path = prepared['source'] / 'node_modules/checker' if substitution == 'directory' else prepared['source']
            replacement = tmp_path / 'replacement'
            shutil.copytree(path, replacement)
            path.rename(tmp_path / 'moved')
            replacement.rename(path)
        return result
    monkeypatch.setattr(verification, '_verify_file', checked)
    with pytest.raises(ValueError, match='changed'):
        verify(prepared)


def test_fifo_raced_into_leaf_is_never_opened_for_read(prepared, monkeypatch):
    original = os.open
    changed = False
    def opening(path, flags, *args, **kwargs):
        nonlocal changed
        if path == 'index.js' and flags & os.O_PATH and not changed:
            changed = True
            target = prepared['source'] / 'node_modules/checker/index.js'
            target.unlink()
            os.mkfifo(target)
        if isinstance(path, str) and path.startswith('/proc/self/fd/'):
            assert stat.S_ISREG(os.fstat(int(path.rsplit('/', 1)[1])).st_mode)
        return original(path, flags, *args, **kwargs)
    monkeypatch.setattr(os, 'open', opening)
    with pytest.raises(ValueError, match='file type'):
        verify(prepared)
    assert changed


@pytest.mark.parametrize('when', ['before', 'read', 'final-walk', 'deadline'])
def test_cancellation_and_deadline_abort_without_creating_or_changing_files(prepared, monkeypatch, when):
    cancel = threading.Event()
    before = fingerprints(prepared['source'])
    if when == 'before':
        cancel.set()
    elif when == 'read':
        original = os.read
        def read(fd, count):
            data = original(fd, count)
            cancel.set()
            return data
        monkeypatch.setattr(os, 'read', read)
    elif when == 'final-walk':
        original = verification._walk
        def walk(*args, **kwargs):
            if not kwargs['hash_bytes']:
                cancel.set()
            return original(*args, **kwargs)
        monkeypatch.setattr(verification, '_walk', walk)
    with pytest.raises(ValueError, match='cancelled|deadline'):
        verify(prepared, cancel=cancel, deadline=time.monotonic() - 1 if when == 'deadline' else None)
    assert fingerprints(prepared['source']) == before
    assert not prepared['destination'].exists()


@pytest.mark.parametrize('value', [None, False, 1, 'yes'])
def test_authorization_checkpoint_requires_exact_true(prepared, value):
    with pytest.raises(ValueError, match='authority was denied'):
        verify(prepared, check_authorized=lambda: value)


def test_live_authority_revocation_interrupts_streaming(prepared, monkeypatch):
    allowed = True
    original = os.read
    def read(fd, count):
        nonlocal allowed
        result = original(fd, count)
        allowed = False
        return result
    monkeypatch.setattr(os, 'read', read)
    with pytest.raises(ValueError, match='authority was denied'):
        verify(prepared, check_authorized=lambda: allowed)


@pytest.mark.parametrize('identity', [None, [], [1, 2], (1,), (True, 2), (1, -1), (1, 2**64)])
def test_root_identity_is_required_and_exact(prepared, identity):
    with pytest.raises(ValueError, match='identity'):
        verify(prepared, expected_source_identity=identity)


@pytest.mark.parametrize('deadline', [True, 'later', float('inf'), float('nan')])
def test_deadline_must_be_finite_and_monotonic(prepared, deadline):
    with pytest.raises(ValueError, match='deadline'):
        verify(prepared, deadline=deadline)


def test_changed_root_inode_and_retained_bundle_hash_fail_before_reading_files(prepared, monkeypatch):
    def forbidden(*_):
        pytest.fail('Mismatched root/bundle reached file reading')
    monkeypatch.setattr(os, 'read', forbidden)
    with pytest.raises(ValueError, match='owner-approved identity'):
        verify(prepared, expected_source_identity=(0, 0))
    with pytest.raises(ValueError, match='independently retained identity'):
        verify(prepared, expected_bundle_sha256='0' * 64)


@pytest.mark.parametrize('field,value', [('max_files', 1), ('max_bytes', 1), ('max_file_bytes', 1),
    ('max_manifest_bytes', 8), ('max_lockfile_bytes', 8), ('max_package_manifests', 1),
    ('max_package_manifest_bytes', 8), ('max_depth', 1), ('max_path_bytes', 1)])
def test_fixed_explicit_inventory_and_input_bounds_are_preserved(prepared, field, value):
    with pytest.raises(ValueError):
        verify(prepared, limits=replace(bundle.DependencyBundleLimits(), **{field: value}))


@pytest.mark.parametrize('change', ['package-version', 'package-name', 'toolchain', 'lock', 'provenance'])
def test_matching_file_hashes_cannot_replace_exact_lock_package_and_provenance_rules(prepared, change):
    if change in {'package-version', 'package-name'}:
        data = {'name': 'checker', 'version': '1.2.3'}
        data['version' if change == 'package-version' else 'name'] = '9.9.9' if change == 'package-version' else 'impostor'
        change_file(prepared, 'node_modules/checker/package.json', raw(data))
        pin(prepared)
    elif change == 'toolchain':
        prepared['inputs']['expected_toolchain']['nodeVersion'] = '25.0.0'
    elif change == 'lock':
        prepared['inputs']['lockfile_bytes'] += b' '
    else:
        prepared['manifest']['provenance']['kind'] = 'authenticated-by-model'
    with pytest.raises(ValueError):
        verify(prepared)


def test_nested_install_and_opaque_distribution_payload_keep_existing_ownership_rules(prepared):
    nested = add_locked_nested_package(prepared)
    add_dependency_file(prepared, 'node_modules/checker/dist/node_modules/embedded/package.json', b'opaque payload')
    pin(prepared)
    result = verify(prepared)
    assert result['fileCount'] == len(prepared['manifest']['files'])
    change_file(prepared, nested + '/package.json', raw({'name': 'other', 'version': '2.3.4'}))
    pin(prepared)
    with pytest.raises(ValueError, match='package name'):
        verify(prepared)


def test_inputs_are_detached_from_manifest_mutation_during_authorization(prepared):
    expected = normalize(prepared)
    calls = 0
    def check():
        nonlocal calls
        calls += 1
        # Calls 1/2 surround normalization. After detachment, external manifest
        # mutation cannot change either the verified inventory or return identity.
        if calls == 2:
            prepared['manifest']['files'][0]['sha256'] = '0' * 64
        return True
    result = verify(prepared, check_authorized=check)
    assert result['bundleSha256'] == expected['bundleSha256']
    assert calls > 2


@pytest.mark.parametrize('abort', [False, True])
def test_all_opened_descriptors_close_on_success_and_read_exception(prepared, monkeypatch, abort):
    opened = set()
    original_open, original_close, original_read = os.open, os.close, os.read
    def opening(*args, **kwargs):
        fd = original_open(*args, **kwargs)
        opened.add(fd)
        return fd
    def closing(fd):
        original_close(fd)
        opened.discard(fd)
    def reading(fd, count):
        if abort:
            raise OSError('Injected read failure')
        return original_read(fd, count)
    monkeypatch.setattr(os, 'open', opening)
    monkeypatch.setattr(os, 'close', closing)
    monkeypatch.setattr(os, 'read', reading)
    if abort:
        with pytest.raises(OSError, match='Injected read failure'):
            verify(prepared)
    else:
        verify(prepared)
    assert not opened


def test_ancestor_symlink_substitution_between_opened_root_and_final_path_check_is_detected(prepared, tmp_path, monkeypatch):
    parent = tmp_path / 'real-parent'
    parent.mkdir()
    source = parent / 'dependencies'
    prepared['source'].rename(source)
    prepared['source'] = source
    pin(prepared)
    original = verification._verify_file
    changed = False
    def checked(*args):
        nonlocal changed
        result = original(*args)
        if not changed:
            changed = True
            moved = tmp_path / 'moved-parent'
            parent.rename(moved)
            parent.symlink_to(moved, target_is_directory=True)
        return result
    monkeypatch.setattr(verification, '_verify_file', checked)
    with pytest.raises(OSError):
        verify(prepared)
    assert changed


def test_physical_workspace_symlink_is_rejected_even_when_declared_as_inert_mapping(prepared):
    link = prepared['source'] / 'node_modules/desktop'
    link.symlink_to('../../apps/desktop', target_is_directory=True)
    with pytest.raises(ValueError, match='undeclared'):
        verify(prepared)


def test_registry_provenance_stays_unverified_and_must_match_exact_lock_metadata(prepared):
    import json
    locked = json.loads(prepared['inputs']['lockfile_bytes'])['packages']['node_modules/checker']
    prepared['manifest']['provenance'].update(kind='registry-prepared', registryPackages=[
        {'path': 'node_modules/checker', 'resolved': locked['resolved'], 'integrity': locked['integrity']}])
    pin(prepared)
    assert verify(prepared)['provenanceAuthenticated'] is False
    prepared['manifest']['provenance']['registryPackages'][0]['resolved'] = 'https://registry.npmjs.org/other.tgz'
    with pytest.raises(ValueError):
        verify(prepared)


def test_raw_manifest_is_supported_but_independently_retained_digest_is_still_required(prepared):
    expected = deepcopy(prepared['ownerPins'])
    prepared['manifest'] = raw(prepared['manifest'])
    assert verify(prepared)['bundleSha256'] == expected['expected_bundle_sha256']
    with pytest.raises(ValueError):
        verify(prepared, expected_bundle_sha256=None)
