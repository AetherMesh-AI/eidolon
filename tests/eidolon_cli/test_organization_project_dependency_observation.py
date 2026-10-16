"""Private operation-scoped dependency observations over real filesystem trees."""
from copy import copy, deepcopy
import gc
import json
import os
import pickle
import shutil
import threading
import time
import weakref

import pytest

from eidolon_cli import organization_project_dependency_verification as verification
from tests.eidolon_cli.test_organization_project_dependency_verification import (
    owner_pins, prepared, verify,  # noqa: F401
)


pytestmark = pytest.mark.linux_only


def observe(prepared, **changes):
    arguments = {**prepared['inputs'], **prepared['ownerPins'], **changes}
    return verification._observe_dependency_bundle(prepared['source'], prepared['manifest'], **arguments)


def recheck(observation, **changes):
    return verification._recheck_dependency_observation(observation, **{'check_authorized': lambda: True, **changes})


def test_metadata_is_public_compatible_and_recheck_reads_no_bytes_or_writes(prepared, monkeypatch):
    expected = verify(prepared)
    with observe(prepared) as observation:
        assert observation.metadata() == expected
        original_open = os.open
        def opening(path, flags, *args, **kwargs):
            assert not flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
            return original_open(path, flags, *args, **kwargs)
        def forbidden(*args, **kwargs):
            pytest.fail('Metadata recheck attempted to read bytes or write')
        monkeypatch.setattr(os, 'open', opening)
        monkeypatch.setattr(os, 'read', forbidden)
        monkeypatch.setattr(verification, '_verify_file', forbidden)
        for name in ('write', 'mkdir', 'rename', 'unlink', 'fchmod'):
            monkeypatch.setattr(os, name, forbidden)
        assert recheck(observation) is True
    assert not prepared['destination'].exists()


def test_observation_detaches_policy_and_returns_only_fresh_nonauthoritative_metadata(prepared):
    with observe(prepared) as observation:
        expected = observation.metadata()
        assert expected['executionAuthorized'] is False
        assert not {'files', 'manifest', 'content', 'toolchain', 'source'} & expected.keys()
        altered = observation.metadata()
        altered['rootIdentity']['inode'] = 0
        altered['executionAuthorized'] = True
        prepared['manifest']['files'].clear()
        prepared['manifest']['toolchain']['nodeVersion'] = '99.0.0'
        prepared['inputs']['expected_toolchain'].clear()
        prepared['inputs']['package_manifests'].clear()
        prepared['ownerPins'].clear()
        assert observation.metadata() == expected
        with pytest.raises(AttributeError):
            observation.source = '/replacement'
        assert recheck(observation) is True


@pytest.mark.parametrize('kind', ['none', 'dict', 'receipt', 'object', 'unregistered'])
def test_fabricated_or_wire_value_is_never_an_observation(prepared, monkeypatch, kind):
    values = {'none': None, 'dict': {}, 'receipt': verify(prepared), 'object': object(),
              'unregistered': object.__new__(verification._DependencyObservation)}
    def forbidden(*args, **kwargs):
        pytest.fail('Forged observation reached filesystem traversal')
    monkeypatch.setattr(os, 'open', forbidden)
    with pytest.raises(ValueError, match='private capability|not active'):
        recheck(values[kind])


@pytest.mark.parametrize('attempt', [copy, deepcopy, pickle.dumps, json.dumps])
def test_capability_cannot_be_copied_or_serialized(prepared, attempt):
    with observe(prepared) as observation:
        with pytest.raises(TypeError):
            attempt(observation)
        assert recheck(observation) is True


def test_constructor_rejects_and_expired_or_consumed_capabilities_cannot_be_reused(prepared):
    with pytest.raises(TypeError, match='scoped observer'):
        verification._DependencyObservation()
    with observe(prepared) as expired:
        pass
    with pytest.raises(ValueError, match='not active'):
        expired.metadata()
    with pytest.raises(ValueError, match='not active'):
        recheck(expired)
    with observe(prepared) as used:
        assert recheck(used) is True
        with pytest.raises(ValueError, match='already consumed'):
            recheck(used)
        with pytest.raises(ValueError, match='already consumed'):
            used.metadata()


@pytest.mark.parametrize('authority', [None, False, True, lambda: False, lambda: 1, lambda: None])
def test_recheck_requires_fresh_exact_authority_and_failure_consumes_token(prepared, authority):
    with observe(prepared) as observation:
        with pytest.raises(ValueError, match='checkpoint|authority'):
            recheck(observation, check_authorized=authority)
        with pytest.raises(ValueError, match='already consumed'):
            recheck(observation)


def test_authority_revoked_during_walk_consumes_token_and_closes_descriptors(prepared, monkeypatch):
    with observe(prepared) as observation:
        before = set(os.listdir('/proc/self/fd'))
        calls = 0
        def authorized():
            nonlocal calls
            calls += 1
            return calls < 8
        with pytest.raises(ValueError, match='authority was denied'):
            recheck(observation, check_authorized=authorized)
        assert calls == 8
        assert set(os.listdir('/proc/self/fd')) == before


def _mutate_in_place(target, before, original, *, restore_bytes=False, restore_mtime=False):
    target.write_bytes(b'x' * len(original))
    if restore_bytes:
        target.write_bytes(original)
    if restore_mtime:
        os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
        assert target.stat().st_mtime_ns == before.st_mtime_ns
        assert target.stat().st_ctime_ns != before.st_ctime_ns
    assert target.stat().st_ino == before.st_ino


def _replace_file(target, temporary, original, mode):
    temporary.write_bytes(original)
    temporary.chmod(mode & 0o777)
    os.replace(temporary, target)


def _swap_root(source, moved, *, symlink=False):
    source.rename(moved)
    if symlink:
        source.symlink_to(moved, target_is_directory=True)
    else:
        shutil.copytree(moved, source)


@pytest.mark.parametrize('mutation', ['same-size', 'restored-mtime', 'restored-bytes-and-mtime', 'grow', 'truncate',
                                     'mode', 'hardlink', 'replace', 'remove', 'add', 'empty-directory',
                                     'file-symlink', 'directory-symlink', 'fifo', 'root-swap', 'root-symlink'])
def test_post_observation_tree_mutations_cannot_reuse_verified_bytes(prepared, tmp_path, mutation):
    with observe(prepared) as observation:
        target = prepared['source'] / 'node_modules/checker/index.js'
        before, original = target.stat(), target.read_bytes()
        mutations = {
            'same-size': lambda: _mutate_in_place(target, before, original),
            'restored-mtime': lambda: _mutate_in_place(target, before, original, restore_mtime=True),
            'restored-bytes-and-mtime': lambda: _mutate_in_place(
                target, before, original, restore_bytes=True, restore_mtime=True),
            'grow': lambda: target.write_bytes(original + b'x'),
            'truncate': lambda: target.write_bytes(b''),
            'mode': lambda: target.chmod(0o600),
            'hardlink': lambda: os.link(target, tmp_path / 'other-link'),
            'replace': lambda: _replace_file(target, tmp_path / 'replacement', original, before.st_mode),
            'remove': target.unlink,
            'add': lambda: target.with_name('extra.js').write_bytes(b'extra'),
            'empty-directory': lambda: target.with_name('extra-directory').mkdir(),
            'file-symlink': lambda: (target.unlink(), target.symlink_to(tmp_path / 'outside')),
            'directory-symlink': lambda: (target.parent.rename(tmp_path / 'moved'),
                                         target.parent.symlink_to(tmp_path / 'moved', target_is_directory=True)),
            'fifo': lambda: (target.unlink(), os.mkfifo(target)),
            'root-swap': lambda: _swap_root(prepared['source'], tmp_path / 'old-root'),
            'root-symlink': lambda: _swap_root(prepared['source'], tmp_path / 'old-root', symlink=True),
        }
        mutations[mutation]()
        with pytest.raises((ValueError, OSError)):
            recheck(observation)
        with pytest.raises(ValueError, match='already consumed'):
            recheck(observation)


def test_original_absolute_source_cannot_be_redirected_by_changing_caller_path(prepared, tmp_path):
    with observe(prepared) as observation:
        old = prepared['source']
        prepared['source'] = tmp_path / 'irrelevant'
        old.joinpath('node_modules/checker/index.js').write_bytes(b'changed')
        with pytest.raises(ValueError):
            recheck(observation)


def test_absolute_ancestor_symlink_substitution_is_rejected(prepared, tmp_path):
    parent = tmp_path / 'parent'
    parent.mkdir()
    relocated = parent / 'dependencies'
    prepared['source'].rename(relocated)
    prepared['source'] = relocated
    with observe(prepared) as observation:
        parent.rename(tmp_path / 'moved-parent')
        parent.symlink_to(tmp_path / 'moved-parent', target_is_directory=True)
        with pytest.raises(OSError):
            recheck(observation)


@pytest.mark.parametrize('when', ['original-cancel', 'new-cancel', 'original-deadline', 'new-deadline', 'during-walk'])
def test_cancel_and_deadline_cannot_be_relaxed_and_failed_recheck_is_consumed(prepared, monkeypatch, when):
    event = threading.Event()
    initial = time.monotonic()
    with observe(prepared, cancel=event, deadline=initial + 60) as observation:
        extra = threading.Event()
        extra.set()
        calls = 0
        def authorized():
            nonlocal calls
            calls += 1
            if calls == 8:
                event.set()
            return True
        changes = {
            'original-cancel': {}, 'new-cancel': {'cancel': extra},
            'original-deadline': {'deadline': initial + 120},
            'new-deadline': {'deadline': initial - 1},
            'during-walk': {'check_authorized': authorized},
        }[when]
        if when == 'original-cancel':
            event.set()
        if when == 'original-deadline':
            monkeypatch.setattr(time, 'monotonic', lambda: initial + 61)
        with pytest.raises(ValueError, match='cancelled|deadline'):
            recheck(observation, **changes)
        with pytest.raises(ValueError, match='already consumed'):
            recheck(observation)


@pytest.mark.parametrize('deadline', [True, float('nan'), float('inf'), 'later'])
def test_invalid_recheck_deadline_consumes_observation(prepared, deadline):
    with observe(prepared) as observation:
        with pytest.raises(ValueError, match='finite monotonic'):
            recheck(observation, deadline=deadline)
        with pytest.raises(ValueError, match='already consumed'):
            recheck(observation)


def test_failed_and_abandoned_contexts_do_not_retain_capabilities(prepared):
    count = len(verification._OBSERVATIONS)
    with pytest.raises(RuntimeError, match='operation failed'):
        with observe(prepared) as failed:
            raise RuntimeError('operation failed')
    with pytest.raises(ValueError, match='not active'):
        recheck(failed)
    manager = observe(prepared)
    abandoned = manager.__enter__()
    reference = weakref.ref(abandoned)
    assert len(verification._OBSERVATIONS) == count + 1
    del manager, abandoned
    gc.collect()
    assert reference() is None
    assert len(verification._OBSERVATIONS) == count


def test_capture_failure_registers_no_capability(prepared):
    count = len(verification._OBSERVATIONS)
    with pytest.raises(ValueError, match='caller policy'):
        with observe(prepared, expected_toolchain={**prepared['inputs']['expected_toolchain'], 'nodeVersion': '99.0.0'}):
            pytest.fail('Invalid authority issued a capability')
    assert len(verification._OBSERVATIONS) == count
