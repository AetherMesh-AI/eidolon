"""Typed build acceptance requires actual reviewed native compiler output bytes."""
import json
import threading

import pytest

from eidolon_cli.organization_outcome_contracts import definitions, write_contract, verify_criteria
from tests.eidolon_cli.test_organization_project_typescript_build import native, _build_reviewed_store  # noqa: F401


def test_native_build_criterion_requires_receipt_and_complete_output_manifest(native, tmp_path):
    store, source, objective, claim, original = _build_reviewed_store(tmp_path)
    store.run_project_stage(claim, threading.Event())
    store.finish(claim, {})
    review = store.claim_next()
    payload = json.loads(review['payload'])
    assert len(payload['evidenceIds']) == 3
    store.finish(review, {'approved': True, 'summary': 'Reviewed receipt and both actual outputs', 'evidenceIds': payload['evidenceIds']})
    with store._write() as conn:
        contract = write_contract(conn, objective['id'], definitions([
            {'id': 'build', 'text': 'Complete build outputs', 'kind': 'authorized_check', 'checkKind': 'project_build'}], ''))
        pairs = store.verify_project_check_runs(conn, objective['id'], 'project_build')
        ids = [artifact['id'] for _, artifact in pairs]
        assert len(ids) == 3
        request = {**dict(review), 'payload': json.dumps({'outcomeContractSha256': contract['sha256'],
            'evidenceHashes': {artifact['id']: artifact['sha256'] for _, artifact in pairs}})}
        result = {'criterionId': 'build', 'criterionRevision': contract['criteria'][0]['revision'],
                  'satisfied': True, 'evidenceIds': ids}
        for subset in (ids[:1], ids[1:], ids[:-1], ['invented']):
            with pytest.raises(ValueError, match='exact current evidence'):
                verify_criteria(store, conn, request, [{**result, 'evidenceIds': subset}])
        store._verify_required_checks(conn, objective['id'], ['project_build'])
        for wrong_kind in ('project_tests', 'project_typecheck'):
            with pytest.raises(ValueError):
                store._verify_required_checks(conn, objective['id'], [wrong_kind])
        verify_criteria(store, conn, request, [result])
        assert conn.execute('SELECT contract_sha256 FROM objective_contract_acceptances WHERE request_id=?',
                            (review['id'],)).fetchone()[0] == contract['sha256']
    assert source.read_bytes() == original
    source.write_text('export const value = 99;')
    with store._connect() as conn:
        with pytest.raises(ValueError):
            store._verify_required_checks(conn, objective['id'], ['project_build'])


def test_native_build_acceptance_finish_rejects_forged_hashes(native, tmp_path):
    from tests.eidolon_cli.test_organization_acceptance import _integrate, _decision
    store, _, objective, claim, _ = _build_reviewed_store(tmp_path)
    with store._write() as conn:
        conn.execute('UPDATE objective_control SET criteria=? WHERE objective_id=?',
                     (json.dumps(['Complete build outputs']), objective['id']))
        write_contract(conn, objective['id'], definitions([
            {'id': 'build', 'text': 'Complete build outputs', 'kind': 'authorized_check', 'checkKind': 'project_build'}], ''))
    store.run_project_stage(claim, threading.Event())
    store.finish(claim, {})
    review = store.claim_next()
    payload = json.loads(review['payload'])
    store.finish(review, {'approved': True, 'summary': 'Reviewed complete build', 'evidenceIds': payload['evidenceIds']})
    accept = _integrate(store)
    decision = _decision(store, accept)
    criterion = store.context(accept)['objective']['outcomeContract']['criteria'][0]
    decision['criteriaResults'][0].update(criterionId=criterion['id'], criterionRevision=criterion['revision'])
    with store._write() as conn:
        original = conn.execute('SELECT payload FROM requests WHERE id=?', (accept['id'],)).fetchone()[0]
        forged = json.loads(original)
        forged['evidenceHashes'][payload['evidenceIds'][1]] = '0' * 64
        conn.execute('UPDATE requests SET payload=? WHERE id=?', (json.dumps(forged), accept['id']))
    with pytest.raises(ValueError, match='current persisted objective evidence'):
        store.finish(accept, decision)
    with store._write() as conn:
        assert conn.execute('SELECT count(*) FROM objective_contract_acceptances WHERE request_id=?', (accept['id'],)).fetchone()[0] == 0
        conn.execute('UPDATE requests SET payload=? WHERE id=?', (original, accept['id']))
    assert store.finish(accept, decision)
    assert store.snapshot()['objectives'][0]['status'] == 'completed'
