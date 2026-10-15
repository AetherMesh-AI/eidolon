"""Bounded offline benchmark records. Caller-supplied evidence is not attestation.

No file, network, credential, provider, ledger or routing APIs are used here.
Hashes identify already-authorized saved inputs/outputs without carrying content.
"""
from collections import defaultdict
from copy import deepcopy
import math
import re

PHASES = ('generation', 'repair', 'review')
ORIGINS = ('saved_model_output', 'synthetic_fixture')


def _text(value):
    return isinstance(value, str) and 1 <= len(value) <= 128 and all(ord(c) >= 32 for c in value)


def _hash(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1_000_000_000


def validate_record(value):
    """Validate shape and cross-bindings only, never the truth of external claims."""
    fields = {'recordId', 'origin', 'taskSha256', 'inputSha256', 'difficulty', 'stage',
              'routeId', 'provider', 'model', 'reasoning', 'configurationSha256', 'outputSha256', 'contractSha256',
              'criteria', 'verifications', 'completion', 'costs'}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError('Benchmark record requires the exact version-one fields')
    if (any(not _text(value[key]) for key in ('recordId', 'difficulty', 'stage', 'routeId', 'provider', 'model', 'reasoning'))
            or any(not _hash(value[key]) for key in ('taskSha256', 'inputSha256',
                'configurationSha256', 'outputSha256', 'contractSha256'))
            or value['origin'] not in ORIGINS
            or value['completion'] not in ('success', 'failure', 'incomplete')):
        raise ValueError('Invalid benchmark identity, origin or completion')
    criteria = value['criteria']
    if (not isinstance(criteria, list) or not 1 <= len(criteria) <= 12
            or any(not isinstance(c, dict) or set(c) != {'id', 'revision'} or not _text(c['id'])
                   or type(c['revision']) is not int or not 1 <= c['revision'] <= 2**31 for c in criteria)
            or len({c['id'] for c in criteria}) != len(criteria)):
        raise ValueError('Benchmark requires unique exact criterion identities')
    expected = {(c['id'], c['revision']) for c in criteria}
    checks = value['verifications']
    if not isinstance(checks, list) or len(checks) > 12:
        raise ValueError('Invalid benchmark verification list')
    seen = set()
    for check in checks:
        if (not isinstance(check, dict) or set(check) != {'criterionId', 'criterionRevision',
                'verifierId', 'verifierVersion', 'contractSha256', 'outputSha256', 'evidenceSha256', 'result'}
                or not _text(check['criterionId']) or type(check['criterionRevision']) is not int
                or not _text(check['verifierId']) or not _text(check['verifierVersion'])
                or check['contractSha256'] != value['contractSha256']
                or check['outputSha256'] != value['outputSha256']
                or not _hash(check['evidenceSha256']) or check['result'] not in ('pass', 'fail', 'unknown')):
            raise ValueError('Verification must bind exact output, contract and versioned verifier')
        ident = (check['criterionId'], check['criterionRevision'])
        if ident not in expected or ident in seen:
            raise ValueError('Verification criterion is stale, duplicate or unexpected')
        seen.add(ident)
    costs = value['costs']
    if not isinstance(costs, dict) or set(costs) != set(PHASES):
        raise ValueError('Generation, repair and review accounting must be explicit')
    for phase in PHASES:
        cost = costs[phase]
        if (not isinstance(cost, dict) or set(cost) != {'complete', 'attemptCount', 'billedUsd',
                'estimatedUsd', 'pricingVersion'} or type(cost['complete']) is not bool
                or (cost['attemptCount'] is not None and (type(cost['attemptCount']) is not int
                    or not 0 <= cost['attemptCount'] <= 100_000))
                or any(cost[key] is not None and not _number(cost[key]) for key in ('billedUsd', 'estimatedUsd'))
                or (cost['pricingVersion'] is not None and not _text(cost['pricingVersion']))
                or (cost['estimatedUsd'] is not None and cost['pricingVersion'] is None)
                or (cost['complete'] and cost['attemptCount'] is None)):
            raise ValueError('Cost accounting requires explicit completeness, attempts and price provenance')
    return deepcopy(value)


def _reported_success(record):
    checks = record['verifications']
    if record['completion'] == 'incomplete' or len(checks) != len(record['criteria']):
        return None
    if any(check['result'] == 'unknown' for check in checks):
        return None
    return record['completion'] == 'success' and all(check['result'] == 'pass' for check in checks)


def _cost(record, field):
    phases = record['costs'].values()
    if record['completion'] == 'incomplete' or any(not p['complete'] or p[field] is None for p in phases):
        return None
    return sum(p[field] for p in phases)


def evaluate_records(values):
    """Evaluate supplied offline reports, never certify quality or select a route."""
    if not isinstance(values, list) or len(values) > 1000:
        raise ValueError('Offline benchmark accepts at most 1000 records')
    records = [validate_record(value) for value in values]
    if len({r['recordId'] for r in records}) != len(records):
        raise ValueError('Duplicate benchmark record identity')
    groups = defaultdict(list)
    for record in records:
        # Verifier identity is a comparability dimension, never inferred from kind.
        verifier_set = tuple(sorted((v['criterionId'], v['criterionRevision'], v['verifierId'],
                                     v['verifierVersion']) for v in record['verifications']))
        key = (record['origin'], record['taskSha256'], record['inputSha256'], record['difficulty'],
               record['stage'], record['contractSha256'], verifier_set,
               tuple(sorted((c['id'], c['revision']) for c in record['criteria'])))
        groups[key].append(record)
    cohorts = []
    for key, cohort in sorted(groups.items(), key=lambda pair: repr(pair[0])):
        routes = defaultdict(list)
        for record in cohort:
            routes[(record['routeId'], record['provider'], record['model'], record['reasoning'],
                    record['configurationSha256'])].append(record)
        summaries = []
        for (route, provider, model, reasoning, config), samples in sorted(routes.items()):
            outcomes = [_reported_success(r) for r in samples]
            successes = sum(outcome is True for outcome in outcomes)
            pricing_comparable = len({tuple(r['costs'][phase]['pricingVersion'] for phase in PHASES)
                                      for r in samples}) == 1
            costs = {}
            for field in ('billedUsd', 'estimatedUsd'):
                totals = [_cost(r, field) for r in samples]
                complete = all(total is not None for total in totals) and all(o is not None for o in outcomes)
                if field == 'estimatedUsd' and not pricing_comparable:
                    complete = False
                costs[field] = {'knownCompletionSamples': sum(total is not None for total in totals),
                    'totalCompletionCost': sum(totals) if complete else None,
                    'reportedCostPerSuccess': sum(totals) / successes if complete and successes else None}
            summaries.append({'routeId': route, 'provider': provider, 'model': model, 'reasoning': reasoning,
                'configurationSha256': config, 'samples': len(samples),
                'reportedSuccesses': successes, 'reportedFailures': sum(o is False for o in outcomes),
                'unknownOutcomes': sum(o is None for o in outcomes), 'completionCosts': costs,
                'estimatePricingComparable': pricing_comparable,
                'estimatePricingVersions': sorted({p['pricingVersion'] for r in samples for p in r['costs'].values()
                                                   if p['estimatedUsd'] is not None}),
                'attemptsByPhase': {phase: sum(r['costs'][phase]['attemptCount'] for r in samples)
                    if all(r['costs'][phase]['complete'] and r['costs'][phase]['attemptCount'] is not None for r in samples)
                    else None for phase in PHASES}})
        cohorts.append({'origin': key[0], 'taskSha256': key[1], 'inputSha256': key[2],
            'difficulty': key[3], 'stage': key[4], 'contractSha256': key[5],
            'verifiers': [list(v) for v in key[6]], 'expectedCriteria': [list(c) for c in key[7]], 'pairedRoutes': len(routes) > 1, 'routes': summaries})
    return {'schemaVersion': 1, 'evidenceProvenance': 'caller-supplied-offline-unattested',
            'cohorts': cohorts, 'productionEligible': False, 'selectionChanged': False,
            'notice': 'Saved-output labels and reported verification are not independently authenticated. '
                      'Synthetic results test the evaluator only. No ranking or production optimization is authorized.'}
