"""Real SQLite writer barriers; no provider, native-vault or platform substitution."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import gc
import os
from pathlib import Path
from queue import Queue
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest

from eidolon_cli import organization_ledger_writer as writers
from eidolon_cli import organization_owner_policy as owner
from eidolon_cli import organization_setup_subscription_admission as admission
from eidolon_cli.organization_store import OrganizationStore
from tui_gateway import organization_credential_transport as owners


@contextmanager
def held_writer(store):
    entered, release = threading.Event(), threading.Event()
    identity, failures = [], []
    def hold():
        try:
            with store._write() as conn:
                path = Path(conn.execute('PRAGMA database_list').fetchone()[2])
                identity.append((path.resolve(), path.stat().st_dev, path.stat().st_ino))
                conn.execute("INSERT INTO events(kind,text,created) VALUES ('system','holder',0)")
                entered.set()
                assert release.wait(20)
        except BaseException as error:
            failures.append(error)
            entered.set()
    worker = threading.Thread(target=hold, name='same-ledger-writer', daemon=True)
    worker.start()
    try:
        assert entered.wait(20) and not failures
        yield release, identity
    finally:
        release.set()
        worker.join(20)
        assert not worker.is_alive() and not failures


def queued_writers(monkeypatch):
    queued = Queue()
    original = writers._Gate._wait_for_turn
    def observe(self, ticket, deadline, cancelled):
        if self.owner is not None:
            queued.put((self, threading.current_thread().name))
        return original(self, ticket, deadline, cancelled)
    monkeypatch.setattr(writers._Gate, '_wait_for_turn', observe)
    return queued


@pytest.fixture
def store(tmp_path):
    return OrganizationStore(tmp_path / 'organization' / 'state.db')


@pytest.mark.parametrize('consumer', ['owner', 'accounting', 'store', 'scheduler', 'initialize'])
def test_same_physical_ledger_writer_is_queued_then_commits_once(store, tmp_path, monkeypatch, consumer):
    queued = queued_writers(monkeypatch)
    def write():
        if consumer == 'scheduler':
            return store.recover_expired()
        if consumer == 'initialize':
            OrganizationStore(store.path)
            return 'initialized'
        scope = {'owner': lambda: owner._connection(tmp_path, write=True),
                 'accounting': lambda: admission._ledger_connection(tmp_path, write=True),
                 'store': store._write}[consumer]
        with scope() as conn:
            path = Path(conn.execute('PRAGMA database_list').fetchone()[2])
            identity = (path.resolve(), path.stat().st_dev, path.stat().st_ino)
            assert conn.execute("SELECT count(*) FROM events WHERE text='holder'").fetchone()[0] == 1
            conn.execute("INSERT INTO events(kind,text,created) VALUES ('system','waiter',0)")
            return identity
    with ThreadPoolExecutor(max_workers=1) as pool:
        with held_writer(store) as (release, identity):
            future = pool.submit(write)
            gate, _ = queued.get(timeout=20)
            with gate.condition:
                assert gate.owner is not None and len(gate.waiters) == 1
            # Actual SQLite transaction is held on exactly the same physical file.
            with sqlite3.connect(store.path, timeout=0, isolation_level=None) as external:
                with pytest.raises(sqlite3.OperationalError) as error:
                    external.execute('BEGIN IMMEDIATE')
                assert error.value.sqlite_errorcode & 255 == sqlite3.SQLITE_BUSY
            release.set()
        result = future.result(timeout=20)
    with owner._connection(tmp_path) as conn:
        texts = [row[0] for row in conn.execute("SELECT text FROM events WHERE text IN ('holder','waiter') ORDER BY id")]
    assert texts == ['holder'] + ([] if consumer in {'scheduler', 'initialize'} else ['waiter'])
    if consumer not in {'scheduler', 'initialize'}:
        assert result == identity[0]


def test_readers_and_unrelated_ledger_do_not_queue(store, tmp_path, monkeypatch):
    other = tmp_path / 'other'
    OrganizationStore(other / 'organization' / 'state.db')
    queued = queued_writers(monkeypatch)
    with held_writer(store):
        with owner._connection(tmp_path) as conn:
            assert conn.execute("SELECT count(*) FROM events WHERE text='holder'").fetchone()[0] == 0
        with owner._connection(other, write=True) as conn:
            conn.execute("INSERT INTO events(kind,text,created) VALUES ('system','other',0)")
    assert queued.empty()


def test_fifo_queue_reentry_and_registry_lifetime(tmp_path, monkeypatch):
    path = tmp_path / 'state.db'
    queued = queued_writers(monkeypatch)
    order = []
    def take(number):
        with writers.ledger_writer(path, deadline=time.monotonic() + 10):
            order.append(number)
    with ThreadPoolExecutor(max_workers=3) as pool:
        with writers.ledger_writer(path, deadline=time.monotonic() + 10):
            with pytest.raises(writers.LedgerWriterUnavailable, match='reentrant'):
                with writers.ledger_writer(path, deadline=time.monotonic() + 10):
                    pytest.fail('A nested independent SQLite writer cannot reenter')
            first = pool.submit(take, 1)
            queued.get(timeout=20)
            second = pool.submit(take, 2)
            queued.get(timeout=20)
        third = pool.submit(take, 3)
        for future in (first, second, third):
            future.result(timeout=20)
    assert order == [1, 2, 3]
    # Queue observations deliberately own strong references until consumed.
    while not queued.empty():
        queued.get_nowait()
    gc.collect()
    assert str(path.resolve()) not in writers._gates


@pytest.mark.parametrize('failure', ['cancelled', 'deadline', 'queue_full'])
def test_waiter_failure_is_removed_and_never_takes_late_authority(tmp_path, monkeypatch, failure):
    path = tmp_path / 'state.db'
    queued = queued_writers(monkeypatch)
    cancel = threading.Event()
    entered = []
    def take(deadline):
        try:
            with writers.ledger_writer(path, deadline=deadline, cancelled=cancel.is_set):
                entered.append(True)
            return 'entered'
        except writers.LedgerWriterUnavailable as error:
            return error.reason
    with ThreadPoolExecutor(max_workers=2) as pool:
        with writers.ledger_writer(path, deadline=time.monotonic() + 10):
            first = pool.submit(take, time.monotonic() + (.1 if failure == 'deadline' else 10))
            gate, _ = queued.get(timeout=20)
            if failure == 'queue_full':
                monkeypatch.setattr(writers, '_MAX_WAITERS', 1)
                assert pool.submit(take, time.monotonic() + 10).result(timeout=20) == 'queue_full'
            if failure != 'deadline':
                cancel.set()
            assert first.result(timeout=20) == ('deadline' if failure == 'deadline' else 'cancelled')
            with gate.condition:
                assert not gate.waiters
            assert not entered
        cancel.clear()
        assert pool.submit(take, time.monotonic() + 10).result(timeout=20) == 'entered'
    assert entered == [True]


@pytest.mark.parametrize('expired,cancelled', [(True, False), (False, True)])
def test_no_authority_after_deadline_or_cancellation_even_without_holder(tmp_path, expired, cancelled):
    stop = threading.Event()
    if cancelled:
        stop.set()
    with pytest.raises(writers.LedgerWriterUnavailable, match='deadline' if expired else 'cancelled'):
        with writers.ledger_writer(tmp_path / 'state.db',
                deadline=time.monotonic() + (-1 if expired else 10), cancelled=stop.is_set):
            pytest.fail('Expired/cancelled waiter acquired authority')


def test_cancellation_is_rechecked_when_holder_releases(tmp_path, monkeypatch):
    path = tmp_path / 'state.db'
    queued = queued_writers(monkeypatch)
    stop = threading.Event()
    def take():
        with pytest.raises(writers.LedgerWriterUnavailable, match='cancelled'):
            with writers.ledger_writer(path, deadline=time.monotonic() + 10, cancelled=stop.is_set):
                pytest.fail('Cancelled waiter acquired authority after release')
    with ThreadPoolExecutor(max_workers=1) as pool:
        with writers.ledger_writer(path, deadline=time.monotonic() + 10):
            future = pool.submit(take)
            queued.get(timeout=20)
            stop.set()
        future.result(timeout=20)


def test_external_sqlite_writer_is_still_immediate_fail_closed(store, tmp_path):
    with sqlite3.connect(store.path, isolation_level=None) as external:
        external.execute('BEGIN IMMEDIATE')
        with pytest.raises(sqlite3.OperationalError) as error:
            with admission._ledger_connection(tmp_path, write=True):
                pytest.fail('A process-local gate cannot override SQLite')
        assert error.value.sqlite_errorcode & 255 == sqlite3.SQLITE_BUSY
        external.rollback()
    with admission._ledger_connection(tmp_path, write=True):
        pass


def test_hardlink_alias_is_not_misrepresented_as_coordinated_authority(store, tmp_path):
    alias_home = tmp_path / 'alias'
    (alias_home / 'organization').mkdir(parents=True)
    alias = alias_home / 'organization' / 'state.db'
    os.link(store.path, alias)
    assert os.stat(alias).st_ino == os.stat(store.path).st_ino
    # Different canonical paths stay separate; SQLite, not this advisory queue,
    # decides whether an inode alias is available. Do not open an alias WAL.
    with writers.ledger_writer(store.path, deadline=time.monotonic() + 10):
        with writers.ledger_writer(alias, deadline=time.monotonic() + 10):
            assert writers._gates[str(store.path.resolve())] is not writers._gates[str(alias.resolve())]


@pytest.mark.parametrize('stop', ['deadline', 'cancel'])
def test_queued_dispatch_releases_publication_before_holder_and_cannot_run_late(store, tmp_path, monkeypatch, stop):
    class Transport:
        closed = False
    transport, generation = Transport(), object()
    monkeypatch.setitem(owners._owners, transport, (tmp_path.resolve(), generation))
    queued = queued_writers(monkeypatch)
    caller_cancel, abandoned = threading.Event(), threading.Event()
    body_calls = []
    def unexpected(*args, **kwargs):
        body_calls.append(True)
        raise AssertionError('Blocked dispatch must never enter the transaction body')
    monkeypatch.setattr(admission, '_exact_attempt', unexpected)
    with ThreadPoolExecutor(max_workers=2) as pool:
        with held_writer(store):
            future = pool.submit(admission._dispatch_job, tmp_path, transport,
                SimpleNamespace(owner_generation=generation), None, None, abandoned,
                time.monotonic() + 120, caller_cancel)
            queued.get(timeout=20)
            retiring = pool.submit(owners.retire_owner, transport)
            if stop == 'cancel':
                caller_cancel.set()
            assert future.result(timeout=5) is False
            retiring.result(timeout=5)
            assert owners.owner_generation(transport) is None
        assert not body_calls
    assert not body_calls


# Complete dispatch/accounting paths keep native platform eligibility intact.
from tests.eidolon_cli.test_organization_setup_subscription_admission import (
    activated, infer, ledger, request, state,
)
from tests.eidolon_cli.test_organization_setup_subscription_transport import install, wire, event, response


@pytest.mark.macos_only
@pytest.mark.parametrize('release_at', ['dispatch_queue', 'cleanup_queue'])
def test_real_dispatch_and_late_cleanup_share_scheduler_writer_without_replay(activated, monkeypatch, release_at):
    from eidolon_cli import organization_team_generation as generation
    from eidolon_cli import organization_team_budget as budget
    rig = activated
    store = OrganizationStore(rig.home / 'organization' / 'state.db')
    socket, connects = install(monkeypatch, wire(event(response=response())))
    complete = generation.complete_subscription
    original_wait = writers._Gate._wait_for_turn
    attempts, held = [], []
    def wait(self, ticket, deadline, cancelled):
        if self.owner is not None and threading.current_thread().name == 'setup-subscription-accounting':
            attempts.append('dispatch' if cancelled is not None else 'cleanup')
            if attempts[-1] == ('dispatch' if release_at == 'dispatch_queue' else 'cleanup'):
                held[0].set()
        return original_wait(self, ticket, deadline, cancelled)
    monkeypatch.setattr(writers._Gate, '_wait_for_turn', wait)
    def contend(prepared):
        # Admission already committed. Hold the actual scheduler writer across
        # dispatch, and through the caller's error into cleanup when requested.
        scope = held_writer(store)
        release, identity = scope.__enter__()
        held.extend([release, scope, identity])
        return complete(prepared)
    monkeypatch.setattr(generation, 'complete_subscription', contend)
    value = request(rig)
    try:
        if release_at == 'dispatch_queue':
            assert infer(rig, value)['suggestion'] == 'Ready'
            assert state(rig)[0]['status'] == 'completed'
            assert state(rig)[0]['dispatched'] == 1
            assert budget.operation_counts(rig.conn)['total'] == 1
            assert infer(rig, value)['suggestion'] == 'Ready'
            assert len(connects) == 1
        else:
            with pytest.raises(Exception):
                infer(rig, value)
            assert attempts == ['dispatch', 'cleanup']
            assert not socket.sent and state(rig)[0]['status'] == 'failed'
            assert state(rig)[0]['dispatched'] == 0
            assert budget.operation_counts(rig.conn)['total'] == 0
            before = list(rig.conn.iterdump())
            with pytest.raises(owner.OwnerPolicyError, match='generation_failed'):
                infer(rig, value)
            offer = admission.retry_unsent_disposition(rig.home, value, 'improveTeamInput', transport=rig.transport)
            assert offer is not None and offer['disposition'] == 'retry_unsent'
            assert list(rig.conn.iterdump()) == before and not socket.sent and len(connects) == 1
    finally:
        if held:
            held[1].__exit__(None, None, None)


# This existing fixture substitutes only synthetic local-owner eligibility on
# non-macOS hosts. All transaction, lease, transport and accounting code is real;
# these tests do not establish production platform or native readiness.
from tests.eidolon_cli.test_organization_setup_subscription_recovery import recovery_ledger


@pytest.mark.parametrize('exhaust', [False, True])
def test_retained_failed_accounting_has_bounded_lifetime_and_honest_recovery(recovery_ledger, monkeypatch, exhaust):
    from eidolon_cli import organization_team_generation as generation
    rig = recovery_ledger
    store = OrganizationStore(rig.home / 'organization' / 'state.db')
    socket, connects = install(monkeypatch, wire(event(response=response())))
    held, result = [], []
    finished = threading.Event()
    original_failed, complete = admission._failed_job, generation.complete_subscription
    def failed(*args):
        try:
            result.append(original_failed(*args))
        except Exception as error:
            result.append(type(error).__name__)
            raise
        finally:
            finished.set()
    def contend(prepared):
        scope = held_writer(store)
        release, _ = scope.__enter__()
        held.extend([release, scope])
        return complete(prepared)
    monkeypatch.setattr(admission, '_failed_job', failed)
    monkeypatch.setattr(generation, 'complete_subscription', contend)
    value = request(rig)
    try:
        with pytest.raises(Exception):
            infer(rig, value)
        old = state(rig)[0]
        assert old['status'] == 'running' and old['dispatched'] == 0 and not socket.sent
        attempts = [tuple(row) for row in rig.conn.execute('SELECT * FROM organization_setup_subscription_attempts')]
        assert len(attempts) == 1
        assert admission.retry_unsent_disposition(rig.home, value, 'improveTeamInput', transport=rig.transport) is None
        with pytest.raises(owner.OwnerPolicyError, match='generation_running'):
            infer(rig, value)
        if exhaust:
            # The actual five-second grace expires while the holder is still
            # known live. No blind retry, false terminal state or recovery offer.
            assert finished.wait(20)
            assert result == ['LedgerWriterUnavailable']
            assert state(rig) == [old]
        held[0].set()
        assert finished.wait(20)
        assert [tuple(row) for row in rig.conn.execute('SELECT * FROM organization_setup_subscription_attempts')] == attempts
        assert rig.conn.execute('SELECT count(*) FROM organization_setup_subscription_dispatches').fetchone()[0] == 0
        assert rig.conn.execute('SELECT count(*) FROM organization_setup_subscription_outcomes').fetchone()[0] == 0
        if exhaust:
            assert state(rig) == [old]
            assert admission.retry_unsent_disposition(rig.home, value, 'improveTeamInput', transport=rig.transport) is None
        else:
            assert result == [True]
            assert state(rig)[0] == {**old, 'status': 'failed'}
            before = list(rig.conn.iterdump())
            with pytest.raises(owner.OwnerPolicyError, match='generation_failed'):
                infer(rig, value)
            offer = admission.retry_unsent_disposition(rig.home, value, 'improveTeamInput', transport=rig.transport)
            assert offer is not None and offer['disposition'] == 'retry_unsent'
            assert list(rig.conn.iterdump()) == before
        assert not socket.sent and len(connects) == 1
    finally:
        if held:
            held[1].__exit__(None, None, None)


def test_late_outcome_waits_for_same_ledger_once_and_keeps_original_charge(recovery_ledger, monkeypatch):
    from eidolon_cli import organization_team_budget as budget
    rig = recovery_ledger
    store = OrganizationStore(rig.home / 'organization' / 'state.db')
    socket, connects = install(monkeypatch, wire(event(response=response())))
    held, observations, failures = [], [], []
    finished = threading.Event()
    original_outcome, persist = admission._outcome_job, admission._persist
    def before_recv():
        socket.before_recv = None
        scope = held_writer(store)
        release, _ = scope.__enter__()
        held.extend([release, scope])
    def outcome(*args):
        observations.append(args)
        try:
            return original_outcome(*args)
        except Exception as error:
            failures.append(error)
            raise
        finally:
            finished.set()
    def bounded(function, args, deadline, **kwargs):
        if function is outcome:
            deadline = min(deadline, time.monotonic() + 2)
        return persist(function, args, deadline, **kwargs)
    socket.before_recv = before_recv
    monkeypatch.setattr(admission, '_outcome_job', outcome)
    monkeypatch.setattr(admission, '_persist', bounded)
    try:
        with pytest.raises(Exception):
            infer(rig, request(rig))
        row = state(rig)[0]
        assert row['status'] == 'running' and row['dispatched'] == 1
        assert len(observations) == 1 and not failures
        immutable = {table: [tuple(row) for row in rig.conn.execute('SELECT * FROM ' + table)] for table in (
            'organization_setup_subscription_attempts', 'organization_setup_subscription_dispatches',
            'organization_team_generation_operations')}
        rig.transport.closed = True
        held[0].set()
        assert finished.wait(20) and not failures
        # Synchronize retained cleanup as well, rather than assuming a thread's
        # body return and persistence-slot release are simultaneous.
        acquired = [admission._PERSISTENCE_SLOTS.acquire(timeout=20) for _ in range(4)]
        for obtained in acquired:
            if obtained:
                admission._PERSISTENCE_SLOTS.release()
        assert all(acquired)
        assert state(rig)[0] == {**row, 'status': 'failed'}
        assert budget.operation_counts(rig.conn)['total'] == 1
        for table, before in immutable.items():
            assert [tuple(row) for row in rig.conn.execute('SELECT * FROM ' + table)] == before
        before = list(rig.conn.iterdump())
        assert original_outcome(*observations[0]) is True
        assert list(rig.conn.iterdump()) == before
        assert rig.conn.execute('SELECT count(*) FROM organization_setup_subscription_outcomes').fetchone()[0] == 1
        assert len(connects) == 1 and socket.sent and state(rig)[0]['result_json'] is None
    finally:
        if held:
            held[1].__exit__(None, None, None)


@pytest.mark.parametrize('pause_at', ['before_commit', 'after_commit'])
def test_synthetic_ambiguous_dispatch_preserves_uncertainty_and_never_late_sends(recovery_ledger, monkeypatch, pause_at):
    from tests.eidolon_cli.test_organization_subscription_races import (
        test_late_dispatch_worker_never_returns_permit_or_releases_slot_early,
    )
    test_late_dispatch_worker_never_returns_permit_or_releases_slot_early(recovery_ledger, monkeypatch, pause_at)


@pytest.mark.parametrize('reason', ['deadline', 'queue_full', 'cancelled'])
def test_dispatch_queue_refusal_releases_accounting_slot(store, tmp_path, monkeypatch, reason):
    class Transport:
        closed = False
    transport, generation = Transport(), object()
    monkeypatch.setitem(owners._owners, transport, (tmp_path.resolve(), generation))
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(admission, '_PERSISTENCE_SLOTS', slots)
    stop = threading.Event()
    if reason == 'cancelled':
        stop.set()
    with held_writer(store):
        if reason == 'queue_full':
            monkeypatch.setattr(writers, '_MAX_WAITERS', 0)
        deadline = time.monotonic() + 10
        assert admission._persist(admission._dispatch_job, (tmp_path, transport,
            SimpleNamespace(owner_generation=generation), None, None, threading.Event(), deadline, stop),
            deadline, detailed=True) == 'not_committed'
        assert slots.acquire(timeout=20)
        slots.release()


def test_dispatch_rechecks_cancellation_after_gate_acquisition(store, tmp_path, monkeypatch):
    class Transport:
        closed = False
    transport, generation = Transport(), object()
    monkeypatch.setitem(owners._owners, transport, (tmp_path.resolve(), generation))
    stop = threading.Event()
    acquire = writers._Gate.acquire
    def cancel_after_acquire(self, deadline, cancelled):
        acquire(self, deadline, cancelled)
        stop.set()
    monkeypatch.setattr(writers._Gate, 'acquire', cancel_after_acquire)
    assert admission._dispatch_job(tmp_path, transport, SimpleNamespace(owner_generation=generation),
        None, None, threading.Event(), time.monotonic() + 10, stop) is False
    with owner._connection(tmp_path) as conn:
        assert conn.execute('SELECT count(*) FROM organization_team_generations').fetchone()[0] == 0


def test_cancel_after_dispatch_snapshot_rolls_back_without_refund_or_send(recovery_ledger, monkeypatch):
    from eidolon_cli import organization_team_admission as teams
    rig = recovery_ledger
    socket, connects = install(monkeypatch, wire(event(response=response())))
    stop = threading.Event()
    original = admission._snapshot
    def cancel_on_charged_snapshot(conn, home, transport, evidence, binding, **kwargs):
        value = original(conn, home, transport, evidence, binding, **kwargs)
        if kwargs.get('allow_charged'):
            stop.set()
        return value
    monkeypatch.setattr(admission, '_snapshot', cancel_on_charged_snapshot)
    with pytest.raises(Exception):
        teams.improve_team_input(rig.home, request(rig), subscription_transport=rig.transport, cancel=stop)
    row = state(rig)[0]
    assert row['status'] == 'failed' and row['dispatched'] == 0
    assert rig.conn.execute('SELECT count(*) FROM organization_setup_subscription_attempts').fetchone()[0] == 1
    assert rig.conn.execute('SELECT count(*) FROM organization_setup_subscription_dispatches').fetchone()[0] == 0
    assert rig.conn.execute('SELECT count(*) FROM organization_setup_subscription_outcomes').fetchone()[0] == 0
    assert not socket.sent and len(connects) == 1
