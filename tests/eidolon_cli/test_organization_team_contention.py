"""Committed team receipts remain recoverable while writer cleanup is pending."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import sqlite3
import threading
import time

import pytest

from eidolon_cli import organization_ledger_writer as writers
from eidolon_cli import organization_team_setup as team
from eidolon_cli.organization_owner_policy import OwnerPolicyError
from tests.eidolon_cli.test_organization_team_setup import profile, confirmed, dump


@pytest.mark.parametrize('phase', ['commit', 'close'])
def test_concurrent_create_recovers_only_committed_receipt(profile, monkeypatch, phase):
    apply = confirmed(profile)
    entered, release, requested = threading.Event(), threading.Event(), threading.Event()
    original_connect = sqlite3.connect
    original_acquire = writers._Gate.acquire
    owner = []
    failures = []
    request_times = []

    def hold():
        entered.set()
        assert release.wait(20), 'Controller did not release first writer'

    class Connection(sqlite3.Connection):
        def commit(self):
            if threading.get_ident() in owner and phase == 'commit':
                hold()
            return super().commit()

        def close(self):
            if threading.get_ident() in owner and phase == 'close':
                hold()
            return super().close()

    def connect(*args, **kwargs):
        kwargs.setdefault('factory', Connection)
        return original_connect(*args, **kwargs)

    def acquire(self, deadline, cancelled):
        if threading.get_ident() not in owner:
            request_times.append(time.monotonic())
            requested.set()
        try:
            return original_acquire(self, deadline, cancelled)
        except writers.LedgerWriterUnavailable as exc:
            failures.append((exc.reason, time.monotonic()))
            raise

    monkeypatch.setattr(sqlite3, 'connect', connect)
    monkeypatch.setattr(writers._Gate, 'acquire', acquire)

    def first():
        owner.append(threading.get_ident())
        return team.create_team(profile.home, apply)

    with ThreadPoolExecutor(2) as pool:
        first_future = pool.submit(first)
        try:
            assert entered.wait(20)
            second = pool.submit(team.create_team, profile.home, apply)
            assert requested.wait(20)
            try:
                second_result = second.result(timeout=20)
            except OwnerPolicyError as exc:
                second_result = exc.reason
            assert failures and failures[0][0] == 'deadline'
            assert failures[0][1] - request_times[0] >= writers.PUBLICATION_WAIT_SECONDS
        finally:
            release.set()
        first_result = first_future.result(timeout=20)

    # Verify durable state and exact retry even when the original second call failed.
    before = dump(profile)
    assert team.create_team(profile.home, apply) == first_result
    assert dump(profile) == before
    with original_connect(profile.path) as conn:
        assert conn.execute('SELECT count(*) FROM organization_team_setup').fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM organization_configuration').fetchone()[0] == 1
    assert second_result == (first_result if phase == 'close' else 'locked')


def test_busy_receipt_recovery_rejects_changed_intent_and_identity(profile, monkeypatch):
    apply = confirmed(profile)
    team.create_team(profile.home, apply)
    def unavailable(*args, **kwargs):
        raise writers.LedgerWriterUnavailable('deadline')
    monkeypatch.setattr(writers._Gate, 'acquire', unavailable)
    changed = deepcopy(apply)
    changed['team']['executive']['name'] = 'Changed'
    with pytest.raises(OwnerPolicyError, match='idempotency_conflict'):
        team.create_team(profile.home, changed)
    changed = {**apply, 'expectedOrganizationId': '00000000-0000-4000-8000-000000000001'}
    with pytest.raises(OwnerPolicyError, match='identity_conflict'):
        team.create_team(profile.home, changed)
