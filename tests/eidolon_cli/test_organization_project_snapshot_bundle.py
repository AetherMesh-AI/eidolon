"""Real bounded source capture; no installation or project code execution."""
from dataclasses import FrozenInstanceError
import hashlib
import json
import os
from pathlib import Path
import threading
import time

import pytest

from eidolon_cli import organization_project_snapshot_bundle as snapshots
from eidolon_cli.organization_project_snapshot_bundle import (
    ProjectSnapshotBundle, ProjectSnapshotError, ProjectSnapshotFile,
    ProjectSnapshotLimits, capture_project_snapshot,
)


pytestmark = pytest.mark.linux_only


@pytest.fixture
def source(tmp_path):
    root = tmp_path / 'source'
    root.mkdir()
    return root.resolve()


def write(source, name, content=b'ordinary source\n'):
    path = source / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def capture(source, paths, **kwargs):
    info = source.stat()
    options = dict(root_alias='root0', expected_root_identity=(info.st_dev, info.st_ino),
                   check_authorized=lambda: True, deadline_monotonic=time.monotonic() + 60,
                   limits=ProjectSnapshotLimits(100, 1024 * 1024, 2 * 1024 * 1024))
    options.update(kwargs)
    return capture_project_snapshot(source, paths, **options)


def test_binary_workspace_snapshot_is_complete_immutable_and_canonical(source):
    binary = b'\x89PNG\r\n\x00\xff\x80' + bytes(range(256))
    write(source, 'apps/desktop/assets/icon.png', binary)
    write(source, 'apps/shared/empty.json', b'')
    executable = write(source, 'scripts/check.js', b'process.exit(0)\n')
    executable.chmod(0o755)
    paths = ['scripts/check.js', 'apps/shared/empty.json', 'apps/desktop/assets/icon.png']
    first = capture(source, paths)
    assert first == capture(source, list(reversed(paths)))
    assert [item.path for item in first.files] == sorted(paths)
    assert first.files[0].content == binary
    assert first.files[-1].mode == 0o555
    assert first.files[0].mode == 0o444
    assert first.byte_count == sum(len(item.content) for item in first.files)
    assert first.execution_authorized is False
    for item in first.files:
        assert item.sha256 == hashlib.sha256(item.content).hexdigest()
        assert item.manifest()['bytes'] == len(item.content)
    encoded = json.dumps(first.manifest(), sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')
    assert first.snapshot_sha256 == hashlib.sha256(encoded).hexdigest()
    assert all('content' not in entry for entry in first.manifest()['files'])
    assert 'content=' not in repr(first)
    public_manifest = first.manifest()
    public_manifest['files'][0]['sha256'] = 'forged'
    assert first.manifest()['files'][0]['sha256'] == first.files[0].sha256
    with pytest.raises(FrozenInstanceError):
        first.files[0].content = b'replacement'
    with pytest.raises(FrozenInstanceError):
        first.execution_authorized = True
    write(source, paths[-1], b'new asset')
    assert first.files[0].content == binary
    assert first.snapshot_sha256 != capture(source, paths).snapshot_sha256


def test_explicit_capacity_exceeds_legacy_contract_without_changing_it(source):
    paths = [f'app/src/file-{index:03}.bin' for index in range(80)]
    for index, path in enumerate(paths):
        write(source, path, bytes([index]) * 8192)
    bundle = capture(source, paths)
    assert len(bundle.files) == len(paths) > 64
    assert bundle.byte_count == 80 * 8192 > 524288
    assert sum(entry['bytes'] for entry in bundle.manifest()['files']) == bundle.byte_count


@pytest.mark.parametrize('paths', [
    [], ['a', 'a'], ['A', 'a'], ['src/a', 'SRC/b'], ['a', 'a/b'], ['a/b', 'A'],
    ['/absolute'], ['../escape'], ['a/../escape'], ['a//b'], ['a/./b'], ['a/'],
    ['a\\b'], ['a\x00b'], ['a\nb'], ['~home/file'], ['C:drive'], ['a?'], ['a*'],
    ['a[0]'], ['bad.'], ['bad '], ['CON.txt'], ['nested/NUL'], ['e\u0301.txt'],
    ['a' * 256], ['a/' * 32 + 'file'], ['\ud800'], ['a\u202eb'], ['a\x85b'],
])
def test_ambiguous_or_incomplete_selection_fails_before_any_source_read(source, monkeypatch, paths):
    def forbidden(*args, **kwargs):
        raise AssertionError('Invalid selection must not open source files')
    monkeypatch.setattr(snapshots, '_open_root', forbidden)
    with pytest.raises(ValueError):
        capture(source, paths)


@pytest.mark.parametrize('path', [
    '.git/config', 'NODE_MODULES/package/index.js', 'apps/desktop/dist/main.js',
    'build/output.js', 'coverage/index.html', '.cache/item', '.venv/bin/python',
    '.env', '.env.example', '.env.production', 'nested/.envrc', '.npmrc',
    'secrets.json', 'secrets/key', 'credentials.json', 'credentials.yaml', 'secret.toml', 'auth.json',
    '.ssh/config', '.aws/config', '.config/gcloud/config', 'cert.pem', 'key.key',
    'file.tsbuildinfo', '__pycache__/source.pyc', '.yarnrc.yml',
])
def test_default_excluded_selection_is_a_failure_never_a_silent_omission(source, monkeypatch, path):
    write(source, 'safe.txt')
    write(source, path)
    def forbidden(*args, **kwargs):
        raise AssertionError('Excluded selection must be rejected before source descriptors open')
    monkeypatch.setattr(snapshots, '_open_root', forbidden)
    with pytest.raises(ValueError):
        capture(source, ['safe.txt', path])


@pytest.mark.parametrize('attack', ['symlink', 'parent_symlink', 'hardlink', 'directory', 'fifo', 'socket', 'setuid'])
def test_unsafe_files_and_links_are_not_captured(source, tmp_path, attack):
    import socket
    outside = write(tmp_path, 'outside.txt')
    target = source / 'item'
    selection, server = ['item'], None
    if attack == 'symlink':
        target.symlink_to(outside)
    elif attack == 'parent_symlink':
        target.symlink_to(tmp_path, target_is_directory=True)
        selection = ['item/outside.txt']
    elif attack == 'hardlink':
        os.link(outside, target)
    elif attack == 'directory':
        target.mkdir()
    elif attack == 'fifo':
        os.mkfifo(target)
    elif attack == 'socket':
        try:
            server = socket.socket(socket.AF_UNIX)
        except PermissionError:
            pytest.skip('This executor forbids creating a Unix socket fixture')
        server.bind(str(target))
    else:
        write(source, 'item')
        target.chmod(0o4755)
    try:
        with pytest.raises(ProjectSnapshotError):
            capture(source, selection)
    finally:
        if server:
            server.close()


def test_root_identity_and_ancestor_links_are_enforced(source, tmp_path):
    write(source, 'selected.txt')
    identity = (source.stat().st_dev, source.stat().st_ino)
    with pytest.raises(ProjectSnapshotError, match='root identity'):
        capture(source, ['selected.txt'], expected_root_identity=(identity[0], identity[1] + 1))
    alias = tmp_path / 'alias'
    alias.symlink_to(source, target_is_directory=True)
    with pytest.raises(ProjectSnapshotError):
        capture(alias, ['selected.txt'])
    parent_alias = tmp_path / 'parent-alias'
    parent_alias.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ProjectSnapshotError):
        capture(parent_alias / 'source', ['selected.txt'])


def test_missing_selection_is_not_truncated_and_unselected_exclusions_are_not_read(source):
    write(source, '.env', b'SECRET=owner-secret')
    write(source, 'safe.txt')
    assert len(capture(source, ['safe.txt']).files) == 1
    with pytest.raises(ProjectSnapshotError):
        capture(source, ['safe.txt', 'missing.txt'])


@pytest.mark.parametrize('limits,paths', [
    (ProjectSnapshotLimits(1, 8, 16), ['a', 'b']),
    (ProjectSnapshotLimits(2, 7, 16), ['a']),
    (ProjectSnapshotLimits(2, 8, 12), ['a', 'b']),
    (ProjectSnapshotLimits(2, 8, 16, max_path_bytes=1), ['nested/a']),
    (ProjectSnapshotLimits(2, 8, 16, max_depth=1), ['nested/a']),
])
def test_owner_limits_fail_closed(source, limits, paths):
    for path in ('a', 'b', 'nested/a'):
        write(source, path, b'12345678')
    with pytest.raises(ProjectSnapshotError):
        capture(source, paths, limits=limits)


@pytest.mark.parametrize('field,value', [
    ('max_files', True), ('max_files', 0), ('max_files', snapshots.MAX_FILES + 1),
    ('max_file_bytes', -1), ('max_file_bytes', snapshots.MAX_FILE_BYTES + 1),
    ('max_total_bytes', snapshots.MAX_TOTAL_BYTES + 1), ('max_total_bytes', 1.5),
    ('max_depth', 33), ('max_path_bytes', 1025),
])
def test_limits_cannot_exceed_hard_ceiling_or_use_coercible_types(field, value):
    arguments = dict(max_files=100, max_file_bytes=100, max_total_bytes=100)
    arguments[field] = value
    with pytest.raises(ProjectSnapshotError):
        ProjectSnapshotLimits(**arguments)


@pytest.mark.parametrize('content', [
    b'const token = "ghp_' + b'a' * 36 + b'";',
    b'\x89PNG\x00\xff ghp_' + b'a' * 36 + b'\x00',
    b'-----BEGIN PRIVATE KEY-----\nnot-a-real-key\n-----END PRIVATE KEY-----',
])
def test_recognized_secrets_are_refused_in_text_and_binary_without_redacting_bytes(source, content):
    write(source, 'asset.bin', content)
    with pytest.raises(ProjectSnapshotError, match='secret-bearing'):
        capture(source, ['asset.bin'])
    assert (source / 'asset.bin').read_bytes() == content


def test_exact_reviewed_bytes_are_execution_only_and_manifest_exposes_only_metadata(source):
    content = b'const fixture = "ghp_' + b'a' * 36 + b'";'
    write(source, 'public-fixture.js', content)
    approved = {'public-fixture.js': hashlib.sha256(content).hexdigest()}
    with pytest.raises(ProjectSnapshotError, match='secret-bearing'):
        capture(source, ['public-fixture.js'])
    bundle = capture(source, ['public-fixture.js'], reviewed_execution_only_hashes=approved)
    assert bundle.files[0].content == content
    assert bundle.files[0].execution_only is True
    manifest = bundle.manifest()
    assert manifest['files'][0]['executionOnly'] is True
    assert manifest['files'][0]['sha256'] == approved['public-fixture.js']
    assert all('content' not in entry for entry in manifest['files'])
    assert content.decode() not in json.dumps(manifest)
    assert content.decode() not in repr(bundle)
    assert bundle.execution_authorized is False


def test_changed_execution_only_bytes_fail_even_after_becoming_clean(source):
    old = b'ghp_' + b'a' * 36
    approved = {'fixture.js': hashlib.sha256(old).hexdigest()}
    write(source, 'fixture.js', b'ordinary clean content')
    assert capture(source, ['fixture.js']).files[0].execution_only is False
    with pytest.raises(ProjectSnapshotError, match='execution-only source hash changed'):
        capture(source, ['fixture.js'], reviewed_execution_only_hashes=approved)


def test_execution_only_policy_changes_snapshot_identity_without_changing_byte_hash(source):
    content = b'ordinary fixture'
    write(source, 'fixture.js', content)
    regular = capture(source, ['fixture.js'])
    empty = capture(source, ['fixture.js'], reviewed_execution_only_hashes={})
    execution_only = capture(source, ['fixture.js'], reviewed_execution_only_hashes={
        'fixture.js': hashlib.sha256(content).hexdigest(),
    })
    assert regular.snapshot_sha256 == empty.snapshot_sha256
    assert execution_only.files[0].sha256 == regular.files[0].sha256
    assert execution_only.snapshot_sha256 != regular.snapshot_sha256
    assert execution_only.manifest()['files'][0]['executionOnly'] is True
    assert regular.manifest()['files'][0]['executionOnly'] is False


@pytest.mark.parametrize('approved', [
    [], {'unselected.js': '0' * 64}, {'fixture.js': 'A' * 64}, {'fixture.js': '0' * 63},
    {'fixture.js': True}, {1: '0' * 64}, {'../fixture.js': '0' * 64},
    {'.env': '0' * 64}, {'node_modules/pkg/index.js': '0' * 64}, {'fixture.js': None},
])
def test_invalid_execution_only_policy_fails_before_source_open(source, monkeypatch, approved):
    def forbidden(*args, **kwargs):
        raise AssertionError('Invalid reviewed hash policy must not open source files')
    monkeypatch.setattr(snapshots, '_open_root', forbidden)
    with pytest.raises(ValueError):
        capture(source, ['fixture.js'], reviewed_execution_only_hashes=approved)


def test_execution_only_hash_never_bypasses_protected_selection(source):
    content = b'ordinary bytes with a reviewed hash'
    write(source, '.env', content)
    with pytest.raises(ValueError):
        capture(source, ['.env'], reviewed_execution_only_hashes={'.env': hashlib.sha256(content).hexdigest()})


@pytest.mark.parametrize('state', [False, None, 1, 'true'])
def test_grant_checker_must_explicitly_confirm_current_authority(source, state):
    write(source, 'a')
    with pytest.raises(ProjectSnapshotError, match='revoked'):
        capture(source, ['a'], check_authorized=lambda: state)


@pytest.mark.parametrize('deadline', [float('inf'), float('nan'), True, -1, 'future', 10**500])
def test_invalid_absolute_deadline_fails_closed(source, deadline):
    with pytest.raises(ProjectSnapshotError, match='deadline'):
        capture(source, ['a'], deadline_monotonic=deadline)


def test_unbounded_capture_window_is_refused(source):
    with pytest.raises(ProjectSnapshotError, match='time bound'):
        capture(source, ['a'], deadline_monotonic=time.monotonic() + 3600)


@pytest.mark.parametrize('stop', ['cancel', 'revoke', 'deadline'])
def test_capture_stops_within_one_read_chunk(source, monkeypatch, stop):
    write(source, 'large.bin', b'ordinary bytes ' * 20_000)
    event, allowed, now, reads = threading.Event(), True, 1000.0, []
    original = os.read
    def interrupted_read(fd, amount):
        nonlocal allowed, now
        reads.append(amount)
        block = original(fd, amount)
        if stop == 'cancel':
            event.set()
        elif stop == 'revoke':
            allowed = False
        else:
            now = 1061.0
        return block
    monkeypatch.setattr(snapshots.time, 'monotonic', lambda: now)
    monkeypatch.setattr(os, 'read', interrupted_read)
    with pytest.raises(ProjectSnapshotError, match='cancelled|revoked|deadline'):
        capture(source, ['large.bin'], cancel=event, check_authorized=lambda: allowed)
    assert len(reads) == 1
    assert reads[0] <= 65536


@pytest.mark.parametrize('attack', ['write', 'grow', 'shrink', 'replace', 'parent_swap', 'root_swap', 'link'])
def test_mid_read_mutations_never_produce_a_snapshot(source, tmp_path, monkeypatch, attack):
    target = write(source, 'nested/file.bin', b'x' * 100_000)
    original, changed = os.read, False
    def raced_read(fd, amount):
        nonlocal changed
        block = original(fd, amount)
        if not changed:
            changed = True
            if attack == 'write':
                target.write_bytes(b'y' * 100_000)
            elif attack == 'grow':
                with target.open('ab') as stream:
                    stream.write(b'extra')
            elif attack == 'shrink':
                target.write_bytes(b'y')
            elif attack == 'replace':
                target.unlink()
                target.write_bytes(b'x' * 100_000)
            elif attack == 'parent_swap':
                (source / 'nested').rename(source / 'old-nested')
                (source / 'nested').symlink_to(source / 'old-nested', target_is_directory=True)
            elif attack == 'root_swap':
                source.rename(tmp_path / 'old-source')
                source.mkdir()
                write(source, 'nested/file.bin', b'x' * 100_000)
            else:
                os.link(target, tmp_path / 'new-alias')
        return block
    monkeypatch.setattr(os, 'read', raced_read)
    with pytest.raises(ProjectSnapshotError):
        capture(source, ['nested/file.bin'])
    assert changed


def test_earlier_file_change_is_caught_by_final_verification(source, monkeypatch):
    first = write(source, 'a.bin', b'first')
    second = write(source, 'b.bin', b'second')
    original, changed = os.read, False
    def raced_read(fd, amount):
        nonlocal changed
        block = original(fd, amount)
        if not changed and os.fstat(fd).st_ino == second.stat().st_ino:
            changed = True
            first.write_bytes(b'other')
        return block
    monkeypatch.setattr(os, 'read', raced_read)
    with pytest.raises(ProjectSnapshotError, match='changed'):
        capture(source, ['a.bin', 'b.bin'])


@pytest.mark.parametrize('replace_before_pin', [False, True])
def test_leaf_is_pinned_without_read_open_before_rejecting_a_raced_special_file(source, monkeypatch, replace_before_pin):
    target = write(source, 'file.bin', b'ordinary bytes')
    original, leaf_opens, descriptor_opens = os.open, [], []
    def opening(name, flags, *args, **kwargs):
        if name == 'file.bin':
            leaf_opens.append(flags)
            assert flags & os.O_PATH, 'A mutable source name must never receive a read-open'
            if replace_before_pin:
                target.unlink()
                os.mkfifo(target)
        if isinstance(name, str) and name.startswith('/proc/self/fd/'):
            descriptor_opens.append(name)
        return original(name, flags, *args, **kwargs)
    monkeypatch.setattr(os, 'open', opening)
    # This forwarding wrapper genuinely supports the original dir_fd API.
    monkeypatch.setattr(os, 'supports_dir_fd', os.supports_dir_fd | {opening})
    if replace_before_pin:
        with pytest.raises(ProjectSnapshotError, match='changed while opening'):
            capture(source, ['file.bin'])
        assert descriptor_opens == []
    else:
        assert capture(source, ['file.bin']).files[0].content == b'ordinary bytes'
        assert len(descriptor_opens) == 2  # Capture and exact verification pass.
    assert leaf_opens and all(flags & os.O_PATH for flags in leaf_opens)


def test_final_authority_check_happens_after_manifest_construction(source, monkeypatch):
    write(source, 'a')
    allowed, original = True, ProjectSnapshotBundle.__post_init__
    def revoke_on_seal(bundle):
        nonlocal allowed
        original(bundle)
        allowed = False
    monkeypatch.setattr(ProjectSnapshotBundle, '__post_init__', revoke_on_seal)
    with pytest.raises(ProjectSnapshotError, match='revoked'):
        capture(source, ['a'], check_authorized=lambda: allowed)


def test_cancellation_during_final_root_check_cannot_return_a_bundle(source, monkeypatch):
    write(source, 'a')
    sealed, event = False, threading.Event()
    original_seal, original_open = ProjectSnapshotBundle.__post_init__, snapshots._open_root
    def seal(bundle):
        nonlocal sealed
        original_seal(bundle)
        sealed = True
    def final_open(*args, **kwargs):
        fd = original_open(*args, **kwargs)
        if sealed:
            event.set()
        return fd
    monkeypatch.setattr(ProjectSnapshotBundle, '__post_init__', seal)
    monkeypatch.setattr(snapshots, '_open_root', final_open)
    with pytest.raises(ProjectSnapshotError, match='cancelled'):
        capture(source, ['a'], cancel=event)


def test_bundle_constructors_do_not_accept_mutable_or_forged_records(source):
    with pytest.raises(ProjectSnapshotError):
        ProjectSnapshotFile('a', bytearray(b'bytes'))
    with pytest.raises(ProjectSnapshotError):
        ProjectSnapshotFile('a', b'bytes', 0o777)
    write(source, 'a')
    bundle = capture(source, ['a'])
    with pytest.raises(ProjectSnapshotError):
        ProjectSnapshotBundle(bundle.root_alias, bundle.root_identity, list(bundle.files), bundle.limits)
    with pytest.raises(ProjectSnapshotError):
        ProjectSnapshotBundle(bundle.root_alias, bundle.root_identity, bundle.files * 2, bundle.limits)
    with pytest.raises(ProjectSnapshotError):
        ProjectSnapshotBundle(bundle.root_alias, bundle.root_identity, bundle.files,
                              ProjectSnapshotLimits(1, 1, 1))


def test_modes_and_root_identity_are_part_of_exact_snapshot_identity(source):
    path = write(source, 'a')
    plain = capture(source, ['a'])
    path.chmod(0o755)
    executable = capture(source, ['a'])
    assert executable.files[0].sha256 == plain.files[0].sha256
    assert executable.snapshot_sha256 != plain.snapshot_sha256
    renamed_alias = capture(source, ['a'], root_alias='root1')
    assert renamed_alias.snapshot_sha256 != executable.snapshot_sha256


def test_descriptor_cleanup_on_failed_and_successful_capture(source):
    write(source, 'a')
    before = len(os.listdir('/proc/self/fd'))
    for _ in range(10):
        capture(source, ['a'])
        with pytest.raises(ProjectSnapshotError):
            capture(source, ['missing'])
    assert len(os.listdir('/proc/self/fd')) == before


@pytest.fixture
def renderer_selection(source, tmp_path, monkeypatch):
    from tests.eidolon_cli import prepared_workspace_fixture as fixture
    contents = {
        'apps/desktop/src/main.tsx': b'export const renderer = 1;\n',
        'apps/desktop/src/sample.test.tsx': b"const fake = 'sk-" + b'1234567890' * 3 + b"';\n",
        'apps/shared/src/index.ts': b'export const shared = 2;\n',
    }
    for path, content in contents.items():
        write(source, path, content)
    reviewed = 'apps/desktop/src/sample.test.tsx'
    policy = {reviewed: hashlib.sha256(contents[reviewed]).hexdigest()}
    value = {
        'version': 1, 'baselineCommit': fixture._BASELINE,
        'files': [{'path': path, 'sha256': hashlib.sha256(content).hexdigest()}
                  for path, content in sorted(contents.items())],
        'reviewedExecutionOnlyFiles': [
            {'path': reviewed, 'sha256': policy[reviewed], 'reason': 'Synthetic test bytes only.'}],
    }
    path = tmp_path / 'renderer-selection.json'
    _write_renderer_selection(path, value)
    monkeypatch.setattr(fixture, '_FIXTURE_PATH', path)
    return fixture, contents, policy, path


def _write_renderer_selection(path, value):
    payload = {key: item for key, item in value.items() if key != 'selectionSha256'}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'),
                                       ensure_ascii=True).encode('ascii')).hexdigest()
    path.write_text(json.dumps({**payload, 'selectionSha256': digest}))


def test_renderer_fixture_captures_complete_exact_bytes_without_discovery(source, renderer_selection, monkeypatch):
    import subprocess
    fixture, contents, policy, _ = renderer_selection

    def no_discovery(*args, **kwargs):
        raise AssertionError('Fixture capture must not discover files or execute commands')

    monkeypatch.setattr(subprocess, 'run', no_discovery)
    monkeypatch.setattr(os, 'walk', no_discovery)
    monkeypatch.setattr(Path, 'rglob', no_discovery)
    bundle = fixture.capture_renderer_source(source, policy, lambda: True)
    assert {item.path: item.content for item in bundle.files} == contents
    assert {item.path for item in bundle.files if item.execution_only} == set(policy)
    assert bundle.execution_authorized is False
    assert 'content' not in json.dumps(bundle.manifest())
    assert bundle == fixture.capture_renderer_source(source, dict(policy), lambda: True)


def test_renderer_fixture_rejects_clean_source_changes(source, renderer_selection):
    fixture, _, policy, _ = renderer_selection
    write(source, 'apps/desktop/src/main.tsx', b'export const renderer = 999;\n')
    with pytest.raises(ProjectSnapshotError, match='baseline bytes'):
        fixture.capture_renderer_source(source, policy, lambda: True)


def test_renderer_fixture_never_omits_missing_selected_source(source, renderer_selection):
    fixture, _, policy, _ = renderer_selection
    (source / 'apps/shared/src/index.ts').unlink()
    with pytest.raises(ProjectSnapshotError, match='missing'):
        fixture.capture_renderer_source(source, policy, lambda: True)


@pytest.mark.parametrize('alteration', ['missing', 'extra', 'changed', 'malformed'])
def test_renderer_fixture_requires_exact_policy_before_source_stat(source, renderer_selection, monkeypatch, alteration):
    fixture, _, policy, _ = renderer_selection
    supplied = dict(policy)
    if alteration == 'missing':
        supplied.clear()
    elif alteration == 'extra':
        supplied['apps/shared/src/index.ts'] = '0' * 64
    elif alteration == 'changed':
        supplied[next(iter(supplied))] = '0' * 64
    else:
        supplied = list(supplied.items())

    def no_source_stat(*args, **kwargs):
        raise AssertionError('Rejected policy must not inspect source')

    monkeypatch.setattr(fixture.os, 'stat', no_source_stat)
    with pytest.raises(ProjectSnapshotError, match='exact reviewed'):
        fixture.capture_renderer_source(source, supplied, lambda: True)


def test_renderer_fixture_selection_identity_detects_manifest_tampering(source, renderer_selection):
    fixture, _, policy, path = renderer_selection
    value = json.loads(path.read_text())
    value['files'][0]['sha256'] = '0' * 64
    path.write_text(json.dumps(value))
    with pytest.raises(ProjectSnapshotError, match='selection digest'):
        fixture.capture_renderer_source(source, policy, lambda: True)


@pytest.mark.parametrize('alteration', ['baseline', 'duplicate', 'unordered', 'review_hash', 'review_extra', 'version'])
def test_renderer_fixture_rejects_malformed_identity_even_with_matching_manifest_digest(
        source, renderer_selection, alteration):
    fixture, _, policy, path = renderer_selection
    value = json.loads(path.read_text())
    if alteration == 'baseline':
        value['baselineCommit'] = '0' * 40
    elif alteration == 'duplicate':
        value['files'].append(value['files'][0])
    elif alteration == 'unordered':
        value['files'].reverse()
    elif alteration == 'review_hash':
        value['reviewedExecutionOnlyFiles'][0]['sha256'] = '0' * 64
    elif alteration == 'review_extra':
        value['reviewedExecutionOnlyFiles'][0]['path'] = 'not-selected.ts'
    else:
        value['version'] = True
    _write_renderer_selection(path, value)
    with pytest.raises(ProjectSnapshotError):
        fixture.capture_renderer_source(source, policy, lambda: True)


def test_renderer_fixture_checks_authority_after_capture(source, renderer_selection, monkeypatch):
    fixture, _, policy, _ = renderer_selection
    original = fixture.capture_project_snapshot
    authorized = True

    def capture_then_revoke(*args, **kwargs):
        nonlocal authorized
        result = original(*args, **kwargs)
        authorized = False
        return result

    monkeypatch.setattr(fixture, 'capture_project_snapshot', capture_then_revoke)
    with pytest.raises(ProjectSnapshotError, match='authority was revoked'):
        fixture.capture_renderer_source(source, policy, lambda: authorized)
