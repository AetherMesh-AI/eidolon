"""Capture the reviewed public renderer fixture without discovery or execution.

The checked-in selection was generated in review from every tracked desktop/src
and shared/src file at the stated commit, plus package/configuration inputs and
their literal-import closure. The exact byte inventory includes binary assets.
It is test data, not a production allowlist or a claim of dynamic import closure.
Changing an input requires regenerating and reviewing this test fixture.
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


_BASELINE = 'b79325580a3373ad6f5fac0169e2df03eaeb97a0'
_FIXTURE_PATH = Path(__file__).resolve().parents[1] / 'fixtures' / 'prepared-workspace-renderer-b7932558.json'
_SHA256 = re.compile(r'[0-9a-f]{64}')
_LIMITS = ProjectSnapshotLimits(10_000, 32 * 1024 * 1024, 128 * 1024 * 1024)


def _load_renderer_selection():
    # This adjacent checked-in metadata is trusted test configuration, never a
    # workspace-provided manifest or runtime discovery input.
    with _FIXTURE_PATH.open('rb') as stream:
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ProjectSnapshotError('Renderer fixture manifest exceeds its byte bound')
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        raise ProjectSnapshotError('Renderer fixture manifest must be valid JSON') from None
    if (type(value) is not dict or set(value) != {
            'version', 'baselineCommit', 'files', 'reviewedExecutionOnlyFiles', 'selectionSha256'}
            or type(value['version']) is not int or value['version'] != 1
            or value['baselineCommit'] != _BASELINE):
        raise ProjectSnapshotError('Renderer fixture identity is invalid')
    digest = value['selectionSha256']
    payload = {key: item for key, item in value.items() if key != 'selectionSha256'}
    actual = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'),
                                       ensure_ascii=True).encode('ascii')).hexdigest()
    if type(digest) is not str or not _SHA256.fullmatch(digest) or actual != digest:
        raise ProjectSnapshotError('Renderer fixture selection digest changed')
    files = value['files']
    if type(files) is not list or not 1 <= len(files) <= _LIMITS.max_files:
        raise ProjectSnapshotError('Renderer fixture requires a bounded explicit file inventory')
    expected = {}
    for item in files:
        if (type(item) is not dict or set(item) != {'path', 'sha256'}
                or type(item['path']) is not str or type(item['sha256']) is not str
                or not _SHA256.fullmatch(item['sha256']) or item['path'] in expected):
            raise ProjectSnapshotError('Renderer fixture file identity is invalid')
        expected[item['path']] = item['sha256']
    if list(expected) != sorted(expected):
        raise ProjectSnapshotError('Renderer fixture inventory must have canonical path order')
    reviewed = value['reviewedExecutionOnlyFiles']
    if type(reviewed) is not list or len(reviewed) > len(expected):
        raise ProjectSnapshotError('Renderer fixture review policy is invalid')
    policy = {}
    for item in reviewed:
        if (type(item) is not dict or set(item) != {'path', 'sha256', 'reason'}
                or type(item['path']) is not str or type(item['sha256']) is not str
                or type(item['reason']) is not str or not 1 <= len(item['reason']) <= 1024
                or item['path'] in policy or item['path'] not in expected
                or item['sha256'] != expected[item['path']]):
            raise ProjectSnapshotError('Renderer fixture reviewed policy must bind exact selected bytes')
        policy[item['path']] = item['sha256']
    if list(policy) != sorted(policy):
        raise ProjectSnapshotError('Renderer fixture policy must have canonical path order')
    return expected, policy


def capture_renderer_source(repository, execution_only_hashes, check_authorized):
    """Return exact reviewed public bytes as an inert ``ProjectSnapshotBundle``.

    The caller must authorize the root, complete selection and exact reviewed
    execution-only policy, with current revocation checks. The supplied mapping
    must equal this fixture's full policy, never a subset or filename bypass.
    No git command, recursive scan, installation or project code is executed.
    The manifest contains no file contents; captured bytes stay in the backend.
    """
    deadline = time.monotonic() + 299
    if not callable(check_authorized):
        raise ProjectSnapshotError('Renderer fixture capture requires a current authority checker')

    def current():
        if time.monotonic() >= deadline:
            raise ProjectSnapshotError('Renderer fixture capture deadline exceeded')
        if check_authorized() is not True:
            raise ProjectSnapshotError('Renderer fixture authority was revoked')
        if time.monotonic() >= deadline:
            raise ProjectSnapshotError('Renderer fixture capture deadline exceeded')
        return True

    current()
    expected, reviewed = _load_renderer_selection()
    if type(execution_only_hashes) is not dict or execution_only_hashes != reviewed:
        raise ProjectSnapshotError('Renderer fixture requires its exact reviewed execution-only policy')
    current()
    repository = os.path.abspath(os.fspath(repository))
    info = os.stat(repository, follow_symlinks=False)
    snapshot = capture_project_snapshot(
        repository, list(expected), root_alias='root0',
        expected_root_identity=(info.st_dev, info.st_ino), check_authorized=current,
        limits=_LIMITS, deadline_monotonic=deadline,
        reviewed_execution_only_hashes=reviewed,
    )
    for item in snapshot.files:
        current()
        if item.sha256 != expected[item.path]:
            raise ProjectSnapshotError('Renderer fixture source differs from the reviewed baseline bytes')
    current()
    return snapshot
