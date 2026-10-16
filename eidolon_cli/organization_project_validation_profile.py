"""Inert versioned workspace validation contracts, never execution authority.

Legacy single-recipe bindings remain unchanged. Native runtime and durable
multi-receipt integration are required before these profiles can execute.
"""
from __future__ import annotations

import re

from eidolon_cli.organization_project_validation import canonical, digest

_HASH = re.compile(r'[0-9a-f]{64}')
_ID = re.compile(r'[a-z][a-z0-9_-]{0,63}')
_KINDS = {'workspace_typescript': 'project_typecheck', 'workspace_vitest_ui': 'project_tests'}


def _hash(value):
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise ValueError('Profile requires exact lowercase SHA-256 identities')
    return value


def _path(value):
    if (not isinstance(value, str) or not value or len(value.encode('utf-8')) > 512
            or value.startswith('/') or any(char in value for char in '\\:*?[]{}')
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
            or any(part in {'', '.', '..'} for part in value.split('/'))
            or len(value.split('/')) > 32):
        raise ValueError('Profile requires an exact relative path')
    return value


def _exact(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError(f'{label} requires exact versioned fields')


def normalize_validation_profile(value):
    """Normalize explicit two-recipe scope, without commands or host probing."""
    _exact(value, {'version', 'id', 'workspace', 'bundleSha256', 'steps', 'limits'}, 'Profile')
    if type(value['version']) is not int or value['version'] != 1:
        raise ValueError('Unsupported validation profile version')
    if not isinstance(value['id'], str) or not _ID.fullmatch(value['id']):
        raise ValueError('Validation profile ID is invalid')
    workspace, bundle = _path(value['workspace']), _hash(value['bundleSha256'])
    limits = value['limits']
    bounds = {'wallSeconds': (1, 1800), 'memoryMiB': (256, 8192), 'cpus': (1, 4),
              'pids': (16, 256), 'scratchMiB': (16, 2048), 'logBytes': (1024, 1048576)}
    _exact(limits, bounds, 'Limits')
    for key, (low, high) in bounds.items():
        if type(limits[key]) is not int or not low <= limits[key] <= high:
            raise ValueError(f'Profile {key} is outside its bound')
    steps = value['steps']
    if not isinstance(steps, list) or len(steps) != 2:
        raise ValueError('Profile requires exactly one typecheck and one UI test step')
    result, ids, kinds = [], set(), set()
    for step in steps:
        _exact(step, {'id', 'recipe', 'checkKind', 'config', 'testFiles', 'sourceDirectories'}, 'Step')
        identifier, recipe = step['id'], step['recipe']
        if not isinstance(identifier, str) or not _ID.fullmatch(identifier) or identifier in ids:
            raise ValueError('Step IDs must be valid and unique')
        if not isinstance(recipe, str) or recipe not in _KINDS or recipe in kinds:
            raise ValueError('Steps require distinct supported recipes')
        if step['checkKind'] != _KINDS[recipe]:
            raise ValueError('Check kind does not match its exact recipe')
        config = _path(step['config'])
        if not config.startswith(workspace + '/'):
            raise ValueError('Configuration must be inside selected workspace')
        if recipe == 'workspace_typescript' and not config.endswith('.json'):
            raise ValueError('Typecheck configuration must be an explicit JSON file')
        directories = step['sourceDirectories']
        if not isinstance(directories, list) or len(directories) > 8:
            raise ValueError('Typecheck source directories must be explicitly bounded')
        directories = [_path(path) for path in directories]
        if recipe == 'workspace_typescript' and not directories:
            raise ValueError('Typechecking requires explicit source directory coverage')
        if recipe == 'workspace_vitest_ui' and directories:
            raise ValueError('UI tests use exact test files, not source directory selection')
        ordered = sorted(path.casefold() for path in directories)
        if len(set(ordered)) != len(ordered) or any(right.startswith(left + '/')
                for index, left in enumerate(ordered) for right in ordered[index + 1:]):
            raise ValueError('Typecheck source directories overlap or alias')
        files = step['testFiles']
        if not isinstance(files, list):
            raise ValueError('Test selection must be a list')
        if recipe == 'workspace_typescript' and files:
            raise ValueError('Typechecking does not accept a test selection')
        if recipe == 'workspace_vitest_ui' and not 1 <= len(files) <= 512:
            raise ValueError('UI tests require a nonempty exact bounded file selection')
        selected = []
        for path in files:
            path = _path(path)
            if not path.startswith(workspace + '/src/') or not path.endswith(('.test.ts', '.test.tsx')):
                raise ValueError('UI selection must name exact workspace source tests')
            selected.append(path)
        if len({path.casefold() for path in selected}) != len(selected):
            raise ValueError('Test selection contains duplicate or ambiguous paths')
        ids.add(identifier)
        kinds.add(recipe)
        result.append({'id': identifier, 'recipe': recipe, 'checkKind': step['checkKind'],
                       'config': config, 'testFiles': sorted(selected), 'sourceDirectories': sorted(directories)})
    normalized = {'version': 1, 'id': value['id'], 'workspace': workspace, 'bundleSha256': bundle,
                  'steps': sorted(result, key=lambda step: step['id']), 'limits': dict(limits)}
    if len(canonical(normalized).encode('utf-8')) > 65536:
        raise ValueError('Profile exceeds its combined byte bound')
    return normalized


def describe_validation_profile(value):
    """Configuration only; availability and successful execution remain unknown."""
    normalized = normalize_validation_profile(value)
    return {'version': 1, 'profileSha256': digest(normalized), 'state': 'runtime_unverified',
            'executionAuthorized': False, 'requiredSteps': normalized['steps'],
            'limitation': 'Prepared dependencies and actual native execution remain unverified.'}


def validation_set_identity(value, *, objective_id, project_id, source_sha256, authority_sha256):
    """Identity of a proposed set, not a successful or reusable run receipt."""
    profile = normalize_validation_profile(value)
    for identifier in (objective_id, project_id):
        if (not isinstance(identifier, str) or not identifier or len(identifier) > 128
                or any(ord(char) < 32 or ord(char) == 127 for char in identifier)):
            raise ValueError('Set requires bounded objective and project identities')
    record = {'version': 1, 'objectiveId': objective_id, 'projectId': project_id,
              'sourceSha256': _hash(source_sha256), 'authoritySha256': _hash(authority_sha256),
              'profileSha256': digest(profile), 'bundleSha256': profile['bundleSha256'],
              'requiredSteps': [{'id': step['id'], 'recipe': step['recipe'],
                                 'checkKind': step['checkKind'], 'definitionSha256': digest(step)}
                                for step in profile['steps']]}
    return {**record, 'sha256': digest(record)}
