"""Actual prepared-workspace tests; absent opt-in is explicitly skipped."""
from dataclasses import replace
import json
import os
from pathlib import Path

import pytest

from eidolon_cli.organization_project_dependency_bundle import normalize_prepared_dependency_manifest
from eidolon_cli.organization_project_workspace_node import run_prepared_workspace_step

UI_TEST = 'apps/desktop/src/components/idle-mount.test.tsx'
PROFILE_REFRESH_TEST = 'apps/desktop/src/app/contrib/hooks/use-background-sync.test.tsx'
SAME_STEM_TESTS = (
    'apps/desktop/src/app/chat/sidebar/session-actions-menu.test',
    'apps/desktop/src/app/contrib/hooks/use-background-sync.test',
    'apps/desktop/src/app/settings/gateway-settings.test',
)


@pytest.fixture(scope='module')
def prepared():
    if os.environ.get('EIDOLON_REQUIRE_WORKSPACE_SANDBOX') != '1':
        pytest.skip('Actual prepared workspace isolation was not explicitly enabled')
    directory = os.environ.get('EIDOLON_TEST_WORKSPACE_BUNDLE')
    assert directory, 'Prepared workspace prerequisite is required, never silently skipped'
    base = Path(directory)
    manifest = json.loads((base / 'manifest.json').read_text())
    from tests.eidolon_cli.prepared_workspace_fixture import capture_renderer_source
    repository = Path(__file__).resolve().parents[2] / '.prepared-renderer-fixture'
    assert repository.is_dir(), 'Exact reviewed renderer fixture checkout is required'
    hashes = json.loads((base / 'execution-only-hashes.json').read_text())
    source = capture_renderer_source(repository, hashes, lambda: True)
    files = {item.path: item.content for item in source.files}
    checked = normalize_prepared_dependency_manifest(manifest,
        lockfile_bytes=files['package-lock.json'],
        package_manifests={item['path']: files[item['path']] for item in manifest['packageManifests']},
        expected_toolchain=manifest['toolchain'])
    profile = {'version': 1, 'id': 'renderer-validation', 'workspace': 'apps/desktop',
        'bundleSha256': checked['bundleSha256'],
        'steps': [
            {'id': 'renderer-types', 'recipe': 'workspace_typescript', 'checkKind': 'project_typecheck',
             'config': 'apps/desktop/tsconfig.json', 'testFiles': [],
             'sourceDirectories': ['apps/desktop/src', 'apps/shared/src']},
            {'id': 'ui-tests', 'recipe': 'workspace_vitest_ui', 'checkKind': 'project_tests',
             'config': 'apps/desktop/vitest.config.ts', 'testFiles': [UI_TEST], 'sourceDirectories': []}],
        'limits': {'wallSeconds': 180, 'memoryMiB': 4096, 'cpus': 1, 'pids': 64,
                   'scratchMiB': 256, 'logBytes': 131072}}
    assert len(source.files) > 64 and source.byte_count > 512 * 1024
    return source, base / 'bundle', manifest, profile


def execute(prepared, step, source=None):
    original, directory, manifest, profile = prepared
    return run_prepared_workspace_step(source or original, directory, manifest,
        manifest['toolchain'], profile, step, allow_local_docker=True,
        check_authorized=lambda: True)


def changed(source, path, suffix):
    files = tuple(replace(item, content=item.content + suffix) if item.path == path else item
                  for item in source.files)
    assert any(item.path == path for item in files)
    return replace(source, files=files)


def replaced(source, path, content):
    assert sum(item.path == path for item in source.files) == 1
    return replace(source, files=tuple(replace(item, content=content) if item.path == path else item
                                      for item in source.files))


def test_actual_compiler_includes_both_same_stem_test_roots(prepared):
    result = execute(prepared, 'renderer-types')
    assert result['status'] == 'passed', json.dumps({
        'status': result['status'], 'reason': result.get('reason'), 'stderr': result.get('stderr', '')[:8192]})
    assert result['cleanupConfirmed'] is True and result['isolation']['established'] is True
    assert result['snapshotSha256'] == prepared[0].snapshot_sha256
    outcome = result['outcome']
    assert outcome['toolEntry'] == 'node_modules/typescript/lib/typescript.js'
    assert outcome['evidenceOrigin'] == 'workload-claim'
    for stem in SAME_STEM_TESTS:
        for extension in ('.ts', '.tsx'):
            path = '/source/root0/' + stem + extension
            assert path in outcome['selectedFiles'] and path in outcome['reportedFiles']


@pytest.mark.parametrize('stem', SAME_STEM_TESTS)
def test_actual_compiler_rejects_type_error_in_glob_shadowed_sibling(prepared, stem):
    source = changed(prepared[0], stem + '.tsx',
                     b'\nconst __eidolon_hidden_type_failure: string = 123;\n')
    result = execute(prepared, 'renderer-types', source)
    assert result['status'] == 'failed', result
    assert result['cleanupConfirmed'] is True and result['isolation']['established'] is True
    assert result['snapshotSha256'] == source.snapshot_sha256 != prepared[0].snapshot_sha256
    assert type(result['failureClaim']['toolExitCode']) is int
    assert result['failureClaim']['toolExitCode'] != 0
    assert 'TS2322' in result['stderr'] and stem.removeprefix('apps/desktop/') + '.tsx' in result['stderr']
    assert 'outcome' not in result


def test_actual_compiler_rejects_project_references_without_narrowing_them(prepared):
    path = 'apps/desktop/tsconfig.json'
    original = next(item.content for item in prepared[0].files if item.path == path)
    configuration = json.loads(original)
    configuration['references'] = [{'path': '../shared'}]
    source = replaced(prepared[0], path, json.dumps(configuration).encode())
    result = execute(prepared, 'renderer-types', source)
    assert result['status'] == 'failed', result
    assert result['cleanupConfirmed'] is True and result['isolation']['established'] is True
    assert result['snapshotSha256'] == source.snapshot_sha256 != prepared[0].snapshot_sha256
    assert 'project references are unsupported' in result['stderr']
    assert 'outcome' not in result


def test_actual_renderer_typecheck_and_real_ui_share_source_and_bundle(prepared):
    typed = execute(prepared, 'renderer-types')
    assert typed['cleanupConfirmed'] is True, typed
    assert typed['status'] == 'passed', json.dumps({
        **{key: typed.get(key) for key in (
            'status', 'reason', 'cleanupConfirmed', 'exitCode', 'isolation', 'failureClaim')},
        'stderr': typed.get('stderr', '')[:8192],
    }, sort_keys=True)
    tested = execute(prepared, 'ui-tests')
    for receipt in (typed, tested):
        assert receipt['status'] == 'passed', receipt
        assert receipt['cleanupConfirmed'] is True
        assert receipt['isolation']['established'] is True
        assert receipt['sourceWritesPerformed'] is False
        assert receipt['snapshotSha256'] == prepared[0].snapshot_sha256
    for key in ('snapshotSha256', 'bundleSha256', 'profileSha256'):
        assert typed[key] == tested[key]
    assert typed['stepDefinitionSha256'] != tested['stepDefinitionSha256']
    assert typed['outcome']['testCount'] == 0
    assert typed['outcome']['sourceCount'] > 64
    assert tested['outcome']['testCount'] > 0
    assert tested['outcome']['reportedFiles'] == ['/source/root0/' + UI_TEST]


def test_actual_profile_and_backend_refresh_ui_fixture_passes(prepared):
    source, directory, manifest, profile = prepared
    selected_profile = {**profile, 'steps': [
        {**step, 'testFiles': [PROFILE_REFRESH_TEST]} if step['id'] == 'ui-tests' else step
        for step in profile['steps']]}
    result = execute((source, directory, manifest, selected_profile), 'ui-tests')
    assert result['status'] == 'passed', json.dumps({
        **{key: result.get(key) for key in (
            'status', 'reason', 'cleanupConfirmed', 'exitCode', 'isolation', 'failureClaim')},
        'stderr': result.get('stderr', '')[:8192],
    }, sort_keys=True)
    assert result['cleanupConfirmed'] is True and result['isolation']['established'] is True
    assert result['sourceWritesPerformed'] is False
    assert result['snapshotSha256'] == source.snapshot_sha256
    outcome = result['outcome']
    path = '/source/root0/' + PROFILE_REFRESH_TEST
    assert outcome['evidenceOrigin'] == 'workload-claim'
    assert outcome['selectedFiles'] == outcome['reportedFiles'] == [path]
    assert outcome['testCount'] == 2
    assert outcome['skippedCount'] == outcome['failedCount'] == 0
    assert outcome['modules'] == [
        {'path': path, 'passed': 2, 'skipped': 0, 'failed': 0, 'state': 'passed'}]


def test_actual_renderer_type_error_cannot_pass(prepared):
    source = changed(prepared[0], 'apps/desktop/src/main.tsx',
                     b'\nconst __eidolon_type_failure: string = 123;\n')
    result = execute(prepared, 'renderer-types', source)
    assert result['status'] == 'failed', result
    assert result['cleanupConfirmed'] is True
    assert result['isolation']['established'] is True
    assert result['snapshotSha256'] == source.snapshot_sha256
    assert result['snapshotSha256'] != prepared[0].snapshot_sha256
    assert type(result['failureClaim']['toolExitCode']) is int
    assert result['failureClaim']['toolExitCode'] != 0
    assert 'TS2322' in result['stderr']


def test_actual_ui_assertion_failure_cannot_pass(prepared):
    source = changed(prepared[0], UI_TEST,
                     b"\nit('prepared negative assertion', () => { expect(1).toBe(2) });\n")
    result = execute(prepared, 'ui-tests', source)
    assert result['status'] == 'failed', result
    assert result['cleanupConfirmed'] is True
    assert result['isolation']['established'] is True
    assert result['snapshotSha256'] == source.snapshot_sha256
    assert result['snapshotSha256'] != prepared[0].snapshot_sha256
    assert result['failureClaim']['failedCount'] >= 1


def test_actual_ui_runner_requires_authored_esm_filename_binding(prepared):
    path = 'apps/desktop/vite.config.ts'
    original = next(item.content for item in prepared[0].files if item.path == path)
    declaration = b'const __dirname = path.dirname(fileURLToPath(import.meta.url))\n'
    assert original.count(declaration) == 1, 'Reviewed fixture must include the exact ESM config correction'
    source = replaced(prepared[0], path, original.replace(declaration, b'', 1))
    result = execute(prepared, 'ui-tests', source)
    assert result['status'] == 'failed', result
    assert result['cleanupConfirmed'] is True and result['isolation']['established'] is True
    assert result['snapshotSha256'] == source.snapshot_sha256 != prepared[0].snapshot_sha256
    assert 'ReferenceError' in result['stderr'] and '__dirname is not defined' in result['stderr']
    assert 'outcome' not in result


def test_actual_ui_workload_cannot_write_source_signal_supervisor_or_use_network(prepared):
    probe = b'''
it('prepared sandbox boundaries', async () => {
  const fs = await import('node:fs');
  const net = await import('node:net');
  expect(() => fs.writeFileSync('/source/root0/apps/desktop/src/probe.txt', 'bad')).toThrow();
  expect(() => fs.readFileSync('/proc/1/environ')).toThrow();
  expect(() => process.kill(1, 'SIGUSR1')).toThrow();
  const blocked = await new Promise<boolean>((resolve) => {
    const socket = net.createConnection({ host: '1.1.1.1', port: 443 });
    socket.once('connect', () => { socket.destroy(); resolve(false) });
    socket.once('error', () => { socket.destroy(); resolve(true) });
    socket.setTimeout(1500, () => { socket.destroy(); resolve(true) });
  });
  expect(blocked).toBe(true);
});
'''
    source = changed(prepared[0], UI_TEST, probe)
    result = execute(prepared, 'ui-tests', source)
    assert result['status'] == 'passed', result
    assert result['cleanupConfirmed'] is True
    assert result['isolation']['network'] == 'none'


def test_actual_project_configuration_import_obeys_sandbox_boundaries(prepared):
    # Configuration executes in the harness before the Vitest worker. Probe
    # that separate untrusted-code entry point, not only the UI test process.
    probe = b'''
import configProbeAssert from 'node:assert/strict';
import configProbeFs from 'node:fs';
import configProbeNet from 'node:net';
configProbeAssert.equal(process.getuid(), 65534);
configProbeAssert.throws(() => configProbeFs.writeFileSync(
  '/source/root0/apps/desktop/src/config-probe.txt', 'bad'));
configProbeAssert.throws(() => configProbeFs.readFileSync('/proc/1/environ'));
configProbeAssert.throws(() => process.kill(1, 'SIGUSR1'));
const configProbeBlocked = await new Promise<boolean>((resolve) => {
  const socket = configProbeNet.createConnection({ host: '1.1.1.1', port: 443 });
  socket.once('connect', () => { socket.destroy(); resolve(false) });
  socket.once('error', () => { socket.destroy(); resolve(true) });
  socket.setTimeout(1500, () => { socket.destroy(); resolve(true) });
});
configProbeAssert.equal(configProbeBlocked, true);
process.stderr.write('CONFIG_IMPORT_BOUNDARY_PROBE_COMPLETE\\n');
'''
    source = changed(prepared[0], 'apps/desktop/vitest.config.ts', probe)
    result = execute(prepared, 'ui-tests', source)
    assert result['status'] == 'passed', result
    assert result['cleanupConfirmed'] is True
    assert result['isolation']['established'] is True
    assert result['isolation']['network'] == 'none'
    assert result['snapshotSha256'] == source.snapshot_sha256
    assert result['snapshotSha256'] != prepared[0].snapshot_sha256
    assert 'CONFIG_IMPORT_BOUNDARY_PROBE_COMPLETE' in result['stderr']


@pytest.mark.linux_only
def test_actual_running_workspace_observes_authority_revocation(prepared, monkeypatch):
    import threading
    import time
    import uuid
    from eidolon_cli import organization_project_workspace_node as runtime
    from tests.eidolon_cli.test_organization_project_workspace_abuse import attach_frames, process_identity
    from tests.eidolon_cli.test_organization_project_workspace_revocation import (
        exact_revocation_process, revocation_diagnostic,
    )
    original_docker = runtime._docker
    revoked = threading.Event()
    observed = threading.Event()
    stop_observing = threading.Event()
    observers = []
    evidence = {'topAttempts': 0, 'lastTopCode': None, 'lastTopForced': None, 'lastTopOutput': b'',
                'errors': [], 'pid': None, 'identity': None, 'callbackSawRevocation': False, 'attach': None}
    marker = 'eidolon-revoke-' + uuid.uuid4().hex[:12]

    def observe_workload(container, configuration):
        deadline = time.monotonic() + 90
        try:
            while not stop_observing.is_set() and time.monotonic() < deadline:
                code, output, _, forced = original_docker(
                    ['top', container, '-eo', 'pid,uid,args'], configuration,
                    runtime._Budget(None, 2, time.monotonic()), limit=65536)
                evidence.update(topAttempts=evidence['topAttempts'] + 1, lastTopCode=code,
                                lastTopForced=forced, lastTopOutput=output['stdout'])
                if code == 0 and forced is None:
                    match = exact_revocation_process(output['stdout'], marker, process_identity)
                    if match is not None:
                        evidence['pid'], evidence['identity'] = match
                        observed.set()
                        return
                stop_observing.wait(0.1)
        except Exception as error:
            evidence['errors'].append(type(error).__name__)
        finally:
            # Bound failure cleanup too, but never mistake this for a successful
            # observation: the assertion below requires the actual UID/title.
            revoked.set()

    def observed_docker(arguments, configuration, budget, limit=1048576):
        if arguments[:2] == ['start', '--attach']:
            observer = threading.Thread(target=observe_workload,
                                        args=(arguments[2], configuration), daemon=True)
            observers.append(observer)
            observer.start()
        answer = original_docker(arguments, configuration, budget, limit)
        if arguments[:2] == ['start', '--attach']:
            evidence['attach'] = answer
        return answer

    def authorized():
        if revoked.is_set() and observed.is_set():
            evidence['callbackSawRevocation'] = True
        return not revoked.is_set()

    monkeypatch.setattr(runtime, '_docker', observed_docker)
    source, directory, manifest, profile = prepared
    probe = ("\nimport revocationProcess from 'node:process';\n"
             "import { isMainThread as revocationMainThread } from 'node:worker_threads';\n"
             "import { readFileSync as revocationRead, writeSync as revocationWrite } from 'node:fs';\n"
             "import { it as revocationIt } from 'vitest';\n"
             "revocationWrite(2, 'EIDOLON_REVOCATION_REGISTERED_WORKLOAD_CLAIM\\n');\n"
             "revocationIt('prepared running revocation probe', async () => {\n"
             "  revocationWrite(2, 'EIDOLON_REVOCATION_BODY_STARTED_WORKLOAD_CLAIM\\n');\n"
             f"  process.title = '{marker}';\n"
             "  revocationWrite(2, 'EIDOLON_REVOCATION_TITLE_WORKLOAD_CLAIM ' + JSON.stringify({\n"
             "    isMainThread: revocationMainThread, sameProcess: process === revocationProcess,\n"
             "    pid: revocationProcess.pid, uid: revocationProcess.getuid(),\n"
             "    title: revocationProcess.title.slice(0, 128), globalTitle: String(process.title).slice(0, 128),\n"
             "    cmdline: revocationRead('/proc/self/cmdline', 'utf8').replaceAll('\\0', ' ').trim().slice(0, 256)\n"
             "  }) + '\\n');\n"
             "  await new Promise((resolve) => setTimeout(resolve, 120000));\n"
             "}, 150000);\n").encode('utf-8')
    source = changed(source, UI_TEST, probe)
    try:
        result = run_prepared_workspace_step(source, directory, manifest, manifest['toolchain'],
            profile, 'ui-tests', allow_local_docker=True, check_authorized=authorized)
        report = revocation_diagnostic(result, evidence)
        assert observers and observed.is_set(), report
        assert evidence['errors'] == [] and evidence['callbackSawRevocation'] is True, report
        assert revoked.is_set() and result['status'] == 'cancelled', report
        assert result['cleanupConfirmed'] is True and 'outcome' not in result, report
        assert result['snapshotSha256'] == source.snapshot_sha256
        assert evidence['attach'] is not None, report
        code, output, totals, forced = evidence['attach']
        assert code != 0 and forced == 'cancelled', report
        assert totals['stdout'] <= 1048576 and totals['stderr'] <= profile['limits']['logBytes'], report
        frames = attach_frames(output['stdout'], forced)
        assert frames and frames[0]['kind'] == 'isolation', report
        assert frames[0]['supervisorUid'] == 0 and frames[0]['workloadUid'] == 65534, report
        assert frames[0]['supervisorCapabilities'] == ['SETUID', 'SETGID'], report
        assert frames[0]['workloadCapabilities'] == [], report
        deadline = time.monotonic() + 5
        while process_identity(evidence['pid']) == evidence['identity']:
            assert time.monotonic() < deadline, 'Observed revoked workload survived exact container cleanup'
            time.sleep(0.05)
    finally:
        stop_observing.set()
        revoked.set()
        for observer in observers:
            observer.join(timeout=5)
            assert not observer.is_alive(), 'Bounded native process observer did not stop'
