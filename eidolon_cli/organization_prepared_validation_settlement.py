"""Read-only resource settlement from complete retained prepared host events.

No live source, current owner grant, runtime, or cleanup action is consulted.
Reservation charges survive cancellation/revocation; unstarted reservations have
no created resource. Dispatched work requires complete, exact event history and
latest positive resource cleanup before it can leave the unsettled projection.
This is not receipt authentication, success verification, or replay authority.
"""
from contextlib import contextmanager
from functools import lru_cache
from dataclasses import dataclass, field
import sqlite3

from eidolon_cli import organization_prepared_validation_activity as activity
from eidolon_cli import organization_prepared_validation_contract as contract
from eidolon_cli.organization_prepared_validation_events import PREPARED_RUNTIME_SCHEMA, retained_runtime_events
from eidolon_cli.organization_project_workspace_receipt import inspect_prepared_runtime_history
from eidolon_cli.organization_project_workspace_resources import RESOURCE_CONTRACT, normalize_runtime_identity

_MAX_TOTAL_SETS = 100_000
_MAX_OBJECTIVES = 11_000
_RUNTIME_TABLE = 'prepared_validation_runtime_events'
_TERMINAL_STATUSES = {'passed', 'failed', 'blocked', 'cancelled', 'timed_out', 'unsupported',
                      'cleanup_failed', 'staging_cleanup_failed', 'persistence_failed', 'unknown', 'interrupted'}


class PreparedRuntimeSettlementError(ValueError):
    """Resource settlement is unknown; resume/archive/replay must fail closed."""


@contextmanager
def _read(conn):
    owned = False
    try:
        if conn.row_factory is not sqlite3.Row:
            raise PreparedRuntimeSettlementError('Prepared settlement requires an organization database connection')
        if not conn.in_transaction:
            conn.execute('BEGIN')
            owned = True
        yield
        if owned:
            conn.commit()
    except BaseException as error:
        if owned:
            conn.rollback()
        if isinstance(error, PreparedRuntimeSettlementError):
            raise
        if isinstance(error, (ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError, sqlite3.Error, UnicodeError)):
            raise PreparedRuntimeSettlementError('Prepared runtime settlement storage is malformed; retained resource review is required') from None
        raise


@lru_cache(maxsize=1)
def _runtime_schema():
    with sqlite3.connect(':memory:') as conn:
        conn.executescript(PREPARED_RUNTIME_SCHEMA)
        return (activity._columns(conn, _RUNTIME_TABLE), activity._foreign_keys(conn, _RUNTIME_TABLE),
                activity._unique_keys(conn, _RUNTIME_TABLE),
                {row[0]: activity._sql(row[1]) for row in conn.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'")})


def _storage(conn):
    storage = activity._storage(conn)
    if storage == 'absent':
        return storage
    objects = dict(conn.execute('SELECT name,type FROM main.sqlite_master'))
    if (objects.get(_RUNTIME_TABLE) != 'table'
            or conn.execute('SELECT 1 FROM sqlite_temp_master WHERE name=?', (_RUNTIME_TABLE,)).fetchone()):
        raise PreparedRuntimeSettlementError('Prepared runtime settlement schema is incomplete; event history is required')
    columns, foreign_keys, keys, guards = _runtime_schema()
    actual = activity._columns(conn, _RUNTIME_TABLE)
    actual_guards = dict(conn.execute("SELECT name,sql FROM main.sqlite_master WHERE type='trigger'"))
    if (any(actual.get(key) != value for key, value in columns.items())
            or not foreign_keys <= activity._foreign_keys(conn, _RUNTIME_TABLE)
            or not keys <= activity._unique_keys(conn, _RUNTIME_TABLE)
            or any(name not in actual_guards or activity._sql(actual_guards[name]) != sql for name, sql in guards.items())
            or conn.execute(f'PRAGMA main.foreign_key_check("{_RUNTIME_TABLE}")').fetchone() is not None):
        raise PreparedRuntimeSettlementError('Prepared runtime settlement schema or event relationships are malformed')
    return storage


def _mirrors(conn, envelope, step, identity, events, inspected):
    resource = conn.execute(f'SELECT {activity._bounded("container_id", 64)} FROM prepared_validation_resources WHERE run_id=?',
                            (step['runId'],)).fetchone()
    if resource:
        contract._hash(resource[0])
    if (resource[0] if resource else None) != inspected['containerId']:
        raise ValueError('Runtime observed resource mirror changed')
    count = conn.execute('SELECT count(*) FROM prepared_validation_cleanups WHERE run_id=?', (step['runId'],)).fetchone()[0]
    cleanups = [event for event in events if event['kind'] == 'cleanup_observed']
    if count != len(cleanups):
        raise ValueError('Runtime cleanup mirror is missing or extraneous')
    for sequence, (row, event) in enumerate(zip(conn.execute(f'SELECT {activity._bounded("event_id", 128)},sequence,'
            f'{activity._bounded("nonce", 64)},{activity._bounded("expected_container_name", 128)},'
            f'{activity._bounded("container_id", 64)},container_confirmed,staging_confirmed,container_id IS NULL '
            'FROM prepared_validation_cleanups WHERE run_id=? ORDER BY sequence',
            (step['runId'],)), cleanups), 1):
        contract._id(row[0])
        data = event['data']
        expected = (sequence, identity['nonce'], 'eidolon-prepared-' + identity['nonce'], data['containerId'],
                    int(data['containerCleanupConfirmed']), int(data['stagingCleanupConfirmed']))
        if (tuple(row[1:7]) != expected or (row[4] is None) != bool(row[7])
                or any(type(row[index]) is not int for index in (1, 5, 6))):
            raise ValueError('Runtime cleanup mirror differs from its exact observation')
    terminal = next((event['data'] for event in events if event['kind'] == 'receipt'), None)
    row = conn.execute(f'SELECT {activity._bounded("record", 8192)},{activity._bounded("sha256", 64)},'
                       f'{activity._bounded("status", 32)},{activity._bounded("receipt_sha256", 64)} '
                       'FROM prepared_validation_results WHERE run_id=?', (step['runId'],)).fetchone()
    if terminal is None:
        if row is not None:
            raise ValueError('Runtime terminal mirror has no event')
        return None
    receipt = terminal['receipt']
    if (receipt.get('runner') != 'eidolon.prepared-workspace' or type(receipt.get('runnerVersion')) is not int
            or receipt['runnerVersion'] != 1 or type(receipt.get('status')) is not str or receipt['status'] not in _TERMINAL_STATUSES):
        raise ValueError('Runtime terminal status is malformed')
    status = receipt['status'] if receipt['status'] in {'passed', 'failed', 'interrupted', 'cleanup_failed', 'unknown'} else 'unknown'
    expected = {**contract.versioned_contract_fields(envelope['version'], 'prepared-validation-result-reference'),
        'setId': envelope['setId'], 'generation': envelope['generation'], 'runId': step['runId'], 'stepId': step['id'],
        **{key: step[key] for key in ('definitionSha256', 'recipe', 'checkKind')},
        'snapshotSha256': envelope['source']['snapshotSha256'],
        **{key: envelope['identity'][key] for key in ('profileSha256', 'bundleSha256', 'authoritySha256')},
        'receiptSha256': terminal['receiptSha256'], 'status': status,
        'cleanupConfirmed': receipt['cleanupConfirmed'], 'stagingCleanupConfirmed': receipt['stagingCleanupConfirmed']}
    if (row is None or activity._ledger()._checked_json(row[0], row[1]) != expected
            or tuple(row[2:]) != (status, terminal['receiptSha256'])):
        raise ValueError('Runtime terminal reference differs from its retained event')
    return receipt['status']


def _run(conn, envelope, step):
    result = {'setId': envelope['setId'], 'requestId': envelope['requestId'], 'objectiveId': envelope['objectiveId'],
              'projectId': envelope['projectId'], 'generation': envelope['generation'], 'runId': step['runId'],
              'reservedUnits': 1, 'dispatched': False, 'resourceCleanupSettled': True,
              'terminalStatus': None, 'state': 'reserved_undispatched', 'blockerReasons': []}
    dispatch = conn.execute(f'SELECT {activity._bounded("nonce", 64)},'
                            f'{activity._bounded("expected_container_name", 128)},{activity._bounded("image", 512)} '
                            'FROM prepared_validation_dispatches WHERE run_id=?',
                            (step['runId'],)).fetchone()
    if dispatch is None:
        return result
    identity = normalize_runtime_identity({'version': 1, 'contract': RESOURCE_CONTRACT,
        'setId': envelope['setId'], 'runId': step['runId'], 'nonce': dispatch[0],
        'authoritySha256': envelope['identity']['authoritySha256']})
    if dispatch[1] != 'eidolon-prepared-' + identity['nonce'] or dispatch[2] != contract.IMAGE:
        raise ValueError('Runtime dispatch identity changed')
    events = retained_runtime_events(conn, step['runId'])
    result.update(dispatched=True, resourceCleanupSettled=False, state='runtime_history_missing')
    if not events:
        result['blockerReasons'] = ['runtime_history_missing']
        return result
    planned = events[0]['data']
    expected = {'snapshotSha256': envelope['source']['snapshotSha256'],
        **{key: envelope['identity'][key] for key in ('profileSha256', 'bundleSha256')},
        'stepId': step['id'], 'stepDefinitionSha256': step['definitionSha256'],
        'recipe': step['recipe'], 'checkKind': step['checkKind']}
    if any(planned.get(key) != value for key, value in expected.items()):
        raise ValueError('Runtime dispatch differs from its retained reservation')
    inspected = inspect_prepared_runtime_history(events, resource_identity=identity)
    status = _mirrors(conn, envelope, step, identity, events, inspected)
    settled = (inspected['containerState'] in {'not_attempted', 'cleanup_confirmed'}
               and inspected['stagingState'] == 'cleanup_confirmed')
    reasons = []
    if not settled:
        reasons.append('resource_cleanup_unconfirmed')
    if status is None or status in {'unknown', 'interrupted', 'persistence_failed'}:
        reasons.append('terminal_outcome_missing' if status is None else 'terminal_outcome_unknown')
    return {**result, 'resourceCleanupSettled': settled, 'state': inspected['state'], 'terminalStatus': status,
            'containerState': inspected['containerState'], 'stagingState': inspected['stagingState'],
            'containerId': inspected['containerId'], 'latestEventSequence': len(events), 'blockerReasons': reasons}


def _project_runtime_settlement(conn, *, objective_id=None, limit=100):
    """Return bounded visibility and complete blocker counts, never permission.

Latest exact cleanup governs resource settlement. Failed receipts stay failed;
missing/unknown terminal outcomes remain visible even after cleanup settles.
Undispatched reservations can be archived after cancellation, with units intact.
"""
    with _read(conn):
        limit = activity._limit(limit)
        storage = _storage(conn)
        total_sets, total_runs, undispatched, resource_blockers, outcome_blockers = 0, 0, 0, 0, 0
        entries, unsettled, unsettled_count, blocked_objectives = [], [], 0, set()
        if objective_id is not None:
            objectives = [contract._id(objective_id)]
        elif storage == 'absent':
            objectives = []
        else:
            sizes = conn.execute('SELECT count(*),count(DISTINCT objective_id) FROM prepared_validation_sets').fetchone()
            if sizes[0] > _MAX_TOTAL_SETS or sizes[1] > _MAX_OBJECTIVES:
                raise PreparedRuntimeSettlementError('Prepared runtime settlement history exceeds its safe read bound')
            objectives = [contract._id(row[0]) for row in conn.execute(
                f'SELECT DISTINCT {activity._bounded("objective_id", 128)} FROM prepared_validation_sets ORDER BY 1')]
        for identifier in objectives:
            _, histories = activity._reservation_history(conn, identifier, storage)
            for history in histories.values():
                total_sets += len(history)
                for envelope in history:
                    for step in contract.validation_execution_order(envelope):
                        entry = _run(conn, envelope, step)
                        total_runs += 1
                        undispatched += int(not entry['dispatched'])
                        resource_blockers += int(not entry['resourceCleanupSettled'])
                        outcome_blockers += int(any(reason.startswith('terminal_outcome_') for reason in entry['blockerReasons']))
                        if len(entries) < limit:
                            entries.append(entry)
                        if entry['blockerReasons']:
                            blocked_objectives.add(identifier)
                            unsettled_count += 1
                            if len(unsettled) < limit:
                                unsettled.append({'id': entry['runId'], 'request_id': entry['requestId']})
        return activity._flags(storageState=storage, objectiveId=objective_id, totalSets=total_sets,
            reservedUnits=total_runs, totalRuns=total_runs, undispatchedRuns=undispatched,
            resourceBlockerCount=resource_blockers, unconfirmedOutcomeCount=outcome_blockers,
            unsettledRunCount=unsettled_count, entries=entries, entriesTruncated=total_runs > len(entries),
            unsettledRuns=unsettled, unsettledRunsTruncated=unsettled_count > len(unsettled), replayAllowed=False), frozenset(blocked_objectives)


def prepared_runtime_settlement(conn, *, objective_id=None, limit=100):
    """Read complete settlement counts with bounded, unverified public entries."""
    return _project_runtime_settlement(conn, objective_id=objective_id, limit=limit)[0]


def _read_revision(conn):
    return (conn.total_changes, *(conn.execute('PRAGMA ' + name).fetchone()[0]
        for name in ('main.schema_version', 'temp.schema_version', 'main.data_version')))


@dataclass(frozen=True)
class PreparedSettlementProjection:
    """Immutable read-page snapshot; never retained or supplied to archive writes.

The caller must keep it local to the same enclosing read transaction. Its full
blocked-objective set is independent of the displayed entry limit. This object
provides no mutation, acceptance, cleanup, or replay authority.
"""
    blocked_objective_ids: frozenset
    _connection: object = field(repr=False, compare=False)
    _revision: tuple = field(repr=False)


def prepared_runtime_settlement_projection(conn):
    """Build one read-page snapshot only within an explicit caller transaction."""
    if not conn.in_transaction:
        return None
    _, blocked = _project_runtime_settlement(conn, limit=1)
    return PreparedSettlementProjection(blocked, conn, _read_revision(conn))


def prepared_runtime_settlement_blocked(conn, objective_id, *, projection=None):
    """Use a local read-page snapshot, or perform a fresh full settlement read."""
    objective_id = contract._id(objective_id)
    if (type(projection) is PreparedSettlementProjection and conn.in_transaction
            and projection._connection is conn and projection._revision == _read_revision(conn)):
        return objective_id in projection.blocked_objective_ids
    return bool(prepared_runtime_settlement(conn, objective_id=objective_id)['unsettledRunCount'])


def request_has_prepared_reservation(conn, request_id):
    """Any reserved generation fences automatic replay, regardless of old token."""
    with _read(conn):
        request_id = contract._id(request_id)
        storage = _storage(conn)
        request = conn.execute(f'SELECT {activity._bounded("objective_id", 128)} FROM requests WHERE id=?',
                               (request_id,)).fetchone()
        if request is None:
            raise PreparedRuntimeSettlementError('Prepared recovery request is missing from retained history')
        _, histories = activity._reservation_history(conn, contract._id(request[0]), storage)
        return any(envelope['requestId'] == request_id for history in histories.values() for envelope in history)
