"""Read-only, fail-closed accounting and activity for prepared reservations.

Every committed set costs two units forever, irrespective of dispatch or result.
This module projects retained CLAIMS, never authenticated runtime evidence. Even
apparently successful dispatch/result/cleanup claims remain execution blockers
until a separately reviewed trusted runtime-event reader is integrated. Extra
schema tables are permitted, but are not implicitly evidence.

These metadata projections do not load or rehash historical source BLOBs. Exact
source-byte verification remains reconstruct_prepared_snapshot's responsibility
before use. No lease token, source bytes, or dependency/root paths are exported.
"""
from contextlib import contextmanager
from functools import lru_cache
import hashlib
import json
import sqlite3

from eidolon_cli import organization_prepared_validation_contract as contract
from eidolon_cli.organization_project_dependency_bundle import _json
from eidolon_cli.organization_project_snapshot_bundle import ProjectSnapshotLimits, _validate_selection
from eidolon_cli.organization_project_validation import digest

_MAX_SETS = 10_000
_MAX_OBSERVATIONS = 10_000
_MAX_OUTPUT = 1_000
_GOAL_TABLES = {'organization_goal_revisions', 'organization_goal_members', 'organization_goal_receipts'}


class PreparedValidationProjectionError(ValueError):
    """Malformed or incomplete storage cannot be treated as idle or zero usage."""


def _ledger():
    # Deliberately late: goal_allowances will consume this module, while the
    # proposal ledger currently imports goal_allowances for its write admission.
    from eidolon_cli import organization_prepared_validation_ledger
    return organization_prepared_validation_ledger


def _flags(**values):
    return {**values, 'sourceBytesVerified': False, 'runtimeReceiptsVerified': False,
            'executionAuthorized': False, 'acceptanceAuthorized': False, 'sourceDeliveryAuthorized': False}


@contextmanager
def _read(conn):
    owned = False
    try:
        if conn.row_factory is not sqlite3.Row:
            raise ValueError('Projection requires an organization sqlite3.Row connection')
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
            # Retained text may contain secrets; do not echo malformed records.
            raise PreparedValidationProjectionError('Prepared accounting/activity storage is incomplete or malformed') from None
        raise


def _columns(conn, name):
    return {row[1]: (row[2].upper(), row[3], row[5]) for row in conn.execute(f'PRAGMA main.table_info("{name}")')}


def _foreign_keys(conn, name):
    groups = {}
    for row in conn.execute(f'PRAGMA main.foreign_key_list("{name}")'):
        groups.setdefault(row[0], []).append(tuple(row)[1:])
    return {tuple(sorted(group)) for group in groups.values()}


def _unique_keys(conn, name):
    return {tuple(item[2] for item in conn.execute(f'PRAGMA main.index_info("{row[1]}")'))
            for row in conn.execute(f'PRAGMA main.index_list("{name}")') if row[2] and not row[4]}


def _sql(value):
    return ' '.join(value.split()).replace('IF NOT EXISTS ', '')


@lru_cache(maxsize=1)
def _expected_schema():
    # Build the reference schema only in a private, ephemeral memory database.
    # Never register, repair, or migrate anything in the caller's connection.
    with sqlite3.connect(':memory:') as reference:
        reference.executescript(_ledger().PREPARED_VALIDATION_SCHEMA)
        tables = {name: (_columns(reference, name), _foreign_keys(reference, name), _unique_keys(reference, name))
                  for name in _ledger().PREPARED_VALIDATION_TABLES}
        triggers = {row[0]: _sql(row[1]) for row in reference.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'")}
        return tables, triggers


def _storage(conn):
    from eidolon_cli.organization_prepared_validation_ledger import validate_prepared_generated_schema
    expected, guards = _expected_schema()
    objects = dict(conn.execute('SELECT name,type FROM main.sqlite_master'))
    shadows = {row[0] for row in conn.execute('SELECT name FROM sqlite_temp_master')}
    dependencies = set(expected) | _GOAL_TABLES | {'objectives', 'objective_projects', 'requests', 'project_run_starts'}
    if shadows & dependencies:
        raise ValueError('Temporary objects may not shadow accounting storage')
    present = {name for name in objects if name.startswith('prepared_validation_')}
    if not present:
        return 'absent'
    validate_prepared_generated_schema(conn)
    if any(objects.get(name) != 'table' for name in expected):
        raise ValueError('Prepared storage requires every base table')
    for name, (columns, foreign_keys, unique_keys) in expected.items():
        actual = _columns(conn, name)
        if any(actual.get(key) != value for key, value in columns.items()):
            raise ValueError('Prepared storage required columns changed')
        if not foreign_keys <= _foreign_keys(conn, name) or not unique_keys <= _unique_keys(conn, name):
            raise ValueError('Prepared storage required relationships changed')
        if conn.execute(f'PRAGMA main.foreign_key_check("{name}")').fetchone() is not None:
            raise ValueError('Prepared storage contains an orphan')
    actual_guards = dict(conn.execute("SELECT name,sql FROM main.sqlite_master WHERE type='trigger'"))
    if any(name not in actual_guards or _sql(actual_guards[name]) != sql for name, sql in guards.items()):
        raise ValueError('Prepared immutable storage guards changed')
    return 'present'


def prepared_validation_storage_state(conn):
    """Return absent only when no prepared objects exist; partial storage raises."""
    with _read(conn):
        return _storage(conn)


def _bounded(column, limit):
    return _ledger()._bounded_text(column, limit)


def _record(row):
    if row is None:
        return None
    return _ledger()._checked_json(row[0], row[1])


def _snapshot_metadata(conn, envelope, grant):
    row = conn.execute(f'SELECT {_bounded("manifest", 8 * 1024**2)},snapshot_sha256,'
        f'{_bounded("limits", 4096)},limits_sha256 FROM prepared_validation_snapshots WHERE set_id=?',
        (envelope['setId'],)).fetchone()
    if row is None or type(row[0]) is not str:
        raise ValueError('Missing bounded snapshot metadata')
    manifest = _json(row[0].encode(), 8 * 1024**2, 'Snapshot metadata')
    limits_record = _ledger()._checked_json(row[2], row[3])
    limits = ProjectSnapshotLimits(**limits_record)
    if limits_record != grant['source']['limits']:
        raise ValueError('Snapshot limits changed')
    metadata = conn.execute('SELECT count(*),COALESCE(sum(byte_count),0),COALESCE(max(byte_count),0) '
                            'FROM prepared_validation_files WHERE set_id=?', (envelope['setId'],)).fetchone()
    if not 1 <= metadata[0] <= limits.max_files or metadata[1] > limits.max_total_bytes or metadata[2] > limits.max_file_bytes:
        raise ValueError('Snapshot metadata exceeds source limits')
    files = []
    for item in conn.execute('SELECT path,sha256,byte_count,mode,execution_only,typeof(content),length(content) '
                              'FROM prepared_validation_files WHERE set_id=? ORDER BY path', (envelope['setId'],)):
        if (type(item[2]) is not int or item[2] < 0 or type(item[3]) is not int or item[3] not in (292, 365)
                or type(item[4]) is not int or item[4] not in (0, 1) or item[5] != 'blob' or item[6] != item[2]):
            raise ValueError('Snapshot file metadata changed')
        contract._hash(item[1])
        exception = grant['source']['executionOnlyHashes'].get(item[0])
        if bool(item[4]) != (exception is not None) or exception is not None and item[1] != exception:
            raise ValueError('Snapshot execution-only identity changed')
        files.append({'path': item[0], 'sha256': item[1], 'bytes': item[2], 'mode': item[3], 'executionOnly': bool(item[4])})
    paths = list(_validate_selection([item['path'] for item in files], limits))
    root = grant['root']
    expected = {'version': 1, 'rootAlias': root['alias'], 'rootIdentity': {'device': root['device'], 'inode': root['inode']},
                'files': files, 'totalBytes': metadata[1]}
    actual_hash = hashlib.sha256(json.dumps(expected, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')).hexdigest()
    if manifest != expected or paths != grant['source']['files'] or actual_hash != row[1] or row[1] != envelope['source']['snapshotSha256']:
        raise ValueError('Snapshot metadata has missing, extra, or changed source')


def _context(envelope, grant, binding):
    return {'profile': grant['profile'], 'organization_id': grant['organizationId'], 'owner_id': grant['ownerId'],
            'owner_grant_sha256': digest(grant), 'objective_binding_sha256': digest(binding),
            'objective_id': envelope['objectiveId'], 'project_id': envelope['projectId'],
            'round_number': envelope['round'], 'policy_generation': envelope['policy']['generation'],
            'policy_fingerprint': envelope['policy']['fingerprint'], 'snapshot_sha256': envelope['source']['snapshotSha256'],
            'preimage_sha256': envelope['source']['preimageSha256'], 'managed_revisions_sha256': envelope['source']['managedRevisionsSha256']}


def _step(conn, envelope, step, result):
    run_id = step['runId']
    dispatch = conn.execute('SELECT nonce,expected_container_name,image FROM prepared_validation_dispatches WHERE run_id=?', (run_id,)).fetchone()
    resource = conn.execute('SELECT container_id FROM prepared_validation_resources WHERE run_id=?', (run_id,)).fetchone()
    count = conn.execute('SELECT count(*) FROM prepared_validation_cleanups WHERE run_id=?', (run_id,)).fetchone()[0]
    if count > _MAX_OBSERVATIONS:
        raise ValueError('Cleanup history exceeds its bound')
    latest = None
    if dispatch:
        contract._hash(dispatch[0])
        if dispatch[1] != 'eidolon-prepared-' + dispatch[0] or dispatch[2] != contract.IMAGE:
            raise ValueError('Dispatch identity changed')
    elif resource or count or result:
        raise ValueError('Undispatched step has execution claims')
    if resource:
        contract._hash(resource[0])
    for sequence, observation in enumerate(conn.execute('SELECT event_id,sequence,nonce,expected_container_name,container_id,'
            'container_confirmed,staging_confirmed FROM prepared_validation_cleanups WHERE run_id=? ORDER BY sequence', (run_id,)), 1):
        contract._id(observation[0])
        if (type(observation[1]) is not int or observation[1] != sequence or tuple(observation[2:4]) != tuple(dispatch[:2])
                or any(type(flag) is not int or flag not in (0, 1) for flag in observation[5:7])
                or observation[4] is not None and (resource is None or observation[4] != resource[0])):
            raise ValueError('Cleanup observation has a mixed or incomplete resource history')
        latest = observation
    settled = bool(latest and resource and latest[4] == resource[0] and tuple(latest[5:7]) == (1, 1))
    if result:
        if (result['status'] not in {'passed', 'failed', 'interrupted', 'cleanup_failed', 'unknown'}
                or type(result['cleanupConfirmed']) is not bool or type(result['stagingCleanupConfirmed']) is not bool):
            raise ValueError('Malformed result claim')
        contract._result_reference({**result, 'status': 'passed', 'cleanupConfirmed': True, 'stagingCleanupConfirmed': True}, envelope)
    reasons = ['reserved_undispatched'] if dispatch is None else ['runtime_receipt_unverified']
    if dispatch:
        if result is None or result['status'] in {'unknown', 'interrupted'}:
            reasons.append('execution_outcome_unknown')
        elif result['status'] != 'passed':
            reasons.append('validation_failed')
        if not settled:
            reasons.append('resource_cleanup_unconfirmed')
        if result and (not result['cleanupConfirmed'] or not result['stagingCleanupConfirmed']):
            reasons.append('result_cleanup_unconfirmed')
    return {'runId': run_id, 'stepId': step['id'], 'recipe': step['recipe'], 'reservedUnits': 1,
            'dispatched': dispatch is not None, 'resourceClaimed': resource is not None,
            'resultClaimStatus': result['status'] if result else None, 'cleanupClaimSettled': settled,
            'latestCleanupSequence': latest[1] if latest else None, 'blockerReasons': reasons}


def _set_activity(conn, envelope, grant, binding, history):
    context = _context(envelope, grant, binding)
    contract.normalize_validation_set_envelope(envelope, **context)
    ordered = contract.validation_execution_order(envelope)
    _snapshot_metadata(conn, envelope, grant)
    results = _ledger()._results(conn, envelope)
    if len({result['receiptSha256'] for result in results}) != len(results):
        raise ValueError('Set repeats a receipt reference')
    steps = [_step(conn, envelope, step, next((item for item in results if item['runId'] == step['runId']), None)) for step in ordered]
    aggregate = _record(conn.execute(f'SELECT {_bounded("record", 32768)},sha256 FROM prepared_validation_aggregates WHERE set_id=?',
                                    (envelope['setId'],)).fetchone())
    if aggregate is not None and aggregate != _ledger()._aggregate(envelope, results):
        raise ValueError('Aggregate claim changed')
    review = conn.execute(f'SELECT {_bounded("record", 2 * 1024**2)},sha256,{_bounded("contributors", 2 * 1024**2)},'
        f'{_bounded("source_evidence", 2 * 1024**2)},request_id,reviewer_id FROM prepared_validation_reviews WHERE set_id=?',
        (envelope['setId'],)).fetchone()
    if review:
        value = _ledger()._checked_json(review[0], review[1])
        contributors = _json(b'{"items":' + review[2].encode() + b'}', 2 * 1024**2 + 10, 'Contributors')['items']
        evidence = _json(review[3].encode(), 2 * 1024**2, 'Source evidence')
        if (value['requestId'], value['reviewerId']) != tuple(review[4:6]):
            raise ValueError('Review identity changed')
        request = conn.execute('SELECT objective_id,type FROM requests WHERE id=?', (review[4],)).fetchone()
        if request is None or tuple(request) != (envelope['objectiveId'], 'request.test_review'):
            raise ValueError('Review request changed')
        # Placeholder consistency only, NOT live/current root verification. The
        # pure contract inspector deliberately returns no authorization either.
        contract.inspect_latest_validation_contract(history, results=results, review=value, contributor_ids=contributors,
            source_evidence=evidence, receipt_hashes={item['runId']: item['receiptSha256'] for item in results},
            context=context, root_authorized=True)
    return {'setId': envelope['setId'], 'requestId': envelope['requestId'], 'projectId': envelope['projectId'],
            'round': envelope['round'], 'generation': envelope['generation'], 'reservedUnits': contract.validation_step_count(envelope),
            'snapshotSha256': envelope['source']['snapshotSha256'], 'steps': steps,
            'aggregateClaimPresent': aggregate is not None, 'reviewClaimPresent': review is not None,
            'state': 'reserved_undispatched' if not any(step['dispatched'] for step in steps) else 'runtime_unverified'}


def _reservation_identity(conn, envelope, binding):
    if (envelope['ownerGrantSha256'] != binding['ownerGrantSha256']
            or envelope['objectiveBindingSha256'] != digest(binding)):
        raise ValueError('Reservation objective binding changed')
    row = conn.execute('SELECT typecheck_run_id,tests_run_id,build_run_id,prior_units,objective_ceiling,owner_ceiling '
                       'FROM prepared_validation_sets WHERE id=?', (envelope['setId'],)).fetchone()
    ordered = contract.validation_execution_order(envelope)
    expected_ids = tuple(step['runId'] for step in ordered)
    stored_ids = tuple(row[:3]) if envelope['version'] == 2 else tuple(row[:2])
    if (stored_ids != expected_ids or envelope['version'] == 1 and row[2] is not None
            or any(type(v) is not int for v in row[3:])):
        raise ValueError('Set reservation metadata changed')
    if row[3] < 0 or row[3] + len(ordered) > min(row[4:]):
        raise ValueError('Set accounting metadata changed')
    request = conn.execute('SELECT objective_id FROM requests WHERE id=?', (envelope['requestId'],)).fetchone()
    if request is None or request[0] != envelope['objectiveId']:
        raise ValueError('Set request identity changed')


def _reservation_history(conn, objective_id, storage):
    """Only cumulative reservations, never source manifests or receipt claims.

Malformed reservation identities fail closed. Corrupt later source/result data
cannot refund already committed units; deep activity readers report that error.
"""
    contract._id(objective_id)
    if conn.execute('SELECT 1 FROM objectives WHERE id=?', (objective_id,)).fetchone() is None:
        raise ValueError('Objective does not exist')
    legacy = conn.execute('SELECT count(*) FROM project_run_starts WHERE objective_id=?', (objective_id,)).fetchone()[0]
    histories, count = {}, 0
    if storage == 'present':
        count = conn.execute('SELECT count(*) FROM prepared_validation_sets WHERE objective_id=?', (objective_id,)).fetchone()[0]
        project_count = conn.execute('SELECT count(*) FROM prepared_validation_bindings WHERE objective_id=?', (objective_id,)).fetchone()[0]
        if count > _MAX_SETS or project_count > 8:
            raise ValueError('Objective prepared reservations exceed their bound')
        rows = conn.execute(f'SELECT {_bounded("project_id", 128)},grant_sha256,'
            f'{_bounded("binding_record", 4096)},binding_sha256 FROM prepared_validation_bindings WHERE objective_id=? ORDER BY project_id',
            (objective_id,)).fetchall()
        for row in rows:
            project_id = contract._id(row[0])
            binding = _ledger()._checked_json(row[2], row[3])
            contract._hash(row[1])
            legacy_binding = conn.execute(f'SELECT {_bounded("binding", 8192)} FROM objective_projects WHERE objective_id=? AND project_id=?',
                                          (objective_id, project_id)).fetchone()
            if legacy_binding is None or type(legacy_binding[0]) is not str:
                raise ValueError('Legacy objective binding missing')
            binding_sha256 = contract.legacy_project_binding_sha256(_json(legacy_binding[0].encode(), 8192, 'Legacy binding'))
            expected = {'version': 1, 'contract': 'prepared-validation-objective-binding-v1',
                        'objectiveId': objective_id, 'projectId': project_id,
                        'projectBindingSha256': binding_sha256, 'ownerGrantSha256': row[1]}
            if binding != expected:
                raise ValueError('Reservation binding changed')
            history = _ledger()._history(conn, objective_id, project_id)
            for envelope in history:
                _reservation_identity(conn, envelope, binding)
            histories[project_id] = history
        if sum(map(len, histories.values())) != count:
            raise ValueError('Prepared sets are missing a binding')
    units = sum(contract.validation_step_count(envelope) for history in histories.values() for envelope in history)
    return {'objectiveId': objective_id, 'storageState': storage, 'legacyUnits': legacy,
            'preparedUnits': units, 'totalUnits': legacy + units}, histories


def _objective(conn, objective_id, storage):
    units, histories = _reservation_history(conn, objective_id, storage)
    activity = []
    for project_id, history in histories.items():
        grant, binding = _ledger()._binding(conn, objective_id, project_id)
        for index, envelope in enumerate(history):
            activity.append(_set_activity(conn, envelope, grant, binding, history[:index + 1]))
    return units, activity


def objective_project_reserved_units(conn, objective_id):
    """Cumulative objective execution units; failures and crashes never refund."""
    with _read(conn):
        units, _ = _reservation_history(conn, objective_id, _storage(conn))
        return _flags(**units)


def goal_project_reserved_units(conn, goal_id):
    """Sum immutable canonical goal membership, including historical members.

No import of goal_allowances, budget ceiling/reset logic, or second wallet is
involved. Return None only when all goal tables are absent, never for a missing
configuration in otherwise present goal storage.
"""
    with _read(conn):
        contract._id(goal_id)
        storage = _storage(conn)
        objects = dict(conn.execute("SELECT name,type FROM main.sqlite_master"))
        if not objects.keys() & _GOAL_TABLES:
            return None
        if any(objects.get(name) != 'table' for name in _GOAL_TABLES):
            raise ValueError('Goal storage is incomplete')
        row = conn.execute(f'SELECT {_bounded("record", 65536)},sha256,revision FROM organization_goal_revisions WHERE id=? ORDER BY revision DESC LIMIT 1', (goal_id,)).fetchone()
        if row is None or type(row[0]) is not str:
            raise ValueError('Goal configuration missing')
        goal = _json(row[0].encode(), 65536, 'Goal configuration')
        sha256 = hashlib.sha256(json.dumps(goal, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        if (sha256 != row[1] or goal['id'] != goal_id or type(goal['revision']) is not int or goal['revision'] != row[2]
                or type(goal['paused']) is not bool or type(goal['limits']['projectRuns']) is not int or goal['limits']['projectRuns'] < 0):
            raise ValueError('Goal configuration changed')
        count = conn.execute('SELECT count(*) FROM organization_goal_members WHERE goal_id=?', (goal_id,)).fetchone()[0]
        if not 0 <= count <= 1024:
            raise ValueError('Goal membership exceeds its bound')
        members = [row[0] for row in conn.execute('SELECT objective_id FROM organization_goal_members WHERE goal_id=? ORDER BY objective_id', (goal_id,))]
        totals = [_reservation_history(conn, member, storage)[0] for member in members]
        return _flags(goalId=goal_id, objectiveIds=members, storageState=storage,
                      **{key: sum(item[key] for item in totals) for key in ('legacyUnits', 'preparedUnits', 'totalUnits')})


def _limit(value):
    if type(value) is not int or not 1 <= value <= _MAX_OUTPUT:
        raise ValueError('Projection output limit must be between one and 1000')
    return value


def _blockers(activity, limit):
    entries = [{'setId': item['setId'], 'projectId': item['projectId'], 'generation': item['generation'],
                'runId': step['runId'], 'reasons': step['blockerReasons']}
               for item in activity for step in item['steps'] if step['blockerReasons']]
    return {'blocked': bool(entries), 'totalBlockers': len(entries), 'entries': entries[:limit], 'truncated': len(entries) > limit}


def prepared_validation_blockers(conn, objective_id, *, limit=100):
    """Bounded blockers with complete counts; truncated entries never mean idle."""
    with _read(conn):
        limit = _limit(limit)
        units, activity = _objective(conn, objective_id, _storage(conn))
        return _flags(objectiveId=objective_id, storageState=units['storageState'], **_blockers(activity, limit))


def prepared_validation_activity(conn, objective_id, *, history_limit=100):
    """Bounded history plus each project's latest generation, without fallback.

All historical rows are validated before truncating display output. Unknown or
failed newer generations remain current even if an older review claimed a pass.
"""
    with _read(conn):
        limit = _limit(history_limit)
        units, activity = _objective(conn, objective_id, _storage(conn))
        current = {}
        for item in activity:
            current[item['projectId']] = item
        history = sorted(activity, key=lambda item: (item['round'], item['generation'], item['projectId']), reverse=True)
        return _flags(**units, current=current, history=history[:limit], totalSets=len(activity),
                      historyTruncated=len(activity) > limit, blockers=_blockers(activity, limit))
