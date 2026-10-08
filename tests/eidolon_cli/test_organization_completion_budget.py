from tests.organization_package_helpers import claim_after_decomposition
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
    plan = claim_after_decomposition(store)
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


def test_project_budget_exhaustion_blocks_staff_activation_before_inspection_and_survives_restart(tmp_path):
    source = (tmp_path / 'source').resolve()
    source.mkdir()
    original = {'app.py': 'def add(left, right):\n    return left - right\n',
                'test_app.py': 'import unittest\nfrom app import add\n'}
    for name, content in original.items():
        (source / name).write_bytes(content.encode())
    grants = ['read_file', 'patch', 'run_tests', 'integrate_source']
    settings = OrganizationSettings.from_config({'organization': {
        'max_workers': 1, 'max_inflight': 1, 'max_model_calls': 1,
        'max_context_tokens': 65536, 'max_output_tokens': 2048,
        'capabilities': ['work.inspect', 'work.edit'], 'tool_grants': grants,
        'read_roots': [str(source)],
        'project_grants': [{'id': 'addition', 'files': ['root0/app.py', 'root0/test_app.py'],
                            'execution': {'recipe': 'python_unittest', 'timeout_seconds': 10}}],
        'roster': [{'id': 'editor', 'name': 'Project editor', 'team': 'general',
                    'capabilities': ['work.inspect', 'work.edit'], 'tool_grants': grants}]}})
    store = OrganizationStore(tmp_path / 'organization' / 'state.db', settings)
    objective = store.create_objective('Fix and verify addition', idempotency_key='budgeted-project',
        required_checks=['project_tests', 'managed_validation', 'source_integration'])
    plan = claim_after_decomposition(store)
    assert plan['type'] == 'request.plan'
    call_id = store.reserve_model_call(plan, provider='local', model='fixture', input_limit=3072, output_limit=512)
    assert store.finish(plan, {'workers': 1, 'tasks': [
        {'title': 'Inspect addition and its tests', 'type': 'work.inspect', 'team': 'general',
         'agentId': 'editor', 'description': 'Inspect root0/app.py and root0/test_app.py.', 'dependsOn': []},
        {'title': 'Fix addition without weakening tests', 'type': 'work.edit', 'team': 'general',
         'agentId': 'editor', 'description': 'Correct subtraction to addition.', 'dependsOn': [0]}]})
    reason = 'Objective model-call budget exhausted. Automatic execution has stopped.'

    def assert_stopped(current):
        # One scheduler pass parks the control request; the next observes its
        # downstream inactive worker. Neither pass may admit execution.
        assert current.claim_next() is None
        assert current.claim_next() is None
        snapshot = current.snapshot()
        requests = {row['type']: row for row in snapshot['requests']}
        assert requests['request.plan']['status'] == 'completed'
        assert requests['request.hire']['status'] == 'pending_intervention'
        assert reason in requests['request.hire']['reason']
        assert requests['request.hire']['attempts'] == 0
        assert requests['work.inspect']['status'] == 'pending_intervention'
        assert 'not activated; request.hire is required' in requests['work.inspect']['reason']
        assert requests['work.inspect']['attempts'] == requests['work.edit']['attempts'] == 0
        assert requests['work.edit']['status'] == 'queued'
        assert snapshot['objectives'][0]['projectExecution'] is None
        assert snapshot['objectives'][0]['status'] == 'needs_input'
        assert usage(current, objective)['modelCalls'] == usage(current, objective)['modelCallLimit'] == 1
        assert [row['id'] for row in current.execution_audit(plan['id'])['modelCalls']] == [call_id]
        with current._connect() as conn:
            assert conn.execute("SELECT active FROM staff_state WHERE agent_id='editor'").fetchone()[0] == 0
            for table in ('tool_receipts', 'edit_proposals', 'project_run_starts', 'project_source_receipts'):
                assert conn.execute('SELECT count(*) FROM ' + table).fetchone()[0] == 0
        assert {name: (source / name).read_bytes().decode() for name in original} == original
        return requests['request.hire']['id']

    blocked_id = assert_stopped(store)
    reopened = OrganizationStore(store.path, settings)
    assert assert_stopped(reopened) == blocked_id
    assert reopened.cancel(objective['id'])
    cancelled = OrganizationStore(store.path, settings)
    assert cancelled.claim_next() is None
    snapshot = cancelled.snapshot()
    assert snapshot['objectives'][0]['status'] == 'cancelled'
    assert snapshot['objectives'][0]['projectExecution'] is None
    assert all(row['status'] == ('completed' if row['type'] in {'request.decompose', 'request.plan'} else 'cancelled')
               for row in snapshot['requests'])
    assert usage(cancelled, objective)['modelCalls'] == 1
    assert {name: (source / name).read_bytes().decode() for name in original} == original
