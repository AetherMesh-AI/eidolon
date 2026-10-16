"""Real SQLite scalar polling stays bounded and never becomes validation proof."""
from contextlib import contextmanager
import json
import sqlite3

import pytest

from eidolon_cli import organization_prepared_validation_status as status
from eidolon_cli import organization_prepared_validation_activity as activity
from tests.eidolon_cli.test_organization_prepared_validation_activity import mutate
from tests.eidolon_cli.test_organization_prepared_validation_events import append_prefix, stream  # noqa: F401
from tests.eidolon_cli.test_organization_prepared_validation_ledger import (
    claims, legacy_run, objective, prepared, proposal, reserve, state,  # noqa: F401
)


def view(state, envelope, **kwargs):
    return status.prepared_validation_status(state['conn'], envelope['objectiveId'], **kwargs)


@contextmanager
def forbid_payload_reads(conn):
    forbidden = {
        'prepared_validation_bindings': {'grant_record', 'binding_record'},
        'prepared_validation_sets': {'envelope', 'lease_token', 'build_run_id'},
        'prepared_validation_snapshots': {'manifest', 'limits'},
        'prepared_validation_results': {'record', 'sha256', 'receipt_sha256'},
        'prepared_validation_aggregates': {'record', 'sha256'},
        'prepared_validation_reviews': {'record', 'sha256', 'contributors', 'source_evidence'},
        'prepared_validation_runtime_events': {'record', 'sha256'},
        'prepared_validation_pair_reviews': {'record', 'sha256', 'approved'},
        'objective_projects': {'binding'}, 'organization_policy': {'settings'},
        'project_run_starts': {'snapshot', 'grant_record', 'token'},
    }
    denied = []
    def authorize(operation, table, column, database, trigger):
        if operation == sqlite3.SQLITE_READ and (table == 'prepared_validation_files' or column in forbidden.get(table, set())):
            denied.append((table, column))
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    conn.set_authorizer(authorize)
    try:
        yield denied
    finally:
        conn.set_authorizer(None)


@pytest.mark.parametrize('length', range(8))
def test_every_recorded_phase_is_scalar_only_and_never_success_authority(state, stream, length):
    append_prefix(state, stream, length)
    with forbid_payload_reads(state['conn']) as denied:
        result = view(state, stream['envelope'])
    first = result['current']['desktop']['steps'][0]
    assert not denied
    assert result['scalarMetadataOnly'] and result['accountingVerified'] is False
    assert result['preparedUnits'] == result['totalUnits'] == 2 and result['totalHistory'] == result['totalSets'] == 1
    assert first['runId'] == stream['run'] and first['dispatched'] == bool(length)
    assert first['resultClaimStatus'] == ('failed' if length == 7 else None)
    assert first['latestRuntimeEventSequence'] == (length or None)
    assert first['cleanupClaimSettled'] == (length >= 6)
    assert result['blockers']['blocked'] and result['blockers']['totalBlockers'] == 2
    assert all(result[key] is False for key in ('sourceBytesVerified', 'runtimeReceiptsVerified', 'executionAuthorized',
        'acceptanceAuthorized', 'sourceDeliveryAuthorized'))
    assert all(step['blockerReasons'] for step in result['history'][0]['steps'])
    encoded = json.dumps(result)
    assert 'private-lease-token-never-export' not in encoded and '/private/' not in encoded


def test_latest_generation_and_full_charges_survive_truncated_failed_history(state, monkeypatch):
    obj = objective(state)
    legacy_run(state, obj)
    envelopes = []
    for generation in range(1, 7):
        envelope = proposal(state, obj, generation)
        reserve(state, obj, envelope)
        if generation != 6:
            claims(state, envelope, fail=generation % 2 == 0)
        envelopes.append(envelope)
    inspected = []
    real_set = status._set
    def counted(conn, row, objective_id):
        inspected.append(row['id'])
        return real_set(conn, row, objective_id)
    monkeypatch.setattr(status, '_set', counted)
    with forbid_payload_reads(state['conn']):
        result = view(state, envelopes[-1], history_limit=2)
    assert result['legacyUnits'] == 1 and result['preparedUnits'] == 12 and result['totalUnits'] == 13
    assert result['totalHistory'] == 6 and result['historyTruncated']
    assert len(inspected) == len(result['history']) == 2
    assert [entry['generation'] for entry in result['history']] == [6, 5]
    assert result['current']['desktop']['setId'] == envelopes[-1]['setId']
    assert result['current']['desktop']['state'] == 'reserved_undispatched'
    assert result['blockers']['totalBlockers'] == 12 and result['blockers']['truncated']
    assert len(result['blockers']['entries']) == 2


def test_large_opaque_payloads_and_twenty_thousand_file_rows_do_not_affect_poll_work(state, stream):
    conn = state['conn']
    append_prefix(state, stream, 7)
    def poll_ticks():
        ticks = 0
        def progress():
            nonlocal ticks
            ticks += 1
            return 0
        conn.set_progress_handler(progress, 100)
        try:
            with forbid_payload_reads(conn) as denied:
                result = view(state, stream['envelope'])
                assert not denied
        finally:
            conn.set_progress_handler(None, 0)
        return ticks, result
    before_ticks, before = poll_ticks()
    # Opaque deliberately unverified inventories: their correctness belongs to
    # deep readers. A status poll must neither load nor attempt to repair them.
    huge_manifest = json.dumps({'files': [{'path': 'private/' + 'x' * 512, 'sha256': 'a' * 64}] * 20000})
    assert len(huge_manifest) > 10 * 1024**2
    for table, column in (('bindings', 'grant_record'), ('snapshots', 'manifest'), ('sets', 'envelope'),
                          ('results', 'record'), ('runtime_events', 'record')):
        mutate(conn, 'prepared_validation_' + table, f'UPDATE prepared_validation_{table} SET {column}=?', (huge_manifest,))
    mutate(conn, 'prepared_validation_files',
        'WITH RECURSIVE inventory(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM inventory WHERE n<20000) '
        'INSERT INTO prepared_validation_files SELECT ?,\'inventory/\'||n,?, ?,1,292,0 FROM inventory',
        (stream['envelope']['setId'], b'x', 'a' * 64))
    assert conn.execute('SELECT count(*) FROM prepared_validation_files').fetchone()[0] > 20000
    after_ticks, after = poll_ticks()
    assert after == before and after_ticks <= before_ticks + 20
    assert not after['accountingVerified'] and not after['runtimeReceiptsVerified']
    with pytest.raises(ValueError):
        activity.prepared_validation_activity(conn, stream['envelope']['objectiveId'])


@pytest.mark.parametrize('change', ['missing', 'column', 'guard', 'shadow'])
def test_partial_or_malformed_schema_is_unavailable_not_an_empty_success(state, change):
    obj = objective(state)
    conn = state['conn']
    if change == 'missing':
        conn.execute('DROP TABLE prepared_validation_runtime_events')
    elif change == 'column':
        conn.execute('ALTER TABLE prepared_validation_results RENAME COLUMN status TO wrong_status')
    elif change == 'guard':
        conn.execute('DROP TRIGGER immutable_prepared_validation_files_delete')
    else:
        conn.execute('CREATE TEMP TABLE prepared_validation_sets (id TEXT)')
    with pytest.raises(status.PreparedValidationStatusError):
        status.prepared_validation_status(conn, obj['ctx']['objective_id'])


@pytest.mark.parametrize('table,column', [('sets', 'request_id'), ('steps', 'step_id'), ('dispatches', 'nonce'),
    ('results', 'status'), ('snapshots', 'snapshot_sha256'), ('runtime_events', 'kind')])
def test_oversized_or_invalid_selected_scalars_fail_before_export(state, stream, table, column):
    append_prefix(state, stream, 7)
    mutate(state['conn'], 'prepared_validation_' + table,
           f'UPDATE prepared_validation_{table} SET {column}=? WHERE rowid=(SELECT max(rowid) FROM prepared_validation_{table})',
           ('private' * 10000,))
    with pytest.raises(status.PreparedValidationStatusError) as error:
        view(state, stream['envelope'])
    assert len(str(error.value)) < 128 and 'private' not in str(error.value)


@pytest.mark.parametrize('counter', ['_MAX_SETS', '_MAX_LEGACY_RUNS', '_MAX_CLEANUPS', '_MAX_RUNTIME_EVENTS'])
def test_count_bounds_fail_closed_instead_of_truncating_usage(state, stream, monkeypatch, counter):
    append_prefix(state, stream, 7)
    legacy_run(state, stream['objective'])
    monkeypatch.setattr(status, counter, 0)
    with pytest.raises(status.PreparedValidationStatusError):
        view(state, stream['envelope'])


@pytest.mark.parametrize('limit', [0, 101, True, '20'])
def test_history_limit_is_bounded(state, stream, limit):
    with pytest.raises(status.PreparedValidationStatusError):
        view(state, stream['envelope'], history_limit=limit)


@pytest.mark.parametrize('legacy_only', [False, True])
def test_absent_and_new_empty_storage_do_not_register_or_authorize(state, legacy_only):
    conn, store = state['conn'], state['store']
    with store._write() as write:
        identifier = store._create_objective(write, 'No prepared data', idempotency_key='none',
            project_ids=['desktop'], delivery_mode='managed_artifact', required_checks=[])
    if legacy_only:
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'prepared_validation_%'")]
        conn.execute('PRAGMA foreign_keys=OFF')
        for table in tables:
            assert conn.execute('SELECT count(*) FROM ' + table).fetchone()[0] == 0
            conn.execute('DROP TABLE ' + table)
        conn.execute('PRAGMA foreign_keys=ON')
    before = list(conn.execute('SELECT name,sql FROM sqlite_master'))
    result = status.prepared_validation_status(conn, identifier)
    assert result['storageState'] == ('absent' if legacy_only else 'present')
    assert result['totalHistory'] == result['preparedUnits'] == result['legacyUnits'] == 0
    assert result['current'] == {} and result['history'] == [] and not result['blockers']['blocked']
    assert result['accountingVerified'] is result['acceptanceAuthorized'] is False
    assert before == list(conn.execute('SELECT name,sql FROM sqlite_master'))


def test_read_only_caller_transaction_and_additive_schema(state, stream):
    conn = state['conn']
    conn.execute('ALTER TABLE prepared_validation_runtime_events ADD COLUMN future_metadata TEXT')
    conn.execute('CREATE TABLE prepared_validation_future_display (value TEXT)')
    conn.execute('BEGIN IMMEDIATE')
    conn.execute("INSERT INTO agents VALUES ('temporary','Temporary','Worker',NULL,'engineering','[]')")
    assert view(state, stream['envelope'])['preparedUnits'] == 2 and conn.in_transaction
    with pytest.raises(status.PreparedValidationStatusError):
        status.prepared_validation_status(conn, 'missing-objective')
    assert conn.in_transaction and conn.execute("SELECT 1 FROM agents WHERE id='temporary'").fetchone()
    conn.rollback()
    with sqlite3.connect('file:' + str(state['store'].path) + '?mode=ro', uri=True) as readonly:
        readonly.row_factory = sqlite3.Row
        readonly.execute('PRAGMA query_only=ON')
        with forbid_payload_reads(readonly):
            assert status.prepared_validation_status(readonly, stream['envelope']['objectiveId'])['preparedUnits'] == 2
        assert readonly.total_changes == 0


@pytest.mark.parametrize('table,column', [('sets', 'generation'), ('sets', 'prior_units'), ('sets', 'round'),
    ('steps', 'units'), ('cleanups', 'sequence'), ('cleanups', 'container_confirmed'), ('runtime_events', 'sequence')])
@pytest.mark.parametrize('value', ['private' * 10000, b'private' * 10000])
def test_damaged_numeric_scalars_are_sql_clipped_before_fetch(state, stream, table, column, value):
    append_prefix(state, stream, 7)
    mutate(state['conn'], 'prepared_validation_' + table,
        f'UPDATE prepared_validation_{table} SET {column}=? WHERE rowid=(SELECT max(rowid) FROM prepared_validation_{table})',
        (value,))
    with pytest.raises(status.PreparedValidationStatusError) as error:
        view(state, stream['envelope'])
    assert len(str(error.value)) < 128 and 'private' not in str(error.value)


def test_pair_review_presence_is_distinct_from_inert_claim_and_never_approval(state, stream):
    conn = state['conn']
    assert not view(state, stream['envelope'])['current']['desktop']['pairReviewRecorded']
    conn.execute('INSERT INTO prepared_validation_pair_reviews VALUES (?,?,?,?,?,?,?)',
        (stream['envelope']['setId'], stream['envelope']['requestId'], 'reviewer-1', 1, 'unverified private payload', 'a' * 64, 0))
    with forbid_payload_reads(conn) as denied:
        result = view(state, stream['envelope'])
    assert not denied and result['current']['desktop']['pairReviewRecorded']
    assert not result['current']['desktop']['reviewClaimPresent']
    assert result['acceptanceAuthorized'] is result['runtimeReceiptsVerified'] is False


def test_real_scalar_examples_for_cross_language_consumer(state, tmp_path, pytestconfig):
    obj = objective(state)
    identifier = obj['ctx']['objective_id']
    examples = [{'label': 'zero', 'value': status.prepared_validation_status(state['conn'], identifier)}]
    envelope = proposal(state, obj)
    reserve(state, obj, envelope)
    examples.append({'label': 'reserved', 'value': view(state, envelope)})
    claims(state, envelope)
    examples.append({'label': 'passed-but-unverified', 'value': view(state, envelope)})
    second = proposal(state, obj, 2)
    reserve(state, obj, second)
    activity._ledger().record_prepared_dispatch_proposal(state['conn'], second['steps'][0]['runId'], nonce='a' * 64)
    examples.append({'label': 'partial', 'value': view(state, second)})
    path = tmp_path / 'prepared-scalar-status-examples.json'
    path.write_text(json.dumps(examples, sort_keys=True), encoding='utf-8')
    assert json.loads(path.read_text(encoding='utf-8')) == examples
    pytestconfig.cache.set('prepared_status/examples', examples)
