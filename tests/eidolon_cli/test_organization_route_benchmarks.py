"""Synthetic records exercise offline accounting, never real model quality."""
from copy import deepcopy

import pytest

from eidolon_cli.organization_route_benchmarks import evaluate_records, validate_record


def sample(ident='one', route='route-a', origin='synthetic_fixture'):
    return dict(recordId=ident, origin=origin, taskSha256='a'*64, inputSha256='b'*64,
                difficulty='small', stage='work.edit', routeId=route, provider='fixture', model='fixture-model',
                reasoning='standard', configurationSha256='c'*64,
                outputSha256='d'*64, contractSha256='e'*64, criteria=[{'id': 'test', 'revision': 1}],
                verifications=[dict(criterionId='test', criterionRevision=1, verifierId='pytest-runtime',
                    verifierVersion='v1', contractSha256='e'*64, outputSha256='d'*64,
                    evidenceSha256='f'*64, result='pass')], completion='success',
                costs={phase: dict(complete=True, attemptCount=1, billedUsd=2, estimatedUsd=3,
                                   pricingVersion='owner-price-v1') for phase in ('generation', 'repair', 'review')})


def test_complete_cost_counts_failed_work_repair_and_review_without_promoting_routing():
    success, failure = sample(), sample('two')
    failure['completion'] = 'failure'
    failure['verifications'][0]['result'] = 'fail'
    report = evaluate_records([success, failure])
    row = report['cohorts'][0]['routes'][0]
    assert row['reportedSuccesses'] == row['reportedFailures'] == 1
    assert row['completionCosts']['billedUsd'] == dict(knownCompletionSamples=2, totalCompletionCost=12,
                                                      reportedCostPerSuccess=12)
    assert row['completionCosts']['estimatedUsd']['reportedCostPerSuccess'] == 18
    assert report['productionEligible'] is report['selectionChanged'] is False
    assert report['evidenceProvenance'] == 'caller-supplied-offline-unattested'


@pytest.mark.parametrize('phase', ['generation', 'repair', 'review'])
@pytest.mark.parametrize('missing', ['unknown', 'incomplete'])
def test_missing_downstream_cost_is_never_zero(phase, missing):
    item = sample()
    if missing == 'unknown': item['costs'][phase]['billedUsd'] = None
    else: item['costs'][phase]['complete'] = False
    row = evaluate_records([item])['cohorts'][0]['routes'][0]
    assert row['completionCosts']['billedUsd']['reportedCostPerSuccess'] is None
    assert row['completionCosts']['billedUsd']['totalCompletionCost'] is None


def test_observed_zero_cost_and_no_attempt_phase_are_explicit():
    item = sample()
    for cost in item['costs'].values():
        cost.update(attemptCount=0, billedUsd=0, estimatedUsd=0)
    row = evaluate_records([item])['cohorts'][0]['routes'][0]
    assert row['completionCosts']['billedUsd']['reportedCostPerSuccess'] == 0


@pytest.mark.parametrize('field', ['origin', 'inputSha256', 'taskSha256', 'difficulty', 'stage', 'contractSha256'])
def test_comparability_and_origin_boundaries_are_not_pooled(field):
    first, other = sample(), sample('two', 'route-b')
    other[field] = {'origin': 'saved_model_output', 'inputSha256': '1'*64, 'taskSha256': '2'*64,
                    'difficulty': 'large', 'stage': 'review', 'contractSha256': '3'*64}[field]
    other['verifications'][0]['contractSha256'] = other['contractSha256']
    result = evaluate_records([first, other])
    assert len(result['cohorts']) == 2
    assert not any(c['pairedRoutes'] for c in result['cohorts'])


def test_paired_routes_require_same_versioned_verifier_and_exact_task():
    first, other = sample(), sample('two', 'route-b')
    report = evaluate_records([first, other])
    assert len(report['cohorts']) == 1 and report['cohorts'][0]['pairedRoutes']
    other['verifications'][0]['verifierVersion'] = 'v2'
    assert len(evaluate_records([first, other])['cohorts']) == 2


@pytest.mark.parametrize('change', ['unknown', 'missing', 'incomplete'])
def test_unknown_quality_prevents_cost_per_success(change):
    item = sample()
    if change == 'unknown': item['verifications'][0]['result'] = 'unknown'
    if change == 'missing': item['verifications'] = []
    if change == 'incomplete': item['completion'] = 'incomplete'
    # Missing verifier sets form a separate incomparable cohort; no pooling.
    incomplete = next(r for c in evaluate_records([item])['cohorts'] for r in c['routes'])
    assert incomplete['unknownOutcomes'] == 1
    assert incomplete['completionCosts']['billedUsd']['reportedCostPerSuccess'] is None


@pytest.mark.parametrize('attack', ['hash', 'criterion', 'version', 'duplicate', 'nan', 'boolean', 'negative',
                                   'price', 'attempt', 'unknown_field', 'extra_phase'])
def test_malformed_or_stale_records_fail_closed(attack):
    item = sample()
    if attack == 'hash': item['verifications'][0]['outputSha256'] = '0'*64
    if attack == 'criterion': item['verifications'][0]['criterionRevision'] = 2
    if attack == 'version': item['verifications'][0]['verifierVersion'] = ''
    if attack == 'duplicate': item['verifications'] *= 2
    if attack == 'nan': item['costs']['review']['billedUsd'] = float('nan')
    if attack == 'boolean': item['costs']['review']['billedUsd'] = True
    if attack == 'negative': item['costs']['review']['billedUsd'] = -1
    if attack == 'price': item['costs']['review']['pricingVersion'] = None
    if attack == 'attempt': item['costs']['review']['attemptCount'] = None
    if attack == 'unknown_field': item['providerApiKey'] = 'never-accepted'
    if attack == 'extra_phase': item['costs']['extra'] = item['costs']['review']
    with pytest.raises(ValueError): validate_record(item)


def test_bounded_records_unique_ids_and_input_copy():
    item = sample(); before = deepcopy(item)
    validated = validate_record(item)
    validated['costs']['review']['billedUsd'] = 999
    assert item == before
    evaluate_records([item])
    assert item == before
    with pytest.raises(ValueError): evaluate_records([item, item])
    with pytest.raises(ValueError): evaluate_records([item] * 1001)
    assert evaluate_records([])['cohorts'] == []


def test_mixed_estimate_versions_do_not_form_a_cost_per_success():
    first, other = sample(), sample('two')
    other['costs']['review']['pricingVersion'] = 'owner-price-v2'
    row = evaluate_records([first, other])['cohorts'][0]['routes'][0]
    assert row['estimatePricingComparable'] is False
    assert row['completionCosts']['estimatedUsd']['reportedCostPerSuccess'] is None
    assert row['completionCosts']['billedUsd']['reportedCostPerSuccess'] == 6
    assert row['estimatePricingVersions'] == ['owner-price-v1', 'owner-price-v2']


def test_expected_criterion_sets_are_not_pooled_when_checks_are_missing():
    first, other = sample(), sample('two', 'route-b')
    first['verifications'] = other['verifications'] = []
    other['criteria'].append({'id': 'additional', 'revision': 1})
    result = evaluate_records([first, other])
    assert len(result['cohorts']) == 2
    assert not any(c['pairedRoutes'] for c in result['cohorts'])
