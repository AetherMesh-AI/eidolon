"""Bounded scalar-only prepared status for routine snapshot polling.

This is a display projection of recorded lifecycle columns, not evidence or an
accounting/admission decision. No grant, envelope, manifest, file inventory,
receipt, event payload, review or source bytes are read. Exact accounting,
activity, runtime settlement and authorization continue to use their full readers.
"""
from contextlib import contextmanager
from functools import lru_cache
import sqlite3

from eidolon_cli import organization_prepared_validation_activity as activity
from eidolon_cli import organization_prepared_validation_contract as contract
from eidolon_cli.organization_prepared_validation_settlement import _runtime_schema
from eidolon_cli.organization_prepared_validation_verification import PREPARED_PAIR_REVIEW_SCHEMA

_MAX_SETS = 10_000
_MAX_LEGACY_RUNS = 10_000
_MAX_PROJECTS = 8
_MAX_HISTORY = 100
_MAX_CLEANUPS = 10_000
_MAX_RUNTIME_EVENTS = 128
_RUNTIME_TABLE = 'prepared_validation_runtime_events'
_RESULT_STATUSES = ('passed', 'failed', 'interrupted', 'cleanup_failed', 'unknown')
_EVENT_KINDS = ('dispatch_planned', 'staging_allocated', 'container_create_planned', 'container_observed',
                'container_start_planned', 'cleanup_observed', 'receipt')
_STEP_KINDS = {'workspace_typescript': 'project_typecheck', 'workspace_vitest_ui': 'project_tests'}
_SET_TEXT = ('id', 'request_id', 'objective_id', 'project_id', 'typecheck_run_id', 'tests_run_id')
_SET_NUMBERS = ('round', 'generation', 'prior_units', 'objective_ceiling', 'owner_ceiling')


class PreparedValidationStatusError(ValueError):
    """Recorded display metadata is unavailable; never interpret it as success."""


@contextmanager
def _read(conn):
    owned = False
    try:
        if conn.row_factory is not sqlite3.Row:
            raise ValueError('Status requires an organization database connection')
        if not conn.in_transaction:
            conn.execute('BEGIN')
            owned = True
        yield
        if owned:
            conn.commit()
    except BaseException as error:
        if owned:
            conn.rollback()
        if isinstance(error, (ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError, sqlite3.Error, UnicodeError)):
            raise PreparedValidationStatusError('Prepared status metadata is incomplete, malformed or exceeds its display bound') from None
        raise


@lru_cache(maxsize=1)
def _pair_schema():
    with sqlite3.connect(':memory:') as conn:
        conn.executescript(PREPARED_PAIR_REVIEW_SCHEMA)
        table = 'prepared_validation_pair_reviews'
        return (activity._columns(conn, table), activity._foreign_keys(conn, table), activity._unique_keys(conn, table),
                {row[0]: activity._sql(row[1]) for row in conn.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'")})


def _storage(conn):
    expected, base_guards = activity._expected_schema()
    columns, foreign_keys, keys, runtime_guards = _runtime_schema()
    expected = {**expected, _RUNTIME_TABLE: (columns, foreign_keys, keys)}
    pair_columns, pair_fks, pair_keys, pair_guards = _pair_schema()
    expected['prepared_validation_pair_reviews'] = (pair_columns, pair_fks, pair_keys)
    guards = {**base_guards, **runtime_guards, **pair_guards}
    objects = dict(conn.execute('SELECT name,type FROM main.sqlite_master'))
    dependencies = set(expected) | {'objectives', 'objective_projects', 'requests', 'project_run_starts'}
    if dependencies & {row[0] for row in conn.execute('SELECT name FROM sqlite_temp_master')}:
        raise ValueError('Temporary objects shadow display dependencies')
    if not any(name.startswith('prepared_validation_') for name in objects):
        return 'absent'
    if any(objects.get(name) != 'table' for name in expected):
        raise ValueError('Prepared display dependencies are incomplete')
    # Validate shape, not full-data FK scans. Those scans traverse retained file
    # inventories and belong to the deep gate readers, never routine polling.
    for name, (columns, foreign_keys, keys) in expected.items():
        actual = activity._columns(conn, name)
        if (any(actual.get(key) != value for key, value in columns.items())
                or not foreign_keys <= activity._foreign_keys(conn, name)
                or not keys <= activity._unique_keys(conn, name)):
            raise ValueError('Prepared display schema changed')
    actual_guards = dict(conn.execute("SELECT name,sql FROM main.sqlite_master WHERE type='trigger'"))
    if any(name not in actual_guards or activity._sql(actual_guards[name]) != sql for name, sql in guards.items()):
        raise ValueError('Prepared display immutable guards changed')
    return 'present'


def _count(conn, table, column, identifier, maximum):
    count = conn.execute(f'SELECT count(*) FROM (SELECT 1 FROM {table} WHERE {column}=? LIMIT ?)',
                         (identifier, maximum + 1)).fetchone()[0]
    if count > maximum:
        raise ValueError('Recorded history exceeds its display bound')
    return count


def _enum(column, values):
    # Constants only. A corrupt oversized/unknown scalar becomes NULL in SQLite,
    # before Python can materialize it or a client could accidentally display it.
    options = ','.join("'" + value + "'" for value in values)
    return f'CASE WHEN {column} IN ({options}) THEN {column} END'


def _number(column, minimum=0, maximum=2**53 - 1):
    return (f"CASE WHEN typeof({column})='integer' AND {column} BETWEEN {minimum} AND {maximum} "
            f'THEN {column} END')


def _integer(value, minimum=0):
    if type(value) is not int or not minimum <= value <= 2**53 - 1:
        raise ValueError('Recorded counter is not an exact bounded integer')
    return value


def _latest(conn, table, run_id, columns, maximum):
    count = _count(conn, table, 'run_id', run_id, maximum)
    row = conn.execute(f'SELECT {_number("sequence", 1)},{columns} FROM {table} WHERE run_id=? ORDER BY sequence DESC LIMIT 1',
                       (run_id,)).fetchone()
    if row is not None and _integer(row[0], 1) != count:
        raise ValueError('Recorded lifecycle sequence is incomplete')
    return row


def _step(conn, step):
    run_id, step_id = contract._id(step['run_id']), contract._id(step['step_id'])
    contract._hash(step['definition_sha256'])
    if step['check_kind'] != _STEP_KINDS.get(step['recipe']) or step['recipe'] not in _STEP_KINDS or step['units'] != 1:
        raise ValueError('Recorded step kind or unit changed')
    _integer(step['units'], 1)
    dispatch = conn.execute(f'SELECT {activity._bounded("nonce", 64)},{activity._bounded("expected_container_name", 128)} '
                            'FROM prepared_validation_dispatches WHERE run_id=?', (run_id,)).fetchone()
    resource = conn.execute(f'SELECT {activity._bounded("container_id", 64)} FROM prepared_validation_resources WHERE run_id=?',
                            (run_id,)).fetchone()
    result = conn.execute(f'SELECT {_enum("status", _RESULT_STATUSES)} FROM prepared_validation_results WHERE run_id=?',
                          (run_id,)).fetchone()
    cleanup = _latest(conn, 'prepared_validation_cleanups', run_id,
        f'{activity._bounded("nonce", 64)},{activity._bounded("expected_container_name", 128)},'
        f'{activity._bounded("container_id", 64)},container_id IS NULL,'
        f'{_number("container_confirmed", 0, 1)},{_number("staging_confirmed", 0, 1)}', _MAX_CLEANUPS)
    event = _latest(conn, _RUNTIME_TABLE, run_id, _enum('kind', _EVENT_KINDS), _MAX_RUNTIME_EVENTS)
    if dispatch:
        contract._hash(dispatch[0])
        if dispatch[1] != 'eidolon-prepared-' + dispatch[0]:
            raise ValueError('Recorded dispatch identity changed')
    elif resource or result or cleanup or event:
        raise ValueError('Undispatched step has lifecycle metadata')
    if resource:
        contract._hash(resource[0])
    if result and result[0] not in _RESULT_STATUSES or event and event[1] not in _EVENT_KINDS:
        raise ValueError('Recorded lifecycle enum changed')
    if cleanup:
        if (tuple(cleanup[1:3]) != tuple(dispatch) or (cleanup[3] is None) != bool(cleanup[4])
                or cleanup[3] is not None and (resource is None or cleanup[3] != resource[0])
                or any(type(value) is not int or value not in (0, 1) for value in cleanup[5:7])):
            raise ValueError('Recorded cleanup identity or flags changed')
    settled_claim = bool(cleanup and resource and cleanup[3] == resource[0] and tuple(cleanup[5:7]) == (1, 1))
    reasons = ['runtime_receipt_unverified'] if dispatch else ['reserved_undispatched']
    if dispatch and (result is None or result[0] in {'unknown', 'interrupted'}):
        reasons.append('execution_outcome_unknown')
    elif result and result[0] != 'passed':
        reasons.append('validation_failed')
    if dispatch and not settled_claim:
        reasons.append('resource_cleanup_unconfirmed')
    return {'runId': run_id, 'stepId': step_id, 'recipe': step['recipe'], 'reservedUnits': 1,
            'dispatched': dispatch is not None, 'resourceClaimed': resource is not None,
            'resultClaimStatus': result[0] if result else None, 'cleanupClaimSettled': settled_claim,
            'latestCleanupSequence': cleanup[0] if cleanup else None,
            'latestRuntimeEventSequence': event[0] if event else None, 'latestRuntimeEventKind': event[1] if event else None,
            'blockerReasons': reasons}


def _set_columns():
    return ','.join([*(activity._bounded(name, 128) + ' AS ' + name for name in _SET_TEXT),
        *(_number(name, 0 if name in {'round', 'prior_units'} else 1) + ' AS ' + name for name in _SET_NUMBERS)])


def _set(conn, row, objective_id):
    for name in _SET_TEXT:
        contract._id(row[name])
    for name in _SET_NUMBERS:
        _integer(row[name], 0 if name in {'round', 'prior_units'} else 1)
    if (row['objective_id'] != objective_id or row['typecheck_run_id'] == row['tests_run_id']
            or row['id'] in {row['typecheck_run_id'], row['tests_run_id']}
            or row['prior_units'] + 2 > min(row['objective_ceiling'], row['owner_ceiling'])):
        raise ValueError('Recorded reservation scalar identity changed')
    request = conn.execute(f'SELECT {activity._bounded("objective_id", 128)} FROM requests WHERE id=?', (row['request_id'],)).fetchone()
    binding = conn.execute('SELECT 1 FROM prepared_validation_bindings WHERE objective_id=? AND project_id=?',
                           (objective_id, row['project_id'])).fetchone()
    if request is None or request[0] != objective_id or binding is None:
        raise ValueError('Recorded reservation has a missing scalar dependency')
    snapshot = conn.execute(f'SELECT {activity._bounded("snapshot_sha256", 64)} FROM prepared_validation_snapshots WHERE set_id=?',
                            (row['id'],)).fetchone()
    if snapshot is None:
        raise ValueError('Recorded snapshot identity missing')
    contract._hash(snapshot[0])
    steps = conn.execute(f'SELECT {activity._bounded("run_id", 128)} AS run_id,'
        f'{activity._bounded("step_id", 128)} AS step_id,{activity._bounded("definition_sha256", 64)} AS definition_sha256,'
        f'{_enum("recipe", _STEP_KINDS)} AS recipe,{_enum("check_kind", _STEP_KINDS.values())} AS check_kind,'
        f'{_number("units", 1, 1)} AS units '
        'FROM prepared_validation_steps WHERE set_id=? LIMIT 3', (row['id'],)).fetchall()
    if (len(steps) != 2 or {step['run_id'] for step in steps} != {row['typecheck_run_id'], row['tests_run_id']}
            or {step['recipe'] for step in steps} != set(_STEP_KINDS)
            or next(step for step in steps if step['recipe'] == 'workspace_typescript')['run_id'] != row['typecheck_run_id']):
        raise ValueError('Recorded reservation is not an exact two-kind pair')
    projected = [_step(conn, step) for step in sorted(steps, key=lambda item: item['recipe'] != 'workspace_typescript')]
    aggregate = conn.execute('SELECT 1 FROM prepared_validation_aggregates WHERE set_id=?', (row['id'],)).fetchone() is not None
    review = conn.execute('SELECT 1 FROM prepared_validation_reviews WHERE set_id=?', (row['id'],)).fetchone() is not None
    pair_review = conn.execute('SELECT 1 FROM prepared_validation_pair_reviews WHERE set_id=?', (row['id'],)).fetchone() is not None
    return {'setId': row['id'], 'requestId': row['request_id'], 'projectId': row['project_id'], 'round': row['round'],
            'generation': row['generation'], 'reservedUnits': 2, 'snapshotSha256': snapshot[0], 'steps': projected,
            'aggregateClaimPresent': aggregate, 'reviewClaimPresent': review, 'pairReviewRecorded': pair_review,
            'state': 'runtime_unverified' if any(step['dispatched'] for step in projected) else 'reserved_undispatched'}


def prepared_validation_status(conn, objective_id, *, history_limit=20):
    """Display latest generations and bounded history, never permission to act.

Only selected rows receive scalar consistency checks. Unselected envelopes and
payloads are deliberately unread, so this result cannot certify ledger integrity
or replace objective_project_reserved_units for authoritative accounting.
"""
    with _read(conn):
        objective_id = contract._id(objective_id)
        if type(history_limit) is not int or not 1 <= history_limit <= _MAX_HISTORY:
            raise ValueError('History display limit is out of range')
        storage = _storage(conn)
        if conn.execute('SELECT 1 FROM objectives WHERE id=?', (objective_id,)).fetchone() is None:
            raise ValueError('Objective not found')
        legacy = _count(conn, 'project_run_starts', 'objective_id', objective_id, _MAX_LEGACY_RUNS)
        total, current, history = 0, {}, []
        if storage == 'present':
            total = _count(conn, 'prepared_validation_sets', 'objective_id', objective_id, _MAX_SETS)
            latest = conn.execute(f'SELECT {activity._bounded("project_id", 128)},{_number("max(generation)", 1)} '
                'FROM prepared_validation_sets WHERE objective_id=? GROUP BY project_id LIMIT ?',
                (objective_id, _MAX_PROJECTS + 1)).fetchall()
            if len(latest) > _MAX_PROJECTS:
                raise ValueError('Project display count exceeds its bound')
            rows = conn.execute(f'SELECT {_set_columns()} FROM prepared_validation_sets WHERE objective_id=? '
                'ORDER BY round DESC,generation DESC,project_id DESC LIMIT ?', (objective_id, history_limit)).fetchall()
            selected = {}
            for row in rows:
                item = _set(conn, row, objective_id)
                selected[item['setId']] = item
                history.append(item)
            for project, generation in latest:
                project, generation = contract._id(project), _integer(generation, 1)
                row = conn.execute(f'SELECT {_set_columns()} FROM prepared_validation_sets '
                    'WHERE objective_id=? AND project_id=? AND generation=?', (objective_id, project, generation)).fetchone()
                if row is None:
                    raise ValueError('Latest recorded generation missing')
                current[project] = selected.get(row['id']) or _set(conn, row, objective_id)
        entries = [{'setId': item['setId'], 'projectId': item['projectId'], 'generation': item['generation'],
                    'runId': step['runId'], 'reasons': step['blockerReasons']} for item in history for step in item['steps']]
        return activity._flags(objectiveId=objective_id, storageState=storage, legacyUnits=legacy, preparedUnits=2 * total,
            totalUnits=legacy + 2 * total, totalSets=total, totalHistory=total, current=current, history=history,
            historyTruncated=total > len(history), scalarMetadataOnly=True, accountingVerified=False,
            blockers={'blocked': bool(total), 'totalBlockers': 2 * total, 'entries': entries[:history_limit],
                      'truncated': 2 * total > min(len(entries), history_limit)})
