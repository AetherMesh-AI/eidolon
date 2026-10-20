"""Real ledger reads preserve unknowns, scope, and all persisted state."""
import json

import pytest

from eidolon_cli.organization_routing_observations import routing_observations
from tests.eidolon_cli.test_organization_model_routing import setup, reserve


def test_real_reservations_are_not_invoice_costs_or_success_and_read_is_nonmutating(tmp_path):
    store, objective, claim = setup(tmp_path)
    call = reserve(store, claim)
    with store._connect() as conn:
        before = '\n'.join(conn.iterdump())
    report = routing_observations(store.path)
    item, = report['observations']
    assert item['callId'] == call
    assert item['reservedCostUsd'] is not None
    assert item['actualCostUsd'] is None and item['latencySeconds'] is None
    assert item['physicalRetries'] is None
    assert item['objectiveAccepted'] is False
    assert item['qualityAttribution'] == 'unproven'
    assert report['shadowRanking'] is None and report['selectionChanged'] is False
    assert 'endpointIdentity' not in json.dumps(report)
    with store._connect() as conn:
        assert '\n'.join(conn.iterdump()) == before
    reserve(store, claim)
    report = routing_observations(store.path, limit=1)
    assert report['truncated'] is True and len(report['observations']) == 1


def test_absent_ledger_and_cli_never_provision(tmp_path, monkeypatch, capsys):
    from eidolon_cli.organization_model_routing_cli import main
    home = tmp_path / 'absent'
    monkeypatch.setenv('HERMES_HOME', str(home))
    assert main(['observations']) == 0
    assert json.loads(capsys.readouterr().out)['state'] == 'unconfigured'
    assert not home.exists()
    for limit in (True, 0, 1001):
        with pytest.raises(ValueError):
            routing_observations(home / 'organization' / 'state.db', limit=limit)


def test_measurements_are_bound_immutable_and_late_completion_is_preserved(tmp_path):
    from types import SimpleNamespace
    from eidolon_cli.organization_routing_observations import provider_usage
    store, objective, claim = setup(tmp_path)
    call = reserve(store, claim)
    with store._connect(write=True) as conn:
        conn.execute("UPDATE requests SET token='replacement-token',lease=NULL,status='queued' WHERE id=?", (claim['id'],))
    measured = dict(input_tokens=10, output_tokens=5, elapsed_seconds=0.25, outcome='response')
    assert store.record_model_observation(claim, call, **measured)
    assert not store.record_model_observation(claim, call, **measured)
    with pytest.raises(ValueError, match='immutable'):
        store.record_model_observation(claim, call, **{**measured, 'input_tokens': 11})
    with pytest.raises(ValueError, match='matching'):
        store.record_model_observation({**claim, 'token': 'wrong'}, call, **measured)
    assert provider_usage(SimpleNamespace(usage=SimpleNamespace(prompt_tokens=True, completion_tokens=-1))) == (None, None)
    assert provider_usage(None) == (None, None)
    report = routing_observations(store.path)
    assert report['observations'][0]['providerReportedUsage']['inputTokens'] == 10
    with store._connect(write=True) as conn:
        conn.execute("UPDATE objective_usage SET completed=1 WHERE request_id=? AND token=?", (claim['id'], claim['token']))
    item = routing_observations(store.path)['observations'][0]
    assert item['stageCompleted'] is True
    assert item['objectiveAccepted'] is False


def test_legacy_missing_measurement_table_remains_readable_without_migration(tmp_path):
    store, objective, claim = setup(tmp_path)
    reserve(store, claim)
    with store._connect(write=True) as conn:
        conn.execute('DROP TABLE organization_route_observations')
        before = '\n'.join(conn.iterdump())
    report = routing_observations(store.path)
    assert report['state'] == 'available'
    from eidolon_cli.organization_observation import read_observation
    assert read_observation(store.path.parent.parent)['state'] == 'available'
    assert report['observations'][0]['transportOutcome'] == 'unobserved'
    with store._connect() as conn:
        assert '\n'.join(conn.iterdump()) == before
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='organization_route_observations'").fetchone() is None


def test_optional_measurement_writer_fails_fast_under_contention(tmp_path):
    import sqlite3
    import time
    store, objective, claim = setup(tmp_path)
    call = reserve(store, claim)
    with store._connect(write=True) as conn:
        conn.execute('BEGIN IMMEDIATE')
        start = time.monotonic()
        with pytest.raises(sqlite3.OperationalError, match='locked'):
            store.record_model_observation(claim, call, input_tokens=1, output_tokens=1,
                elapsed_seconds=0.1, outcome='response')
        assert time.monotonic() - start < 2
        conn.rollback()
    item = routing_observations(store.path)['observations'][0]
    assert item['latencySeconds'] is None
    assert item['reservedCostUsd'] is not None


def test_readiness_keeps_unknowns_and_counts_only_bounded_sample(tmp_path):
    store, objective, claim = setup(tmp_path)
    reserve(store, claim)
    reserve(store, claim)
    report = routing_observations(store.path, limit=1)
    readiness = report['evidenceReadiness']
    group, = readiness['groups']
    assert group['sampledReservations'] == group['distinctRequests'] == group['distinctObjectives'] == 1
    assert group['missingBilledCostSamples'] == group['missingProviderUsageSamples'] == 1
    assert group['latency'] == dict(sampleCount=0, missingCount=1, minimumSeconds=None,
                                   medianSeconds=None, maximumSeconds=None)
    assert group['transportOutcomes'] == dict(response=0, error=0, unobserved=1)
    assert 'report-window-truncated' in group['blockingEvidence']
    assert readiness['rankingReady'] is readiness['selectionChanged'] is False


def test_readiness_groups_exact_configuration_and_stage_without_quality_scores():
    from eidolon_cli.organization_routing_readiness import evidence_readiness
    item = dict(routeId='r', provider='p', model='m', reasoning='low', configurationFingerprint='c',
                stage='work.inspect', requestId='q', objectiveId='o', latencySeconds=0,
                providerReportedUsage={'inputTokens': 0, 'outputTokens': 0}, actualCostUsd=None,
                transportOutcome='response', objectiveAccepted=True, stageCompleted=True)
    items = [item, {**item, 'latencySeconds': 2, 'transportOutcome': 'error'},
             {**item, 'configurationFingerprint': 'different'}, {**item, 'stage': 'work.edit'}]
    report = evidence_readiness(items, truncated=False)
    assert len(report['groups']) == 3
    group = next(g for g in report['groups'] if g['sampledReservations'] == 2)
    assert group['distinctRequests'] == group['distinctObjectives'] == 1
    assert group['latency'] == dict(sampleCount=2, missingCount=0, minimumSeconds=0,
                                   medianSeconds=1, maximumSeconds=2)
    assert group['completeProviderUsageSamples'] == 2
    assert group['knownBilledCostSamples'] == 0
    assert group['transportOutcomes'] == dict(response=1, error=1, unobserved=0)
    assert all(g['rankingReady'] is False for g in report['groups'])
    assert 'score' not in json.dumps(report).lower()


def test_readiness_rejects_malformed_counts_and_never_promotes_full_coverage():
    from eidolon_cli.organization_routing_readiness import evidence_readiness
    item = dict(routeId='r', provider='p', model='m', reasoning='low', configurationFingerprint='c',
                stage='s', requestId='q', objectiveId='o', latencySeconds=float('nan'),
                providerReportedUsage={'inputTokens': True, 'outputTokens': -1}, actualCostUsd=True,
                transportOutcome='unobserved')
    group, = evidence_readiness([item], truncated=False)['groups']
    assert group['completeProviderUsageSamples'] == group['knownBilledCostSamples'] == 0
    assert group['latency']['sampleCount'] == 0
    complete = {**item, 'latencySeconds': 1, 'actualCostUsd': 0,
                'providerReportedUsage': {'inputTokens': 1, 'outputTokens': 1}}
    group, = evidence_readiness([complete], truncated=False)['groups']
    assert group['knownBilledCostSamples'] == 1
    assert group['rankingReady'] is False
    assert 'independent-downstream-quality-attribution-unavailable' in group['blockingEvidence']
    assert evidence_readiness([], truncated=False)['groups'] == []
