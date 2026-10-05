from dataclasses import replace
import threading
import time

import pytest

from eidolon_cli.organization_budget import OrganizationModelCost, budget_view
from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_service import OrganizationService
from eidolon_cli.organization_store import OrganizationStore


def setup_store(tmp_path, **settings):
    config = OrganizationSettings(max_output_tokens=512, max_context_tokens=4096, **settings)
    store = OrganizationStore(tmp_path / 'organization' / 'state.db', config)
    objective = store.create_objective('Bounded result', idempotency_key='objective')
    claim = store.claim_next()
    return store, objective, claim


def reserve(store, claim, **kwargs):
    return store.reserve_model_call(claim, provider='local', model='fixture', input_limit=3072,
                                    output_limit=512, **kwargs)


def usage(store, objective):
    with store._connect() as conn:
        return budget_view(conn, objective['id'], store.settings)


def test_stopping_retry_is_rejected_before_mutation_and_same_key_can_resume(tmp_path):
    store, objective, claim = setup_store(tmp_path)
    store.fail(claim, 'Provider setup needs correction', retryable=False)
    service = OrganizationService(store, home=tmp_path)
    assert service.stop()
    with pytest.raises(RuntimeError, match='stopping'):
        service.retry(claim['id'], idempotency_key='resume')
    assert not store.retry_recorded(claim['id'], 'resume')
    assert store.snapshot()['requests'][0]['status'] == 'pending_intervention'
    entered = threading.Event()

    def execute(request, context, cancel):
        entered.set()
        return {'intervention': 'Controlled fixture completion'}

    resumed = OrganizationService(OrganizationStore(store.path), home=tmp_path, executor=execute)
    try:
        assert resumed.retry(claim['id'], idempotency_key='resume')
        assert entered.wait(5)
        assert resumed.retry(claim['id'], idempotency_key='resume') is False
    finally:
        assert resumed.stop()
    assert store.retry_recorded(claim['id'], 'resume')
    assert store.snapshot()['tasks'] == []


def test_unknown_or_interrupted_usage_keeps_tokens_and_call_reservations_on_restart(tmp_path):
    store, objective, claim = setup_store(tmp_path, max_total_tokens=6000, max_model_calls=2)
    first = reserve(store, claim)
    with pytest.raises(ValueError, match='reserved-token budget'):
        reserve(store, claim)
    assert len(store.execution_audit(claim['id'])['modelCalls']) == 1
    assert store.finish(claim, {'intervention': 'Need owner input'})
    reopened = OrganizationStore(store.path)
    assert usage(reopened, objective)['reservedTokens'] == 3584
    assert usage(reopened, objective)['modelCalls'] == 1
    assert reopened.execution_audit(claim['id'])['modelCalls'][0]['id'] == first
    assert reopened.snapshot()['objectives'][0]['usage']['usageComplete'] is False
    assert reopened.retry(claim['id'], idempotency_key='retry')
    retry = reopened.claim_next()
    with pytest.raises(ValueError, match='reserved-token budget'):
        reserve(reopened, retry)
    assert reopened.cancel(objective['id'])
    with pytest.raises(ValueError, match='no longer owned'):
        reserve(reopened, retry)
    assert usage(reopened, objective)['reservedTokens'] == 3584


def test_explicit_exact_route_cost_ceilings_are_conservative_and_never_reset(tmp_path):
    rate = OrganizationModelCost('local', 'fixture', '1', '3')
    store, objective, claim = setup_store(tmp_path, max_cost_usd='0.009216', model_costs=(rate,))
    reserve(store, claim)
    reserve(store, claim)
    assert usage(store, objective)['configuredCostReservedUsd'] == '0.009216000'
    pricing = store.execution_audit(claim['id'])['modelCalls'][0]['pricing']
    assert pricing['inputUsdPerMillion'] == rate.input_usd_per_million
    assert pricing['outputUsdPerMillion'] == rate.output_usd_per_million
    with pytest.raises(ValueError, match='configured-cost budget exhausted'):
        reserve(store, claim)
    assert store.finish(claim, {'intervention': 'No provider usage reported'})
    snapshot = store.snapshot()
    assert snapshot['requests'][0]['allowedResolutions'] == []
    assert snapshot['objectives'][0]['usage']['usageComplete'] is False
    reopened = OrganizationStore(store.path)
    with pytest.raises(ValueError, match='configured-cost budget exhausted'):
        reopened.retry(claim['id'], idempotency_key='repeat')
    assert not reopened.retry_recorded(claim['id'], 'repeat')
    assert usage(reopened, objective)['configuredCostReservedUsd'] == '0.009216000'


def test_cost_policy_cannot_guess_unknown_routes_or_erase_original_ceiling(tmp_path):
    store, objective, claim = setup_store(tmp_path, max_cost_usd='1')
    with pytest.raises(ValueError, match='No configured cost ceiling'):
        reserve(store, claim)
    assert usage(store, objective)['modelCalls'] == 0
    store.fail(claim, 'Configure exact price ceilings', retryable=False)
    lowered = replace(store.settings, max_cost_usd=None)
    reopened = OrganizationStore(store.path, lowered)
    assert reopened.retry(claim['id'], idempotency_key='config')
    retry = reopened.claim_next()
    assert reopened.context(retry)['costBudgetEnabled'] is True
    with pytest.raises(ValueError, match='No configured cost ceiling'):
        reserve(reopened, retry)


def test_deadline_caps_active_timeout_and_fences_late_completion(tmp_path):
    store, objective, claim = setup_store(tmp_path)
    with store._write() as conn:
        conn.execute('UPDATE objective_budgets SET deadline=? WHERE objective_id=?', (time.time() + 2, objective['id']))
    assert 0 < store.context(claim)['timeoutSeconds'] <= 2
    with store._write() as conn:
        conn.execute('UPDATE objective_budgets SET deadline=? WHERE objective_id=?', (time.time() - 1, objective['id']))
    assert not store.finish(claim, {'tasks': [{'title': 'Late', 'description': 'Must not publish', 'type': 'work.draft'}]})
    assert store.snapshot()['tasks'] == []
    assert 'deadline' in store.snapshot()['requests'][0]['reason']
    assert store.snapshot()['requests'][0]['allowedResolutions'] == []
    with pytest.raises(ValueError, match='deadline'):
        store.retry(claim['id'], idempotency_key='expired')


def test_restart_never_treats_legacy_unknown_spend_as_zero(tmp_path):
    rate = OrganizationModelCost('local', 'fixture', '1', '3')
    store, objective, claim = setup_store(tmp_path, max_cost_usd='1', model_costs=(rate,))
    store.finish(claim, {'intervention': 'Legacy provider result'})
    with store._write() as conn:
        conn.execute('DELETE FROM objective_budgets WHERE objective_id=?', (objective['id'],))
    reopened = OrganizationStore(store.path)
    view = usage(reopened, objective)
    assert view['legacyUsageUnknown'] is True
    assert view['configuredCostReservedUsd'] is None
    with pytest.raises(ValueError, match='Prior model usage is unknown'):
        reopened.retry(claim['id'], idempotency_key='legacy')


def test_replanning_keeps_model_call_budget_and_cannot_resume_exhausted_objective(tmp_path):
    store, objective, claim = setup_store(tmp_path, max_model_calls=2)
    reserve(store, claim)
    store.finish(claim, {'intervention': 'Need bounded new plan'})
    assert store.resolve(claim['id'], 'request_replan', 'Try a different plan', idempotency_key='replan')
    plan = store.claim_next()
    reserve(store, plan)
    store.finish(plan, {'intervention': 'No more calls permitted'})
    assert usage(store, objective)['modelCalls'] == 2
    assert store.snapshot()['objectives'][0]['acceptance']['round'] == 1
    assert next(row for row in store.snapshot()['requests'] if row['id'] == plan['id'])['allowedResolutions'] == []
    with pytest.raises(ValueError, match='unavailable'):
        store.resolve(plan['id'], 'amend_scope', 'Smaller scope', required_checks=[], idempotency_key='do-not-reset')


@pytest.mark.parametrize('configuration', [
    {'max_cost_usd': True}, {'max_cost_usd': 'NaN'}, {'max_cost_usd': '-1'},
    {'max_context_tokens': 8000}, {'max_model_calls': 0}, {'max_total_tokens': True},
    {'model_costs': [{'provider': '*', 'model': 'fixture', 'input_usd_per_million': '1', 'output_usd_per_million': '2'}]},
])
def test_budget_configuration_rejects_unknown_or_unbounded_values(configuration):
    with pytest.raises(ValueError):
        OrganizationSettings.from_config({'organization': configuration})


def test_profile_default_loader_and_organization_admission_use_the_same_budget_defaults():
    from eidolon_cli.config import DEFAULT_CONFIG
    expected = OrganizationSettings()
    parsed = OrganizationSettings.from_config(DEFAULT_CONFIG)
    for field in ('max_context_tokens', 'max_model_calls', 'max_total_tokens',
                  'objective_timeout_seconds', 'max_cost_usd', 'model_costs'):
        assert field in DEFAULT_CONFIG['organization']
        assert getattr(parsed, field) == getattr(expected, field)


def test_legacy_unknown_call_usage_parks_only_executed_open_work(tmp_path):
    store, objective, claim = setup_store(tmp_path)
    store.finish(claim, {'intervention': 'Old runtime stage'})
    queued = store.create_objective('Never executed', idempotency_key='queued')
    with store._write() as conn:
        conn.execute('DELETE FROM objective_budgets')
    reopened = OrganizationStore(store.path)
    assert usage(reopened, objective)['legacyUsageUnknown'] is True
    assert usage(reopened, queued)['legacyUsageUnknown'] is False
    with pytest.raises(ValueError, match='Prior model usage is unknown'):
        reopened.retry(claim['id'], idempotency_key='old-usage')
    admitted = reopened.claim_next()
    assert admitted['objective_id'] == queued['id']
    assert reopened.snapshot()['objectives'][1]['status'] == 'needs_input'
