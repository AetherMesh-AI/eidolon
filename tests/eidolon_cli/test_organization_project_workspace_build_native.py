"""Actual locked renderer builds, never inferred from protocol or synthetic tests.

Only the selected UI test named below is covered by the companion lifecycle.
The complete renderer build uses the separately captured current-main source.
"""
import hashlib
import json
import os
from pathlib import Path
import re

import pytest

from eidolon_cli.organization_project_dependency_bundle import normalize_prepared_dependency_manifest
from eidolon_cli.organization_project_validation import canonical, digest
from eidolon_cli.organization_project_validation_profile import normalize_validation_profile
from eidolon_cli.organization_project_workspace_node import run_prepared_workspace_step
from tests.eidolon_cli.test_organization_project_workspace_native import changed, replaced

UI_TEST = 'apps/desktop/src/components/idle-mount.test.tsx'
BUILD_STEP = 'renderer-build'
BUILD_CONFIG = 'apps/desktop/vite.config.ts'


@pytest.fixture(scope='module')
def prepared_build():
    if os.environ.get('EIDOLON_REQUIRE_WORKSPACE_SANDBOX') != '1':
        pytest.skip('Actual prepared workspace isolation was not explicitly enabled')
    directory = os.environ.get('EIDOLON_TEST_WORKSPACE_BUNDLE')
    assert directory, 'Prepared workspace prerequisite is required, never silently skipped'
    from tests.eidolon_cli.prepared_workspace_build_fixture import (
        _load_renderer_build_manifest, capture_renderer_build_source,
    )
    repository = Path(__file__).resolve().parents[2] / '.prepared-renderer-build-fixture'
    assert repository.is_dir(), 'Exact reviewed current renderer build fixture checkout is required'
    selection, expected, execution_only = _load_renderer_build_manifest()
    source = capture_renderer_build_source(repository, execution_only, lambda: True)
    assert {item.path: item.sha256 for item in source.files} == expected
    assert len(source.files) == selection['sourceSummary']['fileCount']
    assert source.byte_count == selection['sourceSummary']['totalBytes']
    base = Path(directory)
    manifest = json.loads((base / 'manifest.json').read_text())
    files = {item.path: item.content for item in source.files}
    checked = normalize_prepared_dependency_manifest(manifest,
        lockfile_bytes=files['package-lock.json'],
        package_manifests={item['path']: files[item['path']] for item in manifest['packageManifests']},
        expected_toolchain=manifest['toolchain'])
    assert 'vite' in checked['manifest']['toolchain']['tools']
    profile = normalize_validation_profile({'version': 2, 'id': 'renderer-build-validation',
        'workspace': 'apps/desktop', 'bundleSha256': checked['bundleSha256'],
        'steps': [
            {'id': 'renderer-types', 'recipe': 'workspace_typescript', 'checkKind': 'project_typecheck',
             'config': 'apps/desktop/tsconfig.json', 'testFiles': [],
             'sourceDirectories': ['apps/desktop/src', 'apps/shared/src']},
            {'id': 'ui-tests', 'recipe': 'workspace_vitest_ui', 'checkKind': 'project_tests',
             'config': 'apps/desktop/vitest.config.ts', 'testFiles': [UI_TEST], 'sourceDirectories': []},
            {'id': BUILD_STEP, 'recipe': 'workspace_vite_build', 'checkKind': 'project_build',
             'config': BUILD_CONFIG, 'testFiles': [], 'sourceDirectories': []}],
        'limits': {'wallSeconds': 180, 'memoryMiB': 4096, 'cpus': 1, 'pids': 64,
                   'scratchMiB': 256, 'logBytes': 131072}})
    return source, base / 'bundle', manifest, profile


def execute_build(prepared, source=None):
    original, dependencies, manifest, profile = prepared
    return run_prepared_workspace_step(source or original, dependencies, manifest,
        manifest['toolchain'], profile, BUILD_STEP, allow_local_docker=True,
        check_authorized=lambda: True)


def assert_build_cleanup(receipt, source):
    assert receipt['cleanupConfirmed'] is True, receipt
    assert receipt['stagingCleanupConfirmed'] is True, receipt
    assert receipt['isolation']['established'] is True, receipt
    assert receipt['isolation']['network'] == 'none'
    assert receipt['sourceWritesPerformed'] is False
    assert receipt['snapshotSha256'] == source.snapshot_sha256


def assert_renderer_manifest(receipt, source):
    assert receipt['status'] == 'passed', json.dumps({
        **{key: receipt.get(key) for key in ('status', 'reason', 'exitCode', 'failureClaim')},
        'stderr': receipt.get('stderr', '')[:16384]}, sort_keys=True)
    assert_build_cleanup(receipt, source)
    outcome = receipt['outcome']
    assert outcome['toolEntry'] == 'node_modules/vite/dist/node/index.js'
    assert outcome['configuration'] == BUILD_CONFIG
    assert outcome['evidenceOrigin'] == 'workload-claim'
    assert outcome['outputBodiesRetained'] is False
    assert outcome['outputManifestVersion'] == 1
    assert outcome['sourceCount'] == outcome['testCount'] == outcome['failedCount'] == outcome['skippedCount'] == 0
    assert outcome['selectedFiles'] == outcome['reportedFiles'] == outcome['modules'] == []
    manifest = outcome['outputManifest']
    assert outcome['outputManifestSha256'] == digest(manifest)
    assert 1 < len(manifest) <= 1024
    assert sum(entry['bytes'] for entry in manifest) <= 128 * 1024**2
    assert len(canonical(manifest).encode('utf-8')) <= 256 * 1024
    assert [entry['path'] for entry in manifest] == sorted(
        {entry['path'] for entry in manifest}, key=lambda path: path.encode('utf-8'))
    for entry in manifest:
        assert set(entry) == {'path', 'bytes', 'sha256', 'kind'}
        assert type(entry['bytes']) is int and 0 <= entry['bytes'] <= 32 * 1024**2
        assert re.fullmatch('[0-9a-f]{64}', entry['sha256'])
        assert entry['kind'] in {'asset', 'chunk'}
    outputs = {entry['path']: entry for entry in manifest}
    assert outputs['index.html']['kind'] == 'asset' and outputs['index.html']['bytes'] > 0
    assert any(entry['kind'] == 'chunk' and entry['bytes'] > 0 for entry in manifest)
    public = [item for item in source.files if item.path.startswith('apps/desktop/public/')]
    assert public and any(b'\x00' in item.content for item in public)
    for item in public:
        relative = item.path.removeprefix('apps/desktop/public/')
        assert outputs[relative] == {'path': relative, 'bytes': len(item.content),
                                     'sha256': hashlib.sha256(item.content).hexdigest(), 'kind': 'asset'}
    # Authored plugin assets demonstrate that the real config/plugin chain ran.
    assert outputs['emojibase/en/data.json']['kind'] == 'asset'
    assert outputs['emojibase/en/data.json']['bytes'] > 0
    assert 'outputBodies' not in outcome
    return manifest


def test_actual_renderer_build_retains_only_bounded_binary_aware_metadata(prepared_build, record_property):
    result = execute_build(prepared_build)
    manifest = assert_renderer_manifest(result, prepared_build[0])
    record_property('build_source_sha256', result['snapshotSha256'])
    record_property('build_bundle_sha256', result['bundleSha256'])
    record_property('build_profile_sha256', result['profileSha256'])
    record_property('build_output_manifest_sha256', result['outcome']['outputManifestSha256'])
    record_property('build_output_file_count', len(manifest))
    record_property('build_output_bytes', sum(item['bytes'] for item in manifest))


@pytest.mark.parametrize('failure', ['broken-import', 'invalid-config'])
def test_actual_renderer_build_failure_cannot_retain_success(prepared_build, failure):
    original = prepared_build[0]
    if failure == 'broken-import':
        source = changed(original, 'apps/desktop/src/main.tsx',
                         b'\nimport "./__eidolon_missing_build_import__.tsx";\n')
        diagnostic = '__eidolon_missing_build_import__'
    else:
        source = changed(original, BUILD_CONFIG,
                         b'\nthrow new Error("EIDOLON_INVALID_BUILD_CONFIG");\n')
        diagnostic = 'EIDOLON_INVALID_BUILD_CONFIG'
    receipt = execute_build(prepared_build, source)
    assert receipt['status'] == 'failed', receipt
    assert_build_cleanup(receipt, source)
    assert source.snapshot_sha256 != original.snapshot_sha256
    assert diagnostic in receipt['stderr']
    assert 'outcome' not in receipt
    assert receipt['failureClaim']['evidenceOrigin'] == 'workload-claim'
    assert 'outputManifest' not in receipt['failureClaim']


@pytest.mark.parametrize('attack,operation,diagnostic', [
    ('symlink', "fs.symlinkSync(out + '/index.html', out + '/linked-output')", 'linked'),
    ('oversized-file', "fs.writeFileSync(out + '/oversized.bin', ''); fs.truncateSync(out + '/oversized.bin', 32 * 1024 * 1024 + 1)", 'excessive'),
    ('noncanonical-path', "fs.writeFileSync(out + '/bad\\\\output.bin', 'bad')", 'path'),
])
def test_actual_vite_output_collection_fails_closed(prepared_build, attack, operation, diagnostic):
    # Tiny entry avoids rebuilding the renderer for every collection-boundary
    # probe; this still executes the real inventoried Vite API in its sandbox.
    source = replaced(prepared_build[0], 'apps/desktop/index.html',
                      b'<html><script type="module">console.log("bounded output probe")</script></html>')
    configuration = f'''import fs from 'node:fs';
export default {{ plugins: [{{ name: 'eidolon-{attack}-probe',
  writeBundle() {{
    const out = '/tmp/vite-build/out';
    {operation};
    process.stderr.write('EIDOLON_OUTPUT_PROBE_COMPLETE\\n');
  }}
}}] }};
'''.encode()
    source = replaced(source, BUILD_CONFIG, configuration)
    receipt = execute_build(prepared_build, source)
    assert receipt['status'] == 'failed', receipt
    assert_build_cleanup(receipt, source)
    assert 'EIDOLON_OUTPUT_PROBE_COMPLETE' in receipt['stderr']
    assert diagnostic in receipt['stderr'].lower()
    assert 'outcome' not in receipt
    assert receipt['failureClaim']['evidenceOrigin'] == 'workload-claim'
    assert 'outputManifest' not in receipt['failureClaim']
