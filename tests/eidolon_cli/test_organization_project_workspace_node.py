"""Prepared runner boundary tests. No fake native success receipts."""
import time

import pytest

from eidolon_cli import organization_project_workspace_node as runtime
from eidolon_cli.organization_project_runner import _Budget, normalize_execution_grant, ProjectExecutionGrant
from eidolon_cli.organization_project_snapshot_bundle import (
    ProjectSnapshotBundle, ProjectSnapshotFile, ProjectSnapshotLimits,
)
def profile():
    return {'version': 1, 'id': 'desktop-ui', 'workspace': 'apps/desktop', 'bundleSha256': 'a' * 64,
            'steps': [{'id': 'renderer-types', 'recipe': 'workspace_typescript',
                       'checkKind': 'project_typecheck', 'config': 'apps/desktop/tsconfig.json', 'testFiles': [],
                       'sourceDirectories': ['apps/desktop/src', 'apps/shared/src']},
                      {'id': 'ui-tests', 'recipe': 'workspace_vitest_ui', 'checkKind': 'project_tests',
                       'config': 'apps/desktop/vitest.config.ts',
                       'testFiles': ['apps/desktop/src/example.test.tsx'], 'sourceDirectories': []}],
            'limits': {'wallSeconds': 300, 'memoryMiB': 2048, 'cpus': 2, 'pids': 64,
                       'scratchMiB': 256, 'logBytes': 65536}}



def source():
    files = {'apps/desktop/src/example.test.tsx': b'export const fixture = true;',
             'apps/desktop/src/main.tsx': b'export const value = 1;',
             'apps/desktop/tsconfig.json': b'{"include":["src"]}',
             'apps/desktop/vitest.config.ts': b'export default {};',
             'apps/shared/src/shared.ts': b'export const shared = 1;'}
    return ProjectSnapshotBundle('root0', (1, 2), tuple(ProjectSnapshotFile(path, data)
        for path, data in sorted(files.items())), ProjectSnapshotLimits(10, 1024, 4096))


def test_no_explicit_opt_in_means_no_filesystem_or_runtime(monkeypatch):
    monkeypatch.setattr(runtime, '_docker', lambda *args: pytest.fail('Docker must not be accessed'))
    result = runtime.run_prepared_workspace_step(None, '/unapproved', {}, {}, {}, 'test')
    assert result['status'] == 'blocked'
    assert 'containerName' not in result
    assert result['isolation']['established'] is False


@pytest.mark.parametrize('authority', [None, lambda: False, lambda: 1])
def test_current_authority_required_before_preparation(authority):
    result = runtime.run_prepared_workspace_step(None, '/unapproved', {}, {}, {}, 'test',
                                                allow_local_docker=True, check_authorized=authority)
    assert result['status'] == 'blocked'
    assert 'authority' in result['reason']
    assert 'containerName' not in result


def test_legacy_runner_contract_unchanged_and_profile_not_dispatched():
    assert normalize_execution_grant({}) == ProjectExecutionGrant()
    with pytest.raises(ValueError):
        normalize_execution_grant({'recipe': 'workspace_vitest_ui'})


def test_private_source_staging_preserves_binary_bytes_and_readonly(tmp_path):
    value = source()
    root = runtime._stage_source(tmp_path, value, _Budget(None, 5, time.monotonic()))
    assert root == tmp_path / 'source' / 'root0'
    for item in value.files:
        assert (root / item.path).read_bytes() == item.content
        assert (root / item.path).stat().st_mode & 0o777 == 0o444


def frame(recipe):
    step = next(step for step in profile()['steps'] if step['recipe'] == recipe)
    value = source()
    paths = (runtime._selected_sources(value, step) if recipe == 'workspace_typescript' else step['testFiles'])
    paths = ['/source/root0/' + path for path in paths]
    is_typecheck = recipe == 'workspace_typescript'
    return step, {'kind': 'result', 'status': 'passed', 'recipe': recipe,
                  'configuration': step['config'], 'evidenceOrigin': 'workload-claim', 'toolExitCode': 0,
                  'testCount': 0 if is_typecheck else 2, 'skippedCount': 0, 'failedCount': 0,
                  'sourceCount': len(paths) if is_typecheck else 0,
                  'selectedFiles': paths, 'reportedFiles': paths.copy(),
                  'toolEntry': 'node_modules/typescript/lib/typescript.js' if is_typecheck else 'node_modules/vitest/dist/node.js',
                  'modules': [] if is_typecheck else [{'path': paths[0], 'passed': 2, 'skipped': 0,
                                                       'failed': 0, 'state': 'passed'}]}


@pytest.mark.parametrize('recipe', ['workspace_typescript', 'workspace_vitest_ui'])
def test_exact_complete_workload_claim_parsing(recipe):
    step, result = frame(recipe)
    assert runtime._outcome(result, step, source(), 'apps/desktop')


@pytest.mark.parametrize('change', [
    {'status': 'failed'}, {'recipe': 'typescript_check'}, {'toolExitCode': True}, {'toolExitCode': 1},
    {'evidenceOrigin': 'trusted'}, {'configuration': 'another.json'}, {'failedCount': 1},
    {'selectedFiles': []}, {'reportedFiles': []}, {'sourceCount': True}, {'toolEntry': 'npm'},
    {'reportedFiles': ['/source/root0/other.ts']},
])
@pytest.mark.parametrize('recipe', ['workspace_typescript', 'workspace_vitest_ui'])
def test_forged_partial_wrong_scope_outcomes_rejected(recipe, change):
    step, result = frame(recipe)
    result.update(change)
    assert not runtime._outcome(result, step, source(), 'apps/desktop')


@pytest.mark.parametrize('change', [{'testCount': 0}, {'testCount': 3}, {'modules': []},
                                   {'modules': [{'path': 'bad'}]}, {'skippedCount': 1}])
def test_ui_counts_and_complete_modules_are_required(change):
    step, result = frame('workspace_vitest_ui')
    result.update(change)
    assert not runtime._outcome(result, step, source(), 'apps/desktop')


def test_typecheck_requires_all_selected_sources_and_no_unknown_paths():
    step, result = frame('workspace_typescript')
    result['reportedFiles'] = result['reportedFiles'][:-1]
    result['sourceCount'] -= 1
    assert not runtime._outcome(result, step, source(), 'apps/desktop')


def test_typecheck_never_claims_the_old_cli_entry_executed():
    step, result = frame('workspace_typescript')
    result['toolEntry'] = 'node_modules/typescript/bin/tsc'
    assert not runtime._outcome(result, step, source(), 'apps/desktop')
    checked = {'manifest': {'files': [{'path': result['toolEntry']}],
                           'toolchain': {'tools': {'typescript': '6.0.3'}}}}
    with pytest.raises(ValueError, match='entry point'):
        runtime._tool_entry(step, checked)


def test_assembly_only_creates_declared_internal_workspace_links(tmp_path):
    root = tmp_path / 'source'
    prepared = tmp_path / 'prepared'
    (root / 'apps/shared').mkdir(parents=True)
    (root / 'apps/shared/package.json').write_text('{}')
    path = 'node_modules/tool/index.js'
    (prepared / path).parent.mkdir(parents=True)
    (prepared / path).write_text('fixture')
    (prepared / path).chmod(0o444)
    manifest = {'files': [{'path': path}],
                'workspaceLinks': [{'path': 'node_modules/shared', 'target': 'apps/shared'}]}
    runtime._assemble_dependencies(root, prepared, manifest, _Budget(None, 5, time.monotonic()))
    assert (root / path).read_text() == 'fixture'
    assert (root / 'node_modules/shared').resolve() == root / 'apps/shared'
    assert not (prepared / path).exists()
    runtime._cleanup_tree(root)


def test_invalid_source_object_and_unknown_step_fail_closed():
    data = profile()
    data['limits'].update(cpus=1)
    result = runtime.run_prepared_workspace_step(source(), '/not-read', {}, {}, data, 'missing',
            allow_local_docker=True, check_authorized=lambda: True)
    assert result['status'] == 'blocked'
    assert 'step' in result['reason']
    result = runtime.run_prepared_workspace_step(object(), '/not-read', {}, {}, data, 'missing',
            allow_local_docker=True, check_authorized=lambda: True)
    assert result['status'] == 'blocked'


@pytest.mark.parametrize('value', [False, None, 1, 'true'])
def test_authority_polling_latches_revocation(value):
    state = [True]
    signal = runtime._AuthorityCancellation(None, lambda: state[0])
    assert not signal.is_set()
    state[0] = value
    assert signal.is_set()
    state[0] = True
    assert signal.is_set()


def test_authority_read_error_cancels_without_escaping_poll():
    def failed():
        raise OSError('authority unavailable')
    assert runtime._AuthorityCancellation(None, failed).is_set()


def test_dependency_staging_uses_detached_checked_identity(monkeypatch, tmp_path):
    original = {'toolchain': {'nodeVersion': 'reviewed'}}
    import copy
    checked = {'manifest': copy.deepcopy(original), 'bundleSha256': 'a',
               'inventorySha256': 'b', 'lockfileSha256': 'c'}
    original['toolchain']['nodeVersion'] = 'mutated'
    calls = []
    def stage(directory, destination, manifest, **kwargs):
        calls.append((manifest, kwargs))
        return {key: checked[key] for key in ('bundleSha256', 'inventorySha256', 'lockfileSha256')}
    monkeypatch.setattr(runtime, 'copy_verified_dependency_bundle', stage)
    budget = _Budget(None, 5, time.monotonic())
    runtime._stage_dependencies('input', tmp_path / 'stage', checked, b'lock', {}, budget)
    assert calls[0][0]['toolchain']['nodeVersion'] == 'reviewed'
    assert calls[0][1]['expected_toolchain'] == {'nodeVersion': 'reviewed'}


@pytest.mark.parametrize('field', ['bundleSha256', 'inventorySha256', 'lockfileSha256'])
def test_staging_identity_drift_is_rejected(monkeypatch, tmp_path, field):
    checked = {'manifest': {'toolchain': {}}, 'bundleSha256': 'a',
               'inventorySha256': 'b', 'lockfileSha256': 'c'}
    receipt = dict(checked)
    receipt[field] = 'changed'
    monkeypatch.setattr(runtime, 'copy_verified_dependency_bundle', lambda *a, **k: receipt)
    with pytest.raises(ValueError, match='identity'):
        runtime._stage_dependencies('input', tmp_path / 'stage', checked, b'lock', {},
                                    _Budget(None, 5, time.monotonic()))


@pytest.mark.parametrize('record', [None, {}, [], [None], [[]], [{}],
    [{'Id': []}], [{'Id': 'a' * 64, 'Name': '/name', 'Image': 'image', 'Config': None}],
    [{'Id': 'a' * 64, 'Name': '/name', 'Image': 'image', 'Config': {'Labels': []}}]])
def test_malformed_cleanup_provider_never_removes_or_claims_cleanup(monkeypatch, record):
    monkeypatch.setattr(runtime, '_json_command', lambda *a: record)
    monkeypatch.setattr(runtime, '_docker', lambda *a: pytest.fail('uncertain identity must not be removed'))
    assert runtime._cleanup_container('name', 'name', 'image', 'config') == (None, False)


def test_cleanup_removes_only_verified_immutable_id(monkeypatch):
    identifier = 'a' * 64
    record = {'Id': identifier, 'Name': '/name', 'Image': 'image',
              'Config': {'Labels': {'ai.eidolon.execution': 'name'}}}
    monkeypatch.setattr(runtime, '_json_command', lambda *a: [record])
    calls = []
    def docker(arguments, *args):
        calls.append(arguments)
        return 0, {}, {}, None
    monkeypatch.setattr(runtime, '_docker', docker)
    assert runtime._cleanup_container('name', 'name', 'image', 'config') == (identifier, True)
    assert calls == [['rm', '--force', identifier]]


@pytest.mark.parametrize('recipe,tool,entry', [
    ('workspace_typescript', 'typescript', 'node_modules/typescript/lib/typescript.js'),
    ('workspace_vitest_ui', 'vitest', 'node_modules/vitest/dist/node.js')])
def test_active_tool_requires_declared_toolchain_and_exact_entry(recipe, tool, entry):
    checked = {'manifest': {'files': [{'path': entry}], 'toolchain': {'tools': {}}}}
    with pytest.raises(ValueError, match='toolchain'):
        runtime._tool_entry({'recipe': recipe}, checked)
    checked['manifest']['toolchain']['tools'][tool] = '1.0.0'
    assert runtime._tool_entry({'recipe': recipe}, checked) == entry
    checked['manifest']['files'] = []
    with pytest.raises(ValueError, match='entry'):
        runtime._tool_entry({'recipe': recipe}, checked)


def test_runtime_rejects_source_path_outside_protocol_before_docker(monkeypatch):
    path = '/'.join(['a' * 100] * 6) + '.ts'
    value = ProjectSnapshotBundle('root0', (1, 2), (ProjectSnapshotFile(path, b'export {};'),),
                                  ProjectSnapshotLimits(10, 1024, 4096))
    monkeypatch.setattr(runtime, '_docker', lambda *a: pytest.fail('invalid path must not execute'))
    result = runtime.run_prepared_workspace_step(value, '/not-read', {}, {}, profile(), 'renderer-types',
                allow_local_docker=True, check_authorized=lambda: True)
    assert result['status'] == 'blocked'
    assert 'relative path' in result['reason']
    assert 'containerName' not in result


def test_runtime_diagnostics_do_not_reexpose_execution_only_secret_shapes():
    credential = 'sk-' + 'a' * 48
    raw = ('diagnostic https://fixture:' + credential + '@example.invalid/ detail').encode()
    result = runtime._safe_log(raw)
    assert credential not in result
    assert 'diagnostic' in result


def test_failure_summary_is_diagnostic_only_and_drops_arbitrary_payloads():
    step, report = frame('workspace_vitest_ui')
    assert runtime._failure_summary(report, step) is None
    report.update(status='failed', failedCount=1, toolExitCode=1, arbitrary='not forwarded')
    summary = runtime._failure_summary(report, step)
    assert summary['failedCount'] == 1
    assert summary['evidenceOrigin'] == 'workload-claim'
    assert 'arbitrary' not in summary
    assert not runtime._outcome(report, step, source(), 'apps/desktop')
    report['failedCount'] = True
    assert runtime._failure_summary(report, step) is None
