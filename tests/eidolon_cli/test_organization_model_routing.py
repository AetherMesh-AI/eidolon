"""Owner routes preserve identity while exact candidates remain budget-fenced."""
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
import threading
from types import SimpleNamespace

import pytest

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_store import OrganizationStore
from eidolon_cli.organization_model_routing import assignment_view
from eidolon_cli.organization_model_routing_executor import ModelRouteFailure, effective_reasoning, invalid_json_failure


def route(ident='quality', **changes):
    return {'id': ident, 'provider': 'named-local', 'model': ident, 'reasoning': 'low',
            'request_types': ['request.decompose', 'request.plan', 'work.draft', 'request.review', 'request.accept'],
            'evidence_level': 3, 'max_context_tokens': 32768, 'max_output_tokens': 512, **changes}


def configuration(*, mode='fixed', agent='executive', fallbacks=0, routes=None):
    routes = routes or [route()]
    return {'model_routes': routes, 'model_assignments': [{'agent_id': agent, 'mode': mode,
        'route_ids': [r['id'] for r in routes], 'minimum_evidence': 2, 'max_fallbacks': fallbacks}]}


def settings(config=None, **changes):
    config = config or configuration()
    rates = [{'provider': r['provider'], 'model': r['model'], 'input_usd_per_million': str(i + 1),
              'output_usd_per_million': str(2 * (i + 1))} for i, r in enumerate(config['model_routes'])]
    return OrganizationSettings.from_config({'organization': {'max_output_tokens': 512,
        'max_context_tokens': 32768, 'model_costs': rates, **config, **changes}})


def setup(tmp_path, config=None, **changes):
    from eidolon_constants import get_eidolon_home
    from eidolon_cli import config as loader
    home = get_eidolon_home()
    home.mkdir(exist_ok=True, parents=True)
    (home / 'config.yaml').write_text(json.dumps({'providers': {'named-local': {'name': 'Named Local', 'api': 'http://127.0.0.1:9/v1', 'api_mode': 'chat_completions'}}}))
    loader._LOAD_CONFIG_CACHE.clear()
    store = OrganizationStore(tmp_path / 'organization' / 'state.db', settings(config, **changes))
    objective = store.create_objective('Analyze the exact supplied facts', idempotency_key='objective')
    claim = store.claim_next()
    return store, objective, claim


def snapshot(store, claim, bound=1000):
    return {**store.select_model_route(claim, input_bound=bound), 'adapterProvider': 'custom', 'endpointIdentity': 'a' * 64}


def reserve(store, claim, selected=None):
    selected = selected or snapshot(store, claim)
    return store.reserve_model_call(claim, provider=selected['selectedProvider'], model=selected['model'],
        input_limit=3048, output_limit=512, route_snapshot=selected)


def test_fixed_root_and_identity_preserving_owner_configuration_replay_reload_and_aba(tmp_path):
    with pytest.raises(ValueError, match='executive must remain Fixed'):
        settings(configuration(mode='auto'))
    store, objective, claim = setup(tmp_path)
    before = next(row for row in store.snapshot()['agents'] if row['id'] == 'executive')
    initial = store._policy_generation
    changed = configuration(routes=[route('replacement')])
    receipt = store.configure_model_routing(changed, expected_generation=initial, idempotency_key='changed')
    assert receipt['policyGeneration'] > initial
    assert store.configure_model_routing(changed, expected_generation=initial, idempotency_key='changed')['changed'] is False
    with pytest.raises(ValueError, match='idempotency'):
        store.configure_model_routing(configuration(), expected_generation=initial, idempotency_key='changed')
    reopened = OrganizationStore(store.path)
    assert reopened.settings.model_routes == store.settings.model_routes
    after = next(row for row in reopened.snapshot()['agents'] if row['id'] == 'executive')
    assert (after['id'], after['context'], after['purpose']) == (before['id'], before['context'], before['purpose'])
    reopened.configure_model_routing(configuration(), expected_generation=reopened._policy_generation, idempotency_key='back')
    assert reopened._policy_generation > receipt['policyGeneration']
    with pytest.raises(ValueError, match='no longer owned'):
        reserve(store, claim, {**snapshot_placeholder(), 'policyGeneration': initial})
    assert reopened.execution_audit(claim['id'])['modelCalls'] == []


def snapshot_placeholder():
    return {'selectedProvider': 'named-local', 'model': 'quality'}


def test_auto_filters_declared_capability_evidence_context_output_and_unknown_price_before_cost(tmp_path):
    routes = [route('unsupported', provider='openrouter'), route('weak', evidence_level=1), route('short', max_context_tokens=4096),
              route('tiny-output', max_output_tokens=256), route('unpriced'), route('wrong', request_types=['request.review']),
              route('economy'), route('premium')]
    config = configuration(mode='auto', agent='manager', routes=routes)
    rates = [{'provider': 'named-local', 'model': r['id'], 'input_usd_per_million': str(i + 1), 'output_usd_per_million': str(i + 1)}
             for i, r in enumerate(routes) if r['id'] != 'unpriced']
    store, objective, claim = setup(tmp_path, config, model_costs=rates)
    store.finish(claim, {'workPackages': [{'title': 'Exact analysis', 'description': 'Analyze facts', 'managerId': 'manager', 'criterionIndexes': [0], 'projectIds': [], 'dependsOn': [], 'maxTasks': 1}], 'requiredChecks': []})
    claim = store.claim_next()
    chosen = store.select_model_route(claim, input_bound=5000)
    assert chosen['routeId'] == 'economy'
    assert chosen['maxOutputTokens'] == 512
    assert assignment_view(store.settings, 'reviewer')['mode'] == 'legacy'
    with pytest.raises(ValueError, match='No eligible'):
        store.select_model_route(claim, input_bound=32768)


def test_dispatch_and_reservation_are_atomic_immutable_exact_named_prices_and_hard_capped(tmp_path):
    store, objective, claim = setup(tmp_path, max_model_calls=1)
    chosen = snapshot(store, claim)
    # Shared adapter identity is not the owner's named provider/pricing identity.
    with pytest.raises(ValueError, match='wire route'):
        store.reserve_model_call(claim, provider='custom', model='quality', input_limit=3048, output_limit=512, route_snapshot=chosen)
    assert store.execution_audit(claim['id'])['modelDispatches'] == []
    outcomes = []
    def contend():
        try:
            outcomes.append(reserve(store, claim, chosen))
        except ValueError:
            outcomes.append(None)
    threads = [threading.Thread(target=contend) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(value is not None for value in outcomes) == 1
    audit = store.execution_audit(claim['id'])
    assert len(audit['modelDispatches']) == len(audit['modelCalls']) == 1
    dispatch, call = audit['modelDispatches'][0], audit['modelCalls'][0]
    assert call['dispatchId'] == dispatch['id']
    assert call['provider'] == dispatch['selectedProvider'] == 'named-local'
    assert dispatch['adapterProvider'] == 'custom'
    assert call['pricing']['inputUsdPerMillion'] == dispatch['pricing']['inputUsdPerMillion'] == '1'
    assert 'endpointIdentity' not in dispatch
    assert call['reserved_cost_usd'] == '0.004072000'


def test_failure_uses_new_claim_and_retains_charges_without_replaying_unknown_outcomes(tmp_path):
    config = configuration(routes=[route('first'), route('second')], fallbacks=1)
    store, objective, claim = setup(tmp_path, config)
    reserve(store, claim)
    failure = ModelRouteFailure('invalid-complete-json', hashlib.sha256(b'invalid').hexdigest())
    with pytest.raises(ValueError, match='typed'):
        store.finish_model_route_failure(claim, asdict(failure))
    assert store.finish_model_route_failure(claim, failure)
    second = store.claim_next()
    assert second['token'] != claim['token'] and second['attempts'] == claim['attempts'] + 1
    assert second['agent_id'] == claim['agent_id']
    selected = snapshot(store, second)
    assert selected['routeId'] == 'second'
    reserve(store, second, selected)
    assert not store.finish_model_route_failure(second, failure)
    audit = store.execution_audit(claim['id'])
    assert len(audit['modelDispatches']) == len(audit['modelCalls']) == len(audit['modelRouteFailures']) == 2
    assert all(row['reserved_cost_usd'] is not None for row in audit['modelCalls'])
    assert store.claim_next() is None


def test_only_exact_complete_raw_invalid_json_qualifies_not_semantic_or_partial_output():
    agent = SimpleNamespace(_organization_complete_response=True, _organization_physical_sends=1,
        _organization_raw_response='invalid')
    assert invalid_json_failure(agent, 'invalid', execution=None, passes=[]).kind == 'invalid-complete-json'
    for changes, raw in [({'_organization_complete_response': False}, 'invalid'),
                         ({'_organization_physical_sends': 2}, 'invalid'),
                         ({'_organization_route_error': True}, 'invalid'), ({}, 'rewritten'),
                         ({'_organization_raw_response': '{}'}, '{}'),
                         ({'_organization_raw_response': '{"approved":false}'}, '{"approved":false}')]:
        changed = SimpleNamespace(**{**vars(agent), **changes})
        assert invalid_json_failure(changed, raw, execution=None, passes=[]) is None
    assert invalid_json_failure(agent, 'invalid', execution=object(), passes=[]) is None
    assert invalid_json_failure(agent, 'invalid', execution=None, passes=[{}]) is None


@pytest.mark.parametrize('options', [{'reasoning_effort': 'medium'}, {'reasoning_effort': 'low', 'extra_body': {'reasoning_effort': 'high'}},
    {'extra_body': {'thinking_config': {'budget_tokens': 4096}}}, {'extra_body': {'reasoning': {'enabled': 0, 'effort': 'low'}}}, {'extra_body': {'reasoning': {'enabled': False, 'effort': 'low'}}}])
def test_wire_reasoning_clamp_or_conflict_never_accepted(options):
    with pytest.raises(ValueError):
        effective_reasoning(options, 'low')
    assert effective_reasoning({'reasoning_effort': 'low'}, 'low') == 'low'
    assert effective_reasoning({'extra_body': {'reasoning': {'enabled': True, 'effort': 'low'}}}, 'low') == 'low'


def test_owner_cli_persists_replay_and_preserves_file_and_staffing_authority(tmp_path, monkeypatch, capsys):
    from pathlib import Path
    from eidolon_cli.organization_model_routing_cli import main
    from eidolon_cli.organization_management import _validate_agent_members
    from eidolon_cli.organization_roster import OrganizationStaff
    home = tmp_path / 'profile'
    home.mkdir()
    base = settings(configuration())
    original = json.dumps({'organization': {k: v for k, v in asdict(base).items() if k != 'roster'}, 'private': 'fixture-preserved'})
    (home / 'config.yaml').write_text(original)
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    store = OrganizationStore(home / 'organization' / 'state.db', base)
    chosen = configuration(routes=[route('replacement')])
    payload = tmp_path / 'routes.json'
    payload.write_text(json.dumps(chosen))
    args = ['configure', '--file', str(payload), '--expected-generation', str(store._policy_generation), '--idempotency-key', 'cli']
    assert main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['changed'] is True
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)['changed'] is False
    assert main(['show']) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown['routes'][0]['id'] == 'replacement'
    reopened = OrganizationStore(store.path)
    assert reopened.settings.model_routes[0].id == 'replacement'
    assert (home / 'config.yaml').read_text() == original
    actor = OrganizationStaff('boss', 'Boss', 'general', ('request.hire',), role='Manager', authority=('staff.manage',))
    with pytest.raises(ValueError, match='owner model assignment'):
        _validate_agent_members({'members': [{'id': 'new-worker', 'name': 'New', 'team': 'general', 'capabilities': ['work.draft']}]}, reopened.settings, actor)
    with pytest.raises(ValueError, match='only members'):
        _validate_agent_members({'model_assignments': []}, reopened.settings, actor)


def test_dispatch_denials_roll_back_first_snapshot_and_no_clamped_caps_are_sent(tmp_path):
    store, objective, claim = setup(tmp_path, completion_reserve_model_calls=1, max_model_calls=1)
    chosen = snapshot(store, claim)
    with pytest.raises(ValueError, match='completion model-call reserve'):
        reserve(store, claim, chosen)
    assert store.execution_audit(claim['id'])['modelDispatches'] == []
    assert store.execution_audit(claim['id'])['modelCalls'] == []
    other, _, other_claim = setup(tmp_path / 'other')
    selected = snapshot(other, other_claim)
    reserve(other, other_claim, selected)
    for changed in ({'endpointIdentity': 'b' * 64}, {'reasoning': 'high'}, {'policyGeneration': selected['policyGeneration'] + 1}):
        with pytest.raises(ValueError, match='immutable|changed'):
            reserve(other, other_claim, {**selected, **changed})
    assert len(other.execution_audit(other_claim['id'])['modelCalls']) == 1
    with pytest.raises(ValueError, match='token cap'):
        other.reserve_model_call(other_claim, provider='named-local', model='quality', input_limit=3048, output_limit=256, route_snapshot=selected)
    assert len(other.execution_audit(other_claim['id'])['modelCalls']) == 1


def test_assignment_disabled_legacy_and_custom_executive_auto_remain_supported():
    legacy = OrganizationSettings.from_config({'organization': {}})
    assert assignment_view(legacy, 'executive')['mode'] == 'legacy'
    custom = configuration(mode='auto', agent='other-executive')
    parsed = settings(custom, roster=[{'id': 'other-executive', 'name': 'Other Executive', 'team': 'general',
        'role': 'Executive', 'manager_id': 'owner', 'capabilities': ['request.decompose', 'request.accept']}])
    assert assignment_view(parsed, 'other-executive')['mode'] == 'auto'


def test_corrected_owner_policy_retry_starts_new_fallback_chain_without_refund(tmp_path):
    store, objective, claim = setup(tmp_path)
    reserve(store, claim)
    assert not store.finish_model_route_failure(claim, ModelRouteFailure('invalid-complete-json', 'a' * 64))
    before = store.execution_audit(claim['id'])
    corrected = configuration(routes=[route(model='corrected-model')])
    store.configure_model_routing(corrected, expected_generation=store._policy_generation, idempotency_key='corrected')
    assert store.retry(claim['id'], idempotency_key='owner-retry')
    retried = store.claim_next()
    assert retried['attempts'] == 2
    selected = snapshot(store, retried)
    assert selected['routeId'] == 'quality' and selected['model'] == 'corrected-model'
    reserve(store, retried, selected)
    after = store.execution_audit(claim['id'])
    assert after['modelCalls'][0] == before['modelCalls'][0]
    assert after['modelRouteFailures'] == before['modelRouteFailures']
    assert len(after['modelCalls']) == 2


@pytest.mark.parametrize('provider,endpoint', [('openrouter', 'https://openrouter.ai/api/v1'),
    ('custom', 'https://openrouter.ai/api/v1'), ('custom', 'https://api.perplexity.ai/v1'),
    ('custom', 'http://localhost:11434/v1'), ('custom', 'http://localhost:1234/v1'),
    ('custom', 'https://models.github.ai/inference'), ('custom', 'https://api.nousresearch.com/v1')])
def test_discovery_and_search_backed_routes_are_explicitly_unsupported(provider, endpoint):
    from eidolon_cli.organization_model_routing_executor import require_supported_route
    with pytest.raises(ValueError, match='currently support'):
        require_supported_route({'selectedProvider': provider}, {'model': {'provider': provider, 'base_url': endpoint}})


def test_retry_epoch_prevents_route_cycling_when_wall_clock_timestamps_tie(tmp_path, monkeypatch):
    import time
    frozen = time.time()
    monkeypatch.setattr(time, 'time', lambda: frozen)
    routes = [route('first'), route('second')]
    store, objective, claim = setup(tmp_path, configuration(routes=routes), max_attempts=3)
    reserve(store, claim)
    failure = ModelRouteFailure('invalid-complete-json', 'b' * 64)
    assert not store.finish_model_route_failure(claim, failure)
    store.configure_model_routing(configuration(routes=routes, fallbacks=1),
        expected_generation=store._policy_generation, idempotency_key='owner-expanded-order')
    assert store.retry(claim['id'], idempotency_key='fresh-owner-chain')
    second = store.claim_next()
    chosen = snapshot(store, second)
    assert chosen['routeId'] == 'first'
    reserve(store, second, chosen)
    assert store.finish_model_route_failure(second, failure)
    third = store.claim_next()
    assert third['attempts'] == 3
    assert snapshot(store, third)['routeId'] == 'second'
    assert len(store.execution_audit(claim['id'])['modelCalls']) == 2
