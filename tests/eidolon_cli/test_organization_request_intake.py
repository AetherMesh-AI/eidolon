"""Actual profile-ledger tests; no provider calls or activated project access."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json

import pytest

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_store import OrganizationStore


@pytest.fixture
def store(tmp_path):
    return OrganizationStore(tmp_path / 'organization' / 'state.db')


def response(store, intake, **changes):
    value = dict(snapshotId=intake['snapshotId'], routeId='manager', projectIds=[],
                 deliveryMode='managed_artifact', priority='P3', rationale='Ordinary document request.',
                 evidenceIds=['owner-request'], acceptanceCriteria=['Deliver the requested report.'],
                 assumptions=[], duplicateCandidateIds=[], dependencyCandidateIds=[], question=None)
    return json.dumps({**value, **changes})


def reserve(store, intake):
    store.reserve_intake_call(intake['id'], provider='test', model='small', input_limit=1000, output_limit=1000)


def test_proposal_admits_exactly_one_objective_and_survives_restart(store):
    intake, created = store.begin_intake('Prepare a project status report', idempotency_key='request-1')
    assert created
    reserve(store, intake)
    finished = store.finish_intake(intake['id'], response(store, intake), usage={'inputTokens': 123, 'outputTokens': 45})
    assert finished['usage']['inputTokens'] == 123
    result = store.admit_intake(intake['id'])
    restarted = OrganizationStore(store.path)
    repeated = restarted.admit_intake(intake['id'])
    assert result == repeated
    again, created = restarted.begin_intake('Prepare a project status report', idempotency_key='request-1')
    assert not created and again['objectiveId'] == result['objectiveId']
    assert len(restarted.snapshot()['objectives']) == 1


def test_concurrent_duplicate_intake_never_creates_two_records(store):
    def submit(_):
        return OrganizationStore(store.path).begin_intake('Write report', idempotency_key='same')
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(submit, range(4)))
    assert len({row['id'] for row, _ in results}) == 1
    assert sum(created for _, created in results) == 1


def test_single_send_survives_transport_uncertainty_and_restart(store):
    intake, _ = store.begin_intake('Write report', idempotency_key='first')
    reserve(store, intake)
    store.fail_intake(intake['id'], 'Provider response unknown')
    restarted = OrganizationStore(store.path)
    with pytest.raises(ValueError, match='exactly one'):
        reserve(restarted, intake)
    assert restarted.intake_read(intake['id'])['usage']['reservedTokens'] == 2000
    assert restarted.intake_read(intake['id'])['status'] == 'uncertain'


def test_known_bad_response_keeps_charge(store):
    intake, _ = store.begin_intake('Write report', idempotency_key='first')
    reserve(store, intake)
    with pytest.raises(ValueError):
        store.finish_intake(intake['id'], '{}')
    result = store.fail_intake(intake['id'], 'Malformed response', known_response=True)
    assert result['status'] == 'failed' and result['usage']['reservedTokens'] == 2000


def test_stale_backlog_rejects_dispatch_and_admission(store):
    intake, _ = store.begin_intake('Write report', idempotency_key='first')
    reserve(store, intake)
    store.finish_intake(intake['id'], response(store, intake))
    store.create_objective('Urgent new work', idempotency_key='new')
    with pytest.raises(ValueError, match='context changed'):
        store.admit_intake(intake['id'])
    second, _ = store.begin_intake('Another report', idempotency_key='second')
    store.create_objective('Other work', idempotency_key='other')
    with pytest.raises(ValueError, match='context changed'):
        reserve(store, second)


def test_essential_question_blocks_only_this_intake(store):
    intake, _ = store.begin_intake('Write it', idempotency_key='ambiguous')
    reserve(store, intake)
    result = store.finish_intake(intake['id'], response(store, intake, routeId=None,
        question={'text': 'Which report?', 'neededFor': 'The intended subject is not available.', 'checkedEvidenceIds': ['owner-request']}))
    assert result['status'] == 'needs_answer'
    with pytest.raises(ValueError, match='answer or review'):
        store.admit_intake(intake['id'])
    objective = store.create_objective('Independent work', idempotency_key='independent')
    assert store.claim_next()['objective_id'] == objective['id']


def test_owner_override_is_admitted(store):
    intake, _ = store.begin_intake('Write report', idempotency_key='override', owner_priority='P1')
    reserve(store, intake)
    store.finish_intake(intake['id'], response(store, intake, priority='P4'))
    store.admit_intake(intake['id'])
    assert store.snapshot()['objectives'][0]['priority'] == 'P1'


def test_source_change_without_recipe_not_downgraded(store):
    intake, _ = store.begin_intake('Fix startup', idempotency_key='fix')
    reserve(store, intake)
    store.finish_intake(intake['id'], response(store, intake, deliveryMode='source_project'))
    with pytest.raises(ValueError, match='not downgraded'):
        store.admit_intake(intake['id'])
    assert not store.snapshot()['objectives']


def test_allowance_is_finite_across_intakes(tmp_path):
    store = OrganizationStore(tmp_path / 'organization' / 'state.db', replace(OrganizationSettings(), max_model_calls=1))
    first, _ = store.begin_intake('One', idempotency_key='one')
    reserve(store, first)
    store.fail_intake(first['id'], 'Unknown send')
    second, _ = store.begin_intake('Two', idempotency_key='two')
    with pytest.raises(ValueError, match='allowance exhausted'):
        reserve(store, second)
    assert not store.intake_read(second['id'])['dispatched']


def test_cross_profile_intake_is_unavailable(store, tmp_path):
    intake, _ = store.begin_intake('One', idempotency_key='one')
    other = OrganizationStore(tmp_path / 'other' / 'organization' / 'state.db')
    with pytest.raises(ValueError, match='not available'):
        other.intake_read(intake['id'])


def test_response_without_reservation_is_rejected(store):
    intake, _ = store.begin_intake('One', idempotency_key='one')
    with pytest.raises(ValueError, match='no reserved'):
        store.finish_intake(intake['id'], response(store, intake))


@pytest.mark.parametrize('stage', ['dispatch', 'response', 'admission'])
def test_policy_pause_fences_every_intake_effect(store, stage):
    intake, _ = store.begin_intake('Write report', idempotency_key='paused')
    if stage != 'dispatch':
        reserve(store, intake)
    if stage == 'admission':
        store.finish_intake(intake['id'], response(store, intake))
    store.pause_configuration()
    with pytest.raises(ValueError, match='grants or routing changed'):
        if stage == 'dispatch':
            reserve(store, intake)
        elif stage == 'response':
            store.finish_intake(intake['id'], response(store, intake))
        else:
            store.admit_intake(intake['id'])
    assert not store.snapshot()['objectives']
    assert store.intake_read(intake['id'])['dispatched'] is (stage != 'dispatch')


@pytest.mark.parametrize('price,limit,allowed', [(None, '1', False), ('1', '0.001', False), ('1', '0.003', True)])
def test_exact_model_price_and_configured_dollar_ceiling(tmp_path, price, limit, allowed):
    from eidolon_cli.organization_budget import OrganizationModelCost
    rates = () if price is None else (OrganizationModelCost('test', 'small', price, price),)
    settings = replace(OrganizationSettings(), max_cost_usd=limit, model_costs=rates)
    store = OrganizationStore(tmp_path / 'organization' / 'state.db', settings)
    intake, _ = store.begin_intake('Report', idempotency_key='cost')
    if allowed:
        reserve(store, intake)
        assert store.intake_read(intake['id'])['usage']['reservedCostUsd'] == '0.002000000'
        store.fail_intake(intake['id'], 'Unknown provider outcome')
        second, _ = store.begin_intake('Another report', idempotency_key='second')
        with pytest.raises(ValueError, match='cost limit exhausted'):
            reserve(store, second)
        assert not store.intake_read(second['id'])['dispatched']
    else:
        with pytest.raises(ValueError, match='prices|cost limit'):
            reserve(store, intake)
        assert not store.intake_read(intake['id'])['dispatched']


def test_question_remains_answerable_when_candidate_links_also_need_review(store):
    existing = store.create_objective('Existing report', idempotency_key='existing')
    intake, _ = store.begin_intake('Update it', idempotency_key='ambiguous-link')
    reserve(store, intake)
    result = store.finish_intake(intake['id'], response(store, intake, duplicateCandidateIds=[existing['id']],
        question={'text': 'Replace the existing report or add a new one?', 'neededFor': 'Duplicate intent is unresolved',
                  'checkedEvidenceIds': ['owner-request']}))
    assert result['status'] == 'needs_answer'
    answer, created = store.begin_intake('Add a new one', idempotency_key='answer-link', parent_intake_id=intake['id'])
    assert created and answer['parentIntakeId'] == intake['id']


def test_configured_source_project_admits_without_expanding_scope(tmp_path):
    root = tmp_path / 'source'
    root.mkdir()
    (root / 'app.py').write_text('print("hello")\n', encoding='utf-8')
    settings = OrganizationSettings.from_config({'organization': {
        'capabilities': ['work.edit', 'work.inspect'], 'tool_grants': ['read_file', 'patch'],
        'read_roots': [str(root)],
        'project_grants': [{'id': 'source-tests', 'files': ['root0/app.py'], 'execution': {'root': 'root0'}}],
        'projects': [{'id': 'eidolon', 'root': 'root0', 'recipe': 'source-tests', 'team': 'general'}]}})
    store = OrganizationStore(tmp_path / 'organization' / 'state.db', settings)
    intake, _ = store.begin_intake('Fix Eidolon startup', idempotency_key='source')
    prompt = store.intake_prompt(intake['id'])
    assert prompt['organization']['routes'][0]['project_ids'] == ['eidolon']
    assert prompt['organization']['projects'] == [
        {'id': 'eidolon', 'root': 'root0', 'recipe': 'source-tests', 'team': 'general'}]
    wire = json.dumps(prompt)
    assert str(root) not in wire
    assert 'readRoot' not in wire and 'recipeSha256' not in wire and 'policyFingerprint' not in wire
    reserve(store, intake)
    store.finish_intake(intake['id'], response(store, intake, deliveryMode='source_project',
        projectIds=['eidolon'], priority='P2', rationale='Reported startup failure needs investigation.'))
    admitted = store.admit_intake(intake['id'])
    objective = next(item for item in store.snapshot()['objectives'] if item['id'] == admitted['objectiveId'])
    assert objective['deliveryMode'] == 'source_project'
    assert [project['id'] for project in objective['projects']] == ['eidolon']
    assert store.settings.read_roots == settings.read_roots


def test_recent_intakes_are_bounded_and_bound_to_current_owner(store):
    ids = []
    for index in range(23):
        row, _ = store.begin_intake(f'Request {index}', idempotency_key=f'recent-{index}')
        ids.append(row['id'])
        store.fail_intake(row['id'], 'Stopped before dispatch')
    recent = store.recent_intakes()
    assert len(recent) == 20 and recent[0]['id'] == ids[-1] and recent[-1]['id'] == ids[3]
    with store._write() as conn:
        conn.execute("UPDATE agent_identity SET identity_id='different-owner' WHERE agent_id='owner'")
    assert store.recent_intakes() == []
