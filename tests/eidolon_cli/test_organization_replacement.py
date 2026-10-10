"""Expired work replacement is a single owner-authorized transaction, never renewal."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import time

import pytest

from eidolon_cli.organization_store import OrganizationStore
from tests.eidolon_cli.test_organization_request_recovery import pending_question


def expired(tmp_path, monkeypatch):
    store, objective, work, question = pending_question(tmp_path, max_open_objectives=1)
    instant = time.time() + store.settings.objective_timeout_seconds + 1
    monkeypatch.setattr(time, 'time', lambda: instant)
    return store, objective, work, question


def submit(store, draft, **overrides):
    return store.replace_objective(draft['sourceId'], draft['sourceVersion'],
        overrides.get('title', draft['title']), overrides.get('description', draft['description']),
        overrides.get('criteria', draft['acceptanceCriteria']), confirmed=overrides.get('confirmed', True))


def test_replacement_race_replay_and_restart_preserve_history_and_finite_authority(tmp_path, monkeypatch):
    store, original, work, question = expired(tmp_path, monkeypatch)
    before = store.snapshot()
    draft = store.preview_replacement(original['id'])
    settings = store.settings
    # A read/cancelled review cannot consume a slot or alter the old objective.
    with pytest.raises(ValueError, match='confirmation'):
        submit(store, draft, confirmed=False)
    assert store.snapshot() == before
    with pytest.raises(ValueError, match='open-objective limit'):
        store.create_objective('Unrelated objective', idempotency_key='blocked')
    peers = [OrganizationStore(store.path), OrganizationStore(store.path)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda peer: submit(peer, draft), peers))
    assert results[0]['id'] == results[1]['id']
    reopened = OrganizationStore(store.path)
    replacement = submit(reopened, draft)
    assert replacement['id'] == results[0]['id']
    assert replacement['replacesObjectiveId'] == original['id']
    after = reopened.snapshot()
    assert len(after['objectives']) == 2
    old = next(row for row in after['objectives'] if row['id'] == original['id'])
    assert old['status'] == 'cancelled'
    assert old['replacementObjectiveId'] == replacement['id']
    assert old['usage'] == before['objectives'][0]['usage']
    assert replacement['usage']['modelCalls'] == replacement['usage']['reservedTokens'] == 0
    assert replacement['usage']['modelCallLimit'] == draft['allowance']['modelCalls']
    assert replacement['usage']['deadlineTimestamp'] == time.time() + draft['allowance']['durationSeconds']
    assert replacement['executiveId'] == old['executiveId'] and replacement['managerId'] == old['managerId']
    assert [(a['id'], a['identityId']) for a in after['agents']] == [(a['id'], a['identityId']) for a in before['agents']]
    assert reopened.settings == settings
    assert not reopened.finish(work, {'summary': 'Late result'})
    with pytest.raises(ValueError, match='not awaiting|cancelled|deadline'):
        reopened.respond(question['id'], 'Late answer', idempotency_key='late')
    with pytest.raises(ValueError, match='different input'):
        submit(reopened, draft, title='Changed intent')
    with reopened._write() as conn:
        assert conn.execute('SELECT count(*) FROM objective_replacements').fetchone()[0] == 1
    claim = reopened.claim_next()
    assert claim['objective_id'] == replacement['id'] and claim['type'] == 'request.decompose'


@pytest.mark.parametrize('change', ['unconfirmed', 'scope', 'policy', 'cancel', 'history_capacity', 'invalid_criteria'])
def test_replacement_rejects_stale_or_unauthorized_drafts_without_partial_cancellation(tmp_path, monkeypatch, change):
    store, original, _, _ = expired(tmp_path, monkeypatch)
    draft = store.preview_replacement(original['id'])
    kwargs = {}
    if change == 'unconfirmed':
        kwargs['confirmed'] = 'true'
    elif change == 'scope':
        with store._write() as conn:
            conn.execute('UPDATE objective_control SET amended_scope=? WHERE objective_id=?', ('Changed elsewhere', original['id']))
    elif change == 'policy':
        store.reload_configuration(replace(store.settings, max_model_calls=store.settings.max_model_calls + 1))
    elif change == 'cancel':
        store.cancel(original['id'])
    elif change == 'history_capacity':
        monkeypatch.setattr('eidolon_cli.organization_history.MAX_CURRENT_OBJECTIVES', 1)
    else:
        kwargs['criteria'] = ['']
    before = store.snapshot()
    with pytest.raises(ValueError):
        submit(store, draft, **kwargs)
    assert store.snapshot() == before
    with store._connect() as conn:
        assert conn.execute('SELECT count(*) FROM objective_replacements').fetchone()[0] == 0


@pytest.mark.parametrize('cause', ['deadline', 'model_calls'])
def test_replacement_waits_for_cross_process_execution(tmp_path, monkeypatch, cause):
    import multiprocessing
    from eidolon_cli.organization_service import OrganizationService
    from tests.eidolon_cli.test_organization_request_recovery import _hold_peer_recovery_lock

    if cause == 'deadline':
        store, original, work, _ = expired(tmp_path, monkeypatch)
    else:
        store, original, work = exhausted(tmp_path, cause)
        store.fail(work, 'Stopped ledger claim; peer still holds process lock', retryable=False)
    draft = store.preview_replacement(original['id'])
    service = OrganizationService(store, home=tmp_path)
    starts = []
    monkeypatch.setattr(service, 'start', lambda: starts.append(True))
    context = multiprocessing.get_context('spawn')
    ready, release = context.Queue(), context.Event()
    process = context.Process(target=_hold_peer_recovery_lock, args=(str(tmp_path / 'organization' / 'execution-locks'), work['id'], ready, release))
    process.start()
    try:
        assert ready.get(timeout=20)
        with pytest.raises(ValueError, match='another runtime'):
            service.replace_objective(draft['sourceId'], draft['sourceVersion'], draft['title'], draft['description'], draft['acceptanceCriteria'], confirmed=True)
        assert starts == []
        assert len(store.snapshot()['objectives']) == 1
    finally:
        release.set()
        process.join(timeout=10)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        ready.close()
    assert process.exitcode == 0
    result = service.replace_objective(draft['sourceId'], draft['sourceVersion'], draft['title'], draft['description'], draft['acceptanceCriteria'], confirmed=True)
    assert result['replacesObjectiveId'] == original['id']
    assert starts == [True]


@pytest.mark.parametrize('changed_grant', [False, True])
def test_replacement_preserves_selected_projects_checks_and_original_budget_ceiling(tmp_path, monkeypatch, changed_grant):
    from tests.eidolon_cli.test_organization_multi_project_execution import configured
    store, original = configured(tmp_path, selected=('alpha',))
    settings = store.settings
    now = time.time() + settings.objective_timeout_seconds + 1
    monkeypatch.setattr(time, 'time', lambda: now)
    grants = settings.project_grants
    if changed_grant:
        grants = (replace(grants[0], files=('root0/different.py',)), *grants[1:])
    store.reload_configuration(replace(settings, max_model_calls=settings.max_model_calls + 1, project_grants=grants))
    before = store.snapshot()
    if changed_grant:
        with pytest.raises(ValueError, match='Project authority changed'):
            store.preview_replacement(original['id'])
        assert store.snapshot() == before
        return
    draft = store.preview_replacement(original['id'])
    result = submit(store, draft)
    assert result['projects'] == original['projects']
    assert result['requiredChecks'] == ['project_tests']
    assert result['usage']['modelCallLimit'] == settings.max_model_calls
    assert draft['projectIds'] == ['alpha']
    assert store.settings.project_grants == settings.project_grants
    assert store.settings.tool_grants == settings.tool_grants


def exhausted(tmp_path, dimension):
    from eidolon_cli.organization_config import OrganizationSettings
    from eidolon_cli.organization_budget import OrganizationModelCost
    from tests.organization_package_helpers import decompose
    limits = {'model_calls': {'max_model_calls': 1}, 'tokens': {'max_total_tokens': 4096},
              'cost': {'max_cost_usd': '0.004096', 'model_costs': (OrganizationModelCost('local', 'fixture', '1', '1'),)},
              'stages': {'max_stages': 4}}[dimension]
    store = OrganizationStore(tmp_path / 'state.db', OrganizationSettings(max_open_objectives=1, max_context_tokens=4096, max_output_tokens=512, **limits))
    objective = store.create_objective('Recover bounded work', idempotency_key='original')
    claim = store.claim_next()
    if dimension == 'stages':
        decompose(store, claim)
        plan = store.claim_next()
        store.finish(plan, {'tasks': [{'title': 'Draft', 'description': 'Use supplied facts', 'type': 'work.draft'}]})
        work = store.claim_next()
        store.finish(work, {'summary': 'Draft retained', 'deliverable': 'Evidence for review'})
        claim = store.claim_next()
        assert claim['type'] == 'request.review'
    else:
        store.reserve_model_call(claim, provider='local', model='fixture', input_limit=3584, output_limit=512)
    return store, objective, claim


@pytest.mark.parametrize('dimension', ['model_calls', 'tokens', 'cost', 'stages'])
def test_exhausted_replacement_waits_for_execution_and_preserves_charges_across_race_restart(tmp_path, dimension):
    store, original, claim = exhausted(tmp_path, dimension)
    assert not store.snapshot()['objectives'][0]['replacementEligible']
    with pytest.raises(ValueError, match='still stopping'):
        store.preview_replacement(original['id'])
    store.fail(claim, 'Bounded execution stopped', retryable=False)
    before = store.snapshot()
    assert before['objectives'][0]['replacementEligible']
    assert all(not row['allowedResolutions'] for row in before['requests'])
    draft = store.preview_replacement(original['id'])
    assert draft['reasonCodes'] == [dimension]
    assert draft['sourceUsage'] == before['objectives'][0]['usage']
    assert draft['sourceUsage']['deadlineTimestamp'] > time.time()
    with pytest.raises(ValueError, match='confirmation'):
        submit(store, draft, confirmed=False)
    assert store.snapshot() == before
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit(OrganizationStore(store.path), draft), range(2)))
    reopened = OrganizationStore(store.path)
    result = submit(reopened, draft)
    assert results[0]['id'] == results[1]['id'] == result['id']
    old = next(row for row in reopened.snapshot()['objectives'] if row['id'] == original['id'])
    assert old['usage'] == draft['sourceUsage']
    assert old['replacementObjectiveId'] == result['id'] and old['status'] == 'cancelled'
    assert result['replacesObjectiveId'] == original['id']
    assert result['usage']['modelCalls'] == result['usage']['reservedTokens'] == result['usage']['stages'] == 0
    assert not result['replacementEligible']
    assert not reopened.finish(claim, {'summary': 'Late result'})
    assert reopened.settings == store.settings
    assert reopened.claim_next()['objective_id'] == result['id']


def test_nonexhausted_next_call_failure_and_existing_typed_recovery_do_not_offer_replacement(tmp_path):
    from tests.eidolon_cli.test_organization_completion_budget import setup_store, reserve
    store, original, claim = setup_store(tmp_path / 'headroom', max_total_tokens=6000, max_replans=0)
    reserve(store, claim)
    with pytest.raises(ValueError, match='next model call'):
        reserve(store, claim)
    store.fail(claim, 'Next reservation could be reduced by configuration', retryable=False)
    assert store.snapshot()['requests'][0]['allowedResolutions']
    assert not store.snapshot()['objectives'][0]['replacementEligible']
    with pytest.raises(ValueError, match='expired or exhausted'):
        store.preview_replacement(original['id'])
    # A linked question can still be answered at the stage ceiling. Do not
    # replace the original while this existing recovery is available.
    store, original, _, request = pending_question(tmp_path / 'question', max_stages=4)
    store.respond(request['id'], 'Use the supplied fact', idempotency_key='first-answer')
    resumed = store.claim_next()
    assert resumed['type'] == 'work.analyze'
    store.finish(resumed, {'requests': [{'type': 'request.question', 'requestedOutcome': 'Confirm the remaining fact.'}]})
    assert store.claim_next() is None
    request = next(row for row in store.snapshot()['requests'] if row['status'] == 'pending_intervention' and row['id'] != request['id'])
    assert store.snapshot()['objectives'][0]['usage']['stages'] == 4
    assert 'answer_request' in {item['action'] for item in request['allowedResolutions']}
    assert not store.snapshot()['objectives'][0]['replacementEligible']
    with pytest.raises(ValueError, match='Existing owner recovery'):
        store.preview_replacement(original['id'])


def test_unknown_cost_is_not_claimed_as_finite_exhaustion_and_stale_exhausted_policy_is_rejected(tmp_path):
    from tests.eidolon_cli.test_organization_completion_budget import setup_store
    store, original, claim = setup_store(tmp_path / 'unknown', max_cost_usd='1')
    with pytest.raises(ValueError, match='No configured cost ceiling'):
        store.reserve_model_call(claim, provider='local', model='unpriced', input_limit=3584, output_limit=512)
    store.fail(claim, 'Configure exact route ceilings', retryable=False)
    assert not store.snapshot()['objectives'][0]['replacementEligible']
    with pytest.raises(ValueError, match='expired or exhausted'):
        store.preview_replacement(original['id'])
    store, original, claim = setup_store(tmp_path / 'unpriced-history')
    store.reserve_model_call(claim, provider='local', model='unpriced', input_limit=3584, output_limit=512)
    store.fail(claim, 'Unpriced usage retained', retryable=False)
    store.reload_configuration(replace(store.settings, max_cost_usd='1'))
    assert store.snapshot()['objectives'][0]['usage']['configuredCostReservedUsd'] is None
    assert not store.snapshot()['objectives'][0]['replacementEligible']
    with pytest.raises(ValueError, match='expired or exhausted'):
        store.preview_replacement(original['id'])
    store, original, claim = exhausted(tmp_path / 'stale', 'model_calls')
    store.fail(claim, 'Stop', retryable=False)
    draft = store.preview_replacement(original['id'])
    store.reload_configuration(replace(store.settings, max_total_tokens=store.settings.max_total_tokens - 1))
    before = store.snapshot()
    with pytest.raises(ValueError, match='changed'):
        submit(store, draft)
    assert store.snapshot() == before
