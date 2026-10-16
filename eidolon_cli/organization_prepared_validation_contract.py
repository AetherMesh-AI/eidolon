"""Inert prepared-validation contracts, separate from legacy project bindings.

These pure checks bind metadata; they neither register owner authority nor verify
runtime receipts. Callers must retain the complete durable history, authenticate
current authority, verify actual source/dependency bytes and validate runtime
receipts before any later dispatch or acceptance integration may use them.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import re

from eidolon_cli.organization_project_dependency_bundle import (
    _json, normalize_prepared_dependency_manifest,
)
from eidolon_cli.organization_project_node import IMAGE
from eidolon_cli.organization_project_snapshot_bundle import (
    ProjectSnapshotLimits, _execution_only_policy, _validate_selection,
)
from eidolon_cli.organization_project_validation import canonical, digest
from eidolon_cli.organization_project_validation_profile import (
    normalize_validation_profile, validation_set_identity,
)

_CONTRACT = 'prepared-validation-v1'
_SET = 'prepared-validation-set-v1'
_MAX_HISTORY = 10_000
_MAX_INTEGER = 2**63 - 1
_KINDS = {'workspace_typescript': 'project_typecheck', 'workspace_vitest_ui': 'project_tests'}
_CONTRACT_FIELDS = {'version', 'contract', 'organizationId', 'ownerId', 'projectId',
                    'projectBindingSha256', 'revision', 'root', 'source',
                    'dependencies', 'profile', 'runtime'}
_SET_FIELDS = {'version', 'contract', 'setId', 'requestId', 'objectiveId', 'projectId',
               'round', 'generation', 'ownerGrantSha256', 'objectiveBindingSha256',
               'policy', 'source', 'identity', 'steps'}
_STEP_FIELDS = {'id', 'runId', 'definitionSha256', 'recipe', 'checkKind'}


def _exact(value, keys, label):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError(label + ' requires exact versioned fields')
    return value


def _integer(value, label, low=0, high=_MAX_INTEGER):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(label + ' requires a bounded integer')
    return value


def _id(value):
    if type(value) is not str or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', value):
        raise ValueError('Contract identity must be a bounded exact identifier')
    return value


def _hash(value):
    if type(value) is not str or not re.fullmatch(r'[0-9a-f]{64}', value):
        raise ValueError('Contract requires an exact lowercase SHA-256')
    return value


def _version(value, name):
    if type(value['version']) is not int or value['version'] != 1 or value['contract'] != name:
        raise ValueError('Unsupported prepared validation contract version')


def _directory(value):
    if (type(value) is not str or not value.startswith('/') or value == '/'
            or len(value.encode('utf-8', errors='replace')) > 4096
            or any(part in {'', '.', '..'} or part.endswith((' ', '.')) for part in value[1:].split('/'))
            or any(char in value for char in '\\:*?[]{}')
            or any(ord(char) < 32 or ord(char) == 127 or 0xD800 <= ord(char) <= 0xDFFF for char in value)):
        raise ValueError('Prepared directory requires an exact bounded absolute path')
    return value


def _device_identity(value):
    for key in ('device', 'inode'):
        _integer(value[key], 'Directory ' + key, high=2**64 - 1)
    return value


def legacy_project_binding_sha256(binding):
    """Hash the unchanged legacy stored binding encoding, not a new projection."""
    _exact(binding, {'id', 'root', 'recipe', 'team', 'readRoot', 'recipeSha256'}, 'Legacy binding')
    _id(binding['id'])
    if type(binding['root']) is not str or not re.fullmatch(r'root[0-7]', binding['root']):
        raise ValueError('Legacy binding root is invalid')
    _directory(binding['readRoot'])
    if (type(binding['team']) is not str or not binding['team'].strip()
            or binding['team'] != binding['team'].strip() or len(binding['team']) > 64):
        raise ValueError('Legacy binding team is invalid')
    if binding['recipe'] is None:
        if binding['recipeSha256'] is not None:
            raise ValueError('Planning-only legacy binding has no recipe digest')
    else:
        _id(binding['recipe'])
        _hash(binding['recipeSha256'])
    return hashlib.sha256(json.dumps(binding, sort_keys=True).encode('utf-8')).hexdigest()


def normalize_prepared_validation_contract(value, *, project_binding, organization_id, owner_id,
                                             registration_revision, root_identity, lockfile_bytes,
                                             package_manifests):
    """Bind supplied preparation metadata and bytes without filesystem access.

The identity arguments are independently retained caller context. They are not
inferred from the candidate. Dependency inventory bytes, directory liveness,
owner consent and current policy remain unverified by this function.
"""
    value = _json(value, 40 * 1024**2, 'Prepared validation contract')
    _exact(value, _CONTRACT_FIELDS, 'Prepared validation contract')
    _version(value, _CONTRACT)
    binding_sha256 = legacy_project_binding_sha256(project_binding)
    expected = {'organizationId': _id(organization_id), 'ownerId': _id(owner_id),
                'projectId': _id(project_binding['id']),
                'projectBindingSha256': binding_sha256,
                'revision': _integer(registration_revision, 'Registration revision', 1)}
    if any(type(value[key]) is not type(item) or value[key] != item for key, item in expected.items()):
        raise ValueError('Prepared validation owner/project/revision binding changed')
    root = _exact(value['root'], {'alias', 'device', 'inode'}, 'Source root')
    _device_identity(root)
    if (type(root_identity) is not tuple or len(root_identity) != 2
            or any(type(item) is not int or not 0 <= item <= 2**64 - 1 for item in root_identity)
            or root['alias'] != project_binding['root']
            or (root['device'], root['inode']) != root_identity):
        raise ValueError('Prepared validation root identity changed')
    source = _exact(value['source'], {'files', 'limits', 'executionOnlyHashes'}, 'Source policy')
    limits = _exact(source['limits'], {'max_files', 'max_file_bytes', 'max_total_bytes',
                                      'max_path_bytes', 'max_depth'}, 'Snapshot limits')
    limits = ProjectSnapshotLimits(**limits)
    if limits.max_path_bytes > 512:
        raise ValueError('Prepared runtime source paths are limited to 512 bytes')
    if type(source['files']) is not list:
        raise ValueError('Source files must be an exact bounded list')
    paths = list(_validate_selection(source['files'], limits))
    exceptions = _execution_only_policy(source['executionOnlyHashes'], paths, lambda: None)
    if type(source['executionOnlyHashes']) is not dict:
        raise ValueError('Execution-only hashes must be an explicit exact mapping')
    dependencies = _exact(value['dependencies'], {'directory', 'device', 'inode', 'manifest',
        'bundleSha256', 'inventorySha256', 'lockfileSha256', 'toolchain'}, 'Dependencies')
    _directory(dependencies['directory'])
    _device_identity(dependencies)
    checked = normalize_prepared_dependency_manifest(dependencies['manifest'],
        lockfile_bytes=lockfile_bytes, package_manifests=package_manifests,
        expected_toolchain=dependencies['toolchain'])
    for key in ('bundleSha256', 'inventorySha256', 'lockfileSha256'):
        if _hash(dependencies[key]) != checked[key]:
            raise ValueError('Prepared dependency ' + key + ' changed')
    if dependencies['toolchain']['arch'] != 'x64' or dependencies['toolchain']['libc'] != 'glibc':
        raise ValueError('Pinned prepared runtime requires a Linux x64 glibc toolchain')
    profile = normalize_validation_profile(value['profile'])
    if (profile['bundleSha256'] != checked['bundleSha256']
            or profile['workspace'] not in checked['manifest']['selectedWorkspaces']):
        raise ValueError('Profile does not match its prepared workspace/bundle')
    if profile['limits']['cpus'] != 1 or profile['limits']['pids'] != 64:
        raise ValueError('Prepared runtime requires exactly one CPU and 64 PIDs')
    required_paths = {'package-lock.json', *(item['path'] for item in checked['manifest']['packageManifests'])}
    required_paths.update(step['config'] for step in profile['steps'])
    required_paths.update(path for step in profile['steps'] for path in step['testFiles'])
    if not required_paths <= set(paths):
        raise ValueError('Source selection omits an exact package/configuration/test file')
    for step in profile['steps']:
        if any(not any(path.startswith(directory + '/') for path in paths)
               for directory in step['sourceDirectories']):
            raise ValueError('Source selection omits a required typecheck directory')
    tools = {'typescript': 'node_modules/typescript/lib/typescript.js',
             'vitest': 'node_modules/vitest/dist/node.js'}
    inventory = {item['path'] for item in checked['manifest']['files']}
    if any(name not in dependencies['toolchain']['tools'] or path not in inventory
           for name, path in tools.items()):
        raise ValueError('Prepared two-recipe toolchain lacks exact entry points')
    runtime = _exact(value['runtime'], {'backend', 'image', 'allowLocalDocker', 'network', 'credentials'}, 'Runtime')
    if (runtime != {'backend': 'linux-docker-pinned', 'image': IMAGE, 'allowLocalDocker': True,
                    'network': 'none', 'credentials': 'none'} or runtime['allowLocalDocker'] is not True):
        raise ValueError('Runtime must explicitly select the fixed isolated pinned image')
    return {**value, 'source': {'files': paths, 'limits': asdict(limits),
                               'executionOnlyHashes': dict(sorted(exceptions.items()))},
            'dependencies': {**dependencies, 'manifest': checked['manifest']}, 'profile': profile}


def validation_authority_sha256(*, organization_id, owner_id, owner_grant_sha256,
                                 objective_binding_sha256, policy_generation, policy_fingerprint):
    """Versioned metadata binding; a hash is not proof of present owner authority."""
    return digest({'version': 1, 'contract': 'prepared-validation-authority-v1',
                   'organizationId': _id(organization_id), 'ownerId': _id(owner_id),
                   'ownerGrantSha256': _hash(owner_grant_sha256),
                   'objectiveBindingSha256': _hash(objective_binding_sha256),
                   'policy': {'generation': _integer(policy_generation, 'Policy generation'),
                              'fingerprint': _hash(policy_fingerprint)}})


def _envelope_shape(value):
    value = _json(value, 131072, 'Validation set envelope')
    _exact(value, _SET_FIELDS, 'Validation set envelope')
    _version(value, _SET)
    for key in ('setId', 'requestId', 'objectiveId', 'projectId'):
        _id(value[key])
    _integer(value['round'], 'Objective round')
    _integer(value['generation'], 'Set generation', 1)
    for key in ('ownerGrantSha256', 'objectiveBindingSha256'):
        _hash(value[key])
    policy = _exact(value['policy'], {'generation', 'fingerprint'}, 'Current policy')
    _integer(policy['generation'], 'Policy generation')
    _hash(policy['fingerprint'])
    source = _exact(value['source'], {'snapshotSha256', 'preimageSha256', 'managedRevisionsSha256'}, 'Set source')
    for item in source.values():
        _hash(item)
    identity = _exact(value['identity'], {'version', 'objectiveId', 'projectId', 'sourceSha256',
        'authoritySha256', 'profileSha256', 'bundleSha256', 'requiredSteps', 'sha256'}, 'Proposed identity')
    if (type(identity['version']) is not int or identity['version'] != 1
            or identity['objectiveId'] != value['objectiveId'] or identity['projectId'] != value['projectId']
            or identity['sourceSha256'] != source['snapshotSha256']):
        raise ValueError('Set identity has mixed source/project context')
    for key in ('sourceSha256', 'authoritySha256', 'profileSha256', 'bundleSha256', 'sha256'):
        _hash(identity[key])
    if digest({key: item for key, item in identity.items() if key != 'sha256'}) != identity['sha256']:
        raise ValueError('Proposed identity hash changed')
    steps = value['steps']
    if type(steps) is not list or len(steps) != 2:
        raise ValueError('Set requires exactly two step reservations')
    for step in steps:
        _exact(step, _STEP_FIELDS, 'Set step')
        _id(step['id'])
        _id(step['runId'])
        _hash(step['definitionSha256'])
        if type(step['recipe']) is not str or _KINDS.get(step['recipe']) != step['checkKind']:
            raise ValueError('Set step recipe/check kind changed')
    if any(len({step[key] for step in steps}) != 2 for key in ('id', 'runId', 'recipe')):
        raise ValueError('Set steps and run identities must be distinct')
    if value['setId'] in {step['runId'] for step in steps}:
        raise ValueError('Set and run identity namespaces collide')
    required = [{key: step[key] for key in ('id', 'recipe', 'checkKind', 'definitionSha256')}
                for step in sorted(steps, key=lambda item: item['id'])]
    if identity['requiredSteps'] != required:
        raise ValueError('Both step definitions must match the proposed identity')
    return {**value, 'steps': sorted(steps, key=lambda item: item['id'])}


def normalize_validation_set_envelope(value, *, profile, organization_id, owner_id,
                                      owner_grant_sha256, objective_binding_sha256,
                                      policy_generation, policy_fingerprint,
                                      objective_id, project_id, round_number, snapshot_sha256,
                                      preimage_sha256, managed_revisions_sha256):
    """Check a proposed envelope against independently supplied exact context."""
    result = _envelope_shape(value)
    expected = {'objectiveId': _id(objective_id), 'projectId': _id(project_id),
                'round': _integer(round_number, 'Objective round'),
                'ownerGrantSha256': _hash(owner_grant_sha256),
                'objectiveBindingSha256': _hash(objective_binding_sha256),
                'source': {'snapshotSha256': _hash(snapshot_sha256),
                           'preimageSha256': _hash(preimage_sha256),
                           'managedRevisionsSha256': _hash(managed_revisions_sha256)},
                'policy': {'generation': _integer(policy_generation, 'Policy generation'),
                           'fingerprint': _hash(policy_fingerprint)}}
    if any(result[key] != item for key, item in expected.items()):
        raise ValueError('Validation set is stale or has a mixed binding/policy')
    authority = validation_authority_sha256(organization_id=organization_id, owner_id=owner_id,
        owner_grant_sha256=owner_grant_sha256, objective_binding_sha256=objective_binding_sha256,
        policy_generation=policy_generation, policy_fingerprint=policy_fingerprint)
    identity = validation_set_identity(profile, objective_id=objective_id, project_id=project_id,
        source_sha256=result['source']['snapshotSha256'], authority_sha256=authority)
    if result['identity'] != identity:
        raise ValueError('Validation set profile/source/bundle/authority identity changed')
    return result


def _history(envelopes, objective_id, project_id):
    if type(envelopes) is not list or len(envelopes) > _MAX_HISTORY:
        raise ValueError('Complete validation history must be an explicitly bounded list')
    records = [_envelope_shape(value) for value in envelopes]
    if any(value['objectiveId'] != objective_id or value['projectId'] != project_id for value in records):
        raise ValueError('Validation history mixes projects/objectives')
    for key in ('setId', 'generation'):
        if len({value[key] for value in records}) != len(records):
            raise ValueError('Validation history repeats a set/generation')
    runs = [step['runId'] for value in records for step in value['steps']]
    if len(set(runs)) != len(runs):
        raise ValueError('Validation generations reuse a run identity')
    if {value['setId'] for value in records} & set(runs):
        raise ValueError('Historical set and run identity namespaces collide')
    ordered = sorted(records, key=lambda value: value['generation'])
    if [value['generation'] for value in ordered] != list(range(1, len(ordered) + 1)):
        raise ValueError('Validation history is incomplete or skips a generation')
    if any(right['round'] < left['round'] for left, right in zip(ordered, ordered[1:])):
        raise ValueError('Validation generation moved backwards across objective rounds')
    return ordered


def plan_validation_reservation(value, *, history, used_units, objective_ceiling,
                                 owner_ceiling, context):
    """Pure two-unit accounting proposal, never a committed reservation or dispatch.

The future store must serialize history and usage reads with both reservations
in one transaction. An interrupted step cannot refund either reserved unit.
"""
    envelope = normalize_validation_set_envelope(value, **context)
    prior = _history(history, envelope['objectiveId'], envelope['projectId'])
    _history([*prior, envelope], envelope['objectiveId'], envelope['projectId'])
    if envelope['generation'] != len(prior) + 1:
        raise ValueError('Only a fresh next validation generation may be reserved')
    used = _integer(used_units, 'Prior charged execution units')
    ceiling = min(_integer(objective_ceiling, 'Objective execution ceiling', 1),
                  _integer(owner_ceiling, 'Owner execution ceiling', 1))
    if used < 2 * len(prior) or used + 2 > ceiling:
        raise ValueError('Two-step reservation would erase prior usage or exceed a ceiling')
    return {'setId': envelope['setId'], 'generation': envelope['generation'], 'priorUnits': used,
            'reservedUnits': 2, 'resultingUnits': used + 2, 'effectiveCeiling': ceiling,
            'steps': [{'runId': step['runId'], 'units': 1} for step in envelope['steps']],
            'committed': False, 'executionAuthorized': False}


def validation_execution_order(value):
    """Compiler validation precedes UI tests regardless of arbitrary step IDs."""
    envelope = _envelope_shape(value)
    return sorted(envelope['steps'], key=lambda step: step['recipe'] != 'workspace_typescript')


def _result_reference(value, envelope):
    keys = {'version', 'contract', 'setId', 'generation', 'runId', 'stepId', 'definitionSha256',
            'recipe', 'checkKind', 'snapshotSha256', 'profileSha256', 'bundleSha256',
            'authoritySha256', 'receiptSha256', 'status', 'cleanupConfirmed', 'stagingCleanupConfirmed'}
    _exact(value, keys, 'Step result reference')
    _version(value, 'prepared-validation-result-reference-v1')
    step = next((step for step in envelope['steps'] if step['runId'] == value['runId']), None)
    if step is None:
        raise ValueError('Result reference has an unreserved run identity')
    expected = {'setId': envelope['setId'], 'generation': envelope['generation'],
                'runId': step['runId'], 'stepId': step['id'],
                **{key: step[key] for key in ('definitionSha256', 'recipe', 'checkKind')},
                'snapshotSha256': envelope['source']['snapshotSha256'],
                **{key: envelope['identity'][key] for key in ('profileSha256', 'bundleSha256', 'authoritySha256')}}
    if any(type(value[key]) is not type(item) or value[key] != item for key, item in expected.items()):
        raise ValueError('Result reference mixes set/source/profile/bundle/authority/step identities')
    _hash(value['receiptSha256'])
    if (value['status'] != 'passed' or value['cleanupConfirmed'] is not True
            or value['stagingCleanupConfirmed'] is not True):
        raise ValueError('Latest validation claims failed, interrupted or incomplete cleanup')
    return dict(value)


def inspect_latest_validation_contract(envelopes, *, results, review, contributor_ids,
                                       source_evidence, receipt_hashes, context, root_authorized):
    """Inspect latest-generation hash/review consistency, NOT execution evidence.

Result references are metadata claims, not authenticated runtime receipts. Even
an internally consistent complete pair always remains runtime-unverified and
cannot authorize acceptance, source delivery or dispatch. No older success is
selected when the newest generation is failed, incomplete, stale or malformed.
"""
    if root_authorized is not True:
        raise ValueError('Current root authority is missing or revoked')
    history = _history(envelopes, context['objective_id'], context['project_id'])
    if not history:
        raise ValueError('No validation generation has been reserved')
    envelope = normalize_validation_set_envelope(history[-1], **context)
    if type(results) is not list or len(results) != 2:
        raise ValueError('Latest validation requires exactly two result references')
    references = [_result_reference(value, envelope) for value in results]
    if any(len({item[key] for item in references}) != 2 for key in ('runId', 'receiptSha256')):
        raise ValueError('Latest validation repeats a result reference or receipt')
    if type(receipt_hashes) is not dict or set(receipt_hashes) != {item['runId'] for item in references}:
        raise ValueError('Exact retained receipt hashes are missing or extraneous')
    if any(item['receiptSha256'] != _hash(receipt_hashes[item['runId']]) for item in references):
        raise ValueError('Result reference differs from the retained receipt hash')
    references.sort(key=lambda item: item['stepId'])
    aggregate = {'version': 1, 'contract': 'prepared-validation-aggregate-v1',
                 'setId': envelope['setId'], 'generation': envelope['generation'],
                 'envelopeSha256': digest(envelope), 'results': references}
    aggregate_sha256 = digest(aggregate)
    if (type(contributor_ids) not in (set, frozenset, list, tuple)
            or not 1 <= len(contributor_ids) <= 10_000):
        raise ValueError('Independent review requires complete contributing author identities')
    contributors = {_id(item) for item in contributor_ids}
    if type(source_evidence) is not dict or not 1 <= len(source_evidence) <= 10_000:
        raise ValueError('Review requires exact bounded source evidence identities/hashes')
    evidence = {_id(key): _hash(value) for key, value in source_evidence.items()}
    run_evidence = {item['runId']: item['receiptSha256'] for item in references}
    if set(evidence) & set(run_evidence):
        raise ValueError('Source and runtime evidence identities must be distinct')
    evidence.update(run_evidence)
    _exact(review, {'version', 'contract', 'requestId', 'setId', 'aggregateSha256', 'reviewerId',
                    'requestType', 'status', 'approved', 'evidence'}, 'Whole-set review')
    _version(review, 'prepared-validation-review-v1')
    _id(review['requestId'])
    reviewer = _id(review['reviewerId'])
    if (review['approved'] is not True or reviewer in contributors
            or review['requestType'] != 'request.test_review' or review['status'] != 'completed'
            or review['setId'] != envelope['setId'] or review['aggregateSha256'] != aggregate_sha256
            or review['evidence'] != evidence):
        raise ValueError('Whole-set review is incomplete, stale or not independent')
    return {'state': 'contract_consistent_runtime_unverified', 'setId': envelope['setId'],
            'generation': envelope['generation'], 'aggregate': aggregate,
            'aggregateSha256': aggregate_sha256, 'reviewerId': reviewer,
            'requiredChecks': [step['checkKind'] for step in validation_execution_order(envelope)],
            'runtimeReceiptsVerified': False, 'executionAuthorized': False,
            'acceptanceAuthorized': False, 'sourceDeliveryAuthorized': False}
