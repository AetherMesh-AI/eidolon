"""Inert real-SQL persistence tests; none authenticate runtime or owner claims."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import sqlite3
import threading
import time

import pytest

from eidolon_cli import organization_prepared_validation_ledger as ledger
from eidolon_cli.organization_goal_allowances import goal_projection
from eidolon_cli.organization_project_snapshot_bundle import (
    ProjectSnapshotBundle, ProjectSnapshotFile, ProjectSnapshotLimits,
)
from eidolon_cli.organization_project_validation import canonical, digest
from eidolon_cli.organization_store import OrganizationStore
from tests.eidolon_cli.test_organization_prepared_validation_contract import (
    context, envelope, evidence, normalize, prepared, sha,  # noqa: F401
)


@pytest.fixture
def state(tmp_path, prepared):
    settings = replace(prepared['settings'], max_project_runs=20)
    store = OrganizationStore(tmp_path / 'ledger.db', settings)
    conn = sqlite3.connect(store.path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.executescript(ledger.PREPARED_VALIDATION_SCHEMA)
    conn.execute("INSERT INTO agents VALUES ('reviewer-1','Reviewer','Worker',NULL,'engineering','[]')")
    state = {'store': store, 'conn': conn, 'prepared': prepared}
    yield state
    conn.close()


def objective(state, *, max_runs=20):
    store, conn, prepared = state['store'], state['conn'], state['prepared']
    with store._write() as write:
        identifier = store._create_objective(write, 'Prepared fixture', idempotency_key='objective-' + str(time.time_ns()),
            project_ids=['desktop'], delivery_mode='managed_artifact', required_checks=[], project_run_limit=max_runs)
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


def proposal(state, obj, generation=1, *, suffix=''):
    value = envelope(obj['ctx'], generation)
    namespace = obj['ctx']['objective_id'] + suffix
    value['setId'] += '-' + namespace
    value['requestId'] += '-' + namespace
    for step in value['steps']:
        step['runId'] += '-' + namespace
    state['conn'].execute('INSERT INTO requests(id,objective_id,type,team,priority,status,created,payload) '
        "VALUES (?,?,'request.project_test','engineering',1,'queued',?,?)",
        (value['requestId'], obj['ctx']['objective_id'], time.time(), canonical({'projectId': 'desktop', 'round': obj['ctx']['round_number']})))
    return value


def reserve(state, obj, value, **changes):
    args = {'snapshot': obj['snapshot'], 'context': obj['ctx'], 'owner_ceiling': 20,
            'lease_token': 'private-lease-token-never-export'}
    args.update(changes)
    return ledger.reserve_prepared_validation_proposal(state['conn'], value, **args)


def legacy_run(state, obj, identifier='legacy-run'):
    value = proposal(state, obj, suffix='-legacy-' + identifier)
    state['conn'].execute('INSERT INTO project_run_starts VALUES (?,?,?,?,?,?,?,?,?,?,?)',
        (identifier, value['requestId'], 'legacy-token', obj['ctx']['objective_id'], 0, '[]', sha(b'[]'), '{}', None, 0, time.time()))


def claims(state, value, *, fail=False, review=True, cleanup_confirmed=True):
    conn = state['conn']
    records = evidence(value)
    records['review']['requestId'] += '-' + value['setId']
    for index, result in enumerate(sorted(records['results'], key=lambda item: item['recipe'] != 'workspace_typescript')):
        run = result['runId']
        ledger.record_prepared_dispatch_proposal(conn, run, nonce=sha(('nonce-' + run).encode()))
        ledger.record_prepared_resource_claim(conn, run, container_id=sha(('container-' + run).encode()))
        if fail and index == 1:
            result['status'] = 'failed'
        result['cleanupConfirmed'] = result['stagingCleanupConfirmed'] = cleanup_confirmed
        ledger.record_prepared_result_claim(conn, result)
        ledger.record_prepared_cleanup_claim(conn, run, event_id='cleanup-' + run,
            nonce=sha(('nonce-' + run).encode()), container_id=sha(('container-' + run).encode()),
            container_confirmed=cleanup_confirmed, staging_confirmed=cleanup_confirmed)
    ledger.record_prepared_aggregate_claim(conn, value['setId'])
    if review:
        conn.execute('INSERT INTO requests(id,objective_id,type,team,priority,status,created,payload) '
            "VALUES (?,?,'request.test_review','engineering',1,'queued',?,'{}')",
            (records['review']['requestId'], value['objectiveId'], time.time()))
    return records


def approve_claim(state, obj, value):
    records = claims(state, value)
    return ledger.record_prepared_review_claim(state['conn'], records['review'], context=obj['ctx'],
        contributor_ids=records['contributor_ids'], source_evidence=records['source_evidence'])


def count(state, table):
    return state['conn'].execute('SELECT count(*) FROM ' + table).fetchone()[0]


def test_registered_schema_does_not_enable_profiles_and_initial_round_zero_is_real(state):
    with state['store']._connect() as conn:
        assert set(ledger.PREPARED_VALIDATION_TABLES) <= {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    # An ordinary store gets empty bookkeeping, never a profile or execution grant.
    other = OrganizationStore(state['store'].path.parent / 'ordinary.db', state['store'].settings)
    with other._connect() as conn:
        assert set(ledger.PREPARED_VALIDATION_TABLES) <= {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert conn.execute("SELECT count(*) FROM prepared_validation_bindings").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM prepared_validation_sets").fetchone()[0] == 0
    obj = objective(state)
    value = proposal(state, obj)
    result = reserve(state, obj, value)
    assert result['committed'] is True and result['reservedUnits'] == 2
    assert result['executionAuthorized'] is result['runtimeReceiptsVerified'] is False
    assert state['conn'].execute('SELECT round FROM prepared_validation_sets').fetchone()[0] == 0
    assert count(state, 'project_run_starts') == 0
    assert ledger.reconstruct_prepared_snapshot(state['conn'], value['setId']) == obj['snapshot']


@pytest.mark.parametrize('ceiling,owner', [(3, 20), (20, 3)])
def test_two_unit_admission_counts_legacy_and_failed_or_interrupted_generations(state, ceiling, owner):
    obj = objective(state, max_runs=ceiling)
    legacy_run(state, obj)
    first = proposal(state, obj)
    result = reserve(state, obj, first, owner_ceiling=owner)
    assert (result['priorUnits'], result['resultingUnits']) == (1, 3)
    claims(state, first, fail=True, review=False)
    second = proposal(state, obj, 2)
    with pytest.raises(ValueError, match='ceiling'):
        reserve(state, obj, second, owner_ceiling=owner)
    assert count(state, 'prepared_validation_sets') == 1
    assert count(state, 'prepared_validation_steps') == 2


def test_reservation_rolls_back_every_row_if_second_insert_fails(state):
    obj = objective(state)
    value = proposal(state, obj)
    state['conn'].executescript("CREATE TRIGGER fail_second BEFORE INSERT ON prepared_validation_steps "
        "WHEN (SELECT count(*) FROM prepared_validation_steps)=1 BEGIN SELECT RAISE(ABORT,'injected failure'); END;")
    with pytest.raises(sqlite3.IntegrityError, match='injected failure'):
        reserve(state, obj, value)
    for suffix in ('sets', 'steps', 'snapshots', 'files'):
        assert count(state, 'prepared_validation_' + suffix) == 0
    assert state['conn'].in_transaction is False
    state['conn'].execute('DROP TRIGGER fail_second')
    assert reserve(state, obj, value)['priorUnits'] == 0


def test_committed_crash_start_stays_charged_after_reopening(state):
    obj = objective(state, max_runs=3)
    value = proposal(state, obj)
    reserve(state, obj, value)
    state['conn'].close()
    state['conn'] = sqlite3.connect(state['store'].path, isolation_level=None)
    state['conn'].row_factory = sqlite3.Row
    state['conn'].execute('PRAGMA foreign_keys=ON')
    second = proposal(state, obj, 2)
    with pytest.raises(ValueError, match='ceiling'):
        reserve(state, obj, second)
    assert count(state, 'prepared_validation_steps') == 2
    assert count(state, 'prepared_validation_results') == 0
    # Fixture finalizer owns the original connection; close this reopened peer.
    state['conn'].close()


def test_transaction_ownership_and_foreign_keys_fail_before_any_write(state):
    obj = objective(state)
    value = proposal(state, obj)
    conn = state['conn']
    conn.execute('BEGIN')
    conn.execute("UPDATE objectives SET title='pending caller write' WHERE id=?", (obj['ctx']['objective_id'],))
    with pytest.raises(ValueError, match='own transaction'):
        reserve(state, obj, value)
    assert conn.in_transaction
    conn.rollback()
    assert conn.execute('SELECT title FROM objectives WHERE id=?', (obj['ctx']['objective_id'],)).fetchone()[0] == 'Prepared fixture'
    conn.execute('PRAGMA foreign_keys=OFF')
    with pytest.raises(ValueError, match='foreign key'):
        reserve(state, obj, value)
    assert count(state, 'prepared_validation_sets') == 0
    conn.execute('PRAGMA foreign_keys=ON')


def test_concurrent_reservations_cannot_spend_the_same_last_two_units(state):
    obj = objective(state, max_runs=2)
    proposals = [proposal(state, obj, suffix='-' + str(n)) for n in range(2)]
    barrier = threading.Barrier(2)
    def attempt(value):
        with sqlite3.connect(state['store'].path, isolation_level=None) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA foreign_keys=ON')
            barrier.wait(timeout=10)
            try:
                return ledger.reserve_prepared_validation_proposal(conn, value, snapshot=obj['snapshot'],
                    context=obj['ctx'], owner_ceiling=2, lease_token=value['requestId'])
            except (ValueError, sqlite3.IntegrityError):
                return None
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(attempt, proposals))
    assert sum(result is not None for result in results) == 1
    assert count(state, 'prepared_validation_steps') == 2


def test_goal_admission_includes_all_members_legacy_and_prepared_units(state):
    left, right = objective(state), objective(state)
    store = state['store']
    limits = {'modelCalls': 10, 'tokens': 10000, 'costUsd': None, 'stages': 100, 'projectRuns': 4,
              'deadlineTimestamp': int(time.time()) + 3600,
              'completionReserve': {'modelCalls': 0, 'tokens': 0, 'costUsd': None}}
    store.configure_goal_allowance('goal', objective_ids=[left['ctx']['objective_id'], right['ctx']['objective_id']],
        limits=limits, paused=False, expected_revision=0, idempotency_key='goal-key', confirmed=True)
    legacy_run(state, left)
    reserve(state, left, proposal(state, left))
    # Unified goal accounting includes the legacy unit and the complete prepared pair.
    assert goal_projection(state['conn'], 'goal')['reserved']['projectRuns'] == 3
    with pytest.raises(ValueError, match='Shared goal'):
        reserve(state, right, proposal(state, right))
    assert count(state, 'prepared_validation_steps') == 2


@pytest.mark.parametrize('mutation', ['paused', 'expired', 'unknown'])
def test_goal_existing_denials_are_not_bypassed(state, mutation):
    obj = objective(state)
    limits = {'modelCalls': 10, 'tokens': 10000, 'costUsd': None, 'stages': 100, 'projectRuns': 10,
              'deadlineTimestamp': int(time.time()) + (3600 if mutation != 'expired' else -1),
              'completionReserve': {'modelCalls': 0, 'tokens': 0, 'costUsd': None}}
    state['store'].configure_goal_allowance('goal', objective_ids=[obj['ctx']['objective_id']], limits=limits,
        paused=mutation == 'paused', expected_revision=0, idempotency_key='goal-key', confirmed=True)
    if mutation == 'unknown':
        state['conn'].execute('UPDATE objective_budgets SET legacy_usage_unknown=1 WHERE objective_id=?', (obj['ctx']['objective_id'],))
    with pytest.raises(ValueError, match='Shared goal'):
        reserve(state, obj, proposal(state, obj))
    assert count(state, 'prepared_validation_steps') == 0


@pytest.mark.parametrize('operation', ['UPDATE', 'DELETE', 'REPLACE'])
def test_all_tables_are_immutable_including_replace_without_recursive_triggers(state, operation):
    obj = objective(state)
    value = proposal(state, obj)
    reserve(state, obj, value)
    approve_claim(state, obj, value)
    conn = state['conn']
    assert conn.execute('PRAGMA recursive_triggers').fetchone()[0] == 0
    for table in ledger.PREPARED_VALIDATION_TABLES:
        assert count(state, table) > 0
        columns = [row[1] for row in conn.execute('PRAGMA table_info(' + table + ')')]
        statements = {'UPDATE': f'UPDATE {table} SET {columns[0]}={columns[0]}',
                      'DELETE': 'DELETE FROM ' + table,
                      'REPLACE': f'INSERT OR REPLACE INTO {table} SELECT * FROM {table} LIMIT 1'}
        with pytest.raises(sqlite3.IntegrityError, match='immutable|replaced|generation'):
            conn.execute(statements[operation])
    assert not conn.execute('PRAGMA foreign_key_check').fetchall()


@pytest.mark.parametrize('mutation', ['missing', 'extra', 'changed', 'mode', 'limits', 'manifest'])
def test_reconstruction_rejects_missing_extra_changed_bytes_or_policy(state, mutation):
    obj = objective(state)
    value = proposal(state, obj)
    reserve(state, obj, value)
    conn = state['conn']
    # Model offline file corruption explicitly: ordinary SQL cannot mutate these rows.
    for table in ('prepared_validation_files', 'prepared_validation_snapshots'):
        for op in ('update', 'delete'):
            conn.execute('DROP TRIGGER immutable_' + table + '_' + op)
    conn.execute('DROP TRIGGER prepared_validation_file_binding')
    path = 'apps/desktop/vitest.config.ts'
    if mutation == 'missing':
        conn.execute('DELETE FROM prepared_validation_files WHERE path=?', (path,))
    elif mutation == 'extra':
        conn.execute('INSERT INTO prepared_validation_files VALUES (?,?,?,?,?,?,?)',
                     (value['setId'], 'extra.ts', b'{}', sha(b'{}'), 2, 292, 0))
    elif mutation == 'changed':
        conn.execute('UPDATE prepared_validation_files SET content=? WHERE path=?', (b'[]', path))
    elif mutation == 'mode':
        conn.execute('UPDATE prepared_validation_files SET mode=365 WHERE path=?', (path,))
    elif mutation == 'limits':
        # Use legitimate snapshot fields with a self-consistent hash, still different from the sidecar.
        limits = {**normalize(state['prepared'])['source']['limits'], 'max_files': 19}
        conn.execute('UPDATE prepared_validation_snapshots SET limits=?,limits_sha256=?', (canonical(limits), digest(limits)))
    else:
        conn.execute("UPDATE prepared_validation_snapshots SET snapshot_sha256=?", ('0' * 64,))
    with pytest.raises(ValueError):
        ledger.reconstruct_prepared_snapshot(conn, value['setId'])


def test_newest_generation_always_blocks_an_older_passing_claim(state):
    obj = objective(state)
    first = proposal(state, obj)
    reserve(state, obj, first)
    result = approve_claim(state, obj, first)
    assert result['state'] == 'contract_consistent_runtime_unverified'
    assert result['acceptanceAuthorized'] is result['sourceDeliveryAuthorized'] is result['executionAuthorized'] is False
    serialized = json.dumps(ledger.inspect_latest_prepared_proposal(state['conn'], context=obj['ctx']))
    assert 'private-lease-token-never-export' not in serialized
    second = proposal(state, obj, 2)
    reserve(state, obj, second)
    with pytest.raises(ValueError, match='Latest prepared generation'):
        ledger.inspect_latest_prepared_proposal(state['conn'], context=obj['ctx'])
    records = claims(state, second, fail=True)
    with pytest.raises(ValueError):
        ledger.record_prepared_review_claim(state['conn'], records['review'], context=obj['ctx'],
            contributor_ids=records['contributor_ids'], source_evidence=records['source_evidence'])
    with pytest.raises(ValueError, match='Latest prepared generation'):
        ledger.inspect_latest_prepared_proposal(state['conn'], context=obj['ctx'])
    assert count(state, 'prepared_validation_steps') == 4


def test_same_generation_reused_run_and_backwards_round_rejected(state):
    obj = objective(state)
    first = proposal(state, obj)
    reserve(state, obj, first)
    repeated = proposal(state, obj, suffix='-repeated')
    with pytest.raises(ValueError):
        reserve(state, obj, repeated)
    second = proposal(state, obj, 2)
    second['steps'][0]['runId'] = first['steps'][0]['runId']
    with pytest.raises(ValueError, match='reuse'):
        reserve(state, obj, second)
    state['conn'].execute('UPDATE objective_control SET round=1 WHERE objective_id=?', (obj['ctx']['objective_id'],))
    obj['ctx']['round_number'] = 1
    fresh = proposal(state, obj, 2, suffix='-fresh')
    reserve(state, obj, fresh)
    state['conn'].execute('UPDATE objective_control SET round=0 WHERE objective_id=?', (obj['ctx']['objective_id'],))
    obj['ctx']['round_number'] = 0
    backwards = proposal(state, obj, 3)
    with pytest.raises(ValueError, match='backwards'):
        reserve(state, obj, backwards)


def test_deferred_foreign_keys_require_both_exact_envelope_steps(state):
    obj = objective(state)
    value = proposal(state, obj)
    reserve(state, obj, value)
    conn = state['conn']
    # Simulate interrupted/raw SQL assembly in another generation. Keep only one
    # reservation via an INSERT-ignoring trigger; deferred FK fails COMMIT.
    conn.executescript("CREATE TRIGGER ignore_second BEFORE INSERT ON prepared_validation_steps "
        "WHEN NEW.recipe='workspace_typescript' BEGIN SELECT RAISE(IGNORE); END;")
    second = proposal(state, obj, 2)
    with pytest.raises(sqlite3.IntegrityError, match='FOREIGN KEY'):
        reserve(state, obj, second)
    assert count(state, 'prepared_validation_sets') == 1
    conn.execute('DROP TRIGGER ignore_second')
    # A syntactically valid alternative step cannot satisfy the exact envelope.
    conn.execute('BEGIN')
    start = dict(conn.execute('SELECT * FROM prepared_validation_sets').fetchone())
    start.update(id=second['setId'], request_id=second['requestId'], generation=2,
                 envelope=canonical(second), envelope_sha256=digest(second),
                 typecheck_run_id=next(s['runId'] for s in second['steps'] if s['recipe'] == 'workspace_typescript'),
                 tests_run_id=next(s['runId'] for s in second['steps'] if s['recipe'] == 'workspace_vitest_ui'))
    conn.execute('INSERT INTO prepared_validation_sets (' + ','.join(start) + ') VALUES (' + ','.join('?' for _ in start) + ')', tuple(start.values()))
    with pytest.raises(sqlite3.IntegrityError, match='exact envelope'):
        conn.execute('INSERT INTO prepared_validation_steps VALUES (?,?,?,?,?,?,1)',
            ('arbitrary-run', second['setId'], 'arbitrary-step', '0' * 64, 'workspace_typescript', 'project_typecheck'))
    conn.rollback()
    with pytest.raises(sqlite3.IntegrityError, match='FOREIGN KEY'):
        conn.execute('INSERT INTO prepared_validation_resources VALUES (?,?,?)', ('unreserved', '0' * 64, time.time()))


@pytest.mark.parametrize('change', ['policy', 'round', 'binding', 'request', 'snapshot'])
def test_independent_retained_context_mismatch_never_reserves(state, change):
    obj = objective(state)
    value = proposal(state, obj)
    if change == 'policy':
        state['conn'].execute('UPDATE organization_policy SET generation=generation+1')
    elif change == 'round':
        state['conn'].execute('UPDATE objective_control SET round=round+1')
    elif change == 'binding':
        obj['ctx']['objective_binding_sha256'] = '0' * 64
        value = envelope(obj['ctx'])
    elif change == 'request':
        state['conn'].execute("UPDATE requests SET payload='{}' WHERE id=?", (value['requestId'],))
    else:
        original = obj['snapshot']
        files = tuple(ProjectSnapshotFile(f.path, b'[]' if f.path.endswith('vitest.config.ts') else f.content,
                                          f.mode, f.execution_only) for f in original.files)
        obj['snapshot'] = ProjectSnapshotBundle(original.root_alias, original.root_identity, files, original.limits)
    with pytest.raises(ValueError):
        reserve(state, obj, value)
    assert count(state, 'prepared_validation_sets') == 0


@pytest.mark.parametrize('field', ['setId', 'requestId', 'objectiveId', 'projectId', 'round', 'generation'])
def test_raw_start_sql_rejects_absent_envelope_binding_fields(state, field):
    obj = objective(state)
    first = proposal(state, obj)
    reserve(state, obj, first)
    second = proposal(state, obj, 2)
    conn = state['conn']
    start = dict(conn.execute('SELECT * FROM prepared_validation_sets').fetchone())
    start.update(id=second['setId'], request_id=second['requestId'], generation=2,
                 typecheck_run_id=next(s['runId'] for s in second['steps'] if s['recipe'] == 'workspace_typescript'),
                 tests_run_id=next(s['runId'] for s in second['steps'] if s['recipe'] == 'workspace_vitest_ui'))
    del second[field]
    start.update(envelope=canonical(second), envelope_sha256=digest(second))
    with pytest.raises(sqlite3.IntegrityError, match='CHECK constraint'):
        conn.execute('INSERT INTO prepared_validation_sets (' + ','.join(start) + ') VALUES (' + ','.join('?' for _ in start) + ')', tuple(start.values()))
    assert count(state, 'prepared_validation_sets') == 1


def test_sql_preflight_rejects_retained_metadata_and_file_size_overrun(state):
    obj = objective(state)
    value = proposal(state, obj)
    reserve(state, obj, value)
    conn = state['conn']
    conn.execute('DROP TRIGGER immutable_prepared_validation_snapshots_update')
    original = dict(conn.execute('SELECT * FROM prepared_validation_snapshots').fetchone())
    conn.execute('UPDATE prepared_validation_snapshots SET limits=?', (' ' * 4097,))
    with pytest.raises(ValueError, match='metadata.*byte bound'):
        ledger.reconstruct_prepared_snapshot(conn, value['setId'])
    limits = {**json.loads(original['limits']), 'max_file_bytes': 1}
    conn.execute('UPDATE prepared_validation_snapshots SET limits=?,limits_sha256=?', (canonical(limits), digest(limits)))
    with pytest.raises(ValueError, match='count/byte limits'):
        ledger.reconstruct_prepared_snapshot(conn, value['setId'])
    conn.execute('UPDATE prepared_validation_snapshots SET limits=?,limits_sha256=?', (original['limits'], original['limits_sha256']))
    conn.execute('DROP TRIGGER immutable_prepared_validation_sets_update')
    too_large = {**value, 'padding': 'x' * 131072}
    conn.execute('UPDATE prepared_validation_sets SET envelope=?,envelope_sha256=?', (canonical(too_large), digest(too_large)))
    with pytest.raises(ValueError, match='history.*count/byte bound'):
        ledger.inspect_latest_prepared_proposal(conn, context=obj['ctx'])


def test_snapshot_fixture_cannot_bypass_recognized_secret_refusal(state):
    obj = objective(state)
    original = obj['snapshot']
    # This is a detector fixture, not an actual credential.
    secret = b'const token = "ghp_' + b'a' * 36 + b'";'
    files = tuple(ProjectSnapshotFile(f.path, secret if f.path.endswith('vitest.config.ts') else f.content,
                                      f.mode, f.execution_only) for f in original.files)
    obj['snapshot'] = ProjectSnapshotBundle(original.root_alias, original.root_identity, files, original.limits)
    obj['ctx']['snapshot_sha256'] = obj['snapshot'].snapshot_sha256
    with pytest.raises(ValueError, match='secret-bearing'):
        reserve(state, obj, proposal(state, obj))
    assert count(state, 'prepared_validation_sets') == 0


def test_concurrent_goal_members_cannot_each_reserve_the_last_two_units(state):
    objects = [objective(state), objective(state)]
    limits = {'modelCalls': 10, 'tokens': 10000, 'costUsd': None, 'stages': 100, 'projectRuns': 2,
              'deadlineTimestamp': int(time.time()) + 3600,
              'completionReserve': {'modelCalls': 0, 'tokens': 0, 'costUsd': None}}
    state['store'].configure_goal_allowance('goal', objective_ids=[obj['ctx']['objective_id'] for obj in objects],
        limits=limits, paused=False, expected_revision=0, idempotency_key='goal-race', confirmed=True)
    values = [proposal(state, obj) for obj in objects]
    barrier = threading.Barrier(2)
    def attempt(pair):
        obj, value = pair
        conn = sqlite3.connect(state['store'].path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        try:
            barrier.wait(timeout=10)
            return ledger.reserve_prepared_validation_proposal(conn, value, snapshot=obj['snapshot'],
                context=obj['ctx'], owner_ceiling=20, lease_token=value['requestId'])
        except ValueError as error:
            assert 'Shared goal' in str(error)
            return None
        finally:
            conn.close()
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(attempt, zip(objects, values)))
    assert sum(result is not None for result in results) == 1
    assert count(state, 'prepared_validation_sets') == 1
    assert count(state, 'prepared_validation_steps') == 2


def test_cleanup_observations_settle_unknown_resource_without_rewriting_history(state):
    obj = objective(state)
    value = proposal(state, obj)
    reserve(state, obj, value)
    conn, run = state['conn'], value['steps'][0]['runId']
    nonce, container = sha(b'precommitted-resource'), sha(b'observed-container')
    ledger.record_prepared_dispatch_proposal(conn, run, nonce=nonce)
    first = ledger.record_prepared_cleanup_claim(conn, run, event_id='unknown-cleanup', nonce=nonce,
        container_id=None, container_confirmed=False, staging_confirmed=False)
    ledger.record_prepared_resource_claim(conn, run, container_id=container)
    settled = ledger.record_prepared_cleanup_claim(conn, run, event_id='settled-cleanup', nonce=nonce,
        container_id=container, container_confirmed=True, staging_confirmed=True)
    rows = conn.execute('SELECT sequence,container_id,container_confirmed,staging_confirmed '
                        'FROM prepared_validation_cleanups WHERE run_id=? ORDER BY sequence', (run,)).fetchall()
    assert [tuple(row) for row in rows] == [(1, None, 0, 0), (2, container, 1, 1)]
    assert (first['sequence'], settled['sequence']) == (1, 2)
    assert settled['runtimeReceiptsVerified'] is settled['executionAuthorized'] is settled['acceptanceAuthorized'] is False
    assert count(state, 'prepared_validation_results') == 0
    with pytest.raises(ValueError, match='no complete review'):
        ledger.inspect_latest_prepared_proposal(conn, context=obj['ctx'])


@pytest.mark.parametrize('failed', [False, True])
def test_cleanup_settlement_never_upgrades_failed_result_or_original_cleanup_flags(state, failed):
    obj = objective(state)
    value = proposal(state, obj)
    reserve(state, obj, value)
    records = claims(state, value, fail=failed, cleanup_confirmed=False)
    conn = state['conn']
    before = [tuple(row) for row in conn.execute('SELECT * FROM prepared_validation_results ORDER BY run_id')]
    for step in value['steps']:
        run = step['runId']
        ledger.record_prepared_cleanup_claim(conn, run, event_id='settled-' + run,
            nonce=sha(('nonce-' + run).encode()), container_id=sha(('container-' + run).encode()),
            container_confirmed=True, staging_confirmed=True)
    assert [tuple(row) for row in conn.execute('SELECT * FROM prepared_validation_results ORDER BY run_id')] == before
    assert count(state, 'prepared_validation_cleanups') == 4
    with pytest.raises(ValueError, match='failed, interrupted or incomplete cleanup'):
        ledger.record_prepared_review_claim(conn, records['review'], context=obj['ctx'],
            contributor_ids=records['contributor_ids'], source_evidence=records['source_evidence'])
    assert count(state, 'prepared_validation_reviews') == 0
    assert count(state, 'prepared_validation_steps') == 2


def test_latest_unconfirmed_cleanup_blocks_previously_consistent_review(state):
    obj = objective(state)
    value = proposal(state, obj)
    reserve(state, obj, value)
    approve_claim(state, obj, value)
    conn, run = state['conn'], value['steps'][0]['runId']
    assert ledger.inspect_latest_prepared_proposal(conn, context=obj['ctx'])['state'] == 'contract_consistent_runtime_unverified'
    ledger.record_prepared_cleanup_claim(conn, run, event_id='later-unconfirmed-cleanup',
        nonce=sha(('nonce-' + run).encode()), container_id=sha(('container-' + run).encode()),
        container_confirmed=False, staging_confirmed=False)
    assert [row[0] for row in conn.execute('SELECT container_confirmed FROM prepared_validation_cleanups '
        'WHERE run_id=? ORDER BY sequence', (run,))] == [1, 0]
    with pytest.raises(ValueError, match='resource/cleanup claims'):
        ledger.inspect_latest_prepared_proposal(conn, context=obj['ctx'])
    assert count(state, 'prepared_validation_reviews') == 1


@pytest.mark.parametrize('mutation', ['nonce', 'container', 'missing_container', 'event', 'sequence'])
def test_cleanup_observations_cannot_switch_resource_or_reuse_identity(state, mutation):
    obj = objective(state)
    value = proposal(state, obj)
    reserve(state, obj, value)
    claims(state, value, review=False)
    conn, run = state['conn'], value['steps'][0]['runId']
    args = {'event_id': 'later-cleanup', 'nonce': sha(('nonce-' + run).encode()),
            'container_id': sha(('container-' + run).encode()),
            'container_confirmed': True, 'staging_confirmed': True}
    if mutation == 'nonce':
        args['nonce'] = '0' * 64
    elif mutation == 'container':
        args['container_id'] = '0' * 64
    elif mutation == 'missing_container':
        args['container_id'] = None
    elif mutation == 'event':
        args['event_id'] = 'cleanup-' + run
    if mutation == 'sequence':
        row = dict(conn.execute('SELECT * FROM prepared_validation_cleanups WHERE run_id=?', (run,)).fetchone())
        row.update(event_id='skipped-sequence', sequence=3)
        with pytest.raises(sqlite3.IntegrityError, match='next sequence'):
            conn.execute('INSERT INTO prepared_validation_cleanups (' + ','.join(row) + ') VALUES (' + ','.join('?' for _ in row) + ')', tuple(row.values()))
    else:
        with pytest.raises(sqlite3.IntegrityError):
            ledger.record_prepared_cleanup_claim(conn, run, **args)
    assert count(state, 'prepared_validation_cleanups') == 2


def test_transaction_scoped_pair_reservation_never_commits_caller_work(state):
    obj = objective(state)
    value = proposal(state, obj)
    conn = state['conn']
    with pytest.raises(RuntimeError, match='caller rollback'):
        conn.execute('BEGIN IMMEDIATE')
        try:
            result = ledger._reserve_prepared_validation_proposal(conn, value,
                snapshot=obj['snapshot'], context=obj['ctx'], owner_ceiling=20,
                lease_token='private-transaction-token')
            assert result['committed'] is False
            assert result['executionAuthorized'] is False
            assert conn.in_transaction
            assert count(state, 'prepared_validation_steps') == 2
            raise RuntimeError('caller rollback')
        finally:
            conn.rollback()
    assert count(state, 'prepared_validation_sets') == 0
    assert count(state, 'prepared_validation_steps') == 0
    assert count(state, 'prepared_validation_files') == 0
    with pytest.raises(ValueError, match='existing organization transaction'):
        ledger._reserve_prepared_validation_proposal(conn, value,
            snapshot=obj['snapshot'], context=obj['ctx'], owner_ceiling=20,
            lease_token='private-transaction-token')


def test_transaction_scoped_binding_rolls_back_with_new_objective(state):
    store, conn, prepared = state['store'], state['conn'], state['prepared']
    with pytest.raises(RuntimeError, match='cancel admission'):
        with store._write() as write:
            identifier = store._create_objective(write, 'Atomic prepared admission',
                idempotency_key='atomic-prepared-admission', project_ids=['desktop'],
                delivery_mode='managed_artifact', required_checks=[])
            ledger._bind_prepared_validation_proposal(write, objective_id=identifier,
                value=prepared['contract'], context=prepared['inputs'])
            assert write.in_transaction
            assert write.execute('SELECT 1 FROM prepared_validation_bindings WHERE objective_id=?',
                                 (identifier,)).fetchone()
            raise RuntimeError('cancel admission')
    assert conn.execute('SELECT 1 FROM objectives WHERE idempotency_key=?',
                        ('atomic-prepared-admission',)).fetchone() is None
    assert count(state, 'prepared_validation_bindings') == 0
