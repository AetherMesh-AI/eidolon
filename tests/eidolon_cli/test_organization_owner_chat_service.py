"""Real ledger and OS fences, deterministic provider workers that cannot spend."""
import threading
import time

import pytest

from eidolon_cli.organization_store import OrganizationStore
from eidolon_cli.organization_service import OrganizationService
from eidolon_cli import organization_owner_chat_service as chat


def wait(check):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if value := check():
            return value
        time.sleep(0.02)
    raise AssertionError('Owner-chat state did not settle')


def setup(tmp_path):
    store = OrganizationStore(tmp_path / 'organization' / 'state.db')
    service = OrganizationService(store, home=tmp_path)
    identity = next(a['identityId'] for a in store.snapshot()['agents'] if a['id'] == 'manager')
    view = service.owner_chat.open('manager', identity)
    return service, identity, view['id']


def reserve(context):
    context['reserveModelCall'](provider='fixture', model='fixture', input_limit=1000, output_limit=100)


def test_duplicate_send_and_cross_runtime_cancel_keep_fence_until_worker_exits(tmp_path, monkeypatch):
    service, identity, thread = setup(tmp_path)
    peer = OrganizationService(OrganizationStore(service.store.path), home=tmp_path)
    entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def execute(context, cancel):
        reserve(context)
        calls.append(context)
        entered.set()
        while not release.wait(0.02):
            if cancel.is_set():
                cancelled.set()
        return {'reply': 'Late reply cannot be stored'}

    monkeypatch.setattr(chat, '_execute', execute)
    try:
        sent = service.owner_chat.send(thread, identity, 'Hello', None, 'once')
        assert entered.wait(60)
        duplicate = peer.owner_chat.send(thread, identity, 'Hello', None, 'once')
        assert duplicate['turns'][0]['id'] == sent['turns'][0]['id']
        with pytest.raises(ValueError, match='active or stopping'):
            peer.owner_chat.send(thread, identity, 'Different', duplicate['messages'][-1]['id'], 'different')
        view = peer.owner_chat.cancel(thread, identity, sent['turns'][0]['id'])
        assert view['turns'][0]['status'] == 'cancelled' and not view['canSend']
        assert cancelled.wait(60)
        assert service.active_execution_count == 1
        release.set()
        wait(lambda: service.active_execution_count == 0)
        view = peer.owner_chat.read(thread, identity)
        assert len(view['messages']) == 1 and len(calls) == 1 and view['canSend']
        assert view['budget']['callsReserved'] == 1
    finally:
        release.set()
        assert service.stop(timeout=60) and peer.stop(timeout=60)


def test_timeout_and_restart_never_replay_and_profile_capacity_stays_occupied(tmp_path, monkeypatch):
    service, identity, thread = setup(tmp_path)
    entered, release = threading.Event(), threading.Event()
    def execute(context, cancel):
        reserve(context)
        entered.set()
        assert release.wait(60), 'Test did not release the blocked provider'
        return {'reply': 'Too late'}

    monkeypatch.setattr(chat, '_execute', execute)
    try:
        service.owner_chat.send(thread, identity, 'Hello', None, 'once')
        assert entered.wait(60)
        # Start the short timeout only after the physical-call fixture entered;
        # native SQLite setup must not race an artificial 150ms admission budget.
        monkeypatch.setattr(chat, 'TIMEOUT_SECONDS', 0.15)
        wait(lambda: service.store.owner_chat_read(thread, identity)['turns'][0]['status'] == 'timed_out')
        assert not service.owner_chat.read(thread, identity)['canSend']
        assert not service.stop(timeout=0)
        release.set()
        wait(lambda: service.active_execution_count == 0)
        restarted = OrganizationService(OrganizationStore(service.store.path), home=tmp_path)
        try:
            view = restarted.owner_chat.read(thread, identity)
            assert view['turns'][0]['status'] == 'timed_out'
            assert len(view['messages']) == 1
            assert restarted.owner_chat.send(thread, identity, 'Hello', None, 'once') == view
            assert not restarted.running
        finally:
            restarted.stop(timeout=60)
    finally:
        release.set()
        service.stop(timeout=60)


def test_close_admission_does_not_wait_for_blocked_sqlite_send(tmp_path, monkeypatch):
    service, identity, thread = setup(tmp_path)
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    original = service.store.owner_chat_recorded
    errors = []

    def blocked(*args):
        entered.set()
        assert release.wait(60), 'Test did not release the blocked provider'
        return original(*args)

    def send():
        try:
            service.owner_chat.send(thread, identity, 'Hello', None, 'once')
        except RuntimeError as error:
            errors.append(str(error))

    monkeypatch.setattr(service.store, 'owner_chat_recorded', blocked)
    worker = threading.Thread(target=send)
    worker.start()
    assert entered.wait(60)
    closer = threading.Thread(target=lambda: (service.close_admission(), closed.set()))
    closer.start()
    try:
        assert closed.wait(5), 'Shutdown admission waited on SQLite'
    finally:
        release.set()
        closer.join(60)
        worker.join(60)
        service.stop(timeout=60)
    assert errors and not service.owner_chat.read(thread, identity)['messages']


def test_real_subprocess_cancels_without_releasing_live_provider_fence(tmp_path, monkeypatch):
    import json
    import subprocess
    import sys
    service, identity, thread = setup(tmp_path)
    entered, release, observed = threading.Event(), threading.Event(), threading.Event()

    def execute(context, cancel):
        reserve(context)
        entered.set()
        while not release.wait(0.02):
            if cancel.is_set():
                observed.set()
        return {'reply': 'Discarded'}

    monkeypatch.setattr(chat, '_execute', execute)
    try:
        view = service.owner_chat.send(thread, identity, 'Hi', None, 'once')
        assert entered.wait(60)
        script = '''
import json, sys
from pathlib import Path
from eidolon_cli.organization_store import OrganizationStore
from eidolon_cli.organization_service import OrganizationService
home, thread, identity, turn = sys.argv[1:]
service = OrganizationService(OrganizationStore(Path(home) / 'organization' / 'state.db'), home=Path(home))
view = service.owner_chat.cancel(thread, identity, turn)
assert not view['canSend']
try:
    service.owner_chat.send(thread, identity, 'Another send', view['messages'][-1]['id'], 'another')
except ValueError as exc:
    assert 'active or stopping' in str(exc)
else:
    raise AssertionError('Live provider fence was bypassed')
print(json.dumps(view))
'''
        proc = subprocess.run([sys.executable, '-c', script, str(tmp_path), thread, identity, view['turns'][0]['id']],
                              capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr
        assert json.loads(proc.stdout)['turns'][0]['status'] == 'cancelled'
        assert observed.wait(60)
        assert service.active_execution_count == 1
        release.set()
        wait(lambda: service.active_execution_count == 0)
        assert len(service.owner_chat.read(thread, identity)['messages']) == 1
    finally:
        release.set()
        service.stop(timeout=60)


def test_two_profile_slots_and_revoked_member_keep_late_replies_out(tmp_path, monkeypatch):
    from dataclasses import replace
    service, identity, thread = setup(tmp_path)
    peer = OrganizationService(OrganizationStore(service.store.path), home=tmp_path)
    release = threading.Event()
    entered = []

    def execute(context, cancel):
        reserve(context)
        entered.append(context['identity']['identityId'])
        assert release.wait(60), 'Test did not release the blocked provider'
        return {'reply': 'Stale configuration reply'}

    monkeypatch.setattr(chat, '_execute', execute)
    members = {a['id']: a['identityId'] for a in service.store.snapshot()['agents']}
    executive = service.owner_chat.open('executive', members['executive'])
    director = service.owner_chat.open('director', members['director'])
    try:
        service.owner_chat.send(thread, identity, 'Hi', None, 'manager')
        peer.owner_chat.send(executive['id'], members['executive'], 'Hi', None, 'executive')
        wait(lambda: len(entered) == 2)
        with pytest.raises(ValueError, match='Two owner-chat replies'):
            service.owner_chat.send(director['id'], members['director'], 'Hi', None, 'director')
        assert service.owner_chat.read(director['id'], members['director'])['turns'] == []
        peer.reload_configuration(replace(peer.settings, max_workers=3))
        wait(lambda: service.store.owner_chat_read(thread, identity)['turns'][0]['status'] == 'blocked')
        release.set()
        wait(lambda: service.active_execution_count == peer.active_execution_count == 0)
        assert len(service.owner_chat.read(thread, identity)['messages']) == 1
    finally:
        release.set()
        assert service.stop(timeout=60) and peer.stop(timeout=60)


def test_shutdown_cannot_observe_an_unstarted_published_thread(tmp_path, monkeypatch):
    service, identity, thread = setup(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = threading.Thread.start
    errors = []

    def start(worker):
        if worker.name == 'organization-owner-chat':
            entered.set()
            assert release.wait(60), 'Test did not release thread publication'
        return original(worker)

    def stop():
        try:
            service.stop(timeout=0)
        except Exception as error:
            errors.append(error)

    monkeypatch.setattr(threading.Thread, 'start', start)
    sender = threading.Thread(target=lambda: service.owner_chat.send(thread, identity, 'Hi', None, 'once'))
    sender.start()
    assert entered.wait(60)
    stopper = threading.Thread(target=stop)
    stopper.start()
    assert service._stop.wait(60), 'Shutdown did not close admission'
    release.set()
    sender.join(60)
    stopper.join(60)
    assert not sender.is_alive() and not stopper.is_alive() and not errors
    assert service.stop(timeout=60)
    assert service.owner_chat.read(thread, identity)['turns'][0]['status'] == 'uncertain'


def test_renewal_snapshot_failure_is_uncertain_and_exact_retry_does_not_add_allowance(tmp_path, monkeypatch):
    service, identity, thread = setup(tmp_path)
    turn, _ = service.store.owner_chat_reserve(thread, identity, 'One explicit reservation', None, 'reserved')
    service.store.owner_chat_finish(turn, status='cancelled')
    before = service.owner_chat.read(thread, identity)
    read = service.owner_chat.read
    def failed_read(*args, **kwargs):
        raise ValueError('Snapshot could not be delivered after commit')
    monkeypatch.setattr(service.owner_chat, 'read', failed_read)
    try:
        with pytest.raises(ValueError, match='after commit'):
            service.owner_chat.renew(thread, identity, 'renew-lost-result', before['budget']['version'], before['policyGeneration'], 1)
        monkeypatch.setattr(service.owner_chat, 'read', read)
        recovered = service.owner_chat.renew(thread, identity, 'renew-lost-result', before['budget']['version'], before['policyGeneration'], 1)
        assert recovered['budget']['version'] == before['budget']['version'] + 1
        assert recovered['budget']['callsReserved'] == before['budget']['callsReserved']
        assert recovered['budget']['maxCalls'] == before['budget']['maxCalls'] + 1
        rejected = service.owner_chat.renew(thread, identity, 'different-stale-key', before['budget']['version'], before['policyGeneration'], 1)
        assert rejected['renewalRejected'] is True
    finally:
        assert service.stop(timeout=60)
