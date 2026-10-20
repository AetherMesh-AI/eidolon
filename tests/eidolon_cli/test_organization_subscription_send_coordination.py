"""Real ledger/lease/sender fences; synthetic local eligibility, vault and TLS only."""
from contextlib import closing, contextmanager
import sqlite3
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from eidolon_cli import organization_ledger_writer as writers
from eidolon_cli import organization_setup_subscription_admission as admission
from eidolon_cli import organization_team_budget as budget
from eidolon_cli.organization_store import OrganizationStore
from tests.eidolon_cli.test_organization_ledger_writer import held_writer
from tests.eidolon_cli.test_organization_setup_subscription_admission import (
    ledger, activated, infer, request, state,
)
from tests.eidolon_cli.test_organization_setup_subscription_recovery import recovery_ledger
from tests.eidolon_cli.test_organization_setup_subscription_transport import install, wire, event, response


@contextmanager
def held_scheduler_close(store, monkeypatch):
    """Run the real no-op scheduler transaction through commit, then hold close."""
    entered, release = threading.Event(), threading.Event()
    failures = []
    connect = store._connect
    @contextmanager
    def connection(*, write=False):
        assert write is True
        with connect(write=write) as conn:
            try:
                yield conn
            finally:
                assert not conn.in_transaction
                entered.set()
                assert release.wait(20)
    def scheduler():
        try:
            assert store.advance_goal_continuation() == {'launched': None, 'blocked': []}
        except BaseException as error:
            failures.append(error)
            entered.set()
    with monkeypatch.context() as patch:
        patch.setattr(store, '_connect', connection)
        worker = threading.Thread(target=scheduler, name='scheduler-close', daemon=True)
        worker.start()
        try:
            assert entered.wait(20) and not failures
            yield release
        finally:
            release.set()
            worker.join(20)
            assert not worker.is_alive() and not failures


@pytest.mark.parametrize('edge', [1, 2, 3], ids=['headers', 'body', 'reply'])
def test_send_authority_reads_wait_for_full_scheduler_lifecycle_without_replay(
        recovery_ledger, monkeypatch, edge):
    rig = recovery_ledger
    store = OrganizationStore(rig.home / 'organization' / 'state.db')
    socket, connects = install(monkeypatch, wire(event(response=response())))
    original_guard = admission._Admission.try_send_allowed
    original_wait = writers._Gate._wait_for_turn
    guards, queued, releases = [], [], []
    sender = threading.get_ident()

    def wait(gate, ticket, deadline, cancelled):
        if gate.owner is not None and threading.get_ident() == sender:
            queued.append(True)
            assert deadline <= time.monotonic() + writers.PUBLICATION_WAIT_SECONDS
            releases[-1].set()
        return original_wait(gate, ticket, deadline, cancelled)

    def guard(instance):
        guards.append(True)
        if len(guards) != edge:
            return original_guard(instance)
        assert instance._permit is not None and instance._permit.consume() is False
        with held_scheduler_close(store, monkeypatch) as release:
            releases.append(release)
            allowed = original_guard(instance)
            # A WAL reader can normally read an active write snapshot. Its
            # authority must nevertheless wait until this writer's close, not
            # race WAL cleanup/recovery on the next short-lived connection.
            assert release.is_set(), 'Send authority bypassed scheduler coordination'
            return allowed

    def physical_write(_):
        key = str(store.path.resolve())
        with writers._registry_lock:
            gate = writers._gates.get(key)
        if gate is not None:
            with gate.condition:
                assert gate.owner != sender
        assert all(release.is_set() for release in releases)

    monkeypatch.setattr(writers._Gate, '_wait_for_turn', wait)
    monkeypatch.setattr(admission._Admission, 'try_send_allowed', guard)
    socket.before_send = physical_write
    value = request(rig)
    assert infer(rig, value)['suggestion'] == 'Ready'
    assert len(guards) == 3 and queued == [True]
    assert len(socket.sent) == 2 and len(connects) == 1
    assert state(rig)[0]['status'] == 'completed' and state(rig)[0]['dispatched'] == 1
    assert budget.operation_counts(rig.conn)['total'] == 1
    assert rig.conn.execute('SELECT count(*) FROM organization_setup_subscription_dispatches').fetchone()[0] == 1
    assert infer(rig, value)['suggestion'] == 'Ready'
    assert len(connects) == 1 and len(guards) == 3


@pytest.mark.parametrize('failure', ['cancel', 'source', 'retire', 'deadline', 'reentry', 'external', 'external_process', 'close_cancel', 'close_deadline',
    'missing_deadline', 'expired_deadline', 'invalid_deadline'])
def test_contended_send_authority_refusal_preserves_original_charge_without_late_send(
        recovery_ledger, monkeypatch, failure):
    rig = recovery_ledger
    if failure == 'invalid_deadline':
        before = list(rig.conn.iterdump())
        def forbidden(*args, **kwargs):
            raise AssertionError('Invalid deadline reached accounting')
        monkeypatch.setattr(admission, '_persist', forbidden)
        for value in (None, True, 'later', float('inf'), float('-inf'), float('nan')):
            bound = admission._Admission(rig.home, rig.transport, rig.evidence,
                SimpleNamespace(request_sha256='synthetic'), b'0' * 32, threading.Event(), (), 'a' * 64)
            assert bound.begin_dispatch('synthetic', deadline=value) is None
            assert bound._attempted is False and bound._dispatch_deadline is None
        assert list(rig.conn.iterdump()) == before
        return
    store = OrganizationStore(rig.home / 'organization' / 'state.db')
    socket, connects = install(monkeypatch, wire(event(response=response())))
    original_guard = admission._Admission.try_send_allowed
    original_wait = writers._Gate._wait_for_turn
    holder, active, queued, results = [], [], [], []
    close_edge = failure.startswith('close_')
    calls = []
    sender = threading.get_ident()

    def wait(gate, ticket, deadline, cancelled):
        if gate.owner is not None and threading.get_ident() == sender:
            queued.append(True)
            if failure == 'cancel':
                active[0]._cancel.set()
            elif failure == 'source':
                (rig.home / 'config.yaml').write_text('# changed source\n{}\n')
            elif failure == 'retire':
                rig.transport.closed = True
            if failure != 'deadline':
                holder[-1].set()
        return original_wait(gate, ticket, deadline, cancelled)

    def guard(instance):
        calls.append(True)
        if close_edge and len(calls) < 3:
            return original_guard(instance)
        active.append(instance)
        original_deadline = getattr(instance, '_dispatch_deadline', None)
        assert instance.begin_dispatch(instance.binding.request_sha256,
            deadline=time.monotonic() + 600) is None
        assert getattr(instance, '_dispatch_deadline', None) == original_deadline
        if failure in {'missing_deadline', 'expired_deadline'}:
            instance._dispatch_deadline = None if failure == 'missing_deadline' else time.monotonic() - 1
            allowed = original_guard(instance)
        elif close_edge:
            entered, release = threading.Event(), threading.Event()
            connection = admission._ledger_connection
            # Give the final guard a smaller valid original budget, then let it
            # expire while its real connection context is still closing.
            if failure == 'close_deadline':
                instance._dispatch_deadline = min(
                    getattr(instance, '_dispatch_deadline', float('inf')), time.monotonic() + 2)
            def revoke():
                assert entered.wait(20)
                if failure == 'close_cancel':
                    instance._cancel.set()
                else:
                    threading.Event().wait(max(0, instance._dispatch_deadline - time.monotonic()))
                release.set()
            @contextmanager
            def closing_boundary(*args, **kwargs):
                with connection(*args, **kwargs) as conn:
                    yield conn
                    entered.set()
                    assert release.wait(20)
            worker = threading.Thread(target=revoke, name='retire-during-read-close', daemon=True)
            worker.start()
            try:
                with monkeypatch.context() as patch:
                    patch.setattr(admission, '_ledger_connection', closing_boundary)
                    allowed = original_guard(instance)
            finally:
                entered.set()
                release.set()
                worker.join(20)
                assert not worker.is_alive()
        elif failure == 'reentry':
            with writers.ledger_writer(store.path, deadline=time.monotonic() + 10):
                allowed = original_guard(instance)
        elif failure == 'external_process':
            rig.conn.execute('PRAGMA journal_mode=DELETE')
            child = subprocess.Popen([sys.executable, '-c',
                "import sqlite3,sys; c=sqlite3.connect(sys.argv[1],timeout=0,isolation_level=None); "
                "c.execute('BEGIN EXCLUSIVE'); print('locked',flush=True); "
                "sys.stdin.readline(); c.rollback(); c.close()", str(store.path)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            ready = threading.Event()
            output = []
            def read_ready():
                output.append(child.stdout.readline())
                ready.set()
            reader = threading.Thread(target=read_ready, daemon=True)
            reader.start()
            try:
                assert ready.wait(20) and output == ['locked\n']
                allowed = original_guard(instance)
            finally:
                child.stdin.write('release\n')
                child.stdin.flush()
                child.wait(timeout=20)
                reader.join(20)
                assert child.returncode == 0 and not child.stderr.read()
                child.stdin.close()
                child.stdout.close()
                child.stderr.close()
        elif failure == 'external':
            # Rollback journal exclusive lock is a deterministic real-SQLite
            # outside-coordinator conflict. Never turn it into a storage retry.
            rig.conn.execute('PRAGMA journal_mode=DELETE')
            with closing(sqlite3.connect(store.path, timeout=0, isolation_level=None)) as other:
                other.execute('BEGIN EXCLUSIVE')
                try:
                    allowed = original_guard(instance)
                finally:
                    other.rollback()
        else:
            with held_writer(store) as (release, _):
                holder.append(release)
                allowed = original_guard(instance)
        results.append(allowed)
        return allowed

    monkeypatch.setattr(writers._Gate, '_wait_for_turn', wait)
    monkeypatch.setattr(admission._Admission, 'try_send_allowed', guard)
    value = request(rig)
    before = budget.operation_counts(rig.conn)['total']
    with pytest.raises(Exception):
        infer(rig, value)
    assert results == [False]
    assert queued == ([] if failure in {'reentry', 'external', 'external_process', 'missing_deadline', 'expired_deadline'} or close_edge else [True])
    assert len(socket.sent) == (2 if close_edge else 0) and len(connects) == 1
    assert state(rig)[0]['status'] == ('failed' if close_edge else 'uncertain') and state(rig)[0]['dispatched'] == 1
    assert budget.operation_counts(rig.conn)['total'] == before + 1
    assert rig.conn.execute('SELECT count(*) FROM organization_setup_subscription_dispatches').fetchone()[0] == 1
    assert rig.conn.execute('SELECT count(*) FROM organization_setup_subscription_outcomes').fetchone()[0] == int(close_edge)
    assert admission.retry_unsent_disposition(rig.home, value, 'improveTeamInput', transport=rig.transport) is None
    assert active[0]._permit.consume() is False
    old = state(rig)
    with pytest.raises(Exception):
        infer(rig, value)
    assert state(rig) == old and len(socket.sent) == (2 if close_edge else 0) and len(connects) == 1
