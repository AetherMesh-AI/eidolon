"""Local pacing is opt-in, durable, atomic and independent of hard admission."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
import sqlite3
import threading
import time

import pytest

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_executor import _prompt
from eidolon_cli.organization_pacing import OrganizationPacingDeferred, PACING_FIELDS, pacing_view
from eidolon_cli.organization_store import OrganizationStore
from tests.eidolon_cli.test_organization_completion_reserve import configured, reserve, usage, backend_request


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def observe(store, objective, percent, *, revision=0, now=None, key=None, **kwargs):
    now = time.time() if now is None else now
    return store.record_budget_observation(objective['id'], remaining_percent=percent,
        as_of=iso(now), expires_at=iso(now + 60), expected_revision=revision,
        idempotency_key=key or f'observation-{revision}', **kwargs)


def pacing(store, objective):
    view = usage(store, objective)
    with store._connect() as conn:
        return pacing_view(conn, objective['id'], store.settings, view)


def test_opt_in_defaults_preserve_admission_and_observation_never_changes_caps(tmp_path):
    store, objective = configured(tmp_path)
    first = store.claim_next()
    observe(store, objective, 0)
    reserve(store, first)
    reserve(store, first)
    view = usage(store, objective)
    assert view['adaptivePacing']['mode'] == 'off'
    assert view['adaptivePacing']['reason'] == 'disabled'
    assert view['modelCallLimit'] == store.settings.max_model_calls
    assert view['tokenLimit'] == store.settings.max_total_tokens
    assert view['modelCalls'] == 2
    assert not OrganizationSettings.from_config({}).adaptive_pacing_enabled


@pytest.mark.parametrize('field,value', [(field, True) for field in PACING_FIELDS if field != 'adaptive_pacing_enabled'] + [
    ('adaptive_pacing_enabled', 'true'), ('pacing_conserve_below_percent', 0),
    ('pacing_conserve_interval_seconds', 0), ('pacing_critical_max_inflight', 0),
    ('pacing_critical_below_percent', 31), ('pacing_critical_interval_seconds', 59),
    ('pacing_critical_max_inflight', 2)])
def test_invalid_configuration_is_rejected(field, value):
    with pytest.raises(ValueError, match='pacing|Critical'):
        OrganizationSettings.from_config({'organization': {field: value}})


def test_ratio_excludes_protected_headroom_and_real_config_reload_fences_stale_hosts(tmp_path):
    store, objective = configured(tmp_path, max_model_calls=10, completion_reserve_model_calls=5)
    claim = store.claim_next()
    for _ in range(4):
        reserve(store, claim)
    before = usage(store, objective)
    assert 'adaptivePacing' not in before
    assert pacing(store, objective)['remainingPercent'] == 20
    assert pacing(store, objective)['remaining']['modelCalls'] == 1
    assert pacing(store, objective)['protectedRemaining']['modelCalls'] == 5
    settings = OrganizationSettings.from_config({'organization': {**{key: value for key, value in asdict(store.settings).items() if key != 'roster'}, 'model_costs': [],
        'model_routes': [], 'model_assignments': [],
        'tool_grants': [], 'read_roots': [], 'project_grants': [], 'projects': [],
        'capabilities': list(store.settings.capabilities), 'adaptive_pacing_enabled': True}})
    peer = OrganizationStore(store.path, settings)
    assert pacing(peer, objective)['mode'] == 'conserve'
    assert not store.heartbeat(claim)
    assert store.claim_next() is None
    assert OrganizationStore(store.path).settings.adaptive_pacing_enabled is True


def test_queue_waits_consume_no_attempts_and_low_priority_completion_is_not_starved(tmp_path, monkeypatch):
    now = [float(int(time.time()))]
    monkeypatch.setattr(time, 'time', lambda: now[0])
    store, objective = configured(tmp_path, adaptive_pacing_enabled=True)
    observe(store, objective, 5, now=now[0])
    first = store.claim_next()
    reserve(store, first)
    ordinary = backend_request(store, objective, 'request.plan')
    assert store.claim_next() is None
    with store._connect() as conn:
        row = conn.execute('SELECT * FROM requests WHERE id=?', (ordinary,)).fetchone()
        assert row['attempts'] == row['execution_count'] == 0
        assert row['status'] == 'queued'
        assert 'Adaptive pacing' in row['reason']
    completion = backend_request(store, objective, 'request.review')
    claim = store.claim_next()
    assert claim['id'] == completion
    reserve(store, claim)
    assert store.defer(claim, 'Never refund admitted completion') is False
    assert usage(store, objective)['modelCalls'] == 2
    assert store.defer(first, 'Never refund admitted ordinary') is False
    now[0] += store.settings.pacing_critical_interval_seconds
    next_claim = store.claim_next()
    assert next_claim['id'] == ordinary
    assert next_claim['attempts'] == 1
    assert pacing(store, objective)['deferUntil'] is None


def test_two_preclaimed_instances_atomically_admit_one_stage_and_inner_calls_keep_original_anchor(tmp_path, monkeypatch):
    now = [float(int(time.time()))]
    monkeypatch.setattr(time, 'time', lambda: now[0])
    store, objective = configured(tmp_path, adaptive_pacing_enabled=True)
    claims = [store.claim_next()]
    backend_request(store, objective, 'request.plan')
    claims.append(store.claim_next())
    assert all(claims)
    observe(store, objective, 20, now=now[0])
    peer = OrganizationStore(store.path)
    barrier = threading.Barrier(2)

    def admit(index):
        barrier.wait(timeout=10)
        try:
            return reserve([store, peer][index], claims[index])
        except OrganizationPacingDeferred as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(admit, range(2)))
    winners = [index for index, row in enumerate(results) if isinstance(row, str)]
    assert len(winners) == 1
    winner = winners[0]
    loser = 1 - winner
    wait = results[loser]
    assert isinstance(wait, OrganizationPacingDeferred)
    first_deadline = pacing(store, objective)['deferUntil']
    now[0] += 10
    reserve(store, claims[winner])
    assert pacing(store, objective)['deferUntil'] == first_deadline
    assert store.defer(claims[loser], str(wait), defer_until=wait.defer_until)
    with store._connect() as conn:
        row = conn.execute('SELECT * FROM requests WHERE id=?', (claims[loser]['id'],)).fetchone()
        assert row['attempts'] == 0 and row['execution_count'] == 1
        assert conn.execute('SELECT count(*) FROM objective_usage WHERE request_id=?', (row['id'],)).fetchone()[0] == 0
    assert usage(store, objective)['modelCalls'] == 2


def test_expiry_and_reset_preserve_low_signal_with_scope_isolation_and_owner_refresh(tmp_path, monkeypatch):
    now = [float(int(time.time()))]
    monkeypatch.setattr(time, 'time', lambda: now[0])
    store, objective = configured(tmp_path, adaptive_pacing_enabled=True)
    other = store.create_objective('Separate capacity', idempotency_key='other')
    receipt = observe(store, objective, 5, now=now[0], reset_at=iso(now[0] + 90))
    before = usage(store, objective)
    assert pacing(store, other)['mode'] == 'normal'
    now[0] += 61
    expired = pacing(OrganizationStore(store.path), objective)
    assert expired['mode'] == 'critical'
    assert expired['reason'] == 'stale_observation'
    assert expired['observation']['status'] == 'expired'
    now[0] += 30
    reset = pacing(store, objective)
    assert reset['observation']['status'] == 'reset_unconfirmed'
    assert reset['remainingPercent'] == 5
    assert observe(store, objective, 5, now=now[0] - 91, reset_at=iso(now[0] - 1)) == receipt
    observe(store, objective, 100, revision=1, now=now[0])
    after = usage(store, objective)
    assert after['adaptivePacing']['mode'] == 'normal'
    for key in ('modelCalls', 'reservedTokens', 'modelCallLimit', 'tokenLimit', 'deadlineAt', 'completionReserve'):
        assert after[key] == before[key]
    with store._write() as conn, pytest.raises(sqlite3.IntegrityError, match='immutable'):
        conn.execute('DELETE FROM organization_budget_observations')


@pytest.mark.parametrize('changes', [
    {'remaining_percent': True}, {'remaining_percent': float('nan')}, {'remaining_percent': 101},
    {'as_of': '2026-01-01'}, {'as_of': 'future'},
    {'expires_at': 'bad'}, {'expected_revision': True}, {'expected_revision': 1},
])
def test_observation_validation_never_changes_budget(tmp_path, monkeypatch, changes):
    store, objective = configured(tmp_path)
    now = float(int(time.time()))
    monkeypatch.setattr(time, 'time', lambda: now)
    if changes.get('as_of') == 'future':
        changes = {**changes, 'as_of': iso(now + 30)}
    args = dict(remaining_percent=10, as_of=iso(now), expires_at=iso(now + 60),
                expected_revision=0, idempotency_key='key')
    with pytest.raises(ValueError):
        store.record_budget_observation(objective['id'], **{**args, **changes})
    assert pacing(store, objective)['observation'] is None
    assert usage(store, objective)['modelCalls'] == 0


def test_context_reaches_manager_prompt_without_expanding_peer_message_context(tmp_path):
    store, objective = configured(tmp_path, adaptive_pacing_enabled=True)
    claim = store.claim_next()
    observe(store, objective, 20)
    context = store.context(claim)
    submitted = json.loads(_prompt(claim, context, claim['type']).split('Submitted context:\n', 1)[1])
    view = submitted['context']['budget']['adaptivePacing']
    assert view['mode'] == 'conserve'
    assert view['remaining'] == usage(store, objective)['completionReserve']['ordinaryRemaining']
    peer = json.loads(_prompt({'type': 'request.message'}, {**context, 'conversation': {}}, 'request.message').split('Submitted context:\n', 1)[1])
    assert 'budget' not in peer['context']


def test_completion_slots_and_other_objectives_do_not_consume_local_ordinary_capacity(tmp_path, monkeypatch):
    now = [float(int(time.time()))]
    monkeypatch.setattr(time, 'time', lambda: now[0])
    store, objective = configured(tmp_path, adaptive_pacing_enabled=True,
                                 pacing_conserve_interval_seconds=5, pacing_critical_interval_seconds=10)
    first = store.claim_next()
    observe(store, objective, 5, now=now[0])
    reserve(store, first)
    assert store.fail(first, 'Retained stage ended')
    ordinary = backend_request(store, objective, 'request.plan')
    completion = backend_request(store, objective, 'request.review')
    claim = store.claim_next()
    assert claim['id'] == completion
    reserve(store, claim)
    now[0] += 10
    admitted = store.claim_next()
    assert admitted['id'] == ordinary
    assert reserve(store, admitted).startswith('modelcall_')
    assert store.fail(admitted, 'Retained ordinary stage ended')
    other = store.create_objective('Independent allowance', idempotency_key='other')
    next_claim = store.claim_next()
    assert next_claim['objective_id'] == other['id']
    assert pacing(store, other)['mode'] == 'normal'


def test_stale_high_replacement_and_conflicting_replay_cannot_release_retained_low_signal(tmp_path, monkeypatch):
    now = [float(int(time.time()))]
    monkeypatch.setattr(time, 'time', lambda: now[0])
    store, objective = configured(tmp_path, adaptive_pacing_enabled=True)
    observe(store, objective, 5, now=now[0])
    with pytest.raises(ValueError, match='different observation'):
        observe(store, objective, 100, now=now[0])
    now[0] += 61
    with pytest.raises(ValueError, match='must be fresh'):
        observe(store, objective, 100, now=now[0] - 61, revision=1)
    with pytest.raises(ValueError, match='must be fresh'):
        observe(store, objective, 100, now=now[0], revision=1, reset_at=iso(now[0]))
    assert pacing(store, objective)['mode'] == 'critical'
    assert pacing(store, objective)['observation']['revision'] == 1


def test_default_off_preserves_legacy_projection_and_pacing_clock_cannot_invalidate_replacement(tmp_path, monkeypatch):
    from tests.eidolon_cli.test_organization_replacement import submit
    now = [float(int(time.time()))]
    monkeypatch.setattr(time, 'time', lambda: now[0])
    store, objective = configured(tmp_path)
    assert 'adaptivePacing' not in usage(store, objective)
    before = store.snapshot()
    now[0] += 1
    assert store.snapshot() == before
    settings = replace(store.settings, adaptive_pacing_enabled=True)
    store.reload_configuration(settings)
    now[0] += settings.objective_timeout_seconds
    draft = store.preview_replacement(objective['id'])
    now[0] += 10
    later = store.preview_replacement(objective['id'])
    assert draft['sourceVersion'] == later['sourceVersion']
    assert draft['sourceUsage']['adaptivePacing']['evaluatedAt'] != later['sourceUsage']['adaptivePacing']['evaluatedAt']
    result = submit(store, draft)
    assert result['replacesObjectiveId'] == objective['id']


def test_default_off_context_and_direct_executor_preserve_legacy_prompt(tmp_path):
    store, objective = configured(tmp_path)
    claim = store.claim_next()
    context = store.context(claim)
    assert 'budget' not in context
    ordinary = _prompt(claim, context, claim['type'])
    injected = _prompt(claim, {**context, 'budget': {'modelCalls': 1, 'tokenLimit': 4096}}, claim['type'])
    assert ordinary == injected
    submitted = json.loads(ordinary.split('Submitted context:\n', 1)[1])
    assert 'budget' not in submitted['context']
