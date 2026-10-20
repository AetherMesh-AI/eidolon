"""Real SQLite, deterministic gate admission, and bounded receipt reconciliation."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from queue import Queue
import sqlite3
import threading
import time

import pytest

from eidolon_cli import organization_ledger_writer as writers
from eidolon_cli import organization_owner_policy as policy
from eidolon_cli import organization_team_setup as team
from eidolon_cli.organization_owner_policy import OwnerPolicyError
from tests.eidolon_cli.test_organization_team_setup import profile, confirmed, dump


class GateClock:
    """Only gate deadline production/consumption uses simulated time."""
    now = 100.0

    def monotonic(self):
        return self.now

    def __getattr__(self, name):
        return getattr(time, name)


@pytest.mark.parametrize('scenario', ['queued', 'precommit', 'postcommit'])
def test_concurrent_creates_are_atomic_and_idempotent(profile, monkeypatch, scenario):
    apply = confirmed(profile)
    entered, release = threading.Event(), threading.Event()
    queued = Queue()
    original_connect = sqlite3.connect
    original_wait = writers._Gate._wait_for_turn
    clock = GateClock()
    monkeypatch.setattr(policy, 'time', clock)
    monkeypatch.setattr(writers, 'time', clock)
    owner, writer_opens = [], []

    def hold():
        entered.set()
        assert release.wait(20), 'Controller did not release first writer'

    class Connection(sqlite3.Connection):
        def commit(self):
            if threading.get_ident() in owner and scenario != 'postcommit':
                hold()
            return super().commit()

        def close(self):
            if threading.get_ident() in owner and scenario == 'postcommit':
                hold()
            return super().close()

    def connect(*args, **kwargs):
        if '?mode=rw' in str(args[0]):
            writer_opens.append(threading.get_ident())
        kwargs.setdefault('factory', Connection)
        return original_connect(*args, **kwargs)

    def wait(self, ticket, deadline, cancelled):
        # acquire() has already enqueued this actual ticket under the condition.
        if self.owner is not None:
            assert ticket in self.waiters
            queued.put((self, deadline))
        return original_wait(self, ticket, deadline, cancelled)

    monkeypatch.setattr(sqlite3, 'connect', connect)
    monkeypatch.setattr(writers._Gate, '_wait_for_turn', wait)

    def first():
        owner.append(threading.get_ident())
        return team.create_team(profile.home, apply)

    with ThreadPoolExecutor(2) as pool:
        first_future = pool.submit(first)
        try:
            assert entered.wait(20)
            second = pool.submit(team.create_team, profile.home, apply)
            gate, deadline = queued.get(timeout=20)
            with gate.condition:
                assert gate.owner == owner[0] and len(gate.waiters) == 1
                if scenario != 'queued':
                    clock.now = deadline + 1
                    gate.condition.notify_all()
            if scenario == 'queued':
                release.set()
                second_result = second.result(timeout=20)
            else:
                before = dump(profile)
                if scenario == 'precommit':
                    with pytest.raises(OwnerPolicyError, match='locked'):
                        second.result(timeout=20)
                    second_result = None
                    with original_connect(profile.path) as conn:
                        assert conn.execute('SELECT count(*) FROM organization_team_setup').fetchone()[0] == 0
                else:
                    second_result = second.result(timeout=20)
                    assert second_result['state'] == 'created'
                assert not first_future.done()
                assert len(writer_opens) == 1
                assert dump(profile) == before
        finally:
            release.set()
        first_result = first_future.result(timeout=20)

    if scenario != 'precommit':
        assert second_result == first_result
    assert len(writer_opens) == (2 if scenario == 'queued' else 1)
    before = dump(profile)
    assert team.create_team(profile.home, apply) == first_result
    assert dump(profile) == before
    with original_connect(profile.path) as conn:
        assert conn.execute('SELECT count(*) FROM organization_team_setup').fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM organization_configuration').fetchone()[0] == 1


@pytest.mark.parametrize('change,reason', [
    ('key', 'idempotency_conflict'), ('input', 'idempotency_conflict'),
    ('organization', 'identity_conflict'),
])
def test_busy_receipt_recovery_rejects_changed_binding(profile, monkeypatch, change, reason):
    apply = confirmed(profile)
    team.create_team(profile.home, apply)
    before = dump(profile)
    def unavailable(*args, **kwargs):
        raise writers.LedgerWriterUnavailable('deadline')
    monkeypatch.setattr(writers._Gate, 'acquire', unavailable)
    changed = deepcopy(apply)
    if change == 'key':
        changed['idempotencyKey'] = 'different'
    elif change == 'input':
        changed['team']['executive']['name'] = 'Changed'
    else:
        changed['expectedOrganizationId'] = '00000000-0000-4000-8000-000000000001'
    with pytest.raises(OwnerPolicyError, match=reason):
        team.create_team(profile.home, changed)
    assert dump(profile) == before


@pytest.mark.parametrize('phase', ['commit', 'close'])
def test_write_busy_reconciles_only_durable_state(profile, monkeypatch, phase):
    apply = confirmed(profile)
    before = dump(profile)
    original_connect = sqlite3.connect
    rollbacks, reads = [], []
    injected = []

    class Connection(sqlite3.Connection):
        def commit(self):
            if self.writable and phase == 'commit' and not injected:
                injected.append(phase)
                raise writers.LedgerWriterUnavailable('injected_commit')
            return super().commit()

        def rollback(self):
            result = super().rollback()
            rollbacks.append(self.in_transaction)
            return result

        def close(self):
            super().close()
            if self.writable and phase == 'close' and not injected:
                injected.append(phase)
                raise writers.LedgerWriterUnavailable('injected_close')

    def connect(*args, **kwargs):
        conn = original_connect(*args, **{**kwargs, 'factory': Connection})
        conn.writable = '?mode=rw' in str(args[0])
        if '?mode=ro' in str(args[0]):
            reads.append(conn)
        return conn

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, 'connect', connect)
        if phase == 'commit':
            with pytest.raises(OwnerPolicyError, match='locked'):
                team.create_team(profile.home, apply)
            assert rollbacks == [False]
            assert dump(profile) == before
        else:
            result = team.create_team(profile.home, apply)
            assert result['state'] == 'created'
            assert rollbacks == []
        assert injected == [phase] and len(reads) == 1
    if phase == 'commit':
        result = team.create_team(profile.home, apply)
    before = dump(profile)
    assert team.create_team(profile.home, apply) == result
    assert dump(profile) == before
    with original_connect(profile.path) as conn:
        assert conn.execute('SELECT count(*) FROM organization_team_setup').fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM organization_configuration').fetchone()[0] == 1


@pytest.mark.parametrize('phase', ['open', 'read', 'close'])
@pytest.mark.parametrize('failure,expected', [
    ('busy', 'locked'), ('unreadable', 'locked'), ('oserror', 'locked'),
    ('unconfigured', 'locked'),
    ('corrupt', 'corrupt'), ('unsupported', 'unsupported'),
])
def test_reconciliation_error_policy_requires_completed_read(profile, monkeypatch, phase, failure, expected):
    apply = confirmed(profile)
    team.create_team(profile.home, apply)
    before = dump(profile)
    original_connect = sqlite3.connect
    original_connection = team._connection
    original_errors, read_opens, fetched = [], [], []

    # Observe the actual policy translation so preservation means object identity,
    # not merely another error with the same public reason.
    from contextlib import contextmanager
    @contextmanager
    def connection(*args, **kwargs):
        try:
            with original_connection(*args, **kwargs) as conn:
                yield conn
        except OwnerPolicyError as exc:
            if kwargs.get('write'):
                original_errors.append(exc)
            raise

    def unavailable(*args, **kwargs):
        raise writers.LedgerWriterUnavailable('deadline')

    def fail():
        if failure == 'oserror':
            raise OSError('synthetic unavailable storage')
        if failure in {'unsupported', 'unconfigured'}:
            raise OwnerPolicyError(failure)
        exc = sqlite3.OperationalError('synthetic read fault')
        exc.sqlite_errorcode = {'busy': sqlite3.SQLITE_BUSY,
            'unreadable': sqlite3.SQLITE_CANTOPEN, 'corrupt': sqlite3.SQLITE_CORRUPT}[failure]
        raise exc

    class Connection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if 'SELECT * FROM organization_team_setup' in sql:
                if phase == 'read':
                    fail()
                fetched.append(True)
            return super().execute(sql, *args, **kwargs)

        def close(self):
            super().close()
            if phase == 'close':
                fail()

    def connect(*args, **kwargs):
        assert '?mode=ro' in str(args[0])
        read_opens.append(True)
        if phase == 'open':
            fail()
        return original_connect(*args, **{**kwargs, 'factory': Connection})

    with monkeypatch.context() as patch:
        patch.setattr(team, '_connection', connection)
        patch.setattr(writers._Gate, 'acquire', unavailable)
        patch.setattr(sqlite3, 'connect', connect)
        with pytest.raises(OwnerPolicyError, match=expected) as caught:
            team.create_team(profile.home, apply)
        assert len(original_errors) == 1 and len(read_opens) == 1
        assert (caught.value is original_errors[0]) == (expected == 'locked')
        assert bool(fetched) == (phase == 'close')
    assert dump(profile) == before
