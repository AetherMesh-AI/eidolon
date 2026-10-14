"""Owner policy previews are read-only and confirmed writes retain exact receipts."""
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace
import uuid

import pytest

from eidolon_cli import organization_metadata as metadata
from eidolon_cli import organization_owner_policy as owner_policy
from eidolon_cli import organization_service as services
from eidolon_cli.organization_budget import budget_view
from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_policy import persisted_settings
from eidolon_cli.organization_store import OrganizationStore


def create_profile(home):
    home.mkdir(parents=True, exist_ok=True)
    configuration = {'max_output_tokens': 512, 'max_context_tokens': 4096,
        'max_model_calls': 4, 'max_total_tokens': 8192, 'objective_timeout_seconds': 3600,
        'completion_reserve_model_calls': 1, 'completion_reserve_tokens': 1024,
        'max_cost_usd': '1', 'completion_reserve_cost_usd': '0.1',
        'model_costs': [{'provider': 'named-local', 'model': 'baseline',
                         'input_usd_per_million': '1', 'output_usd_per_million': '2'}]}
    (home / 'config.yaml').write_text(json.dumps({'organization': configuration,
        'providers': {'named-local': {'name': 'Local test route', 'api': 'http://127.0.0.1:9/v1',
                                      'api_mode': 'chat_completions'}}}), encoding='utf-8')
    path = home / 'organization' / 'state.db'
    identity = metadata.save_metadata(path, display_name='Policy test organization',
        owner_display_name='Alex', expected_organization_id=None, expected_revision=None,
        idempotency_key='initialize')['metadata']
    store = OrganizationStore(path, OrganizationSettings.from_config({'organization': configuration}))
    objective = store.create_objective('Retain admission and execution history', idempotency_key='objective')
    claim = store.claim_next()
    store.reserve_model_call(claim, provider='named-local', model='baseline', input_limit=100, output_limit=100)
    return SimpleNamespace(home=home, path=path, identity=identity, store=store,
                           objective=objective, claim=claim)


def forbid_execution(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Owner policy constructed or started execution state')
    monkeypatch.setattr(services, 'get_service', forbidden)
    monkeypatch.setattr(services.OrganizationService, '__init__', forbidden)
    monkeypatch.setattr(services.OrganizationService, 'start', forbidden)
    monkeypatch.setattr(OrganizationStore, '__init__', forbidden)


def rows(path):
    with closing(sqlite3.connect(path)) as conn:
        names = [name for name, in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {name: conn.execute(f'SELECT * FROM "{name}" ORDER BY rowid').fetchall() for name in names}


def durable_dump(path):
    with closing(sqlite3.connect(path)) as conn:
        return tuple(conn.iterdump())


def state(profile):
    return owner_policy.owner_policy(profile.home, profile.identity['organizationId'])


def routing(*, mode='fixed', agent='executive', model='economy'):
    return {'model_routes': [{'id': model, 'provider': 'named-local', 'model': model,
        'reasoning': 'low', 'request_types': ['request.decompose', 'request.plan', 'request.review', 'request.accept'],
        'evidence_level': 3, 'max_context_tokens': 4096, 'max_output_tokens': 512}],
        'model_assignments': [{'agent_id': agent, 'mode': mode, 'route_ids': [model],
                               'minimum_evidence': 1, 'max_fallbacks': 0}]}


def policy_params(snapshot, configuration):
    return {'expectedOrganizationId': snapshot['organizationId'],
            'expectedGeneration': snapshot['policyGeneration'], 'sourceRevision': snapshot['sourceRevision'],
            'configuration': configuration}


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def observation_params(profile, snapshot=None, **changes):
    snapshot = snapshot or state(profile)
    now = time.time()
    result = policy_params(snapshot, {})
    result.pop('configuration')
    result['observation'] = {'objectiveId': profile.objective['id'], 'expectedRevision': 0,
        'remainingPercent': 5, 'asOf': iso(now - 1), 'expiresAt': iso(now + 300),
        'resetAt': None, **changes}
    return result


def confirmed(profile, action, params, key='save'):
    preview = owner_policy.preview_policy(profile.home, action, params)
    return {**params, 'previewVersion': preview['previewVersion'], 'idempotencyKey': key, 'confirmed': True}


@pytest.fixture
def profile(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    home = tmp_path / '.eidolon'
    monkeypatch.setenv('HERMES_HOME', str(home))
    return create_profile(home)


def test_missing_identity_or_execution_does_not_initialize_storage(tmp_path, monkeypatch):
    home = tmp_path / 'unconfigured'
    forbid_execution(monkeypatch)
    result = owner_policy.owner_policy(home, str(uuid.uuid4()))
    assert result['state'] == 'unconfigured' and result['reason']
    assert not home.exists()
    path = home / 'organization' / 'state.db'
    identity = metadata.save_metadata(path, display_name='Metadata only', owner_display_name='Alex',
        expected_organization_id=None, expected_revision=None, idempotency_key='metadata')['metadata']
    before = durable_dump(path)
    result = owner_policy.owner_policy(home, identity['organizationId'])
    assert result['state'] in {'unconfigured', 'unsupported'} and result['reason']
    assert durable_dump(path) == before


def test_read_and_every_preview_are_durable_write_free_and_never_adopt_sources(profile, monkeypatch):
    forbid_execution(monkeypatch)
    before = durable_dump(profile.path)
    original = state(profile)
    assert original['capabilityVersion'] == 1 and original['state'] == 'ready'
    assert original['ownerId'] == profile.identity['owner']['ownerId']
    assert original['policyGeneration'] == profile.store._policy_generation
    assert set(original['capabilities']) == {'modelRouting', 'budgetPolicy', 'budgetObservation'}
    assert any(agent['id'] == profile.claim['agent_id'] for agent in original['agents'])
    requests = [('modelRouting', policy_params(original, routing())),
                ('budgetPolicy', policy_params(original, {'max_model_calls': 8})),
                ('budgetObservation', observation_params(profile, original))]
    for action, params in requests:
        preview = owner_policy.preview_policy(profile.home, action, params)
        assert preview['organizationId'] == original['organizationId']
        assert preview['ownerId'] == original['ownerId']
        assert preview['policyGeneration'] == original['policyGeneration']
        assert preview['sourceRevision'] == original['sourceRevision']
        assert preview['previewVersion']
        assert isinstance(preview['changes'], list)
        assert preview['impact']
        assert isinstance(preview['warnings'], list)
        assert all(isinstance(warning, dict) and warning.get('code') for warning in preview['warnings'])
        repeated = owner_policy.preview_policy(profile.home, action, params)
        for field in ('previewVersion', 'changes', 'warnings'):
            assert repeated[field] == preview[field]
    assert durable_dump(profile.path) == before
    source = profile.home / 'config.yaml'
    content = json.loads(source.read_text())
    content['organization']['max_model_calls'] = 9
    source.write_text(json.dumps(content))
    changed = state(profile)
    assert changed['sourceRevision'] != original['sourceRevision']
    assert changed['policyGeneration'] == original['policyGeneration']
    owner_policy.preview_policy(profile.home, 'budgetPolicy', policy_params(changed, {'max_model_calls': 10}))
    assert durable_dump(profile.path) == before
    assert json.loads(source.read_text()) == content


@pytest.mark.parametrize('action,configuration', [
    ('modelRouting', routing()),
    ('budgetPolicy', {'max_model_calls': 10, 'max_total_tokens': 16384,
                      'objective_timeout_seconds': 7200, 'max_cost_usd': '2',
                      'completion_reserve_model_calls': 2, 'completion_reserve_tokens': 2048}),
])
def test_confirmed_policy_changes_fence_without_topup_restart_or_history_loss(profile, monkeypatch, action, configuration):
    initial = state(profile)
    request = confirmed(profile, action, policy_params(initial, configuration))
    before = rows(profile.path)
    source = (profile.home / 'config.yaml').read_bytes()
    forbid_execution(monkeypatch)
    result = owner_policy.apply_policy(profile.home, action, request)
    receipt = result['receipt']
    assert receipt['idempotencyKey'] == request['idempotencyKey']
    assert receipt['organizationId'] == initial['organizationId']
    assert receipt['ownerId'] == initial['ownerId']
    assert receipt['action'] == action
    assert receipt['policyGeneration'] == initial['policyGeneration'] + 1
    assert receipt['previewVersion'] == request['previewVersion']
    current = state(profile)
    assert current['policyGeneration'] == receipt['policyGeneration']
    assert current['ownerId'] == initial['ownerId']
    after = rows(profile.path)
    for name in ('objectives', 'objective_budgets', 'objective_completion_reserves',
                 'organization_model_calls', 'organization_model_call_classes', 'objective_usage', 'agents'):
        assert after[name] == before[name], name
    assert (profile.home / 'config.yaml').read_bytes() == source
    with closing(sqlite3.connect(profile.path)) as conn:
        conn.row_factory = sqlite3.Row
        request_row = conn.execute('SELECT * FROM requests WHERE id=?', (profile.claim['id'],)).fetchone()
        assert request_row['status'] != 'running'
        assert request_row['attempts'] == profile.claim['attempts']
        budget = budget_view(conn, profile.objective['id'], persisted_settings(conn))
    assert budget['modelCalls'] == 1
    assert budget['modelCallLimit'] == profile.store.settings.max_model_calls
    assert budget['tokenLimit'] == profile.store.settings.max_total_tokens
    assert budget['completionReserve']['captured']['modelCalls'] == profile.store.settings.completion_reserve_model_calls


def test_exact_replay_precedes_generation_source_and_clock_validation_but_input_reuse_fails(profile, monkeypatch):
    params = policy_params(state(profile), {'max_model_calls': 6})
    request = confirmed(profile, 'budgetPolicy', params, 'original')
    first = owner_policy.apply_policy(profile.home, 'budgetPolicy', request)
    later = confirmed(profile, 'budgetPolicy', policy_params(state(profile), {'max_model_calls': 7}), 'later')
    owner_policy.apply_policy(profile.home, 'budgetPolicy', later)
    (profile.home / 'config.yaml').write_text('organization: [invalid source')
    before = durable_dump(profile.path)
    assert owner_policy.apply_policy(profile.home, 'budgetPolicy', request) == first
    assert durable_dump(profile.path) == before
    changed = {**request, 'configuration': {'max_model_calls': 8}}
    with pytest.raises((ValueError, RuntimeError), match='[Ii]dempotency|[Rr]etry|conflict'):
        owner_policy.apply_policy(profile.home, 'budgetPolicy', changed)
    assert durable_dump(profile.path) == before


@pytest.mark.parametrize('field,value', [('expectedOrganizationId', str(uuid.uuid4())),
    ('expectedGeneration', 2**60), ('sourceRevision', '0' * 64)])
def test_preview_cas_rejects_wrong_identity_generation_and_source_without_writes(profile, field, value):
    params = {**policy_params(state(profile), {'max_model_calls': 7}), field: value}
    before = durable_dump(profile.path)
    with pytest.raises((ValueError, RuntimeError)):
        owner_policy.preview_policy(profile.home, 'budgetPolicy', params)
    assert durable_dump(profile.path) == before


@pytest.mark.parametrize('change', ['preview', 'source', 'generation', 'confirmation'])
def test_apply_rejects_unreviewed_or_stale_intent_without_partial_writes(profile, change):
    request = confirmed(profile, 'budgetPolicy', policy_params(state(profile), {'max_model_calls': 8}))
    if change == 'preview':
        request['previewVersion'] = '0' * 64
    elif change == 'source':
        with (profile.home / 'config.yaml').open('a') as stream:
            stream.write('\n# Changed after owner review\n')
    elif change == 'generation':
        other = confirmed(profile, 'budgetPolicy', policy_params(state(profile), {'max_model_calls': 7}), 'other')
        owner_policy.apply_policy(profile.home, 'budgetPolicy', other)
    else:
        request['confirmed'] = False
    before = durable_dump(profile.path)
    with pytest.raises((ValueError, RuntimeError)):
        owner_policy.apply_policy(profile.home, 'budgetPolicy', request)
    assert durable_dump(profile.path) == before


def test_observation_is_revisioned_advisory_only_and_exact_replay_survives_expiry(profile, monkeypatch):
    now = float(int(time.time()))
    monkeypatch.setattr(time, 'time', lambda: now)
    original = state(profile)
    params = observation_params(profile, original, asOf=iso(now - 1), expiresAt=iso(now + 60), resetAt=iso(now + 30))
    request = confirmed(profile, 'budgetObservation', params)
    before = rows(profile.path)
    forbid_execution(monkeypatch)
    first = owner_policy.apply_policy(profile.home, 'budgetObservation', request)
    assert first['receipt']['policyGeneration'] == original['policyGeneration']
    assert first['receipt']['observationRevision'] == 1
    after = rows(profile.path)
    for name in ('organization_policy', 'objectives', 'objective_budgets', 'objective_completion_reserves',
                 'organization_model_calls', 'organization_model_call_classes', 'requests', 'request_policy'):
        assert after[name] == before[name], name
    observed = next(row for row in state(profile)['observations'] if row['objectiveId'] == profile.objective['id'])
    assert observed['revision'] == 1
    assert observed['observation']['remainingPercent'] == 5
    now += 120
    stale = durable_dump(profile.path)
    assert owner_policy.apply_policy(profile.home, 'budgetObservation', request) == first
    assert durable_dump(profile.path) == stale
    fresh = observation_params(profile, expectedRevision=1, remainingPercent=100)
    fresh_request = confirmed(profile, 'budgetObservation', fresh, 'refresh')
    second = owner_policy.apply_policy(profile.home, 'budgetObservation', fresh_request)
    assert second['receipt']['observationRevision'] == 2
    assert second['receipt']['policyGeneration'] == original['policyGeneration']
    final = rows(profile.path)
    for name in ('objective_budgets', 'objective_completion_reserves', 'organization_model_calls', 'requests'):
        assert final[name] == before[name]


@pytest.mark.parametrize('changes', [
    {'expectedRevision': 1}, {'expectedRevision': True}, {'remainingPercent': True},
    {'remainingPercent': float('nan')}, {'remainingPercent': -1}, {'remainingPercent': 101},
    {'asOf': '2026-10-14T12:00:00'}, {'objectiveId': 'another-objective'},
    {'actorId': 'owner'}, {'remainingTokens': 1000}, {'expectedRevision': 0.0}, {'resetAt': True},
])
def test_invalid_or_stale_observation_does_not_write(profile, changes):
    params = observation_params(profile, **changes)
    before = durable_dump(profile.path)
    with pytest.raises((ValueError, RuntimeError)):
        owner_policy.preview_policy(profile.home, 'budgetObservation', params)
    assert durable_dump(profile.path) == before


def test_auto_requires_exact_prices_saved_separately_before_route_assignment(profile):
    configuration = routing(mode='auto', agent='manager')
    original = state(profile)
    before = durable_dump(profile.path)
    with pytest.raises((ValueError, RuntimeError), match='[Pp]ric|[Cc]ost'):
        owner_policy.preview_policy(profile.home, 'modelRouting', policy_params(original, configuration))
    assert durable_dump(profile.path) == before
    prices = [*original['configuration']['model_costs'], {'provider': 'named-local', 'model': 'economy',
              'input_usd_per_million': '0.1', 'output_usd_per_million': '0.2'}]
    pricing = confirmed(profile, 'budgetPolicy', policy_params(original, {'model_costs': prices}), 'prices')
    owner_policy.apply_policy(profile.home, 'budgetPolicy', pricing)
    routed = confirmed(profile, 'modelRouting', policy_params(state(profile), configuration), 'routes')
    receipt = owner_policy.apply_policy(profile.home, 'modelRouting', routed)['receipt']
    assert receipt['configuration'] == configuration
    assert state(profile)['configuration']['model_assignments'][0]['mode'] == 'auto'


@pytest.mark.parametrize('action,configuration', [
    ('modelRouting', {'model_routes': []}), ('modelRouting', {'model_assignments': []}),
    ('modelRouting', {**routing(), 'model_costs': []}),
    ('modelRouting', routing(mode='auto')),
    ('modelRouting', routing(agent='nonexistent-agent')),
    ('budgetPolicy', {'tool_grants': ['read_file']}), ('budgetPolicy', {'read_roots': ['/']}),
    ('budgetPolicy', {'roster': []}), ('budgetPolicy', {'max_inflight': 4}),
    ('budgetPolicy', {'model_routes': []}), ('budgetPolicy', {'max_model_calls': True}),
    ('budgetPolicy', {'completion_reserve_model_calls': 5}),
    ('budgetPolicy', {'max_context_tokens': 4096, 'max_output_tokens': 4096}),
    ('budgetPolicy', {'model_costs': [{'provider': '*', 'model': 'economy',
        'input_usd_per_million': '1', 'output_usd_per_million': '1'}]}),
])
def test_policy_configuration_is_narrow_typed_and_never_grants_execution(profile, action, configuration):
    before = durable_dump(profile.path)
    with pytest.raises((ValueError, RuntimeError)):
        owner_policy.preview_policy(profile.home, action, policy_params(state(profile), deepcopy(configuration)))
    assert durable_dump(profile.path) == before


def test_failed_receipt_insert_rolls_back_policy_generation_and_all_mutations(profile):
    initial = confirmed(profile, 'budgetPolicy', policy_params(state(profile), {'max_model_calls': 6}), 'first')
    owner_policy.apply_policy(profile.home, 'budgetPolicy', initial)
    request = confirmed(profile, 'budgetPolicy', policy_params(state(profile), {'max_model_calls': 7}), 'must-roll-back')
    with closing(sqlite3.connect(profile.path)) as conn:
        receipt_tables = [name for name, in conn.execute("SELECT name FROM sqlite_master WHERE type='table' "
            "AND name LIKE '%owner_policy%receipt%'")]
        assert len(receipt_tables) == 1
        conn.execute(f'CREATE TRIGGER reject_owner_policy_receipt BEFORE INSERT ON "{receipt_tables[0]}" '
                     "BEGIN SELECT RAISE(ABORT, 'test receipt failed'); END")
        conn.commit()
    before = durable_dump(profile.path)
    with pytest.raises((ValueError, RuntimeError)):
        owner_policy.apply_policy(profile.home, 'budgetPolicy', request)
    assert durable_dump(profile.path) == before


def test_saved_budget_overrides_survive_file_hosts_but_administrator_fields_remain_authoritative(profile, tmp_path, monkeypatch):
    original = state(profile)
    request = confirmed(profile, 'budgetPolicy', policy_params(original, {'max_model_calls': 8}))
    first = owner_policy.apply_policy(profile.home, 'budgetPolicy', request)
    restarted = OrganizationStore(profile.path, OrganizationSettings.from_config(
        json.loads((profile.home / 'config.yaml').read_text())))
    assert restarted.settings.max_model_calls == 8
    assert restarted._policy_generation == first['receipt']['policyGeneration']
    restarted.reload_profile_configuration(profile.home)
    assert restarted.settings.max_model_calls == 8
    assert owner_policy.apply_policy(profile.home, 'budgetPolicy', request) == first
    pending = confirmed(profile, 'budgetPolicy', policy_params(state(profile), {'max_total_tokens': 16384}), 'pending')
    managed = tmp_path / 'administrator'
    managed.mkdir()
    (managed / 'config.yaml').write_text(json.dumps({'organization': {'max_model_calls': 3}}))
    monkeypatch.setenv('HERMES_MANAGED_DIR', str(managed))
    before = durable_dump(profile.path)
    current = state(profile)
    assert current['configuration']['max_model_calls'] == 3
    assert 'max_model_calls' in current['managedFields']
    assert current['sourceRevision'] != pending['sourceRevision']
    with pytest.raises((ValueError, RuntimeError)):
        owner_policy.preview_policy(profile.home, 'budgetPolicy', policy_params(current, {'max_model_calls': 9}))
    with pytest.raises((ValueError, RuntimeError)):
        owner_policy.apply_policy(profile.home, 'budgetPolicy', pending)
    assert durable_dump(profile.path) == before
    restarted.reload_profile_configuration(profile.home)
    assert restarted.settings.max_model_calls == 3
    assert state(profile)['configuration']['max_model_calls'] == 3
    assert owner_policy.apply_policy(profile.home, 'budgetPolicy', request) == first


def test_same_key_concurrent_apply_returns_one_receipt_and_one_generation(profile):
    from concurrent.futures import ThreadPoolExecutor
    import threading

    original = state(profile)
    request = confirmed(profile, 'budgetPolicy', policy_params(original, {'max_model_calls': 8}))
    barrier = threading.Barrier(2)
    def apply():
        barrier.wait(timeout=10)
        return owner_policy.apply_policy(profile.home, 'budgetPolicy', request)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(apply) for _ in range(2)]
        results = [future.result(timeout=15) for future in futures]
    assert results[0] == results[1]
    assert results[0]['receipt']['policyGeneration'] == original['policyGeneration'] + 1
    assert state(profile)['policyGeneration'] == original['policyGeneration'] + 1
    with closing(sqlite3.connect(profile.path)) as conn:
        assert conn.execute('SELECT count(*) FROM organization_owner_policy_receipts').fetchone()[0] == 1


def test_explicit_unchanged_policy_still_advances_once_and_replay_does_not(profile):
    original = state(profile)
    request = confirmed(profile, 'budgetPolicy', policy_params(original,
        {'max_model_calls': original['configuration']['max_model_calls']}))
    first = owner_policy.apply_policy(profile.home, 'budgetPolicy', request)
    assert first['receipt']['policyGeneration'] == original['policyGeneration'] + 1
    assert owner_policy.apply_policy(profile.home, 'budgetPolicy', request) == first
    assert state(profile)['policyGeneration'] == first['receipt']['policyGeneration']


@pytest.mark.parametrize('case', ['future_as_of', 'expired', 'elapsed_reset', 'long_window', 'missing_reset'])
def test_observation_time_contract_rejects_invalid_new_signal(profile, case):
    now = time.time()
    observation = observation_params(profile)['observation']
    if case == 'future_as_of':
        observation.update(asOf=iso(now + 120), expiresAt=iso(now + 300))
    elif case == 'expired':
        observation.update(asOf=iso(now - 120), expiresAt=iso(now - 1))
    elif case == 'elapsed_reset':
        observation.update(asOf=iso(now - 120), resetAt=iso(now - 1))
    elif case == 'long_window':
        observation.update(asOf=iso(now - 1), expiresAt=iso(now + 604801))
    else:
        observation.pop('resetAt')
    params = observation_params(profile)
    params['observation'] = observation
    before = durable_dump(profile.path)
    with pytest.raises((ValueError, RuntimeError)):
        owner_policy.preview_policy(profile.home, 'budgetObservation', params)
    assert durable_dump(profile.path) == before


@pytest.mark.parametrize('action,configuration', [
    ('budgetPolicy', {'max_cost_usd': 2}),
    ('modelRouting', {**routing(), 'model_assignments': [
        {'agent_id': 'executive', 'mode': 'fixed', 'route_ids': ['economy']}]}),
])
def test_apply_requires_the_canonical_candidate_that_preview_disclosed(profile, action, configuration):
    params = policy_params(state(profile), configuration)
    preview = owner_policy.preview_policy(profile.home, action, params)
    assert preview['configuration'] != configuration
    request = {**params, 'previewVersion': preview['previewVersion'], 'idempotencyKey': 'canonical', 'confirmed': True}
    before = durable_dump(profile.path)
    with pytest.raises((ValueError, RuntimeError), match='canonical'):
        owner_policy.apply_policy(profile.home, action, request)
    assert durable_dump(profile.path) == before
    canonical = {**request, 'configuration': preview['configuration']}
    first = owner_policy.apply_policy(profile.home, action, canonical)
    assert first['receipt']['configuration'] == preview['configuration']
    assert owner_policy.apply_policy(profile.home, action, canonical) == first


@pytest.mark.parametrize('damage', ['json', 'root', 'version', 'action', 'generation', 'source',
                                  'preview', 'configuration', 'missing', 'extra', 'owner', 'organization'])
def test_corrupt_receipt_fails_closed_before_stale_source_or_generation_checks(profile, damage):
    request = confirmed(profile, 'budgetPolicy', policy_params(state(profile), {'max_model_calls': 8}), 'original')
    original = owner_policy.apply_policy(profile.home, 'budgetPolicy', request)
    later = confirmed(profile, 'budgetPolicy', policy_params(state(profile), {'max_model_calls': 9}), 'later')
    owner_policy.apply_policy(profile.home, 'budgetPolicy', later)
    damaged = deepcopy(original)
    changes = {
        'version': lambda: damaged.update(capabilityVersion=True),
        'action': lambda: damaged['receipt'].update(action='modelRouting'),
        'generation': lambda: damaged['receipt'].update(policyGeneration=True),
        'source': lambda: damaged['receipt'].update(sourceRevision='0' * 64),
        'preview': lambda: damaged['receipt'].update(previewVersion='0' * 64),
        'configuration': lambda: damaged['receipt'].update(configuration={'max_model_calls': 1000}),
        'missing': lambda: damaged['receipt'].pop('configuration'),
        'extra': lambda: damaged['receipt'].update(unreviewed=True),
        'owner': lambda: damaged['receipt'].update(ownerId=str(uuid.uuid4())),
        'organization': lambda: damaged['receipt'].update(organizationId=str(uuid.uuid4())),
    }
    if damage in changes:
        changes[damage]()
    payload = '{not JSON' if damage == 'json' else '[]' if damage == 'root' else json.dumps(damaged)
    with closing(sqlite3.connect(profile.path)) as conn:
        conn.execute('DROP TRIGGER organization_owner_policy_receipts_immutable_update')
        conn.execute('UPDATE organization_owner_policy_receipts SET result_json=? WHERE idempotency_key=?',
                     (payload, request['idempotencyKey']))
        conn.commit()
    (profile.home / 'config.yaml').write_text('organization: [unreadable source')
    before = durable_dump(profile.path)
    with pytest.raises(owner_policy.OwnerPolicyError) as rejected:
        owner_policy.apply_policy(profile.home, 'budgetPolicy', request)
    assert rejected.value.reason == 'corrupt'
    assert durable_dump(profile.path) == before


@pytest.mark.parametrize('revision', [True, 0, 2, None])
def test_observation_receipt_requires_exact_integer_revision(profile, revision):
    request = confirmed(profile, 'budgetObservation', observation_params(profile))
    receipt = owner_policy.apply_policy(profile.home, 'budgetObservation', request)
    receipt['receipt']['observationRevision'] = revision
    with closing(sqlite3.connect(profile.path)) as conn:
        conn.execute('DROP TRIGGER organization_owner_policy_receipts_immutable_update')
        conn.execute('UPDATE organization_owner_policy_receipts SET result_json=? WHERE idempotency_key=?',
                     (json.dumps(receipt), request['idempotencyKey']))
        conn.commit()
    before = durable_dump(profile.path)
    with pytest.raises(owner_policy.OwnerPolicyError) as rejected:
        owner_policy.apply_policy(profile.home, 'budgetObservation', request)
    assert rejected.value.reason == 'corrupt'
    assert durable_dump(profile.path) == before


@pytest.mark.parametrize('action', ['modelRouting', 'budgetPolicy', 'budgetObservation'])
def test_apply_refreshes_only_cached_host_and_never_wakes_execution(profile, monkeypatch, action):
    def forbidden_provider(*args, **kwargs):
        raise AssertionError('Owner configuration attempted a model call')
    service = services.OrganizationService(profile.store, home=profile.home, executor=forbidden_provider)
    running = services._Running(profile.claim)
    service._running[profile.claim['id']] = running
    monkeypatch.setitem(services._services, profile.path.resolve(), service)
    forbid_execution(monkeypatch)
    initial = state(profile)
    params = (observation_params(profile, initial) if action == 'budgetObservation' else
              policy_params(initial, routing() if action == 'modelRouting' else {'max_model_calls': 8}))
    request = confirmed(profile, action, params)
    assert not running.cancel.is_set() and not service._wake.is_set()
    original_generation = service.store._policy_generation
    result = owner_policy.apply_policy(profile.home, action, request)
    assert service.store._policy_generation == result['receipt']['policyGeneration']
    assert not service.running and service._thread is None
    assert not service._wake.is_set()
    if action == 'budgetObservation':
        assert service.store._policy_generation == original_generation
        assert not running.cancel.is_set() and not running.fenced
    else:
        assert service.store._policy_generation == original_generation + 1
        assert running.cancel.is_set() and running.fenced
        if action == 'budgetPolicy':
            assert service.settings.max_model_calls == 8
        else:
            assert service.settings.model_assignments[0].agent_id == 'executive'
    before = durable_dump(profile.path)
    assert owner_policy.apply_policy(profile.home, action, request) == result
    assert not service._wake.is_set()
    assert durable_dump(profile.path) == before


def test_new_administrator_route_pin_blocks_old_store_without_disabling_unpinned_cli_assignments(profile, tmp_path, monkeypatch, capsys):
    from eidolon_cli.organization_model_routing_cli import main

    original_generation = profile.store._policy_generation
    administered = routing(model='admin-route')
    managed = tmp_path / 'administrator-routes'
    managed.mkdir()
    (managed / 'config.yaml').write_text(json.dumps({'organization': {
        'model_routes': administered['model_routes']}}))
    monkeypatch.setenv('HERMES_MANAGED_DIR', str(managed))
    before = durable_dump(profile.path)
    with pytest.raises(ValueError, match='managed'):
        profile.store.configure_model_routing(routing(model='override-route'),
            expected_generation=original_generation, idempotency_key='must-not-bypass-pin')
    assert durable_dump(profile.path) == before
    assert profile.store._policy_generation == original_generation
    assert profile.store.settings.model_routes == ()
    allowed = routing(model='admin-route', agent='manager')
    receipt = profile.store.configure_model_routing(allowed, expected_generation=original_generation,
                                                    idempotency_key='allowed-unpinned-assignment')
    assert receipt['policyGeneration'] == original_generation + 1
    assert profile.store.settings.model_routes[0].id == 'admin-route'
    assert profile.store.settings.model_assignments[0].agent_id == 'manager'
    source = (profile.home / 'config.yaml').read_bytes()
    payload = tmp_path / 'cli-routing.json'
    payload.write_text(json.dumps(routing(model='admin-route', agent='reviewer')))
    assert main(['configure', '--file', str(payload), '--expected-generation',
                 str(receipt['policyGeneration']), '--idempotency-key', 'allowed-cli-assignment']) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied['changed'] is True
    assert applied['policyGeneration'] == receipt['policyGeneration'] + 1
    current = state(profile)
    assert current['configuration']['model_routes'] == administered['model_routes']
    assert current['configuration']['model_assignments'][0]['agent_id'] == 'reviewer'
    assert (profile.home / 'config.yaml').read_bytes() == source


def test_postcommit_cached_refresh_failure_does_not_hide_success_or_duplicate_write(profile, monkeypatch):
    service = services.OrganizationService(profile.store, home=profile.home)
    monkeypatch.setitem(services._services, profile.path.resolve(), service)
    calls = []
    def unavailable_refresh(*, wake):
        calls.append(wake)
        raise RuntimeError('Test host cannot refresh its local projection')
    monkeypatch.setattr(service, 'refresh_configuration', unavailable_refresh)
    forbid_execution(monkeypatch)
    initial = state(profile)
    request = confirmed(profile, 'budgetPolicy', policy_params(initial, {'max_model_calls': 8}))
    result = owner_policy.apply_policy(profile.home, 'budgetPolicy', request)
    assert result['receipt']['policyGeneration'] == initial['policyGeneration'] + 1
    assert state(profile)['policyGeneration'] == result['receipt']['policyGeneration']
    assert calls == [False]
    assert not service._wake.is_set() and not service.running
    before = durable_dump(profile.path)
    assert owner_policy.apply_policy(profile.home, 'budgetPolicy', request) == result
    assert durable_dump(profile.path) == before
    assert calls == [False]


@pytest.mark.parametrize('change', [
    {'team': 'new-team'},
    {'tool_grants': ['read_file']},
    {'roster': [{'id': 'new-analyst', 'name': 'New analyst', 'capabilities': ['work.draft']}]},
])
def test_unrelated_budget_apply_cannot_adopt_changed_file_authority(profile, change):
    initial = state(profile)
    source = profile.home / 'config.yaml'
    configuration = json.loads(source.read_text())
    configuration['organization'].update(change)
    source.write_text(json.dumps(configuration))
    before = durable_dump(profile.path)
    current = state(profile)
    assert current['sourceRevision'] != initial['sourceRevision']
    assert current['policyGeneration'] == initial['policyGeneration']
    request = confirmed(profile, 'budgetPolicy', policy_params(current, {'max_model_calls': 8}))
    with pytest.raises(owner_policy.OwnerPolicyError) as rejected:
        owner_policy.apply_policy(profile.home, 'budgetPolicy', request)
    assert rejected.value.reason == 'source_policy_conflict'
    assert durable_dump(profile.path) == before
    assert json.loads(source.read_text()) == configuration
    # The ordinary policy reload owns staffing/grant reconciliation. A fresh
    # narrow intent is available after that existing path adopts the source.
    profile.store.reload_profile_configuration(profile.home)
    reloaded = state(profile)
    fresh = confirmed(profile, 'budgetPolicy', policy_params(reloaded, {'max_model_calls': 8}), 'after-normal-reload')
    applied = owner_policy.apply_policy(profile.home, 'budgetPolicy', fresh)
    assert applied['receipt']['policyGeneration'] == reloaded['policyGeneration'] + 1


def test_reviewed_yaml_metadata_edit_does_not_block_unchanged_authority(profile):
    initial = state(profile)
    source = profile.home / 'config.yaml'
    configuration = json.loads(source.read_text())
    configuration['display'] = {'skin': 'fixture-skin'}
    source.write_text(json.dumps(configuration))
    current = state(profile)
    assert current['sourceRevision'] != initial['sourceRevision']
    assert current['policyGeneration'] == initial['policyGeneration']
    request = confirmed(profile, 'budgetPolicy', policy_params(current, {'max_model_calls': 8}))
    applied = owner_policy.apply_policy(profile.home, 'budgetPolicy', request)
    assert applied['receipt']['policyGeneration'] == initial['policyGeneration'] + 1
    assert json.loads(source.read_text()) == configuration


@pytest.mark.parametrize('scenario', ['prices_to_routing', 'routing_to_budget'])
def test_narrow_apply_rejects_unadopted_cross_surface_file_policy(profile, scenario):
    initial = state(profile)
    source = profile.home / 'config.yaml'
    configuration = json.loads(source.read_text())
    if scenario == 'prices_to_routing':
        configuration['organization']['model_costs'].append({
            'provider': 'named-local', 'model': 'economy',
            'input_usd_per_million': '0.1', 'output_usd_per_million': '0.2'})
        action, candidate = 'modelRouting', routing(mode='auto', agent='manager')
    else:
        configuration['organization'].update(routing())
        action, candidate = 'budgetPolicy', {'max_model_calls': 8}
    source.write_text(json.dumps(configuration))
    before = durable_dump(profile.path)
    current = state(profile)
    assert current['sourceRevision'] != initial['sourceRevision']
    assert current['policyGeneration'] == initial['policyGeneration']
    request = confirmed(profile, action, policy_params(current, candidate))
    assert durable_dump(profile.path) == before
    with pytest.raises(owner_policy.OwnerPolicyError) as rejected:
        owner_policy.apply_policy(profile.home, action, request)
    assert rejected.value.reason == 'source_policy_conflict'
    assert durable_dump(profile.path) == before
    assert json.loads(source.read_text()) == configuration
    profile.store.reload_profile_configuration(profile.home)
    reloaded = state(profile)
    assert reloaded['policyGeneration'] > initial['policyGeneration']
    fresh = confirmed(profile, action, policy_params(reloaded, candidate), 'after-source-adoption')
    applied = owner_policy.apply_policy(profile.home, action, fresh)
    assert applied['receipt']['policyGeneration'] == reloaded['policyGeneration'] + 1
    assert applied['receipt']['configuration'] == candidate
    assert json.loads(source.read_text()) == configuration


def test_re_raising_current_caps_restores_only_headroom_within_unchanged_captured_limits(profile):
    def budget():
        with closing(sqlite3.connect(profile.path)) as conn:
            conn.row_factory = sqlite3.Row
            return budget_view(conn, profile.objective['id'], persisted_settings(conn))

    original_rows = rows(profile.path)
    with closing(sqlite3.connect(profile.path)) as conn:
        conn.row_factory = sqlite3.Row
        captured = dict(conn.execute('SELECT * FROM objective_budgets WHERE objective_id=?',
                                     (profile.objective['id'],)).fetchone())
        created = conn.execute('SELECT created FROM objectives WHERE id=?', (profile.objective['id'],)).fetchone()[0]
    assert captured['max_model_calls'] == 4
    assert captured['max_total_tokens'] == 8192
    assert captured['max_cost_usd'] == '1'
    assert captured['deadline'] == created + 3600
    lower = {'max_model_calls': 2, 'max_total_tokens': 4096,
             'max_cost_usd': '0.5', 'objective_timeout_seconds': 1800}
    params = policy_params(state(profile), lower)
    preview = owner_policy.preview_policy(profile.home, 'budgetPolicy', params)
    warning = next(item for item in preview['warnings'] if item['code'] == 'existing_caps_may_tighten')
    assert warning['values']['fields'] == sorted(lower)
    owner_policy.apply_policy(profile.home, 'budgetPolicy', {
        **params, 'previewVersion': preview['previewVersion'], 'idempotencyKey': 'lower', 'confirmed': True})
    tightened = budget()
    assert tightened['modelCallLimit'] == 2
    assert tightened['tokenLimit'] == 4096
    assert tightened['configuredCostLimitUsd'] == '0.5'
    assert tightened['deadlineTimestamp'] == created + 1800
    raised = {'max_model_calls': 3, 'max_total_tokens': 6144,
              'max_cost_usd': '0.75', 'objective_timeout_seconds': 2700,
              'completion_reserve_model_calls': 2}
    params = policy_params(state(profile), raised)
    preview = owner_policy.preview_policy(profile.home, 'budgetPolicy', params)
    warnings = {item['code']: item['values'] for item in preview['warnings']}
    assert warnings['captured_caps_still_limit_increase']['fields'] == sorted(lower)
    assert warnings['future_defaults_only']['fields'] == ['completion_reserve_model_calls']
    owner_policy.apply_policy(profile.home, 'budgetPolicy', {
        **params, 'previewVersion': preview['previewVersion'], 'idempotencyKey': 'raise-within-capture', 'confirmed': True})
    relaxed = budget()
    assert relaxed['modelCallLimit'] == 3 < captured['max_model_calls']
    assert relaxed['tokenLimit'] == 6144 < captured['max_total_tokens']
    assert relaxed['configuredCostLimitUsd'] == '0.75'
    assert relaxed['deadlineTimestamp'] == created + 2700 < captured['deadline']
    assert tightened['modelCalls'] == relaxed['modelCalls'] == 1
    assert tightened['reservedTokens'] == relaxed['reservedTokens'] == 200
    assert tightened['configuredCostReservedUsd'] == relaxed['configuredCostReservedUsd']
    assert tightened['completionReserve']['captured'] == relaxed['completionReserve']['captured']
    assert relaxed['completionReserve']['captured']['modelCalls'] == 1
    current_rows = rows(profile.path)
    for table in ('objective_budgets', 'objective_completion_reserves', 'organization_model_calls',
                  'organization_model_call_classes', 'objective_usage'):
        assert current_rows[table] == original_rows[table], table


def test_administrator_pin_racing_after_source_validation_rolls_back_adoption_and_receipt(profile, tmp_path, monkeypatch):
    managed = tmp_path / 'racing-administrator'
    managed.mkdir()
    monkeypatch.setenv('HERMES_MANAGED_DIR', str(managed))
    request = confirmed(profile, 'budgetPolicy', policy_params(state(profile), {'max_model_calls': 8}))
    before = durable_dump(profile.path)
    retained = rows(profile.path)
    changes = []
    original = owner_policy._apply_candidate
    pin = {'organization': {'max_model_calls': 3}}
    def pin_after_validation(conn, store, action, canonical, key):
        changes.append(action)
        (managed / 'config.yaml').write_text(json.dumps(pin))
        return original(conn, store, action, canonical, key)
    monkeypatch.setattr(owner_policy, '_apply_candidate', pin_after_validation)
    with pytest.raises(owner_policy.OwnerPolicyError) as rejected:
        owner_policy.apply_policy(profile.home, 'budgetPolicy', request)
    assert rejected.value.reason == 'source_conflict'
    assert changes == ['budgetPolicy']
    assert durable_dump(profile.path) == before
    current = rows(profile.path)
    assert len(current.get('organization_owner_policy_receipts', [])) == len(
        retained.get('organization_owner_policy_receipts', []))
    for table in ('organization_policy', 'objective_budgets', 'objective_completion_reserves',
                  'organization_model_calls', 'organization_model_call_classes', 'requests'):
        assert current[table] == retained[table], table
    # The administrator's independently edited file is not part of the ledger
    # transaction and remains authoritative for the next explicit source review.
    assert json.loads((managed / 'config.yaml').read_text()) == pin


@pytest.mark.parametrize('mode', ['pinned_fields', 'managed_install'])
def test_fully_managed_routing_rejects_same_value_preview_apply_and_direct_store(profile, tmp_path, monkeypatch, mode):
    candidate = {'model_routes': [], 'model_assignments': []}
    initial = state(profile)
    old_preview = owner_policy.preview_policy(profile.home, 'modelRouting', policy_params(initial, candidate))
    if mode == 'managed_install':
        monkeypatch.setenv('HERMES_MANAGED', 'true')
    else:
        managed = tmp_path / 'readonly-routing'
        managed.mkdir()
        (managed / 'config.yaml').write_text(json.dumps({'organization': candidate}))
        monkeypatch.setenv('HERMES_MANAGED_DIR', str(managed))
    before = durable_dump(profile.path)
    current = state(profile)
    assert set(candidate) <= set(current['managedFields'])
    assert {field: current['configuration'][field] for field in candidate} == candidate
    params = policy_params(current, candidate)
    with pytest.raises(owner_policy.OwnerPolicyError) as preview_rejection:
        owner_policy.preview_policy(profile.home, 'modelRouting', params)
    assert preview_rejection.value.reason == 'managed_configuration'
    with pytest.raises(owner_policy.OwnerPolicyError) as apply_rejection:
        owner_policy.apply_policy(profile.home, 'modelRouting', {
            **params, 'previewVersion': old_preview['previewVersion'],
            'idempotencyKey': 'readonly-noop', 'confirmed': True})
    assert apply_rejection.value.reason == 'managed_configuration'
    with pytest.raises(ValueError, match='managed'):
        profile.store.configure_model_routing(candidate, expected_generation=initial['policyGeneration'],
                                              idempotency_key='readonly-direct-noop')
    assert durable_dump(profile.path) == before
    assert profile.store._policy_generation == initial['policyGeneration']


@pytest.mark.parametrize('action', ['read', 'modelRouting', 'budgetPolicy', 'budgetObservation'])
@pytest.mark.parametrize('invalid_yaml', [False, True])
def test_cold_owner_policy_read_and_previews_never_discover_repair_or_adopt(profile, action, invalid_yaml):
    import os
    import subprocess
    import sys

    initial = state(profile)
    params = (observation_params(profile, initial) if action == 'budgetObservation' else
              policy_params(initial, routing() if action == 'modelRouting' else {'max_model_calls': 8}))
    marker = 'SYNTHETIC-PRIVATE-OWNER-POLICY-PARSER-CONTENT-NEVER-LOG'
    (profile.home / '.env').write_bytes(b'UNUSED_SYNTHETIC_VALUE="fixture-only"\r\n')
    if invalid_yaml:
        (profile.home / 'config.yaml').write_bytes(('organization: ["' + marker).encode())

    def files():
        # SQLite's read-only WAL connections may maintain transient locks; the
        # database bytes and every other profile file must remain unchanged.
        return {path.relative_to(profile.home): path.read_bytes() for path in profile.home.rglob('*')
                if path.is_file() and path.name not in {'state.db-wal', 'state.db-shm'}}

    before_sql = durable_dump(profile.path)
    before_files = files()
    before_directories = {path.relative_to(profile.home) for path in profile.home.rglob('*') if path.is_dir()}
    probe = r'''
import json, sys
from pathlib import Path
payload = json.loads(sys.stdin.read())
calls = []
def record(frame, event, arg):
    if event != 'call':
        return
    name = frame.f_code.co_name
    module = frame.f_globals.get('__name__', '')
    if name in {'_discover_providers', 'load_config', '_backup_corrupt_config'}:
        calls.append(module + '.' + name)
    elif module in {'eidolon_cli.organization_store', 'eidolon_cli.organization_service'} and name in {'__init__', 'start', 'get_service'}:
        calls.append(module + '.' + frame.f_code.co_qualname)
    elif module == 'eidolon_cli.organization_policy' and name == '_adopt_policy':
        calls.append(module + '.' + name)
sys.setprofile(record)
try:
    from eidolon_cli.organization_owner_policy import owner_policy, preview_policy
    home = Path(sys.argv[1])
    result = (owner_policy(home, payload['expectedId']) if payload['action'] == 'read' else
              preview_policy(home, payload['action'], payload['params']))
    outcome = {'kind': 'result', 'state': result.get('state'), 'hasPreview': 'previewVersion' in result}
except Exception as error:
    outcome = {'kind': 'error', 'error': type(error).__name__, 'message': str(error)}
finally:
    sys.setprofile(None)
print(json.dumps({**outcome, 'calls': calls}))
'''
    env = dict(os.environ, HERMES_HOME=str(profile.home))
    env.pop('EIDOLON_HOME', None)
    completed = subprocess.run([sys.executable, '-c', probe, str(profile.home)], env=env,
        input=json.dumps({'action': action, 'expectedId': initial['organizationId'], 'params': params}),
        capture_output=True, text=True, timeout=90)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result['calls'] == []
    assert completed.stderr == ''
    assert marker not in completed.stdout + completed.stderr
    if invalid_yaml:
        assert result['kind'] == 'error'
        assert result['error'] == 'ValueError'
        assert 'cannot be read safely' in result['message']
    else:
        assert result['kind'] == 'result'
        assert result['state'] == ('ready' if action == 'read' else None)
        assert result['hasPreview'] is (action != 'read')
    assert durable_dump(profile.path) == before_sql
    assert files() == before_files
    assert {path.relative_to(profile.home) for path in profile.home.rglob('*') if path.is_dir()} == before_directories


@pytest.mark.parametrize('mode', ['managed_install', 'equal_candidate_pin'])
def test_late_write_lock_rolls_back_even_when_adopted_values_match_review(profile, tmp_path, monkeypatch, mode):
    managed = tmp_path / 'late-write-lock'
    managed.mkdir()
    monkeypatch.setenv('HERMES_MANAGED_DIR', str(managed))
    request = confirmed(profile, 'budgetPolicy', policy_params(state(profile), {'max_model_calls': 8}))
    before = durable_dump(profile.path)
    original = owner_policy._apply_candidate
    changes = []
    def lock_after_validation(conn, store, action, canonical, key):
        changes.append(action)
        if mode == 'managed_install':
            (profile.home / '.managed').write_text('true')
        else:
            (managed / 'config.yaml').write_text(json.dumps({'organization': {'max_model_calls': 8}}))
        return original(conn, store, action, canonical, key)
    monkeypatch.setattr(owner_policy, '_apply_candidate', lock_after_validation)
    with pytest.raises(owner_policy.OwnerPolicyError) as rejected:
        owner_policy.apply_policy(profile.home, 'budgetPolicy', request)
    assert rejected.value.reason == 'managed_configuration'
    assert changes == ['budgetPolicy']
    assert durable_dump(profile.path) == before
    if mode == 'managed_install':
        assert (profile.home / '.managed').read_text() == 'true'
    else:
        assert json.loads((managed / 'config.yaml').read_text()) == {'organization': {'max_model_calls': 8}}
