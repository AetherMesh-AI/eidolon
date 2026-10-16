"""Bounded exact SQLite inputs for one prepared-finish compare-and-swap.

This private observation is neither authority nor a receipt/review verdict. The
caller performs strict verification in the captured read transaction, then checks
these same inputs in its write transaction before using that verified result.
No transaction, lease, live-source observation or owner check is supplied here.
"""
from dataclasses import dataclass, field
import json
import sqlite3
import struct

_MAX_INVENTORY_ROWS = 100_000
_MAX_INVENTORY_BYTES = 128 * 1024**2
_SQLITE_TYPES = {'null': type(None), 'integer': int, 'real': float, 'text': str, 'blob': bytes}
_PROJECTS = 'SELECT value FROM json_each(:project_ids)'
_SELECTED = f'objective_id=:objective_id AND project_id IN ({_PROJECTS})'
_SETS = f'SELECT id FROM prepared_validation_sets WHERE {_SELECTED}'
_RUNS = f'SELECT run_id FROM prepared_validation_steps WHERE set_id IN ({_SETS})'
_PROPOSALS = 'SELECT id FROM edit_proposals WHERE objective_id=:objective_id'
_TASKS = ('SELECT id FROM tasks WHERE objective_id=:objective_id UNION '
          'SELECT task_id FROM objective_task_rounds WHERE objective_id=:objective_id UNION '
          'SELECT task_id FROM objective_reused_evidence WHERE objective_id=:objective_id UNION '
          'SELECT task_id FROM edit_proposals WHERE objective_id=:objective_id')
_EVIDENCE = (f'SELECT id FROM evidence WHERE task_id IN ({_TASKS}) UNION '
             'SELECT evidence_id FROM edit_proposals WHERE objective_id=:objective_id UNION '
             'SELECT evidence_id FROM objective_reused_evidence WHERE objective_id=:objective_id')
_REVIEWS = (f'SELECT request_id FROM reviews WHERE task_id IN ({_TASKS}) UNION '
            f'SELECT request_id FROM edit_reviews WHERE proposal_id IN ({_PROPOSALS}) UNION '
            f'SELECT review_request_id FROM edit_applications WHERE proposal_id IN ({_PROPOSALS})')
_RECEIPTS = (f'SELECT receipt_id FROM evidence_tools WHERE evidence_id IN ({_EVIDENCE}) UNION '
             'SELECT source_receipt_id FROM edit_proposals WHERE objective_id=:objective_id')
_REQUESTS = (f'SELECT request_id FROM evidence WHERE id IN ({_EVIDENCE}) UNION {_REVIEWS} UNION '
             'SELECT request_id FROM edit_proposals WHERE objective_id=:objective_id UNION '
             f'SELECT request_id FROM edit_applications WHERE proposal_id IN ({_PROPOSALS}) UNION '
             f'SELECT request_id FROM project_validation_receipts WHERE proposal_id IN ({_PROPOSALS}) UNION '
             f'SELECT request_id FROM tool_receipts WHERE id IN ({_RECEIPTS})')
_REGISTRATIONS = (
    'SELECT g.sequence FROM organization_prepared_validation_registrations g '
    f'WHERE g.project_id IN ({_PROJECTS}) AND g.revision=(SELECT max(r.revision) '
    'FROM organization_prepared_validation_registrations r WHERE r.project_id=g.project_id) UNION '
    'SELECT g.sequence FROM organization_prepared_validation_registrations g '
    'JOIN prepared_validation_registration_bindings b ON '
    'b.project_id=g.project_id AND b.revision=g.revision '
    f'WHERE b.objective_id=:objective_id AND b.project_id IN ({_PROJECTS})')

# A single selector per table deduplicates source/lineage overlap before counting
# or fetching bodies. Keep this inventory explicit: changes need the same review
# as changes to the strict verifier's SQL dependency closure. Ordering includes
# physical row identity because existing latest-review selectors can tie.
_FIELDS = (
    ('objective_projects', 'objective_id=:objective_id OR (objective_id,project_id) IN '
     f'(SELECT objective_id,project_id FROM task_projects WHERE task_id IN ({_TASKS}))',
     'objective_id project_id binding'),
    ('prepared_validation_bindings', _SELECTED,
     'objective_id project_id grant_record grant_sha256 binding_record binding_sha256 created'),
    ('prepared_validation_registration_bindings', _SELECTED,
     'objective_id project_id revision registration_sha256 record sha256 created'),
    ('organization_prepared_validation_registrations', f'sequence IN ({_REGISTRATIONS})',
     'sequence project_id revision organization_id owner_id enabled policy_generation source_revision '
     'candidate candidate_hash receipt receipt_hash'),
    ('organization_prepared_validation_registration_receipts', f'sequence IN ({_REGISTRATIONS})',
     'idempotency_key input_hash sequence'),
    ('prepared_validation_sets', _SELECTED,
     'id request_id lease_token objective_id project_id round generation envelope envelope_sha256 '
     'typecheck_run_id tests_run_id prior_units objective_ceiling owner_ceiling created build_run_id'),
    ('prepared_validation_steps', f'set_id IN ({_SETS})',
     'run_id set_id step_id definition_sha256 recipe check_kind units'),
    ('prepared_validation_snapshots', f'set_id IN ({_SETS})',
     'set_id manifest snapshot_sha256 limits limits_sha256'),
    ('prepared_validation_files', f'set_id IN ({_SETS})',
     'set_id path content sha256 byte_count mode execution_only'),
    ('prepared_validation_source_proofs', f'set_id IN ({_SETS})', 'set_id record sha256'),
    ('prepared_validation_aggregates', f'set_id IN ({_SETS})', 'set_id record sha256 created'),
    ('prepared_validation_review_scopes', f'set_id IN ({_SETS})',
     'set_id id objective_id content sha256'),
    ('prepared_validation_runtime_events', f'run_id IN ({_RUNS})', 'run_id sequence kind record sha256 created'),
    ('prepared_validation_dispatches', f'run_id IN ({_RUNS})',
     'run_id nonce expected_container_name image created'),
    ('prepared_validation_resources', f'run_id IN ({_RUNS})', 'run_id container_id created'),
    ('prepared_validation_results', f'run_id IN ({_RUNS})',
     'run_id status receipt_sha256 record sha256 created'),
    ('prepared_validation_cleanups', f'run_id IN ({_RUNS})',
     'event_id run_id sequence nonce expected_container_name container_id container_confirmed staging_confirmed created'),
    ('tasks', f'id IN ({_TASKS})',
     'id objective_id title description type team priority status dependencies revision author_id result feedback'),
    ('task_projects', f'objective_id=:objective_id OR task_id IN ({_TASKS})',
     'task_id objective_id project_id'),
    ('objective_task_rounds', f'objective_id=:objective_id OR task_id IN ({_TASKS})', 'task_id objective_id round'),
    # current_tasks joins the round row's objective, including corrupt foreign
    # links. The current control is separately fenced by the finish operation
    # and may legitimately advance during rejection; foreign controls may not.
    ('objective_control', 'objective_id<>:objective_id AND objective_id IN '
     f'(SELECT objective_id FROM objective_task_rounds WHERE task_id IN ({_TASKS}))',
     'objective_id criteria status round max_replans max_stages summary deliverable_id '
     'delivery_mode amended_scope required_checks'),
    ('objective_reused_evidence', 'objective_id=:objective_id',
     'objective_id round task_id evidence_id record sha256'),
    ('evidence', f'id IN ({_EVIDENCE})', 'id objective_id task_id request_id content sha256 summary created'),
    ('evidence_tools', f'evidence_id IN ({_EVIDENCE})', 'evidence_id receipt_id'),
    ('tool_receipts', f'id IN ({_RECEIPTS})',
     'id request_id attempt tool_call_id tool_name arguments status result result_sha256 created completed reason'),
    ('reviews', f'request_id IN ({_REVIEWS})', 'request_id task_id agent_id approved summary evidence_ids created'),
    ('requests', f'id IN ({_REQUESTS})',
     'id objective_id task_id type team priority status created agent_id token lease attempts execution_count reason available payload'),
    ('workspaces', 'objective_id=:objective_id OR id IN '
     '(SELECT workspace_id FROM edit_proposals WHERE objective_id=:objective_id)', 'id objective_id'),
    ('edit_proposals', 'objective_id=:objective_id',
     'id request_id objective_id task_id evidence_id evidence_sha256 author_id workspace_id '
     'path base_revision base_sha256 old_text new_text new_content new_sha256 diff sha256 source_receipt_id created'),
    ('edit_project_specs', f'proposal_id IN ({_PROPOSALS})', 'proposal_id files validations sha256'),
    ('edit_applications', f'proposal_id IN ({_PROPOSALS})',
     'proposal_id request_id review_request_id proposal_sha256 applied_revision applied_sha256 created'),
    ('edit_reviews', f'request_id IN ({_REVIEWS})',
     'request_id proposal_id proposal_sha256 evidence_sha256 reviewer_id approved created'),
    ('workspace_revisions', 'objective_id=:objective_id', 'objective_id path revision workspace_id content sha256 created'),
    ('workspace_heads', 'objective_id=:objective_id', 'objective_id path revision'),
    ('workspace_roots', 'objective_id=:objective_id', 'objective_id alias root_identity'),
    ('workspace_absent_sources', 'objective_id=:objective_id', 'objective_id path'),
    ('project_validation_receipts', f'proposal_id IN ({_PROPOSALS})',
     'proposal_id request_id proposal_sha256 result result_sha256 created'),
)


# current_tasks checks this complete schema family before reading reused tasks.
# Their bodies do not participate in source selection, but absence does.
_SCHEMA_ONLY_TABLES = ('task_repair_specifications', 'task_repair_inputs',
                       'objective_repair_decisions', 'task_repair_lineage')


@dataclass(frozen=True)
class _PreparedInventory:
    objective_id: str
    project_ids: tuple[str, ...]
    schema: tuple = field(repr=False)
    rows: tuple = field(repr=False)
    row_count: int
    raw_bytes: int


def _connection(conn):
    if (not isinstance(conn, sqlite3.Connection) or not conn.in_transaction
            or conn.row_factory is not sqlite3.Row
            or conn.execute('PRAGMA foreign_keys').fetchone()[0] != 1
            or conn.text_factory is not str):
        raise ValueError('Prepared inventory requires an existing organization SQLite transaction')
    # Strict readers use unqualified organization table names. No TEMP schema
    # belongs to this connection contract: a shadow could substitute a proof or
    # authority read while leaving the retained main-table rows unchanged.
    if conn.execute('SELECT 1 FROM temp.sqlite_schema LIMIT 1').fetchone() is not None:
        raise ValueError('Prepared inventory requires an organization connection without TEMP schema shadows')


def _parameters(objective_id, project_ids):
    if (type(objective_id) is not str or not 1 <= len(objective_id.encode()) <= 128
            or type(project_ids) not in (tuple, list) or not 1 <= len(project_ids) <= 8
            or any(type(value) is not str or not 1 <= len(value.encode()) <= 64 for value in project_ids)
            or len(set(project_ids)) != len(project_ids)):
        raise ValueError('Prepared inventory requires a bounded objective and unique selected projects')
    return {'objective_id': objective_id, 'project_ids': json.dumps(sorted(project_ids))}


def _typed(row):
    # sqlite3 otherwise equates INTEGER 1 and REAL 1.0; NULL, TEXT and BLOB must
    # remain distinct too. Binary REAL encoding also retains signed zero.
    result = []
    for index in range(0, len(row), 2):
        kind, value = row[index:index + 2]
        expected_type = _SQLITE_TYPES.get(kind)
        if expected_type is None or type(value) is not expected_type:
            raise ValueError('Prepared inventory requires exact immutable SQLite scalar values')
        if kind == 'real':
            value = struct.pack('!d', value)
        result.append((kind, value))
    return tuple(result)


def _read_rows(conn, table, condition, columns, parameters, shared_static_records, shared,
               shared_source_files, shared_files, expected):
    fields = ('rowid', *columns.split())
    projection = ','.join(f'typeof({name}),{name}' for name in fields)
    positions = dict(zip(fields, range(len(fields))))
    result = []
    for row in conn.execute(
            f'SELECT {projection} FROM main.{table} WHERE {condition} ORDER BY rowid', parameters):
        cells = list(_typed(row))
        if shared_static_records is not None and table in {
                'prepared_validation_bindings', 'organization_prepared_validation_registrations'}:
            project = cells[positions['project_id']][1]
            if table == 'prepared_validation_bindings':
                key, raw, hashed = ('grant', project), 'grant_record', 'grant_sha256'
                identity = ('grant', cells[positions['objective_id']][1], project)
            else:
                key, raw, hashed = ('candidate', project), 'candidate', 'candidate_hash'
                identity = ('candidate', project, cells[positions['sequence']][1])
            retained = shared_static_records.get(key)
            # Old bound registration revisions can accompany the current owner
            # row. Only the exact selected cached identity can share storage.
            if retained is not None and (key[0] == 'grant' or retained[0] == identity):
                if (type(retained) is not tuple or len(retained) != 3
                        or cells[positions[raw]][0] != 'text' or cells[positions[hashed]][0] != 'text'
                        or retained != (identity, cells[positions[raw]][1], cells[positions[hashed]][1])):
                    raise ValueError('Prepared shared static record differs from its exact SQL input')
                cells[positions[raw]] = ('text', retained[1])
                shared.add(key)
        if shared_source_files is not None and table == 'prepared_validation_files':
            key = (cells[positions['set_id']][1], cells[positions['path']][1])
            if key in shared_source_files:
                retained = shared_source_files[key]
                if (cells[positions['set_id']][0] != 'text' or cells[positions['path']][0] != 'text'
                        or cells[positions['content']] != ('blob', retained)):
                    raise ValueError('Prepared shared source file differs from its exact SQL input')
                cells[positions['content']] = ('blob', retained)
                shared_files.add(key)
        cells = tuple(cells)
        if expected is not None:
            if len(result) >= len(expected) or cells != expected[len(result)]:
                raise ValueError('Prepared proof inputs changed after their observation')
            result.append(None)
        else:
            result.append(cells)
    if expected is not None and len(result) != len(expected):
        raise ValueError('Prepared proof inputs changed after their observation')
    return tuple(result) if expected is None else ()


def _bounds(conn, parameters):
    names = tuple(table for table, _, _ in _FIELDS) + _SCHEMA_ONLY_TABLES
    selected = ','.join('?' for _ in names)
    schema_where = f'tbl_name IN ({selected})'
    # Bound all schema and raw bodies before fetching *any* of them. Aggregate
    # SQLite lengths include TEXT's UTF-8 encoding, BLOB bytes, and scalars.
    count, size = conn.execute(
        'SELECT count(*),coalesce(sum(length(CAST(type AS BLOB))+length(CAST(name AS BLOB))+'
        'length(CAST(tbl_name AS BLOB))+coalesce(length(CAST(sql AS BLOB)),0)),0) '
        f'FROM main.sqlite_schema WHERE {schema_where}', names).fetchone()
    for table, condition, columns in _FIELDS:
        lengths = '+'.join(f'coalesce(length(CAST({name} AS BLOB)),0)' for name in ('rowid', *columns.split()))
        rows, raw_bytes = conn.execute(
            f'SELECT count(*),coalesce(sum({lengths}),0) FROM main.{table} WHERE {condition}', parameters).fetchone()
        count += rows
        size += raw_bytes
        if count > _MAX_INVENTORY_ROWS or size > _MAX_INVENTORY_BYTES:
            raise ValueError('Prepared inventory exceeds its aggregate row or byte bound')
    schemas = []
    for table, _, columns in _FIELDS:
        metadata = tuple(tuple(row) for row in conn.execute(f'PRAGMA main.table_xinfo({table})'))
        if tuple(row[1] for row in metadata) != tuple(columns.split()):
            raise ValueError('Prepared inventory table schema has unsupported columns')
        schemas.append((table, metadata))
    return count, size, tuple(schemas)


def check_prepared_inventory_bounds(conn, objective_id, project_ids):
    """Admit the whole inventory before any strict proof/source materialization."""
    _connection(conn)
    parameters = _parameters(objective_id, project_ids)
    try:
        return _bounds(conn, parameters)[:2]
    except sqlite3.Error as error:
        raise ValueError('Prepared inventory schema or input rows are unavailable') from error


def _capture(conn, parameters, shared_static_records, shared_source_files=None, expected=None):
    count, size, schemas = _bounds(conn, parameters)
    names = tuple(table for table, _, _ in _FIELDS) + _SCHEMA_ONLY_TABLES
    selected = ','.join('?' for _ in names)
    schema_where = f'tbl_name IN ({selected})'
    schema_rows = tuple(tuple(row) for row in conn.execute(
        f'SELECT type,name,tbl_name,sql FROM main.sqlite_schema WHERE {schema_where} ORDER BY type,name', names))
    schema = (schema_rows, schemas)
    if expected is not None and (schema != expected.schema or count != expected.row_count or size != expected.raw_bytes):
        raise ValueError('Prepared proof inputs changed after their observation')
    shared, shared_files = set(), set()
    rows = tuple(_read_rows(conn, table, condition, columns, parameters, shared_static_records, shared,
                            shared_source_files, shared_files, None if expected is None else expected.rows[index])
                 for index, (table, condition, columns) in enumerate(_FIELDS))
    if shared_static_records is not None:
        expected = {key for key in shared_static_records if key[1] in json.loads(parameters['project_ids'])}
        if shared != expected:
            raise ValueError('Prepared shared static record has a missing or changed SQL identity')
    if shared_source_files is not None and shared_files != set(shared_source_files):
        raise ValueError('Prepared shared source file has a missing or changed SQL identity')
    return schema, rows, count, size


def capture_prepared_inventory(conn, objective_id, project_ids, *, shared_static_records=None,
                               shared_source_files=None):
    """Capture exact bounded proof inputs in the caller's existing transaction."""
    _connection(conn)
    parameters = _parameters(objective_id, project_ids)
    if shared_static_records is not None and (type(shared_static_records) is not dict
            or any(type(key) is not tuple or len(key) != 2 or key[0] not in ('grant', 'candidate')
                   or type(key[1]) is not str or type(value) is not tuple or len(value) != 3
                   or type(value[0]) is not tuple or len(value[0]) != 3
                   or type(value[1]) is not str or type(value[2]) is not str
                   for key, value in shared_static_records.items())):
        raise ValueError('Prepared shared records require exact private static identities')
    if shared_source_files is not None and (type(shared_source_files) is not dict
            or len(shared_source_files) > _MAX_INVENTORY_ROWS
            or any(type(key) is not tuple or len(key) != 2
                   or any(type(part) is not str for part in key) or type(value) is not bytes
                   for key, value in shared_source_files.items())):
        raise ValueError('Prepared shared source files require exact identities and immutable bytes')
    try:
        schema, rows, count, size = _capture(conn, parameters, shared_static_records, shared_source_files)
    except sqlite3.Error as error:
        raise ValueError('Prepared inventory schema or input rows are unavailable') from error
    return _PreparedInventory(objective_id, tuple(sorted(project_ids)), schema, rows, count, size)


def require_prepared_inventory(conn, inventory):
    """Fail closed unless the exact typed input row sets remain unchanged."""
    _connection(conn)
    if type(inventory) is not _PreparedInventory:
        raise ValueError('Prepared finish requires its private input inventory')
    parameters = _parameters(inventory.objective_id, inventory.project_ids)
    try:
        _capture(conn, parameters, None, expected=inventory)
    except sqlite3.Error as error:
        raise ValueError('Prepared inventory schema or input rows changed or are unavailable') from error
