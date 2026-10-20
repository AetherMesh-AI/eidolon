"""One-write static parsing scopes over real prepared authority and SQLite."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import copy_context
import json
import sqlite3

import pytest

from eidolon_cli import organization_prepared_validation_finish as finish
from eidolon_cli import organization_prepared_validation_ledger as ledger
from eidolon_cli import organization_prepared_validation_records as records
from eidolon_cli import organization_prepared_validation_setup as setup
from eidolon_cli import organization_prepared_validation_stage as stage
from eidolon_cli.organization_project_validation import canonical, digest
from tests.eidolon_cli.test_organization_prepared_validation_stage_runtime import (
    running, owner, prepared, folder_profile, confirmed, host, scalar, events_for,  # noqa: F401
)
from tests.eidolon_cli.test_organization_prepared_validation_lifecycle import change_current_authority

pytestmark = pytest.mark.linux_only


def preload(running, **kwargs):
    return records.prepare_static_record_scope(running.store, running.objective, ('notes',), **kwargs)


def intercept_preload(monkeypatch, action):
    original = stage.prepare_static_record_scope
    @contextmanager
    def observed(*args, **kwargs):
        with original(*args, **kwargs) as token:
            action(token)
            yield token
    monkeypatch.setattr(stage, 'prepare_static_record_scope', observed)


def drop_guards(conn, table):
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (table,)).fetchall():
        conn.execute('DROP TRIGGER "' + name.replace('"', '""') + '"')


def test_every_stage_write_has_fresh_preparse_and_competing_writer_can_progress(running, monkeypatch):
    scopes, grants, candidates, writes = [], [], [], []
    checked, parse, write = ledger._checked_json, setup._json, running.store._write
    def unlocked():
        token = records._SCOPE.get()
        assert token is not None and token.phase == 'preflight'
        with running.store._connect(write=True) as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.rollback()
        return token
    def checked_grant(value, sha256):
        if type(value) is str and '"contract":"prepared-validation-v1"' in value:
            grants.append(unlocked())
        return checked(value, sha256)
    def parsed_candidate(value, maximum, label):
        if label == 'Retained prepared candidate':
            candidates.append(unlocked())
        return parse(value, maximum, label)
    @contextmanager
    def writing():
        token = records._SCOPE.get()
        assert token is not None and token.phase == 'prepared'
        writes.append(token)
        with write() as conn:
            yield conn
    intercept_preload(monkeypatch, scopes.append)
    monkeypatch.setattr(ledger, '_checked_json', checked_grant)
    monkeypatch.setattr(setup, '_json', parsed_candidate)
    monkeypatch.setattr(running.store, '_write', writing)
    calls = host(monkeypatch, running)
    assert stage.run_prepared_project_stage(running.store, running.claim, running.cancel) == {}
    event_count = sum(len(events_for(receipt)) for receipt in calls)
    assert len(scopes) == len(writes) == event_count + len(calls) + 3
    assert len({id(token) for token in scopes}) == len(scopes)
    assert grants == scopes
    assert candidates == [token for token in scopes if token.include_current_registration]
    assert all(not token.alive and not token.static_records and token.static_bytes == 0 for token in scopes)
    assert records._SCOPE.get() is None
    assert scalar(running, 'SELECT sum(units) FROM prepared_validation_steps') == 2
    assert scalar(running, 'SELECT count(*) FROM prepared_validation_aggregates') == 1


@pytest.mark.parametrize('kind', ['grant', 'candidate'])
@pytest.mark.parametrize('change', ['raw', 'hash', 'rehashed', 'binary', 'hash_binary', 'identity'])
def test_exact_raw_hash_type_and_identity_required_under_write(running, kind, change):
    with preload(running) as token:
        identity, raw, sha256 = token.static_records[(kind, 'notes')]
        with running.store._write() as conn, records.bind_static_record_scope(token, running.store, conn):
            if change == 'raw':
                raw += ' '
            elif change == 'hash':
                sha256 = 'f' * 64
            elif change == 'rehashed':
                value = json.loads(raw)
                value['injected'] = True
                raw, sha256 = canonical(value), digest(value)
            elif change == 'binary':
                raw = raw.encode()
            elif change == 'hash_binary':
                sha256 = sha256.encode()
            else:
                identity = (*identity[:2], identity[2] + 1) if kind == 'candidate' else (kind, 'another-objective', 'notes')
            with pytest.raises(ValueError, match='static'):
                records.observed_static_record(conn, identity, raw, sha256)
    assert not token.alive and token.static_records == {}


def test_detached_values_cannot_mutate_another_reader(running):
    with preload(running) as token:
        identity, raw, sha256 = token.static_records[('grant', 'notes')]
        with running.store._write() as conn, records.bind_static_record_scope(token, running.store, conn):
            first = records.observed_static_record(conn, identity, raw, sha256)
            first['profile']['bundleSha256'] = 'f' * 64
            assert records.observed_static_record(conn, identity, raw, sha256) == json.loads(raw)
    with running.store._connect() as conn:
        assert records.observed_static_record(conn, identity, raw, sha256) is None


@pytest.mark.parametrize('foreign', ['connection', 'thread', 'store', 'database', 'missing'])
def test_foreign_or_missing_observation_has_no_expensive_fallback(running, monkeypatch, tmp_path, foreign):
    from eidolon_cli.organization_store import OrganizationStore
    other = OrganizationStore(tmp_path / 'other' / 'state.db') if foreign in {'store', 'database'} else None
    with preload(running) as token:
        identity, raw, sha256 = token.static_records[('grant', 'notes')]
        if foreign == 'store':
            with running.store._write() as conn, pytest.raises(ValueError, match='foreign'):
                with records.bind_static_record_scope(token, other, conn):
                    pytest.fail('Foreign store accepted')
            return
        if foreign == 'database':
            with other._write() as conn, pytest.raises(ValueError, match='exact'):
                with records.bind_static_record_scope(token, running.store, conn):
                    pytest.fail('Foreign database accepted')
            return
        with running.store._write() as conn, records.bind_static_record_scope(token, running.store, conn):
            if foreign == 'connection':
                with running.store._connect() as other_conn, pytest.raises(ValueError, match='foreign'):
                    records.observed_static_record(other_conn, identity, raw, sha256)
            elif foreign == 'thread':
                with ThreadPoolExecutor(max_workers=1) as worker:
                    future = worker.submit(copy_context().run,
                        lambda: records.observed_static_record(conn, identity, raw, sha256))
                    with pytest.raises(ValueError, match='foreign'):
                        future.result()
            else:
                del token.static_records[('grant', 'notes')]
                monkeypatch.setattr(ledger, '_checked_json', lambda *a: pytest.fail('Unexpected in-write parsing'))
                with pytest.raises(ValueError, match='not observed'):
                    ledger._binding(conn, running.objective, 'notes')


@pytest.mark.parametrize('restart', [False, True])
def test_rollback_and_transaction_restart_cannot_reuse_a_bound_scope(running, restart):
    original = scalar(running, 'SELECT title FROM objectives WHERE id=?', (running.objective,))
    with preload(running) as token:
        identity, raw, sha256 = token.static_records[('grant', 'notes')]
        with pytest.raises(sqlite3.OperationalError, match='no such savepoint'):
            with running.store._write() as conn, records.bind_static_record_scope(token, running.store, conn):
                conn.execute('UPDATE objectives SET title=? WHERE id=?', ('Rolled back operation', running.objective))
                conn.rollback()
                if restart:
                    conn.execute('BEGIN IMMEDIATE')
                    conn.execute('UPDATE objectives SET title=? WHERE id=?', ('Unauthorized token reuse', running.objective))
                    # The final savepoint fence rejects this second transaction
                    # even if its fresh raw bytes still match the observation.
                    records.observed_static_record(conn, identity, raw, sha256)
                else:
                    with pytest.raises(ValueError, match='ended'):
                        records.observed_static_record(conn, identity, raw, sha256)
        assert not token.alive and not token.static_records
    assert scalar(running, 'SELECT title FROM objectives WHERE id=?', (running.objective,)) == original
    with preload(running) as retry:
        assert retry is not token
        with running.store._write() as conn, records.bind_static_record_scope(retry, running.store, conn):
            assert ledger._binding(conn, running.objective, 'notes')


def test_scope_cannot_be_nested_or_reused_and_retry_preloads_new_bytes(running):
    first = None
    for _ in range(2):
        with preload(running) as token:
            assert token is not first and token.static_bytes > 0
            with pytest.raises(ValueError, match='nonnested'):
                with preload(running):
                    pytest.fail('Nested scope accepted')
            with running.store._write() as conn:
                with records.bind_static_record_scope(token, running.store, conn):
                    with pytest.raises(ValueError, match='nonnested'):
                        with records.bind_static_record_scope(token, running.store, conn):
                            pytest.fail('Nested binding accepted')
                with pytest.raises(ValueError, match='consumed'):
                    with records.bind_static_record_scope(token, running.store, conn):
                        pytest.fail('Repeated binding accepted')
                conn.rollback()
        assert not token.alive and token.static_bytes == 0 and token.static_records == {}
        first = token


def test_finish_and_static_scopes_cannot_overlap(running):
    with preload(running):
        with pytest.raises(ValueError, match='nonnested'):
            with finish.prepare_finish_verification(running.store, running.claim, {}):
                pytest.fail('Nested finish accepted')
    with running.store._connect() as conn:
        token = finish._FinishToken(running.store, '', '', {}, objective_id=running.objective)
        with finish._static_preflight_scope(token, conn):
            with pytest.raises(ValueError, match='nonnested'):
                with preload(running):
                    pytest.fail('Nested static scope accepted')


def test_static_operation_can_finish_in_unbound_finish_gap(running):
    # Finish has no active authority scope in this gap; its independent exact
    # inventory CAS remains responsible for detecting any intervening changes.
    with finish.prepare_finish_verification(running.store, running.claim, {}) as finish_token:
        assert finish_token is not None and finish._ACTIVE.get() is None
        with preload(running) as token:
            with running.store._write() as conn, records.bind_static_record_scope(token, running.store, conn):
                ledger._binding(conn, running.objective, 'notes')
                request = running.store._owned(conn, running.claim)
                with pytest.raises(ValueError, match='nonnested'):
                    with finish.finish_verification_scope(finish_token, running.store, conn, request):
                        pytest.fail('Finish overlapped active static scope')
        with running.store._write() as conn:
            request = running.store._owned(conn, running.claim)
            with finish.finish_verification_scope(finish_token, running.store, conn, request):
                with pytest.raises(ValueError, match='nonnested'):
                    with preload(running):
                        pytest.fail('Static preload overlapped active finish')
            conn.rollback()


@pytest.mark.parametrize('kind', ['grant', 'candidate'])
@pytest.mark.parametrize('change', ['whitespace', 'rehashed', 'hash', 'binary'])
def test_database_mutation_after_preflight_aborts_before_reservation(running, monkeypatch, kind, change):
    def mutate(_token):
        table, body, hash_column = ('prepared_validation_bindings', 'grant_record', 'grant_sha256') if kind == 'grant' else (setup._TABLE, 'candidate', 'candidate_hash')
        with running.store._write() as conn:
            drop_guards(conn, table)
            raw, sha256 = conn.execute(f'SELECT {body},{hash_column} FROM {table}').fetchone()
            if change == 'whitespace':
                raw += ' '
            elif change == 'rehashed':
                value = json.loads(raw)
                value['injected'] = True
                raw, sha256 = canonical(value), digest(value)
            elif change == 'binary':
                raw = raw.encode()
            else:
                sha256 = 'f' * 64
            conn.execute(f'UPDATE {table} SET {body}=?,{hash_column}=?', (raw, sha256))
    intercept_preload(monkeypatch, mutate)
    calls = host(monkeypatch, running)
    with pytest.raises((ValueError, RuntimeError)):
        stage.run_prepared_project_stage(running.store, running.claim, running.cancel)
    assert not calls
    assert scalar(running, 'SELECT count(*) FROM prepared_validation_sets') == 0
    assert records._SCOPE.get() is None


@pytest.mark.parametrize('loss', ['source', 'registration', 'author', 'lease', 'cancel'])
def test_reservation_still_checks_current_source_authority_and_lease(running, monkeypatch, loss):
    count = 0
    def mutate(_token):
        nonlocal count
        count += 1
        if count != 2:
            return
        if loss == 'cancel':
            running.cancel.set()
        elif loss == 'lease':
            with running.store._write() as conn:
                conn.execute('UPDATE requests SET lease=0 WHERE id=?', (running.claim['id'],))
        else:
            # A concurrent operation has its own ContextVars and database view.
            with ThreadPoolExecutor(max_workers=1) as worker:
                worker.submit(change_current_authority, running, loss).result()
    intercept_preload(monkeypatch, mutate)
    calls = host(monkeypatch, running)
    with pytest.raises((ValueError, RuntimeError)):
        stage.run_prepared_project_stage(running.store, running.claim, running.cancel)
    assert not calls
    # Source bytes are recaptured before dispatch; a concurrently changed file
    # can leave its inert two-unit reservation, but never starts a workload.
    assert scalar(running, 'SELECT count(*) FROM prepared_validation_sets') == int(loss == 'source')


def test_late_cleanup_and_receipt_settle_after_revocation_and_root_removal(running, monkeypatch):
    scopes = []
    intercept_preload(monkeypatch, scopes.append)
    def revoke(receipt, options, _number):
        events = events_for(receipt)
        for event in events[:-2]:
            assert options['record_event'](event)
        change_current_authority(running, 'registration')
        running.owner.root.rename(running.owner.root.with_name('revoked-source'))
        running.owner.dependency.rename(running.owner.dependency.with_name('revoked-dependencies'))
        for event in events[-2:]:
            assert event['kind'] in {'cleanup_observed', 'receipt'}
            assert options['record_event'](event)
        return receipt
    calls = host(monkeypatch, running, revoke)
    with pytest.raises((ValueError, RuntimeError)):
        stage.run_prepared_project_stage(running.store, running.claim, running.cancel)
    assert len(calls) == 1
    assert all(not token.include_current_registration for token in scopes[-2:])
    assert scalar(running, 'SELECT count(*) FROM prepared_validation_results') == 1
    assert scalar(running, 'SELECT count(*) FROM prepared_validation_cleanups') == 1
    assert scalar(running, 'SELECT count(*) FROM prepared_validation_aggregates') == 0
    assert scalar(running, 'SELECT sum(units) FROM prepared_validation_steps') == 2


def test_failed_event_callback_rolls_back_and_discards_static_scope(running, monkeypatch):
    from eidolon_cli import organization_prepared_validation_events as events
    original, scopes = events.append_prepared_runtime_event, []
    def fail(conn, value):
        original(conn, value)
        raise RuntimeError('Synthetic event callback failure')
    intercept_preload(monkeypatch, scopes.append)
    monkeypatch.setattr(events, 'append_prepared_runtime_event', fail)
    calls = host(monkeypatch, running)
    with pytest.raises(RuntimeError, match='callback failure'):
        stage.run_prepared_project_stage(running.store, running.claim, running.cancel)
    assert len(calls) == 1
    assert scalar(running, 'SELECT count(*) FROM prepared_validation_runtime_events') == 0
    assert scalar(running, 'SELECT sum(units) FROM prepared_validation_steps') == 2
    assert all(not token.alive and not token.static_records for token in scopes)
    assert records._SCOPE.get() is None
    with pytest.raises(ValueError, match='already reserved'):
        stage.run_prepared_project_stage(running.store, running.claim, running.cancel)
    assert len(calls) == 1


@pytest.mark.parametrize('remaining', [0, -1])
def test_aggregate_raw_capacity_checked_before_any_stage_write(running, monkeypatch, remaining):
    with running.store._connect() as conn:
        size = conn.execute('SELECT length(CAST(grant_record AS BLOB)) FROM prepared_validation_bindings').fetchone()[0]
        size += conn.execute(f'SELECT length(CAST(candidate AS BLOB)) FROM {setup._TABLE}').fetchone()[0]
    assert records._MAX_STATIC_RECORD_BYTES == 128 * 1024**2
    monkeypatch.setattr(records, '_MAX_STATIC_RECORD_BYTES', size + remaining)
    writes, original = [], running.store._write
    @contextmanager
    def write():
        writes.append(True)
        with original() as conn:
            yield conn
    monkeypatch.setattr(running.store, '_write', write)
    host(monkeypatch, running)
    if remaining == 0:
        assert stage.run_prepared_project_stage(running.store, running.claim, running.cancel) == {}
        assert writes
    else:
        with pytest.raises(ValueError, match='aggregate raw-byte capacity'):
            stage.run_prepared_project_stage(running.store, running.claim, running.cancel)
        assert writes == []
        assert scalar(running, 'SELECT count(*) FROM prepared_validation_sets') == 0
        assert records._SCOPE.get() is None
        monkeypatch.setattr(records, '_MAX_STATIC_RECORD_BYTES', size)
        assert stage.run_prepared_project_stage(running.store, running.claim, running.cancel) == {}


@pytest.mark.parametrize('projects', [[], ['notes', 'notes'], ['notes'] * 9, ['notes;DROP'], 'notes'])
def test_preload_requires_explicit_small_unique_project_selection(running, projects):
    with pytest.raises(ValueError, match='bounded project'):
        with records.prepare_static_record_scope(running.store, running.objective, projects):
            pytest.fail('Invalid project selection accepted')


@pytest.mark.parametrize('column', ['revision', 'policy_generation'])
@pytest.mark.parametrize('binary', [False, True])
def test_preload_rejects_unbounded_numeric_metadata_before_materialization(running, monkeypatch, column, binary):
    value = 'malformed-integer-' * 65536
    with running.store._write() as conn:
        drop_guards(conn, setup._TABLE)
        conn.execute(f'UPDATE {setup._TABLE} SET {column}=?', (value.encode() if binary else value,))
        assert conn.execute(f'SELECT typeof({column}) FROM {setup._TABLE}').fetchone()[0] == ('blob' if binary else 'text')
    monkeypatch.setattr(setup, '_sidecar_rows', lambda *_: pytest.fail('Malformed numeric metadata reached Python materialization'))
    with pytest.raises(ValueError, match='scalar metadata'):
        with preload(running):
            pytest.fail('Malformed numeric metadata accepted')
    assert records._SCOPE.get() is None
