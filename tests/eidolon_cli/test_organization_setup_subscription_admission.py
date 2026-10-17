"""Real policy/ledger/lease/sender integration, synthetic vault and TLS only."""
from dataclasses import replace
import json
import os
import pickle
import threading
import time

import pytest

from eidolon_cli import organization_setup_subscription_admission as admission
from eidolon_cli import organization_team_admission as team_admission
from eidolon_cli import organization_team_budget as budget
from eidolon_cli import organization_team_setup as team
from eidolon_cli.organization_owner_policy import OwnerPolicyError
from eidolon_cli.organization_setup_subscription_store import SubscriptionStore
from tui_gateway import methods_organization_setup_subscription as gateway
from tests.eidolon_cli.test_organization_setup_subscription_policy_integration import ledger, apply
from tests.eidolon_cli.test_organization_setup_subscription_transport import Vault, install, wire, event, response


@pytest.fixture
def activated(ledger, monkeypatch):
    if os.name != 'posix':
        pytest.skip('Synthetic native-vault primitive fixture requires POSIX; not production support')
    binding = ledger.evidence.binding
    store = SubscriptionStore(ledger.home, backend=Vault())
    store.connect_grant(binding, access_token='synthetic-access', refresh_token='synthetic-refresh',
        id_token='synthetic-id', expires_at=time.time() + 3600,
        granted_scopes=('chatgpt.tokens.use.direct', 'openid', 'resource.invoke'),
        auth_attempt_id='synthetic-auth', expected_revision=0,
        identity_evidence={'subject': binding.account_id, 'audience': binding.issued_client_id,
            'nonce': 'synthetic-consent', 'authTime': None, 'authorizedParty': None})
    epoch = store.grant_descriptor(binding, 1)['authorizationEpoch']
    ledger.candidate['authorizationEpoch'] = epoch
    catalog = {**ledger.evidence.catalog_evidence, 'authorizationEpoch': epoch}
    ledger.evidence = replace(ledger.evidence, store=store, epoch=epoch, catalog_evidence=catalog)
    ledger.store = store

    def preflight(transport, home, candidate, *, inference=False):
        assert transport is ledger.transport and home == ledger.home
        assert candidate['authorizationEpoch'] == epoch
        revision = team._current(ledger.conn, ledger.home, ledger.identity['organizationId'])[3]
        catalog = {**ledger.evidence.catalog_evidence, 'sourceRevision': revision}
        lease = store.lease_access_token(binding, 1, owner_generation=ledger.generation,
            owner_guard=ledger.evidence.cheap_guard, cancel=threading.Event()) if inference else None
        return replace(ledger.evidence, catalog_evidence=catalog, lease=lease,
                       release=lease.close if lease else lambda: None)
    monkeypatch.setattr(gateway, 'subscription_preflight', preflight)
    apply(ledger)
    return ledger


def request(ledger, key='inference', *, improve=True):
    revision = team._current(ledger.conn, ledger.home, ledger.identity['organizationId'])[3]
    value = {'expectedOrganizationId': ledger.identity['organizationId'], 'sourceRevision': revision,
             'jobs': 'Produce useful reports', 'goals': 'Clear decisions', 'namingStyle': '',
             'idempotencyKey': key}
    return {**value, 'field': 'jobs'} if improve else value


def infer(ledger, value):
    operation = team_admission.improve_team_input if 'field' in value else team_admission.generate_team
    return operation(ledger.home, value, subscription_transport=ledger.transport)


def state(ledger):
    return [dict(row) for row in ledger.conn.execute('SELECT * FROM organization_team_generations ORDER BY idempotency_key')]


@pytest.mark.macos_only
def test_real_subscription_improve_and_generate_are_review_only_and_replay_is_zero_send(activated, monkeypatch):
    ledger = activated
    first = request(ledger)
    socket, connects = install(monkeypatch, wire(event(response=response())))
    result = infer(ledger, first)
    assert result['suggestion'] == 'Ready'
    assert result['generationAllowance']['setupUsage']['totalTokens'] == 3
    assert result['generationAllowance']['setupUsage']['costUsd'] is None
    assert len(connects) == 1 and b'POST /v1/responses' in b''.join(socket.sent)
    assert infer(ledger, first) == result
    assert len(connects) == 1
    second = request(ledger, 'generate', improve=False)
    proposal = team.presets()[0]['team']
    socket, connects = install(monkeypatch, wire(event(response=response(json.dumps(proposal)))))
    preview = infer(ledger, second)
    assert preview['team'] == proposal and preview['previewVersion']
    assert ledger.conn.execute('SELECT count(*) FROM organization_team_setup').fetchone()[0] == 0
    assert ledger.conn.execute('SELECT count(*) FROM objectives').fetchone()[0] == 0
    assert all(row['dispatched'] == 1 and row['status'] == 'completed' for row in state(ledger))
    assert budget.operation_counts(ledger.conn)['total'] == 2
    assert budget.budget_view(ledger.conn, ledger.settings)['reservedTokens'] is None
    with pytest.raises(OwnerPolicyError, match='idempotency_conflict'):
        infer(ledger, {**second, 'jobs': 'Different'})
    assert len(connects) == 1


@pytest.mark.macos_only
@pytest.mark.parametrize('mode', ['missing_usage', 'invalid_output', 'unknown'])
def test_terminal_and_unknown_usage_remain_distinct_without_refund_or_retry(activated, monkeypatch, mode):
    ledger = activated
    if mode == 'unknown':
        data = wire(event('response.created', response={'id': 'resp_synthetic', 'status': 'in_progress', 'output': []}))
    else:
        value = response('not JSON' if mode == 'invalid_output' else '{"suggestion":"Ready","assumptions":[]}')
        if mode == 'missing_usage':
            value.pop('usage')
        data = wire(event(response=value))
    socket, connects = install(monkeypatch, data)
    value = request(ledger)
    if mode == 'missing_usage':
        result = infer(ledger, value)
        assert result['generationAllowance']['setupUsage']['tokenStatus'] == 'unknown'
        assert result['generationAllowance']['setupUsage']['totalTokens'] is None
    else:
        with pytest.raises(Exception):
            infer(ledger, value)
        assert state(ledger)[0]['status'] == ('uncertain' if mode == 'unknown' else 'failed')
        with pytest.raises(OwnerPolicyError, match='generation_'):
            infer(ledger, value)
    assert len(connects) == 1 and state(ledger)[0]['dispatched'] == 1
    outcomes = ledger.conn.execute('SELECT count(*) FROM organization_setup_subscription_outcomes').fetchone()[0]
    assert outcomes == (0 if mode == 'unknown' else 1)
    if mode == 'unknown':
        with pytest.raises(Exception):
            infer(ledger, request(ledger, 'new-intent'))
        assert len(connects) == 1
    assert budget.budget_view(ledger.conn, ledger.settings)['setupUsage']['costUsd'] is None


@pytest.mark.macos_only
def test_retired_owner_loses_output_but_authoritative_terminal_usage_survives(activated, monkeypatch):
    ledger = activated
    socket, _ = install(monkeypatch, wire(event(response=response())))
    original = admission._outcome_job
    def retire_after_commit(*args):
        result = original(*args)
        ledger.transport.closed = True
        return result
    monkeypatch.setattr(admission, '_outcome_job', retire_after_commit)
    with pytest.raises(Exception):
        infer(ledger, request(ledger))
    row = state(ledger)[0]
    assert row['dispatched'] == 1 and row['status'] == 'failed' and row['result_json'] is None
    raw = ledger.conn.execute('SELECT outcome_json FROM organization_setup_subscription_outcomes').fetchone()[0]
    assert json.loads(raw)['usage']['total_tokens'] == 3
    assert 'suggestion' not in raw and 'synthetic-access' not in raw


@pytest.mark.macos_only
def test_ambiguous_dispatch_commit_never_issues_send_or_replays(activated, monkeypatch):
    ledger = activated
    socket, connects = install(monkeypatch, wire(event(response=response())))
    original = admission._dispatch_job
    def lost_ack(*args):
        assert original(*args) is True
        raise OSError('synthetic lost local commit acknowledgement')
    monkeypatch.setattr(admission, '_dispatch_job', lost_ack)
    value = request(ledger)
    with pytest.raises(Exception):
        infer(ledger, value)
    assert not socket.sent and len(connects) == 1
    assert state(ledger)[0]['dispatched'] == 1 and state(ledger)[0]['status'] == 'uncertain'
    for candidate in (value, request(ledger, 'another-key')):
        with pytest.raises(Exception):
            infer(ledger, candidate)
    assert len(connects) == 1


@pytest.mark.macos_only
def test_ordinary_api_reservation_and_missing_owner_cannot_spend_v3(activated):
    ledger = activated
    value = request(ledger)
    with pytest.raises(OwnerPolicyError, match='local_owner'):
        team_admission.improve_team_input(ledger.home, value)
    with pytest.raises(OwnerPolicyError, match='dedicated_admission'):
        team_admission._reserve(ledger.home, value, provider='fixture', model='fixture', input_limit=1, output_limit=1)
    assert state(ledger) == []


def test_one_use_permit_and_abandoned_accounting_slot_remain_bounded(monkeypatch):
    permit = admission._Permit(admission._PERMIT_KEY)
    assert permit.consume() is True and permit.consume() is False
    with pytest.raises(TypeError):
        pickle.dumps(permit)
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(admission, '_PERSISTENCE_SLOTS', slots)
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()
    def stalled():
        entered.set()
        assert release.wait(10)
        exited.set()
        return True
    try:
        assert admission._persist(stalled, (), time.monotonic() + 2) is False
        assert entered.is_set()
        assert slots.acquire(blocking=False) is False
        assert admission._persist(lambda: True, (), time.monotonic() + 5) is False
    finally:
        release.set()
    assert exited.wait(10)
    # Synchronize on the slot, not a scheduling-sensitive sleep or negative wait.
    assert slots.acquire(timeout=10)
    slots.release()
    assert admission._persist(lambda: True, (), time.monotonic() + 5) is True


@pytest.mark.macos_only
def test_known_sqlite_dispatch_refusal_leaves_safe_failed_intent_not_uncertain(activated, monkeypatch):
    from eidolon_cli import organization_team_generation as generation
    ledger = activated
    socket, _ = install(monkeypatch, wire(event(response=response())))
    complete = generation.complete_subscription
    def locked(prepared):
        ledger.conn.execute('BEGIN IMMEDIATE')
        try:
            return complete(prepared)
        finally:
            ledger.conn.rollback()
    monkeypatch.setattr(generation, 'complete_subscription', locked)
    with pytest.raises(Exception):
        infer(ledger, request(ledger))
    assert not socket.sent
    assert state(ledger)[0]['status'] == 'failed' and state(ledger)[0]['dispatched'] == 0
    monkeypatch.setattr(generation, 'complete_subscription', complete)
    install(monkeypatch, wire(event(response=response())))
    assert infer(ledger, request(ledger, 'new-explicit-intent'))['suggestion'] == 'Ready'
    assert budget.operation_counts(ledger.conn)['total'] == 1


@pytest.mark.macos_only
def test_completed_replay_requires_current_owner_and_grant_without_new_send(activated, monkeypatch):
    ledger = activated
    _, connects = install(monkeypatch, wire(event(response=response())))
    value = request(ledger)
    infer(ledger, value)
    class Foreign:
        closed = False
    with pytest.raises(Exception):
        team_admission.improve_team_input(ledger.home, value, subscription_transport=Foreign())
    ledger.store.local_disconnect(ledger.evidence.binding, expected_revision=1,
        owner_guard=ledger.evidence.cheap_guard, cancel=threading.Event())
    with pytest.raises(Exception):
        infer(ledger, value)
    assert len(connects) == 1 and state(ledger)[0]['status'] == 'completed'


@pytest.mark.macos_only
def test_new_unknown_managed_authority_after_dispatch_blocks_each_write(activated, monkeypatch):
    from eidolon_cli import managed_scope
    ledger = activated
    socket, _ = install(monkeypatch, wire(event(response=response())))
    original = admission._dispatch_job
    def changed(*args):
        assert original(*args) is True
        monkeypatch.setattr(managed_scope, 'load_managed_config', lambda: {'organization': {'unknown_budget': 9}})
        return True
    monkeypatch.setattr(admission, '_dispatch_job', changed)
    with pytest.raises(Exception):
        infer(ledger, request(ledger))
    assert not socket.sent
    assert state(ledger)[0]['dispatched'] == 1 and state(ledger)[0]['status'] == 'uncertain'


@pytest.mark.macos_only
def test_catalog_expiry_after_admitted_terminal_does_not_discard_valid_long_result(activated, monkeypatch):
    ledger = activated
    ledger.evidence.catalog_evidence['expiresAt'] = time.time() + 60
    install(monkeypatch, wire(event(response=response())))
    original, wall_clock = admission._outcome_job, time.time
    def later(*args):
        result = original(*args)
        monkeypatch.setattr(time, 'time', lambda: wall_clock() + 61)
        return result
    monkeypatch.setattr(admission, '_outcome_job', later)
    assert infer(ledger, request(ledger))['suggestion'] == 'Ready'
    assert state(ledger)[0]['status'] == 'completed'


def _process_infer(home, binding, vault_values, candidate, value, entered, release, reports, pause):
    """Spawned process imports real policy/admission; only native and TLS are fake."""
    from pathlib import Path
    from unittest.mock import patch
    from eidolon_cli import organization_setup_subscription_policy as policy
    from tui_gateway import organization_credential_transport as owner_transport
    from tests.eidolon_cli.test_organization_setup_subscription_transport import Socket
    import http.client
    import sqlite3
    home = Path(home)
    os.environ['HERMES_HOME'] = str(home)
    os.environ['HOME'] = str(home)
    class Local:
        closed = False
    local, generation = Local(), object()
    owner_transport._owners[local] = (home.resolve(), generation)
    vault = Vault()
    vault.values.update(vault_values)
    store = SubscriptionStore(home, backend=vault)
    guard = lambda: owner_transport.owner_generation(local) is generation
    def preflight(transport, selected_home, selected_candidate, *, inference=False):
        with sqlite3.connect(home / 'organization' / 'state.db') as conn:
            conn.row_factory = sqlite3.Row
            revision = team._current(conn, home, binding.organization_id)[3]
        catalog = {'version': 1, 'state': 'ready', 'catalogId': 'process-catalog',
            'connectionRevision': 1, 'connectionId': candidate['connectionId'],
            'authorizationEpoch': candidate['authorizationEpoch'], 'sourceRevision': revision,
            'fetchedAt': time.time(), 'expiresAt': time.time() + 60,
            'models': [{'slug': candidate['modelSlug'], 'displayName': 'Synthetic'}]}
        lease = store.lease_access_token(binding, 1, owner_generation=generation,
            owner_guard=guard, cancel=threading.Event()) if inference else None
        return policy.SubscriptionPreflight(binding, store, 1, candidate['authorizationEpoch'],
            candidate['connectionId'], candidate['modelSlug'], catalog, generation, guard, lease,
            lease.close if lease else lambda: None)
    socket = Socket(wire(event(response=response())))
    def before_recv():
        if pause:
            entered.set()
            assert release.wait(20)
    socket.before_recv = before_recv
    def connect(connection):
        connection.sock = socket
    try:
        with patch.object(gateway, 'subscription_preflight', preflight), patch.object(http.client.HTTPSConnection, 'connect', connect):
            result = team_admission.improve_team_input(home, value, subscription_transport=local)
        reports.put(('completed', result['suggestion'], len(socket.sent)))
    except Exception as error:
        reports.put(('refused', type(error).__name__, len(socket.sent)))


@pytest.mark.macos_only
def test_cross_process_same_and_competing_keys_cannot_duplicate_physical_send(activated):
    import multiprocessing
    ledger = activated
    context = multiprocessing.get_context('spawn')
    entered, release, reports = context.Event(), context.Event(), context.Queue()
    value = request(ledger)
    def start(candidate, pause):
        process = context.Process(target=_process_infer, args=(str(ledger.home), ledger.evidence.binding,
            ledger.store._backend.values, ledger.candidate, candidate, entered, release, reports, pause))
        process.start()
        return process
    first = start(value, True)
    children = [first]
    try:
        assert entered.wait(20)
        for candidate in (value, {**value, 'idempotencyKey': 'competing-process'}):
            second = start(candidate, False)
            children.append(second)
            report = reports.get(timeout=20)
            assert report[0] == 'refused' and report[2] == 0
            second.join(20)
            assert second.exitcode == 0
        release.set()
        assert reports.get(timeout=20)[0] == 'completed'
        first.join(20)
        assert first.exitcode == 0
    finally:
        release.set()
        for child in children:
            child.join(20)
            if child.is_alive():
                child.terminate()
                child.join(10)
    assert budget.operation_counts(ledger.conn)['total'] == 1
    assert len(state(ledger)) == 1 and state(ledger)[0]['status'] == 'completed'


@pytest.mark.macos_only
def test_late_terminal_worker_preserves_usage_after_timeout_and_retirement(activated, monkeypatch):
    ledger = activated
    install(monkeypatch, wire(event(response=response())))
    entered, release, committed = threading.Event(), threading.Event(), threading.Event()
    original_job, original_persist = admission._outcome_job, admission._persist
    def late_job(*args):
        entered.set()
        assert release.wait(20)
        result = original_job(*args)
        committed.set()
        return result
    def bounded(function, args, deadline, **kwargs):
        if function is late_job:
            return original_persist(function, args, min(deadline, time.monotonic() + 2), **kwargs)
        return original_persist(function, args, deadline, **kwargs)
    monkeypatch.setattr(admission, '_outcome_job', late_job)
    monkeypatch.setattr(admission, '_persist', bounded)
    try:
        with pytest.raises(Exception):
            infer(ledger, request(ledger))
        assert entered.is_set()
        assert state(ledger)[0]['status'] == 'uncertain'
        ledger.transport.closed = True
    finally:
        release.set()
    assert committed.wait(20)
    assert state(ledger)[0]['status'] == 'failed'
    raw = ledger.conn.execute('SELECT outcome_json FROM organization_setup_subscription_outcomes').fetchone()[0]
    assert json.loads(raw)['usage']['total_tokens'] == 3
    assert 'suggestion' not in raw


@pytest.mark.macos_only
def test_new_attempt_uses_lowered_current_objective_timeout(activated, monkeypatch):
    from eidolon_cli import organization_owner_policy as owner
    ledger = activated
    current = owner.owner_policy(ledger.home, ledger.identity['organizationId'])
    params = {'expectedOrganizationId': current['organizationId'],
        'expectedGeneration': current['policyGeneration'], 'sourceRevision': current['sourceRevision'],
        'configuration': {'objective_timeout_seconds': 60}}
    preview = owner.preview_policy(ledger.home, 'budgetPolicy', params)
    owner.apply_policy(ledger.home, 'budgetPolicy', {**params, 'previewVersion': preview['previewVersion'],
        'idempotencyKey': 'lower-local-timeout', 'confirmed': True})
    install(monkeypatch, wire(event(response=response())))
    assert infer(ledger, request(ledger))['suggestion'] == 'Ready'
    binding = json.loads(ledger.conn.execute('SELECT binding_json FROM organization_setup_subscription_attempts').fetchone()[0])
    assert binding['timeout_seconds'] == 60
