"""Explicit recovery against real private store/ledger; synthetic network and vault only."""
from contextlib import nullcontext
from dataclasses import replace
from functools import wraps
import json
import os
from queue import Queue
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from eidolon_cli import organization_setup_subscription_auth as auth
from eidolon_cli import organization_setup_subscription_admission as admission
from eidolon_cli import organization_setup_subscription_policy as policy
from eidolon_cli.organization_setup_subscription_host import HostAuthority, PendingAttempts
from eidolon_cli.organization_setup_subscription_store import SubscriptionStore, StoreError
from tests.eidolon_cli.test_organization_setup_subscription_auth import (
    FakeListener, ORG, OWNER, callback, synthetic_vault,
)
from tests.eidolon_cli.test_organization_setup_subscription_admission import (
    activated, request, state, infer,
)
from tests.eidolon_cli.test_organization_setup_subscription_policy_integration import ledger
from tests.eidolon_cli.test_organization_setup_subscription_transport import install, wire, event, response

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='Synthetic private-store filesystem proof requires POSIX')


@pytest.fixture
def recovery_rig(tmp_path, monkeypatch, synthetic_vault):
    root = tmp_path / 'profile'
    root.mkdir(mode=0o700)
    host = PendingAttempts(HostAuthority.capture(lambda: root, lambda: root), synthetic=True)
    store = SubscriptionStore(root, backend=synthetic_vault)
    valid = [True]
    context = auth.OwnerContext(str(root), ORG, OWNER, 'a' * 64, object(), object(),
        lambda: valid[0], nullcontext, lambda: valid[0], lambda: valid[0])
    opened, exchanges, puts = [], [], []
    put = synthetic_vault.put
    monkeypatch.setattr(synthetic_vault, 'put', lambda *args: puts.append(True) or put(*args))
    monkeypatch.setattr(auth, 'LoopbackListener', FakeListener)
    def http(url, form=None):
        assert url == auth.TOKEN
        exchanges.append(form)
        return {'access_token': 'synthetic-access', 'refresh_token': 'synthetic-refresh',
            'id_token': 'synthetic-id', 'token_type': 'Bearer', 'expires_in': 3600,
            'scope': auth.SCOPES}
    def service():
        result = auth.SubscriptionAuthService(store, host, context, synthetic=True,
            browser_open=lambda url: opened.append(url) or True, http=http)
        result._verifier = SimpleNamespace(verify_initial=lambda *args: {
            'subject': 'synthetic-account', 'audience': 'oaiapp_synthetic',
            'nonce': 'synthetic-nonce', 'authTime': None, 'authorizedParty': None})
        return result
    return SimpleNamespace(host=host, store=store, service=service(), restart=service,
        opened=opened, exchanges=exchanges, puts=puts, vault=synthetic_vault, valid=valid)


def fail_publication(rig, monkeypatch, where):
    fired = []
    if where == 'initialization':
        original = rig.store._write
        def write(name, data, **kwargs):
            original(name, data, **kwargs)
            if name == 'ticket-anchor.json' and not fired:
                fired.append(True)
                raise StoreError('publication_uncertain')
        monkeypatch.setattr(rig.store, '_write', write)
    elif where == 'candidate':
        original = rig.store._decrypt
        def decrypt(state):
            if not fired:
                fired.append(True)
                raise StoreError('publication_uncertain')
            return original(state)
        monkeypatch.setattr(rig.store, '_decrypt', decrypt)
    else:
        original = rig.store._write
        def write(name, data, **kwargs):
            original(name, data, **kwargs)
            value = json.loads(data) if name == 'current.json' else {}
            if value.get('body', {}).get('phase') == 'active' and not fired:
                fired.append(True)
                raise StoreError('publication_uncertain')
        monkeypatch.setattr(rig.store, '_write', write)
    rig.service.begin(0)
    assert callback(rig.service._attempt)
    assert fired and rig.service.status()['stage'] == 'publication_uncertain'
    assert rig.host.inspect().stage == 'publishing'
    assert len(rig.opened) == len(rig.exchanges) == 1
    assert len(rig.puts) == int(where != 'initialization')


@pytest.mark.parametrize('where', ['initialization', 'candidate', 'committed'])
@pytest.mark.parametrize('restart', [False, True])
def test_auth_recovery_is_exact_local_and_restart_safe(recovery_rig, monkeypatch, where, restart):
    rig = recovery_rig
    fail_publication(rig, monkeypatch, where)
    record = rig.host.inspect()
    binding = rig.store.returning_binding()
    assert record.publication_binding_digest == rig.store._binding_digest(binding)
    service = rig.restart() if restart else rig.service
    result = service.recover_operation_current()
    if where in {'initialization', 'candidate'}:
        assert result['state'] == 'local_retired'
        assert result['receipt'] == {'kind': 'local', 'revision': 0,
            'localRetired': True, 'remoteRevocation': 'unconfirmed'}
        assert rig.host.inspect().stage == 'locally_retired'
        assert service.status()['stage'] == 'locally_retired'
        assert service.recover_operation_current() == result
        assert rig.store.returning_binding() == binding
        assert rig.store.status() == {'revision': 0, 'state': 'disconnected', 'cleanup_pending': False}
        assert rig.store.history_status()['state'] == 'available'
        service.begin(0)  # A new explicit consent only; no exchange until callback.
        assert len(rig.opened) == 2
        service.cancel(service._attempt.attempt_id)
    else:
        assert result['state'] == 'none' and result['receipt'] is None
        assert rig.host.inspect().stage == 'published'
        assert service.status()['connectionState'] == 'active'
    assert result['catalogRevision'] is None and result['cleanupPending'] is False
    assert len(rig.exchanges) == 1 and len(rig.puts) == int(where != 'initialization')


@pytest.mark.parametrize('restart', [False, True])
def test_legacy_publication_requires_exact_live_verified_attempt(recovery_rig, monkeypatch, restart):
    rig = recovery_rig
    fail_publication(rig, monkeypatch, 'candidate')
    with rig.host._locked():
        rig.host._save(replace(rig.host._read_attempt(), publication_binding_digest=None))
    service = rig.restart() if restart else rig.service
    if restart:
        with pytest.raises(auth.AuthError):
            service.recover_operation_current()
        assert rig.host.inspect().stage == 'publishing'
    else:
        assert service.recover_operation_current()['state'] == 'local_retired'
        assert rig.host.inspect().publication_binding_digest is not None
    assert len(rig.exchanges) == len(rig.puts) == 1


@pytest.mark.parametrize('hostile', ['binding', 'source', 'revision', 'anchor', 'owner'])
def test_auth_recovery_refuses_unproven_identity_without_send(recovery_rig, monkeypatch, hostile):
    rig = recovery_rig
    fail_publication(rig, monkeypatch, 'candidate')
    if hostile in {'binding', 'source', 'revision'}:
        changes = {'binding': {'publication_binding_digest': 'b' * 64},
            'source': {'source_digest': 'b' * 64}, 'revision': {'expected_store_revision': 2}}[hostile]
        with rig.host._locked():
            rig.host._save(replace(rig.host._read_attempt(), **changes))
    elif hostile == 'anchor':
        (rig.store.directory / 'ticket-anchor.json').unlink()
    else:
        rig.valid[0] = False
    with pytest.raises(auth.AuthError):
        rig.restart().recover_operation_current()
    assert rig.host.inspect().stage == 'publishing'
    assert len(rig.exchanges) == len(rig.puts) == 1


def test_auth_cleanup_debt_blocks_host_retirement_until_exact_cleanup(recovery_rig, monkeypatch):
    rig = recovery_rig
    fail_publication(rig, monkeypatch, 'candidate')
    delete = rig.vault.delete
    monkeypatch.setattr(rig.vault, 'delete', lambda *args: (_ for _ in ()).throw(RuntimeError('synthetic failure')))
    with pytest.raises(auth.AuthError):
        rig.restart().recover_operation_current()
    assert rig.host.inspect().stage == 'publishing'
    assert rig.store.status()['cleanup_pending'] is True
    monkeypatch.setattr(rig.vault, 'delete', delete)
    assert rig.restart().recover_operation_current()['state'] == 'local_retired'
    assert len(rig.exchanges) == len(rig.puts) == 1


def test_lost_host_retirement_acknowledgment_revalidates_without_resend(recovery_rig, monkeypatch):
    rig = recovery_rig
    fail_publication(rig, monkeypatch, 'candidate')
    save = rig.host._save
    fired = []
    def fail(record):
        save(record)
        if record.stage == 'locally_retired' and not fired:
            fired.append(True)
            raise RuntimeError('synthetic lost acknowledgment')
    monkeypatch.setattr(rig.host, '_save', fail)
    with pytest.raises(auth.AuthError):
        rig.service.recover_operation_current()
    assert rig.host.inspect().stage == 'locally_retired'
    assert rig.restart().recover_operation_current()['state'] == 'local_retired'
    assert len(rig.exchanges) == len(rig.puts) == 1


@pytest.fixture
def recovery_ledger(tmp_path, monkeypatch, request):
    # A cold gateway import starts a real passive update daemon. Finish that
    # exact startup work under supported opt-out before capturing ledger source
    # evidence or installing synthetic sender sockets.
    bootstrap = tmp_path / 'gateway-bootstrap'
    bootstrap.mkdir()
    (bootstrap / 'config.yaml').write_text('updates:\n  check: false\n', encoding='utf-8')
    from eidolon_cli import banner
    completions = []
    original_daemon = banner._daemon
    with monkeypatch.context() as startup:
        startup.setenv('HERMES_HOME', str(bootstrap))
        def tracked_daemon(name, target):
            done = threading.Event()
            completions.append(done)
            def run():
                try:
                    target()
                finally:
                    done.set()
            original_daemon(name, run)
        startup.setattr(banner, '_daemon', tracked_daemon)
        from eidolon_cli.config import load_config
        assert load_config()['updates']['check'] is False
        from tui_gateway import server  # noqa: F401
        for done in completions:
            assert done.wait(10), 'Passive startup did not finish under the fixture profile'
    ledger = request.getfixturevalue('ledger')
    # Bypass only OS admission in this explicitly synthetic filesystem proof.
    # Production platform eligibility is unchanged and separately host-tested.
    from tui_gateway.organization_credential_transport import owner_generation
    def synthetic_owner(home, transport):
        generation = owner_generation(transport)
        policy.require_local_owner(transport, home, generation)
        return generation
    monkeypatch.setattr(policy, '_runtime_owner', synthetic_owner)
    return request.getfixturevalue('activated')


def _retry_error_category(error):
    """Fixed test diagnostics only; never format errors or inspect unknown types."""
    from eidolon_cli.organization_owner_policy import OwnerPolicyError
    from eidolon_cli.organization_setup_subscription_transport import TransportError
    from eidolon_cli.organization_setup_subscription_usage import SAFE_ERRORS
    classes = {StoreError: 'StoreError', OwnerPolicyError: 'OwnerPolicyError',
        TransportError: 'TransportError', sqlite3.OperationalError: 'OperationalError',
        RuntimeError: 'RuntimeError', OSError: 'OSError', ValueError: 'ValueError'}
    category = {'class': classes.get(type(error), 'other'), 'code': 'other'}
    if type(error) is sqlite3.OperationalError:
        attributes = vars(error)
        code = attributes.get('sqlite_errorcode') if type(attributes) is dict else None
        if type(code) is int:
            category['code'] = {sqlite3.SQLITE_BUSY: 'SQLITE_BUSY', sqlite3.SQLITE_LOCKED: 'SQLITE_LOCKED',
                sqlite3.SQLITE_CANTOPEN: 'SQLITE_CANTOPEN', sqlite3.SQLITE_READONLY: 'SQLITE_READONLY',
                sqlite3.SQLITE_IOERR: 'SQLITE_IOERR', sqlite3.SQLITE_FULL: 'SQLITE_FULL'}.get(code & 255, 'other')
    elif type(error) in {StoreError, OwnerPolicyError, TransportError}:
        attributes = vars(error)
        code = attributes.get('code' if type(error) is TransportError else 'reason') if type(attributes) is dict else None
        allowed = SAFE_ERRORS if type(error) is TransportError else {
            'connection_unavailable', 'unsafe_filesystem', 'operation_failed', 'grant_expired',
            'lease_cancelled', 'owner_generation_changed', 'stale_revision', 'invalid_binding',
            'source_conflict', 'locked', 'unreadable', 'corrupt', 'subscription_attempt_conflict'}
        if type(code) is str and code in allowed:
            category['code'] = code
    return category


def _retry_socket_category(socket, read_calls):
    """Only bounded synthetic byte counts and phases; never request/response data."""
    from tests.eidolon_cli.test_organization_setup_subscription_transport import Socket
    if type(socket) is not Socket:
        return {'phase': 'unavailable'}
    fields = vars(socket)
    if type(fields) is not dict:
        return {'phase': 'unavailable'}
    sent, received, remaining = fields.get('sent'), fields.get('received'), fields.get('data')
    rejected = fields.get('rejected_connections')
    if (type(sent) is not list or type(received) is not int or received < 0
            or type(remaining) is not bytearray or type(read_calls) is not int or read_calls < 0
            or type(rejected) is not int or rejected < 0):
        return {'phase': 'unavailable'}
    writes = min(3, len(sent))
    return {'writePhase': ('none', 'headers_only', 'headers_and_body', 'other')[writes],
        'postWriteObserved': any(type(frame) is bytes and frame.startswith(b'POST /v1/responses HTTP/1.1\r\n') for frame in sent[:3]),
        'writes': writes, 'readCalls': min(32, read_calls), 'rejectedConnections': min(2, rejected),
        'receivePhase': 'none' if received == 0 else 'some',
        'remainingPhase': 'none' if len(remaining) == 0 else 'some',
        'receivedBytes': min(8 * 1024 * 1024 + 1, received),
        'remainingBytes': min(8 * 1024 * 1024 + 1, len(remaining))}


def _capture_retry_boundaries(monkeypatch, observed_socket=None):
    """Observe the existing one-shot call, without changing a gate or retrying."""
    from eidolon_cli import organization_setup_subscription_transport as sender
    from eidolon_cli.organization_setup_subscription_usage import SAFE_ERRORS, TransportResult
    events, lock = [], threading.Lock()
    reads = [0]
    if observed_socket is not None:
        original_connect, original_recv = sender._Connection.connect, observed_socket.recv
        @wraps(original_connect)
        def connect(connection):
            result = original_connect(connection)
            try:
                emit({'stage': 'connection', 'result': 'exact_socket' if connection.sock is observed_socket else 'other_socket'})
            except BaseException:
                emit({'stage': 'connection', 'result': 'diagnostic_error'})
            return result
        @wraps(original_recv)
        def recv(count):
            try:
                with lock:
                    reads[0] = min(32, reads[0] + 1)
            except BaseException:
                pass
            return original_recv(count)
        monkeypatch.setattr(sender._Connection, 'connect', connect)
        monkeypatch.setattr(observed_socket, 'recv', recv)
    def emit(value):
        try:
            with lock:
                if len(events) < 32:
                    events.append(value)
        except BaseException:
            pass  # An observer failure must not change the original call.
    def wrap(module, name, stage):
        original = getattr(module, name)
        @wraps(original)
        def observed(*args, **kwargs):
            try:
                result = original(*args, **kwargs)
            except BaseException as error:
                try:
                    emit({'stage': stage, 'result': 'exception', 'error': _retry_error_category(error)})
                except BaseException:
                    emit({'stage': stage, 'result': 'diagnostic_error'})
                raise
            try:
                if stage == 'transport' and type(result) is TransportResult:
                    observation = result.observation
                    emit({'stage': stage, 'result': 'returned',
                        'dispatch': observation.dispatch_state if observation.dispatch_state in {'not_sent', 'possible_send'} else 'other',
                        'terminal': observation.terminal_kind if observation.terminal_kind in {'none', 'unknown', 'completed', 'failed', 'incomplete'} else 'other',
                        'error': result.delivery_error_code if result.delivery_error_code in SAFE_ERRORS else 'none' if result.delivery_error_code is None else 'other',
                        'accounted': result.accounting_committed is True})
                else:
                    value = 'true' if result is True else 'false' if result is False else (
                        result if type(result) is str and result in {'committed', 'not_committed', 'unknown'} else 'returned')
                    emit({'stage': stage, 'result': value})
            except BaseException:
                emit({'stage': stage, 'result': 'diagnostic_error'})
            return result
        monkeypatch.setattr(module, name, observed)
    for name, stage in (('_dispatch_job', 'dispatch'), ('_outcome_job', 'outcome'),
                        ('_failed_job', 'failure'), ('_persist', 'persistence'),
                        ('infer_subscription', 'inference')):
        wrap(admission, name, stage)
    wrap(sender, 'send_once', 'transport')
    def snapshot(conn, keys, socket):
        with lock:
            result = {'boundaries': list(events),
                'postWriteObserved': _retry_socket_category(socket, reads[0]).get('postWriteObserved', False)}
        if socket is observed_socket:
            result['socket'] = _retry_socket_category(socket, reads[0])
        try:
            rows = []
            for key in keys:
                row = conn.execute('SELECT status,dispatched FROM organization_team_generations WHERE idempotency_key=?', (key,)).fetchone()
                counts = [conn.execute('SELECT count(*) FROM ' + table + ' WHERE idempotency_key=?', (key,)).fetchone()[0]
                    for table in ('organization_setup_subscription_attempts', 'organization_setup_subscription_dispatches',
                                  'organization_setup_subscription_outcomes')]
                outcome = conn.execute('SELECT outcome_json FROM organization_setup_subscription_outcomes WHERE idempotency_key=?', (key,)).fetchone()
                terminal = json.loads(outcome[0]).get('terminal_kind') if outcome is not None else None
                rows.append({'phase': 'absent' if row is None else row['status'] if row['status'] in {'running', 'failed', 'uncertain', 'completed'} else 'other',
                    'dispatched': 'absent' if row is None else 'yes' if row['dispatched'] == 1 else 'no' if row['dispatched'] == 0 else 'other',
                    'counts': [min(2, count) for count in counts],
                    'terminal': 'absent' if outcome is None else terminal if type(terminal) is str and terminal in {'completed', 'failed', 'incomplete'} else 'other'})
            result['attempts'] = rows  # Original failed intent first, new explicit intent second; no IDs.
        except BaseException as error:
            result['snapshotError'] = _retry_error_category(error)
        return result
    return snapshot


@pytest.mark.parametrize('improve', [True, False])
def test_registered_retry_offer_preserves_failed_row_and_allows_only_explicit_new_intent(
        recovery_ledger, monkeypatch, improve):
    import tui_gateway.server as server
    from tests.tui_gateway.test_organization_setup_subscription_flow import call
    from eidolon_cli import organization_team_budget as budget
    rig = recovery_ledger
    monkeypatch.setattr(server, '_eidolon_home', rig.home)
    rig.transport.responses = Queue()
    rig.transport.write = rig.transport.responses.put
    socket, connects = install(monkeypatch, wire(event(response=response())))
    dispatch = admission._dispatch_job
    monkeypatch.setattr(admission, '_dispatch_job', lambda *args: False)
    value = request(rig, improve=improve)
    method = 'improveTeamInput' if improve else 'generateTeam'
    error = call(method, value, rig.transport)['error']
    offer = {'version': 1, 'disposition': 'retry_unsent', 'idempotencyKey': value['idempotencyKey'],
        'operation': 'improve_jobs' if improve else 'generate', 'sourceRevision': value['sourceRevision']}
    assert error == {'code': 5076, 'message': 'Team setup is unavailable',
        'data': {'reason': 'unavailable', 'recovery': offer}}
    assert not socket.sent
    old = state(rig)[0]
    before = list(rig.conn.iterdump())
    assert call(method, value, rig.transport)['error']['data'] == {'reason': 'generation_failed', 'recovery': offer}
    assert list(rig.conn.iterdump()) == before and len(connects) == 1
    monkeypatch.setattr(admission, '_dispatch_job', dispatch)
    from eidolon_cli import organization_team_setup as team
    content = 'Ready' if improve else json.dumps(team.presets()[0]['team'])
    # Improving requires structured output; the sender fixture wraps a plain suggestion.
    if improve:
        content = None
    socket, new_connects = install(monkeypatch, wire(event(response=response() if improve else response(content))))
    diagnostic = _capture_retry_boundaries(monkeypatch, socket)
    result = call(method, {**value, 'idempotencyKey': 'explicit-new-intent'}, rig.transport)
    assert 'result' in result, json.dumps(diagnostic(rig.conn, (value['idempotencyKey'], 'explicit-new-intent'), socket), sort_keys=True)
    assert state(rig)[1] == old  # Keys sort explicit-new-intent before inference.
    assert len(new_connects) == 1 and socket.sent
    assert budget.operation_counts(rig.conn)['total'] == 1


@pytest.mark.parametrize('fault', ['body_guard', 'truncated_receive'])
def test_retry_diagnostics_distinguish_partial_send_from_truncated_receive(recovery_ledger, monkeypatch, fault):
    from eidolon_cli import organization_setup_subscription_transport as sender
    rig = recovery_ledger
    data = wire(event(response=response()))
    socket, connects = install(monkeypatch, data if fault == 'body_guard' else data[:-3])
    if fault == 'body_guard':
        original = admission._Admission.try_send_allowed
        monkeypatch.setattr(admission._Admission, 'try_send_allowed',
            lambda self: original(self) and len(socket.sent) != 1)
    diagnostic = _capture_retry_boundaries(monkeypatch, socket)
    value = request(rig)
    with pytest.raises(Exception):
        infer(rig, value)
    observed = diagnostic(rig.conn, (value['idempotencyKey'],), socket)
    transport = next(row for row in observed['boundaries'] if row['stage'] == 'transport')
    assert transport['terminal'] == 'unknown' and transport['dispatch'] == 'possible_send'
    assert transport['error'] == ('stale_binding' if fault == 'body_guard' else 'incomplete_stream')
    assert observed['socket']['writePhase'] == ('headers_only' if fault == 'body_guard' else 'headers_and_body')
    assert observed['socket']['receivePhase'] == ('none' if fault == 'body_guard' else 'some')
    assert observed['socket']['remainingPhase'] == ('some' if fault == 'body_guard' else 'none')
    assert observed['socket']['writes'] == (1 if fault == 'body_guard' else 2)
    assert observed['socket']['readCalls'] == (0 if fault == 'body_guard' else 2)
    assert {'stage': 'connection', 'result': 'exact_socket'} in observed['boundaries']
    assert observed['attempts'] == [{'phase': 'uncertain', 'dispatched': 'yes', 'counts': [1, 1, 0], 'terminal': 'absent'}]
    before = list(rig.conn.iterdump())
    with pytest.raises(Exception):
        infer(rig, value)
    assert len(connects) == 1 and list(rig.conn.iterdump()) == before
    encoded = json.dumps(observed, sort_keys=True)
    assert 'synthetic-access' not in encoded and 'Ready' not in encoded and str(rig.home) not in encoded
    assert '"error": "' + transport['error'] + '"' in encoded and '...' not in encoded
    socket.received = 2 ** 60
    assert _retry_socket_category(socket, 2 ** 60)['receivedBytes'] == sender.MAX_WIRE + 1
    assert _retry_socket_category(socket, 2 ** 60)['readCalls'] == 32


def test_late_old_connection_interrupt_cannot_close_explicit_retry_socket(recovery_ledger, monkeypatch):
    import tui_gateway.server as server
    from eidolon_cli import organization_setup_subscription_transport as sender
    from tests.tui_gateway.test_organization_setup_subscription_flow import call
    rig = recovery_ledger
    monkeypatch.setattr(server, '_eidolon_home', rig.home)
    rig.transport.responses = Queue()
    rig.transport.write = rig.transport.responses.put
    old_socket, old_connects = install(monkeypatch, wire(event(response=response())))
    old_connection = []
    original_connect, dispatch = sender._Connection.connect, admission._dispatch_job
    def connect(connection):
        original_connect(connection)
        old_connection.append(connection)
    monkeypatch.setattr(sender._Connection, 'connect', connect)
    monkeypatch.setattr(admission, '_dispatch_job', lambda *args: False)
    value = request(rig)
    first = call('improveTeamInput', value, rig.transport)
    assert first['error']['data']['recovery']['disposition'] == 'retry_unsent'
    assert len(old_connection) == len(old_connects) == 1 and not old_socket.sent
    old_row = state(rig)[0]
    monkeypatch.setattr(admission, '_dispatch_job', dispatch)
    socket, connects = install(monkeypatch, wire(event(response=response())))
    release, finished = threading.Event(), threading.Event()
    def late_cleanup():
        if release.wait(10):
            sender._interrupt(old_connection[0], threading.Event())
        finished.set()
    worker = threading.Thread(target=late_cleanup)
    worker.start()
    def before_body(_):
        if len(socket.sent) == 1:
            release.set()
            assert finished.wait(10)
            assert not socket.closed
    socket.before_send = before_body
    try:
        result = call('improveTeamInput', {**value, 'idempotencyKey': 'explicit-new-intent'}, rig.transport)
        assert result['result']['suggestion'] == 'Ready'
    finally:
        release.set()
        worker.join(10)
    assert not worker.is_alive() and finished.is_set()
    assert len(connects) == 1 and len(socket.sent) == 2 and socket.received > 0
    assert state(rig)[1] == old_row and state(rig)[0]['status'] == 'completed'


def test_synthetic_recovery_profile_opt_out_finishes_exact_passive_invocation(recovery_ledger, monkeypatch):
    from eidolon_cli import banner
    rig = recovery_ledger
    (rig.home / 'config.yaml').write_text('updates:\n  check: false\n', encoding='utf-8')
    probes, completions = [], []
    original_daemon = banner._daemon
    def tracked_daemon(name, target):
        done = threading.Event()
        completions.append(done)
        def run():
            try:
                target()
            finally:
                done.set()
        original_daemon(name, run)
    monkeypatch.setattr(banner, '_daemon', tracked_daemon)
    monkeypatch.setattr(banner, '_check_via_rev', lambda *args: probes.append(True))
    monkeypatch.setattr(banner, '_check_via_local_git', lambda *args: probes.append(True))
    banner.prefetch_update_check()
    assert len(completions) == 1 and completions[0].wait(10)
    assert probes == [] and not (rig.home / '.update_check').exists()


def test_retry_diagnostics_export_only_closed_error_categories():
    private = 'synthetic-private-error-text'
    class UnknownError(RuntimeError):
        @property
        def reason(self):
            pytest.fail('Unknown error attributes must not be inspected')
        def __str__(self):
            pytest.fail('Error text must not be formatted')
    assert _retry_error_category(UnknownError(private)) == {'class': 'other', 'code': 'other'}
    error = StoreError('operation_failed')
    error.reason = private
    assert _retry_error_category(error) == {'class': 'StoreError', 'code': 'other'}
    error = sqlite3.OperationalError(private)
    error.sqlite_errorcode = sqlite3.SQLITE_BUSY | (1 << 8)
    assert _retry_error_category(error) == {'class': 'OperationalError', 'code': 'SQLITE_BUSY'}
    accesses = []
    class HostileDict(dict):
        def get(self, *args):
            accesses.append(True)
            raise RuntimeError(private)
    error.__dict__ = HostileDict(sqlite_errorcode=sqlite3.SQLITE_BUSY)
    assert _retry_error_category(error) == {'class': 'OperationalError', 'code': 'other'}
    error = StoreError('operation_failed')
    error.__dict__ = HostileDict(reason='operation_failed')
    assert _retry_error_category(error) == {'class': 'StoreError', 'code': 'other'}
    from tests.eidolon_cli.test_organization_setup_subscription_transport import Socket
    assert _retry_socket_category(object(), 0) == {'phase': 'unavailable'}
    socket = Socket(b'')
    socket.__dict__ = HostileDict(sent=[], received=0, data=bytearray())
    assert _retry_socket_category(socket, 0) == {'phase': 'unavailable'}
    assert accesses == []


def test_retry_diagnostic_failure_preserves_original_return_and_exception(monkeypatch):
    from eidolon_cli import organization_setup_subscription_transport as sender
    from eidolon_cli.organization_setup_subscription_usage import TransportResult
    broken = object.__new__(TransportResult)  # Deliberately invalid synthetic return, no fields.
    monkeypatch.setattr(sender, 'send_once', lambda *args: broken)
    error = RuntimeError('synthetic-private-error-text')
    def fail(*args):
        raise error
    monkeypatch.setattr(admission, '_dispatch_job', fail)
    monkeypatch.setattr(__import__(__name__, fromlist=['_retry_error_category']), '_retry_error_category',
        lambda *args: (_ for _ in ()).throw(RuntimeError('synthetic observer failure')))
    diagnostic = _capture_retry_boundaries(monkeypatch)
    assert sender.send_once(None) is broken
    with pytest.raises(RuntimeError) as caught:
        admission._dispatch_job()
    assert caught.value is error
    observed = diagnostic(None, (), SimpleNamespace(sent=[]))
    assert observed['boundaries'] == [
        {'stage': 'transport', 'result': 'diagnostic_error'},
        {'stage': 'dispatch', 'result': 'diagnostic_error'}]


def test_retry_diagnostics_observe_failed_dispatch_without_replay(recovery_ledger, monkeypatch):
    rig = recovery_ledger
    private = 'synthetic-private-error-text'
    calls = []
    def fail(*args):
        calls.append(True)
        error = sqlite3.OperationalError(private)
        error.sqlite_errorcode = sqlite3.SQLITE_BUSY
        raise error
    monkeypatch.setattr(admission, '_dispatch_job', fail)
    socket, connects = install(monkeypatch, wire(event(response=response())))
    diagnostic = _capture_retry_boundaries(monkeypatch)
    value = request(rig, key='synthetic-private-original-id')
    with pytest.raises(Exception):
        infer(rig, value)
    observed = diagnostic(rig.conn, (value['idempotencyKey'], 'missing'), socket)
    assert observed['postWriteObserved'] is False and not socket.sent
    assert len(calls) == len(connects) == 1
    assert observed['attempts'] == [
        {'phase': 'uncertain', 'dispatched': 'no', 'counts': [1, 0, 0], 'terminal': 'absent'},
        {'phase': 'absent', 'dispatched': 'absent', 'counts': [0, 0, 0], 'terminal': 'absent'}]
    assert {'stage': 'dispatch', 'result': 'exception',
            'error': {'class': 'OperationalError', 'code': 'SQLITE_BUSY'}} in observed['boundaries']
    encoded = json.dumps(observed)
    assert private not in encoded and str(rig.home) not in encoded
    assert value['idempotencyKey'] not in encoded and rig.identity['organizationId'] not in encoded


@pytest.mark.parametrize('hostile', ['changed_input', 'changed_source', 'unknown_operation', 'uncertain', 'running', 'missing_attempt', 'owner', 'proof_exception'])
def test_retry_offer_is_absent_when_exact_proof_is_unavailable(recovery_ledger, monkeypatch, hostile):
    rig = recovery_ledger
    install(monkeypatch, wire(event(response=response())))
    monkeypatch.setattr(admission, '_dispatch_job', lambda *args: False)
    value = request(rig)
    with pytest.raises(Exception):
        infer(rig, value)
    operation, transport = 'improveTeamInput', rig.transport
    if hostile == 'changed_input':
        value = {**value, 'jobs': 'Different input'}
    elif hostile == 'changed_source':
        value = {**value, 'sourceRevision': 'b' * 64}
    elif hostile == 'unknown_operation':
        operation = 'invalid'
    elif hostile in {'uncertain', 'running'}:
        rig.conn.execute('UPDATE organization_team_generations SET status=?', (hostile,))
    elif hostile == 'missing_attempt':
        rig.conn.execute('DROP TRIGGER organization_setup_subscription_attempts_immutable_delete')
        rig.conn.execute('DELETE FROM organization_setup_subscription_attempts')
    elif hostile == 'owner':
        transport = SimpleNamespace(closed=False)
    else:
        from tui_gateway import methods_organization_setup_subscription as gateway
        monkeypatch.setattr(gateway, 'subscription_preflight', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('private')))
    before = list(rig.conn.iterdump())
    assert admission.retry_unsent_disposition(rig.home, value, operation, transport=transport) is None
    assert list(rig.conn.iterdump()) == before


@pytest.mark.parametrize('where', ['initialization', 'candidate', 'committed'])
def test_registered_recover_status_and_new_begin_use_local_only_contract(recovery_rig, monkeypatch, where):
    import tui_gateway.server as server
    from tui_gateway import methods_organization_setup_subscription as gateway
    from tests.tui_gateway.test_organization_setup_subscription_flow import call, Transport
    rig = recovery_rig
    fail_publication(rig, monkeypatch, where)
    service = rig.restart()
    transport = Transport()
    context = service._owner
    monkeypatch.setattr(gateway, '_context', lambda actual, params, authority, **kw: context)
    monkeypatch.setattr(gateway, '_service', lambda *args: service)
    monkeypatch.setattr(gateway, '_catalog', lambda *args: SimpleNamespace(invalidate=lambda: None))
    monkeypatch.setattr(gateway, '_load_catalog', lambda *args: pytest.fail('Recovery must not fetch models'))
    value = {'expectedOrganizationId': ORG, 'sourceRevision': context.source_digest}
    assert call('recoverSetupSubscriptionOperation', {**value, 'confirmed': False}, transport)['error']['code'] == 5077
    result = call('recoverSetupSubscriptionOperation', {**value, 'confirmed': True}, transport)['result']
    assert result['catalogState'] == 'not_requested' and result['catalogRevision'] is None
    status = call('setupSubscriptionStatus', value, transport)['result']
    if where != 'committed':
        assert status['stage'] == 'locally_retired' and status['connectionState'] == 'disconnected'
        begun = call('beginSetupSubscription', {**value, 'confirmed': True, 'consentVersion': 1,
            'expectedRevision': status['connectionRevision']}, transport)['result']
        assert begun['stage'] == 'pending'
        service.cancel(begun['attemptId'])
    else:
        assert status['stage'] == 'completed' and status['connectionState'] == 'active'
    assert len(rig.exchanges) == 1 and len(rig.puts) == int(where != 'initialization')


def test_lost_host_publication_acknowledgment_proves_exact_grant(recovery_rig, monkeypatch):
    rig = recovery_rig
    fail_publication(rig, monkeypatch, 'committed')
    save = rig.host._save
    fired = []
    def fail(record):
        save(record)
        if record.stage == 'published' and not fired:
            fired.append(True)
            raise RuntimeError('synthetic lost acknowledgment')
    monkeypatch.setattr(rig.host, '_save', fail)
    with pytest.raises(auth.AuthError):
        rig.service.recover_operation_current()
    assert rig.host.inspect().stage == 'published'
    assert rig.service.recover_operation_current()['revision'] == 1
    assert rig.service.status()['stage'] == 'connected'
    assert len(rig.exchanges) == len(rig.puts) == 1


@pytest.mark.parametrize('turnover', ['owner', 'cancel', 'attempt', 'same_id_attempt'])
def test_guard_turnover_after_store_proof_never_terminalizes_host(recovery_rig, monkeypatch, turnover):
    rig = recovery_rig
    fail_publication(rig, monkeypatch, 'candidate')
    service = rig.service
    original = rig.store.reconcile_auth_publication
    attempt = service._attempt
    def proof(*args, **kwargs):
        result = original(*args, **kwargs)
        if turnover == 'owner':
            rig.valid[0] = False
        elif turnover == 'cancel':
            attempt.cancel.set()
        else:
            service._attempt = SimpleNamespace(
                attempt_id=attempt.attempt_id if turnover == 'same_id_attempt' else 'different', cancel=attempt.cancel)
        return result
    monkeypatch.setattr(rig.store, 'reconcile_auth_publication', proof)
    with pytest.raises(auth.AuthError):
        service.recover_operation_current()
    assert rig.host.inspect().stage == 'publishing'
    assert rig.store.status()['state'] == 'disconnected'
    assert len(rig.exchanges) == len(rig.puts) == 1


def test_source_turnover_after_local_proof_never_reports_stale_success(recovery_rig, monkeypatch):
    rig = recovery_rig
    fail_publication(rig, monkeypatch, 'candidate')
    original = rig.store.reconcile_auth_publication
    def proof(*args, **kwargs):
        result = original(*args, **kwargs)
        rig.service._setup_guard = lambda: False
        return result
    monkeypatch.setattr(rig.store, 'reconcile_auth_publication', proof)
    with pytest.raises(auth.AuthError):
        rig.service.recover_operation_current()
    # Preserve proven local retirement; do not represent it as stale setup success.
    assert rig.host.inspect().stage == 'locally_retired'
    assert rig.store.status()['state'] == 'disconnected'
    assert len(rig.exchanges) == len(rig.puts) == 1


def test_unrelated_active_consent_never_recovers_or_changes_history(recovery_rig, monkeypatch):
    rig = recovery_rig
    fail_publication(rig, monkeypatch, 'committed')
    with rig.host._locked():
        record = rig.host._read_attempt()
        rig.host._save(replace(record, attempt_id='00000000-0000-4000-8000-000000000099'))
    before = {str(p.relative_to(rig.store.directory)): p.read_bytes()
        for p in rig.store.directory.rglob('*') if p.is_file()}
    with pytest.raises(auth.AuthError):
        rig.restart().recover_operation_current()
    after = {str(p.relative_to(rig.store.directory)): p.read_bytes()
        for p in rig.store.directory.rglob('*') if p.is_file()}
    assert before == after and rig.host.inspect().stage == 'publishing'
    assert len(rig.exchanges) == len(rig.puts) == 1


@pytest.mark.parametrize('terminal', ['failed', 'uncertain', 'completed'])
def test_dispatched_attempt_never_offers_unsent_retry(recovery_ledger, monkeypatch, terminal):
    rig = recovery_ledger
    body = wire(event(response=response())) if terminal == 'completed' else b''
    install(monkeypatch, body)
    value = request(rig)
    if terminal == 'completed':
        infer(rig, value)
    else:
        with pytest.raises(Exception):
            infer(rig, value)
        if terminal == 'failed':
            rig.conn.execute("UPDATE organization_team_generations SET status='failed'")
    assert state(rig)[0]['dispatched'] == 1
    before = list(rig.conn.iterdump())
    assert admission.retry_unsent_disposition(rig.home, value, 'improveTeamInput', transport=rig.transport) is None
    assert list(rig.conn.iterdump()) == before


@pytest.mark.parametrize('kind', ['refresh', 'revoke'])
def test_historical_published_auth_does_not_block_old_source_lifecycle_recovery(recovery_rig, monkeypatch, kind):
    rig = recovery_rig
    fail_publication(rig, monkeypatch, 'committed')
    assert rig.service.recover_operation_current()['revision'] == 1
    ticket = rig.service.prepare_operation(kind, 1)['ticket']
    if kind == 'revoke':
        rig.service._http = lambda *args: (_ for _ in ()).throw(RuntimeError('synthetic network uncertainty'))
        result = rig.service.execute_operation(ticket)
        assert result['state'] == 'admitted'  # Unknown remote outcome never permits replay.
        assert result['receipt']['remoteRevocation'] == 'unconfirmed'
    old = rig.store.ticket_status()
    owner = replace(rig.service._owner, source_digest='b' * 64, setup_guard=lambda: False)
    restarted = auth.SubscriptionAuthService(rig.store, rig.host, owner, synthetic=True,
        http=lambda *args: pytest.fail('Local recovery must not send'))
    result = restarted.recover_operation_current()
    assert result['ticket'] == ticket and result['state'] == old['state']
    assert result['catalogRevision'] is None
    assert rig.store.ticket_status() == old
    assert rig.host.inspect().stage == 'published'
    if kind == 'revoke':
        assert result['receipt']['remoteRevocation'] == 'unconfirmed'


def test_registered_optional_proof_failure_preserves_original_error(recovery_ledger, monkeypatch):
    import tui_gateway.server as server
    from tests.tui_gateway.test_organization_setup_subscription_flow import call
    rig = recovery_ledger
    monkeypatch.setattr(server, '_eidolon_home', rig.home)
    rig.transport.responses = Queue()
    rig.transport.write = rig.transport.responses.put
    install(monkeypatch, wire(event(response=response())))
    monkeypatch.setattr(admission, '_dispatch_job', lambda *args: False)
    monkeypatch.setattr(admission, 'retry_unsent_disposition',
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('synthetic-private-proof')))
    assert call('improveTeamInput', request(rig), rig.transport)['error'] == {
        'code': 5076, 'message': 'Team setup is unavailable', 'data': {'reason': 'unavailable'}}


def test_host_record_accepts_exact_legacy_shape_and_rejects_ambiguous_recovery_fields(recovery_rig, monkeypatch):
    from dataclasses import asdict
    from eidolon_cli.organization_setup_subscription_host import HostError
    rig = recovery_rig
    rig.service.begin(0)
    record = rig.host.inspect()
    value = asdict(record)
    for field in ('publication_binding_digest', 'authorization_host_id',
                  'host_identity_migration', 'authorization_binding_digest'):
        value.pop(field)
    record = replace(record, authorization_host_id=None, host_identity_migration=False,
                     authorization_binding_digest=None)
    rig.host._io._write(rig.host._name, json.dumps(value).encode(), replace=True)
    assert rig.host.inspect() == record
    for changes in ({'publication_binding_digest': 'a' * 64}, {'unexpected': 'field'},
                    {'stage': 'locally_retired'}, {'stage': 'publishing', 'publication_binding_digest': 'invalid'}):
        rig.host._io._write(rig.host._name, json.dumps({**value, **changes}).encode(), replace=True)
        with pytest.raises(HostError):
            rig.host.inspect()
    rig.host._io._write(rig.host._name, json.dumps(value).encode(), replace=True)
    rig.service.cancel(record.attempt_id)


@pytest.mark.parametrize('kind', ['refresh', 'revoke'])
def test_durable_published_lost_ack_cannot_divert_later_lifecycle_recovery(recovery_rig, monkeypatch, kind):
    rig = recovery_rig
    fail_publication(rig, monkeypatch, 'committed')
    save = rig.host._save
    def lose_ack(record):
        save(record)
        if record.stage == 'published':
            raise RuntimeError('synthetic lost acknowledgment')
    monkeypatch.setattr(rig.host, '_save', lose_ack)
    with pytest.raises(auth.AuthError):
        rig.service.recover_operation_current()
    assert rig.host.inspect().stage == 'published'
    assert rig.service._attempt.stage == 'publication_uncertain'
    binding = rig.store.returning_binding()
    ticket = rig.store.prepare_ticket(binding, 1, kind=kind, source_revision='a' * 64,
        owner_guard=rig.service._lease_guard, cancel=rig.service._lease_cancel)
    rig.service._owner = replace(rig.service._owner, source_digest='b' * 64)
    rig.service._setup_guard = lambda: False
    result = rig.service.recover_operation_current()
    assert result['state'] == 'prepared' and result['ticket'] == ticket
    assert result['catalogRevision'] is None
    assert rig.service._attempt.stage == 'connected'
    assert len(rig.exchanges) == len(rig.puts) == 1


def retirement_archive(rig, record):
    return rig.host._io.directory / 'ticket-archive' / (rig.host._name + '.' + record.attempt_id + '.retired')


def test_next_consent_preserves_exact_remote_unknown_retirement(recovery_rig, monkeypatch):
    from dataclasses import asdict
    rig = recovery_rig
    fail_publication(rig, monkeypatch, 'candidate')
    rig.service.recover_operation_current()
    retired = rig.host.inspect()
    rig.service.begin(0)
    current = rig.host.inspect()
    assert current.attempt_id != retired.attempt_id and current.stage == 'pending'
    archived = retirement_archive(rig, retired)
    assert json.loads(archived.read_bytes()) == asdict(retired)
    assert json.loads(archived.read_bytes())['stage'] == 'locally_retired'
    before = archived.read_bytes()
    rig.service.cancel(current.attempt_id)
    rig.service.begin(0)
    assert archived.read_bytes() == before
    assert len(rig.exchanges) == len(rig.puts) == 1
    rig.service.cancel(rig.service._attempt.attempt_id)


@pytest.mark.parametrize('failure', ['lost_ack', 'collision'])
def test_retirement_archive_failure_never_overwrites_or_starts_new_consent(recovery_rig, monkeypatch, failure):
    rig = recovery_rig
    fail_publication(rig, monkeypatch, 'candidate')
    rig.service.recover_operation_current()
    retired = rig.host.inspect()
    path = retirement_archive(rig, retired)
    if failure == 'collision':
        rig.host._io._archive_directory(create=True)
        path.write_bytes(b'conflicting evidence')
        path.chmod(0o600)
    else:
        original = rig.host._io._archive_sync
        failed = []
        def lose_ack():
            original()
            if not failed:
                failed.append(True)
                raise RuntimeError('synthetic lost archive acknowledgment')
        monkeypatch.setattr(rig.host._io, '_archive_sync', lose_ack)
    with pytest.raises(auth.AuthError):
        rig.service.begin(0)
    assert rig.host.inspect() == retired
    assert len(rig.opened) == len(rig.exchanges) == len(rig.puts) == 1
    archive_bytes = path.read_bytes()
    if failure == 'lost_ack':
        syncs = []
        original_sync = rig.host._io._archive_sync
        def synced():
            original_sync()
            syncs.append(True)
        monkeypatch.setattr(rig.host._io, '_archive_sync', synced)
        rig.service.begin(0)
        assert syncs and rig.host.inspect().attempt_id != retired.attempt_id
        assert path.read_bytes() == archive_bytes
        rig.service.cancel(rig.service._attempt.attempt_id)
    else:
        with pytest.raises(auth.AuthError):
            rig.service.begin(0)
        assert path.read_bytes() == archive_bytes == b'conflicting evidence'
        assert rig.host.inspect() == retired
    assert len(rig.exchanges) == len(rig.puts) == 1
