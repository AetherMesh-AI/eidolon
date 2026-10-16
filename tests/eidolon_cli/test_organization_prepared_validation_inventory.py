"""Exact row-set fences over real prepared/source storage, without native execution."""
from dataclasses import FrozenInstanceError
import hashlib
import sqlite3

import pytest

from eidolon_cli import organization_prepared_validation_inventory as inventory
from tests.eidolon_cli.test_organization_prepared_validation_lifecycle import (
    running, confirmed, folder_profile, owner, prepared,  # noqa: F401
)
from tests.eidolon_cli.test_organization_prepared_validation_stage_runtime import host

pytestmark = pytest.mark.linux_only


@pytest.fixture
def retained(running, monkeypatch):
    host(monkeypatch, running)
    assert running.store.run_project_stage(running.claim, running.cancel) == {}
    return running


def capture(conn, case, **kwargs):
    return inventory.capture_prepared_inventory(conn, case.objective, ['notes'], **kwargs)


def mutable(conn, *tables):
    for table in tables:
        names = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_schema WHERE type='trigger' AND tbl_name=?", (table,))]
        for name in names:
            conn.execute(f'DROP TRIGGER "{name}"')


def test_inventory_is_private_frozen_exact_and_preserves_caller_transaction(retained):
    with retained.store._connect() as conn:
        with pytest.raises(ValueError, match='transaction'):
            capture(conn, retained)
        conn.execute('BEGIN')
        observed = capture(conn, retained)
        inventory.require_prepared_inventory(conn, observed)
        assert conn.in_transaction
        assert 0 < observed.raw_bytes < inventory._MAX_INVENTORY_BYTES
        assert 0 < observed.row_count < inventory._MAX_INVENTORY_ROWS
        with pytest.raises(FrozenInstanceError):
            observed.objective_id = 'other'
        assert type(observed.rows) is tuple
        assert all(type(table) is tuple and all(type(row) is tuple for row in table) for table in observed.rows)
        with pytest.raises(ValueError, match='private'):
            inventory.require_prepared_inventory(conn, {'rows': observed.rows})
        conn.rollback()


@pytest.mark.parametrize('sql', [
    "UPDATE prepared_validation_files SET content=CAST(printf('%.*c',length(content),'x') AS BLOB)",
    "UPDATE prepared_validation_source_proofs SET record=replace(record,'original','originaL')",
    "UPDATE prepared_validation_runtime_events SET record=replace(record,'version','versioN')",
    "UPDATE prepared_validation_review_scopes SET content=replace(content,'version','versioN')",
    "UPDATE prepared_validation_bindings SET grant_record=replace(grant_record,'version','versioN')",
    "UPDATE organization_prepared_validation_registrations SET candidate=replace(candidate,'version','versioN')",
    "UPDATE tool_receipts SET result=replace(result,'success','succesS')",
    "UPDATE reviews SET evidence_ids=replace(evidence_ids,'evidence','evidencE')",
    "UPDATE requests SET payload=replace(payload,'evidenceHashes','evidenceHashEs') WHERE type='request.review'",
    "UPDATE tasks SET result=NULL WHERE result IS NOT NULL",
    "UPDATE tool_receipts SET reason='' WHERE reason IS NULL",
    "UPDATE tool_receipts SET result=CAST(result AS BLOB)",
])
def test_body_scalar_null_and_storage_type_mutations_fail_without_relying_on_hash(retained, sql):
    with retained.store._connect() as conn:
        conn.execute('BEGIN')
        mutable(conn, *(table for table, _, _ in inventory._FIELDS))
        observed = capture(conn, retained)
        changed = conn.execute(sql)
        assert changed.rowcount > 0
        with pytest.raises(ValueError, match='changed'):
            inventory.require_prepared_inventory(conn, observed)
        conn.rollback()


def test_rehashed_source_evidence_and_same_count_replacement_are_not_equal(retained):
    with retained.store._connect() as conn:
        conn.execute('BEGIN')
        observed = capture(conn, retained)
        row = conn.execute('SELECT id,content FROM evidence LIMIT 1').fetchone()
        content = row['content'].swapcase()
        conn.execute('UPDATE evidence SET content=?,sha256=? WHERE id=?',
                     (content, hashlib.sha256(content.encode()).hexdigest(), row['id']))
        with pytest.raises(ValueError, match='changed'):
            inventory.require_prepared_inventory(conn, observed)
        conn.rollback()
        conn.execute('BEGIN')
        observed = capture(conn, retained)
        # Same logical values and count, but a new row changes tied latest-review
        # ordering. Physical identity is part of the consumed SQL row-set input.
        row = conn.execute('SELECT rowid,* FROM reviews LIMIT 1').fetchone()
        conn.execute('DELETE FROM reviews WHERE request_id=?', (row['request_id'],))
        conn.execute('INSERT INTO reviews(rowid,request_id,task_id,agent_id,approved,summary,evidence_ids,created) '
                     'VALUES (?,?,?,?,?,?,?,?)', (row[0] + 1, *tuple(row[1:])))
        assert capture(conn, retained).raw_bytes == observed.raw_bytes
        with pytest.raises(ValueError, match='changed'):
            inventory.require_prepared_inventory(conn, observed)
        conn.rollback()


@pytest.mark.parametrize('table,sql', [
    ('prepared_validation_cleanups', "INSERT INTO prepared_validation_cleanups SELECT 'extra-cleanup',run_id,sequence+10,nonce,expected_container_name,container_id,container_confirmed,staging_confirmed,created FROM prepared_validation_cleanups LIMIT 1"),
    ('workspace_absent_sources', "INSERT INTO workspace_absent_sources VALUES (:objective,'root0/new-file')"),
    ('workspace_roots', "INSERT INTO workspace_roots VALUES (:objective,'root7','new-root')"),
    ('workspace_heads', "INSERT INTO workspace_heads VALUES (:objective,'root0/unselected',0)"),
    ('evidence', "INSERT INTO evidence SELECT 'new-evidence',objective_id,task_id,:request,content,sha256,summary,created+1 FROM evidence LIMIT 1"),
    ('task_projects', "DELETE FROM task_projects"),
    ('objective_task_rounds', "UPDATE objective_task_rounds SET round=round+1"),
])
def test_new_rows_absence_and_source_membership_invalidate(retained, table, sql):
    with retained.store._connect() as conn:
        conn.execute('BEGIN')
        mutable(conn, table)
        if table == 'workspace_heads':
            conn.execute('INSERT INTO workspaces VALUES (?,?)', ('inventory-workspace', retained.objective))
            conn.execute('INSERT INTO workspace_revisions VALUES (?,?,?,?,?,?,?)',
                         (retained.objective, 'root0/unselected', 0, 'inventory-workspace', '', hashlib.sha256(b'').hexdigest(), 0))
        observed = capture(conn, retained)
        conn.execute(sql, {'objective': retained.objective, 'request': retained.claim['id']})
        with pytest.raises(ValueError, match='changed'):
            inventory.require_prepared_inventory(conn, observed)
        conn.rollback()


def test_new_generation_is_detected_even_with_missing_steps(retained):
    with retained.store._connect() as conn:
        conn.execute('BEGIN')
        mutable(conn, 'prepared_validation_sets')
        conn.execute('PRAGMA ignore_check_constraints=ON')
        observed = capture(conn, retained)
        row = dict(conn.execute('SELECT * FROM prepared_validation_sets LIMIT 1').fetchone())
        row.update(id='new-generation', lease_token='new-lease', generation=row['generation'] + 1,
                   typecheck_run_id='new-types', tests_run_id='new-tests')
        conn.execute('INSERT INTO prepared_validation_sets VALUES (' + ','.join('?' for _ in row) + ')', tuple(row.values()))
        with pytest.raises(ValueError, match='changed'):
            inventory.require_prepared_inventory(conn, observed)
        conn.rollback()


def test_schema_mutation_is_not_an_equal_empty_table(retained):
    with retained.store._connect() as conn:
        conn.execute('BEGIN')
        observed = capture(conn, retained)
        conn.execute('CREATE INDEX inventory_schema_change ON workspace_absent_sources(path)')
        with pytest.raises(ValueError, match='changed'):
            inventory.require_prepared_inventory(conn, observed)
        conn.rollback()


def test_current_finish_lease_and_pair_review_rows_are_not_static_inputs(retained):
    with retained.store._connect() as conn:
        conn.execute('BEGIN')
        observed = capture(conn, retained)
        conn.execute('UPDATE requests SET lease=lease+100,status=\'completed\' WHERE id=?', (retained.claim['id'],))
        # Pair-review schema and contents are freshly checked by the caller.
        conn.execute('CREATE INDEX fresh_review_index ON prepared_validation_pair_reviews(approved)')
        inventory.require_prepared_inventory(conn, observed)
        conn.rollback()


def test_total_cap_fails_before_any_raw_table_fetch_and_deduplicates_overlap(retained, monkeypatch):
    with retained.store._connect() as conn:
        conn.execute('BEGIN')
        observed = capture(conn, retained)
        evidence_index = next(i for i, (table, _, _) in enumerate(inventory._FIELDS) if table == 'evidence')
        assert len(observed.rows[evidence_index]) == conn.execute('SELECT count(*) FROM evidence').fetchone()[0]
        def forbidden(*args, **kwargs):
            pytest.fail('Raw rows fetched before aggregate capacity check')
        monkeypatch.setattr(inventory, '_read_rows', forbidden)
        monkeypatch.setattr(inventory, '_MAX_INVENTORY_BYTES', observed.raw_bytes - 1)
        with pytest.raises(ValueError, match='aggregate'):
            capture(conn, retained)
        monkeypatch.setattr(inventory, '_MAX_INVENTORY_BYTES', observed.raw_bytes)
        monkeypatch.setattr(inventory, '_MAX_INVENTORY_ROWS', observed.row_count - 1)
        with pytest.raises(ValueError, match='aggregate'):
            capture(conn, retained)
        conn.rollback()


def test_shared_static_records_preserve_exact_string_objects_and_reject_changed_identity(retained):
    with retained.store._connect() as conn:
        conn.execute('BEGIN')
        grant = conn.execute('SELECT grant_record,grant_sha256 FROM prepared_validation_bindings').fetchone()
        candidate = conn.execute('SELECT sequence,candidate,candidate_hash FROM organization_prepared_validation_registrations ORDER BY sequence DESC LIMIT 1').fetchone()
        shared = {('grant', 'notes'): (('grant', retained.objective, 'notes'), *tuple(grant)),
                  ('candidate', 'notes'): (('candidate', 'notes', candidate[0]), *tuple(candidate[1:]))}
        observed = capture(conn, retained, shared_static_records=shared)
        strings = [value for rows in observed.rows for row in rows for _, value in row if type(value) is str]
        assert any(value is grant[0] for value in strings)
        assert any(value is candidate[1] for value in strings)
        shared[('grant', 'notes')] = (('grant', 'other-objective', 'notes'), *tuple(grant))
        with pytest.raises(ValueError, match='shared static record'):
            capture(conn, retained, shared_static_records=shared)
        conn.rollback()


def test_connection_requires_row_factory_foreign_keys_and_existing_transaction():
    conn = sqlite3.connect(':memory:')
    conn.execute('BEGIN')
    with pytest.raises(ValueError, match='transaction'):
        inventory.capture_prepared_inventory(conn, 'objective', ['notes'])
    conn.row_factory = sqlite3.Row
    with pytest.raises(ValueError, match='transaction'):
        inventory.capture_prepared_inventory(conn, 'objective', ['notes'])
    conn.rollback()
    conn.close()


def test_sqlite_typed_values_never_coerce_numeric_null_text_or_blob_equality():
    assert inventory._typed(('integer', 1)) != inventory._typed(('real', 1.0))
    assert inventory._typed(('real', -0.0)) != inventory._typed(('real', 0.0))
    assert inventory._typed(('text', '1')) != inventory._typed(('blob', b'1'))
    assert inventory._typed(('null', None)) != inventory._typed(('text', ''))


def test_aggregate_cap_combines_projects_before_fetching_any_raw_rows(retained, monkeypatch):
    with retained.store._connect() as conn:
        conn.execute('BEGIN')
        conn.execute('INSERT INTO objective_projects SELECT objective_id,\'second\',binding '
                     'FROM objective_projects WHERE project_id=\'notes\'')
        conn.execute('INSERT INTO prepared_validation_bindings '
                     'SELECT objective_id,\'second\',grant_record,grant_sha256,binding_record,binding_sha256,created '
                     'FROM prepared_validation_bindings WHERE project_id=\'notes\'')
        first = capture(conn, retained)
        second = inventory.capture_prepared_inventory(conn, retained.objective, ['second'])
        both = inventory.capture_prepared_inventory(conn, retained.objective, ['notes', 'second'])
        assert max(first.raw_bytes, second.raw_bytes) < both.raw_bytes < first.raw_bytes + second.raw_bytes
        # Shared objective source rows are counted once, while the project-bound
        # grants are combined rather than receiving independent per-project caps.
        monkeypatch.setattr(inventory, '_MAX_INVENTORY_BYTES', max(first.raw_bytes, second.raw_bytes))
        def forbidden(*args, **kwargs):
            pytest.fail('A selected project was fetched before whole-inventory admission')
        monkeypatch.setattr(inventory, '_read_rows', forbidden)
        with pytest.raises(ValueError, match='aggregate'):
            inventory.capture_prepared_inventory(conn, retained.objective, ['notes', 'second'])
        conn.rollback()


def test_new_source_review_invalidates_the_latest_selector(retained):
    with retained.store._connect() as conn:
        conn.execute('BEGIN')
        observed = capture(conn, retained)
        request = dict(conn.execute("SELECT * FROM requests WHERE type='request.review' LIMIT 1").fetchone())
        request['id'] = 'later-source-review'
        conn.execute('INSERT INTO requests VALUES (' + ','.join('?' for _ in request) + ')', tuple(request.values()))
        conn.execute('INSERT INTO reviews SELECT ?,task_id,agent_id,approved,summary,evidence_ids,created+1 '
                     'FROM reviews LIMIT 1', (request['id'],))
        with pytest.raises(ValueError, match='changed'):
            inventory.require_prepared_inventory(conn, observed)
        conn.rollback()


def test_new_owner_revision_invalidates_latest_registration_and_pins_old_bound_row(retained):
    with retained.store._connect() as conn:
        conn.execute('BEGIN')
        observed = capture(conn, retained)
        conn.execute('INSERT INTO organization_prepared_validation_registrations '
                     'SELECT sequence+1,project_id,revision+1,organization_id,owner_id,enabled,policy_generation,'
                     'source_revision,candidate,candidate_hash,receipt,receipt_hash '
                     'FROM organization_prepared_validation_registrations ORDER BY sequence DESC LIMIT 1')
        with pytest.raises(ValueError, match='changed'):
            inventory.require_prepared_inventory(conn, observed)
        next_inventory = capture(conn, retained)
        index = next(i for i, (table, _, _) in enumerate(inventory._FIELDS)
                     if table == 'organization_prepared_validation_registrations')
        assert len(next_inventory.rows[index]) == 2
        conn.rollback()


def test_schema_column_change_is_detected_without_rows(retained):
    with retained.store._connect() as conn:
        conn.execute('BEGIN')
        observed = capture(conn, retained)
        conn.execute('ALTER TABLE workspace_absent_sources ADD COLUMN changed TEXT')
        with pytest.raises(ValueError, match='schema'):
            inventory.require_prepared_inventory(conn, observed)
        conn.rollback()


def test_inventory_is_rechecked_against_a_fresh_transaction(retained):
    with retained.store._connect() as conn:
        conn.execute('BEGIN')
        observed = capture(conn, retained)
        conn.rollback()
    with retained.store._write() as conn:
        conn.execute("UPDATE evidence SET summary=summary||' changed'")
    with retained.store._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        with pytest.raises(ValueError, match='changed'):
            inventory.require_prepared_inventory(conn, observed)
        assert conn.in_transaction
        conn.rollback()


def test_source_reuse_schema_absence_invalidates_even_without_reused_rows(retained):
    with retained.store._connect() as conn:
        conn.execute('BEGIN')
        observed = capture(conn, retained)
        conn.execute('DROP TABLE task_repair_inputs')
        with pytest.raises(ValueError, match='changed'):
            inventory.require_prepared_inventory(conn, observed)
        conn.rollback()


def test_unselected_project_binding_is_part_of_task_selection(retained):
    with retained.store._connect() as conn:
        conn.execute('BEGIN')
        mutable(conn, 'objective_projects')
        conn.execute('INSERT INTO objective_projects SELECT objective_id,\'other\',binding '
                     'FROM objective_projects LIMIT 1')
        observed = capture(conn, retained)
        conn.execute("UPDATE objective_projects SET binding=replace(binding,'notes','other') WHERE project_id='other'")
        with pytest.raises(ValueError, match='changed'):
            inventory.require_prepared_inventory(conn, observed)
        conn.rollback()


def test_bounds_precheck_reads_no_raw_proof_values(retained, monkeypatch):
    with retained.store._connect() as conn:
        conn.execute('BEGIN')
        observed = capture(conn, retained)
        def forbidden(*args, **kwargs):
            pytest.fail('Bounds precheck fetched raw proof inputs')
        monkeypatch.setattr(inventory, '_read_rows', forbidden)
        assert inventory.check_prepared_inventory_bounds(conn, retained.objective, ['notes']) == (
            observed.row_count, observed.raw_bytes)
        monkeypatch.setattr(inventory, '_MAX_INVENTORY_BYTES', observed.raw_bytes - 1)
        with pytest.raises(ValueError, match='aggregate'):
            inventory.check_prepared_inventory_bounds(conn, retained.objective, ['notes'])
        conn.rollback()


def test_shared_source_bytes_are_exact_immutable_and_not_copied(retained):
    with retained.store._connect() as conn:
        conn.execute('BEGIN')
        file = conn.execute('SELECT set_id,path,content FROM prepared_validation_files LIMIT 1').fetchone()
        shared = {(file['set_id'], file['path']): file['content']}
        observed = capture(conn, retained, shared_source_files=shared)
        assert any(value is file['content'] for rows in observed.rows for row in rows for _, value in row)
        shared[(file['set_id'], file['path'])] = file['content'] + b'changed'
        with pytest.raises(ValueError, match='shared source file'):
            capture(conn, retained, shared_source_files=shared)
        shared.clear()
        shared[('missing-set', file['path'])] = file['content']
        with pytest.raises(ValueError, match='missing or changed SQL identity'):
            capture(conn, retained, shared_source_files=shared)
        conn.rollback()


def test_foreign_round_control_is_pinned_when_it_changes_current_task_membership(retained):
    from eidolon_cli.organization_acceptance import current_tasks
    with retained.store._connect() as conn:
        conn.execute('BEGIN')
        conn.execute("INSERT INTO objectives SELECT 'foreign-objective','foreign-key',input_hash,title,"
                     'description,priority,created,cancelled FROM objectives WHERE id=?', (retained.objective,))
        conn.execute("INSERT INTO objective_control SELECT 'foreign-objective',criteria,status,round,max_replans,"
                     'max_stages,summary,deliverable_id,delivery_mode,amended_scope,required_checks '
                     'FROM objective_control WHERE objective_id=?', (retained.objective,))
        conn.execute("UPDATE objective_task_rounds SET objective_id='foreign-objective' WHERE objective_id=?",
                     (retained.objective,))
        assert current_tasks(conn, retained.objective)
        observed = capture(conn, retained)
        conn.execute("UPDATE objective_control SET round=round+1 WHERE objective_id='foreign-objective'")
        assert current_tasks(conn, retained.objective) == []
        with pytest.raises(ValueError, match='changed'):
            inventory.require_prepared_inventory(conn, observed)
        conn.rollback()


@pytest.mark.parametrize('before_capture', [True, False])
def test_temp_proof_shadow_is_rejected_before_capture_and_before_reuse(retained, before_capture):
    with retained.store._connect() as conn:
        conn.execute('BEGIN')
        observed = None if before_capture else capture(conn, retained)
        conn.execute('CREATE TEMP VIEW evidence AS SELECT * FROM main.evidence')
        with pytest.raises(ValueError, match='TEMP schema shadows'):
            if before_capture:
                capture(conn, retained)
            else:
                inventory.require_prepared_inventory(conn, observed)
        conn.rollback()
