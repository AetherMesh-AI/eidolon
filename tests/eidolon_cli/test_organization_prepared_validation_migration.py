"""Populated v1 upgrade preserves immutable history and restores all constraints."""
from dataclasses import replace
import sqlite3

import pytest

from eidolon_cli import organization_prepared_validation_activity as activity
from eidolon_cli import organization_prepared_validation_ledger as ledger
from eidolon_cli import organization_store as store_module
from eidolon_cli.organization_project_validation import digest
from tests.eidolon_cli.test_organization_prepared_validation_ledger import (
    approve_claim, objective, prepared, proposal, reserve,  # noqa: F401
)


def _v1_reservation_identity(conn, envelope, binding):
    """The historical reader uses only the five original reservation columns."""
    if (envelope['ownerGrantSha256'] != binding['ownerGrantSha256']
            or envelope['objectiveBindingSha256'] != digest(binding)):
        raise ValueError('Reservation objective binding changed')
    row = conn.execute('SELECT typecheck_run_id,tests_run_id,prior_units,objective_ceiling,owner_ceiling '
                       'FROM prepared_validation_sets WHERE id=?', (envelope['setId'],)).fetchone()
    ordered = ledger.contract.validation_execution_order(envelope)
    if (envelope['version'] != 1 or len(ordered) != 2
            or tuple(row[:2]) != tuple(step['runId'] for step in ordered)
            or any(type(value) is not int for value in row[2:])):
        raise ValueError('Set reservation metadata changed')
    if row[2] < 0 or row[2] + 2 > min(row[3:]):
        raise ValueError('Set accounting metadata changed')
    request = conn.execute('SELECT objective_id FROM requests WHERE id=?', (envelope['requestId'],)).fetchone()
    if request is None or request[0] != envelope['objectiveId']:
        raise ValueError('Set request identity changed')


@pytest.fixture
def old_state(tmp_path, prepared, monkeypatch):
    fresh = ledger.PREPARED_VALIDATION_SCHEMA
    with monkeypatch.context() as old:
        old.setattr(store_module, 'ORGANIZATION_SCHEMA',
                    store_module.ORGANIZATION_SCHEMA.replace(fresh, ledger.PREPARED_VALIDATION_V1_SCHEMA))
        old.setattr(ledger, 'PREPARED_VALIDATION_SCHEMA', ledger.PREPARED_VALIDATION_V1_SCHEMA)
        # Model the legacy writer solely while seeding the genuine old schema.
        # Restore the new read guard before testing migration or retained reads.
        old.setattr(ledger, 'validate_prepared_generated_schema', lambda conn: None)
        old.setattr(activity, '_reservation_identity', _v1_reservation_identity)
        activity._expected_schema.cache_clear()
        store = store_module.OrganizationStore(tmp_path / 'old.db', replace(prepared['settings'], max_project_runs=20))
        conn = sqlite3.connect(store.path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute("INSERT INTO agents VALUES ('reviewer-1','Reviewer','Worker',NULL,'engineering','[]')")
        state = {'store': store, 'conn': conn, 'prepared': prepared}
        obj = objective(state)
        value = proposal(state, obj)
        reserve(state, obj, value)
        approve_claim(state, obj, value)
        conn.execute('CREATE INDEX extra_prepared_recipe_index ON prepared_validation_steps(recipe,run_id)')
        conn.execute('CREATE TRIGGER extra_prepared_guard BEFORE INSERT ON prepared_validation_steps BEGIN SELECT 1; END')
        state.update(obj=obj, value=value)
        state['usage'] = activity.objective_project_reserved_units(conn, obj['ctx']['objective_id'])
    activity._expected_schema.cache_clear()
    yield state
    conn.close()
    activity._expected_schema.cache_clear()


def retained_rows(conn):
    rows = {}
    for table in ledger.PREPARED_VALIDATION_TABLES:
        # Virtual values have no historical bytes. Compare all real stored fields.
        columns = [row[1] for row in conn.execute('PRAGMA table_info(' + table + ')')]
        rows[table] = [tuple(row) for row in conn.execute('SELECT rowid,' + ','.join(columns) + ' FROM ' + table + ' ORDER BY rowid')]
    return rows


def objects(conn):
    return [tuple(row) for row in conn.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')]


def test_populated_migration_keeps_every_row_hash_charge_and_extra_guard(old_state):
    conn = old_state['conn']
    before = retained_rows(conn)
    original_objects = {row[1]: row for row in objects(conn)}
    assert ledger.migrate_prepared_validation_schema(conn) is True
    assert retained_rows(conn) == before
    assert ledger.migrate_prepared_validation_schema(conn) is False
    assert retained_rows(conn) == before
    ledger.validate_prepared_generated_schema(conn)
    assert conn.execute('PRAGMA foreign_key_check').fetchone() is None
    assert conn.execute('PRAGMA foreign_keys').fetchone()[0] == 1
    assert conn.execute('PRAGMA legacy_alter_table').fetchone()[0] == 0
    assert activity.objective_project_reserved_units(conn, old_state['obj']['ctx']['objective_id']) == old_state['usage']
    assert ledger.inspect_latest_prepared_proposal(conn, context=old_state['obj']['ctx'])['setId'] == old_state['value']['setId']
    upgraded = {row[1]: row for row in objects(conn)}
    for name, item in original_objects.items():
        if item[0] in {'trigger', 'index'} and name != 'prepared_validation_step_binding':
            assert upgraded[name] == item
    for action in ('UPDATE prepared_validation_steps SET units=units', 'DELETE FROM prepared_validation_steps'):
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            conn.execute(action)


@pytest.mark.parametrize('failure', ['pragma', 'copy', 'rename', 'guard'])
def test_late_upgrade_failures_rollback_history_and_restore_connection_pragmas(old_state, failure):
    conn = old_state['conn']
    before_rows, before_schema = retained_rows(conn), objects(conn)
    blocked = []
    def deny(action, first, second, *_):
        match = {
            'pragma': action == sqlite3.SQLITE_PRAGMA and first == 'legacy_alter_table' and second == 'ON',
            'copy': action == sqlite3.SQLITE_INSERT and first == 'prepared_validation_steps_v2_migration',
            'rename': action == sqlite3.SQLITE_ALTER_TABLE and second == 'prepared_validation_steps_v2_migration',
            'guard': action == sqlite3.SQLITE_CREATE_TRIGGER and first == 'prepared_validation_build_binding',
        }[failure]
        if match:
            blocked.append(True)
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    conn.set_authorizer(deny)
    try:
        with pytest.raises(sqlite3.DatabaseError, match='authorized'):
            ledger.migrate_prepared_validation_schema(conn)
    finally:
        conn.set_authorizer(None)
    assert blocked
    assert conn.in_transaction is False
    assert conn.execute('PRAGMA foreign_keys').fetchone()[0] == 1
    assert conn.execute('PRAGMA legacy_alter_table').fetchone()[0] == 0
    assert retained_rows(conn) == before_rows
    assert objects(conn) == before_schema
    assert conn.execute('PRAGMA foreign_key_check').fetchone() is None
    assert ledger.migrate_prepared_validation_schema(conn) is True
    assert retained_rows(conn) == before_rows


@pytest.mark.parametrize('drift', ['missing_guard', 'changed_guard', 'extra_column'])
def test_migration_does_not_repair_drifted_v1_history(old_state, drift):
    conn = old_state['conn']
    if drift == 'extra_column':
        conn.execute('ALTER TABLE prepared_validation_steps ADD COLUMN unreviewed TEXT')
    else:
        conn.execute('DROP TRIGGER immutable_prepared_validation_steps_delete')
        if drift == 'changed_guard':
            conn.execute('CREATE TRIGGER immutable_prepared_validation_steps_delete BEFORE DELETE ON prepared_validation_steps BEGIN SELECT 1; END')
    before = objects(conn)
    with pytest.raises(ValueError, match='schema|guards'):
        ledger.migrate_prepared_validation_schema(conn)
    assert objects(conn) == before
    assert conn.execute('PRAGMA foreign_keys').fetchone()[0] == 1


def test_read_only_generated_guard_detects_expression_change_with_same_columns_and_fk():
    conn = sqlite3.connect(':memory:', isolation_level=None)
    try:
        changed = ledger.PREPARED_VALIDATION_SCHEMA.replace(ledger._BUILD_RUN_EXPRESSION,
            "CASE WHEN json_extract(envelope,'$.version')=2 THEN 'forged-build' END", 1)
        conn.executescript(changed)
        decoy = ledger._BUILD_RUN_COLUMN.replace("'", "''")
        conn.execute("ALTER TABLE prepared_validation_sets ADD COLUMN decoy TEXT DEFAULT '" + decoy + "'")
        before = objects(conn)
        with pytest.raises(ValueError, match='schema guards'):
            ledger.validate_prepared_generated_schema(conn)
        assert objects(conn) == before
    finally:
        conn.close()


def test_generated_guard_preserves_inert_additive_columns():
    conn = sqlite3.connect(':memory:', isolation_level=None)
    try:
        conn.executescript(ledger.PREPARED_VALIDATION_SCHEMA)
        conn.execute("ALTER TABLE prepared_validation_sets ADD COLUMN future_metadata TEXT DEFAULT 'x,(y),''z'''")
        before = objects(conn)
        ledger.validate_prepared_generated_schema(conn)
        assert objects(conn) == before
    finally:
        conn.close()


def test_concurrent_openers_upgrade_once_without_losing_history(old_state, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    barrier = threading.Barrier(2)
    original = ledger._schema_objects
    local = threading.local()
    path = str(old_state['store'].path)
    before = retained_rows(old_state['conn'])
    def snapshot(conn):
        result = original(conn)
        database = conn.execute('PRAGMA database_list').fetchone()[2]
        if database == path and not getattr(local, 'observed', False):
            local.observed = True
            barrier.wait(timeout=10)
        return result
    monkeypatch.setattr(ledger, '_schema_objects', snapshot)
    def migrate(_):
        conn = sqlite3.connect(path, isolation_level=None, timeout=10)
        try:
            conn.execute('PRAGMA foreign_keys=ON')
            changed = ledger.migrate_prepared_validation_schema(conn)
            assert conn.execute('PRAGMA foreign_keys').fetchone()[0] == 1
            assert conn.execute('PRAGMA legacy_alter_table').fetchone()[0] == 0
            return changed
        finally:
            conn.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(migrate, range(2))) == [False, True]
    assert retained_rows(old_state['conn']) == before
    ledger.validate_prepared_generated_schema(old_state['conn'])


def test_generated_guard_rejects_comment_decoy_and_quoted_forged_column():
    conn = sqlite3.connect(':memory:', isolation_level=None)
    try:
        changed = ledger.PREPARED_VALIDATION_SCHEMA.replace(ledger._BUILD_RUN_COLUMN,
            '"build_run_id" TEXT GENERATED ALWAYS AS (NULL) VIRTUAL '
            'REFERENCES prepared_validation_steps(run_id) DEFERRABLE INITIALLY DEFERRED', 1)
        conn.executescript(changed)
        conn.execute('ALTER TABLE prepared_validation_sets ADD COLUMN decoy TEXT /* , ' + ledger._BUILD_RUN_COLUMN + ', */')
        before = objects(conn)
        with pytest.raises(ValueError, match='schema guards'):
            ledger.validate_prepared_generated_schema(conn)
        assert objects(conn) == before
    finally:
        conn.close()
