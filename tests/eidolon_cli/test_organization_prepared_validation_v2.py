"""Three-recipe metadata contracts and atomic real-SQL reservations, not native proof."""
from copy import deepcopy
from dataclasses import replace
import json
import sqlite3

import pytest

from eidolon_cli import organization_prepared_validation_contract as contract
from eidolon_cli import organization_prepared_validation_ledger as ledger
from eidolon_cli.organization_project_dependency_bundle import normalize_prepared_dependency_manifest
from eidolon_cli.organization_project_snapshot_bundle import ProjectSnapshotBundle, ProjectSnapshotFile, ProjectSnapshotLimits
from eidolon_cli.organization_project_validation import canonical, digest
from eidolon_cli.organization_project_validation_profile import (
    normalize_validation_profile, profile_required_checks, validation_step_count,
)
from eidolon_cli.organization_store import OrganizationStore
from tests.eidolon_cli.test_organization_prepared_validation_contract import (
    context, envelope, evidence, normalize, prepared, raw, sha,  # noqa: F401
)
from tests.eidolon_cli.test_organization_prepared_validation_ledger import (
    legacy_run, proposal, reserve,
)


@pytest.fixture
def prepared_v2(prepared):
    value = deepcopy(prepared)
    grant = value['contract']
    grant.update(contract.versioned_contract_fields(2, 'prepared-validation'))
    profile = grant['profile']
    profile['version'] = 2
    profile['steps'].append({'id': 'b-renderer', 'recipe': 'workspace_vite_build', 'checkKind': 'project_build',
                            'config': 'apps/desktop/vite.config.ts', 'testFiles': [], 'sourceDirectories': []})
    grant['source']['files'] += ['apps/desktop/index.html', 'apps/desktop/vite.config.ts']
    dep = grant['dependencies']
    dep['toolchain']['tools']['vite'] = '8.2.0'
    lock = json.loads(value['inputs']['lockfile_bytes'])
    lock['packages']['node_modules/vite'] = {'version': '8.2.0'}
    value['inputs']['lockfile_bytes'] = raw(lock)
    manifest = dep['manifest']
    manifest['toolchain'] = dep['toolchain']
    manifest['lockfile']['sha256'] = sha(value['inputs']['lockfile_bytes'])
    for path, content in {'node_modules/vite/package.json': raw({'name': 'vite', 'version': '8.2.0'}),
                          'node_modules/vite/dist/node/index.js': b'// inert renderer fixture'}.items():
        manifest['files'].append({'path': path, 'bytes': len(content), 'sha256': sha(content),
                                  'mode': 0o644, 'kind': 'file'})
    checked = normalize_prepared_dependency_manifest(manifest, lockfile_bytes=value['inputs']['lockfile_bytes'],
        package_manifests=value['inputs']['package_manifests'], expected_toolchain=dep['toolchain'])
    dep.update({key: checked[key] for key in ('manifest', 'bundleSha256', 'inventorySha256', 'lockfileSha256')})
    profile['bundleSha256'] = checked['bundleSha256']
    return value


def versioned_envelope(ctx, generation=1):
    value = envelope(ctx, generation)
    value.update(contract.versioned_contract_fields(ctx['profile']['version'], 'prepared-validation-set'))
    return value


def versioned_evidence(value):
    records = evidence(value)
    for item in records['results']:
        item.update(contract.versioned_contract_fields(value['version'], 'prepared-validation-result-reference'))
    aggregate = ledger._aggregate(value, records['results'])
    records['review'].update(contract.versioned_contract_fields(value['version'], 'prepared-validation-review'))
    records['review']['aggregateSha256'] = digest(aggregate)
    return records


@pytest.fixture
def state_v2(tmp_path, prepared_v2):
    store = OrganizationStore(tmp_path / 'v2.db', replace(prepared_v2['settings'], max_project_runs=20))
    conn = sqlite3.connect(store.path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    result = {'store': store, 'conn': conn, 'prepared': prepared_v2}
    yield result
    conn.close()


def objective_v2(state, max_runs=20):
    store, conn, prepared = state['store'], state['conn'], state['prepared']
    with store._write() as write:
        identifier = store._create_objective(write, 'Three checks', idempotency_key='v2-objective',
            project_ids=['desktop'], delivery_mode='managed_artifact', required_checks=[], project_run_limit=max_runs)
    bound = ledger.bind_prepared_validation_proposal(conn, objective_id=identifier,
        value=prepared['contract'], context=prepared['inputs'])
    grant = normalize(prepared)
    data = {**prepared['inputs']['package_manifests'], 'package-lock.json': prepared['inputs']['lockfile_bytes']}
    data.update({'apps/desktop/tsconfig.json': b'{}', 'apps/desktop/vitest.config.ts': b'{}',
                 'apps/desktop/src/main.test.tsx': b'reviewed fixture', 'apps/desktop/index.html': b'<html></html>',
                 'apps/desktop/vite.config.ts': b'export default {}'})
    snapshot = ProjectSnapshotBundle('root0', prepared['inputs']['root_identity'],
        tuple(ProjectSnapshotFile(path, data[path], execution_only=path in grant['source']['executionOnlyHashes'])
              for path in grant['source']['files']), ProjectSnapshotLimits(**grant['source']['limits']))
    policy = conn.execute('SELECT generation,fingerprint FROM organization_policy').fetchone()
    ctx = {**context(prepared), 'objective_id': identifier, 'round_number': 0,
           'objective_binding_sha256': bound['objectiveBindingSha256'],
           'policy_generation': policy[0], 'policy_fingerprint': policy[1], 'snapshot_sha256': snapshot.snapshot_sha256}
    return {'ctx': ctx, 'snapshot': snapshot}


def proposal_v2(state, obj, generation=1):
    value = proposal(state, obj, generation)
    value.update(contract.versioned_contract_fields(2, 'prepared-validation-set'))
    return value


def test_v2_requires_exact_three_recipes_and_every_reference_shares_one_identity(prepared_v2, prepared):
    ctx = context(prepared_v2)
    value = versioned_envelope(ctx)
    assert profile_required_checks(ctx['profile']) == ['project_typecheck', 'project_tests', 'project_build']
    assert [s['recipe'] for s in contract.validation_execution_order(value)] == [
        'workspace_typescript', 'workspace_vitest_ui', 'workspace_vite_build']
    inspected = contract.inspect_latest_validation_contract([value], context=ctx, **versioned_evidence(value))
    assert inspected['aggregate']['version'] == 2
    assert inspected['acceptanceAuthorized'] is inspected['runtimeReceiptsVerified'] is False
    for key in ('sourceSha256', 'profileSha256', 'bundleSha256', 'authoritySha256'):
        result_key = 'snapshotSha256' if key == 'sourceSha256' else key
        assert {result[result_key] for result in inspected['aggregate']['results']} == {value['identity'][key]}
    assert validation_step_count(normalize(prepared)['profile']) == 2
    mutations = [lambda p: p.update(version=1), lambda p: p['steps'].pop(),
                 lambda p: p['steps'].append(deepcopy(p['steps'][-1])),
                 lambda p: p['steps'][-1].update(testFiles=['apps/desktop/src/main.test.tsx']),
                 lambda p: p['steps'][-1].update(sourceDirectories=['apps/desktop/src'])]
    for mutation in mutations:
        candidate = deepcopy(prepared_v2['contract']['profile'])
        mutation(candidate)
        with pytest.raises(ValueError):
            normalize_validation_profile(candidate)
    records = versioned_evidence(value)
    records['results'][-1]['profileSha256'] = sha(b'another profile')
    with pytest.raises(ValueError, match='mixes'):
        contract.inspect_latest_validation_contract([value], context=ctx, **records)
    records = versioned_evidence(value)
    records['results'].pop()
    with pytest.raises(ValueError, match='every versioned'):
        contract.inspect_latest_validation_contract([value], context=ctx, **records)


@pytest.mark.parametrize('missing', ['entry', 'tool'])
def test_v2_requires_selected_renderer_entry_and_prepared_vite(prepared_v2, missing):
    if missing == 'entry':
        prepared_v2['contract']['source']['files'].remove('apps/desktop/index.html')
    else:
        dep = prepared_v2['contract']['dependencies']
        dep['manifest']['files'] = [item for item in dep['manifest']['files']
                                    if item['path'] != 'node_modules/vite/dist/node/index.js']
        checked = normalize_prepared_dependency_manifest(dep['manifest'], lockfile_bytes=prepared_v2['inputs']['lockfile_bytes'],
            package_manifests=prepared_v2['inputs']['package_manifests'], expected_toolchain=dep['toolchain'])
        dep.update({key: checked[key] for key in ('manifest', 'bundleSha256', 'inventorySha256', 'lockfileSha256')})
        prepared_v2['contract']['profile']['bundleSha256'] = checked['bundleSha256']
    with pytest.raises(ValueError, match='omits|entry points'):
        normalize(prepared_v2)


def test_v2_reserves_all_three_atomically_and_keeps_prior_legacy_usage(state_v2):
    obj = objective_v2(state_v2, max_runs=4)
    legacy_run(state_v2, obj)
    value = proposal_v2(state_v2, obj)
    conn = state_v2['conn']
    conn.executescript("CREATE TRIGGER omit_build BEFORE INSERT ON prepared_validation_steps "
                      "WHEN NEW.recipe='workspace_vite_build' BEGIN SELECT RAISE(IGNORE); END;")
    with pytest.raises(sqlite3.IntegrityError, match='FOREIGN KEY'):
        reserve(state_v2, obj, value, owner_ceiling=4)
    assert conn.execute('SELECT count(*) FROM prepared_validation_sets').fetchone()[0] == 0
    assert conn.execute('SELECT count(*) FROM prepared_validation_steps').fetchone()[0] == 0
    assert conn.execute('SELECT count(*) FROM project_run_starts').fetchone()[0] == 1
    conn.execute('DROP TRIGGER omit_build')
    result = reserve(state_v2, obj, value, owner_ceiling=4)
    assert (result['priorUnits'], result['reservedUnits'], result['resultingUnits']) == (1, 3, 4)
    row = conn.execute('SELECT typecheck_run_id,tests_run_id,build_run_id FROM prepared_validation_sets').fetchone()
    assert tuple(row) == tuple(step['runId'] for step in contract.validation_execution_order(value))
    assert conn.execute('PRAGMA foreign_key_check').fetchone() is None
    assert ledger.reconstruct_prepared_snapshot(conn, value['setId']) == obj['snapshot']


@pytest.mark.parametrize('owner,objective', [(3, 20), (20, 3)])
def test_v2_rejects_pair_sized_remaining_allowance_without_partial_charge(state_v2, owner, objective):
    obj = objective_v2(state_v2, max_runs=objective)
    legacy_run(state_v2, obj)
    value = proposal_v2(state_v2, obj)
    with pytest.raises(ValueError, match='ceiling'):
        reserve(state_v2, obj, value, owner_ceiling=owner)
    assert state_v2['conn'].execute('SELECT count(*) FROM prepared_validation_steps').fetchone()[0] == 0


def test_mixed_contract_history_keeps_every_old_charge_and_newest_failure(prepared, prepared_v2):
    old_ctx, ctx = context(prepared), context(prepared_v2)
    old, latest = versioned_envelope(old_ctx), versioned_envelope(ctx, 2)
    plan = contract.plan_validation_reservation(latest, history=[old], used_units=3,
        objective_ceiling=6, owner_ceiling=6, context=ctx)
    assert plan['priorUnits'] == 3 and plan['reservedUnits'] == 3 and plan['resultingUnits'] == 6
    with pytest.raises(ValueError, match='prior usage'):
        contract.plan_validation_reservation(latest, history=[old], used_units=1,
            objective_ceiling=6, owner_ceiling=6, context=ctx)
    records = versioned_evidence(latest)
    records['results'][-1]['status'] = 'failed'
    with pytest.raises(ValueError, match='failed'):
        contract.inspect_latest_validation_contract([old, latest], context=ctx, **records)


def test_three_unit_shared_goal_admission_keeps_existing_pair_and_ceiling(state_v2, prepared):
    import time
    from tests.eidolon_cli.test_organization_prepared_validation_ledger import objective
    old_state = {**state_v2, 'prepared': prepared}
    old_obj = objective(old_state)
    old_set = proposal(old_state, old_obj)
    reserve(old_state, old_obj, old_set)
    obj = objective_v2(state_v2)
    limits = {'modelCalls': 10, 'tokens': 10000, 'costUsd': None, 'stages': 100, 'projectRuns': 4,
              'deadlineTimestamp': int(time.time()) + 3600,
              'completionReserve': {'modelCalls': 0, 'tokens': 0, 'costUsd': None}}
    state_v2['store'].configure_goal_allowance('shared-three-check-goal',
        objective_ids=[old_obj['ctx']['objective_id'], obj['ctx']['objective_id']], limits=limits,
        paused=False, expected_revision=0, idempotency_key='shared-three-check-goal', confirmed=True)
    conn = state_v2['conn']
    before_goal = [tuple(row) for row in conn.execute('SELECT * FROM organization_goal_revisions')]
    before_steps = [tuple(row) for row in conn.execute('SELECT * FROM prepared_validation_steps ORDER BY run_id')]
    with pytest.raises(ValueError, match='Shared goal'):
        reserve(state_v2, obj, proposal_v2(state_v2, obj))
    assert [tuple(row) for row in conn.execute('SELECT * FROM organization_goal_revisions')] == before_goal
    assert [tuple(row) for row in conn.execute('SELECT * FROM prepared_validation_steps ORDER BY run_id')] == before_steps


@pytest.mark.parametrize('collision', ['previous_build', 'pending_build', 'pending_typecheck', 'borrow_pending_build'])
def test_raw_sql_cannot_borrow_other_sets_build_reservation(state_v2, collision):
    obj = objective_v2(state_v2)
    first = proposal_v2(state_v2, obj)
    reserve(state_v2, obj, first)
    second = proposal_v2(state_v2, obj, 2)
    third = proposal_v2(state_v2, obj, 3)
    conn = state_v2['conn']
    columns = [row[1] for row in conn.execute('PRAGMA table_info(prepared_validation_sets)')]
    template = dict(conn.execute('SELECT ' + ','.join(columns) + ' FROM prepared_validation_sets').fetchone())
    def insert(value):
        ordered = contract.validation_execution_order(value)
        row = {**template, 'id': value['setId'], 'request_id': value['requestId'], 'lease_token': value['requestId'],
               'generation': value['generation'], 'envelope': canonical(value), 'envelope_sha256': digest(value),
               'typecheck_run_id': ordered[0]['runId'], 'tests_run_id': ordered[1]['runId']}
        conn.execute('INSERT INTO prepared_validation_sets (' + ','.join(columns) + ') VALUES (' +
                     ','.join('?' for _ in columns) + ')', tuple(row[key] for key in columns))
    conn.execute('BEGIN')
    try:
        if collision == 'previous_build':
            prior = contract.validation_execution_order(first)
            target = second
            build = next(step for step in target['steps'] if step['recipe'] == 'workspace_vite_build')
            build['runId'] = prior[2]['runId']
        else:
            insert(second)
            prior = contract.validation_execution_order(second)
            target = third
            if collision == 'borrow_pending_build':
                next(step for step in target['steps'] if step['recipe'] == 'workspace_typescript')['runId'] = prior[2]['runId']
            else:
                next(step for step in target['steps'] if step['recipe'] == 'workspace_vite_build')['runId'] = prior[
                    0 if collision == 'pending_typecheck' else 2]['runId']
        with pytest.raises(sqlite3.IntegrityError, match='build identity'):
            insert(target)
    finally:
        conn.rollback()
    assert conn.execute('SELECT count(*) FROM prepared_validation_sets').fetchone()[0] == 1
    assert conn.execute('PRAGMA foreign_key_check').fetchone() is None
