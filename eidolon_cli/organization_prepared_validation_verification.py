"""Trusted retained pair verification, with live authority supplied by the caller.

Only host-orchestrated runtime events are evidence here. Neither inert ledger
claims nor model-supplied receipts can replace that complete stream. This module
does not execute workloads, read live source, grant publication, or register
schema. Its callback must revalidate current owner/root/dependency/policy and
exact effective-source, original-preimage and managed-revision identities.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import math
import re
import sqlite3
import time

from eidolon_cli import organization_prepared_validation_contract as contract
from eidolon_cli import organization_prepared_validation_ledger as ledger
from eidolon_cli.organization_prepared_validation_events import retained_runtime_events
from eidolon_cli.organization_project_dependency_bundle import _json
from eidolon_cli.organization_project_validation import canonical, digest
from eidolon_cli.organization_project_workspace_receipt import inspect_prepared_runtime_history
from eidolon_cli.organization_project_workspace_resources import container_name, normalize_runtime_identity

MAX_SOURCE_PROOF_BYTES = 16 * 1024**2
MAX_SET_HISTORY_BYTES = 16 * 1024**2
MAX_REVIEW_BYTES = 2 * 1024**2
MAX_REVIEW_EVIDENCE = 1000
_FAILURE_STATUS = {'failed': 'failed', 'interrupted': 'interrupted', 'cancelled': 'interrupted',
                   'timed_out': 'interrupted', 'blocked': 'blocked', 'unsupported': 'blocked',
                   'cleanup_failed': 'blocked', 'staging_cleanup_failed': 'blocked'}
PREPARED_PAIR_REVIEW_SCHEMA = '''
CREATE TABLE IF NOT EXISTS prepared_validation_pair_reviews (
 set_id TEXT NOT NULL PRIMARY KEY REFERENCES prepared_validation_sets(id),
 request_id TEXT NOT NULL UNIQUE REFERENCES requests(id),
 reviewer_id TEXT NOT NULL REFERENCES agents(id),
 approved INTEGER NOT NULL CHECK(typeof(approved)='integer' AND approved IN (0,1)),
 record TEXT NOT NULL, sha256 TEXT NOT NULL, created REAL NOT NULL);
CREATE TRIGGER IF NOT EXISTS prepared_pair_review_no_replace BEFORE INSERT ON prepared_validation_pair_reviews
 WHEN EXISTS(SELECT 1 FROM prepared_validation_pair_reviews WHERE set_id=NEW.set_id OR request_id=NEW.request_id)
 BEGIN SELECT RAISE(ABORT,'Prepared pair reviews are immutable'); END;
CREATE TRIGGER IF NOT EXISTS prepared_pair_review_no_update BEFORE UPDATE ON prepared_validation_pair_reviews
 BEGIN SELECT RAISE(ABORT,'Prepared pair reviews are immutable'); END;
CREATE TRIGGER IF NOT EXISTS prepared_pair_review_no_delete BEFORE DELETE ON prepared_validation_pair_reviews
 BEGIN SELECT RAISE(ABORT,'Prepared pair reviews are immutable'); END;
'''


@contextmanager
def _read_view(conn):
    if conn.row_factory is not sqlite3.Row or conn.execute('PRAGMA foreign_keys').fetchone()[0] != 1:
        raise ValueError('Prepared verification requires an organization SQLite connection')
    own = not conn.in_transaction
    if own:
        conn.execute('BEGIN')
    try:
        yield
    finally:
        # Never commit, roll back, or end a caller-owned transaction.
        if own:
            conn.rollback()


def _scalar_bounds(conn, table, where, args, limits):
    # These are fixed internal SQL identifiers, never model-provided names.
    # Existing ledger helpers bound their JSON/binary bodies; fence their small
    # companion fields too before calling them on potentially damaged storage.
    exceeds = ' OR '.join(f'({column} IS NOT NULL AND (typeof({column})<>\'text\' '
                         f'OR length(CAST({column} AS BLOB)) NOT BETWEEN 1 AND {limit}))'
                         for column, limit in limits.items())
    if conn.execute(f'SELECT 1 FROM {table} WHERE ({where}) AND ({exceeds}) LIMIT 1', args).fetchone():
        raise ValueError('Prepared retained scalar metadata exceeds its byte bound')


def _snapshot_bounds(conn, set_id, objective_id, project_id):
    _scalar_bounds(conn, 'prepared_validation_sets', 'id=?', (set_id,),
                   {'objective_id': 128, 'project_id': 128, 'envelope_sha256': 64})
    _scalar_bounds(conn, 'prepared_validation_snapshots', 'set_id=?', (set_id,),
                   {'snapshot_sha256': 64, 'limits_sha256': 64})
    _scalar_bounds(conn, 'prepared_validation_files', 'set_id=?', (set_id,), {'path': 1024, 'sha256': 64})
    _scalar_bounds(conn, 'prepared_validation_bindings', 'objective_id=? AND project_id=?',
                   (objective_id, project_id), {'grant_sha256': 64, 'binding_sha256': 64})
    _scalar_bounds(conn, 'objective_projects', 'objective_id=? AND project_id=?',
                   (objective_id, project_id), {'binding': 65536})


def _source_proof(conn, envelope, snapshot):
    row = conn.execute(f'SELECT {ledger._bounded_text("record", MAX_SOURCE_PROOF_BYTES)},'
        f'{ledger._bounded_text("sha256", 64)} '
        'FROM prepared_validation_source_proofs WHERE set_id=?', (envelope['setId'],)).fetchone()
    if row is None:
        raise ValueError('Prepared source proof is missing')
    proof = ledger._checked_json(*row)
    contract._exact(proof, {*envelope['source'], 'original', 'effective', 'managedRevisions'}, 'Source proof')
    if (any(proof[key] != value for key, value in envelope['source'].items())
            or canonical(proof['effective']) != canonical(snapshot.manifest())):
        raise ValueError('Prepared source proof differs from its retained effective snapshot')
    original, effective, managed = proof['original'], proof['effective'], proof['managedRevisions']
    contract._exact(original, effective, 'Original source manifest')
    if any(canonical(original[key]) != canonical(effective[key]) for key in ('version', 'rootAlias', 'rootIdentity')):
        raise ValueError('Original source proof differs from the retained root')
    if type(original['files']) is not list or len(original['files']) != len(effective['files']):
        raise ValueError('Original source proof lacks exact selected coverage')
    total = 0
    for before, after in zip(original['files'], effective['files']):
        contract._exact(before, after, 'Original source file')
        if before['path'] != after['path']:
            raise ValueError('Original source proof changed selected coverage')
        contract._hash(before['sha256'])
        contract._integer(before['bytes'], 'Original source bytes', high=snapshot.limits.max_file_bytes)
        if type(before['mode']) is not int or before['mode'] not in (0o444, 0o555) or type(before['executionOnly']) is not bool:
            raise ValueError('Original source proof has invalid file metadata')
        total += before['bytes']
    if type(original['totalBytes']) is not int or original['totalBytes'] != total or total > snapshot.limits.max_total_bytes:
        raise ValueError('Original source proof has invalid total bytes')
    contract._exact(managed, {'version', 'contract', 'objectiveId', 'rootAlias', 'revisions'}, 'Managed source proof')
    contract._version(managed, 'effective-source-managed-revisions-v1')
    if (managed['objectiveId'] != envelope['objectiveId'] or managed['rootAlias'] != snapshot.root_alias
            or type(managed['revisions']) is not list or len(managed['revisions']) > 10000):
        raise ValueError('Managed source proof differs from its objective or root')
    if (digest({'version': 1, 'contract': 'effective-source-preimages-v1', 'source': original}) != proof['preimageSha256']
            or digest(managed) != proof['managedRevisionsSha256']):
        raise ValueError('Prepared preimage or managed-revision proof hash changed')
    return proof


def _authority(value, envelope):
    contract._exact(value, {'context', 'contributorIds', 'sourceEvidence'}, 'Current prepared authority')
    context = value['context']
    contract.normalize_validation_set_envelope(envelope, **context)
    contributors = value['contributorIds']
    if (type(contributors) is not list or not 1 <= len(contributors) <= 10000
            or contributors != sorted({contract._id(item) for item in contributors})):
        raise ValueError('Prepared review requires the exact nonempty independent contributor set')
    evidence = value['sourceEvidence']
    if type(evidence) is not dict or not 1 <= len(evidence) <= MAX_REVIEW_EVIDENCE:
        raise ValueError('Prepared review requires bounded nonempty source evidence')
    reserved = {envelope['setId'], *(step['runId'] for step in envelope['steps'])}
    for key, sha256 in evidence.items():
        contract._id(key)
        contract._hash(sha256)
        if key in reserved:
            raise ValueError('Source evidence cannot replace prepared receipt identities')
    if len(canonical(value).encode('utf-8')) > MAX_REVIEW_BYTES:
        raise ValueError('Current prepared review metadata exceeds its byte bound')
    return context, contributors, dict(sorted(evidence.items()))


def _run(conn, envelope, step, snapshot, grant, *, require_success=True):
    run_id = step['runId']
    _scalar_bounds(conn, 'prepared_validation_runtime_events', 'run_id=?', (run_id,), {'kind': 32, 'sha256': 64})
    events = retained_runtime_events(conn, run_id)
    if not events:
        raise ValueError('Latest prepared generation lacks complete host runtime events')
    identity = normalize_runtime_identity(events[0]['resource'])
    if (identity['setId'] != envelope['setId'] or identity['runId'] != run_id
            or identity['authoritySha256'] != envelope['identity']['authoritySha256']):
        raise ValueError('Prepared runtime history belongs to another reserved authority')
    dispatch = conn.execute(f'SELECT {ledger._bounded_text("nonce", 64)},'
                            f'{ledger._bounded_text("expected_container_name", 128)},'
                            f'{ledger._bounded_text("image", 256)} FROM prepared_validation_dispatches WHERE run_id=?',
                            (run_id,)).fetchone()
    if dispatch is None or tuple(dispatch) != (identity['nonce'], container_name(identity), contract.IMAGE):
        raise ValueError('Prepared dispatch mirror differs from its host event stream')
    scope = {'snapshotSha256': snapshot.snapshot_sha256,
             **{key: envelope['identity'][key] for key in ('profileSha256', 'bundleSha256')},
             'stepId': step['id'], 'stepDefinitionSha256': step['definitionSha256'],
             'recipe': step['recipe'], 'checkKind': step['checkKind'],
             'dependencyRootIdentity': {key: grant['dependencies'][key] for key in ('device', 'inode')}}
    if any(canonical(events[0]['data'].get(key)) != canonical(value) for key, value in scope.items()):
        raise ValueError('Prepared dispatch scope differs from its exact reserved source and step')
    terminal = [event['data'] for event in events if event['kind'] == 'receipt']
    if len(terminal) != 1:
        raise ValueError('Latest prepared generation has no exact terminal host receipt')
    receipt, receipt_hash = terminal[0]['receipt'], terminal[0]['receiptSha256']
    successful = receipt.get('status') == 'passed'
    receipt_context = {
        'source': snapshot, 'profile': grant['profile'], 'step_id': step['id'],
        'expected_dependency_identity': tuple(grant['dependencies'][key] for key in ('device', 'inode')),
        'trusted_receipt_sha256': receipt_hash}
    verified = inspect_prepared_runtime_history(events, resource_identity=identity,
                                                receipt_context=receipt_context if successful else None)
    if require_success and verified['state'] != 'finish_only':
        raise ValueError('Latest prepared generation failed or has incomplete host runtime evidence')
    if not successful:
        if (verified['state'] != 'terminal_failed' or type(receipt.get('status')) is not str
                or receipt['status'] not in _FAILURE_STATUS
                or receipt.get('runner') != 'eidolon.prepared-workspace'
                or type(receipt.get('runnerVersion')) is not int or receipt['runnerVersion'] != 1
                or any(canonical(receipt[key]) != canonical(value) for key, value in scope.items() if key in receipt)):
            raise ValueError('Prepared failure evidence requires an exact known terminal host failure')
    resource = conn.execute(f'SELECT {ledger._bounded_text("container_id", 64)} '
                            'FROM prepared_validation_resources WHERE run_id=?', (run_id,)).fetchone()
    if ((verified['containerId'] is None and resource is not None)
            or (verified['containerId'] is not None and (resource is None or resource[0] != verified['containerId']))):
        raise ValueError('Prepared resource mirror differs from its host event stream')
    cleanups = [event['data'] for event in events if event['kind'] == 'cleanup_observed']
    count = conn.execute('SELECT count(*) FROM prepared_validation_cleanups WHERE run_id=?', (run_id,)).fetchone()[0]
    if count != len(cleanups):
        raise ValueError('Prepared cleanup mirrors omit or add host observations')
    _scalar_bounds(conn, 'prepared_validation_cleanups', 'run_id=?', (run_id,), {'container_id': 64})
    rows = conn.execute(f'SELECT sequence,{ledger._bounded_text("nonce", 64)},'
        f'{ledger._bounded_text("expected_container_name", 128)},{ledger._bounded_text("container_id", 64)},'
        'container_confirmed,staging_confirmed '
        'FROM prepared_validation_cleanups WHERE run_id=? ORDER BY sequence', (run_id,)).fetchall()
    for sequence, (row, cleanup) in enumerate(zip(rows, cleanups), 1):
        expected = (sequence, identity['nonce'], container_name(identity), cleanup['containerId'],
                    int(cleanup['containerCleanupConfirmed']), int(cleanup['stagingCleanupConfirmed']))
        if tuple(row) != expected:
            raise ValueError('Prepared cleanup mirror differs from its exact host observation')
    expected = {'version': 1, 'contract': 'prepared-validation-result-reference-v1',
        'setId': envelope['setId'], 'generation': envelope['generation'], 'runId': run_id,
        'stepId': step['id'], **{key: step[key] for key in ('definitionSha256', 'recipe', 'checkKind')},
        'snapshotSha256': snapshot.snapshot_sha256,
        **{key: envelope['identity'][key] for key in ('profileSha256', 'bundleSha256', 'authoritySha256')},
        'receiptSha256': receipt_hash,
        'status': receipt['status'] if receipt['status'] in {'passed', 'failed', 'interrupted', 'cleanup_failed'} else 'unknown',
        'cleanupConfirmed': receipt['cleanupConfirmed'], 'stagingCleanupConfirmed': receipt['stagingCleanupConfirmed']}
    result = conn.execute(f'SELECT {ledger._bounded_text("record", 8192)},{ledger._bounded_text("sha256", 64)},'
        f'{ledger._bounded_text("status", 32)},{ledger._bounded_text("receipt_sha256", 64)} '
        'FROM prepared_validation_results WHERE run_id=?', (run_id,)).fetchone()
    if (result is None or canonical(ledger._checked_json(result[0], result[1])) != canonical(expected)
            or tuple(result[2:]) != (expected['status'], receipt_hash)):
        raise ValueError('Prepared result mirror differs from its exact host receipt')
    return expected, receipt, digest(events)


def _artifact(envelope, step, receipt, reference, events_sha256):
    # No diagnostics, source bodies, complete manifests, staging paths, or raw
    # workload output reach model-facing evidence. Source review is separately
    # permission-filtered and supplied by the current-authority callback.
    outcome = receipt['outcome']
    record = {'version': 1, 'contract': 'prepared-validation-evidence-v1',
        'setId': envelope['setId'], 'runId': step['runId'], 'generation': envelope['generation'],
        'projectId': envelope['projectId'], 'round': envelope['round'], 'stepId': step['id'],
        'stepDefinitionSha256': step['definitionSha256'], 'recipe': step['recipe'], 'checkKind': step['checkKind'],
        **envelope['source'],
        **{key: envelope['identity'][key] for key in ('profileSha256', 'bundleSha256', 'authoritySha256')},
        'receiptSha256': digest(receipt), 'status': 'passed', 'exitCode': 0,
        'runtimeEventsSha256': events_sha256, 'resultReferenceSha256': digest(reference),
        'envelopeSha256': digest(envelope),
        'cleanupConfirmed': True, 'stagingCleanupConfirmed': True, 'sourceWritesPerformed': False,
        'workloadEvidenceOrigin': 'workload-claim',
        'reviewScope': 'Complete snapshot identity plus separately permission-filtered changed/test/config evidence; not every-byte model review',
        'coverage': {**{key: outcome[key] for key in ('testCount', 'skippedCount', 'failedCount', 'sourceCount')},
                     'selectedFilesCount': len(outcome['selectedFiles']), 'selectedFilesSha256': digest(outcome['selectedFiles']),
                     'reportedFilesCount': len(outcome['reportedFiles']), 'reportedFilesSha256': digest(outcome['reportedFiles'])},
        'sourceDeliveryAuthorized': False}
    body = canonical(record)
    finished = datetime.fromisoformat(receipt['endedAt']).timestamp()
    row = {'id': step['runId'], 'request_id': envelope['requestId'], 'objective_id': envelope['objectiveId'],
           'project_id': envelope['projectId'], 'round': envelope['round'], 'snapshot_sha256': envelope['source']['snapshotSha256'],
           'result_sha256': digest(record), 'record': body, 'finished': finished,
           'grant_record': canonical({'executionContract': 'prepared-validation-v1', 'execution': {'recipe': step['recipe']}})}
    artifact = {'id': step['runId'], 'objectiveId': envelope['objectiveId'], 'taskId': None,
        'kind': 'project_execution', 'content': body, 'sha256': digest(record),
        'summary': 'Prepared validation passed; host receipt verified, workload coverage independently reviewable.',
        'toolReceipts': [], 'createdAt': datetime.fromtimestamp(finished, timezone.utc).isoformat(), 'projectExecution': record}
    return row, artifact


def _failure_artifact(conn, envelope, step, receipt, reference, events_sha256):
    # Failure reasons are fixed host categories, never copied diagnostics or
    # workload strings. This is useful replan evidence, never a successful check.
    status = _FAILURE_STATUS[receipt['status']]
    record = {'version': 1, 'contract': 'prepared-validation-failure-evidence-v1',
        'setId': envelope['setId'], 'runId': step['runId'], 'generation': envelope['generation'],
        'projectId': envelope['projectId'], 'round': envelope['round'], 'stepId': step['id'],
        'recipe': step['recipe'], 'checkKind': step['checkKind'],
        'stepDefinitionSha256': step['definitionSha256'], **envelope['source'],
        **{key: envelope['identity'][key] for key in ('profileSha256', 'bundleSha256', 'authoritySha256')},
        'envelopeSha256': digest(envelope), 'runtimeEventsSha256': events_sha256,
        'receiptSha256': digest(receipt), 'resultReferenceSha256': digest(reference),
        'status': status, 'hostStatus': receipt['status'],
        'reason': {'failed': 'The retained host run did not pass the required check.',
                   'interrupted': 'The retained host run was interrupted before successful completion.',
                   'blocked': 'The retained host run was blocked or cleanup was not confirmed.'}[status],
        'acceptanceAuthorized': False, 'sourceDeliveryAuthorized': False, 'replayAllowed': False}
    created = conn.execute("SELECT CASE WHEN typeof(created) IN ('integer','real') THEN created END "
        'FROM prepared_validation_results WHERE run_id=?', (step['runId'],)).fetchone()[0]
    if type(created) not in (int, float) or not math.isfinite(created) or not 0 <= created <= 253402300799:
        raise ValueError('Prepared terminal failure has an invalid retained creation time')
    return {'id': step['runId'], 'objectiveId': envelope['objectiveId'], 'taskId': None,
        'kind': 'project_execution', 'content': canonical(record), 'sha256': digest(record),
        'summary': 'Prepared validation ' + status + '; not successful check or acceptance evidence.',
        'toolReceipts': [], 'createdAt': datetime.fromtimestamp(created, timezone.utc).isoformat(),
        'projectExecution': record}


def _payload(verified):
    envelope = verified['envelope']
    return {'executionContract': 'prepared-validation-v1', 'projectId': envelope['projectId'],
            'setId': envelope['setId'], 'round': envelope['round'],
            'evidenceIds': verified['evidenceIds'], 'evidenceHashes': verified['evidenceHashes']}


def prepared_validation_artifact(conn, run_id):
    """Resolve exact retained evidence without granting live/acceptance authority.

This static lookup is for permission-filtered context builders. Review and
acceptance must still call verify_latest_prepared_validation_set with current
authority. Older retained artifacts never establish the latest generation.
"""
    if type(run_id) is not str or not run_id.startswith('prepared_run_'):
        return None
    contract._id(run_id)
    with _read_view(conn):
        row = conn.execute(f'SELECT {ledger._bounded_text("set_id", 128)},'
                           f'{ledger._bounded_text("step_id", 64)},{ledger._bounded_text("definition_sha256", 64)},'
                           f'{ledger._bounded_text("recipe", 32)},{ledger._bounded_text("check_kind", 32)},units '
                           'FROM prepared_validation_steps WHERE run_id=?', (run_id,)).fetchone()
        if row is None:
            return None
        if not re.fullmatch(r'prepared_run_[0-9a-f]{32}', run_id):
            raise ValueError('Retained prepared run has a malformed production identity')
        if row[0] is None:
            raise ValueError('Prepared evidence has an oversized set identity')
        _scalar_bounds(conn, 'prepared_validation_sets', 'id=?', (row[0],), {'envelope_sha256': 64})
        envelope = ledger._run_envelope(conn, run_id)
        if envelope['setId'] != row[0]:
            raise ValueError('Prepared evidence differs from its exact set ownership')
        step = next((item for item in envelope['steps'] if item['runId'] == run_id), None)
        if step is None:
            raise ValueError('Prepared evidence has no exact reserved step')
        if tuple(row[1:]) != (step['id'], step['definitionSha256'], step['recipe'], step['checkKind'], 1):
            raise ValueError('Prepared evidence reservation differs from its exact envelope step')
        _snapshot_bounds(conn, envelope['setId'], envelope['objectiveId'], envelope['projectId'])
        snapshot = ledger._snapshot(conn, envelope['setId'])
        _source_proof(conn, envelope, snapshot)
        grant, _ = ledger._binding(conn, envelope['objectiveId'], envelope['projectId'])
        reference, receipt, events_sha256 = _run(conn, envelope, step, snapshot, grant, require_success=False)
        if receipt['status'] != 'passed':
            return _failure_artifact(conn, envelope, step, receipt, reference, events_sha256)
        return _artifact(envelope, step, receipt, reference, events_sha256)[1]


def _request(conn, request_id, verified):
    row = conn.execute(f'SELECT {ledger._bounded_text("id", 128)} AS id,'
        f'{ledger._bounded_text("objective_id", 128)} AS objective_id,{ledger._bounded_text("type", 64)} AS type,'
        f'{ledger._bounded_text("status", 32)} AS status,{ledger._bounded_text("agent_id", 128)} AS agent_id,'
        f'{ledger._bounded_text("token", 256)} AS token,'
        "CASE WHEN typeof(lease) IN ('integer','real') THEN lease END AS lease,"
        f'{ledger._bounded_text("payload", MAX_REVIEW_BYTES)} AS payload FROM requests WHERE id=?',
        (contract._id(request_id),)).fetchone()
    if (row is None or row['objective_id'] != verified['envelope']['objectiveId'] or row['type'] != 'request.test_review'
            or type(row['payload']) is not str or canonical(_json(row['payload'].encode(), MAX_REVIEW_BYTES, 'Review payload'))
            != canonical(_payload(verified))):
        raise ValueError('Prepared review request must bind the exact whole pair and source evidence')
    reviewer = contract._id(row['agent_id'])
    if reviewer in verified['contributorIds']:
        raise ValueError('Prepared pair review requires an independent reviewer')
    return row


def _review_identity(verified, request):
    return {'version': 1, 'contract': 'prepared-validation-pair-review-v1',
        'setId': verified['envelope']['setId'], 'requestId': request['id'], 'reviewerId': request['agent_id'],
        'envelopeSha256': digest(verified['envelope']), 'aggregateSha256': digest(verified['aggregate']),
        'contributorIds': verified['contributorIds'], 'sourceEvidence': verified['sourceEvidence'],
        'request': _payload(verified)}


def _verify_review(conn, verified):
    row = conn.execute(f'SELECT {ledger._bounded_text("request_id", 128)},{ledger._bounded_text("reviewer_id", 128)},'
        f'approved,{ledger._bounded_text("record", MAX_REVIEW_BYTES)},{ledger._bounded_text("sha256", 64)} '
        'FROM prepared_validation_pair_reviews WHERE set_id=?', (verified['envelope']['setId'],)).fetchone()
    if row is None:
        raise ValueError('Latest prepared generation lacks an independent whole-pair review')
    request = _request(conn, row[0], verified)
    record = ledger._checked_json(row[3], row[4])
    contract._exact(record, {*_review_identity(verified, request), 'approved', 'summary'}, 'Retained pair review')
    if (request['status'] != 'completed' or row[1] != request['agent_id'] or row[2] != 1
            or record['approved'] is not True or not _summary(record['summary'])
            or canonical({key: record[key] for key in _review_identity(verified, request)})
            != canonical(_review_identity(verified, request))):
        raise ValueError('Latest prepared whole-pair review is incomplete, rejected or changed')
    return record


def _summary(value):
    if type(value) is not str or not value.strip() or len(value.encode('utf-8')) > 10000:
        raise ValueError('Prepared review requires a bounded nonempty summary')
    return value


def verify_latest_prepared_validation_set(conn, objective_id, project_id, *, current_authority, require_review=True):
    """Return exact per-check pairs only after the latest generation verifies.

The authority callback receives (conn, envelope, retained effective snapshot).
It must raise on any live root/source/preimage/managed-lineage, owner, policy or
dependency mismatch, then return context, contributorIds and sourceEvidence.
No callback or model-provided receipt is accepted as runtime evidence. A caller
transaction is preserved; otherwise a bounded consistent read view is owned.
"""
    if type(require_review) is not bool or not callable(current_authority):
        raise ValueError('Prepared verification requires explicit current authority and review mode')
    with _read_view(conn):
        objective_id, project_id = contract._id(objective_id), contract._id(project_id)
        size = conn.execute('SELECT COALESCE(sum(length(CAST(envelope AS BLOB))),0) FROM prepared_validation_sets '
            'WHERE objective_id=? AND project_id=?', (objective_id, project_id)).fetchone()[0]
        if size > MAX_SET_HISTORY_BYTES:
            raise ValueError('Prepared set history exceeds its aggregate byte bound')
        _scalar_bounds(conn, 'prepared_validation_sets', 'objective_id=? AND project_id=?',
            (objective_id, project_id), {'id': 128, 'request_id': 128, 'envelope_sha256': 64})
        _scalar_bounds(conn, 'prepared_validation_steps',
            'set_id IN (SELECT id FROM prepared_validation_sets WHERE objective_id=? AND project_id=?)',
            (objective_id, project_id), {'run_id': 128, 'step_id': 64, 'definition_sha256': 64, 'recipe': 32, 'check_kind': 32})
        history = ledger._history(conn, objective_id, project_id)
        if not history:
            raise ValueError('No prepared validation generation has been reserved')
        envelope = history[-1]
        _snapshot_bounds(conn, envelope['setId'], objective_id, project_id)
        snapshot = ledger._snapshot(conn, envelope['setId'])
        _source_proof(conn, envelope, snapshot)
        _scalar_bounds(conn, 'organization_policy', 'id=1', (), {'fingerprint': 64})
        if (conn.execute("SELECT 1 FROM organization_policy WHERE id=1 AND (typeof(generation)<>'integer' OR generation<1)").fetchone()
                or conn.execute("SELECT 1 FROM objective_control WHERE objective_id=? AND (typeof(round)<>'integer' OR round<0)",
                                (objective_id,)).fetchone()):
            raise ValueError('Prepared current policy and round require retained integers')
        context, contributors, source_evidence = _authority(current_authority(conn, envelope, snapshot), envelope)
        grant = ledger._require_context(conn, context)
        results, checks = [], []
        for step in contract.validation_execution_order(envelope):
            result, receipt, events_sha256 = _run(conn, envelope, step, snapshot, grant)
            results.append(result)
            checks.append(_artifact(envelope, step, receipt, result, events_sha256))
        aggregate = ledger._aggregate(envelope, results)
        retained = conn.execute(f'SELECT {ledger._bounded_text("record", 32768)},{ledger._bounded_text("sha256", 64)} '
            'FROM prepared_validation_aggregates WHERE set_id=?', (envelope['setId'],)).fetchone()
        if retained is None or canonical(ledger._checked_json(*retained)) != canonical(aggregate):
            raise ValueError('Latest prepared aggregate is missing or differs from its verified host receipts')
        evidence = {**source_evidence, **{artifact['id']: artifact['sha256'] for _, artifact in checks}}
        verified = {'envelope': envelope, 'aggregate': aggregate, 'checks': checks,
            'contributorIds': contributors, 'sourceEvidence': source_evidence,
            'evidenceIds': sorted(evidence), 'evidenceHashes': dict(sorted(evidence.items())),
            'runtimeReceiptsVerified': True, 'independentReviewVerified': False,
            'sourceDeliveryAuthorized': False, 'replayAllowed': False}
        if require_review:
            _verify_review(conn, verified)
            verified['independentReviewVerified'] = True
        return verified


def record_prepared_validation_pair_review(conn, request, result, *, current_authority):
    """Retain approval/rejection inside the caller's owned completion transaction.

The parent must check its live owned claim before calling. We recheck that exact
request/token/reviewer and lease in SQLite; the parent marks it completed in the
same transaction. No receipt, reviewer identity, or evidence hash comes from the
model's result. Rejection remains immutable and never authorizes acceptance.
"""
    ledger._require_write_transaction(conn)
    contract._exact(result, {'approved', 'summary', 'evidenceIds'}, 'Prepared review response')
    if type(result['approved']) is not bool:
        raise ValueError('Prepared review requires an exact boolean approval')
    summary = _summary(result['summary'])
    raw = conn.execute(f'SELECT {ledger._bounded_text("objective_id", 128)},'
                       f'{ledger._bounded_text("payload", MAX_REVIEW_BYTES)} FROM requests WHERE id=?',
                       (contract._id(request['id']),)).fetchone()
    if raw is None or type(raw[1]) is not str:
        raise ValueError('Prepared review request is missing or oversized')
    payload = _json(raw[1].encode(), MAX_REVIEW_BYTES, 'Prepared review request')
    verified = verify_latest_prepared_validation_set(conn, raw[0], payload.get('projectId'),
        current_authority=current_authority, require_review=False)
    current = _request(conn, request['id'], verified)
    if (current['status'] != 'running' or type(current['token']) is not str or not current['token']
            or current['token'] != request['token'] or current['agent_id'] != request['agent_id']
            or type(current['lease']) not in (int, float) or not math.isfinite(current['lease'])
            or not current['lease'] > time.time()
            or result['evidenceIds'] != verified['evidenceIds']):
        raise ValueError('Prepared pair review requires its current owned lease and exact evidence IDs')
    record = {**_review_identity(verified, current), 'approved': result['approved'], 'summary': summary}
    encoded = canonical(record)
    if len(encoded.encode('utf-8')) > MAX_REVIEW_BYTES:
        raise ValueError('Prepared whole-pair review exceeds its retained byte bound')
    conn.execute('INSERT INTO prepared_validation_pair_reviews VALUES (?,?,?,?,?,?,?)',
        (verified['envelope']['setId'], current['id'], current['agent_id'], int(result['approved']),
         encoded, digest(record), time.time()))
    return {'setId': verified['envelope']['setId'], 'requestId': current['id'],
            'approved': result['approved'], 'reviewSha256': digest(record), 'committed': False}
