"""Capture exact reviewed renderer build inputs without discovery or execution.

This additive current-main fixture leaves historical renderer test fixtures intact.
It covers tracked desktop/shared sources, binary assets, configuration, HTML,
public assets and reviewed local literal-import closure. Review notes state the
remaining dynamic/dependency boundaries; they grant no runtime authority.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import time

from eidolon_cli.organization_project_snapshot_bundle import (
    ProjectSnapshotError, ProjectSnapshotLimits, capture_project_snapshot,
)


_BASELINE = 'd825aebce477d79977a1d1c6a62c8a078c5ce502'
_FIXTURE_PATH = (Path(__file__).resolve().parents[1] / 'fixtures'
                 / 'prepared-workspace-renderer-build-d825aebc.json')
_SHA256 = re.compile(r'[0-9a-f]{64}')
_LIMITS = ProjectSnapshotLimits(10_000, 32 * 1024 * 1024, 128 * 1024 * 1024)


def _digest(value):
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')
    return hashlib.sha256(encoded).hexdigest()


def _load_renderer_build_manifest():
    # Only adjacent checked-in test metadata is trusted, never workspace manifests.
    with _FIXTURE_PATH.open('rb') as stream:
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ProjectSnapshotError('Renderer build fixture manifest exceeds its byte bound')
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        raise ProjectSnapshotError('Renderer build fixture manifest must be valid JSON') from None
    if (type(value) is not dict or set(value) != {
            'version', 'baselineCommit', 'files', 'reviewedExecutionOnlyFiles',
            'sourceSha256', 'sourceSummary', 'reviewNotes', 'selectionSha256'}
            or type(value['version']) is not int or value['version'] != 1
            or value['baselineCommit'] != _BASELINE):
        raise ProjectSnapshotError('Renderer build fixture identity is invalid')
    for name, payload in (
        ('selectionSha256', {key: item for key, item in value.items() if key != 'selectionSha256'}),
        ('sourceSha256', value['files']),
    ):
        digest = value[name]
        if type(digest) is not str or not _SHA256.fullmatch(digest) or _digest(payload) != digest:
            raise ProjectSnapshotError('Renderer build fixture digest changed')
    files = value['files']
    if type(files) is not list or not 1 <= len(files) <= _LIMITS.max_files:
        raise ProjectSnapshotError('Renderer build fixture requires a bounded explicit file inventory')
    expected = {}
    for item in files:
        if (type(item) is not dict or set(item) != {'path', 'bytes', 'mode', 'sha256'}
                or type(item['path']) is not str or type(item['sha256']) is not str
                or not _SHA256.fullmatch(item['sha256']) or item['path'] in expected
                or type(item['bytes']) is not int or not 0 <= item['bytes'] <= _LIMITS.max_file_bytes
                or type(item['mode']) is not int or item['mode'] not in (0o444, 0o555)):
            raise ProjectSnapshotError('Renderer build fixture file identity is invalid')
        expected[item['path']] = item['sha256']
    if list(expected) != sorted(expected):
        raise ProjectSnapshotError('Renderer build fixture inventory must have canonical path order')
    reviewed = value['reviewedExecutionOnlyFiles']
    if type(reviewed) is not list or len(reviewed) > len(expected):
        raise ProjectSnapshotError('Renderer build fixture review policy is invalid')
    policy = {}
    for item in reviewed:
        if (type(item) is not dict or set(item) != {'path', 'sha256', 'reason'}
                or type(item['path']) is not str or type(item['sha256']) is not str
                or type(item['reason']) is not str or not 1 <= len(item['reason']) <= 1024
                or item['path'] in policy or item['path'] not in expected
                or item['sha256'] != expected[item['path']]):
            raise ProjectSnapshotError('Renderer build fixture reviewed policy must bind exact selected bytes')
        policy[item['path']] = item['sha256']
    if list(policy) != sorted(policy):
        raise ProjectSnapshotError('Renderer build fixture policy must have canonical path order')
    summary = value['sourceSummary']
    if (type(summary) is not dict or set(summary) != {
            'fileCount', 'totalBytes', 'largestFileBytes', 'nonUtf8FileCount', 'reviewedExecutionOnlyFileCount'}
            or any(type(item) is not int for item in summary.values())
            or summary['fileCount'] != len(files)
            or summary['totalBytes'] != sum(item['bytes'] for item in files)
            or summary['totalBytes'] > _LIMITS.max_total_bytes
            or summary['largestFileBytes'] != max(item['bytes'] for item in files)
            or not 0 <= summary['nonUtf8FileCount'] <= len(files)
            or summary['reviewedExecutionOnlyFileCount'] != len(policy)):
        raise ProjectSnapshotError('Renderer build fixture source summary is invalid')
    notes = value['reviewNotes']
    if (type(notes) is not list or not 1 <= len(notes) <= 20
            or any(type(note) is not str or not 1 <= len(note) <= 2048 for note in notes)):
        raise ProjectSnapshotError('Renderer build fixture requires bounded review scope notes')
    return value, expected, policy


def _load_renderer_build_selection():
    """Return exact hashes for the selected source and reviewed exception policy."""
    _, expected, policy = _load_renderer_build_manifest()
    return expected, policy


def capture_renderer_build_source(repository, execution_only_hashes, check_authorized):
    """Return inert exact bytes for the current-main renderer build fixture.

    The caller must authorize this root, complete selection and full reviewed
    execution-only policy with current revocation checks. No git, recursive scan,
    installation, build or project code is executed. ``sourceSha256`` hashes the
    canonical inventory independently of host root identity; the returned bundle
    additionally binds its actual root device/inode and execution-only markers.
    """
    deadline = time.monotonic() + 299
    if not callable(check_authorized):
        raise ProjectSnapshotError('Renderer build fixture capture requires a current authority checker')

    def current():
        if time.monotonic() >= deadline:
            raise ProjectSnapshotError('Renderer build fixture capture deadline exceeded')
        if check_authorized() is not True:
            raise ProjectSnapshotError('Renderer build fixture authority was revoked')
        if time.monotonic() >= deadline:
            raise ProjectSnapshotError('Renderer build fixture capture deadline exceeded')
        return True

    current()
    value, expected, reviewed = _load_renderer_build_manifest()
    if type(execution_only_hashes) is not dict or execution_only_hashes != reviewed:
        raise ProjectSnapshotError('Renderer build fixture requires its exact reviewed execution-only policy')
    current()
    repository = os.path.abspath(os.fspath(repository))
    info = os.stat(repository, follow_symlinks=False)
    snapshot = capture_project_snapshot(
        repository, list(expected), root_alias='root0',
        expected_root_identity=(info.st_dev, info.st_ino), check_authorized=current,
        limits=_LIMITS, deadline_monotonic=deadline,
        reviewed_execution_only_hashes=reviewed,
    )
    actual, non_utf8 = [], 0
    for item in snapshot.files:
        current()
        actual.append({'path': item.path, 'bytes': len(item.content), 'mode': item.mode, 'sha256': item.sha256})
        try:
            item.content.decode('utf-8')
        except UnicodeError:
            non_utf8 += 1
    if actual != value['files'] or _digest(actual) != value['sourceSha256']:
        raise ProjectSnapshotError('Renderer build fixture source differs from the reviewed baseline bytes or modes')
    if non_utf8 != value['sourceSummary']['nonUtf8FileCount']:
        raise ProjectSnapshotError('Renderer build fixture source summary differs from captured bytes')
    current()
    return snapshot
