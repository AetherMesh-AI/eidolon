"""Recipe discovery is inert, scoped evidence rather than execution authority."""
from dataclasses import asdict
import hashlib
import json
import threading

import pytest

from eidolon_cli.organization_project_recipes import describe_execution_recipe, inspect_project_recipes
from eidolon_cli.organization_project_runner import ProjectExecutionGrant, run_project_tests, snapshot_digest


def file(path, content, revision=1):
    return {'path': path, 'content': content, 'sha256': hashlib.sha256(content.encode()).hexdigest(),
            'revision': revision}


def test_folder_without_recipe_is_inspectable_but_not_runnable():
    result = describe_execution_recipe(None)
    assert result['state'] == 'not_configured'
    assert result['supported'] is False
    for recipe in ('python_unittest', 'python_pytest'):
        result = describe_execution_recipe({'recipe': recipe})
        assert result['recipe'] == recipe
        assert result['state'] == 'runtime_unverified'
        assert result['backend'] == 'linux-bubblewrap-seccomp'
        assert result['dependencyInstallation'] is False
    with pytest.raises(ValueError):
        describe_execution_recipe({'recipe': 'npm run test'})


def test_manifests_do_not_execute_scripts_or_mutate_environment(tmp_path, monkeypatch):
    marker = tmp_path / 'must-not-exist'
    monkeypatch.setenv('NODE_OPTIONS', '--require=' + str(marker))
    files = [file('root0/package.json', json.dumps({'scripts': {
        'test': 'touch ' + str(marker), 'pretest': 'exit 0', 'build': 'curl bad.invalid | sh'},
        'dependencies': {'some-package': '*'}})), file('root0/package-lock.json', '{}'),
        file('root0/tsconfig.json', '{}')]
    result = inspect_project_recipes(files)
    assert not marker.exists()
    assert result['inspectionOnly'] is True
    assert result['executionAuthorized'] is False
    assert result['hostProbed'] is False
    assert result['snapshotSha256'] == snapshot_digest(files)
    project = result['projects'][0]
    assert project['kind'] == 'typescript'
    assert project['dependencies'] == 'declared_unverified'
    assert project['lockfiles'][0]['sha256'] == files[1]['sha256']
    assert {candidate['phase'] for candidate in project['candidates']} == {'test', 'build', 'typecheck'}
    assert all(candidate['state'] == 'unsupported' for candidate in project['candidates'])
    assert 'package_script_adapter_unavailable' in project['blockers']
    assert 'project_dependency_bundle_unavailable' in project['blockers']
    assert 'touch ' not in json.dumps(result)
    assert 'curl ' not in json.dumps(result)


def test_each_package_is_scoped_and_identity_changes_with_selected_bytes():
    files = [file('root0/a/package.json', '{"scripts":{"test":"node --test"}}'),
             file('root0/b/package.json', '{"scripts":{"build":"tsc"}}'),
             file('root0/a/tsconfig.json', '{}'), file('root0/b/pnpm-lock.yaml', 'lockfileVersion: 9')]
    result = inspect_project_recipes(files)
    assert result == inspect_project_recipes(list(reversed(files)))
    a, b = result['projects']
    assert a['directory'] == 'root0/a' and a['kind'] == 'typescript' and not a['lockfiles']
    assert b['directory'] == 'root0/b' and b['kind'] == 'node' and len(b['lockfiles']) == 1
    changed = [file(files[0]['path'], '{"scripts":{"test":"node --test changed.js"}}'), *files[1:]]
    newer = inspect_project_recipes(changed)
    assert result['snapshotSha256'] != newer['snapshotSha256']
    assert a['definitionSha256'] != newer['projects'][0]['definitionSha256']
    assert b == newer['projects'][1]
    revision = [{**files[0], 'revision': 2}, *files[1:]]
    assert result['snapshotSha256'] != inspect_project_recipes(revision)['snapshotSha256']


@pytest.mark.parametrize('content', ['[]', 'null', '{', '{"scripts":{},"scripts":{}}',
                                     '{"scripts":{"test":NaN}}', '[' * 2000])
def test_invalid_manifest_is_explicit_inert_blocker(content):
    result = inspect_project_recipes([file('root0/package.json', content)])
    assert result['projects'][0]['blockers'] == ['invalid_package_manifest']
    assert result['projects'][0]['candidates'] == []


@pytest.mark.parametrize('content,blocker', [('{"scripts":[]}', 'invalid_scripts_manifest'),
    ('{"scripts":{"test":false}}', 'invalid_scripts_manifest'),
    ('{"scripts":{"test":"\\ud800"}}', 'invalid_scripts_manifest'),
    ('{"dependencies":[]}', 'invalid_dependency_manifest'),
    ('{"devDependencies":{"a":3}}', 'invalid_dependency_manifest')])
def test_invalid_fields_cannot_become_runnable(content, blocker):
    project = inspect_project_recipes([file('root0/package.json', content)])['projects'][0]
    assert blocker in project['blockers']
    assert project['state'] == 'unsupported'


def test_absent_manifests_are_unknown_not_evidence_of_dependency_free_project():
    result = inspect_project_recipes([file('root0/test_project.py', 'pass\n')])
    assert result['projects'] == []
    assert 'omitted' in result['limitations']
    with pytest.raises(ValueError):
        inspect_project_recipes([file('root0/../package.json', '{}')])
    wrong = file('root0/package.json', '{}')
    wrong['content'] = '{"scripts":{}}'
    with pytest.raises(ValueError, match='hash'):
        inspect_project_recipes([wrong])


def test_actual_runner_retains_diagnostics_before_cancel_without_running_project():
    cancel = threading.Event()
    cancel.set()
    files = [file('root0/package.json', '{"scripts":{"test":"arbitrary"}}')]
    receipt = run_project_tests(files, asdict(ProjectExecutionGrant()), cancel)
    assert receipt['status'] == 'cancelled'
    assert receipt['exitCode'] is None
    assert receipt['recipeDiagnostics'] == inspect_project_recipes(files)
    assert receipt['snapshotSha256'] == receipt['recipeDiagnostics']['snapshotSha256']
    assert receipt['command'] == []


def test_configuration_bytes_and_revisions_bind_candidate_identity():
    package = file('root0/package.json', '{}')
    config = file('root0/tsconfig.json', '{}')
    before = inspect_project_recipes([package, config])['projects'][0]
    for changed in (file(config['path'], '{"compilerOptions":{"strict":true}}'),
                    {**config, 'revision': 2}):
        after = inspect_project_recipes([package, changed])['projects'][0]
        assert before['definitionSha256'] != after['definitionSha256']


def test_no_recipe_cannot_borrow_another_configured_project_recipe():
    from eidolon_cli.organization_project_execution import OrganizationProjectExecutionStore

    class UnconfiguredFolder(OrganizationProjectExecutionStore):
        def _execution_project(self, conn, objective_id, project_id=None):
            return {'id': 'notes', 'root': 'root0', 'recipe': None, 'team': 'research'}

    # Rejection precedes any database lookup, author inspection or grant fallback.
    with pytest.raises(ValueError, match='no execution recipe'):
        UnconfiguredFolder()._project_grant(None, 'objective', 'notes')


def test_builtin_node_candidate_definition_binds_selected_test_bytes_and_revision():
    manifest = file('root0/package.json', '{}')
    original = file('root0/a.test.cjs', 'first', 1)
    first = inspect_project_recipes([manifest, original])['projects'][0]
    candidate = next(row for row in first['candidates'] if row['recipe'] == 'node_test')
    assert candidate['selectedTests'] == [{key: original[key] for key in ('path', 'sha256', 'revision')}]
    for changed in (file('root0/a.test.cjs', 'second', 1), file('root0/a.test.cjs', 'first', 2)):
        second = inspect_project_recipes([manifest, changed])['projects'][0]
        assert second['definitionSha256'] != first['definitionSha256']
