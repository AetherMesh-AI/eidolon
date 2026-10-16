"""Inert contract invariants; these tests are not native execution evidence."""
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
import sqlite3

import pytest

from eidolon_cli import organization_prepared_validation_contract as contracts
from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_project_dependency_bundle import normalize_prepared_dependency_manifest
from eidolon_cli.organization_project_validation import digest
from eidolon_cli.organization_project_validation_profile import (
    normalize_validation_profile, validation_set_identity,
)
from eidolon_cli.organization_projects import _binding, bind_task_project, parse_projects


def raw(value):
    return json.dumps(value, sort_keys=True).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


@pytest.fixture
def prepared(tmp_path):
    root = tmp_path / 'source'
    root.mkdir()
    settings = OrganizationSettings.from_config({'organization': {
        'read_roots': [str(root)],
        'project_grants': [{'id': 'existing-recipe', 'files': ['root0/example.py'], 'execution': {'root': 'root0'}}],
        'projects': [{'id': 'desktop', 'root': 'root0', 'recipe': 'existing-recipe', 'team': 'engineering'}]}})
    binding = _binding(settings.projects[0], settings)
    manifests = {'package.json': raw({'name': 'root', 'workspaces': ['apps/desktop']}),
                 'apps/desktop/package.json': raw({'name': 'desktop'})}
    lock = raw({'lockfileVersion': 3, 'packages': {
        '': {'name': 'root'}, 'apps/desktop': {'name': 'desktop'},
        'node_modules/typescript': {'version': '5.9.3'},
        'node_modules/vitest': {'version': '3.2.4'}}})
    tools = {'typescript': '5.9.3', 'vitest': '3.2.4'}
    files = {'node_modules/typescript/package.json': raw({'name': 'typescript', 'version': tools['typescript']}),
             'node_modules/vitest/package.json': raw({'name': 'vitest', 'version': tools['vitest']}),
             'node_modules/typescript/lib/typescript.js': b'// inert compiler fixture',
             'node_modules/vitest/dist/node.js': b'// inert test fixture'}
    toolchain = {'platform': 'linux', 'arch': 'x64', 'libc': 'glibc', 'nodeVersion': '24.11.0',
                 'npmVersion': '11.17.0', 'nodeAbi': '137', 'tools': tools}
    manifest = {'schemaVersion': 1, 'lockfile': {'path': 'package-lock.json', 'sha256': sha(lock)},
                'packageManifests': [{'path': path, 'sha256': sha(data)} for path, data in manifests.items()],
                'selectedWorkspaces': ['apps/desktop'], 'workspaceLinks': [], 'toolchain': toolchain,
                'provenance': {'kind': 'owner-supplied', 'preparationRecordSha256': sha(b'preparation'),
                               'registryPackages': []},
                'files': [{'path': path, 'bytes': len(data), 'sha256': sha(data), 'mode': 0o644, 'kind': 'file'}
                          for path, data in files.items()]}
    checked = normalize_prepared_dependency_manifest(manifest, lockfile_bytes=lock,
        package_manifests=manifests, expected_toolchain=toolchain)
    profile = {'version': 1, 'id': 'renderer', 'workspace': 'apps/desktop',
               'bundleSha256': checked['bundleSha256'],
               'steps': [
                   {'id': 'z-types', 'recipe': 'workspace_typescript', 'checkKind': 'project_typecheck',
                    'config': 'apps/desktop/tsconfig.json', 'testFiles': [],
                    'sourceDirectories': ['apps/desktop/src']},
                   {'id': 'a-tests', 'recipe': 'workspace_vitest_ui', 'checkKind': 'project_tests',
                    'config': 'apps/desktop/vitest.config.ts', 'testFiles': ['apps/desktop/src/main.test.tsx'],
                    'sourceDirectories': []}],
               'limits': {'wallSeconds': 300, 'memoryMiB': 2048, 'cpus': 1,
                          'pids': 64, 'scratchMiB': 256, 'logBytes': 65536}}
    identity = (root.stat().st_dev, root.stat().st_ino)
    contract = {'version': 1, 'contract': 'prepared-validation-v1',
                'organizationId': 'organization-1', 'ownerId': 'owner-1', 'projectId': 'desktop',
                'projectBindingSha256': contracts.legacy_project_binding_sha256(binding), 'revision': 3,
                'root': {'alias': 'root0', 'device': identity[0], 'inode': identity[1]},
                'source': {'files': ['package-lock.json', *manifests, 'apps/desktop/tsconfig.json',
                    'apps/desktop/vitest.config.ts', 'apps/desktop/src/main.test.tsx'],
                    'limits': {'max_files': 20, 'max_file_bytes': 32768, 'max_total_bytes': 65536,
                               'max_path_bytes': 512, 'max_depth': 32},
                    'executionOnlyHashes': {'apps/desktop/src/main.test.tsx': sha(b'reviewed fixture')}},
                'dependencies': {'directory': str(tmp_path / 'prepared'), 'device': 12, 'inode': 34,
                    'manifest': checked['manifest'], 'toolchain': toolchain,
                    **{key: checked[key] for key in ('bundleSha256', 'inventorySha256', 'lockfileSha256')}},
                'profile': profile,
                'runtime': {'backend': 'linux-docker-pinned', 'image': contracts.IMAGE,
                            'allowLocalDocker': True, 'network': 'none', 'credentials': 'none'}}
    inputs = {'project_binding': binding, 'organization_id': 'organization-1', 'owner_id': 'owner-1',
              'registration_revision': 3, 'root_identity': identity, 'lockfile_bytes': lock,
              'package_manifests': manifests}
    return {'contract': contract, 'inputs': inputs, 'settings': settings, 'binding': binding}


def normalize(prepared):
    return contracts.normalize_prepared_validation_contract(prepared['contract'], **prepared['inputs'])


def context(prepared):
    grant = normalize(prepared)
    return {'profile': grant['profile'], 'organization_id': grant['organizationId'], 'owner_id': grant['ownerId'],
            'owner_grant_sha256': digest(grant), 'objective_binding_sha256': sha(b'objective binding'),
            'policy_generation': 7, 'policy_fingerprint': sha(b'current policy'),
            'objective_id': 'objective-1', 'project_id': 'desktop', 'round_number': 1,
            'snapshot_sha256': sha(b'exact effective snapshot'), 'preimage_sha256': sha(b'preimage'),
            'managed_revisions_sha256': sha(b'contributing managed revisions and proposals')}


def envelope(ctx, generation=1):
    authority = contracts.validation_authority_sha256(**{key: ctx[key] for key in (
        'organization_id', 'owner_id', 'owner_grant_sha256', 'objective_binding_sha256',
        'policy_generation', 'policy_fingerprint')})
    identity = validation_set_identity(ctx['profile'], objective_id=ctx['objective_id'],
        project_id=ctx['project_id'], source_sha256=ctx['snapshot_sha256'], authority_sha256=authority)
    return {'version': 1, 'contract': 'prepared-validation-set-v1', 'setId': f'set-{generation}',
            'requestId': f'request-{generation}', 'objectiveId': ctx['objective_id'], 'projectId': ctx['project_id'],
            'round': ctx['round_number'], 'generation': generation, 'ownerGrantSha256': ctx['owner_grant_sha256'],
            'objectiveBindingSha256': ctx['objective_binding_sha256'],
            'policy': {'generation': ctx['policy_generation'], 'fingerprint': ctx['policy_fingerprint']},
            'source': {'snapshotSha256': ctx['snapshot_sha256'], 'preimageSha256': ctx['preimage_sha256'],
                       'managedRevisionsSha256': ctx['managed_revisions_sha256']}, 'identity': identity,
            'steps': [{**step, 'runId': f"run-{generation}-{step['id']}"} for step in identity['requiredSteps']]}


def evidence(value):
    results = [{'version': 1, 'contract': 'prepared-validation-result-reference-v1',
                'setId': value['setId'], 'generation': value['generation'], 'runId': step['runId'],
                'stepId': step['id'], **{key: step[key] for key in ('definitionSha256', 'recipe', 'checkKind')},
                'snapshotSha256': value['source']['snapshotSha256'],
                **{key: value['identity'][key] for key in ('profileSha256', 'bundleSha256', 'authoritySha256')},
                'receiptSha256': sha(step['runId'].encode()), 'status': 'passed',
                'cleanupConfirmed': True, 'stagingCleanupConfirmed': True}
               for step in value['steps']]
    aggregate = {'version': 1, 'contract': 'prepared-validation-aggregate-v1', 'setId': value['setId'],
                 'generation': value['generation'], 'envelopeSha256': digest(value),
                 'results': sorted(results, key=lambda item: item['stepId'])}
    source = {'source-artifact-1': sha(b'exact reviewable source artifact')}
    receipts = {item['runId']: item['receiptSha256'] for item in results}
    review = {'version': 1, 'contract': 'prepared-validation-review-v1', 'requestId': 'review-1',
              'setId': value['setId'], 'aggregateSha256': digest(aggregate), 'reviewerId': 'reviewer-1',
              'requestType': 'request.test_review', 'status': 'completed', 'approved': True,
              'evidence': {**source, **receipts}}
    return {'results': results, 'review': review, 'contributor_ids': ['author-1', 'prior-round-author'],
            'source_evidence': source, 'receipt_hashes': receipts, 'root_authorized': True}


def inspect(values, ctx, **changes):
    inputs = evidence(values[-1])
    inputs.update(changes)
    return contracts.inspect_latest_validation_contract(values, context=ctx, **inputs)


def test_normalization_is_detached_canonical_and_has_no_activation(prepared):
    value = normalize(prepared)
    old = deepcopy(value)
    prepared['contract']['source']['files'].reverse()
    prepared['contract']['profile']['steps'].reverse()
    assert normalize(prepared) == value
    prepared['contract']['profile']['steps'][0]['testFiles'].append('apps/desktop/src/new.test.tsx')
    prepared['contract']['dependencies']['manifest']['files'][0]['bytes'] += 1
    assert value == old
    assert value['revision'] == 3
    assert prepared['settings'].projects[0].recipe == 'existing-recipe'
    assert 'prepared-validation-v1' not in [grant.id for grant in prepared['settings'].project_grants]


@pytest.mark.parametrize('mutation', [
    lambda v: v.update(version=True), lambda v: v.update(contract='prepared-validation-v2'),
    lambda v: v.update(ownerId='different-owner'), lambda v: v.update(organizationId='different-org'),
    lambda v: v.update(projectId='other'), lambda v: v.update(projectBindingSha256='a' * 64),
    lambda v: v.update(revision=4), lambda v: v.update(revision=True),
    lambda v: v.update(command='npm test'), lambda v: v.update(env={'PATH': '/bad'}),
    lambda v: v['root'].update(alias='root1'), lambda v: v['root'].update(inode=9),
    lambda v: v['root'].update(device=True), lambda v: v['source'].update(files=[]),
    lambda v: v['source']['files'].append('.env'),
    lambda v: v['source']['files'].append('apps/desktop/src/../secret.ts'),
    lambda v: v['source']['files'].append('apps/desktop/src/*.tsx'),
    lambda v: v['source']['files'].append('Apps/desktop/src/other.ts'),
    lambda v: v['source']['files'].append('apps/desktop/src'),
    lambda v: v['source']['files'].append(v['source']['files'][0]),
    lambda v: v['source']['files'].remove('apps/desktop/tsconfig.json'),
    lambda v: v['source']['limits'].update(max_files=True),
    lambda v: v['source']['limits'].update(max_path_bytes=1024),
    lambda v: v['source']['limits'].update(network=True),
    lambda v: v['source'].update(executionOnlyHashes=None),
    lambda v: v['source']['executionOnlyHashes'].update({'missing.ts': 'a' * 64}),
    lambda v: v['source']['executionOnlyHashes'].update({'apps/desktop/src/main.test.tsx': 'A' * 64}),
    lambda v: v['dependencies'].update(directory='/tmp/../deps'),
    lambda v: v['dependencies'].update(inode=-1),
    lambda v: v['dependencies'].update(bundleSha256='a' * 64),
    lambda v: v['dependencies'].update(inventorySha256='a' * 64),
    lambda v: v['dependencies'].update(lockfileSha256='a' * 64),
    lambda v: v['dependencies']['toolchain']['tools'].update(typescript='5.0.0'),
    lambda v: v['dependencies']['manifest']['files'][0].update(sha256='a' * 64),
    lambda v: v['profile'].update(bundleSha256='b' * 64),
    lambda v: v['profile']['limits'].update(cpus=2),
    lambda v: v['profile']['limits'].update(pids=65),
    lambda v: v['profile']['steps'][0].update(checkKind='project_tests'),
    lambda v: v['runtime'].update(allowLocalDocker=False),
    lambda v: v['runtime'].update(allowLocalDocker=1),
    lambda v: v['runtime'].update(image='node:latest'),
    lambda v: v['runtime'].update(network='host'),
    lambda v: v['runtime'].update(credentials='inherited'),
    lambda v: v['runtime'].update(packageScripts=False),
    lambda v: v['runtime'].update(env={}),
])
def test_sidecar_rejects_ambiguous_or_expanded_authority(prepared, mutation):
    mutation(prepared['contract'])
    with pytest.raises(ValueError):
        normalize(prepared)


def test_raw_lockfile_and_package_bytes_are_required(prepared):
    for key, new in [('lockfile_bytes', prepared['inputs']['lockfile_bytes'] + b' '),
                     ('package_manifests', {**prepared['inputs']['package_manifests'], 'package.json': b'{}'})]:
        inputs = {**prepared['inputs'], key: new}
        with pytest.raises(ValueError):
            contracts.normalize_prepared_validation_contract(prepared['contract'], **inputs)
    encoded = json.dumps(prepared['contract'])
    with pytest.raises(ValueError, match='unambiguous JSON'):
        contracts.normalize_prepared_validation_contract(encoded.replace('"version": 1', '"version": 1, "version": 1', 1).encode(),
                                                         **prepared['inputs'])


def test_legacy_binding_bytes_and_planning_only_restriction_are_unchanged(prepared):
    settings = prepared['settings']
    before = json.dumps(_binding(settings.projects[0], settings), sort_keys=True).encode()
    before_profile = normalize_validation_profile(prepared['contract']['profile'])
    before_identity = validation_set_identity(before_profile, objective_id='objective-1', project_id='desktop',
                                             source_sha256='a' * 64, authority_sha256='b' * 64)
    normalize(prepared)
    assert json.dumps(_binding(settings.projects[0], settings), sort_keys=True).encode() == before
    assert contracts.legacy_project_binding_sha256(prepared['binding']) == sha(before)
    assert asdict(settings.projects[0]) == {key: prepared['binding'][key] for key in ('id', 'root', 'recipe', 'team')}
    assert normalize_validation_profile(prepared['contract']['profile']) == before_profile
    assert validation_set_identity(before_profile, objective_id='objective-1', project_id='desktop',
                                   source_sha256='a' * 64, authority_sha256='b' * 64) == before_identity
    raw_project = {'id': 'desktop', 'root': 'root0', 'recipe': None, 'team': 'engineering'}
    from dataclasses import replace
    settings = replace(settings, projects=parse_projects([raw_project], settings.read_roots, settings.project_grants))
    binding = _binding(settings.projects[0], settings)
    prepared['contract']['projectBindingSha256'] = contracts.legacy_project_binding_sha256(binding)
    prepared['inputs']['project_binding'] = binding
    normalize(prepared)
    with sqlite3.connect(':memory:') as conn:
        conn.row_factory = sqlite3.Row
        conn.executescript('CREATE TABLE tasks(id TEXT,objective_id TEXT,type TEXT,team TEXT);'
                          'CREATE TABLE objective_projects(objective_id TEXT,project_id TEXT,binding TEXT);')
        conn.execute('INSERT INTO tasks VALUES (?,?,?,?)', ('task-1', 'objective-1', 'work.edit', 'engineering'))
        conn.execute('INSERT INTO objective_projects VALUES (?,?,?)', ('objective-1', 'desktop', json.dumps(binding, sort_keys=True)))
        with pytest.raises(ValueError, match='planning and inspection only'):
            bind_task_project(conn, 'task-1', {'projectId': 'desktop', 'writePaths': ['root0/example.py']}, settings)
    with pytest.raises(ValueError, match='exactly id, root, recipe and team'):
        parse_projects([{**raw_project, 'validation': prepared['contract']}], settings.read_roots, settings.project_grants)


def test_set_wraps_unchanged_identity_and_orders_execution_by_recipe(prepared):
    ctx = context(prepared)
    value = envelope(ctx)
    expected = deepcopy(value)
    normalized = contracts.normalize_validation_set_envelope(value, **ctx)
    assert normalized == expected
    assert normalized['identity']['sha256'] == expected['identity']['sha256']
    assert normalized['steps'][0]['recipe'] == 'workspace_vitest_ui'
    assert [step['checkKind'] for step in contracts.validation_execution_order(normalized)] == ['project_typecheck', 'project_tests']
    value['steps'][0]['definitionSha256'] = 'f' * 64
    assert normalized == expected
    info = inspect([normalized], ctx)
    assert info['state'] == 'contract_consistent_runtime_unverified'
    assert not any(info[key] for key in ('runtimeReceiptsVerified', 'executionAuthorized',
                                        'acceptanceAuthorized', 'sourceDeliveryAuthorized'))
    assert info['requiredChecks'] == ['project_typecheck', 'project_tests']


@pytest.mark.parametrize('mutation', [
    lambda v: v.update(version=True), lambda v: v.update(generation=True),
    lambda v: v.update(generation=0), lambda v: v.update(token='private-lease-token'),
    lambda v: v.update(round=-1), lambda v: v.update(setId=v['steps'][0]['runId']),
    lambda v: v.update(projectId='other'), lambda v: v.update(ownerGrantSha256='a' * 64),
    lambda v: v.update(objectiveBindingSha256='a' * 64), lambda v: v.update(round=2),
    lambda v: v['policy'].update(generation=8), lambda v: v['policy'].update(fingerprint='a' * 64),
    lambda v: v['source'].update(preimageSha256='a' * 64),
    lambda v: v['source'].update(managedRevisionsSha256='a' * 64),
    lambda v: v['identity'].update(profileSha256='a' * 64),
    lambda v: v['steps'][0].update(checkKind='project_typecheck'),
    lambda v: v['steps'][0].update(definitionSha256='a' * 64),
    lambda v: v['steps'][0].update(runId=v['steps'][1]['runId']),
    lambda v: v['steps'].pop(),
])
def test_set_rejects_malformed_mixed_or_stale_metadata(prepared, mutation):
    ctx = context(prepared)
    value = envelope(ctx)
    mutation(value)
    with pytest.raises(ValueError):
        contracts.normalize_validation_set_envelope(value, **ctx)


def test_current_source_and_authority_context_must_match_even_rehashed_claims(prepared):
    ctx = context(prepared)
    for key in ('owner_id', 'organization_id', 'owner_grant_sha256', 'objective_binding_sha256',
                'policy_generation', 'policy_fingerprint', 'snapshot_sha256', 'preimage_sha256',
                'managed_revisions_sha256'):
        new = {**ctx, key: 8 if key == 'policy_generation' else ('other' if key.endswith('_id') else 'a' * 64)}
        with pytest.raises(ValueError):
            contracts.normalize_validation_set_envelope(envelope(new), **ctx)
    stale_bundle = deepcopy(ctx)
    stale_bundle['profile']['bundleSha256'] = 'e' * 64
    with pytest.raises(ValueError):
        contracts.normalize_validation_set_envelope(envelope(stale_bundle), **ctx)


@pytest.mark.parametrize('used,objective,owner', [(0, 1, 10), (0, 10, 1), (True, 10, 10), (9, 10, 20), (9, 20, 10)])
def test_two_unit_reservation_never_overdraws_either_ceiling(prepared, used, objective, owner):
    ctx = context(prepared)
    with pytest.raises(ValueError):
        contracts.plan_validation_reservation(envelope(ctx), history=[], used_units=used,
            objective_ceiling=objective, owner_ceiling=owner, context=ctx)


def test_reservation_conserves_prior_usage_and_requires_fresh_generation_and_runs(prepared):
    ctx = context(prepared)
    first, second = envelope(ctx), envelope(ctx, 2)
    plan = contracts.plan_validation_reservation(first, history=[], used_units=3,
        objective_ceiling=10, owner_ceiling=8, context=ctx)
    assert plan['priorUnits'] == 3 and plan['reservedUnits'] == 2 and plan['resultingUnits'] == 5
    assert sum(item['units'] for item in plan['steps']) == 2
    assert plan['committed'] is False and plan['executionAuthorized'] is False
    after_crash = contracts.plan_validation_reservation(second, history=[first], used_units=5,
        objective_ceiling=10, owner_ceiling=8, context=ctx)
    assert after_crash['resultingUnits'] == 7
    for changed, used in [(second, 1), (first, 5), (envelope(ctx, 3), 5)]:
        with pytest.raises(ValueError):
            contracts.plan_validation_reservation(changed, history=[first], used_units=used,
                objective_ceiling=10, owner_ceiling=8, context=ctx)
    second['steps'][0]['runId'] = first['steps'][0]['runId']
    with pytest.raises(ValueError, match='reuse'):
        contracts.plan_validation_reservation(second, history=[first], used_units=5,
            objective_ceiling=10, owner_ceiling=8, context=ctx)


@pytest.mark.parametrize('mutation', [
    lambda e: e['results'].pop(), lambda e: e['results'].append(deepcopy(e['results'][0])),
    lambda e: e['results'].__setitem__(1, deepcopy(e['results'][0])),
    lambda e: e['results'][0].update(setId='other'),
    lambda e: e['results'][0].update(generation=True),
    lambda e: e['results'][0].update(runId='unreserved'),
    lambda e: e['results'][0].update(snapshotSha256='a' * 64),
    lambda e: e['results'][0].update(profileSha256='a' * 64),
    lambda e: e['results'][0].update(bundleSha256='a' * 64),
    lambda e: e['results'][0].update(authoritySha256='a' * 64),
    lambda e: e['results'][0].update(definitionSha256='a' * 64),
    lambda e: e['results'][0].update(checkKind='project_typecheck'),
    lambda e: e['results'][0].update(receiptSha256='a' * 64),
    lambda e: e['results'][0].update(receiptSha256=e['results'][1]['receiptSha256']),
    lambda e: e['results'][0].update(status='failed'),
    lambda e: e['results'][0].update(status='unknown'),
    lambda e: e['results'][0].update(cleanupConfirmed=False),
    lambda e: e['results'][0].update(cleanupConfirmed=1),
    lambda e: e['results'][0].pop('stagingCleanupConfirmed'),
    lambda e: e['results'][0].update(stagingCleanupConfirmed=False),
    lambda e: e['results'][0].update(stagingCleanupConfirmed=1),
    lambda e: e['receipt_hashes'].clear(),
    lambda e: e['review'].update(approved=1),
    lambda e: e['review'].update(approved=False),
    lambda e: e['review'].update(reviewerId='author-1'),
    lambda e: e['review'].update(reviewerId='prior-round-author'),
    lambda e: e['review'].update(status='running'),
    lambda e: e['review'].update(requestType='work.inspect'),
    lambda e: e['review'].update(aggregateSha256='a' * 64),
    lambda e: e['review'].update(setId='old-set'),
    lambda e: e['review']['evidence'].pop('source-artifact-1'),
    lambda e: e['review']['evidence'].update(extra='a' * 64),
    lambda e: e.update(contributor_ids=[]),
    lambda e: e.update(contributor_ids=['author-1'] * 10_001),
    lambda e: e.update(root_authorized=False),
])
def test_pair_and_independent_review_fail_closed(prepared, mutation):
    ctx = context(prepared)
    value = envelope(ctx)
    inputs = evidence(value)
    mutation(inputs)
    with pytest.raises(ValueError):
        contracts.inspect_latest_validation_contract([value], context=ctx, **inputs)


def test_latest_generation_never_falls_back_after_crash_failure_or_partial_cleanup(prepared):
    ctx = context(prepared)
    old, newest = envelope(ctx), envelope(ctx, 2)
    assert inspect([old], ctx)['generation'] == 1
    for results in ([], evidence(old)['results'], [evidence(newest)['results'][0]]):
        with pytest.raises(ValueError):
            inspect([newest, old], ctx, results=results)
    for status, cleanup in [('failed', True), ('unknown', True), ('passed', False)]:
        inputs = evidence(newest)
        inputs['results'][1].update(status=status, cleanupConfirmed=cleanup)
        with pytest.raises(ValueError):
            contracts.inspect_latest_validation_contract([old, newest], context=ctx, **inputs)
    assert inspect([newest, old], ctx, **evidence(newest))['generation'] == 2
    with pytest.raises(ValueError, match='repeats'):
        inspect([old, old], ctx)
    with pytest.raises(ValueError, match='incomplete'):
        inspect([newest], ctx)


def test_malformed_external_binding_fails_as_contract_error(prepared):
    prepared['inputs']['project_binding'] = {}
    with pytest.raises(ValueError, match='Legacy binding'):
        normalize(prepared)


def test_initial_round_matches_real_store_and_historical_artifact_names_never_collide(prepared, tmp_path):
    from eidolon_cli.organization_store import OrganizationStore
    store = OrganizationStore(tmp_path / 'round.db', prepared['settings'])
    objective = store.create_objective('Inspect exact project', idempotency_key='initial-round',
                                       delivery_mode='managed_artifact', project_ids=['desktop'])
    with store._connect() as conn:
        initial_round = conn.execute('SELECT round FROM objective_control WHERE objective_id=?',
                                     (objective['id'],)).fetchone()[0]
    ctx = {**context(prepared), 'round_number': initial_round}
    first, second = envelope(ctx), envelope(ctx, 2)
    assert contracts.normalize_validation_set_envelope(first, **ctx)['round'] == initial_round
    second['setId'] = first['steps'][0]['runId']
    with pytest.raises(ValueError, match='namespaces collide'):
        inspect([first, second], ctx)
    second = envelope(ctx, 2)
    second['steps'][0]['runId'] = first['setId']
    with pytest.raises(ValueError, match='namespaces collide'):
        inspect([first, second], ctx)
