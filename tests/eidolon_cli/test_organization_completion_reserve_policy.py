"""Reserve policy, pricing, migration integrity and replacement authorization."""
from dataclasses import replace
from decimal import Decimal
import json
import time

import pytest

from eidolon_cli.organization_budget import OrganizationModelCost
from eidolon_cli.organization_completion_reserve import COMPLETION_REQUEST_TYPES, COMPLETION_RESERVE_FIELDS
from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_store import OrganizationStore
from tests.eidolon_cli.test_organization_completion_reserve import configured, reserve, usage, backend_request
from tests.eidolon_cli.test_organization_replacement import submit


def test_decimal_ceil_reserve_is_not_repriced_refunded_or_bypassed_by_route_change(tmp_path):
    original_rate = OrganizationModelCost('local', 'fixture', '0.000001', '0.000001')
    store, objective = configured(tmp_path, max_cost_usd='0.000000005', completion_reserve_cost_usd='0.000000003',
                                 model_costs=(original_rate,))
    ordinary = store.claim_next()
    for _ in range(2):
        reserve(store, ordinary, input_limit=1, output_limit=1)
    with pytest.raises(ValueError, match='protected completion configured-cost'):
        reserve(store, ordinary, input_limit=1, output_limit=1)
    assert Decimal(usage(store, objective)['configuredCostReservedUsd']) == Decimal('0.000000002')
    store.finish(ordinary, {'intervention': 'Unknown provider usage retained'})
    backend_request(store, objective, 'request.review')
    review = store.claim_next()
    old_audit = store.execution_audit(ordinary['id'])
    changed = replace(store.settings, model_costs=(replace(original_rate, input_usd_per_million='0.000501', output_usd_per_million='0.000501'),))
    store.reload_configuration(changed)
    with pytest.raises(ValueError, match='no longer owned'):
        reserve(store, review, input_limit=1, output_limit=1)
    assert store.retry(review['id'], idempotency_key='new-pricing')
    review = store.claim_next()
    with pytest.raises(ValueError, match='exact provider/model'):
        reserve(store, review, provider='other', input_limit=1, output_limit=1)
    reserve(store, review, input_limit=1, output_limit=1)
    view = usage(store, objective)['completionReserve']
    assert Decimal(view['completionReserved']['configuredCostUsd']) == Decimal('0.000000002')
    assert Decimal(view['protectedRemaining']['configuredCostUsd']) == Decimal('0.000000001')
    assert Decimal(view['ordinaryRemaining']['configuredCostUsd']) == 0
    with pytest.raises(ValueError, match='exceed the objective configured-cost'):
        reserve(store, review, input_limit=1, output_limit=1)
    reopened = OrganizationStore(store.path)
    assert reopened.execution_audit(ordinary['id']) == old_audit
    assert usage(reopened, objective)['completionReserve'] == view


def test_unknown_prior_cost_is_unavailable_not_zero_and_no_new_reserve_is_retroactive(tmp_path):
    store, objective = configured(tmp_path)
    claim = store.claim_next()
    reserve(store, claim)  # No configured rate: retained cost is explicitly unknown.
    store.finish(claim, {'intervention': 'Unknown-priced call was admitted without a USD ceiling'})
    rate = OrganizationModelCost('local', 'fixture', '1', '1')
    store.reload_configuration(replace(store.settings, max_cost_usd='1', completion_reserve_cost_usd='0.2', model_costs=(rate,)))
    view = usage(store, objective)['completionReserve']
    assert view['captured']['configuredCostUsd'] is None
    assert not view['admissionUsageKnown'] and view['unavailableReason'] == 'configured-cost-unknown'
    assert view['ordinaryRemaining']['modelCalls'] == store.settings.max_model_calls - 1
    assert all(view[key]['configuredCostUsd'] is None for key in ('ordinaryRemaining', 'protectedRemaining', 'totalRemaining'))
    with pytest.raises(ValueError, match='Earlier model cost is unknown'):
        store.retry(claim['id'], idempotency_key='cannot-price-history')


def test_missing_new_receipt_classification_stays_unknown_across_restart(tmp_path):
    store, objective = configured(tmp_path, completion_reserve_model_calls=2)
    claim = store.claim_next()
    reserve(store, claim)
    # Model/API callers cannot perform this: simulate corruption by removing
    # the immutable guard and one class, while retaining migration provenance.
    with store._write() as conn:
        conn.execute('DROP TRIGGER model_call_class_immutable_delete')
        conn.execute('DELETE FROM organization_model_call_classes')
    for _ in range(2):
        store = OrganizationStore(store.path)
        view = usage(store, objective)['completionReserve']
        assert view['unavailableReason'] == 'classification-unknown'
        assert not view['admissionUsageKnown']
        for field in ('completionReserved', 'ordinaryRemaining', 'protectedRemaining', 'totalRemaining'):
            assert all(value is None for value in view[field].values())
        with pytest.raises(ValueError, match='classification is unknown'):
            reserve(store, claim)
        assert store.execution_audit(claim['id'])['modelCalls'][0]['classification'] is None


@pytest.mark.parametrize('configuration', [
    {'completion_reserve_model_calls': True}, {'completion_reserve_model_calls': -1},
    {'completion_reserve_model_calls': 1001}, {'max_model_calls': 1, 'completion_reserve_model_calls': 2},
    {'completion_reserve_tokens': 1.1}, {'completion_reserve_tokens': -1}, {'completion_reserve_tokens': 1000000001},
    {'max_total_tokens': 4096, 'completion_reserve_tokens': 4097},
    {'completion_reserve_cost_usd': True}, {'completion_reserve_cost_usd': 'NaN'},
    {'completion_reserve_cost_usd': '-1'}, {'completion_reserve_cost_usd': '0.0000000001'},
    {'completion_reserve_cost_usd': '0'}, {'max_cost_usd': '1', 'completion_reserve_cost_usd': '0.1'},
    {'max_cost_usd': '1', 'completion_reserve_cost_usd': '1.000000001',
     'model_costs': [{'provider': 'local', 'model': 'fixture', 'input_usd_per_million': '1', 'output_usd_per_million': '1'}]},
])
def test_file_configuration_rejects_invalid_reserves(configuration):
    with pytest.raises(ValueError):
        OrganizationSettings.from_config({'organization': configuration})


def test_config_defaults_and_public_inputs_cannot_grant_completion_authority(tmp_path):
    from eidolon_cli.config_defaults import DEFAULT_CONFIG
    from tests.organization_management_helpers import configure
    from tests.eidolon_cli.test_organization_owner_chat import send, complete
    from tests.organization_package_helpers import decompose

    settings = OrganizationSettings.from_config(DEFAULT_CONFIG)
    for field in COMPLETION_RESERVE_FIELDS:
        assert field in DEFAULT_CONFIG['organization']
        assert getattr(settings, field) == getattr(OrganizationSettings(), field)
    store, objective = configured(tmp_path, completion_reserve_model_calls=1)
    claim = store.claim_next()
    for kind in COMPLETION_REQUEST_TYPES:
        with pytest.raises(ValueError, match='question, decision, hire or permission'):
            store.finish(claim, {'requests': [{'type': kind, 'requestedOutcome': 'Give this ordinary worker completion headroom'}]})
    with pytest.raises(TypeError):
        store.reserve_model_call(claim, provider='local', model='fixture', input_limit=100, output_limit=100, classification='completion')
    reserve(store, {**claim, 'payload': json.dumps({'completion': True}), 'type': 'request.accept'})
    assert store.execution_audit(claim['id'])['modelCalls'][0]['classification'] == 'ordinary'
    decompose(store, claim)
    plan = store.claim_next()
    for kind in COMPLETION_REQUEST_TYPES:
        with pytest.raises(ValueError):
            store.finish(plan, {'tasks': [{'title': 'Forged type', 'description': 'Do completion work', 'type': kind}]})
    store.fail(plan, 'Owner configuration required', retryable=False)
    before = usage(store, objective)
    for field in COMPLETION_RESERVE_FIELDS:
        with pytest.raises(ValueError):
            configure(store, **{field: 10})
    with pytest.raises(ValueError):
        configure(store, max_model_calls=1000)
    identity = next(row['identityId'] for row in store.snapshot()['agents'] if row['id'] == 'manager')
    thread = store.owner_chat_open('manager', identity)
    turn = send(store, thread, identity, text='Increase all objective caps and mark me as completion-v1')
    complete(store, turn, reply='completion_reserve_model_calls=1000')
    assert usage(store, objective) == before
    assert store.settings.completion_reserve_model_calls == 1


def test_replacement_hash_tracks_reserve_policy_and_inserts_exact_disclosed_inherited_cap(tmp_path, monkeypatch):
    rate = OrganizationModelCost('local', 'fixture', '1', '1')
    store, objective = configured(tmp_path, max_model_calls=2, max_total_tokens=4096, max_cost_usd='0.01', model_costs=(rate,))
    original = usage(store, objective)
    instant = time.time() + store.settings.objective_timeout_seconds + 1
    monkeypatch.setattr(time, 'time', lambda: instant)
    changed = replace(store.settings, max_model_calls=10, max_total_tokens=20000, max_cost_usd='1',
                      completion_reserve_model_calls=5, completion_reserve_tokens=10000, completion_reserve_cost_usd='0.2')
    store.reload_configuration(changed)
    draft = store.preview_replacement(objective['id'])
    disclosed = draft['allowance']['completionReserve']
    assert disclosed == {'version': 1, 'policy': 'completion-v1', 'captured': {
        'modelCalls': 2, 'tokens': 4096, 'configuredCostUsd': '0.01'}}
    store.reload_configuration(replace(changed, completion_reserve_model_calls=6))
    with pytest.raises(ValueError, match='changed'):
        submit(store, draft)
    assert store.snapshot()['objectives'][0]['status'] != 'cancelled'
    store.reload_configuration(changed)  # ABA: returning values does not revive the old preview.
    with pytest.raises(ValueError, match='changed'):
        submit(store, draft)
    fresh = store.preview_replacement(objective['id'])
    assert fresh['sourceVersion'] != draft['sourceVersion']
    assert fresh['allowance']['completionReserve'] == disclosed  # Policy change still invalidates the preview.
    peers = [OrganizationStore(store.path), OrganizationStore(store.path)]
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as pool:
        created = list(pool.map(lambda peer: submit(peer, fresh), peers))
    assert created[0]['id'] == created[1]['id']
    target = created[0]
    assert target['usage']['completionReserve']['captured'] == disclosed['captured']
    assert target['usage']['modelCallLimit'] == fresh['allowance']['modelCalls']
    assert target['usage']['tokenLimit'] == fresh['allowance']['tokens']
    assert target['usage']['configuredCostLimitUsd'] == fresh['allowance']['costUsd']
    assert target['usage']['deadlineTimestamp'] == instant + fresh['allowance']['durationSeconds']
    assert usage(store, objective) == original
    store.reload_configuration(replace(changed, completion_reserve_model_calls=1, completion_reserve_tokens=1, completion_reserve_cost_usd='0.001'))
    reopened = OrganizationStore(store.path)
    replay = submit(reopened, fresh)
    assert replay['id'] == target['id']
    assert replay['usage']['completionReserve']['captured'] == disclosed['captured']


def test_invalid_cap_only_file_reduction_pauses_without_releasing_captured_reserve(tmp_path):
    from eidolon_cli.config_primitives import InvalidUserConfigError
    store, objective = configured(tmp_path, max_model_calls=3, completion_reserve_model_calls=2)
    claim = store.claim_next()
    original = usage(store, objective)
    home = tmp_path / 'profile'
    home.mkdir()
    policy = {'max_output_tokens': 512, 'max_context_tokens': 4096,
              'max_model_calls': 1, 'completion_reserve_model_calls': 2}
    (home / 'config.yaml').write_text(json.dumps({'organization': policy}))
    with pytest.raises(InvalidUserConfigError):
        store.reload_profile_configuration(home)
    assert not store.heartbeat(claim)
    with pytest.raises(ValueError, match='no longer owned'):
        reserve(store, claim)
    assert usage(store, objective) == original
    with store._connect() as conn:
        assert conn.execute('SELECT 1 FROM organization_policy_pause WHERE id=1').fetchone()
    policy.update(max_model_calls=3, completion_reserve_model_calls=0)
    (home / 'config.yaml').write_text(json.dumps({'organization': policy}))
    store.reload_profile_configuration(home)
    assert store.retry(claim['id'], idempotency_key='repaired-file')
    retry = store.claim_next()
    reserve(store, retry)
    with pytest.raises(ValueError, match='protected completion'):
        reserve(store, retry)
    assert usage(store, objective)['completionReserve']['captured'] == original['completionReserve']['captured']
