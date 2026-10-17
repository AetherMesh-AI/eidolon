"""Explicit recovery against real private store/ledger; synthetic network and vault only."""
from contextlib import nullcontext
from dataclasses import replace
import json
import os
from queue import Queue
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
def recovery_ledger(ledger, monkeypatch, request):
    # Bypass only OS admission in this explicitly synthetic filesystem proof.
    # Production platform eligibility is unchanged and separately host-tested.
    from tui_gateway.organization_credential_transport import owner_generation
    def synthetic_owner(home, transport):
        generation = owner_generation(transport)
        policy.require_local_owner(transport, home, generation)
        return generation
    monkeypatch.setattr(policy, '_runtime_owner', synthetic_owner)
    return request.getfixturevalue('activated')


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
    assert 'result' in call(method, {**value, 'idempotencyKey': 'explicit-new-intent'}, rig.transport)
    assert state(rig)[1] == old  # Keys sort explicit-new-intent before inference.
    assert len(new_connects) == 1 and socket.sent
    assert budget.operation_counts(rig.conn)['total'] == 1


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
    value.pop('publication_binding_digest')
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
