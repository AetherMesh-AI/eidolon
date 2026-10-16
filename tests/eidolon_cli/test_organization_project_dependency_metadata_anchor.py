"""Metadata-only dependency anchors retain descriptor and mutation fences."""
from contextlib import contextmanager
import os
import stat
import threading

import pytest

from eidolon_cli import organization_project_dependency_bundle as bundle
from tests.eidolon_cli.test_organization_project_dependency_observation import observe, recheck
from tests.eidolon_cli.test_organization_project_dependency_verification import (
    owner_pins, prepared, verify,  # noqa: F401
)


pytestmark = pytest.mark.linux_only


def _forbid_read(*args, **kwargs):
    pytest.fail('Metadata-only dependency recheck opened or read file contents')


def _watch_leaf(monkeypatch):
    original = bundle._regular_anchor
    active = {}

    @contextmanager
    def watched(parent, name, item):
        with original(parent, name, item) as opened:
            if name == 'index.js':
                active['descriptor'] = opened[0]
            try:
                yield opened
            finally:
                active.pop('descriptor', None)

    monkeypatch.setattr(bundle, '_regular_anchor', watched)
    return active


def test_metadata_recheck_uses_one_anchor_without_read_open_or_bytes(prepared, monkeypatch, record_property):
    with observe(prepared) as observation:
        before = set(os.listdir('/proc/self/fd'))
        original_open, original_fstat = os.open, os.fstat
        original_stat, original_close = os.stat, os.close
        anchors = set()
        counts = {'open': 0, 'fstat': 0, 'stat': 0, 'close': 0}

        def opening(path, flags, *args, **kwargs):
            if isinstance(path, str) and path.startswith('/proc/self/fd/'):
                _forbid_read()
            descriptor = original_open(path, flags, *args, **kwargs)
            if flags & os.O_PATH:
                assert flags & os.O_NOFOLLOW and flags & os.O_CLOEXEC
                assert not flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)
                anchors.add(descriptor)
                counts['open'] += 1
            else:
                assert flags & os.O_DIRECTORY
            return descriptor

        def fstat(descriptor):
            if descriptor in anchors:
                counts['fstat'] += 1
            return original_fstat(descriptor)

        def named_stat(path, *args, **kwargs):
            result = original_stat(path, *args, **kwargs)
            if kwargs.get('dir_fd') is not None and stat.S_ISREG(result.st_mode):
                assert kwargs['follow_symlinks'] is False
                counts['stat'] += 1
            return result

        def close(descriptor):
            if descriptor in anchors:
                anchors.remove(descriptor)
                counts['close'] += 1
            return original_close(descriptor)

        with monkeypatch.context() as patch:
            patch.setattr(os, 'open', opening)
            patch.setattr(os, 'read', _forbid_read)
            patch.setattr(os, 'fstat', fstat)
            patch.setattr(os, 'stat', named_stat)
            patch.setattr(os, 'close', close)
            assert recheck(observation) is True
        files = len(prepared['manifest']['files'])
        assert counts == {'open': files, 'fstat': 2 * files, 'stat': files, 'close': files}
        record_property('synthetic_metadata_file_syscall_counts', counts)
        assert not anchors
        assert set(os.listdir('/proc/self/fd')) == before


@pytest.mark.parametrize('operation', ['verify', 'copy'])
def test_byte_verification_and_copy_still_reopen_regular_anchors(prepared, monkeypatch, operation):
    original_open, original_read = os.open, os.read
    read_descriptors = set()
    opened, read_bytes = [], []

    def opening(path, flags, *args, **kwargs):
        descriptor = original_open(path, flags, *args, **kwargs)
        if isinstance(path, str) and path.startswith('/proc/self/fd/'):
            assert not flags & os.O_PATH
            assert stat.S_ISREG(os.fstat(int(path.rsplit('/', 1)[1])).st_mode)
            read_descriptors.add(descriptor)
            opened.append(descriptor)
        return descriptor

    def reading(descriptor, maximum):
        assert descriptor in read_descriptors
        value = original_read(descriptor, maximum)
        read_bytes.append(len(value))
        return value

    monkeypatch.setattr(os, 'open', opening)
    monkeypatch.setattr(os, 'read', reading)
    if operation == 'verify':
        verify(prepared)
    else:
        bundle.copy_verified_dependency_bundle(prepared['source'], prepared['destination'],
            prepared['manifest'], **prepared['inputs'])
    assert len(opened) == len(prepared['manifest']['files'])
    assert sum(read_bytes) == sum(item['bytes'] for item in prepared['manifest']['files'])


@pytest.mark.parametrize('mutation', ['same-size', 'restored-mtime', 'restored-bytes-and-mtime',
                                     'mode', 'hardlink', 'replace', 'remove'])
def test_mutation_after_anchor_gate_during_checkpoint_is_rejected(prepared, tmp_path, monkeypatch, mutation):
    target = prepared['source'] / 'node_modules/checker/index.js'
    original, info = target.read_bytes(), target.stat()
    changed = False
    with observe(prepared) as observation:
        descriptors = set(os.listdir('/proc/self/fd'))
        active = _watch_leaf(monkeypatch)

        def authorized():
            nonlocal changed
            if 'descriptor' not in active or changed:
                return True
            changed = True
            if mutation in {'same-size', 'restored-mtime', 'restored-bytes-and-mtime'}:
                target.write_bytes(b'x' * len(original))
                if mutation == 'restored-bytes-and-mtime':
                    target.write_bytes(original)
                if mutation != 'same-size':
                    os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns))
                    assert target.stat().st_mtime_ns == info.st_mtime_ns
                assert target.stat().st_ino == info.st_ino
                assert target.stat().st_ctime_ns != info.st_ctime_ns
            elif mutation == 'mode':
                target.chmod(0o600)
            elif mutation == 'hardlink':
                os.link(target, tmp_path / 'other-link')
            elif mutation == 'replace':
                replacement = tmp_path / 'replacement'
                replacement.write_bytes(original)
                replacement.chmod(stat.S_IMODE(info.st_mode))
                replacement.replace(target)
            else:
                target.unlink()
            return True

        monkeypatch.setattr(os, 'read', _forbid_read)
        with pytest.raises((ValueError, OSError)):
            recheck(observation, check_authorized=authorized)
        assert changed
        assert not active
        assert set(os.listdir('/proc/self/fd')) == descriptors
        with pytest.raises(ValueError, match='already consumed'):
            recheck(observation)


@pytest.mark.parametrize('substitution', ['file', 'symlink'])
def test_named_path_swap_after_anchor_fstat_is_rejected(prepared, tmp_path, monkeypatch, substitution):
    target = prepared['source'] / 'node_modules/checker/index.js'
    original, info = target.read_bytes(), target.stat()
    replacement = tmp_path / 'replacement'
    replacement.write_bytes(original)
    replacement.chmod(stat.S_IMODE(info.st_mode))
    changed = False
    original_stat = os.stat
    with observe(prepared) as observation:
        descriptors = set(os.listdir('/proc/self/fd'))

        def named_stat(path, *args, **kwargs):
            nonlocal changed
            if path == 'index.js' and kwargs.get('dir_fd') is not None and not changed:
                assert kwargs['follow_symlinks'] is False
                changed = True
                if substitution == 'file':
                    replacement.replace(target)
                else:
                    target.unlink()
                    target.symlink_to(replacement)
            return original_stat(path, *args, **kwargs)

        monkeypatch.setattr(os, 'stat', named_stat)
        monkeypatch.setattr(os, 'read', _forbid_read)
        with pytest.raises(ValueError, match='changed after its bytes were verified'):
            recheck(observation)
        assert changed
        assert set(os.listdir('/proc/self/fd')) == descriptors


@pytest.mark.parametrize('substitution', ['fifo', 'directory', 'symlink', 'hardlink', 'mode'])
def test_invalid_leaf_raced_before_anchor_gate_never_gets_read_open(prepared, tmp_path, monkeypatch, substitution):
    target = prepared['source'] / 'node_modules/checker/index.js'
    original_open = os.open
    changed = False
    with observe(prepared) as observation:
        descriptors = set(os.listdir('/proc/self/fd'))

        def opening(path, flags, *args, **kwargs):
            nonlocal changed
            if isinstance(path, str) and path.startswith('/proc/self/fd/'):
                _forbid_read()
            if path == 'index.js' and flags & os.O_PATH and not changed:
                changed = True
                if substitution == 'mode':
                    target.chmod(0o600)
                elif substitution == 'hardlink':
                    os.link(target, tmp_path / 'other-link')
                else:
                    target.unlink()
                    if substitution == 'fifo':
                        os.mkfifo(target)
                    elif substitution == 'directory':
                        target.mkdir()
                    else:
                        target.symlink_to(tmp_path / 'outside')
            return original_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(os, 'open', opening)
        monkeypatch.setattr(os, 'read', _forbid_read)
        with pytest.raises(ValueError, match='file type, links, size or mode'):
            recheck(observation)
        assert changed
        assert set(os.listdir('/proc/self/fd')) == descriptors


@pytest.mark.parametrize('failure', ['revocation', 'raise', 'original-cancel', 'new-cancel'])
def test_checkpoint_failure_closes_active_anchor_and_consumes_observation(prepared, monkeypatch, failure):
    original_cancel, new_cancel = threading.Event(), threading.Event()
    changed = False
    with observe(prepared, cancel=original_cancel) as observation:
        descriptors = set(os.listdir('/proc/self/fd'))
        active = _watch_leaf(monkeypatch)

        def authorized():
            nonlocal changed
            if 'descriptor' not in active:
                return True
            changed = True
            if failure == 'raise':
                raise RuntimeError('Owner checkpoint failed')
            if failure == 'original-cancel':
                original_cancel.set()
            elif failure == 'new-cancel':
                new_cancel.set()
            return failure != 'revocation'

        with pytest.raises((ValueError, RuntimeError), match='authority|cancelled|checkpoint failed'):
            recheck(observation, check_authorized=authorized, cancel=new_cancel)
        assert changed
        assert not active
        assert set(os.listdir('/proc/self/fd')) == descriptors
        with pytest.raises(ValueError, match='already consumed'):
            recheck(observation)
