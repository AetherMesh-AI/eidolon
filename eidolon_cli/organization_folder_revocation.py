"""Owner-confirmed, deny-first folder revocation. Reads never probe source paths."""
from contextlib import ExitStack
from dataclasses import asdict, replace
import json
from pathlib import Path
import time

from eidolon_cli.organization_metadata import _uuid, _text
from eidolon_cli.organization_owner_policy import OwnerPolicyError, _connection, _hash, _identity, _schema, _writer, _refresh_existing
from eidolon_cli.organization_policy import persisted_settings, _fingerprint
from eidolon_cli.organization_root_grants import ROOT_GRANT_SCHEMA, root_rows, revoked_aliases


def _grant(row):
    return {'alias': row['alias'], 'path': row['path'], 'sourcePrefix': json.loads(row['source_prefix']),
            'device': row['device'], 'inode': row['inode']}


def _activity(conn, home):
    # Lock ownership, not a cancelled claim's ledger status, establishes quiescence.
    from eidolon_cli.organization_service import _ExecutionLock
    busy = False
    with ExitStack() as locks:
        for index in range(4):
            if not locks.enter_context(_ExecutionLock(Path(home) / 'organization' / 'capacity-locks', str(index))):
                busy = True
    unknown = [dict(row) for row in conn.execute("SELECT id,request_id,status FROM tool_receipts WHERE status IN ('running','unknown') ORDER BY created,id")]
    runs = []
    for row in conn.execute('SELECT s.id,s.request_id,r.record FROM project_run_starts s LEFT JOIN project_run_results r ON r.run_id=s.id ORDER BY s.id'):
        record = json.loads(row['record']) if row['record'] is not None else None
        if (record is None or record.get('status') == 'cleanup_failed' or record.get('cleanupConfirmed') is False or
                record.get('reason') == 'Execution cleanup did not close its process streams within the bounded deadline.' or
                record.get('containerName') and record.get('cleanupConfirmed') is not True):
            runs.append({'id': row['id'], 'request_id': row['request_id']})
    sources = []
    from eidolon_cli.organization_project_execution import digest
    for request in conn.execute("SELECT DISTINCT q.id,q.payload FROM requests q JOIN organization_execution_journal j ON j.request_id=q.id WHERE q.type='request.source_integrate' AND q.status!='completed' AND j.dispatched=1 ORDER BY q.id"):
        receipt = conn.execute('SELECT * FROM project_source_receipts WHERE request_id=?', (request['id'],)).fetchone()
        try:
            settled = receipt is not None and receipt['run_id'] == json.loads(request['payload']).get('runId') and digest(json.loads(receipt['record'])) == receipt['sha256']
        except (ValueError, TypeError, KeyError):
            settled = False
        if not settled:
            sources.append({'id': request['id'], 'request_id': request['id']})
    return {'executionStillStopping': busy, 'uncertainToolReceipts': unknown, 'unsettledProjectRuns': runs, 'uncertainSourceRequests': sources}


def folder_access(home, expected_id):
    _uuid(expected_id, 'expectedOrganizationId')
    with _connection(home) as conn:
        identity = _identity(conn, expected_id)
        policy = _schema(conn)
        revoked = set(revoked_aliases(conn))
        grants = [{**_grant(row), 'grantHash': _hash(_grant(row)), 'revoked': row['alias'] in revoked} for row in root_rows(conn)]
        activity = _activity(conn, home)
        review_activity = {key: value for key, value in activity.items() if key != 'executionStillStopping'}
        reviews = {row[0] for row in conn.execute('SELECT alias FROM organization_root_revocation_reviews')} if conn.execute("SELECT 1 FROM sqlite_master WHERE name='organization_root_revocation_reviews'").fetchone() else set()
        for grant in grants: grant['reviewed'] = grant['alias'] in reviews
        from eidolon_cli.organization_root_grants import pending_revocations
        return {'capabilityVersion': 1, 'organizationId': identity['organizationId'], 'ownerId': identity['owner']['ownerId'],
                'policyGeneration': policy['generation'], 'grants': grants,
                'paused': pending_revocations(conn) or bool(conn.execute('SELECT 1 FROM organization_policy_pause').fetchone()),
                'reviewVersion': _hash(review_activity), **activity}


def revoke_folder(home, params):
    if params.get('confirmed') is not True:
        raise ValueError('Explicitly confirm revocation of this exact folder grant')
    _uuid(params.get('expectedOrganizationId'), 'expectedOrganizationId')
    key = _text(params.get('idempotencyKey'), 'idempotencyKey', 128)
    digest = _hash(params)
    with _connection(home, write=True) as conn:
        identity = _identity(conn, params['expectedOrganizationId'])
        policy = _schema(conn)
        for statement in ROOT_GRANT_SCHEMA.split(';\n'):
            if statement.strip(): conn.execute(statement)
        previous = conn.execute('SELECT * FROM organization_root_revocations WHERE idempotency_key=?', (key,)).fetchone()
        if previous:
            if previous['organization_id'] != identity['organizationId'] or previous['owner_id'] != identity['owner']['ownerId']:
                raise OwnerPolicyError('identity_conflict')
            if previous['input_hash'] != digest: raise OwnerPolicyError('idempotency_conflict')
            return json.loads(previous['receipt'])
        if type(params.get('expectedGeneration')) is not int or params['expectedGeneration'] != policy['generation']:
            raise OwnerPolicyError('generation_conflict')
        row = conn.execute('SELECT * FROM organization_root_grants WHERE alias=?', (params.get('alias'),)).fetchone()
        if row is None or _hash(_grant(row)) != params.get('grantHash'):
            raise OwnerPolicyError('grant_conflict')
        if row['alias'] in revoked_aliases(conn): raise OwnerPolicyError('already_revoked')
        settings = persisted_settings(conn)
        if settings is None: raise OwnerPolicyError('execution_not_initialized')
        store = _writer(conn, home, settings, policy)
        # Commit this deny before attempting any capacity lock or service shutdown.
        # An already-held SQLite publisher transaction must finish first; a lock
        # timeout returns failure and never reports the grant as revoked.
        conn.execute('INSERT OR IGNORE INTO organization_policy_pause VALUES (1)')
        updated = replace(settings, revoked_root_aliases=tuple(sorted({*settings.revoked_root_aliases, row['alias']})))
        values = asdict(updated)
        generation = policy['generation'] + 1
        conn.execute('UPDATE organization_policy SET generation=?,fingerprint=?,settings=? WHERE id=1',
                     (generation, _fingerprint(values), json.dumps(values, sort_keys=True)))
        for request in conn.execute("SELECT * FROM requests WHERE status='running'").fetchall():
            store._pending(conn, request, 'Owner revoked folder access. Execution was fenced; review its retained outcome before retrying.')
        receipt = {'capabilityVersion': 1, 'receipt': {'idempotencyKey': key, 'organizationId': identity['organizationId'],
            'ownerId': identity['owner']['ownerId'], 'alias': row['alias'], 'grantHash': params['grantHash'],
            'policyGeneration': generation, 'accessRevoked': True, 'dispatchPaused': True}}
        conn.execute('INSERT INTO organization_root_revocations VALUES (?,?,?,?,?,?,?,?)',
            (row['alias'], identity['organizationId'], identity['owner']['ownerId'], params['grantHash'], key, digest, json.dumps(receipt, sort_keys=True), time.time()))
    try: _refresh_existing(home)
    except Exception: pass  # Durable fence is authoritative; no replay on refresh failure.
    return receipt


def review_revocation(home, params):
    """Explicitly resume unaffected work only after actual executors have exited.

    Unknown read receipts remain unknown; acknowledgment never invents success.
    Missing project results require positive backend shutdown reconciliation.
    """
    if params.get('confirmed') is not True:
        raise ValueError('Explicitly confirm review before resuming unaffected work')
    _uuid(params.get('expectedOrganizationId'), 'expectedOrganizationId')
    with _connection(home, write=True) as conn, ExitStack() as locks:
        identity = _identity(conn, params['expectedOrganizationId'])
        policy = _schema(conn)
        row = conn.execute('SELECT * FROM organization_root_revocations WHERE alias=?', (params.get('alias'),)).fetchone()
        if row is None or row['grant_hash'] != params.get('grantHash'):
            raise OwnerPolicyError('grant_conflict')
        previous = conn.execute('SELECT * FROM organization_root_revocation_reviews WHERE alias=?', (row['alias'],)).fetchone()
        if previous:
            if previous['owner_id'] != identity['owner']['ownerId'] or previous['review_hash'] != _hash(params):
                raise OwnerPolicyError('review_conflict')
            return {'capabilityVersion': 1, 'alias': row['alias'], 'accessRevoked': True, 'reviewed': True}
        if type(params.get('expectedGeneration')) is not int or params['expectedGeneration'] != policy['generation']:
            raise OwnerPolicyError('generation_conflict')
        from eidolon_cli.organization_service import _ExecutionLock
        for index in range(4):
            if not locks.enter_context(_ExecutionLock(Path(home) / 'organization' / 'capacity-locks', str(index))):
                raise OwnerPolicyError('execution_still_stopping')
        activity = _activity(conn, home)
        # Locks are already held here. Only retained outcome identity is reviewed.
        activity.pop('executionStillStopping')
        if params.get('reviewVersion') != _hash(activity):
            raise OwnerPolicyError('review_conflict')
        if activity['unsettledProjectRuns']:
            raise OwnerPolicyError('project_reconciliation_required')
        if (activity['uncertainToolReceipts'] or activity['uncertainSourceRequests']) and params.get('acknowledgeUncertain') is not True:
            raise OwnerPolicyError('uncertain_outcome_review_required')
        from eidolon_cli.organization_owner_policy import _source
        settings, _, _ = _source(home, conn)
        store = _writer(conn, home, persisted_settings(conn), policy)
        for source in activity['uncertainSourceRequests']:
            request = conn.execute('SELECT * FROM requests WHERE id=?', (source['request_id'],)).fetchone()
            if request is not None and request['status'] == 'queued':
                store._pending(conn, request, 'Owner acknowledged an unknown source-publication outcome. Reconcile its retained evidence before any explicit retry.')
        conn.execute('INSERT INTO organization_root_revocation_reviews VALUES (?,?,?,?,?)',
                     (row['alias'], identity['owner']['ownerId'], _hash(params),
                      json.dumps({'reviewVersion':params['reviewVersion'], 'acknowledgeUncertain':params.get('acknowledgeUncertain') is True,
                                  'activity':activity, 'policyGeneration':policy['generation']}, sort_keys=True), time.time()))
        store._reload_configuration(conn, settings)
        result = {'capabilityVersion': 1, 'alias': row['alias'], 'accessRevoked': True, 'reviewed': True}
    try: _refresh_existing(home)
    except Exception: pass
    return result
