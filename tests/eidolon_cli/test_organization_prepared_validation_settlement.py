"""Retained synthetic host events test settlement, never native execution proof."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import pytest

from eidolon_cli import organization_prepared_validation_settlement as settlement
from eidolon_cli import organization_prepared_validation_activity as activity
from eidolon_cli.organization_folder_revocation import _activity
from eidolon_cli.organization_history import settlement_blocker
from eidolon_cli.organization_project_validation import canonical, digest
from eidolon_cli.organization_project_workspace_resources import container_labels, container_name
from tests.eidolon_cli.test_organization_prepared_validation_activity import mutate
from tests.eidolon_cli.test_organization_prepared_validation_events import append, append_prefix, stream  # noqa: F401
from tests.eidolon_cli.test_organization_prepared_validation_ledger import (
    claims, objective, prepared, proposal, reserve, state,  # noqa: F401
)
from tests.eidolon_cli.test_organization_root_grants import folder_profile  # noqa: F401


def view(state, stream, **kwargs):
    return settlement.prepared_runtime_settlement(state['conn'], objective_id=stream['envelope']['objectiveId'], **kwargs)


@pytest.mark.parametrize('length', range(8))
def test_every_crash_boundary_stays_visible_and_charged(state, stream, length):
    append_prefix(state, stream, length)
    result = view(state, stream)
    assert result['totalSets'] == 1 and result['reservedUnits'] == result['totalRuns'] == 2
    assert result['undispatchedRuns'] == (2 if length == 0 else 1)
    assert result['unsettledRunCount'] == (1 if 0 < length < 7 else 0)
    assert result['resourceBlockerCount'] == (1 if 0 < length < 6 else 0)
    assert result['replayAllowed'] is result['runtimeReceiptsVerified'] is result['acceptanceAuthorized'] is False
    assert settlement.request_has_prepared_reservation(state['conn'], stream['envelope']['requestId'])
    state['store'].cancel(stream['envelope']['objectiveId'])
    reason = settlement_blocker(state['conn'], stream['envelope']['objectiveId'])
    assert bool(reason) == (0 < length < 7)
    if reason:
        with pytest.raises(ValueError, match='unconfirmed prepared'):
            state['store'].set_objective_archived(stream['envelope']['objectiveId'], True,
                expected_revision=0, idempotency_key='archive')
    else:
        assert state['store'].set_objective_archived(stream['envelope']['objectiveId'], True,
            expected_revision=0, idempotency_key='archive')['archived']
    assert activity.objective_project_reserved_units(state['conn'], stream['envelope']['objectiveId'])['preparedUnits'] == 2


def test_false_then_true_cleanup_settles_resource_without_upgrading_failed_result(state, stream):
    records = stream['records']
    records[5]['data']['containerCleanupConfirmed'] = False
    records[5]['data']['stagingCleanupConfirmed'] = False
    records[6]['data']['receipt']['cleanupConfirmed'] = False
    records[6]['data']['receipt']['stagingCleanupConfirmed'] = False
    records[6]['data']['receiptSha256'] = digest(records[6]['data']['receipt'])
    append_prefix(state, stream, 7)
    assert view(state, stream)['unsettledRunCount'] == 1
    cleanup = deepcopy(records[5])
    cleanup['data'].update(containerCleanupConfirmed=True, stagingCleanupConfirmed=True)
    append(state, cleanup)
    result = view(state, stream)
    first = result['entries'][0]
    assert first['terminalStatus'] == 'failed' and first['state'] == 'terminal_failed'
    assert first['resourceCleanupSettled'] and not first['blockerReasons']
    assert result['unsettledRunCount'] == 0 and result['reservedUnits'] == 2
    row = state['conn'].execute('SELECT record FROM prepared_validation_results WHERE run_id=?', (stream['run'],)).fetchone()
    assert json.loads(row[0])['cleanupConfirmed'] is False
    append(state, records[5])
    assert view(state, stream)['unsettledRunCount'] == 1
    assert view(state, stream)['entries'][0]['terminalStatus'] == 'failed'


def test_confirmed_staging_cleanup_before_create_is_not_a_phantom_container(state, stream):
    append_prefix(state, stream, 1)
    cleanup = deepcopy(stream['records'][5])
    cleanup['data'].update(containerId=None, containerCleanupConfirmed=False)
    append(state, cleanup)
    terminal = deepcopy(stream['records'][6])
    terminal['data']['receipt'].update(status='blocked', containerId=None, cleanupConfirmed=False)
    terminal['data']['receiptSha256'] = digest(terminal['data']['receipt'])
    append(state, terminal)
    first = view(state, stream)['entries'][0]
    assert first['containerState'] == 'not_attempted' and first['stagingState'] == 'cleanup_confirmed'
    assert first['resourceCleanupSettled'] and first['blockerReasons'] == []
    assert first['terminalStatus'] == 'blocked'


def test_claim_only_receipts_never_settle_missing_host_event_history(state, stream):
    claims(state, stream['envelope'])
    result = view(state, stream)
    assert result['unsettledRunCount'] == result['resourceBlockerCount'] == 2
    assert all(item['blockerReasons'] == ['runtime_history_missing'] for item in result['entries'])
    assert result['runtimeReceiptsVerified'] is False


def test_cleanup_reader_needs_no_live_source_current_grants_or_source_bytes(state, stream):
    append_prefix(state, stream, 7)
    Path(state['prepared']['settings'].read_roots[0]).rmdir()
    mutate(state['conn'], 'prepared_validation_bindings', 'UPDATE prepared_validation_bindings SET grant_record=?', ('broken source grant',))
    mutate(state['conn'], 'prepared_validation_files', 'UPDATE prepared_validation_files SET content=?', (b'changed source bytes',))
    forbidden = {('prepared_validation_bindings', 'grant_record'), ('prepared_validation_snapshots', 'manifest'),
                 ('prepared_validation_files', 'content'), ('organization_policy', 'settings')}
    denied = []
    def authorize(operation, table, column, database, trigger):
        if operation == sqlite3.SQLITE_READ and (table, column) in forbidden:
            denied.append((table, column))
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    state['conn'].set_authorizer(authorize)
    try:
        assert view(state, stream)['unsettledRunCount'] == 0
        assert not denied
    finally:
        state['conn'].set_authorizer(None)


def test_bounded_folder_visibility_keeps_late_blocker_even_when_entries_are_truncated(state, stream, tmp_path):
    envelope = stream['envelope']
    step = next(item for item in envelope['steps'] if item['recipe'] == 'workspace_vitest_ui')
    event = deepcopy(stream['records'][0])
    event['resource'].update(runId=step['runId'], nonce=digest({'run': step['runId']}))
    event['data'].update(containerName=container_name(event['resource']), labels=container_labels(event['resource']),
        stepId=step['id'], stepDefinitionSha256=step['definitionSha256'], recipe=step['recipe'], checkKind=step['checkKind'])
    event['data']['stagingIntent']['path'] = '/private/' + container_name(event['resource'])
    append(state, event)
    result = view(state, stream, limit=1)
    assert result['entries'][0]['state'] == 'reserved_undispatched'
    assert result['entriesTruncated'] and result['unsettledRuns'] == [{'id': step['runId'], 'request_id': envelope['requestId']}]
    folder = _activity(state['conn'], tmp_path)
    assert folder['preparedRuntime']['reservedUnits'] == 2
    assert folder['unsettledProjectRuns'] == [{'id': step['runId'], 'request_id': envelope['requestId']}]
    encoded = json.dumps(folder)
    assert 'private-lease-token-never-export' not in encoded and '/private/' not in encoded


@pytest.mark.parametrize('corruption', ['hash', 'order', 'resource', 'dispatch_source', 'cleanup_mirror', 'resource_mirror', 'result_mirror'])
def test_malformed_event_or_mirror_history_fails_closed(state, stream, corruption):
    append_prefix(state, stream, 7)
    conn = state['conn']
    if corruption in {'hash', 'order', 'resource', 'dispatch_source'}:
        event = deepcopy(stream['records'][0])
        if corruption == 'resource':
            event['resource']['authoritySha256'] = 'a' * 64
            event['data']['labels'] = container_labels(event['resource'])
        if corruption == 'dispatch_source':
            event['data']['snapshotSha256'] = 'a' * 64
        if corruption == 'order':
            mutate(conn, 'prepared_validation_runtime_events', 'UPDATE prepared_validation_runtime_events SET sequence=99 WHERE sequence=1')
        else:
            mutate(conn, 'prepared_validation_runtime_events', 'UPDATE prepared_validation_runtime_events SET record=?,sha256=? WHERE sequence=1',
                   (canonical(event), 'a' * 64 if corruption == 'hash' else digest(event)))
    elif corruption == 'cleanup_mirror':
        mutate(conn, 'prepared_validation_cleanups', 'UPDATE prepared_validation_cleanups SET staging_confirmed=0')
    elif corruption == 'resource_mirror':
        mutate(conn, 'prepared_validation_resources', 'UPDATE prepared_validation_resources SET container_id=?', ('a' * 64,))
    else:
        mutate(conn, 'prepared_validation_results', 'UPDATE prepared_validation_results SET sha256=?', ('a' * 64,))
    with pytest.raises(settlement.PreparedRuntimeSettlementError, match='malformed'):
        view(state, stream)
    state['store'].cancel(stream['envelope']['objectiveId'])
    with pytest.raises(settlement.PreparedRuntimeSettlementError):
        settlement_blocker(conn, stream['envelope']['objectiveId'])


@pytest.mark.parametrize('change', ['runtime_missing', 'guard_missing', 'wrong_column', 'base_missing', 'temp_shadow'])
def test_partial_or_malformed_schema_blocks_settlement_and_recovery(state, stream, change):
    conn = state['conn']
    if change == 'runtime_missing':
        conn.execute('DROP TABLE prepared_validation_runtime_events')
    elif change == 'guard_missing':
        conn.execute('DROP TRIGGER immutable_prepared_runtime_event_delete')
    elif change == 'wrong_column':
        conn.execute('ALTER TABLE prepared_validation_runtime_events RENAME COLUMN sequence TO wrong_sequence')
    elif change == 'temp_shadow':
        conn.execute('CREATE TEMP TABLE prepared_validation_runtime_events (run_id TEXT)')
    else:
        conn.execute('DROP TABLE prepared_validation_cleanups')
    for callback in (lambda: view(state, stream),
                     lambda: settlement.request_has_prepared_reservation(conn, stream['envelope']['requestId'])):
        with pytest.raises(settlement.PreparedRuntimeSettlementError):
            callback()


@pytest.mark.parametrize('dispatched', [False, True])
@pytest.mark.parametrize('terminal', [False, True])
def test_recovery_never_requeues_reserved_request_even_with_new_token(state, stream, dispatched, terminal):
    if terminal:
        append_prefix(state, stream, 7)
    conn, store = state['conn'], state['store']
    request_id = stream['envelope']['requestId']
    with store._write() as write:
        store._stamp_claim_policy(write, request_id)
        write.execute("UPDATE requests SET status='running',token='different-current-token',agent_id='control:project',"
                      'lease=0,attempts=1,execution_count=1 WHERE id=?', (request_id,))
        store._journal_claim(write, request_id, 'different-current-token')
        write.execute('UPDATE organization_execution_journal SET dispatched=? WHERE request_id=?', (int(dispatched), request_id))
    assert store.recover_expired() == 1
    assert conn.execute('SELECT status FROM requests WHERE id=?', (request_id,)).fetchone()[0] == 'pending_intervention'
    assert conn.execute('SELECT count(*) FROM prepared_validation_steps').fetchone()[0] == 2
    assert conn.execute('SELECT count(*) FROM organization_project_reconcile_only').fetchone()[0] == 0


def test_unknown_schema_cannot_make_untouched_journal_recoverable(state, stream):
    store = state['store']
    request_id = stream['envelope']['requestId']
    with store._write() as conn:
        store._stamp_claim_policy(conn, request_id)
        conn.execute("UPDATE requests SET status='running',token='token',agent_id='control:project',lease=0,attempts=1 WHERE id=?", (request_id,))
        store._journal_claim(conn, request_id, 'token')
        conn.execute('DROP TABLE prepared_validation_runtime_events')
    assert store.recover_expired() == 1
    row = state['conn'].execute('SELECT status,reason FROM requests WHERE id=?', (request_id,)).fetchone()
    assert row[0] == 'pending_intervention' and 'prepared history' in row[1]


def test_read_only_and_caller_transaction_ownership(state, stream):
    conn = state['conn']
    conn.execute('BEGIN IMMEDIATE')
    conn.execute("INSERT INTO agents VALUES ('temporary','Temporary','Worker',NULL,'engineering','[]')")
    assert view(state, stream)['reservedUnits'] == 2 and conn.in_transaction
    with pytest.raises(settlement.PreparedRuntimeSettlementError):
        settlement.prepared_runtime_settlement(conn, objective_id='missing')
    assert conn.in_transaction and conn.execute("SELECT 1 FROM agents WHERE id='temporary'").fetchone()
    conn.rollback()
    with sqlite3.connect('file:' + str(state['store'].path) + '?mode=ro', uri=True) as readonly:
        readonly.row_factory = sqlite3.Row
        readonly.execute('PRAGMA query_only=ON')
        assert settlement.prepared_runtime_settlement(readonly)['reservedUnits'] == 2
        assert readonly.total_changes == 0


def test_deny_first_revocation_does_not_parse_malformed_resource_history(folder_profile):
    from eidolon_cli.organization_folder_setup import register_folder
    from eidolon_cli.organization_folder_revocation import folder_access, revoke_folder, review_revocation
    from tests.eidolon_cli.test_organization_folder_revocation import revoke_params
    p = folder_profile
    register_folder(p.home, p.apply)
    params = revoke_params(p)
    with sqlite3.connect(p.path) as conn:
        # Corrupt retained data without changing the owner's structural schema.
        conn.execute('INSERT INTO prepared_validation_runtime_events VALUES (?,?,?,?,?,?)',
                     ('orphan-run', 1, 'dispatch_planned', '{}', 'a' * 64, 0))
    result = revoke_folder(p.home, params)
    assert result['receipt']['accessRevoked'] and result['receipt']['dispatchPaused']
    with pytest.raises(settlement.PreparedRuntimeSettlementError, match='malformed'):
        folder_access(p.home, p.identity['organizationId'])
    with pytest.raises(settlement.PreparedRuntimeSettlementError):
        review_revocation(p.home, {'confirmed': True, 'expectedOrganizationId': p.identity['organizationId'],
            'expectedGeneration': result['receipt']['policyGeneration'], 'alias': params['alias'],
            'grantHash': params['grantHash'], 'reviewVersion': 'a' * 64, 'acknowledgeUncertain': True})
    with sqlite3.connect(p.path) as conn:
        assert conn.execute('SELECT count(*) FROM organization_root_revocations').fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM organization_root_revocation_reviews').fetchone()[0] == 0


@pytest.mark.parametrize('legacy_only', [False, True])
def test_empty_new_and_legacy_storage_are_read_only_zero_usage(state, legacy_only):
    conn, store = state['conn'], state['store']
    with store._write() as write:
        identifier = store._create_objective(write, 'No prepared reservation', idempotency_key='unprepared',
            project_ids=['desktop'], delivery_mode='managed_artifact', required_checks=[])
    request_id = conn.execute('SELECT id FROM requests WHERE objective_id=? LIMIT 1', (identifier,)).fetchone()[0]
    if legacy_only:
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'prepared_validation_%'")]
        conn.execute('PRAGMA foreign_keys=OFF')
        for table in tables:
            assert conn.execute('SELECT count(*) FROM ' + table).fetchone()[0] == 0
            conn.execute('DROP TABLE ' + table)
        conn.execute('PRAGMA foreign_keys=ON')
    before = list(conn.execute('SELECT name,sql FROM sqlite_master'))
    result = settlement.prepared_runtime_settlement(conn, objective_id=identifier)
    assert result['storageState'] == ('absent' if legacy_only else 'present')
    assert result['reservedUnits'] == result['unsettledRunCount'] == result['totalRuns'] == 0
    assert result['entries'] == []
    assert not settlement.request_has_prepared_reservation(conn, request_id)
    assert before == list(conn.execute('SELECT name,sql FROM sqlite_master'))


@pytest.mark.parametrize('status', ['unknown', 'interrupted', 'persistence_failed'])
def test_unknown_terminal_remains_visible_after_exact_cleanup(state, stream, status):
    terminal = stream['records'][-1]['data']
    terminal['receipt']['status'] = status
    terminal['receiptSha256'] = digest(terminal['receipt'])
    append_prefix(state, stream, 7)
    result = view(state, stream)
    assert result['resourceBlockerCount'] == 0
    assert result['unconfirmedOutcomeCount'] == result['unsettledRunCount'] == 1
    assert result['entries'][0]['blockerReasons'] == ['terminal_outcome_unknown']
    assert result['entries'][0]['terminalStatus'] == status


@pytest.mark.parametrize('field,value', [('status', 'invented'), ('status', 1), ('runner', 'untrusted'), ('runnerVersion', True)])
def test_malformed_terminal_metadata_does_not_settle(state, stream, field, value):
    terminal = stream['records'][-1]['data']
    terminal['receipt'][field] = value
    terminal['receiptSha256'] = digest(terminal['receipt'])
    append_prefix(state, stream, 7)
    with pytest.raises(settlement.PreparedRuntimeSettlementError, match='malformed'):
        view(state, stream)


def test_passed_claim_is_still_unverified_and_grants_no_acceptance(state, stream):
    terminal = stream['records'][-1]['data']
    terminal['receipt']['status'] = 'passed'
    terminal['receiptSha256'] = digest(terminal['receipt'])
    append_prefix(state, stream, 7)
    result = view(state, stream)
    assert result['entries'][0]['state'] == 'terminal_unverified'
    assert result['entries'][0]['resourceCleanupSettled']
    assert result['runtimeReceiptsVerified'] is result['acceptanceAuthorized'] is result['replayAllowed'] is False


@pytest.mark.parametrize('table,column', [('dispatches', 'image'), ('cleanups', 'event_id'),
    ('results', 'status'), ('results', 'receipt_sha256')])
def test_oversized_mirror_scalars_fail_closed_without_export(state, stream, table, column):
    append_prefix(state, stream, 7)
    mutate(state['conn'], 'prepared_validation_' + table,
           'UPDATE prepared_validation_' + table + ' SET ' + column + '=?', ('private' * 10000,))
    with pytest.raises(settlement.PreparedRuntimeSettlementError) as error:
        view(state, stream)
    assert len(str(error.value)) < 128 and 'private' not in str(error.value)


def test_additive_runtime_columns_remain_supported_and_guards_are_rechecked(state, stream):
    append_prefix(state, stream, 7)
    conn = state['conn']
    conn.execute('ALTER TABLE prepared_validation_runtime_events ADD COLUMN future_metadata TEXT')
    conn.execute('CREATE TABLE prepared_validation_future_metadata (value TEXT)')
    assert view(state, stream)['unsettledRunCount'] == 0
    conn.execute('BEGIN')
    conn.execute('CREATE TABLE temporary_addition (id TEXT)')
    assert view(state, stream)['unsettledRunCount'] == 0
    conn.rollback()
    # A rollback/reused schema cookie must not reuse a cached structural verdict.
    conn.execute('DROP TRIGGER immutable_prepared_runtime_event_delete')
    with pytest.raises(settlement.PreparedRuntimeSettlementError):
        view(state, stream)


def test_history_page_projection_is_immutable_and_invalidates_after_writes(state, stream):
    from dataclasses import FrozenInstanceError
    from eidolon_cli.organization_history import archive_blocker, live_history_references
    from eidolon_cli.organization_prepared_validation_events import append_prepared_runtime_event
    conn = state['conn']
    identifier = stream['envelope']['objectiveId']
    state['store'].cancel(identifier)
    assert settlement.prepared_runtime_settlement_projection(conn) is None
    conn.execute('BEGIN')
    references = live_history_references(conn)
    projection = references.prepared_projection
    assert isinstance(projection.blocked_objective_ids, frozenset)
    with pytest.raises(FrozenInstanceError):
        projection.blocked_objective_ids = frozenset({'injected'})
    assert archive_blocker(conn, identifier, live_references=references) is None
    append_prepared_runtime_event(conn, stream['records'][0])
    assert 'unconfirmed prepared' in archive_blocker(conn, identifier, live_references=references)
    conn.commit()
    with pytest.raises(ValueError, match='unconfirmed prepared'):
        state['store'].set_objective_archived(identifier, True, expected_revision=0, idempotency_key='fresh-archive')


def test_page_projection_tracks_later_blocked_objective_beyond_display_limit(state, stream):
    # The first set is undispatched; the second objective's event is the blocker.
    obj = objective(state)
    envelope = proposal(state, obj)
    reserve(state, obj, envelope)
    envelope, other = sorted((envelope, stream['envelope']), key=lambda item: item['objectiveId'], reverse=True)
    step = next(item for item in envelope['steps'] if item['recipe'] == 'workspace_typescript')
    event = deepcopy(stream['records'][0])
    event['resource'].update(setId=envelope['setId'], runId=step['runId'], nonce=digest({'run': step['runId']}),
                             authoritySha256=envelope['identity']['authoritySha256'])
    event['data'].update(containerName=container_name(event['resource']), labels=container_labels(event['resource']),
        stepId=step['id'], stepDefinitionSha256=step['definitionSha256'])
    event['data']['stagingIntent']['path'] = '/private/' + container_name(event['resource'])
    append(state, event)
    conn = state['conn']
    conn.execute('BEGIN')
    assert settlement.prepared_runtime_settlement(conn, limit=1)['entries'][0]['state'] == 'reserved_undispatched'
    projection = settlement.prepared_runtime_settlement_projection(conn)
    assert projection.blocked_objective_ids == frozenset({envelope['objectiveId']})
    assert settlement.prepared_runtime_settlement_blocked(conn, envelope['objectiveId'], projection=projection)
    assert not settlement.prepared_runtime_settlement_blocked(conn, other['objectiveId'], projection=projection)
    conn.rollback()
