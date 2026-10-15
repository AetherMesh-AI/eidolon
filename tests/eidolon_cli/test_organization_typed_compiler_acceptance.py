"""Typed compiler criteria consume the reviewed native receipt, never model claims."""
import json
import threading

import pytest

from eidolon_cli.organization_outcome_contracts import definitions, write_contract, verify_criteria
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
        with pytest.raises(ValueError, match='unsupported'):
            store._verify_required_checks(conn, objective['id'], ['project_build'])
        verify_criteria(store, conn, request, [result])
        assert conn.execute('SELECT contract_sha256 FROM objective_contract_acceptances WHERE request_id=?',
                            (review['id'],)).fetchone()[0] == contract['sha256']
    source.write_text('export const value: number = "wrong";')
    with store._connect() as conn:
        with pytest.raises(ValueError):
            store._verify_required_checks(conn, objective['id'], ['project_typecheck'])
