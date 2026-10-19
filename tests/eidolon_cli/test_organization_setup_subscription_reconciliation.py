"""Exact local reconciliation races; real ledger/store, synthetic vault/network."""
from contextlib import contextmanager
from dataclasses import replace
import json
import multiprocessing
from pathlib import Path
from queue import Queue
import sqlite3
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from eidolon_cli import organization_setup_subscription_admission as admission
from eidolon_cli import organization_setup_subscription_reconciliation as recovery
from eidolon_cli import organization_setup_subscription_policy as policy
from tests.eidolon_cli.test_organization_setup_subscription_recovery import recovery_ledger
from tests.eidolon_cli.test_organization_setup_subscription_admission import activated, request, infer, state, _preflight_revision
from tests.eidolon_cli.test_organization_setup_subscription_policy_integration import ledger
from tests.eidolon_cli.test_organization_setup_subscription_transport import install, wire, event, response

@pytest.fixture
def rig(recovery_ledger, monkeypatch):
    from tui_gateway import methods_organization_setup_subscription as gateway
    rig = recovery_ledger
    rig.descriptors = []
    def current_context(transport, home, expected_id=None):
        assert transport is rig.transport and home == rig.home
        assert expected_id == rig.identity['organizationId']
        context = SimpleNamespace(owner_generation=rig.generation, source_digest=_preflight_revision(rig),
            lease_generation_guard=rig.evidence.cheap_guard)
        def descriptor(revision):
            rig.descriptors.append(True)
            grant = rig.store.grant_descriptor(rig.evidence.binding, revision)
            return {'binding': rig.evidence.binding, 'revision': revision, **grant}
        service = SimpleNamespace(_store=rig.store, _lease_cancel=threading.Event(), current_descriptor=descriptor,
            status=lambda: {'connectionState': 'active', 'connectionRevision': 1, 'cleanupPending': False})
        return context, service  # Deliberately no catalog cache; reconstruct each call.
    monkeypatch.setattr(gateway, '_current_context', current_context)
    monkeypatch.setattr(gateway, '_load_catalog', lambda *args: pytest.fail('Local recovery fetched models'))
    return rig


@pytest.mark.parametrize('registered', [False, True])
def test_registered_unavailable_owner_or_platform_never_opens_grant_authority(tmp_path, monkeypatch, registered):
    if registered and sys.platform == 'darwin':
        pytest.skip('This case proves unsupported production hosts; Darwin is supported')
    import tui_gateway.server as server
    from tui_gateway import organization_credential_transport as credential
    from tests.tui_gateway.test_organization_setup_subscription_flow import call, Transport
    transport = Transport()
    monkeypatch.setattr(server, '_eidolon_home', tmp_path)
    if registered:
        monkeypatch.setitem(credential._owners, transport, (tmp_path.resolve(), object()))
    def forbidden(*args, **kwargs):
        pytest.fail('Unavailable owner/platform touched grant authority')
    monkeypatch.setattr(recovery, '_local_grant', forbidden)
    passive = {'expectedOrganizationId': '00000000-0000-4000-8000-000000000001',
        'sourceRevision': 'a' * 64, 'jobs': 'Reports', 'goals': 'Decisions', 'namingStyle': '',
        'operation': 'improve_jobs'}
    assert call('inspectSetupSubscriptionRequest', passive, transport)['result']['pending'] is None
    result = call('reconcileSetupSubscriptionRequest', {**passive,
        'idempotencyKey': 'original-intent', 'confirmed': True}, transport)
    assert result['error']['code'] == 5077
    assert not (tmp_path / 'organization' / 'state.db').exists()


def values(rig, *, improve=True):
    value = request(rig, improve=improve)
    operation = 'improve_jobs' if improve else 'generate'
    inspect = {**value, 'operation': operation}
    inspect.pop('field', None)
    inspect.pop('idempotencyKey')
    return value, inspect, {**inspect, 'idempotencyKey': value['idempotencyKey'], 'confirmed': True}


def strand(rig, monkeypatch, value):
    args = []
    socket, connects = install(monkeypatch, wire(event(response=response())))
    def denied(*arguments):
        args.append(arguments)
        return False
    with monkeypatch.context() as patch:
        patch.setattr(admission, '_dispatch_job', denied)
        patch.setattr(admission, '_failed_job', lambda *args: False)
        with pytest.raises(Exception):
            infer(rig, value)
    assert len(args) == len(connects) == 1 and not socket.sent
    assert state(rig)[0]['status'] == 'running' and state(rig)[0]['dispatched'] == 0
    # A live/late worker with uncancelled Events is fenced by durable status,
    # not by the original in-memory abandoned Event or elapsed time.
    return (*args[0][:5], threading.Event(), time.monotonic() + 120, threading.Event())


def reconcile(rig, params):
    return recovery.reconcile_unsent_request(rig.home, params, transport=rig.transport)


def inspect(rig, params):
    return recovery.inspect_unsent_request(rig.home, params, transport=rig.transport)


@pytest.mark.parametrize('improve', [True, False])
def test_registered_check_then_separate_retry_preserves_original_history(rig, monkeypatch, improve):
    import tui_gateway.server as server
    from tests.tui_gateway.test_organization_setup_subscription_flow import call
    from eidolon_cli import organization_team_setup as team
    monkeypatch.setattr(server, '_eidolon_home', rig.home)
    rig.transport.responses = Queue()
    rig.transport.write = rig.transport.responses.put
    value, passive, explicit = values(rig, improve=improve)
    strand(rig, monkeypatch, value)
    original = state(rig)[0]
    before = list(rig.conn.iterdump())
    hint = call('inspectSetupSubscriptionRequest', passive, rig.transport)['result']['pending']
    assert hint == {'version': 1, 'disposition': 'reconcile_unsent', 'idempotencyKey': value['idempotencyKey'],
        'operation': passive['operation'], 'sourceRevision': value['sourceRevision']}
    assert list(rig.conn.iterdump()) == before and rig.descriptors == []
    result = call('reconcileSetupSubscriptionRequest', explicit, rig.transport)['result']['recovery']
    assert result == {**hint, 'disposition': 'retry_unsent'}
    assert state(rig)[0] == {**original, 'status': 'failed'}
    after = list(rig.conn.iterdump())
    assert call('reconcileSetupSubscriptionRequest', explicit, rig.transport)['result']['recovery'] == result
    assert list(rig.conn.iterdump()) == after
    content = response() if improve else response(json.dumps(team.presets()[0]['team']))
    socket, connects = install(monkeypatch, wire(event(response=content)))
    method = 'improveTeamInput' if improve else 'generateTeam'
    assert 'result' in call(method, {**value, 'idempotencyKey': 'new-explicit-intent'}, rig.transport)
    assert len(connects) == 1 and socket.sent
    assert state(rig)[0] == {**original, 'status': 'failed'}


def test_passive_discovery_never_opens_service_store_or_catalog(rig, monkeypatch):
    from tui_gateway import methods_organization_setup_subscription as gateway
    value, passive, _ = values(rig)
    strand(rig, monkeypatch, value)
    def forbidden(*args, **kwargs):
        pytest.fail('Passive discovery touched grant authority')
    monkeypatch.setattr(gateway, '_current_context', forbidden)
    monkeypatch.setattr(gateway, 'subscription_preflight', forbidden)
    monkeypatch.setattr(rig.store, 'grant_descriptor', forbidden)
    monkeypatch.setattr(rig.store._backend, 'consume', forbidden)
    before = list(rig.conn.iterdump())
    assert inspect(rig, passive)['pending']['idempotencyKey'] == value['idempotencyKey']
    assert list(rig.conn.iterdump()) == before


def test_late_dispatch_failure_and_outcome_cannot_reopen_reconciled_fence(rig, monkeypatch):
    value, _, explicit = values(rig)
    args = strand(rig, monkeypatch, value)
    reconcile(rig, explicit)
    before = list(rig.conn.iterdump())
    assert admission._dispatch_job(*args) is False
    assert admission._failed_job(rig.home, args[3], args[4], True) is True
    with pytest.raises(Exception):
        admission._outcome_job(rig.home, args[3], args[4], None)
    assert list(rig.conn.iterdump()) == before
    assert not admission._Admission(rig.home, rig.transport, args[2], args[3], b'wrong-nonce',
        threading.Event(), (), 'a' * 64).begin_dispatch(args[3].request_sha256, deadline=time.monotonic() + 5)
    assert list(rig.conn.iterdump()) == before


def test_dispatch_committing_first_refuses_reconciliation_without_mutation(rig, monkeypatch):
    value, passive, explicit = values(rig)
    args = strand(rig, monkeypatch, value)
    assert admission._dispatch_job(*args) is True
    before = list(rig.conn.iterdump())
    assert inspect(rig, passive) == {'pending': None}
    with pytest.raises(Exception):
        reconcile(rig, explicit)
    assert list(rig.conn.iterdump()) == before


def test_lost_commit_reply_revalidates_failed_fence_without_catalog_or_send(rig, monkeypatch):
    value, passive, explicit = values(rig)
    strand(rig, monkeypatch, value)
    original = recovery._ledger_connection
    @contextmanager
    def lose_ack(*args, **kwargs):
        with original(*args, **kwargs) as conn:
            yield conn
        raise RuntimeError('Synthetic lost local commit acknowledgment')
    with monkeypatch.context() as patch:
        patch.setattr(recovery, '_ledger_connection', lose_ack)
        with pytest.raises(RuntimeError):
            reconcile(rig, explicit)
    assert state(rig)[0]['status'] == 'failed'
    before = list(rig.conn.iterdump())
    assert reconcile(rig, explicit)['recovery']['disposition'] == 'retry_unsent'
    assert list(rig.conn.iterdump()) == before
    assert inspect(rig, passive) == {'pending': None}
    # A renderer restart loses its old key, but there is no running blocker.
    socket, connects = install(monkeypatch, wire(event(response=response())))
    assert infer(rig, {**value, 'idempotencyKey': 'explicit-after-renderer-restart'})['suggestion'] == 'Ready'
    assert len(connects) == 1 and socket.sent


@pytest.mark.parametrize('change', ['input', 'source', 'operation', 'confirmed', 'key', 'unknown_field'])
def test_mismatched_reconciliation_never_mutates(rig, monkeypatch, change):
    value, passive, explicit = values(rig)
    strand(rig, monkeypatch, value)
    changes = {'input': {'jobs': 'Different'}, 'source': {'sourceRevision': 'b' * 64},
        'operation': {'operation': 'improve_goals'}, 'confirmed': {'confirmed': False},
        'key': {'idempotencyKey': 'different'}, 'unknown_field': {'binding': 'untrusted'}}
    before = list(rig.conn.iterdump())
    with pytest.raises(Exception):
        reconcile(rig, {**explicit, **changes[change]})
    if change in {'input', 'source', 'operation'}:
        assert inspect(rig, {**passive, **changes[change]}) == {'pending': None}
    assert list(rig.conn.iterdump()) == before


@pytest.mark.parametrize('status', ['uncertain', 'completed'])
def test_unproven_attempt_state_is_never_reconciled(rig, monkeypatch, status):
    value, passive, explicit = values(rig)
    strand(rig, monkeypatch, value)
    rig.conn.execute('UPDATE organization_team_generations SET status=?', (status,))
    before = list(rig.conn.iterdump())
    assert inspect(rig, passive) == {'pending': None}
    with pytest.raises(Exception):
        reconcile(rig, explicit)
    assert list(rig.conn.iterdump()) == before


@pytest.mark.parametrize('turnover', ['source', 'owner', 'grant'])
def test_authority_turnover_inside_reconciliation_rolls_back(rig, monkeypatch, turnover):
    from tui_gateway import organization_credential_transport as credential
    value, _, explicit = values(rig)
    strand(rig, monkeypatch, value)
    original = recovery._grant_fence
    calls = []
    def fence(grant, binding):
        result = original(grant, binding)
        if not calls:
            calls.append(True)
            if turnover == 'source':
                (rig.home / 'config.yaml').write_text('{"display":{"compact":true}}')
            elif turnover == 'owner':
                monkeypatch.setitem(credential._owners, rig.transport, (rig.home, object()))
            else:
                rig.store._retire_leases()
                # A failed current-grant attestation represents authoritative retirement.
                monkeypatch.setattr(rig.store, 'attest_subscription_binding_nonblocking',
                    lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('retired')))
        return result
    monkeypatch.setattr(recovery, '_grant_fence', fence)
    before = list(rig.conn.iterdump())
    with pytest.raises(Exception):
        reconcile(rig, explicit)
    assert list(rig.conn.iterdump()) == before


def _late_process_dispatch(home, binding_json, nonce, started, release, output):
    """A new process has no shared advisory gate or original in-memory cancel."""
    from eidolon_cli.organization_setup_subscription_usage import AttemptBindingV1
    from tui_gateway import organization_credential_transport as credential
    class Transport:
        closed = False
    transport, generation = Transport(), object()
    home = Path(home)
    credential._owners[transport] = (home, generation)
    started.set()
    if not release.wait(20):
        output.put('barrier_timeout')
        return
    binding = AttemptBindingV1(**json.loads(binding_json))
    result = admission._dispatch_job(home, transport, SimpleNamespace(owner_generation=generation),
        binding, nonce, threading.Event(), time.monotonic() + 5, threading.Event())
    output.put(result)


def test_committed_reconciliation_fences_real_delayed_worker_in_new_process(rig, monkeypatch):
    value, _, explicit = values(rig)
    args = strand(rig, monkeypatch, value)
    context = multiprocessing.get_context('spawn')
    started, release, output = context.Event(), context.Event(), context.Queue()
    child = context.Process(target=_late_process_dispatch, args=(str(rig.home), args[3].canonical_bytes().decode(),
        args[4], started, release, output))
    child.start()
    try:
        assert started.wait(20)
        assert reconcile(rig, explicit)['recovery']['disposition'] == 'retry_unsent'
        before = list(rig.conn.iterdump())
        release.set()
        assert output.get(timeout=20) is False
        child.join(20)
        assert child.exitcode == 0 and list(rig.conn.iterdump()) == before
    finally:
        release.set()
        child.join(20)
        if child.is_alive():
            child.terminate()
            child.join(5)


def _external_writer(path, started, release, output):
    with sqlite3.connect(path, isolation_level=None) as conn:
        conn.execute('BEGIN IMMEDIATE')
        started.set()
        if not release.wait(20):
            output.put('barrier_timeout')
            return
        conn.commit()
        output.put('committed')


def test_external_process_transaction_blocks_local_check_until_explicit_recheck(rig, monkeypatch):
    from eidolon_cli.organization_owner_policy import OwnerPolicyError
    value, _, explicit = values(rig)
    strand(rig, monkeypatch, value)
    context = multiprocessing.get_context('spawn')
    started, release, output = context.Event(), context.Event(), context.Queue()
    child = context.Process(target=_external_writer, args=(str(rig.home / 'organization' / 'state.db'),
        started, release, output))
    child.start()
    try:
        assert started.wait(20)
        before = list(rig.conn.iterdump())
        with pytest.raises(OwnerPolicyError, match='subscription_reconciliation_unavailable'):
            reconcile(rig, explicit)
        assert list(rig.conn.iterdump()) == before
        release.set()
        assert output.get(timeout=20) == 'committed'
        child.join(20)
        assert child.exitcode == 0
        assert state(rig)[0]['status'] == 'running'
        assert reconcile(rig, explicit)['recovery']['disposition'] == 'retry_unsent'
    finally:
        release.set()
        child.join(20)
        if child.is_alive():
            child.terminate()
            child.join(5)


def test_late_worker_unknown_ack_cannot_mint_permit_after_local_fence(rig, monkeypatch):
    value, _, explicit = values(rig)
    args = strand(rig, monkeypatch, value)
    started, release, done = threading.Event(), threading.Event(), threading.Event()
    results = []
    def delayed():
        started.set()
        assert release.wait(10)
        try:
            results.append(admission._dispatch_job(*args))
        finally:
            done.set()
    worker = threading.Thread(target=delayed)
    worker.start()
    try:
        assert started.wait(5)
        reconcile(rig, explicit)
        release.set()
        assert done.wait(10)
        worker.join(10)
        assert results == [False]
        assert state(rig)[0]['status'] == 'failed' and state(rig)[0]['dispatched'] == 0
    finally:
        release.set()
        worker.join(10)


@pytest.mark.parametrize('coordinated', [True, False])
def test_registered_busy_check_is_fixed_safe_and_requires_later_explicit_check(rig, monkeypatch, coordinated):
    import tui_gateway.server as server
    from eidolon_cli.organization_store import OrganizationStore
    from tests.tui_gateway.test_organization_setup_subscription_flow import call
    monkeypatch.setattr(server, '_eidolon_home', rig.home)
    rig.transport.responses = Queue()
    rig.transport.write = rig.transport.responses.put
    value, _, explicit = values(rig)
    strand(rig, monkeypatch, value)
    store = OrganizationStore.__new__(OrganizationStore)
    store.path, store.settings = rig.home / 'organization' / 'state.db', rig.settings
    entered, release = threading.Event(), threading.Event()
    errors = []
    @contextmanager
    def external():
        with sqlite3.connect(store.path, isolation_level=None) as conn:
            conn.execute('BEGIN IMMEDIATE')
            yield conn
            conn.commit()
    def hold():
        try:
            with (store._write() if coordinated else external()):
                entered.set()
                assert release.wait(15)
        except BaseException as error:
            errors.append(error)
    worker = threading.Thread(target=hold)
    worker.start()
    try:
        assert entered.wait(10)
        before = list(rig.conn.iterdump())
        result = call('reconcileSetupSubscriptionRequest', explicit, rig.transport)
        assert result['error'] == {'code': 5076,
            'message': 'Organization owner policy: subscription_reconciliation_unavailable',
            'data': {'reason': 'subscription_reconciliation_unavailable'}}
        assert not release.is_set() and list(rig.conn.iterdump()) == before
        release.set()
        worker.join(10)
        assert not worker.is_alive() and not errors
        assert state(rig)[0]['status'] == 'running'
        assert call('reconcileSetupSubscriptionRequest', explicit, rig.transport)['result']['recovery']['disposition'] == 'retry_unsent'
    finally:
        release.set()
        worker.join(10)


@pytest.mark.parametrize('boundary', ['ledger', 'grant_context'])
def test_registered_storage_exception_does_not_expose_private_text(rig, monkeypatch, boundary):
    import tui_gateway.server as server
    from tests.tui_gateway.test_organization_setup_subscription_flow import call
    monkeypatch.setattr(server, '_eidolon_home', rig.home)
    rig.transport.responses = Queue()
    rig.transport.write = rig.transport.responses.put
    value, _, explicit = values(rig)
    strand(rig, monkeypatch, value)
    @contextmanager
    def failed(*args, **kwargs):
        raise sqlite3.OperationalError('synthetic-private-path-and-SQL')
        yield
    if boundary == 'ledger':
        monkeypatch.setattr(recovery, '_ledger_connection', failed)
    else:
        monkeypatch.setattr(recovery, '_local_grant', lambda *args: (_ for _ in ()).throw(
            sqlite3.OperationalError('synthetic-private-path-and-SQL')))
    before = list(rig.conn.iterdump())
    result = call('reconcileSetupSubscriptionRequest', explicit, rig.transport)
    assert result['error']['code'] == 5076
    assert result['error']['data'] == {'reason': 'subscription_reconciliation_unavailable'}
    assert 'synthetic-private' not in json.dumps(result)
    assert list(rig.conn.iterdump()) == before


def test_reconciliation_preserves_legacy_debt_and_original_typed_attempt(rig, monkeypatch):
    from eidolon_cli.organization_team_budget import operation_counts
    org = rig.identity['organizationId']
    rig.conn.execute('INSERT INTO organization_team_generations '
        '(idempotency_key,organization_id,input_hash,source_revision,status,dispatched,reserved_tokens,created) '
        "VALUES (?,?,?,?,'failed',1,700,0)", ('legacy-debt', org, 'legacy-hash', 'legacy-source'))
    rig.conn.execute('INSERT INTO organization_team_generation_operations VALUES (?,?,?,?,?)',
        ('legacy-debt', org, 'legacy-hash', 'legacy', 1))
    value, _, explicit = values(rig)
    # The helper checks its exact original row, so create the strand without the
    # helper's first-row assertion (the retained legacy debt sorts separately).
    socket, _ = install(monkeypatch, wire(event(response=response())))
    with monkeypatch.context() as patch:
        patch.setattr(admission, '_dispatch_job', lambda *args: False)
        patch.setattr(admission, '_failed_job', lambda *args: False)
        with pytest.raises(Exception):
            infer(rig, value)
    before_debt = dict(rig.conn.execute("SELECT * FROM organization_team_generations WHERE idempotency_key='legacy-debt'").fetchone())
    before_attempt = tuple(rig.conn.execute('SELECT * FROM organization_setup_subscription_attempts').fetchone())
    before_counts = operation_counts(rig.conn)
    assert reconcile(rig, explicit)['recovery']['disposition'] == 'retry_unsent'
    assert dict(rig.conn.execute("SELECT * FROM organization_team_generations WHERE idempotency_key='legacy-debt'").fetchone()) == before_debt
    assert tuple(rig.conn.execute('SELECT * FROM organization_setup_subscription_attempts').fetchone()) == before_attempt
    assert operation_counts(rig.conn) == before_counts and before_counts['legacy'] == 1
    assert not socket.sent


@pytest.mark.parametrize('hostile', ['missing_attempt', 'corrupt_binding', 'competing_running'])
def test_corrupt_or_ambiguous_ledger_never_offers_or_reconciles(rig, monkeypatch, hostile):
    value, passive, explicit = values(rig)
    strand(rig, monkeypatch, value)
    if hostile == 'missing_attempt':
        rig.conn.execute('DROP TRIGGER organization_setup_subscription_attempts_immutable_delete')
        rig.conn.execute('DELETE FROM organization_setup_subscription_attempts')
    elif hostile == 'corrupt_binding':
        rig.conn.execute('DROP TRIGGER organization_setup_subscription_attempts_immutable_update')
        rig.conn.execute("UPDATE organization_setup_subscription_attempts SET binding_json='{}'")
    else:
        rig.conn.execute('INSERT INTO organization_team_generations '
            '(idempotency_key,organization_id,input_hash,source_revision,status,created) '
            "VALUES (?,?,?,?,'running',0)", ('other', rig.identity['organizationId'], 'different', 'different'))
    before = list(rig.conn.iterdump())
    assert inspect(rig, passive) == {'pending': None}
    with pytest.raises(Exception):
        reconcile(rig, explicit)
    assert list(rig.conn.iterdump()) == before


def test_precommit_interruption_rolls_back_then_explicit_recheck_succeeds(rig, monkeypatch):
    value, _, explicit = values(rig)
    strand(rig, monkeypatch, value)
    original = recovery._ledger_connection
    @contextmanager
    def interrupted(*args, **kwargs):
        with original(*args, **kwargs) as conn:
            yield conn
            assert conn.execute('SELECT status FROM organization_team_generations').fetchone()[0] == 'failed'
            raise RuntimeError('Synthetic crash before commit')
    before = list(rig.conn.iterdump())
    with monkeypatch.context() as patch:
        patch.setattr(recovery, '_ledger_connection', interrupted)
        with pytest.raises(RuntimeError):
            reconcile(rig, explicit)
    assert list(rig.conn.iterdump()) == before and state(rig)[0]['status'] == 'running'
    assert reconcile(rig, explicit)['recovery']['disposition'] == 'retry_unsent'


def _crash_before_transaction_commit(home, binding_json, nonce):
    """Abrupt process death skips the context manager's rollback/commit paths."""
    import os
    from eidolon_cli.organization_setup_subscription_usage import AttemptBindingV1
    binding = AttemptBindingV1(**json.loads(binding_json))
    with admission._ledger_connection(Path(home), write=True) as conn:
        row = admission._exact_attempt(conn, binding, nonce, dispatched=False)
        assert row['status'] == 'running'
        cursor = conn.execute("UPDATE organization_team_generations SET status='failed' WHERE idempotency_key=? AND status='running' AND dispatched=0",
            (binding.idempotency_key,))
        assert cursor.rowcount == 1
        os._exit(23)


def test_abrupt_process_death_before_commit_preserves_running_then_explicit_check(rig, monkeypatch):
    value, passive, explicit = values(rig)
    args = strand(rig, monkeypatch, value)
    before = list(rig.conn.iterdump())
    context = multiprocessing.get_context('spawn')
    child = context.Process(target=_crash_before_transaction_commit,
        args=(str(rig.home), args[3].canonical_bytes().decode(), args[4]))
    child.start()
    try:
        child.join(20)
        assert child.exitcode == 23
    finally:
        if child.is_alive():
            child.terminate()
            child.join(5)
    assert list(rig.conn.iterdump()) == before
    assert state(rig)[0]['status'] == 'running' and state(rig)[0]['dispatched'] == 0
    assert inspect(rig, passive)['pending']['idempotencyKey'] == explicit['idempotencyKey']
    assert reconcile(rig, explicit)['recovery']['disposition'] == 'retry_unsent'


def test_reentered_input_reconciliation_has_no_service_catalog_dependency(rig, monkeypatch):
    from tui_gateway import methods_organization_setup_subscription as gateway
    value, passive, explicit = values(rig)
    strand(rig, monkeypatch, value)
    def forbidden(*args, **kwargs):
        pytest.fail('Restart reconciliation accessed model preflight/cache')
    monkeypatch.setattr(gateway, 'subscription_preflight', forbidden)
    monkeypatch.setattr(gateway, '_catalog', forbidden)
    assert inspect(rig, passive)['pending']['idempotencyKey'] == explicit['idempotencyKey']
    assert rig.descriptors == []
    assert reconcile(rig, explicit)['recovery']['disposition'] == 'retry_unsent'
    assert rig.descriptors == [True]


@pytest.mark.parametrize('changed', ['account', 'epoch', 'revision'])
def test_exact_current_grant_binding_is_required_for_terminalization(rig, monkeypatch, changed):
    value, _, explicit = values(rig)
    strand(rig, monkeypatch, value)
    original = recovery._local_grant
    def replaced(*args):
        grant = original(*args)
        return replace(grant, **{'account': {'account_digest': 'b' * 64},
            'epoch': {'epoch': '00000000-0000-4000-8000-000000000099'},
            'revision': {'revision': grant.revision + 1}}[changed])
    monkeypatch.setattr(recovery, '_local_grant', replaced)
    before = list(rig.conn.iterdump())
    with pytest.raises(Exception):
        reconcile(rig, explicit)
    assert list(rig.conn.iterdump()) == before


def test_local_check_uses_publication_wait_bound_and_owner_event_cancellation(rig, monkeypatch):
    value, _, explicit = values(rig)
    strand(rig, monkeypatch, value)
    original = recovery._ledger_connection
    captured = []
    @contextmanager
    def observe(*args, **kwargs):
        captured.append(kwargs)
        assert 0 < kwargs['deadline'] - time.monotonic() <= recovery.PUBLICATION_WAIT_SECONDS
        assert kwargs['cancelled']() is False
        with original(*args, **kwargs) as conn:
            yield conn
    monkeypatch.setattr(recovery, '_ledger_connection', observe)
    reconcile(rig, explicit)
    rig.transport.closed = True
    assert len(captured) == 1 and captured[0]['cancelled']() is True
