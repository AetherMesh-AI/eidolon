"""Exact freshness matching of previously classified original source bytes."""
import hashlib
import os
import threading
import time

import pytest

from eidolon_cli import organization_project_snapshot_bundle as snapshots
from tests.eidolon_cli.test_organization_project_snapshot_bundle import source, write, capture  # noqa: F401

pytestmark = pytest.mark.linux_only


def recheck(source, expected, **changes):
    return snapshots._recheck_project_snapshot(source, expected,
        **{'check_authorized': lambda: True, 'deadline_monotonic': time.monotonic() + 60, **changes})


def test_exact_recheck_preserves_two_byte_passes_and_skips_only_reclassification(source, monkeypatch):
    write(source, 'src/a.txt', b'normal source\n')
    secret = b'OPENAI_API_KEY=' + b'sk-proj-' + b'A' * 60
    write(source, 'src/fixture.txt', secret)
    expected = capture(source, ['src/a.txt', 'src/fixture.txt'],
        reviewed_execution_only_hashes={'src/fixture.txt': hashlib.sha256(secret).hexdigest()})
    calls = []
    original = snapshots._read_bounded
    def read(*args, **kwargs):
        calls.append(kwargs['retain'])
        return original(*args, **kwargs)
    monkeypatch.setattr(snapshots, '_read_bounded', read)
    def classified(*args):
        raise AssertionError('Exact bytes were already classified')
    monkeypatch.setattr(snapshots, '_check_no_secrets', classified)
    assert recheck(source, expected) is True
    assert calls == [True, True, False, False]
    assert expected.files[0].execution_only is False and expected.files[1].execution_only is True
    assert expected.execution_authorized is False
    with pytest.raises(AssertionError, match='already classified'):
        capture(source, ['src/a.txt'])  # Public capture must still classify.


@pytest.mark.parametrize('kind', ['ordinary', 'new_secret', 'execution_only', 'mode', 'symlink', 'hardlink', 'fifo'])
def test_recheck_rejects_changed_bytes_or_file_identity_policy(source, kind):
    original = b'source value\n'
    path = write(source, 'a.txt', original)
    exceptions = {'a.txt': hashlib.sha256(original).hexdigest()} if kind == 'execution_only' else None
    expected = capture(source, ['a.txt'], reviewed_execution_only_hashes=exceptions)
    if kind in {'ordinary', 'execution_only'}:
        path.write_bytes(b'changed value\n')
    elif kind == 'new_secret':
        path.write_bytes(b'OPENAI_API_KEY=sk-proj-' + b'A' * 60)
    elif kind == 'mode':
        path.chmod(0o755)
    elif kind == 'hardlink':
        os.link(path, source / 'linked')
    else:
        path.unlink()
        if kind == 'symlink':
            write(source, 'other', original)
            path.symlink_to(source / 'other')
        else:
            os.mkfifo(path)
    with pytest.raises(ValueError):
        recheck(source, expected)


def test_recheck_detects_earlier_file_mutation_during_later_read(source, monkeypatch):
    first = write(source, 'a.txt', b'first')
    second = write(source, 'b.txt', b'second')
    expected = capture(source, ['a.txt', 'b.txt'])
    read = snapshots._read_bounded
    changed = False
    def racing(fd, *args, **kwargs):
        nonlocal changed
        if not changed and os.fstat(fd).st_ino == second.stat().st_ino:
            changed = True
            first.write_bytes(b'other')
        return read(fd, *args, **kwargs)
    monkeypatch.setattr(snapshots, '_read_bounded', racing)
    with pytest.raises(ValueError, match='changed'):
        recheck(source, expected)


@pytest.mark.parametrize('kind', ['cancel', 'deadline', 'authority', 'profile_prefix'])
def test_recheck_keeps_fresh_cancellation_deadline_authority_and_protected_paths(source, monkeypatch, kind):
    write(source, 'a.txt')
    expected = capture(source, ['a.txt'])
    options = {}
    if kind == 'cancel':
        event = threading.Event()
        event.set()
        options['cancel'] = event
    elif kind == 'deadline':
        options['deadline_monotonic'] = time.monotonic() - 1
    elif kind == 'authority':
        options['check_authorized'] = lambda: False
    else:
        monkeypatch.setattr(snapshots, '_profile_credential_prefixes', lambda: (tuple(str(source).split('/')[1:]),))
    with pytest.raises(ValueError):
        recheck(source, expected, **options)


def test_recheck_does_not_reduce_authorization_callback_count(source):
    write(source, 'nested/a.txt')
    calls = []
    def check():
        calls.append(True)
        return True
    expected = capture(source, ['nested/a.txt'], check_authorized=check)
    count = len(calls)
    calls.clear()
    assert recheck(source, expected, check_authorized=check) is True
    assert len(calls) == count
