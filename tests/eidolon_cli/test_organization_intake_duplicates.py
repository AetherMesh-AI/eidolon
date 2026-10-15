"""Exact-repeat links preserve prior scope, intent, owner and usage."""
import json
import pytest
from eidolon_cli.organization_store import OrganizationStore
from eidolon_cli.organization_intake_duplicates import objective_scope, contract_view


@pytest.fixture
def store(tmp_path):
    return OrganizationStore(tmp_path / 'organization' / 'state.db')


def submit(store, key, *, text='Write a status report', priority=None, separate=False, **changes):
    intake, _ = store.begin_intake(text, idempotency_key=key, owner_priority=priority, force_separate=separate)
    store.reserve_intake_call(intake['id'], provider='test', model='small', input_limit=1000, output_limit=1000)
    proposal = dict(snapshotId=intake['snapshotId'], routeId='manager', projectIds=[],
        deliveryMode='managed_artifact', priority='P3', rationale='Ordinary report.', evidenceIds=['owner-request'],
        acceptanceCriteria=['Deliver a report.'], assumptions=[], duplicateCandidateIds=[], dependencyCandidateIds=[], question=None)
    result = store.finish_intake(intake['id'], json.dumps({**proposal, **changes}), usage={'inputTokens': 30, 'outputTokens': 20})
    return store.admit_intake(intake['id']) if result['status'] == 'proposed' else result


def test_exact_repeat_links_without_modifying_original_or_charge(store):
    first = submit(store, 'first')
    with store._connect() as conn:
        before = objective_scope(conn, first['objectiveId'])
    repeat = submit(store, 'repeat', duplicateCandidateIds=[first['objectiveId']])
    assert repeat['status'] == 'linked' and repeat['objectiveId'] == first['objectiveId']
    assert repeat['usage']['inputTokens'] == 30 and repeat['usage']['reservedTokens'] == 2000
    assert len(store.snapshot()['objectives']) == 1
    with store._connect() as conn:
        assert objective_scope(conn, first['objectiveId']) == before
        assert conn.execute('SELECT count(*) FROM organization_intake_duplicate_links').fetchone()[0] == 1
        assert conn.execute('SELECT sum(input_limit+output_limit) FROM organization_request_intakes').fetchone()[0] == 4000
    reopened = OrganizationStore(store.path)
    assert reopened.admit_intake(repeat['id']) == repeat
    assert reopened.recent_intakes()[0]['objectiveId'] == first['objectiveId']


def test_force_separate_is_idempotent_intent_and_preserves_two_objectives(store):
    first = submit(store, 'first')
    second = submit(store, 'second', separate=True)
    assert second['status'] == 'admitted' and second['objectiveId'] != first['objectiveId']
    with pytest.raises(ValueError, match='another request'):
        store.begin_intake('Write a status report', idempotency_key='second', force_separate=False)
    # Having multiple deliberately separate matching originals is ambiguous.
    third = submit(store, 'third')
    assert third['status'] == 'admitted'
    assert len(store.snapshot()['objectives']) == 3


@pytest.mark.parametrize('changes', [dict(text='Write  a status report'), dict(priority='P1'),
                                    dict(acceptanceCriteria=['Deliver two reports.'])])
def test_distinct_owner_intent_never_links(store, changes):
    first = submit(store, 'first')
    second = submit(store, 'second', **changes)
    assert second['status'] == 'admitted' and second['objectiveId'] != first['objectiveId']


@pytest.mark.parametrize('sql', ["UPDATE objective_control SET criteria='[\"Changed criteria\"]'",
    "UPDATE objective_control SET amended_scope='Different scope'", "UPDATE objectives SET priority=5",
    "UPDATE objective_control SET required_checks='[\"managed_validation\"]'",
    "UPDATE objective_control SET status='accepted'", "UPDATE objectives SET cancelled=1"])
def test_changed_or_completed_original_is_not_an_exact_duplicate(store, sql):
    first = submit(store, 'first')
    with store._write() as conn:
        conn.execute(sql)
    second = submit(store, 'second')
    assert second['status'] == 'admitted' and second['objectiveId'] != first['objectiveId']


def test_semantic_candidate_stays_review_required(store):
    first = submit(store, 'first')
    second = submit(store, 'second', text='Summarize project status', duplicateCandidateIds=[first['objectiveId']])
    assert second['status'] == 'needs_review'
    assert len(store.snapshot()['objectives']) == 1


def test_dependencies_never_get_hidden_by_exact_duplicate(store):
    first = submit(store, 'first')
    second = submit(store, 'second', dependencyCandidateIds=[first['objectiveId']])
    assert second['status'] == 'needs_review'


def test_same_text_typed_contract_change_invalidates_snapshot_and_baseline(store):
    first = submit(store, 'first')
    claim = store.claim_next()
    store.fail(claim, 'Needs scope input')
    with store._connect() as conn:
        before = contract_view(conn, first['objectiveId'])
    pending, _ = store.begin_intake('Write a status report', idempotency_key='pending')
    criterion = before['criteria'][0]
    store.resolve(claim['id'], 'amend_scope', 'Keep the same report text', idempotency_key='typed-amend',
        acceptance_criteria=[{'id': criterion['id'], 'text': criterion['text'], 'kind': 'human_judgment'}])
    with store._connect() as conn:
        after = contract_view(conn, first['objectiveId'])
    assert after['revision'] == before['revision'] + 1
    assert after['sha256'] != before['sha256']
    assert after['criteria'][0]['text'] == criterion['text']
    with pytest.raises(ValueError, match='context changed'):
        store.reserve_intake_call(pending['id'], provider='test', model='small', input_limit=1000, output_limit=1000)
    second = submit(store, 'second')
    assert second['status'] == 'admitted' and second['objectiveId'] != first['objectiveId']


def test_typed_tables_without_reader_fail_closed(store, monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, 'eidolon_cli.organization_outcome_contracts', None)
    with store._write() as conn:
        with pytest.raises(ValueError, match='reader is unavailable'):
            contract_view(conn, 'unknown')


def test_malformed_typed_tables_with_reader_fail_closed(store):
    with store._write() as conn:
        conn.execute('DROP TABLE objective_human_judgments')
        with pytest.raises(ValueError, match='storage is incomplete'):
            contract_view(conn, 'unknown')


def test_invalid_force_separate_fails_before_admission(store):
    with pytest.raises(ValueError, match='boolean'):
        store.begin_intake('Write report', idempotency_key='bad', force_separate=1)


def test_force_separate_ignores_only_duplicate_suggestion(store):
    first = submit(store, 'first')
    second = submit(store, 'second', separate=True, duplicateCandidateIds=[first['objectiveId']])
    assert second['status'] == 'admitted' and second['objectiveId'] != first['objectiveId']
    third = submit(store, 'third', separate=True, dependencyCandidateIds=[first['objectiveId']])
    assert third['status'] == 'needs_review'


def test_source_request_needs_current_source_pin_before_link(store):
    from eidolon_cli.organization_intake_duplicates import exact_duplicate
    first = submit(store, 'first')
    with store._connect() as conn:
        row = store._intake_row(conn, first['id'])
        proposal = json.loads(row['proposal_json'])
        proposal['deliveryMode'] = 'source_project'
        assert exact_duplicate(store, conn, row, proposal) is None


def test_clarification_preserves_force_separate(store):
    original = submit(store, 'question', separate=True, question={
        'text': 'Which period?', 'neededFor': 'The reporting period is absent.', 'checkedEvidenceIds': ['owner-request']})
    answer, _ = store.begin_intake('Last week', idempotency_key='answer', parent_intake_id=original['id'])
    with store._connect() as conn:
        assert conn.execute('SELECT force_separate FROM organization_intake_options WHERE intake_id=?', (answer['id'],)).fetchone()[0] == 1


def test_baseline_without_original_owner_does_not_link(store):
    first = submit(store, 'first')
    with store._write() as conn:
        conn.execute("UPDATE agent_identity SET identity_id='rotated-owner' WHERE agent_id='owner'")
    second = submit(store, 'second')
    assert second['status'] == 'admitted' and second['objectiveId'] != first['objectiveId']


def test_duplicate_decision_receipts_are_immutable(store):
    import sqlite3
    first = submit(store, 'first')
    repeat = submit(store, 'repeat')
    assert repeat['status'] == 'linked'
    for table in ('organization_intake_options', 'organization_intake_admission_scopes', 'organization_intake_duplicate_links'):
        with store._write() as conn:
            with pytest.raises(sqlite3.IntegrityError, match='immutable'):
                conn.execute('DELETE FROM ' + table)
    assert store.intake_read(first['id'])['status'] == 'admitted'


def test_changed_assumptions_require_review_instead_of_exact_link(store):
    first = submit(store, 'first', assumptions=['Use last week.'])
    second = submit(store, 'second', assumptions=['Use this week.'], duplicateCandidateIds=[first['objectiveId']])
    assert second['status'] == 'needs_review'
    assert len(store.snapshot()['objectives']) == 1


def test_passive_linked_history_retains_objective_receipt_without_service(store):
    from eidolon_cli.organization_intake_observation import read_intakes
    first = submit(store, 'original-history')
    linked = submit(store, 'linked-history')
    assert linked['status'] == 'linked'
    home = store.path.parent.parent
    before = store.path.read_bytes()
    assert read_intakes(home, intake_id=linked['id']) == linked
    assert read_intakes(home)[0]['objectiveId'] == first['objectiveId']
    assert store.path.read_bytes() == before
