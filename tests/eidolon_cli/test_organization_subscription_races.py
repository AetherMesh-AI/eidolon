"""Real enrollment/dispatch races, with synthetic native vault and TLS only."""
from datetime import datetime, timezone
import threading
import time

import pytest

from eidolon_cli import organization_setup_subscription_admission as admission
from eidolon_cli import organization_team_budget as budget
from eidolon_cli.organization_owner_policy import OwnerPolicyError
from eidolon_cli.organization_setup_subscription_policy import SubscriptionPolicyError
from eidolon_cli.organization_shared_allowance import SharedAllowanceError, SharedAllowanceLedger
from eidolon_cli.organization_shared_budget import _read_scope_pin
from tests.eidolon_cli.test_organization_setup_subscription_admission import (
    activated as activated,
    infer,
    ledger as ledger,
    request,
    state,
)
from tests.eidolon_cli.test_organization_setup_subscription_transport import event, install, response, wire


def _enrollment(ledger):
    now = time.time()
    limits = {'modelCalls': 1, 'tokens': 1, 'configuredCostUsd': '0.01'}
    protected = {'modelCalls': 0, 'tokens': 0, 'configuredCostUsd': '0'}
    return {
        'periodId': 'subscription-race',
        'startsAt': datetime.fromtimestamp(now - 60, timezone.utc).isoformat(),
        'endsAt': datetime.fromtimestamp(now + 3600, timezone.utc).isoformat(),
        'limits': limits,
        'protected': protected,
        'allocations': [{
            'profile': 'default',
            'organizationId': ledger.identity['organizationId'],
            'ownerId': ledger.identity['owner']['ownerId'],
            'limits': dict(limits),
            'protected': dict(protected),
        }],
    }


@pytest.mark.macos_only
def test_real_shared_enrollment_refuses_dispatched_running_subscription_without_mutation(activated, monkeypatch):
    ledger = activated
    shared = SharedAllowanceLedger(ledger.home, home=ledger.home)
    configuration = _enrollment(ledger)
    configure_params = {
        'expected_revision': 0,
        'idempotency_key': 'enroll-default-profile',
        'root_organization_id': ledger.identity['organizationId'],
        'root_owner_id': ledger.identity['owner']['ownerId'],
    }
    socket, connects = install(monkeypatch, wire(event(response=response())))
    enrolled = threading.Event()
    results = []

    def configure():
        try:
            results.append(shared.configure(configuration, **configure_params))
        except Exception as error:
            results.append(error)
        finally:
            enrolled.set()

    worker = threading.Thread(target=configure, daemon=True)

    def while_receiving():
        socket.before_recv = None
        assert state(ledger)[0]['status'] == 'running'
        assert state(ledger)[0]['dispatched'] == 1
        assert b'POST /v1/responses' in b''.join(socket.sent)
        before = tuple(ledger.conn.iterdump())
        worker.start()
        assert enrolled.wait(20)
        worker.join(20)
        assert not worker.is_alive()
        assert len(results) == 1 and isinstance(results[0], SharedAllowanceError)
        assert results[0].reason == 'scope_execution_active'
        assert tuple(ledger.conn.iterdump()) == before
        assert _read_scope_pin(ledger.conn) is None
        assert not shared.path.exists() and not shared.marker.exists()
        assert shared.resolve_binding(ledger.identity['organizationId']) is None

    socket.before_recv = while_receiving
    value = request(ledger)
    try:
        assert infer(ledger, value)['suggestion'] == 'Ready'
    finally:
        if worker.ident is not None:
            worker.join(20)
    assert enrolled.is_set() and not worker.is_alive()
    assert state(ledger)[0]['status'] == 'completed'

    # The same real configuration succeeds after known completion. Its enclosing
    # token/cost authority must then block both replay and a new subscription key.
    assert shared.configure(configuration, **configure_params)['revision'] == 1
    assert shared.resolve_binding(ledger.identity['organizationId']) is not None
    enrolled_state = shared.read()
    before = tuple(ledger.conn.iterdump())
    sent = tuple(socket.sent)
    for candidate in (value, request(ledger, 'after-enrollment')):
        with pytest.raises(SubscriptionPolicyError, match='shared_scope_unsupported'):
            infer(ledger, candidate)
    assert len(connects) == 1 and tuple(socket.sent) == sent
    assert tuple(ledger.conn.iterdump()) == before
    assert shared.read() == enrolled_state
    assert budget.operation_counts(ledger.conn)['total'] == 1


@pytest.mark.macos_only
@pytest.mark.parametrize('pause_at', ['before_commit', 'after_commit'])
def test_late_dispatch_worker_never_returns_permit_or_releases_slot_early(activated, monkeypatch, pause_at):
    ledger = activated
    socket, connects = install(monkeypatch, wire(event(response=response())))
    slots = threading.BoundedSemaphore(2)
    monkeypatch.setattr(admission, '_PERSISTENCE_SLOTS', slots)
    paused, release = threading.Event(), threading.Event()
    original_job = admission._dispatch_job
    original_begin = admission._Admission.begin_dispatch
    worker_results, admissions, permits, deadlines = [], [], [], []

    def delayed_dispatch(*args):
        if pause_at == 'after_commit':
            result = original_job(*args)
            assert result is True
        paused.set()
        assert release.wait(20)
        if pause_at == 'before_commit':
            result = original_job(*args)
        worker_results.append(result)
        return result

    def bounded_begin(self, request_sha256, *, deadline):
        deadline = min(deadline, time.monotonic() + 2)
        admissions.append(self)
        deadlines.append(deadline)
        permit = original_begin(self, request_sha256, deadline=deadline)
        permits.append(permit)
        return permit

    monkeypatch.setattr(admission, '_dispatch_job', delayed_dispatch)
    monkeypatch.setattr(admission._Admission, 'begin_dispatch', bounded_begin)
    value = request(ledger)
    try:
        with pytest.raises(Exception):
            infer(ledger, value)
        assert paused.is_set() and not worker_results
        assert permits == [None] and len(admissions) == 1
        capability = admissions[0]
        assert time.monotonic() >= deadlines[0]
        assert capability._dispatch_outcome == 'unknown'
        assert capability._permit is None and capability.network_cancelled()
        assert not socket.sent and socket.closed and len(connects) == 1
        row = state(ledger)[0]
        assert row['status'] == 'uncertain' and row['result_json'] is None
        assert row['dispatched'] == int(pause_at == 'after_commit')
        markers = [tuple(row) for row in ledger.conn.execute(
            'SELECT * FROM organization_setup_subscription_dispatches')]
        assert len(markers) == row['dispatched']
        assert ledger.conn.execute(
            'SELECT count(*) FROM organization_setup_subscription_outcomes').fetchone()[0] == 0
        view = budget.budget_view(ledger.conn, ledger.settings)
        assert view['status'] == 'uncertain'
        assert view['usage']['total'] == row['dispatched']
        assert view['remaining']['totalCalls'] == view['limits']['totalCalls'] - row['dispatched']
        assert view['setupUsage']['totalTokens'] is None and view['setupUsage']['costUsd'] is None

        # The second slot can finalize failure; the abandoned dispatch still owns
        # its slot. Saturation must refuse new persistence without starting it.
        assert slots.acquire(timeout=20)
        try:
            assert slots.acquire(blocking=False) is False
            called = threading.Event()
            assert admission._persist(called.set, (), time.monotonic() + 2, detailed=True) == 'not_committed'
            assert not called.is_set()
        finally:
            slots.release()
        for candidate in (value, request(ledger, 'while-dispatch-worker-pending')):
            with pytest.raises((OwnerPolicyError, SubscriptionPolicyError), match='uncertain'):
                infer(ledger, candidate)
        assert len(connects) == 1 and not socket.sent
    finally:
        release.set()
        # Acquiring both slots synchronizes with actual worker exit, rather than
        # assuming its return/slot release happens within an arbitrary sleep.
        first = slots.acquire(timeout=20)
        second = slots.acquire(timeout=20) if first else False
        if second:
            slots.release()
        if first:
            slots.release()
        assert first and second

    assert worker_results == [pause_at == 'after_commit']
    assert capability._permit is None and permits == [None]
    assert original_begin(capability, capability.binding.request_sha256,
                          deadline=time.monotonic() + 2) is None
    assert state(ledger) == [row]
    assert [tuple(row) for row in ledger.conn.execute(
        'SELECT * FROM organization_setup_subscription_dispatches')] == markers
    assert budget.budget_view(ledger.conn, ledger.settings) == view
    for candidate in (value, request(ledger, 'after-dispatch-worker-exit')):
        with pytest.raises((OwnerPolicyError, SubscriptionPolicyError), match='uncertain'):
            infer(ledger, candidate)
    assert len(connects) == 1 and not socket.sent
    assert admission._persist(lambda: True, (), time.monotonic() + 2, detailed=True) == 'committed'
