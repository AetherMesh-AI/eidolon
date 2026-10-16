"""Real-SQL pair verification using synthetic host events, never native evidence."""
from copy import deepcopy
import json
import sqlite3
import time

import pytest

from eidolon_cli import organization_prepared_validation_events as events
from eidolon_cli import organization_prepared_validation_ledger as ledger
from eidolon_cli import organization_prepared_validation_verification as verification
from eidolon_cli.organization_prepared_validation_stage import SOURCE_PROOF_SCHEMA, retain_source_proof
from eidolon_cli.organization_project_effective_snapshot import EffectiveProjectSnapshot
from eidolon_cli.organization_project_validation import canonical, digest
from eidolon_cli.organization_project_workspace_resources import container_labels, container_name
from tests.eidolon_cli.test_organization_prepared_validation_contract import prepared  # noqa: F401
from tests.eidolon_cli.test_organization_prepared_validation_ledger import (
    approve_claim, objective, proposal, reserve, state,  # noqa: F401
)
from tests.eidolon_cli.test_organization_project_workspace_receipt import events_for, fixture


def make_pair(state, obj=None, generation=1):
    conn = state['conn']
    if obj is None:
        obj = objective(state)
        source = EffectiveProjectSnapshot(obj['ctx']['objective_id'], obj['snapshot'], obj['snapshot'], ())
        obj['source'] = source
        obj['ctx'].update(preimage_sha256=source.preimage_sha256, managed_revisions_sha256=source.managed_revisions_sha256)
    envelope = proposal(state, obj, generation)
    envelope['setId'] = 'prepared_set_' + digest(envelope)[:32]
    for step in envelope['steps']:
        step['runId'] = 'prepared_run_' + digest(step)[:32]
    reserve(state, obj, envelope)
    with ledger._transaction(conn, write=True):
        retain_source_proof(conn, envelope, obj['source'])
    return {'obj': obj, 'envelope': envelope,
            'authority': {'context': obj['ctx'], 'contributorIds': ['author-1'],
                          'sourceEvidence': {'reviewed-task': digest({'reviewed': 'task source'}),
                                             'reviewed-config': digest({'reviewed': 'config and test scope'})}}}


@pytest.fixture
def pair(state):
    state['conn'].executescript(events.PREPARED_RUNTIME_SCHEMA + SOURCE_PROOF_SCHEMA + verification.PREPARED_PAIR_REVIEW_SCHEMA)
    return make_pair(state)


def records(pair, step):
    receipt, _ = fixture(step['recipe'])
    envelope, ctx = pair['envelope'], pair['obj']['ctx']
    identity = receipt['resourceIdentity']
    identity.update(setId=envelope['setId'], runId=step['runId'], nonce=digest(step),
                    authoritySha256=envelope['identity']['authoritySha256'])
    receipt.update(resourceIdentity=identity, containerName=container_name(identity), containerLabels=container_labels(identity),
        containerId=digest({'container': step['runId']}), snapshotSha256=ctx['snapshot_sha256'],
        profileSha256=digest(ctx['profile']), bundleSha256=ctx['profile']['bundleSha256'],
        stepId=step['id'], stepDefinitionSha256=step['definitionSha256'], dependencyRootIdentity={'device': 12, 'inode': 34},
        stderr='RAW DIAGNOSTIC MUST NEVER ENTER REVIEW ARTIFACT')
    receipt['stagingIdentity']['path'] = '/private/' + container_name(identity)
    outcome = receipt['outcome']
    path = '/source/root0/apps/desktop/src/main.test.tsx'
    outcome.update(configuration=next(item['config'] for item in ctx['profile']['steps'] if item['id'] == step['id']),
                   selectedFiles=[path], reportedFiles=[path], sourceCount=1 if step['recipe'] == 'workspace_typescript' else 0)
    if step['recipe'] == 'workspace_vitest_ui':
        outcome['modules'][0]['path'] = path
    return events_for(receipt)


def append(state, stream):
    for event in stream:
        with ledger._transaction(state['conn'], write=True):
            events.append_prepared_runtime_event(state['conn'], event)


def finish_pair(state, pair, *, failed=False):
    for index, step in enumerate(pair['envelope']['steps']):
        stream = records(pair, step)
        if failed and index == 1:
            stream[-1]['data']['receipt']['status'] = 'failed'
            stream[-1]['data']['receiptSha256'] = digest(stream[-1]['data']['receipt'])
        append(state, stream)
    ledger.record_prepared_aggregate_claim(state['conn'], pair['envelope']['setId'])


def verify(state, pair, *, review=False, authority=None):
    def current(conn, envelope, snapshot):
        assert conn is state['conn'] and conn.in_transaction
        assert snapshot == pair['obj']['snapshot']
        assert envelope == pair['envelope']
        return deepcopy(pair['authority'])
    return verification.verify_latest_prepared_validation_set(state['conn'], pair['envelope']['objectiveId'],
        pair['envelope']['projectId'], current_authority=authority or current, require_review=review)


def review_request(state, pair):
    answer = verify(state, pair)
    payload = {key: answer[key] for key in ('evidenceIds', 'evidenceHashes')}
    payload.update(executionContract='prepared-validation-v1', projectId='desktop',
                   setId=pair['envelope']['setId'], round=pair['envelope']['round'])
    identifier = 'review-' + digest(payload)[:32]
    state['conn'].execute("INSERT INTO requests(id,objective_id,type,team,priority,status,created,payload,agent_id,token,lease) "
        "VALUES (?,?,'request.test_review','engineering',1,'running',?,?,'reviewer-1','private-review-token',?)",
        (identifier, pair['envelope']['objectiveId'], time.time(), canonical(payload), time.time() + 120))
    request = dict(state['conn'].execute('SELECT * FROM requests WHERE id=?', (identifier,)).fetchone())
    result = {'approved': True, 'summary': 'Reviewed the entire pair and bounded source/test/config evidence.',
              'evidenceIds': answer['evidenceIds']}
    return request, result


def persist_review(state, pair, request, result, *, complete=True):
    conn = state['conn']
    with ledger._transaction(conn, write=True):
        outcome = verification.record_prepared_validation_pair_review(conn, request, result,
            current_authority=lambda *_: deepcopy(pair['authority']))
        assert outcome['committed'] is False and conn.in_transaction
        if complete:
            conn.execute("UPDATE requests SET status='completed',token=NULL,lease=NULL WHERE id=?", (request['id'],))
    return outcome


def test_complete_exact_pair_requires_whole_review_and_returns_per_check_artifacts(state, pair):
    finish_pair(state, pair)
    answer = verify(state, pair)
    assert answer['runtimeReceiptsVerified'] and not answer['independentReviewVerified']
    assert [item[1]['projectExecution']['checkKind'] for item in answer['checks']] == ['project_typecheck', 'project_tests']
    assert not answer['sourceDeliveryAuthorized'] and not answer['replayAllowed']
    for row, artifact in answer['checks']:
        assert row['id'] == artifact['id'] and len(row['id']) <= 128
        assert digest(json.loads(artifact['content'])) == artifact['sha256'] == row['result_sha256']
        assert artifact['projectExecution']['receiptSha256'] in {item['receiptSha256'] for item in answer['aggregate']['results']}
        assert artifact['projectExecution']['coverage']['selectedFilesCount'] == 1
        assert 'not every-byte model review' in artifact['projectExecution']['reviewScope']
        for forbidden in ('RAW DIAGNOSTIC', 'reviewed fixture', '/private/', 'main.test.tsx', 'selectedFiles"', 'files"'):
            assert forbidden not in artifact['content']
    with pytest.raises(ValueError, match='whole-pair review'):
        verify(state, pair, review=True)
    request, result = review_request(state, pair)
    persist_review(state, pair, request, result)
    assert verify(state, pair, review=True)['independentReviewVerified'] is True


@pytest.mark.parametrize('prefix', range(7))
def test_each_partial_second_run_blocks_pair_even_with_first_run_success(state, pair, prefix):
    first, second = pair['envelope']['steps']
    append(state, records(pair, first))
    append(state, records(pair, second)[:prefix])
    with pytest.raises(ValueError, match='host runtime events|terminal host receipt'):
        verify(state, pair)


@pytest.mark.parametrize('failed', [False, True])
def test_newer_incomplete_or_failed_generation_never_falls_back_to_approved_pair(state, pair, failed):
    finish_pair(state, pair)
    request, result = review_request(state, pair)
    persist_review(state, pair, request, result)
    later = make_pair(state, pair['obj'], generation=2)
    if failed:
        finish_pair(state, later, failed=True)
    with pytest.raises(ValueError, match='host runtime events|failed or has incomplete'):
        verify(state, later, review=True)


def test_inert_result_and_review_claims_cannot_replace_host_events(state, pair):
    approve_claim(state, pair['obj'], pair['envelope'])
    with pytest.raises(ValueError, match='host runtime events'):
        verify(state, pair, review=True)


@pytest.mark.parametrize('change', ['round', 'policy', 'preimage', 'revisions', 'snapshot', 'owner', 'dependencies'])
def test_current_authority_changes_invalidate_retained_success(state, pair, change):
    finish_pair(state, pair)
    if change == 'round':
        state['conn'].execute('UPDATE objective_control SET round=round+1 WHERE objective_id=?', (pair['envelope']['objectiveId'],))
    elif change == 'policy':
        state['conn'].execute('UPDATE organization_policy SET generation=generation+1')
    elif change in {'preimage', 'revisions', 'snapshot'}:
        key = {'preimage': 'preimage_sha256', 'revisions': 'managed_revisions_sha256', 'snapshot': 'snapshot_sha256'}[change]
        pair['authority']['context'][key] = '0' * 64
    else:
        def denied(*_):
            raise ValueError('Live ' + change + ' authority changed')
        with pytest.raises(ValueError, match='authority changed'):
            verify(state, pair, authority=denied)
        return
    with pytest.raises(ValueError):
        verify(state, pair)


@pytest.mark.parametrize('change', ['empty-source', 'source-collision', 'missing-contributors', 'duplicate-contributors', 'self-review'])
def test_review_requires_exact_nonempty_source_evidence_and_independence(state, pair, change):
    finish_pair(state, pair)
    if change == 'empty-source':
        pair['authority']['sourceEvidence'] = {}
    elif change == 'source-collision':
        pair['authority']['sourceEvidence'] = {pair['envelope']['steps'][0]['runId']: 'a' * 64}
    elif change == 'missing-contributors':
        pair['authority']['contributorIds'] = []
    elif change == 'duplicate-contributors':
        pair['authority']['contributorIds'] = ['author-1', 'author-1']
    else:
        request, result = review_request(state, pair)
        pair['authority']['contributorIds'] = ['reviewer-1']
        with pytest.raises(ValueError, match='independent'):
            persist_review(state, pair, request, result)
        return
    with pytest.raises(ValueError):
        verify(state, pair)


@pytest.mark.parametrize('change', ['wrong-token', 'expired', 'queued', 'reviewer', 'missing-evidence', 'model-receipt', 'nonboolean'])
def test_review_completion_checks_owned_current_request_and_rejects_receipt_injection(state, pair, change):
    finish_pair(state, pair)
    request, result = review_request(state, pair)
    if change == 'wrong-token':
        request['token'] = 'other-token'
    elif change == 'expired':
        state['conn'].execute('UPDATE requests SET lease=0 WHERE id=?', (request['id'],))
    elif change == 'queued':
        state['conn'].execute("UPDATE requests SET status='queued' WHERE id=?", (request['id'],))
    elif change == 'reviewer':
        request['agent_id'] = 'another-reviewer'
    elif change == 'missing-evidence':
        result['evidenceIds'] = result['evidenceIds'][:-1]
    elif change == 'model-receipt':
        result['receipt'] = records(pair, pair['envelope']['steps'][0])[-1]['data']['receipt']
    else:
        result['approved'] = 1
    with pytest.raises(ValueError):
        persist_review(state, pair, request, result)
    assert state['conn'].execute('SELECT count(*) FROM prepared_validation_pair_reviews').fetchone()[0] == 0


@pytest.mark.parametrize('field', ['setId', 'round', 'projectId', 'executionContract', 'evidenceIds', 'evidenceHashes'])
def test_review_request_must_bind_the_exact_pair_and_evidence_map(state, pair, field):
    finish_pair(state, pair)
    request, result = review_request(state, pair)
    payload = json.loads(request['payload'])
    payload[field] = [] if field == 'evidenceIds' else {} if field == 'evidenceHashes' else 99 if field == 'round' else 'other'
    state['conn'].execute('UPDATE requests SET payload=? WHERE id=?', (canonical(payload), request['id']))
    with pytest.raises(ValueError):
        persist_review(state, pair, request, result)


@pytest.mark.parametrize('complete,approved', [(False, True), (True, False)])
def test_incomplete_or_rejected_review_never_authorizes_pair(state, pair, complete, approved):
    finish_pair(state, pair)
    request, result = review_request(state, pair)
    result['approved'] = approved
    persist_review(state, pair, request, result, complete=complete)
    with pytest.raises(ValueError, match='incomplete, rejected or changed'):
        verify(state, pair, review=True)


@pytest.mark.parametrize('change', ['source', 'contributors', 'reviewer', 'payload'])
def test_completed_review_is_invalidated_by_current_source_scope_or_request_changes(state, pair, change):
    finish_pair(state, pair)
    request, result = review_request(state, pair)
    persist_review(state, pair, request, result)
    if change == 'source':
        pair['authority']['sourceEvidence']['reviewed-task'] = 'a' * 64
    elif change == 'contributors':
        pair['authority']['contributorIds'].append('author-2')
    elif change == 'reviewer':
        state['conn'].execute("INSERT INTO agents VALUES ('reviewer-2','Reviewer','Worker',NULL,'engineering','[]')")
        state['conn'].execute("UPDATE requests SET agent_id='reviewer-2' WHERE id=?", (request['id'],))
    else:
        state['conn'].execute("UPDATE requests SET payload='{}' WHERE id=?", (request['id'],))
    with pytest.raises(ValueError):
        verify(state, pair, review=True)


def test_success_and_failure_preserve_caller_transactions_and_rollback_own_reads(state, pair):
    finish_pair(state, pair)
    verify(state, pair)
    assert not state['conn'].in_transaction
    with pytest.raises(ValueError):
        verify(state, pair, review=True)
    assert not state['conn'].in_transaction
    request, result = review_request(state, pair)
    conn = state['conn']
    conn.execute('BEGIN IMMEDIATE')
    verification.record_prepared_validation_pair_review(conn, request, result, current_authority=lambda *_: pair['authority'])
    assert conn.in_transaction
    with pytest.raises(ValueError):
        verify(state, pair, review=True)
    assert conn.in_transaction
    conn.rollback()
    assert conn.execute('SELECT count(*) FROM prepared_validation_pair_reviews').fetchone()[0] == 0


@pytest.mark.parametrize('action', ['update', 'delete', 'replace'])
def test_whole_pair_reviews_are_immutable(state, pair, action):
    finish_pair(state, pair)
    request, result = review_request(state, pair)
    persist_review(state, pair, request, result)
    sql = {'update': 'UPDATE prepared_validation_pair_reviews SET approved=0',
           'delete': 'DELETE FROM prepared_validation_pair_reviews',
           'replace': 'INSERT OR REPLACE INTO prepared_validation_pair_reviews SELECT * FROM prepared_validation_pair_reviews'}[action]
    with pytest.raises(sqlite3.IntegrityError):
        state['conn'].execute(sql)
    assert verify(state, pair, review=True)['independentReviewVerified']


def test_static_artifact_resolves_exact_known_run_without_conferring_current_authority(state, pair):
    finish_pair(state, pair)
    answer = verify(state, pair)
    for row, artifact in answer['checks']:
        assert verification.prepared_validation_artifact(state['conn'], row['id']) == artifact
    assert verification.prepared_validation_artifact(state['conn'], 'legacy-run') is None
    assert verification.prepared_validation_artifact(state['conn'], 'prepared_run_' + '0' * 32) is None
    assert verification.prepared_validation_artifact(state['conn'], None) is None
    later = make_pair(state, pair['obj'], generation=2)
    # Retained evidence can still be inspected; it cannot satisfy a latest-set gate.
    assert verification.prepared_validation_artifact(state['conn'], answer['checks'][0][0]['id'])
    with pytest.raises(ValueError):
        verify(state, later)


@pytest.mark.parametrize('change', ['missing-coverage', 'wrong-step', 'wrong-snapshot', 'wrong-runtime', 'extra-receipt-field'])
@pytest.mark.parametrize('run_index', [0, 1])
def test_each_exact_run_is_replayed_with_strict_receipt_context(state, pair, change, run_index):
    for index, step in enumerate(pair['envelope']['steps']):
        stream = records(pair, step)
        if index == run_index:
            receipt = deepcopy(stream[-1]['data']['receipt'])
            stream[-1]['data']['receipt'] = receipt
            if change == 'missing-coverage':
                receipt['outcome']['selectedFiles'] = []
            elif change == 'wrong-step':
                receipt['stepId'] = 'wrong-step'
            elif change == 'wrong-snapshot':
                receipt['snapshotSha256'] = '0' * 64
            elif change == 'wrong-runtime':
                receipt['runtime']['imageId'] = 'sha256:' + '0' * 64
            else:
                receipt['modelSupplied'] = True
            stream[-1]['data']['receiptSha256'] = digest(receipt)
        append(state, stream)
    ledger.record_prepared_aggregate_claim(state['conn'], pair['envelope']['setId'])
    with pytest.raises(ValueError):
        verify(state, pair)
    run_id = pair['envelope']['steps'][run_index]['runId']
    with pytest.raises(ValueError):
        verification.prepared_validation_artifact(state['conn'], run_id)


@pytest.mark.parametrize('table,column', [('dispatches', 'nonce'), ('resources', 'container_id'),
                                         ('cleanups', 'container_confirmed'), ('results', 'receipt_sha256'),
                                         ('aggregates', 'sha256')])
def test_host_event_mirrors_must_match_exact_stream_not_just_self_consistent_claims(state, pair, table, column):
    finish_pair(state, pair)
    conn = state['conn']
    conn.execute('DROP TRIGGER immutable_prepared_validation_' + table + '_update')
    # Simulate retained-database corruption without letting FK rejection replace
    # the verifier check. Normal application paths cannot make these mutations.
    conn.execute('PRAGMA foreign_keys=OFF')
    conn.execute('UPDATE prepared_validation_' + table + ' SET ' + column + '=? WHERE rowid=(SELECT min(rowid) FROM prepared_validation_' + table + ')',
                 (0 if column == 'container_confirmed' else '0' * 64,))
    conn.execute('PRAGMA foreign_keys=ON')
    with pytest.raises(ValueError):
        verify(state, pair)


def test_extra_cleanup_claim_cannot_substitute_for_complete_event_stream(state, pair):
    finish_pair(state, pair)
    step = pair['envelope']['steps'][0]
    resource = records(pair, step)[0]['resource']
    ledger.record_prepared_cleanup_claim(state['conn'], step['runId'], event_id='untrusted-extra-cleanup',
        nonce=resource['nonce'], container_id=digest({'container': step['runId']}),
        container_confirmed=True, staging_confirmed=True)
    with pytest.raises(ValueError, match='omit or add'):
        verify(state, pair)


def test_later_cleanup_never_upgrades_terminal_failed_receipt(state, pair):
    finish_pair(state, pair, failed=True)
    step = pair['envelope']['steps'][1]
    append(state, [records(pair, step)[5]])
    with pytest.raises(ValueError, match='failed or has incomplete'):
        verify(state, pair)


@pytest.mark.parametrize('change', ['missing', 'record-hash', 'preimage-hash', 'managed-hash', 'coverage', 'effective'])
def test_source_proof_is_required_and_bound_to_retained_snapshot_and_original_coverage(state, pair, change):
    finish_pair(state, pair)
    conn = state['conn']
    if change == 'missing':
        conn.execute('DROP TRIGGER prepared_source_proof_no_delete')
        conn.execute('DELETE FROM prepared_validation_source_proofs')
    else:
        conn.execute('DROP TRIGGER prepared_source_proof_no_update')
        proof = pair['obj']['source'].source_evidence()
        if change == 'preimage-hash':
            proof['original']['files'][0]['sha256'] = '0' * 64
        elif change == 'managed-hash':
            proof['managedRevisions']['revisions'].append({'forged': 'managed lineage'})
        elif change == 'coverage':
            proof['original']['files'].pop()
        elif change == 'effective':
            proof['effective']['files'][0]['sha256'] = '0' * 64
        conn.execute('UPDATE prepared_validation_source_proofs SET record=?,sha256=?',
                     (canonical(proof), '0' * 64 if change == 'record-hash' else digest(proof)))
    with pytest.raises(ValueError):
        verify(state, pair)


def test_sql_history_bound_rejects_before_materializing_any_envelope(state, pair, monkeypatch):
    monkeypatch.setattr(verification, 'MAX_SET_HISTORY_BYTES', 8)
    def forbidden(*_):
        pytest.fail('Oversized retained history was materialized')
    monkeypatch.setattr(ledger, '_history', forbidden)
    with pytest.raises(ValueError, match='aggregate byte bound'):
        verify(state, pair)
    assert not state['conn'].in_transaction


def test_sql_source_proof_bound_applies_before_python_json_materialization(state, pair, monkeypatch):
    monkeypatch.setattr(verification, 'MAX_SOURCE_PROOF_BYTES', 8)
    with pytest.raises(ValueError, match='exceeds its byte bound'):
        verification._source_proof(state['conn'], pair['envelope'], pair['obj']['snapshot'])


def test_sql_runtime_event_bound_applies_before_event_materialization(state, pair, monkeypatch):
    from eidolon_cli import organization_project_workspace_resources as resources
    finish_pair(state, pair)
    monkeypatch.setattr(events, 'MAX_RUNTIME_EVENT_BYTES', 8)
    def forbidden(*_):
        pytest.fail('Oversized runtime event was materialized')
    monkeypatch.setattr(resources, 'normalize_runtime_event', forbidden)
    with pytest.raises(ValueError, match='retained count/byte limits'):
        verify(state, pair)


def test_review_persistence_requires_caller_transaction(state, pair):
    finish_pair(state, pair)
    request, result = review_request(state, pair)
    with pytest.raises(ValueError, match='existing organization transaction'):
        verification.record_prepared_validation_pair_review(state['conn'], request, result,
            current_authority=lambda *_: pair['authority'])


@pytest.mark.parametrize('status,projected', [('failed', 'failed'), ('interrupted', 'interrupted'),
    ('cancelled', 'interrupted'), ('timed_out', 'interrupted'), ('blocked', 'blocked'),
    ('unsupported', 'blocked'), ('cleanup_failed', 'blocked'), ('staging_cleanup_failed', 'blocked')])
def test_static_failure_artifact_is_terminal_bound_sanitized_and_never_pass_evidence(state, pair, status, projected):
    step = pair['envelope']['steps'][0]
    stream = records(pair, step)
    receipt = stream[-1]['data']['receipt']
    receipt.update(status=status, reason='DO NOT EXPOSE RAW REASON secret source body')
    stream[-1]['data']['receiptSha256'] = digest(receipt)
    append(state, stream)
    artifact = verification.prepared_validation_artifact(state['conn'], step['runId'])
    record = artifact['projectExecution']
    assert record['status'] == projected and record['hostStatus'] == status
    assert record['acceptanceAuthorized'] is record['sourceDeliveryAuthorized'] is record['replayAllowed'] is False
    assert record['receiptSha256'] == digest(receipt)
    assert record['runtimeEventsSha256'] == digest(stream)
    assert record['envelopeSha256'] == digest(pair['envelope'])
    assert record['resultReferenceSha256'] == state['conn'].execute(
        'SELECT sha256 FROM prepared_validation_results WHERE run_id=?', (step['runId'],)).fetchone()[0]
    assert not {'exitCode', 'cleanupConfirmed', 'stagingCleanupConfirmed', 'coverage', 'outcome'} & record.keys()
    for forbidden in ('RAW REASON', 'RAW DIAGNOSTIC', 'secret source body', 'reviewed fixture', '/private/'):
        assert forbidden not in artifact['content']
    with pytest.raises(ValueError):
        verify(state, pair)


def test_exact_terminal_failure_before_allocation_is_reviewable_without_fabricating_resources(state, pair):
    step = pair['envelope']['steps'][0]
    stream = records(pair, step)
    cleanup = stream[5]
    cleanup['data'].update(containerId=None, containerCleanupConfirmed=False, stagingCleanupConfirmed=False, staging=None)
    terminal = stream[-1]
    terminal['data']['receipt'] = {'runner': 'eidolon.prepared-workspace', 'runnerVersion': 1,
        'resourceIdentity': stream[0]['resource'], 'status': 'blocked',
        'cleanupConfirmed': False, 'stagingCleanupConfirmed': False, 'containerId': None, 'stagingIdentity': None,
        'reason': 'Raw error does not enter evidence'}
    terminal['data']['receiptSha256'] = digest(terminal['data']['receipt'])
    append(state, [stream[0], cleanup, terminal])
    artifact = verification.prepared_validation_artifact(state['conn'], step['runId'])
    assert artifact['projectExecution']['status'] == 'blocked'
    assert state['conn'].execute('SELECT count(*) FROM prepared_validation_resources').fetchone()[0] == 0
    assert 'Raw error' not in artifact['content']


@pytest.mark.parametrize('status', ['unknown', 'persistence_failed', 'invented-status'])
def test_unknown_terminal_status_cannot_be_promoted_into_failure_artifact(state, pair, status):
    step = pair['envelope']['steps'][0]
    stream = records(pair, step)
    stream[-1]['data']['receipt']['status'] = status
    stream[-1]['data']['receiptSha256'] = digest(stream[-1]['data']['receipt'])
    append(state, stream)
    with pytest.raises(ValueError, match='exact known terminal host failure'):
        verification.prepared_validation_artifact(state['conn'], step['runId'])


@pytest.mark.parametrize('prefix', range(1, 7))
def test_static_lookup_never_promotes_incomplete_known_run_into_any_artifact(state, pair, prefix):
    step = pair['envelope']['steps'][0]
    append(state, records(pair, step)[:prefix])
    with pytest.raises(ValueError, match='terminal host receipt'):
        verification.prepared_validation_artifact(state['conn'], step['runId'])


def test_later_host_cleanup_observation_changes_whole_pair_review_identity(state, pair):
    finish_pair(state, pair)
    request, result = review_request(state, pair)
    persist_review(state, pair, request, result)
    append(state, [records(pair, pair['envelope']['steps'][0])[5]])
    with pytest.raises(ValueError, match='exact whole pair'):
        verify(state, pair, review=True)


@pytest.mark.parametrize('table,column', [('dispatches', 'image'), ('resources', 'container_id'),
    ('cleanups', 'nonce'), ('results', 'receipt_sha256'), ('aggregates', 'sha256'),
    ('source_proofs', 'sha256'), ('runtime_events', 'sha256'), ('sets', 'envelope_sha256'),
    ('snapshots', 'limits_sha256'), ('files', 'sha256'), ('files', 'path'), ('bindings', 'grant_sha256')])
def test_oversized_retained_scalars_reject_in_sql_before_python_allocation(state, pair, table, column):
    finish_pair(state, pair)
    conn = state['conn']
    trigger = {'source_proofs': 'prepared_source_proof_no_update',
               'runtime_events': 'immutable_prepared_runtime_event_update'}.get(
                   table, 'immutable_prepared_validation_' + table + '_update')
    conn.execute('DROP TRIGGER ' + trigger)
    conn.execute('PRAGMA foreign_keys=OFF')
    conn.execute('UPDATE prepared_validation_' + table + ' SET ' + column +
                 '=CAST(zeroblob(32768) AS TEXT) WHERE rowid=(SELECT min(rowid) FROM prepared_validation_' + table + ')')
    conn.execute('PRAGMA foreign_keys=ON')
    def bounded_decode(value):
        assert len(value) < 16384, 'Unbounded retained scalar escaped SQL into Python'
        return value.decode('utf-8')
    conn.text_factory = bounded_decode
    try:
        with pytest.raises(ValueError):
            verify(state, pair)
    finally:
        conn.text_factory = str


@pytest.mark.parametrize('change', ['infinite-lease', 'oversized-token'])
def test_malformed_owned_lease_metadata_cannot_record_review(state, pair, change):
    finish_pair(state, pair)
    request, result = review_request(state, pair)
    conn = state['conn']
    if change == 'infinite-lease':
        conn.execute('UPDATE requests SET lease=? WHERE id=?', (float('inf'), request['id']))
    else:
        conn.execute('UPDATE requests SET token=CAST(zeroblob(32768) AS TEXT) WHERE id=?', (request['id'],))
    with pytest.raises(ValueError, match='current owned lease'):
        persist_review(state, pair, request, result)


@pytest.mark.parametrize('target', ['resource', 'cleanup'])
@pytest.mark.parametrize('size', [0, 32768])
def test_early_failure_null_resource_cannot_hide_oversized_mirror_identity(state, pair, target, size):
    step = pair['envelope']['steps'][0]
    stream = records(pair, step)
    stream[5]['data'].update(containerId=None, containerCleanupConfirmed=False, stagingCleanupConfirmed=False, staging=None)
    receipt = {'runner': 'eidolon.prepared-workspace', 'runnerVersion': 1,
        'resourceIdentity': stream[0]['resource'], 'status': 'blocked', 'cleanupConfirmed': False,
        'stagingCleanupConfirmed': False, 'containerId': None, 'stagingIdentity': None}
    stream[-1]['data'].update(receipt=receipt, receiptSha256=digest(receipt))
    append(state, [stream[0], stream[5], stream[-1]])
    conn = state['conn']
    if target == 'resource':
        conn.execute('INSERT INTO prepared_validation_resources VALUES (?,CAST(zeroblob(?) AS TEXT),?)',
                     (step['runId'], size, time.time()))
    else:
        conn.execute('DROP TRIGGER immutable_prepared_validation_cleanups_update')
        conn.execute('PRAGMA foreign_keys=OFF')
        conn.execute('UPDATE prepared_validation_cleanups SET container_id=CAST(zeroblob(?) AS TEXT)', (size,))
        conn.execute('PRAGMA foreign_keys=ON')
    with pytest.raises(ValueError, match='resource mirror|scalar metadata'):
        verification.prepared_validation_artifact(conn, step['runId'])


def test_static_artifact_checks_exact_reservation_step_mirror(state, pair):
    finish_pair(state, pair)
    conn = state['conn']
    conn.execute('DROP TRIGGER immutable_prepared_validation_steps_update')
    conn.execute('UPDATE prepared_validation_steps SET definition_sha256=?', ('0' * 64,))
    with pytest.raises(ValueError, match='exact envelope step'):
        verification.prepared_validation_artifact(conn, pair['envelope']['steps'][0]['runId'])


@pytest.mark.parametrize('table,column', [('organization_policy', 'fingerprint'),
                                         ('organization_policy', 'generation'), ('objective_control', 'round')])
def test_current_policy_and_round_scalars_reject_before_authority_callback_allocation(state, pair, table, column):
    finish_pair(state, pair)
    conn = state['conn']
    conn.execute('UPDATE ' + table + ' SET ' + column + '=CAST(zeroblob(32768) AS TEXT)')
    def forbidden(*_):
        pytest.fail('Malformed current authority metadata reached its callback')
    def bounded_decode(value):
        assert len(value) < 16384, 'Malformed current authority metadata escaped SQL into Python'
        return value.decode('utf-8')
    conn.text_factory = bounded_decode
    try:
        with pytest.raises(ValueError, match='scalar metadata|retained integers'):
            verify(state, pair, authority=forbidden)
    finally:
        conn.text_factory = str
