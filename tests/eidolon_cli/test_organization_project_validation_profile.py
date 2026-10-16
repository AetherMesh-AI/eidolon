"""Inert preparation identity tests; not runtime proof."""
import copy

import pytest

from eidolon_cli.organization_project_validation_profile import (
    describe_validation_profile,
    normalize_validation_profile,
    validation_set_identity,
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


def identity(value):
    return validation_set_identity(value, objective_id='objective-1', project_id='desktop',
                                   source_sha256='b' * 64, authority_sha256='c' * 64)


def test_inert_description_and_detached_normalization():
    value = profile()
    normalized = normalize_validation_profile(value)
    value['steps'][1]['testFiles'].append('apps/desktop/src/changed.test.tsx')
    assert normalized != normalize_validation_profile(value)
    info = describe_validation_profile(normalized)
    assert info['state'] == 'runtime_unverified'
    assert info['executionAuthorized'] is False
    assert {step['checkKind'] for step in info['requiredSteps']} == {'project_tests', 'project_typecheck'}


def test_canonical_order_and_exact_scope_identity():
    value = profile()
    expected = identity(value)
    value['steps'].reverse()
    assert identity(value) == expected
    value['steps'][0]['testFiles'].append('apps/desktop/src/other.test.ts')
    assert identity(value)['sha256'] != expected['sha256']
    changed = profile()
    changed['bundleSha256'] = 'd' * 64
    assert identity(changed)['sha256'] != expected['sha256']
    changed = profile()
    changed['limits']['wallSeconds'] += 1
    assert identity(changed)['sha256'] != expected['sha256']


@pytest.mark.parametrize('field,value', [('version', True), ('version', 2), ('workspace', '../desktop'),
                                       ('bundleSha256', 'A' * 64), ('steps', []), ('id', 'bad id')])
def test_bad_profile_fields_rejected(field, value):
    data = profile()
    data[field] = value
    with pytest.raises(ValueError):
        normalize_validation_profile(data)


@pytest.mark.parametrize('mutation', [
    lambda p: p.update(command='npm test'),
    lambda p: p['steps'][0].update(sourceDirectories=[]),
    lambda p: p['steps'][0].update(sourceDirectories=['apps/desktop', 'apps/desktop/src']),
    lambda p: p['steps'][1].update(sourceDirectories=['apps/desktop/src']),
    lambda p: p['limits'].update(network=True),
    lambda p: p['limits'].update(cpus=True),
    lambda p: p['limits'].update(wallSeconds=1801),
    lambda p: p['steps'][0].update(recipe='shell'),
    lambda p: p['steps'][0].update(checkKind='project_tests'),
    lambda p: p['steps'][0].update(config='tsconfig.json'),
    lambda p: p['steps'][0].update(testFiles=['apps/desktop/src/a.test.ts']),
    lambda p: p['steps'][1].update(testFiles=[]),
    lambda p: p['steps'][1].update(testFiles=['apps/desktop/src/**/*.test.ts']),
    lambda p: p['steps'][1].update(testFiles=['apps/desktop/src/a.test.ts', 'apps/desktop/src/A.test.ts']),
    lambda p: p['steps'][1].update(testFiles=['apps/desktop/src/../secret.test.ts']),
    lambda p: p['steps'][1].update(testFiles=['apps/desktop/electron/a.test.ts']),
    lambda p: p['steps'][1].update(id='renderer-types'),
    lambda p: p['steps'].__setitem__(1, copy.deepcopy(p['steps'][0])),
])
def test_no_implicit_authority_or_ambiguous_selection(mutation):
    data = profile()
    mutation(data)
    with pytest.raises(ValueError):
        normalize_validation_profile(data)


def test_source_and_owner_authority_changes_invalidate_set():
    original = identity(profile())
    for field in ('source_sha256', 'authority_sha256'):
        kwargs = {'objective_id': 'objective-1', 'project_id': 'desktop',
                  'source_sha256': 'b' * 64, 'authority_sha256': 'c' * 64}
        kwargs[field] = 'd' * 64
        assert validation_set_identity(profile(), **kwargs)['sha256'] != original['sha256']
