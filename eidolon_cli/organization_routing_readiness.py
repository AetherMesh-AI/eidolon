"""Diagnostic coverage summaries, never route scores or selection authority."""
from collections import defaultdict
import math
from statistics import median


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def evidence_readiness(observations, *, truncated):
    """Summarize only the bounded report window; absence is never success or zero."""
    dimensions = ('routeId', 'provider', 'model', 'reasoning', 'configurationFingerprint', 'stage')
    groups = defaultdict(list)
    for item in observations:
        groups[tuple(item[key] for key in dimensions)].append(item)
    result = []
    for key, items in sorted(groups.items(), key=lambda pair: repr(pair[0])):
        latencies = [item['latencySeconds'] for item in items if _number(item.get('latencySeconds'))]
        usage_count = sum(all(type((item.get('providerReportedUsage') or {}).get(field)) is int
                              and (item.get('providerReportedUsage') or {})[field] >= 0
                              for field in ('inputTokens', 'outputTokens')) for item in items)
        cost_count = sum(_number(item.get('actualCostUsd')) for item in items)
        outcomes = {outcome: sum(item.get('transportOutcome') == outcome for item in items)
                    for outcome in ('response', 'error', 'unobserved')}
        result.append({**dict(zip(dimensions, key)),
            'sampledReservations': len(items),
            'distinctRequests': len({item['requestId'] for item in items}),
            'distinctObjectives': len({item['objectiveId'] for item in items}),
            'transportOutcomes': outcomes,
            'completeProviderUsageSamples': usage_count,
            'missingProviderUsageSamples': len(items) - usage_count,
            'knownBilledCostSamples': cost_count,
            'missingBilledCostSamples': len(items) - cost_count,
            'latency': {'sampleCount': len(latencies), 'missingCount': len(items) - len(latencies),
                        'minimumSeconds': min(latencies) if latencies else None,
                        'medianSeconds': median(latencies) if latencies else None,
                        'maximumSeconds': max(latencies) if latencies else None},
            'rankingReady': False,
            'blockingEvidence': ['independent-downstream-quality-attribution-unavailable',
                                 'task-difficulty-and-comparability-unverified',
                                 *(['billed-cost-incomplete'] if cost_count < len(items) else []),
                                 *(['report-window-truncated'] if truncated else [])]})
    return {'scope': 'bounded-local-reservation-window', 'truncated': truncated,
            'groups': result, 'rankingReady': False, 'selectionChanged': False,
            'notice': 'Transport responses and objective acceptance are not attributable model-quality evidence.'}
