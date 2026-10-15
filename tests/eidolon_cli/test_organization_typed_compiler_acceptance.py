"""Typed compiler criteria consume the reviewed native receipt, never model claims."""
import json
import threading

import pytest

from eidolon_cli.organization_outcome_contracts import contract_view, definitions, write_contract, verify_criteria
from tests.eidolon_cli.test_organization_project_typescript import native, _typecheck_reviewed_store  # noqa: F401


def test_native_reviewed_typecheck_satisfies_only_exact_typed_check(native, tmp_path):
    store, source, objective, claim, _ = _typecheck_reviewed_store(tmp_path)
    store.run_project_stage(claim, threading.Event())
    store.finish(claim, {})
    review = store.claim_next()
    payload = json.loads(review['payload'])
    store.finish(review, {'approved': True, 'summary': 'Exact independent compiler review', 'evidenceIds': payload['evidenceIds']})
    with store._write() as conn:
        contract = write_contract(conn, objective['id'], definitions([
            {'id': 'compile', 'text': 'Types are valid', 'kind': 'authorized_check', 'checkKind': 'project_typecheck'}], ''))
        artifact = store.verify_project_check_runs(conn, objective['id'], 'project_typecheck')[0][1]
        with pytest.raises(ValueError):
            store.verify_project_check_runs(conn, objective['id'], 'project_typecheck', 'unselected-project')
        request = {**dict(review), 'payload': json.dumps({'outcomeContractSha256': contract['sha256'],
            'evidenceHashes': {artifact['id']: artifact['sha256']}})}
        result = {'criterionId': 'compile', 'criterionRevision': contract['criteria'][0]['revision'],
                  'satisfied': True, 'evidenceIds': [artifact['id']]}
        with pytest.raises(ValueError, match='exact current evidence'):
            verify_criteria(store, conn, request, [{**result, 'evidenceIds': ['invented']}])
        store._verify_required_checks(conn, objective['id'], ['project_typecheck'])
        with pytest.raises(ValueError, match='Compiler evidence'):
            store._verify_required_checks(conn, objective['id'], ['project_tests'])
        with pytest.raises(ValueError):
            store._verify_required_checks(conn, objective['id'], ['project_build'])
        verify_criteria(store, conn, request, [result])
        assert conn.execute('SELECT contract_sha256 FROM objective_contract_acceptances WHERE request_id=?',
                            (review['id'],)).fetchone()[0] == contract['sha256']
    source.write_text('export const value: number = "wrong";')
    with store._connect() as conn:
        with pytest.raises(ValueError):
            store._verify_required_checks(conn, objective['id'], ['project_typecheck'])


def test_native_typecheck_acceptance_finish_is_exact_and_durable(native, tmp_path):
    from eidolon_cli.organization_store import OrganizationStore
    from tests.eidolon_cli.test_organization_acceptance import _integrate, _decision

    store, source, objective, claim, original_source = _typecheck_reviewed_store(tmp_path)
    with store._write() as conn:
        conn.execute('UPDATE objective_control SET criteria=? WHERE objective_id=?',
                     (json.dumps(['Types are valid']), objective['id']))
        write_contract(conn, objective['id'], definitions([
            {'id': 'compile', 'text': 'Types are valid', 'kind': 'authorized_check', 'checkKind': 'project_typecheck'}], ''))
    store.run_project_stage(claim, threading.Event())
    store.finish(claim, {})
    review = store.claim_next()
    payload = json.loads(review['payload'])
    store.finish(review, {'approved': True, 'summary': 'Reviewed exact typecheck receipt', 'evidenceIds': payload['evidenceIds']})
    accept = _integrate(store)
    decision = _decision(store, accept)
    criterion = store.context(accept)['objective']['outcomeContract']['criteria'][0]
    decision['criteriaResults'][0].update(criterionId=criterion['id'], criterionRevision=criterion['revision'])
    with store._write() as conn:
        original_payload = conn.execute('SELECT payload FROM requests WHERE id=?', (accept['id'],)).fetchone()[0]
        forged = json.loads(original_payload)
        forged['evidenceHashes'][payload['evidenceIds'][0]] = '0' * 64
        conn.execute('UPDATE requests SET payload=? WHERE id=?', (json.dumps(forged), accept['id']))
    with pytest.raises(ValueError, match='current persisted objective evidence'):
        store.finish(accept, decision)
    with store._write() as conn:
        assert conn.execute('SELECT count(*) FROM objective_contract_acceptances WHERE request_id=?', (accept['id'],)).fetchone()[0] == 0
        conn.execute('UPDATE requests SET payload=? WHERE id=?', (original_payload, accept['id']))
    source.write_text('export const value: number = "changed after review";')
    with pytest.raises(ValueError):
        store.finish(accept, decision)
    with store._connect() as conn:
        assert conn.execute('SELECT count(*) FROM objective_contract_acceptances WHERE request_id=?', (accept['id'],)).fetchone()[0] == 0
    source.write_bytes(original_source)
    store = OrganizationStore(store.path, store.settings)
    assert store.finish(accept, decision)
    reopened = OrganizationStore(store.path, store.settings)
    outcome = reopened.snapshot()['objectives'][0]
    assert outcome['id'] == objective['id'] and outcome['status'] == 'completed'
    with reopened._connect() as conn:
        accepted = conn.execute('SELECT * FROM objective_contract_acceptances WHERE request_id=?', (accept['id'],)).fetchone()
        assert accepted is not None
        assert accepted['contract_sha256'] == contract_view(conn, objective['id'])['sha256']
        reopened._verify_required_checks(conn, objective['id'], ['project_typecheck'])
