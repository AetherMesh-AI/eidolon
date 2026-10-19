"""Synthetic, non-activating policy contract proofs."""
from copy import deepcopy
from pathlib import Path

import pytest

from eidolon_cli import organization_setup_subscription_policy as policy


@pytest.fixture
def candidate():
    return {'version': 3, 'mode': 'subscription_calls', 'improveCallLimit': 4,
            'generationCallLimit': 3, 'totalCallLimit': 7, 'requestTimeoutSeconds': 120,
            'connectionId': 'synthetic-connection', 'authorizationEpoch': '00000000-0000-4000-8000-000000000003',
            'modelSlug': 'synthetic-model'}


@pytest.fixture
def snapshot():
    return {'version': 1, 'organizationId': '00000000-0000-4000-8000-000000000001',
            'ownerId': '00000000-0000-4000-8000-000000000002', 'policyGeneration': 4,
            'sourceRevision': 'source-4', 'persistedSourceRevision': 'source-4',
            'authority': 'local_owner', 'scope': 'setup_inheritance', 'workHistoryCount': 0,
            'managedFields': [], 'sharedBinding': None, 'sharedPin': None,
            'allOrganizationConstraints': [], 'capturedExecutionBudgets': [],
            'executionConstraints': {'max_model_calls': 5, 'max_total_tokens': 8000000,
                'max_context_tokens': 128000, 'max_output_tokens': 8000,
                'max_cost_usd': '2.00', 'model_costs': [{'provider': 'synthetic-provider', 'model': 'synthetic-model',
                    'input_usd_per_million': '1', 'output_usd_per_million': '2'}],
                'objective_timeout_seconds': 60},
            'currentCallLimits': {'improveCallLimit': 4, 'generationCallLimit': 3,
                                  'totalCallLimit': 7},
            'attempts': [{'id': 'prior-call', 'operation': 'legacy', 'status': 'failed',
                          'dispatched': True, 'reservedTokens': 4000, 'reservedCost': None}]}


@pytest.fixture
def capability(monkeypatch, tmp_path):
    # Synthetic server registration; no socket, grant or credential-store use.
    from tui_gateway import organization_credential_transport as transport_module
    class Transport:
        closed = False
    transport, generation = Transport(), object()
    monkeypatch.setitem(transport_module._owners, transport, (tmp_path.resolve(), generation))
    return {'transport': transport, 'home': tmp_path, 'transport_generation': generation}


def review(snapshot, candidate, capability, **overrides):
    preview = policy.build_preview(snapshot, candidate)
    args = {'organization_id': snapshot['organizationId'], 'owner_id': snapshot['ownerId'],
            'expected_generation': snapshot['policyGeneration'],
            'source_revision': snapshot['sourceRevision'],
            'reviewed_digest': policy.preview_digest(preview),
            'acknowledgement': deepcopy(policy.ACKNOWLEDGEMENT), **capability}
    args.update(overrides)
    return policy.validate_review(snapshot, candidate, **args)


def test_complete_disclosure_retains_execution_and_debt(snapshot, candidate, capability):
    original = deepcopy(snapshot)
    result = review(snapshot, candidate, capability)
    assert snapshot == original
    assert result['unchangedExecutionConstraints'] == snapshot['executionConstraints']
    assert result['retainedAttempts'] == snapshot['attempts']
    assert result['retainedCallCounts'] == {'legacy': 1, 'improveTeamInput': 0,
                                          'generateTeam': 0, 'total': 1}
    assert result['remainingCalls'] == {'total': 4, 'improveTeamInput': 4, 'generateTeam': 3}
    assert result['effectiveCallLimits']['totalCallLimit'] == 5
    assert result['effectiveLocalTimeoutSeconds'] == 60
    assert {x['guarantee'] for x in result['retiredGuarantees']} == {
        'setup_product_total_tokens', 'max_total_tokens', 'max_context_tokens',
        'max_output_tokens', 'max_cost_usd', 'model_costs'}
    assert all(x['scope'] == 'setup_only' for x in result['retiredGuarantees'])
    assert result['historicalOrigin'] == 'unknown'
    assert 'Historical origins are unknown' in result['acknowledgement']['text']
    assert result['activationSupported'] is False
    assert result['hardTokenGuarantee'] is result['hardUsdGuarantee'] is False
    result['retainedAttempts'][0]['reservedTokens'] = 0
    assert snapshot == original


@pytest.mark.parametrize('field,value,code', [
    ('version', 2, 'snapshot_schema_unsupported'),
    ('workHistoryCount', 1, 'organization_not_empty'),
    ('workHistoryCount', False, 'work_history_invalid'),
    ('authority', 'client_owner', 'authority_or_scope_unknown'),
    ('scope', 'all_work', 'authority_or_scope_unknown'),
    ('persistedSourceRevision', 'stale', 'source_conflict'),
    ('managedFields', ['max_total_tokens'], 'managed_configuration'),
    ('managedFields', ['unknown'], 'managed_configuration'),
    ('sharedBinding', {}, 'shared_scope_unsupported'),
    ('sharedPin', 'retired-scope', 'shared_scope_unsupported'),
    ('allOrganizationConstraints', [{'max_total_tokens': 8000000}], 'enclosing_constraint_or_unknown_scope'),
    ('allOrganizationConstraints', None, 'enclosing_constraint_or_unknown_scope'),
    ('capturedExecutionBudgets', [{}], 'organization_not_empty'),
])
def test_strict_blockers(snapshot, candidate, field, value, code):
    snapshot[field] = value
    with pytest.raises(policy.SubscriptionPolicyError, match=code):
        policy.build_preview(snapshot, candidate)


@pytest.mark.parametrize('status', ['running', 'uncertain', 'unknown'])
def test_attempt_status_fails_closed(snapshot, candidate, status):
    snapshot['attempts'][0]['status'] = status
    with pytest.raises(policy.SubscriptionPolicyError):
        policy.build_preview(snapshot, candidate)


@pytest.mark.parametrize('field,value', [('version', True), ('mode', 'bounded_api'),
    ('totalCallLimit', float('inf')), ('improveCallLimit', True), ('totalCallLimit', -1),
    ('generationCallLimit', 1001), ('requestTimeoutSeconds', 0),
    ('requestTimeoutSeconds', 121), ('requestTimeoutSeconds', 3600),
    ('authorizationEpoch', False), ('modelSlug', ''), ('connectionId', ' secret '),
    ('max_output_tokens', 20)])
def test_candidate_is_closed_and_bounded(candidate, field, value):
    candidate[field] = value
    with pytest.raises(policy.SubscriptionPolicyError):
        policy.validate_candidate(candidate)


def test_no_numeric_default_provenance(snapshot, candidate):
    for total in (4096, 120000, 8000000, 123456):
        snapshot['executionConstraints']['max_total_tokens'] = total
        result = policy.build_preview(snapshot, candidate)
        retired = next(x for x in result['retiredGuarantees'] if x['guarantee'] == 'max_total_tokens')
        assert retired['value'] == total and retired['historicalOrigin'] == 'unknown'
        snapshot['allOrganizationConstraints'] = [{'max_total_tokens': total}]
        with pytest.raises(policy.SubscriptionPolicyError, match='enclosing'):
            policy.build_preview(snapshot, candidate)
        snapshot['allOrganizationConstraints'] = []


@pytest.mark.parametrize('change', ['owner', 'org', 'generation', 'source', 'ack', 'digest'])
def test_review_conflict_is_inert(snapshot, candidate, capability, change):
    before = deepcopy(snapshot)
    overrides = {'owner': {'owner_id': 'other'}, 'org': {'organization_id': 'other'},
        'generation': {'expected_generation': 3}, 'source': {'source_revision': 'stale'},
        'ack': {'acknowledgement': {'version': 1, 'accepted': True}},
        'digest': {'reviewed_digest': '0' * 64}}[change]
    with pytest.raises(policy.SubscriptionPolicyError):
        review(snapshot, candidate, capability, **overrides)
    assert snapshot == before  # Failure has nothing to roll back or partially apply.


def test_changed_debt_or_candidate_invalidates_review(snapshot, candidate, capability):
    digest = policy.preview_digest(policy.build_preview(snapshot, candidate))
    snapshot['attempts'][0]['reservedTokens'] += 1
    with pytest.raises(policy.SubscriptionPolicyError, match='preview_conflict'):
        review(snapshot, candidate, capability, reviewed_digest=digest)
    digest = policy.preview_digest(policy.build_preview(snapshot, candidate))
    candidate['authorizationEpoch'] = '00000000-0000-4000-8000-000000000004'
    with pytest.raises(policy.SubscriptionPolicyError, match='preview_conflict'):
        review(snapshot, candidate, capability, reviewed_digest=digest)


def test_real_capability_required_and_connection_fenced(snapshot, candidate, capability):
    with pytest.raises(policy.SubscriptionPolicyError, match='local_owner'):
        review(snapshot, candidate, capability, transport={'local_owner': True})
    with pytest.raises(policy.SubscriptionPolicyError, match='local_owner'):
        review(snapshot, candidate, capability, transport_generation=object())
    with pytest.raises(policy.SubscriptionPolicyError, match='local_owner'):
        review(snapshot, candidate, capability, home=Path(capability['home']) / 'other')
    capability['transport'].closed = True
    with pytest.raises(policy.SubscriptionPolicyError, match='local_owner'):
        review(snapshot, candidate, capability)


def test_deduplication_and_no_unknown_zero_substitution(snapshot, candidate):
    snapshot['attempts'].append(deepcopy(snapshot['attempts'][0]))
    with pytest.raises(policy.SubscriptionPolicyError, match='attempt_ledger_invalid'):
        policy.build_preview(snapshot, candidate)
    snapshot['attempts'].pop()
    snapshot['attempts'][0]['reservedTokens'] = None
    with pytest.raises(policy.SubscriptionPolicyError, match='attempt_ledger_invalid'):
        policy.build_preview(snapshot, candidate)


def test_no_activation_interface():
    assert not hasattr(policy, 'apply_policy')
    assert not hasattr(policy, 'activate')


@pytest.mark.parametrize('status,dispatched,tokens,cost', [
    ('completed', False, 0, None),
    ('completed', False, 4000, '0.01'),
    ('failed', False, 4000, None),
    ('failed', False, 0, '0'),
    ('failed', False, 0, '0.01'),
    ('failed', True, 0, None),
    ('completed', True, 0, '0'),
])
def test_legacy_attempt_reservation_contradictions(snapshot, candidate, status, dispatched, tokens, cost):
    row = snapshot['attempts'][0]
    row.update(status=status, dispatched=dispatched, reservedTokens=tokens, reservedCost=cost)
    with pytest.raises(policy.SubscriptionPolicyError, match='attempt_ledger_invalid'):
        policy.build_preview(snapshot, candidate)


def test_valid_predispatch_failure_keeps_zero_inference_count(snapshot, candidate):
    row = snapshot['attempts'][0]
    row.update(status='failed', dispatched=False, reservedTokens=0, reservedCost=None)
    result = policy.build_preview(snapshot, candidate)
    assert result['retainedAttempts'] == [row]
    assert result['retainedCallCounts']['total'] == 0


def test_dispatched_legacy_unknown_cost_stays_unknown_and_counted(snapshot, candidate):
    result = policy.build_preview(snapshot, candidate)
    assert result['retainedAttempts'][0]['reservedCost'] is None
    assert result['retainedAttempts'][0]['reservedTokens'] == 4000
    assert result['retainedCallCounts']['total'] == 1


def test_old_rotating_revision_candidate_is_not_reinterpreted(candidate):
    candidate.pop('authorizationEpoch')
    candidate['connectionRevision'] = 1
    with pytest.raises(policy.SubscriptionPolicyError):
        policy.validate_candidate(candidate)
