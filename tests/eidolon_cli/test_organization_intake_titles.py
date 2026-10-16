"""Titles use persisted owner lineage without discarding clarified execution scope."""
import json
import sqlite3

import pytest

from eidolon_cli.organization_store import OrganizationStore
from tests.eidolon_cli.test_organization_dependency_receipts import accepted, receipt
from tests.eidolon_cli.test_organization_request_intake import response, reserve


@pytest.mark.parametrize('original', [
    'Document the literals Clarification question: and Owner answer: in the report.',
    '整理研究報告🌍 ' * 80,
    'dependency',
], ids=['literal-delimiters', 'long-unicode', 'dependency'])
@pytest.mark.parametrize('rounds', [0, 2])
def test_original_request_title_preserves_full_scope_and_retry_receipts(tmp_path, original, rounds):
    store, predecessor, _ = accepted(tmp_path)
    predecessor_receipt = receipt(store, predecessor)
    dependent = original == 'dependency'
    directive = f'Use objective {predecessor} to prepare the follow-on report with ' + 'complete detail ' * 20
    original = directive.strip() if dependent else original.strip()
    intake, _ = store.begin_intake(original, idempotency_key='original', owner_priority='P1')
    complete = original
    for index in range(rounds):
        reserve(store, intake)
        question = f'Which source should round {index} use?'
        store.finish_intake(intake['id'], response(store, intake, routeId=None, question={
            'text': question, 'neededFor': 'The source is unspecified.', 'checkedEvidenceIds': ['owner-request']}))
        parent_id = intake['id']
        answer = 'Include all supplied evidence and preserve Unicode: 日本語 🌍.'
        intake, created = store.begin_intake(answer, idempotency_key=f'answer-{index}', parent_intake_id=parent_id)
        assert created
        again, created = store.begin_intake(answer, idempotency_key=f'answer-{index}', parent_intake_id=parent_id)
        assert not created and again == intake
        complete += '\n\nClarification question: ' + question + '\nOwner answer: ' + answer
    prompt = store.intake_prompt(intake['id'])
    assert prompt['request'] == complete
    assert prompt['evidence'][0]['text'] == complete
    reserve(store, intake)
    criteria = ['Deliver the complete follow-on report using the accepted predecessor.']
    finished = store.finish_intake(intake['id'], response(store, intake,
        priority='P4', acceptanceCriteria=criteria, dependencyCandidateIds=[predecessor] if dependent else []),
        usage={'inputTokens': 19, 'outputTokens': 11})
    if dependent and rounds:
        assert finished['status'] == 'needs_review'
        with pytest.raises(ValueError, match='review'):
            store.admit_intake(intake['id'])
        assert receipt(store, predecessor) == predecessor_receipt
        assert len(store.snapshot()['objectives']) == 1
        return
    assert finished['status'] == 'proposed', finished['reason']
    with store._connect() as conn:
        before = dict(conn.execute('SELECT * FROM organization_request_intakes WHERE id=?', (intake['id'],)).fetchone())
    admitted = store.admit_intake(intake['id'])
    store = OrganizationStore(store.path)
    assert store.admit_intake(intake['id']) == admitted
    with store._connect() as conn:
        objective = dict(conn.execute('SELECT * FROM objectives WHERE id=?', (admitted['objectiveId'],)).fetchone())
        control = conn.execute('SELECT * FROM objective_control WHERE objective_id=?', (admitted['objectiveId'],)).fetchone()
        after = dict(conn.execute('SELECT * FROM organization_request_intakes WHERE id=?', (intake['id'],)).fetchone())
        edges = conn.execute('SELECT * FROM organization_objective_dependencies WHERE successor_id=?', (admitted['objectiveId'],)).fetchall()
    expected_title = original if len(original) <= 120 else original[:119] + '…'
    assert objective['title'] == expected_title
    assert objective['description'] == complete
    assert json.loads(control['criteria']) == criteria
    assert json.loads(control['required_checks']) == []
    assert control['delivery_mode'] == 'managed_artifact'
    assert next(item for item in store.snapshot()['objectives'] if item['id'] == objective['id'])['priority'] == 'P1'
    assert len(edges) == int(dependent)
    if dependent:
        assert edges[0]['predecessor_id'] == predecessor
    assert receipt(store, predecessor) == predecessor_receipt
    for key in before.keys() - {'status', 'objective_id'}:
        assert after[key] == before[key]
    assert store.claim_next()['objective_id'] == objective['id']


@pytest.mark.parametrize('damage', ['missing', 'cycle', 'cross-owner'])
def test_unavailable_or_cyclic_title_lineage_rejects_admission_atomically(tmp_path, damage):
    store = OrganizationStore(tmp_path / 'state.db')
    parent, _ = store.begin_intake('Prepare the report', idempotency_key='original')
    reserve(store, parent)
    store.finish_intake(parent['id'], response(store, parent, routeId=None, question={
        'text': 'Which report?', 'neededFor': 'Subject is missing.', 'checkedEvidenceIds': ['owner-request']}))
    child, _ = store.begin_intake('The research report', idempotency_key='answer', parent_intake_id=parent['id'])
    reserve(store, child)
    store.finish_intake(child['id'], response(store, child))
    # Deliberately corrupt isolated fixture storage; no public path creates these links.
    with sqlite3.connect(store.path) as conn:
        if damage == 'missing':
            conn.execute('UPDATE organization_request_intakes SET parent_intake_id=? WHERE id=?', ('absent', child['id']))
        elif damage == 'cycle':
            conn.execute('UPDATE organization_request_intakes SET parent_intake_id=? WHERE id=?', (child['id'], parent['id']))
        else:
            conn.execute('UPDATE organization_request_intakes SET owner_identity_id=? WHERE id=?', ('another-owner', parent['id']))
    before = store.intake_read(child['id'])
    with pytest.raises(ValueError, match='lineage|not available'):
        store.admit_intake(child['id'])
    assert store.intake_read(child['id']) == before
    assert not store.snapshot()['objectives']
