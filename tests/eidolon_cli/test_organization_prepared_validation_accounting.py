"""Cross-feature real-SQL accounting; these tests never dispatch a runtime."""
from dataclasses import asdict, replace
from pathlib import Path
import json
import threading
import time

import pytest

from eidolon_cli import organization_prepared_validation_activity as activity
from eidolon_cli import organization_prepared_validation_ledger as ledger
from eidolon_cli.organization_goal_allowances import goal_projection, require_goal_admission
from eidolon_cli.organization_project_validation import canonical, digest
from eidolon_cli.organization_project_config import parse_project_grants
from eidolon_cli.organization_projects import parse_projects
from eidolon_cli.organization_project_snapshot_bundle import ProjectSnapshotBundle, ProjectSnapshotFile, ProjectSnapshotLimits
from tests.eidolon_cli.test_organization_prepared_validation_contract import context, normalize
from eidolon_cli.organization_roster import OrganizationStaff
from eidolon_cli.organization_store import OrganizationStore
from tests.eidolon_cli.test_organization_goal_allowances import limits
from tests.eidolon_cli.test_organization_prepared_validation_activity import mutate
from tests.eidolon_cli.test_organization_prepared_validation_ledger import (
    claims, legacy_run, objective, prepared, proposal, reserve, sha, state,  # noqa: F401
)


def configure_goal(state, objects, units):
    state['store'].configure_goal_allowance('shared', objective_ids=[obj['ctx']['objective_id'] for obj in objects],
        limits=limits(projectRuns=units), paused=False, expected_revision=0, idempotency_key='shared', confirmed=True)


def legacy_authority(state, *, owner_ceiling=20):
    """Retain real staff policy; no grant/ownership method is mocked."""
    original = state['store'].settings
    legacy_root = Path(original.read_roots[0]).parent / 'legacy-source'
    legacy_root.mkdir()
    roots = (*original.read_roots, str(legacy_root))
    grants = parse_project_grants([*[{**asdict(grant), 'files': list(grant.files)} for grant in original.project_grants],
        {'id': 'legacy-recipe', 'files': ['root1/example.py'], 'execution': {'root': 'root1'}}], len(roots))
    projects = parse_projects([*[asdict(project) for project in original.projects],
        {'id': 'legacy', 'root': 'root1', 'recipe': 'legacy-recipe', 'team': 'engineering'}], roots, grants)
    settings = replace(original, read_roots=roots, project_grants=grants, projects=projects,
        max_project_runs=owner_ceiling, capabilities=('work.inspect',),
        tool_grants=('read_file', 'run_tests'), roster=(OrganizationStaff('inspector', 'Inspector', 'engineering',
            ('work.inspect',), tool_grants=('read_file', 'run_tests')),))
    state['store'] = OrganizationStore(state['store'].path, settings)
    state['conn'].execute("UPDATE staff_state SET active=1 WHERE agent_id='inspector'")


def mixed_objective(state, *, max_runs=20):
    """Select both projects canonically; only desktop has an inert prepared pair."""
    store, conn, prepared = state['store'], state['conn'], state['prepared']
    with store._write() as write:
        identifier = store._create_objective(write, 'Mixed prepared and legacy fixture', idempotency_key='mixed-objective',
            project_ids=['desktop', 'legacy'], delivery_mode='managed_artifact', required_checks=[], project_run_limit=max_runs)
    bound = ledger.bind_prepared_validation_proposal(conn, objective_id=identifier,
        value=prepared['contract'], context=prepared['inputs'])
    grant = normalize(prepared)
    data = {**prepared['inputs']['package_manifests'], 'package-lock.json': prepared['inputs']['lockfile_bytes']}
    data.update({'apps/desktop/tsconfig.json': b'{}', 'apps/desktop/vitest.config.ts': b'{}',
                 'apps/desktop/src/main.test.tsx': b'reviewed fixture'})
    snapshot = ProjectSnapshotBundle('root0', prepared['inputs']['root_identity'],
        tuple(ProjectSnapshotFile(path, data[path], execution_only=path in grant['source']['executionOnlyHashes'])
              for path in grant['source']['files']), ProjectSnapshotLimits(**grant['source']['limits']))
    policy = conn.execute('SELECT generation,fingerprint FROM organization_policy').fetchone()
    ctx = {**context(prepared), 'objective_id': identifier, 'round_number': 0,
           'objective_binding_sha256': bound['objectiveBindingSha256'],
           'policy_generation': policy[0], 'policy_fingerprint': policy[1],
           'snapshot_sha256': snapshot.snapshot_sha256}
    return {'ctx': ctx, 'snapshot': snapshot}


def legacy_claim(state, obj, *, project_id='legacy', expected_unavailability=None):
    """Canonical fixture rows isolate admission from unrelated task planning."""
    conn, store = state['conn'], state['store']
    ident = obj['ctx']['objective_id']
    task_id = 'inspected-' + ident
    conn.execute('INSERT INTO tasks(id,objective_id,title,description,type,team,priority,status,dependencies,author_id) '
        "VALUES (?,?,'Inspect','Inspection fixture','work.inspect','engineering',1,'completed','[]','inspector')", (task_id, ident))
    conn.execute('INSERT INTO objective_task_rounds VALUES (?,?,0)', (task_id, ident))
    conn.execute('INSERT INTO task_projects VALUES (?,?,?)', (task_id, ident, project_id))
    value = proposal(state, obj, suffix='-legacy-candidate')
    conn.execute('UPDATE requests SET payload=? WHERE id=?',
                 (canonical({'projectId': project_id, 'round': 0}), value['requestId']))
    with store._write() as write:
        store._stamp_claim_policy(write, value['requestId'])
        write.execute("UPDATE requests SET status='running',agent_id='control:project',token='legacy-candidate',lease=? WHERE id=?",
                      (time.time() + 3600, value['requestId']))
        claim = dict(write.execute('SELECT * FROM requests WHERE id=?', (value['requestId'],)).fetchone())
    assert store.project_stage_unavailability(conn, claim) == expected_unavailability
    return claim


@pytest.mark.parametrize('prior_legacy', [False, True])
@pytest.mark.parametrize('outcome', ['incomplete', 'failed', 'unknown', 'corrupt'])
def test_prepared_pair_blocks_legacy_execution_without_refunding_after_restart(state, prior_legacy, outcome):
    legacy_authority(state)
    obj = mixed_objective(state, max_runs=2 + int(prior_legacy))
    if prior_legacy:
        legacy_run(state, obj)
    claim = legacy_claim(state, obj)
    value = proposal(state, obj)
    reserve(state, obj, value)
    if outcome != 'incomplete':
        claims(state, value, fail=True, review=False)
    if outcome == 'unknown':
        for row in state['conn'].execute('SELECT run_id,record FROM prepared_validation_results').fetchall():
            result = {**json.loads(row['record']), 'status': 'unknown'}
            mutate(state['conn'], 'prepared_validation_results',
                'UPDATE prepared_validation_results SET status=?,record=?,sha256=? WHERE run_id=?',
                ('unknown', canonical(result), digest(result), row['run_id']))
        mutate(state['conn'], 'prepared_validation_aggregates', 'DELETE FROM prepared_validation_aggregates')
    if outcome == 'corrupt':
        mutate(state['conn'], 'prepared_validation_results', 'UPDATE prepared_validation_results SET record=?', ('not-json',))
    total = 2 + int(prior_legacy)
    assert ledger._charged_units(state['conn'], obj['ctx']['objective_id']) == total
    reopened = OrganizationStore(state['store'].path, state['store'].settings)
    assert 'run budget exhausted' in reopened.project_stage_unavailability(state['conn'], claim)
    with pytest.raises(ValueError, match='run budget exhausted'):
        reopened.run_project_stage(claim, threading.Event())
    assert state['conn'].execute('SELECT count(*) FROM project_run_starts').fetchone()[0] == int(prior_legacy)
    assert activity.objective_project_reserved_units(state['conn'], obj['ctx']['objective_id'])['totalUnits'] == total
    if outcome == 'corrupt':
        with pytest.raises(activity.PreparedValidationProjectionError):
            activity.prepared_validation_blockers(state['conn'], obj['ctx']['objective_id'])


def test_prepared_charges_also_exhaust_current_owner_legacy_ceiling(state):
    legacy_authority(state, owner_ceiling=2)
    obj = mixed_objective(state, max_runs=20)
    claim = legacy_claim(state, obj)
    reserve(state, obj, proposal(state, obj), owner_ceiling=2)
    assert 'run budget exhausted' in state['store'].project_stage_unavailability(state['conn'], claim)


def test_shared_ceiling_rejects_complete_pair_atomically_after_legacy_and_prepared_use(state):
    left, right = objective(state), objective(state)
    configure_goal(state, [left, right], 4)
    legacy_run(state, left)
    first = proposal(state, left)
    reserve(state, left, first)
    claims(state, first, fail=True, review=False)
    assert goal_projection(state['conn'], 'shared')['reserved']['projectRuns'] == 3
    before = {table: state['conn'].execute('SELECT count(*) FROM ' + table).fetchone()[0]
              for table in ledger.PREPARED_VALIDATION_TABLES}
    with pytest.raises(ValueError, match='both prepared execution units'):
        reserve(state, right, proposal(state, right))
    assert before == {table: state['conn'].execute('SELECT count(*) FROM ' + table).fetchone()[0]
                      for table in ledger.PREPARED_VALIDATION_TABLES}
    assert ledger._charged_units(state['conn'], right['ctx']['objective_id']) == 0
    assert goal_projection(state['conn'], 'shared')['reserved']['projectRuns'] == 3


def test_two_pairs_fit_exact_goal_ceiling_without_double_counting_old_prepared_units(state):
    left, right = objective(state), objective(state)
    configure_goal(state, [left, right], 4)
    reserve(state, left, proposal(state, left))
    reserve(state, right, proposal(state, right))
    assert goal_projection(state['conn'], 'shared')['reserved']['projectRuns'] == 4
    with pytest.raises(ValueError, match='projectRuns reservation ceiling exhausted'):
        require_goal_admission(state['conn'], left['ctx']['objective_id'], project_run=True)
    assert state['conn'].execute('SELECT count(*) FROM prepared_validation_sets').fetchone()[0] == 2
    assert state['conn'].execute('SELECT count(*) FROM prepared_validation_steps').fetchone()[0] == 4


def test_corrupt_prepared_receipt_cannot_refund_shared_goal_units(state):
    obj = objective(state)
    configure_goal(state, [obj], 2)
    value = proposal(state, obj)
    reserve(state, obj, value)
    claims(state, value, fail=True, review=False)
    mutate(state['conn'], 'prepared_validation_results', 'UPDATE prepared_validation_results SET sha256=?', (sha(b'corrupt'),))
    assert goal_projection(state['conn'], 'shared')['reserved']['projectRuns'] == 2
    with pytest.raises(ValueError, match='projectRuns reservation ceiling exhausted'):
        require_goal_admission(state['conn'], obj['ctx']['objective_id'], project_run=True)


def test_legacy_only_goal_counts_and_empty_stored_goals_are_unchanged(state):
    obj = objective(state)
    configure_goal(state, [obj], 4)
    legacy_run(state, obj)
    tables = [row[0] for row in state['conn'].execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'prepared_validation_%'")]
    for table in tables:
        for row in state['conn'].execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (table,)).fetchall():
            state['conn'].execute('DROP TRIGGER ' + row[0])
    state['conn'].execute('PRAGMA foreign_keys=OFF')
    for table in tables:
        state['conn'].execute('DROP TABLE ' + table)
    state['conn'].execute('PRAGMA foreign_keys=ON')
    assert activity.prepared_validation_storage_state(state['conn']) == 'absent'
    assert goal_projection(state['conn'], 'shared')['reserved']['projectRuns'] == 1
    assert ledger._charged_units(state['conn'], obj['ctx']['objective_id']) == 1
    require_goal_admission(state['conn'], obj['ctx']['objective_id'], project_run=True)
    from eidolon_cli.organization_goal_allowances import _hash
    value = {'id': 'empty', 'revision': 1, 'limits': limits(), 'paused': False}
    state['conn'].execute('INSERT INTO organization_goal_revisions VALUES (?,?,?,?,?)',
                         ('empty', 1, json.dumps(value, sort_keys=True), _hash(value), time.time()))
    assert goal_projection(state['conn'], 'empty')['reserved']['projectRuns'] == 0


@pytest.mark.parametrize('change', ['paused', 'expired', 'unknown_usage'])
def test_existing_goal_admission_denials_remain_before_new_pair(state, change):
    obj = objective(state)
    value = limits(projectRuns=4, deadlineTimestamp=int(time.time()) + (-1 if change == 'expired' else 3600))
    state['store'].configure_goal_allowance('shared', objective_ids=[obj['ctx']['objective_id']], limits=value,
        paused=change == 'paused', expected_revision=0, idempotency_key='shared', confirmed=True)
    if change == 'unknown_usage':
        state['conn'].execute('UPDATE objective_budgets SET legacy_usage_unknown=1 WHERE objective_id=?', (obj['ctx']['objective_id'],))
    with pytest.raises(ValueError, match='paused|deadline|unknown historical'):
        reserve(state, obj, proposal(state, obj))
    assert state['conn'].execute('SELECT count(*) FROM prepared_validation_sets').fetchone()[0] == 0


def test_partial_prepared_authority_is_not_a_legacy_budget_route(state):
    legacy_authority(state)
    obj = objective(state)
    reason = 'Prepared objective selection has incomplete retained authority'
    claim = legacy_claim(state, obj, project_id='desktop', expected_unavailability=reason)
    assert ledger._charged_units(state['conn'], obj['ctx']['objective_id']) == 0
    with pytest.raises(ValueError, match='incomplete retained authority'):
        state['store'].run_project_stage(claim, threading.Event())
    assert state['conn'].execute('SELECT count(*) FROM project_run_starts').fetchone()[0] == 0
