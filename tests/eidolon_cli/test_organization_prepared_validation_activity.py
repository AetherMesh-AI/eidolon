"""Real SQLite, metadata-only prepared accounting/activity projections."""
import json
import sqlite3
import time

import pytest

from eidolon_cli import organization_prepared_validation_activity as activity
from eidolon_cli import organization_prepared_validation_ledger as ledger
from eidolon_cli.organization_project_validation import canonical, digest
from eidolon_cli.organization_store import OrganizationStore
from tests.eidolon_cli.test_organization_prepared_validation_ledger import (
    approve_claim, claims, legacy_run, objective, prepared, proposal, reserve, sha, state,  # noqa: F401
)


def usage(state, obj):
    return activity.objective_project_reserved_units(state['conn'], obj['ctx']['objective_id'])


def projection(state, obj, **kwargs):
    return activity.prepared_validation_activity(state['conn'], obj['ctx']['objective_id'], **kwargs)


def mutate(conn, table, sql, args=()):
    """Simulate stored corruption, restoring exact guards before the read."""
    triggers = conn.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (table,)).fetchall()
    for row in triggers:
        conn.execute('DROP TRIGGER ' + row[0])
    conn.execute('PRAGMA foreign_keys=OFF')
    conn.execute('PRAGMA ignore_check_constraints=ON')
    conn.execute(sql, args)
    conn.execute('PRAGMA ignore_check_constraints=OFF')
    conn.execute('PRAGMA foreign_keys=ON')
    for row in triggers:
        conn.execute(row[1])


def goal(state, objects, revision=0):
    limits = {'modelCalls': 10, 'tokens': 10000, 'costUsd': None, 'stages': 100, 'projectRuns': 20,
              'deadlineTimestamp': int(time.time()) + 3600,
              'completionReserve': {'modelCalls': 0, 'tokens': 0, 'costUsd': None}}
    state['store'].configure_goal_allowance('goal', objective_ids=[obj['ctx']['objective_id'] for obj in objects],
        limits=limits, paused=False, expected_revision=revision, idempotency_key='goal-key-' + str(revision), confirmed=True)


def test_legacy_only_absence_does_not_register_schema(state, tmp_path):
    store = OrganizationStore(tmp_path / 'ordinary.db', state['store'].settings)
    with store._write() as conn:
        ident = store._create_objective(conn, 'Legacy objective', idempotency_key='legacy-only',
                                       project_ids=['desktop'], delivery_mode='managed_artifact', required_checks=[])
    with store._connect(write=True) as conn:
        # Model an actual pre-migration store, not a freshly migrated store.
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'prepared_validation_%'")]
        for table in tables:
            assert conn.execute('SELECT count(*) FROM ' + table).fetchone()[0] == 0
            for guard in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (table,)).fetchall():
                conn.execute('DROP TRIGGER ' + guard[0])
        conn.execute('PRAGMA foreign_keys=OFF')
        for table in tables:
            conn.execute('DROP TABLE ' + table)
        conn.execute('PRAGMA foreign_keys=ON')
        before = list(conn.execute('SELECT name,sql FROM sqlite_master'))
        assert activity.prepared_validation_storage_state(conn) == 'absent'
        result = activity.prepared_validation_activity(conn, ident)
        assert result['legacyUnits'] == result['preparedUnits'] == result['totalUnits'] == 0
        assert result['current'] == {} and result['history'] == []
        assert not result['blockers']['blocked']
        assert before == list(conn.execute('SELECT name,sql FROM sqlite_master'))


def test_both_units_per_set_survive_failure_incomplete_and_unstarted_work(state):
    obj = objective(state)
    legacy_run(state, obj)
    first = proposal(state, obj)
    reserve(state, obj, first)
    claims(state, first, fail=True)
    second = proposal(state, obj, 2)
    reserve(state, obj, second)
    assert {key: usage(state, obj)[key] for key in ('legacyUnits', 'preparedUnits', 'totalUnits')} == {
        'legacyUnits': 1, 'preparedUnits': 4, 'totalUnits': 5}
    result = projection(state, obj)
    assert result['current']['desktop']['setId'] == second['setId']
    assert result['current']['desktop']['state'] == 'reserved_undispatched'
    assert len(result['history']) == 2 and result['blockers']['totalBlockers'] == 4
    assert {step['blockerReasons'][0] for step in result['current']['desktop']['steps']} == {'reserved_undispatched'}
    assert not result['runtimeReceiptsVerified'] and not result['acceptanceAuthorized']


def test_latest_incomplete_blocks_older_approved_claim_without_fallback(state):
    obj = objective(state)
    first = proposal(state, obj)
    reserve(state, obj, first)
    approve_claim(state, obj, first)
    second = proposal(state, obj, 2)
    reserve(state, obj, second)
    result = projection(state, obj, history_limit=1)
    assert result['current']['desktop']['setId'] == second['setId']
    assert result['history'][0]['setId'] == second['setId']
    assert result['historyTruncated'] and result['totalSets'] == 2
    assert result['blockers']['blocked'] and result['blockers']['totalBlockers'] == 4
    assert result['blockers']['truncated']


def test_all_claims_approved_are_still_runtime_unverified_and_redacted(state):
    obj = objective(state)
    value = proposal(state, obj)
    reserve(state, obj, value)
    approve_claim(state, obj, value)
    result = projection(state, obj)
    latest = result['current']['desktop']
    assert latest['reviewClaimPresent'] and latest['aggregateClaimPresent']
    assert all(step['cleanupClaimSettled'] for step in latest['steps'])
    assert all(step['blockerReasons'] == ['runtime_receipt_unverified'] for step in latest['steps'])
    assert all(result[key] is False for key in ('sourceBytesVerified', 'runtimeReceiptsVerified', 'executionAuthorized',
                                               'acceptanceAuthorized', 'sourceDeliveryAuthorized'))
    serialized = json.dumps(result)
    assert 'private-lease-token-never-export' not in serialized
    assert 'reviewed fixture' not in serialized and 'parentDevice' not in serialized
    assert state['prepared']['contract']['dependencies']['directory'] not in serialized


def test_cleanup_false_then_true_settles_only_metadata_and_never_upgrades_failure(state):
    obj = objective(state)
    value = proposal(state, obj)
    reserve(state, obj, value)
    claims(state, value, fail=True, review=False, cleanup_confirmed=False)
    run = next(step['runId'] for step in value['steps'] if step['recipe'] == 'workspace_vitest_ui')
    ledger.record_prepared_cleanup_claim(state['conn'], run, event_id='reconciled', nonce=sha(('nonce-' + run).encode()),
        container_id=sha(('container-' + run).encode()), container_confirmed=True, staging_confirmed=True)
    result = projection(state, obj)
    step = next(item for item in result['current']['desktop']['steps'] if item['runId'] == run)
    assert step['cleanupClaimSettled'] and step['latestCleanupSequence'] == 2
    assert step['resultClaimStatus'] == 'failed'
    assert 'validation_failed' in step['blockerReasons'] and 'result_cleanup_unconfirmed' in step['blockerReasons']
    assert 'runtime_receipt_unverified' in step['blockerReasons']
    assert state['conn'].execute('SELECT container_confirmed FROM prepared_validation_cleanups WHERE run_id=? ORDER BY sequence', (run,)).fetchall()[0][0] == 0
    assert usage(state, obj)['preparedUnits'] == 2


def test_latest_false_cleanup_blocks_an_earlier_confirmed_claim(state):
    obj = objective(state)
    value = proposal(state, obj)
    reserve(state, obj, value)
    approve_claim(state, obj, value)
    run = value['steps'][0]['runId']
    ledger.record_prepared_cleanup_claim(state['conn'], run, event_id='uncertain-again', nonce=sha(('nonce-' + run).encode()),
        container_id=sha(('container-' + run).encode()), container_confirmed=True, staging_confirmed=False)
    step = next(item for item in projection(state, obj)['current']['desktop']['steps'] if item['runId'] == run)
    assert not step['cleanupClaimSettled']
    assert 'resource_cleanup_unconfirmed' in step['blockerReasons']
    assert step['resultClaimStatus'] == 'passed'


def test_dispatch_without_observed_container_and_unknown_cleanup_stays_uncertain(state):
    obj = objective(state)
    value = proposal(state, obj)
    reserve(state, obj, value)
    run = value['steps'][0]['runId']
    nonce = sha(b'dispatch')
    ledger.record_prepared_dispatch_proposal(state['conn'], run, nonce=nonce)
    ledger.record_prepared_cleanup_claim(state['conn'], run, event_id='unknown-resource', nonce=nonce,
        container_id=None, container_confirmed=True, staging_confirmed=True)
    result = activity.prepared_validation_blockers(state['conn'], obj['ctx']['objective_id'])
    entry = next(item for item in result['entries'] if item['runId'] == run)
    assert set(entry['reasons']) == {'runtime_receipt_unverified', 'execution_outcome_unknown', 'resource_cleanup_unconfirmed'}
    assert result['totalBlockers'] == 2


def test_goal_membership_counts_legacy_and_prepared_once_across_revisions(state):
    left, right = objective(state), objective(state)
    goal(state, [left, right])
    legacy_run(state, left)
    first = proposal(state, left)
    reserve(state, left, first)
    claims(state, first, fail=True, review=False)
    reserve(state, right, proposal(state, right))
    goal(state, [left, right], revision=1)
    projected = activity.goal_project_reserved_units(state['conn'], 'goal')
    assert projected['legacyUnits'] == 1 and projected['preparedUnits'] == 4 and projected['totalUnits'] == 5
    assert set(projected['objectiveIds']) == {left['ctx']['objective_id'], right['ctx']['objective_id']}
    assert projected['totalUnits'] == usage(state, left)['totalUnits'] + usage(state, right)['totalUnits']


def test_read_projection_never_commits_or_rolls_back_callers_transaction(state):
    obj = objective(state)
    conn = state['conn']
    conn.execute('BEGIN IMMEDIATE')
    conn.execute("INSERT INTO agents VALUES ('uncommitted','New','Worker',NULL,'engineering','[]')")
    assert usage(state, obj)['totalUnits'] == 0 and conn.in_transaction
    with pytest.raises(activity.PreparedValidationProjectionError):
        activity.prepared_validation_activity(conn, 'missing-objective')
    assert conn.in_transaction and conn.execute("SELECT 1 FROM agents WHERE id='uncommitted'").fetchone()
    conn.rollback()
    assert conn.execute("SELECT 1 FROM agents WHERE id='uncommitted'").fetchone() is None
    assert usage(state, obj)['totalUnits'] == 0 and not conn.in_transaction


def test_read_only_connection_succeeds_without_mutations(state):
    obj = objective(state)
    reserve(state, obj, proposal(state, obj))
    with sqlite3.connect('file:' + str(state['store'].path) + '?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        before = conn.total_changes
        assert activity.objective_project_reserved_units(conn, obj['ctx']['objective_id'])['preparedUnits'] == 2
        assert conn.total_changes == before


@pytest.mark.parametrize('schema_change', ['drop_table', 'drop_guard', 'replace_guard', 'view', 'wrong_column', 'temp_shadow'])
def test_partial_or_malformed_schema_fails_closed(state, schema_change):
    obj = objective(state)
    conn = state['conn']
    if schema_change == 'drop_table':
        conn.execute('DROP TABLE prepared_validation_cleanups')
    elif schema_change == 'drop_guard':
        conn.execute('DROP TRIGGER immutable_prepared_validation_sets_delete')
    elif schema_change == 'replace_guard':
        conn.execute('DROP TRIGGER immutable_prepared_validation_sets_delete')
        conn.execute('CREATE TRIGGER immutable_prepared_validation_sets_delete BEFORE DELETE ON prepared_validation_sets BEGIN SELECT 1; END')
    elif schema_change == 'view':
        conn.execute('DROP TABLE prepared_validation_cleanups')
        conn.execute('CREATE VIEW prepared_validation_cleanups AS SELECT 1')
    elif schema_change == 'wrong_column':
        conn.execute('ALTER TABLE prepared_validation_cleanups RENAME COLUMN sequence TO event_order')
    else:
        conn.execute('CREATE TEMP TABLE prepared_validation_sets (id TEXT)')
    with pytest.raises(activity.PreparedValidationProjectionError):
        usage(state, obj)


def test_additive_tables_and_columns_are_allowed_but_not_evidence(state):
    obj = objective(state)
    value = proposal(state, obj)
    reserve(state, obj, value)
    state['conn'].execute('CREATE TABLE prepared_validation_future_claims (receipt TEXT)')
    state['conn'].execute('INSERT INTO prepared_validation_future_claims VALUES (?)', ('{"verified":true}',))
    state['conn'].execute('ALTER TABLE prepared_validation_sets ADD COLUMN future_metadata TEXT')
    state['conn'].execute('CREATE TABLE unrelated (value TEXT)')
    assert activity.prepared_validation_storage_state(state['conn']) == 'present'
    assert projection(state, obj)['blockers']['blocked']


def test_extra_prepared_table_alone_is_not_legacy_only():
    with sqlite3.connect(':memory:') as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('CREATE TABLE prepared_validation_future_claims (receipt TEXT)')
        with pytest.raises(activity.PreparedValidationProjectionError):
            activity.prepared_validation_storage_state(conn)


@pytest.mark.parametrize('target', ['result_hash', 'result_identity', 'result_boolean', 'cleanup_sequence', 'cleanup_resource',
                                     'missing_step', 'envelope_hash', 'file_length', 'file_hash', 'missing_file', 'aggregate', 'review'])
def test_corrupt_history_fails_closed_even_when_display_would_truncate_it(state, target):
    obj = objective(state)
    value = proposal(state, obj)
    reserve(state, obj, value)
    approve_claim(state, obj, value)
    reserve(state, obj, proposal(state, obj, 2))
    conn = state['conn']
    run = value['steps'][0]['runId']
    if target.startswith('result'):
        result = json.loads(conn.execute('SELECT record FROM prepared_validation_results WHERE run_id=?', (run,)).fetchone()[0])
        if target == 'result_identity':
            result['snapshotSha256'] = sha(b'other')
        if target == 'result_boolean':
            result['cleanupConfirmed'] = 1
        mutate(conn, 'prepared_validation_results', 'UPDATE prepared_validation_results SET record=?,sha256=? WHERE run_id=?',
               (canonical(result), sha(b'wrong') if target == 'result_hash' else digest(result), run))
    elif target.startswith('cleanup'):
        column, changed = ('sequence', 4) if target == 'cleanup_sequence' else ('container_id', sha(b'wrong'))
        mutate(conn, 'prepared_validation_cleanups', 'UPDATE prepared_validation_cleanups SET ' + column + '=? WHERE run_id=?', (changed, run))
    elif target == 'missing_step':
        mutate(conn, 'prepared_validation_steps', 'DELETE FROM prepared_validation_steps WHERE run_id=?', (run,))
    elif target == 'envelope_hash':
        mutate(conn, 'prepared_validation_sets', 'UPDATE prepared_validation_sets SET envelope_sha256=? WHERE id=?', (sha(b'wrong'), value['setId']))
    elif target in {'file_length', 'file_hash'}:
        column, changed = ('byte_count', 1) if target == 'file_length' else ('sha256', sha(b'wrong'))
        mutate(conn, 'prepared_validation_files', 'UPDATE prepared_validation_files SET ' + column + '=? WHERE set_id=?', (changed, value['setId']))
    elif target == 'missing_file':
        mutate(conn, 'prepared_validation_files', 'DELETE FROM prepared_validation_files WHERE set_id=?', (value['setId'],))
    else:
        mutate(conn, 'prepared_validation_' + ('aggregates' if target == 'aggregate' else 'reviews'),
               'UPDATE prepared_validation_' + ('aggregates' if target == 'aggregate' else 'reviews') + ' SET sha256=? WHERE set_id=?',
               (sha(b'wrong'), value['setId']))
    with pytest.raises(activity.PreparedValidationProjectionError):
        projection(state, obj, history_limit=1)
    if target in {'missing_step', 'envelope_hash', 'cleanup_resource'}:
        with pytest.raises(activity.PreparedValidationProjectionError):
            usage(state, obj)
    else:
        assert usage(state, obj)['preparedUnits'] == 4


def test_source_byte_rehash_is_explicitly_outside_metadata_projection(state):
    obj = objective(state)
    value = proposal(state, obj)
    reserve(state, obj, value)
    conn = state['conn']
    mutate(conn, 'prepared_validation_files', "UPDATE prepared_validation_files SET content=? WHERE set_id=? AND path='apps/desktop/tsconfig.json'",
           (b'[]', value['setId']))
    assert usage(state, obj)['sourceBytesVerified'] is False
    with pytest.raises(ValueError, match='bytes changed'):
        ledger.reconstruct_prepared_snapshot(conn, value['setId'])


@pytest.mark.parametrize('limit', [0, -1, 1001, True, '1'])
def test_output_limits_fail_closed(state, limit):
    obj = objective(state)
    with pytest.raises(activity.PreparedValidationProjectionError):
        projection(state, obj, history_limit=limit)
    with pytest.raises(activity.PreparedValidationProjectionError):
        activity.prepared_validation_blockers(state['conn'], obj['ctx']['objective_id'], limit=limit)


def test_goal_corruption_or_member_without_objective_fails_closed(state):
    obj = objective(state)
    goal(state, [obj])
    mutate(state['conn'], 'organization_goal_revisions', 'UPDATE organization_goal_revisions SET sha256=?', (sha(b'changed'),))
    with pytest.raises(activity.PreparedValidationProjectionError):
        activity.goal_project_reserved_units(state['conn'], 'goal')


def test_valid_stored_empty_goal_has_zero_reservations(state):
    # Public configure requires an initial member in this baseline, but its
    # canonical read projection also permits preconfigured empty goals.
    from eidolon_cli.organization_goal_allowances import _hash, goal_projection
    from tests.eidolon_cli.test_organization_goal_allowances import limits
    value = {'id': 'empty-goal', 'revision': 1, 'limits': limits(), 'paused': False}
    state['conn'].execute('INSERT INTO organization_goal_revisions VALUES (?,?,?,?,?)',
                          ('empty-goal', 1, json.dumps(value, sort_keys=True), _hash(value), time.time()))
    assert goal_projection(state['conn'], 'empty-goal')['reserved']['projectRuns'] == 0
    result = activity.goal_project_reserved_units(state['conn'], 'empty-goal')
    assert result['objectiveIds'] == []
    assert result['legacyUnits'] == result['preparedUnits'] == result['totalUnits'] == 0


def test_accounting_does_not_read_source_manifests_or_result_claims(state):
    obj = objective(state)
    goal(state, [obj])
    value = proposal(state, obj)
    reserve(state, obj, value)
    claims(state, value, fail=True, review=False)
    forbidden = {('prepared_validation_bindings', 'grant_record'),
                 ('prepared_validation_snapshots', 'manifest'), ('prepared_validation_files', 'content'),
                 ('prepared_validation_results', 'record'), ('prepared_validation_aggregates', 'record'),
                 ('prepared_validation_reviews', 'record')}
    accessed = []
    def authorize(operation, table, column, database, trigger):
        if operation == sqlite3.SQLITE_READ and (table, column) in forbidden:
            accessed.append((table, column))
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    state['conn'].set_authorizer(authorize)
    try:
        assert usage(state, obj)['preparedUnits'] == 2
        assert activity.goal_project_reserved_units(state['conn'], 'goal')['preparedUnits'] == 2
        assert not accessed
    finally:
        state['conn'].set_authorizer(None)


def test_unknown_result_remains_charged_and_blocked(state):
    obj = objective(state)
    value = proposal(state, obj)
    reserve(state, obj, value)
    records = claims(state, value, review=False)
    run = value['steps'][0]['runId']
    record = next(item for item in records['results'] if item['runId'] == run)
    record['status'] = 'unknown'
    mutate(state['conn'], 'prepared_validation_results',
           'UPDATE prepared_validation_results SET status=?,record=?,sha256=? WHERE run_id=?',
           ('unknown', canonical(record), digest(record), run))
    mutate(state['conn'], 'prepared_validation_aggregates', 'DELETE FROM prepared_validation_aggregates WHERE set_id=?', (value['setId'],))
    assert usage(state, obj)['preparedUnits'] == 2
    reasons = {reason for entry in projection(state, obj)['blockers']['entries'] for reason in entry['reasons']}
    assert {'runtime_receipt_unverified', 'execution_outcome_unknown'} <= reasons


@pytest.mark.parametrize('remaining', ['absent', 'partial', 'views'])
def test_goal_storage_is_legacy_only_only_when_completely_absent(state, remaining):
    conn = state['conn']
    for name in ('organization_goal_members', 'organization_goal_receipts', 'organization_goal_revisions'):
        conn.execute('DROP TABLE ' + name)
    if remaining == 'absent':
        assert activity.goal_project_reserved_units(conn, 'unused-goal') is None
        return
    names = ['organization_goal_members'] if remaining == 'partial' else [
        'organization_goal_members', 'organization_goal_receipts', 'organization_goal_revisions']
    for name in names:
        conn.execute('CREATE VIEW ' + name + ' AS SELECT 1')
    with pytest.raises(activity.PreparedValidationProjectionError):
        activity.goal_project_reserved_units(conn, 'unused-goal')


def test_new_empty_prepared_schema_is_present_with_zero_reservations(state, tmp_path):
    store = OrganizationStore(tmp_path / 'new-empty.db', state['store'].settings)
    with store._write() as conn:
        ident = store._create_objective(conn, 'Empty prepared bookkeeping', idempotency_key='new-empty',
                                       project_ids=['desktop'], delivery_mode='managed_artifact', required_checks=[])
    with store._connect() as conn:
        assert activity.prepared_validation_storage_state(conn) == 'present'
        result = activity.prepared_validation_activity(conn, ident)
        assert result['preparedUnits'] == result['totalUnits'] == 0
        assert result['current'] == {} and result['history'] == []
        assert result['blockers']['blocked'] is False
