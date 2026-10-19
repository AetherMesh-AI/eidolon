"""Synthetic owner/adapter proof. No browser, native vault or network operations."""
from types import SimpleNamespace
import sys
import threading
import uuid

import pytest

from tui_gateway import methods_organization_setup_subscription as methods
from tui_gateway import organization_credential_transport as credentials


class Transport:
    closed = False


def params(**extra):
    return {'expectedOrganizationId': str(uuid.UUID(int=1)), 'sourceRevision': 'revision', **extra}


@pytest.mark.parametrize('extra', [
    {'home': '/other'}, {'ownerId': 'invented'}, {'accessToken': 'synthetic-secret'},
    {'returningBinding': {}}, {'endpoint': 'https://example.invalid'},
])
def test_no_client_authority_or_secret_fields(extra):
    with pytest.raises(methods.BoundaryError):
        methods._parameters('status', params(**extra))


@pytest.mark.parametrize('change', [
    {'confirmed': False}, {'confirmed': 1}, {'expectedRevision': True},
    {'expectedRevision': -1}, {'consentVersion': True}, {'consentVersion': 2},
])
def test_begin_requires_exact_consent_and_revision(change):
    value = params(expectedRevision=0, confirmed=True, consentVersion=1)
    value.update(change)
    with pytest.raises(methods.BoundaryError):
        methods._parameters('begin', value)


def install_cancel_spies(monkeypatch):
    calls = []
    secure = SimpleNamespace(cancel_transport=lambda transport: calls.append('generic'))
    monkeypatch.setitem(sys.modules, 'eidolon_cli.organization_credential_service',
                        SimpleNamespace(secure_inputs=secure))

    def cancel(transport, *, generation):
        assert credentials._lock.acquire(blocking=False), 'registry lock held during retirement'
        credentials._lock.release()
        publication = credentials._publication_lock(transport)
        assert publication.acquire(blocking=False), 'publication lock held during retirement'
        publication.release()
        calls.append(('subscription', generation))

    monkeypatch.setitem(sys.modules, 'eidolon_cli.organization_setup_subscription_auth',
                        SimpleNamespace(cancel_transport=cancel))
    return calls


def test_register_and_retire_cancel_outside_registry_lock(tmp_path, monkeypatch):
    calls = install_cancel_spies(monkeypatch)
    transport = Transport()
    credentials.register_owner(transport, tmp_path)
    first = credentials.owner_generation(transport)
    credentials.register_owner(transport, tmp_path)
    assert credentials.owner_generation(transport) is not first
    credentials.retire_owner(transport)
    assert credentials.owner_home(transport) is None
    assert calls[0:2] == ['generic', 'generic']
    assert calls[2] == ('subscription', first)
    assert calls[3] == 'generic'
    assert calls[4][0] == 'subscription'
    assert calls[4][1] is not first


def test_context_rejects_unregistered_and_foreign_source(tmp_path, monkeypatch):
    from eidolon_cli.organization_setup_subscription_auth import OwnerContext
    transport = Transport()
    monkeypatch.setattr(methods, '_durable_scope', lambda *args: ('org', 'owner', 'revision'))
    monkeypatch.setattr(credentials, 'owner_home', lambda value: None)
    with pytest.raises(methods.BoundaryError):
        methods._context(transport, params(), object())
    monkeypatch.setattr(credentials, 'owner_home', lambda value: tmp_path)
    generation = object()
    monkeypatch.setattr(credentials, 'owner_generation', lambda value: generation)
    with pytest.raises(methods.BoundaryError):
        methods._context(transport, params(sourceRevision='stale'), object())
    context = methods._context(transport, params(), object())
    assert isinstance(context, OwnerContext)
    assert context.owner_guard() is True
    monkeypatch.setattr(methods, '_durable_scope', lambda *args: ('org', 'other-owner', 'revision'))
    assert context.owner_guard() is False


def test_context_fences_generation_and_durable_source(tmp_path, monkeypatch):
    transport = Transport()
    generation = object()
    monkeypatch.setattr(credentials, 'owner_home', lambda value: tmp_path)
    monkeypatch.setattr(credentials, 'owner_generation', lambda value: generation)
    monkeypatch.setattr(methods, '_durable_scope', lambda *args: ('org', 'owner', 'revision'))
    context = methods._context(transport, params(), object())
    assert context.owner_guard() is True
    generation = object()
    assert context.owner_guard() is False


def test_dispatch_does_not_accept_caller_binding(monkeypatch):
    monkeypatch.setattr(methods, '_context', lambda *args, **kwargs: pytest.fail('authority must not be reached'))
    with pytest.raises(methods.BoundaryError):
        methods.dispatch('begin', params(expectedRevision=0, confirmed=True, consentVersion=1,
                                        binding={'account': 'foreign'}), Transport(), object())


def test_dispatch_calls_only_requested_service_method(monkeypatch):
    calls = []
    service = SimpleNamespace(status=lambda: {'stage': 'absent'},
        begin=lambda revision: calls.append(('begin', revision)) or {'stage': 'pending'},
        cancel=lambda attempt: calls.append(('cancel', attempt)),
        disconnect_current=lambda revision: calls.append(('disconnect', revision)))
    monkeypatch.setattr(methods, '_context', lambda *args, **kwargs: object())
    monkeypatch.setattr(methods, '_service', lambda *args: service)
    assert methods.dispatch('status', params(), Transport(), object()) == {'stage': 'absent'}
    assert calls == []
    methods.dispatch('begin', params(expectedRevision=0, confirmed=True, consentVersion=1), Transport(), object())
    assert calls == [('begin', 0)]


def test_registered_rpc_uses_adapter_dispatch_and_rejects_client_authority(monkeypatch):
    from types import ModuleType
    from tui_gateway.transport import bind_transport, reset_transport, current_transport
    fake_server = ModuleType('synthetic_server')
    fake_server._methods = {}
    fake_server.current_transport = current_transport
    fake_server._subscription_installation_authority = lambda: object()
    fake_server._ok = lambda rid, value: {'id': rid, 'result': value}
    fake_server._err = lambda rid, code, message, data=None: {'id': rid, 'error': {'code': code}}
    # The server already has its own dispatch. Registration must call the
    # adapter explicitly instead of resolving this unrelated facade symbol.
    fake_server.dispatch = lambda *args: pytest.fail('called generic RPC dispatch recursively')
    methods.register(fake_server)
    monkeypatch.setattr(methods, '_context', lambda *args, **kwargs: object())
    migrations = []
    def begin(revision, *, confirm_host_identity_change=False):
        migrations.append((revision, confirm_host_identity_change))
        return {'stage': 'pending'}
    monkeypatch.setattr(methods, '_service', lambda *args: SimpleNamespace(
        status=lambda: {'stage': 'idle'}, begin=begin))
    token = bind_transport(Transport())
    try:
        handler = fake_server._methods['organization.setupSubscriptionStatus']
        assert handler(1, params())['result'] == {'stage': 'idle'}
        assert handler(2, params(accessToken='forbidden'))['error']['code'] == 5077
        migrate = fake_server._methods['organization.beginSetupSubscriptionHostMigration']
        assert migrate(3, params(expectedRevision=7, confirmed=True, consentVersion=2))['result'] == {
            'stage': 'pending'}
        assert migrations == [(7, True)]
        assert migrate(4, params(expectedRevision=7, confirmed=True, consentVersion=1))['error']['code'] == 5077
        assert migrations == [(7, True)]
    finally:
        reset_transport(token)


def test_stable_authority_lock_and_reentrant_lifecycle_refusal(tmp_path, monkeypatch):
    install_cancel_spies(monkeypatch)
    transport = Transport()
    credentials.register_owner(transport, tmp_path)
    lock = credentials._publication_lock(transport)
    generation = credentials.owner_generation(transport)
    with credentials.owner_publication(transport, tmp_path, generation):
        with pytest.raises(RuntimeError):
            credentials.retire_owner(transport)
        with pytest.raises(RuntimeError):
            credentials.register_owner(transport, tmp_path)
        with pytest.raises(RuntimeError):
            with credentials.owner_publication(transport, tmp_path, generation):
                pytest.fail('recursive publication admitted')
        assert credentials.owner_generation(transport) is generation
    credentials.retire_owner(transport)
    credentials.register_owner(transport, tmp_path)
    assert credentials._publication_lock(transport) is lock


def test_publish_first_serializes_retirement_and_independent_transport(tmp_path, monkeypatch):
    install_cancel_spies(monkeypatch)
    transport, other = Transport(), Transport()
    credentials.register_owner(transport, tmp_path)
    credentials.register_owner(other, tmp_path)
    generation = credentials.owner_generation(transport)
    entered, release, retire_started, retired = (threading.Event() for _ in range(4))
    writes, errors = [], []

    def publish():
        try:
            with credentials.owner_publication(transport, tmp_path, generation):
                entered.set()
                assert release.wait(3)
                writes.append('committed')
        except BaseException as error:
            errors.append(error)

    def retire():
        try:
            retire_started.set()
            credentials.retire_owner(transport)
            retired.set()
        except BaseException as error:
            errors.append(error)

    first = threading.Thread(target=publish)
    second = threading.Thread(target=retire)
    first.start()
    try:
        assert entered.wait(3)
        second.start()
        assert retire_started.wait(3)
        assert not retired.is_set()
        # Independent transport has distinct authority and cannot be blocked.
        with credentials.owner_publication(other, tmp_path, credentials.owner_generation(other)):
            assert writes == []
        release.set()
    finally:
        release.set()
        first.join(3)
        if second.ident is not None:
            second.join(3)
    assert not first.is_alive() and not second.is_alive()
    assert not errors
    assert writes == ['committed'] and retired.is_set()


def test_retire_first_prevents_every_local_write(tmp_path, monkeypatch):
    install_cancel_spies(monkeypatch)
    transport = Transport()
    credentials.register_owner(transport, tmp_path)
    generation = credentials.owner_generation(transport)
    credentials.retire_owner(transport)
    writes = []
    with pytest.raises(RuntimeError):
        with credentials.owner_publication(transport, tmp_path, generation):
            writes.append('forbidden')
    assert writes == []


def test_notification_targets_exact_retired_generation(tmp_path, monkeypatch):
    install_cancel_spies(monkeypatch)
    transport = Transport()
    credentials.register_owner(transport, tmp_path)
    first = credentials.owner_generation(transport)
    notifications = []
    monkeypatch.setattr(credentials, '_retire_setup_subscription',
                        lambda transport, generation: notifications.append(generation))
    credentials.register_owner(transport, tmp_path)
    second = credentials.owner_generation(transport)
    assert notifications == [first] and second is not first
    credentials.retire_owner(transport)
    assert notifications == [first, second]


def test_retired_cache_cleanup_cannot_drop_new_generation():
    transport = Transport()
    first, second = object(), object()
    service = object()
    methods._services[transport] = (('home', 'org', 'owner', 'source', second), service)
    methods.retire_service(transport, first)
    assert methods._services[transport][1] is service
    methods.retire_service(transport, second)
    assert transport not in methods._services


def test_concurrent_source_turnover_retires_only_captured_service(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from eidolon_cli import organization_setup_subscription_auth as auth
    from eidolon_cli.organization_setup_subscription_host import HostAuthority
    from eidolon_cli.organization_setup_subscription_store import TokenLease

    transport, generation = Transport(), object()
    authority = HostAuthority.capture(lambda: tmp_path, lambda: tmp_path)
    source = 'a' * 64

    def context(digest):
        return auth.OwnerContext(str(tmp_path), str(uuid.UUID(int=1)), str(uuid.UUID(int=2)),
            digest, generation, transport, lambda: source == digest,
            publication_authority=nullcontext, setup_guard=lambda: True,
            lease_generation_guard=lambda: True)

    def track_lease(service):
        # Exercise real lease cancellation with an inert synthetic value; no
        # store admission, credential lookup, browser or provider request occurs.
        return service._track_lease(TokenLease(service._store, None, 1, generation,
            service._lease_guard, service._lease_cancel, 2**53,
            'synthetic-test-access', str(tmp_path)))

    previous = methods._service(transport, context(source), authority)
    old_lease = track_lease(previous)
    source = 'b' * 64
    replacement_context = context(source)
    captured = threading.Barrier(2)
    published = threading.Event()
    first_read = threading.local()
    registry_lock = methods._services_lock
    results, failures, leases = {}, {}, {}

    class InterleavingLock:
        def __enter__(self):
            registry_lock.acquire()

        def __exit__(self, *exc):
            registry_lock.release()
            thread = threading.current_thread()
            if thread not in (winner, delayed) or getattr(first_read, 'complete', False):
                return
            first_read.complete = True
            # Both callers capture the same old cache before either retires it.
            captured.wait(3)
            if thread is delayed:
                assert published.wait(3)

    def replace(label):
        try:
            service = methods._service(transport, replacement_context, authority)
            results[label] = service
            leases[label] = track_lease(service)
        except BaseException as error:
            failures[label] = error
        finally:
            if label == 'winner':
                published.set()

    winner = threading.Thread(target=replace, args=('winner',))
    delayed = threading.Thread(target=replace, args=('delayed',))
    monkeypatch.setattr(methods, '_services_lock', InterleavingLock())
    winner.start()
    delayed.start()
    try:
        winner.join(5)
        delayed.join(5)
    finally:
        published.set()
        captured.abort()
        winner.join(5)
        delayed.join(5)
    assert not winner.is_alive() and not delayed.is_alive()
    assert set(results) == {'winner'}
    assert set(failures) == {'delayed'}
    assert isinstance(failures['delayed'], methods.BoundaryError)
    assert previous._retired and old_lease.network_cancelled()
    assert not previous._leases and not old_lease._token
    replacement = results['winner']
    assert methods._services[transport][1] is replacement
    assert methods._service(transport, replacement_context, authority) is replacement
    assert not replacement._retired and replacement._lease_guard()
    replacement._guard()
    assert not leases['winner'].network_cancelled()
    assert leases['winner']._token

    # Actual owner-generation retirement still cancels the replacement and lease.
    auth.cancel_transport(transport, generation=generation)
    assert replacement._retired and leases['winner'].network_cancelled()
    assert not replacement._leases and not leases['winner']._token


@pytest.mark.parametrize('operation,extra,requires_fresh', [
    ('status', {}, False), ('cancel', {'attemptId': 'attempt'}, False),
    ('prepareOperation', {'kind': 'revoke', 'expectedRevision': 1, 'confirmed': True}, False),
    ('begin', {'expectedRevision': 0, 'confirmed': True, 'consentVersion': 1}, True),
    ('beginMigration', {'expectedRevision': 0, 'confirmed': True, 'consentVersion': 2}, True),
])
def test_authentication_requires_fresh_but_local_lifecycle_does_not(monkeypatch, operation, extra, requires_fresh):
    seen = []
    def context(*args, require_fresh):
        seen.append(require_fresh)
        if require_fresh:
            raise methods.BoundaryError()
        return object()
    service = SimpleNamespace(status=lambda: {}, cancel=lambda attempt: {},
                              prepare_operation=lambda kind, revision: {})
    monkeypatch.setattr(methods, '_context', context)
    monkeypatch.setattr(methods, '_service', lambda *args: service)
    if requires_fresh:
        with pytest.raises(methods.BoundaryError):
            methods.dispatch(operation, params(**extra), Transport(), object())
    else:
        assert methods.dispatch(operation, params(**extra), Transport(), object()) == {}
    assert seen == [requires_fresh]


def test_nonfresh_org_still_has_exact_owner_lifecycle_scope(monkeypatch, tmp_path):
    from contextlib import nullcontext
    from eidolon_cli import organization_owner_policy, organization_team_setup
    monkeypatch.setattr(organization_owner_policy, '_connection', lambda home: nullcontext(object()))
    monkeypatch.setattr(organization_team_setup, '_current', lambda *args: (
        {'organizationId': 'org', 'owner': {'ownerId': 'owner'}}, {}, object(), 'current-source'))
    monkeypatch.setattr(organization_team_setup, '_fresh', lambda *args: False)
    assert methods._durable_scope(tmp_path, 'org') == ('org', 'owner', 'current-source')
    with pytest.raises(methods.BoundaryError):
        methods._durable_scope(tmp_path, 'org', require_fresh=True)


def test_status_context_preserves_separate_future_begin_eligibility(tmp_path, monkeypatch):
    transport = Transport()
    generation = object()
    fresh = True
    monkeypatch.setattr(credentials, 'owner_home', lambda value: tmp_path)
    monkeypatch.setattr(credentials, 'owner_generation', lambda value: generation)
    def durable(home, org, require_fresh=False):
        if require_fresh and not fresh:
            raise methods.BoundaryError()
        return ('org', 'owner', 'revision')
    monkeypatch.setattr(methods, '_durable_scope', durable)
    context = methods._context(transport, params(), object(), require_fresh=False)
    assert context.owner_guard() is True and context.setup_guard() is True
    fresh = False
    assert context.owner_guard() is True
    assert context.setup_guard() is False


def test_lease_guard_never_enters_sqlite(tmp_path, monkeypatch):
    transport = Transport()
    generation = object()
    monkeypatch.setitem(credentials._owners, transport, (tmp_path.resolve(), generation))
    monkeypatch.setattr(methods, '_durable_scope', lambda *a: (params()['expectedOrganizationId'], 'owner', 'revision'))
    context = methods._context(transport, params(), object())
    monkeypatch.setattr(methods, '_durable_scope', lambda *a: pytest.fail('store guard entered SQLite'))
    assert context.lease_generation_guard() is True
    monkeypatch.setitem(credentials._owners, transport, (tmp_path.resolve(), object()))
    assert context.lease_generation_guard() is False


def ticket(**changes):
    return {'version': 1, 'sequence': '1', 'nonce': 'b' * 64, 'kind': 'refresh',
            'bindingDigest': 'c' * 64, 'authorizationEpoch': '00000000-0000-4000-8000-000000000008',
            'priorStoreRevision': 2, 'sourceRevision': 'a' * 64, **changes}


@pytest.mark.parametrize('operation,extra', [
    ('prepareOperation', {'kind': 'refresh', 'expectedRevision': 2, 'confirmed': True}),
    ('models', {'expectedRevision': 2}),
    ('executeOperation', {'ticket': ticket()}),
    ('reconcileOperation', {'ticket': ticket()}),
    ('cancelOperation', {'ticket': ticket()}),
    ('recoverOperation', {'confirmed': True}),
])
def test_session_requests_reject_injected_authority(operation, extra):
    methods._parameters(operation, params(sourceRevision='a' * 64, **extra))
    with pytest.raises(methods.BoundaryError):
        methods._parameters(operation, params(sourceRevision='a' * 64, **extra,
                                             endpoint='https://foreign.invalid'))


@pytest.mark.parametrize('change', [
    {'sequence': '0'}, {'sequence': '01'}, {'sequence': str(2**63)}, {'sequence': 1},
    {'nonce': 'bad'}, {'kind': 'other'}, {'kind': []}, {'version': True},
    {'priorStoreRevision': True}, {'priorStoreRevision': 0},
    {'sourceRevision': 'd' * 64}, {'bindingDigest': 'BAD'},
    {'authorizationEpoch': 'bad'}, {'accessToken': 'synthetic'},
])
def test_ticket_shape_is_closed_before_owner_or_service_lookup(monkeypatch, change):
    monkeypatch.setattr(methods, '_context', lambda *a, **k: pytest.fail('owner lookup reached'))
    with pytest.raises(methods.BoundaryError):
        methods.dispatch('executeOperation', params(sourceRevision='a' * 64,
            ticket=ticket(**change)), Transport(), object())


@pytest.mark.parametrize('operation', ['refresh', 'disconnect', 'reconcileRefresh'])
def test_old_uuid_mutation_surface_is_rejected(operation):
    with pytest.raises(methods.BoundaryError):
        methods._parameters(operation, params(operationId=str(uuid.UUID(int=7))))


def completed():
    return {'state': 'completed', 'ticket': ticket(), 'revision': 3,
            'verification': 'verified_current', 'catalogRevision': 3,
            'receipt': {'kind': 'refresh', 'revision': 3, 'localRetired': False,
                        'remoteRevocation': 'not_applicable'}}


def test_explicit_execute_one_catalog_read_and_retained_receipt(monkeypatch):
    calls = []
    result = completed()
    service = SimpleNamespace(execute_operation=lambda value: calls.append(('execute', value)) or result)
    catalog = SimpleNamespace(invalidate=lambda: calls.append('invalidate'),
        load=lambda revision: calls.append(('models', revision)) or {'models': []})
    monkeypatch.setattr(methods, '_context', lambda *a, **k: object())
    monkeypatch.setattr(methods, '_service', lambda *a: service)
    monkeypatch.setattr(methods, '_catalog', lambda *a: catalog)
    response = methods.dispatch('executeOperation', params(sourceRevision='a' * 64,
        ticket=ticket()), Transport(), object())
    assert response['receipt'] == result['receipt'] and response['catalogState'] == 'ready'
    assert calls.count(('execute', ticket())) == 1
    assert calls.count(('models', 3)) == 1
    catalog.load = lambda revision: (_ for _ in ()).throw(ValueError('synthetic'))
    response = methods._after_operation(service, object(), result)
    assert response == {**result, 'catalog': None, 'catalogState': 'unavailable'}
    assert calls.count(('execute', ticket())) == 1


@pytest.mark.parametrize('operation', ['reconcileOperation', 'recoverOperation'])
def test_explicit_local_recovery_chains_only_current_catalog(monkeypatch, operation):
    calls = []
    service = SimpleNamespace(
        reconcile_operation=lambda value: calls.append('reconcile') or completed(),
        recover_operation_current=lambda: calls.append('recover') or completed())
    catalog = SimpleNamespace(invalidate=lambda: None,
        load=lambda revision: calls.append(('models', revision)) or {'models': []})
    monkeypatch.setattr(methods, '_context', lambda *a, **k: object())
    monkeypatch.setattr(methods, '_service', lambda *a: service)
    monkeypatch.setattr(methods, '_catalog', lambda *a: catalog)
    fields = {'ticket': ticket()} if operation == 'reconcileOperation' else {'confirmed': True}
    response = methods.dispatch(operation, params(sourceRevision='a' * 64, **fields), Transport(), object())
    assert response['catalogState'] == 'ready' and calls[-1] == ('models', 3)
    assert len(calls) == 2


@pytest.mark.parametrize('state', ['admitted', 'uncertain', 'local_retired', 'prepared', 'completed'])
def test_no_catalog_without_current_completion_evidence(monkeypatch, state):
    monkeypatch.setattr(methods, '_catalog', lambda *a: pytest.fail('catalog accessed'))
    result = {**completed(), 'state': state, 'catalogRevision': None}
    assert methods._after_operation(object(), object(), result)['catalogState'] == 'not_requested'


def test_prepare_is_not_execute_and_disconnect_fallback_stops_chain(monkeypatch):
    calls = []
    fallback = {'state': 'local_retired', 'ticket': None, 'catalogRevision': None,
                'receipt': {'kind': 'local', 'revision': 3, 'localRetired': True,
                            'remoteRevocation': 'unconfirmed'}}
    service = SimpleNamespace(prepare_operation=lambda kind, rev: calls.append((kind, rev)) or fallback)
    monkeypatch.setattr(methods, '_context', lambda *a, **k: object())
    monkeypatch.setattr(methods, '_service', lambda *a: service)
    monkeypatch.setattr(methods, '_catalog', lambda *a: SimpleNamespace(invalidate=lambda: None))
    response = methods.dispatch('prepareOperation', params(kind='revoke', expectedRevision=2,
        confirmed=True), Transport(), object())
    assert response == fallback and calls == [('revoke', 2)]


@pytest.mark.parametrize('operation,kind,fresh', [
    ('prepareOperation', 'refresh', True), ('prepareOperation', 'revoke', False),
    ('executeOperation', 'refresh', True), ('executeOperation', 'revoke', False),
    ('reconcileOperation', 'refresh', False), ('cancelOperation', 'refresh', False),
])
def test_ticket_refresh_freshness_and_post_setup_lifecycle(monkeypatch, operation, kind, fresh):
    seen = []
    result = {'state': 'uncertain', 'catalogRevision': None}
    def context(*args, require_fresh):
        seen.append(require_fresh)
        if require_fresh:
            raise methods.BoundaryError()
        return object()
    service = SimpleNamespace(prepare_operation=lambda *a: result,
        execute_operation=lambda *a: result, reconcile_operation=lambda *a: result,
        cancel_operation=lambda *a: result)
    monkeypatch.setattr(methods, '_context', context)
    monkeypatch.setattr(methods, '_service', lambda *a: service)
    monkeypatch.setattr(methods, '_catalog', lambda *a: SimpleNamespace(invalidate=lambda: None))
    fields = ({'kind': kind, 'expectedRevision': 2, 'confirmed': True}
              if operation == 'prepareOperation' else {'ticket': ticket(kind=kind)})
    if fresh:
        with pytest.raises(methods.BoundaryError):
            methods.dispatch(operation, params(sourceRevision='a' * 64, **fields), Transport(), object())
    else:
        assert methods.dispatch(operation, params(sourceRevision='a' * 64, **fields),
                                Transport(), object())['state'] == 'uncertain'
    assert seen == [fresh]


@pytest.mark.parametrize('change', [
    {'state': 'admitted'}, {'state': 'uncertain'}, {'state': 'local_retired'},
    {'verification': 'metadata_only'}, {'catalogRevision': True},
    {'receipt': {'kind': 'revoke', 'revision': 3}},
    {'receipt': {'kind': 'refresh', 'revision': 4}},
])
def test_invalid_catalog_authority_never_fetches(monkeypatch, change):
    monkeypatch.setattr(methods, '_catalog', lambda *a: pytest.fail('catalog reached'))
    with pytest.raises(methods.BoundaryError):
        methods._after_operation(object(), object(), {**completed(), **change})


@pytest.mark.parametrize('operation', ['cancelOperation', 'reconcileOperation'])
def test_historical_source_ticket_allows_only_local_bookkeeping(monkeypatch, operation):
    seen = []
    result = {'state': 'completed', 'catalogRevision': None}
    service = SimpleNamespace(cancel_operation=lambda value: seen.append(value) or result,
        reconcile_operation=lambda value: seen.append(value) or result)
    monkeypatch.setattr(methods, '_context', lambda *a, **k: object())
    monkeypatch.setattr(methods, '_service', lambda *a: service)
    monkeypatch.setattr(methods, '_catalog', lambda *a: SimpleNamespace(invalidate=lambda: None))
    response = methods.dispatch(operation, params(sourceRevision='d' * 64, ticket=ticket()),
                                Transport(), object())
    assert response['state'] == 'completed' and seen == [ticket()]
    assert response.get('catalog') is None
    with pytest.raises(methods.BoundaryError):
        methods.dispatch('executeOperation', params(sourceRevision='d' * 64, ticket=ticket()),
                         Transport(), object())
    assert seen == [ticket()]


def test_historical_completion_cannot_request_catalog_even_if_service_mislabels(monkeypatch):
    service = SimpleNamespace(reconcile_operation=lambda value: completed())
    monkeypatch.setattr(methods, '_context', lambda *a, **k: object())
    monkeypatch.setattr(methods, '_service', lambda *a: service)
    monkeypatch.setattr(methods, '_catalog', lambda *a: SimpleNamespace(
        invalidate=lambda: None, load=lambda *a: pytest.fail('historical catalog read')))
    with pytest.raises(methods.BoundaryError):
        methods.dispatch('reconcileOperation', params(sourceRevision='d' * 64,
            ticket=ticket()), Transport(), object())



@pytest.mark.parametrize('change', [
    {'confirmed': False}, {'confirmed': 1}, {'expectedRevision': True},
    {'expectedRevision': -1}, {'consentVersion': True}, {'consentVersion': 1},
    {'confirm_host_identity_change': True}, {'canMigrateHostIdentity': True},
    {'hostIdentityState': 'current'}, {'returningBinding': {}},
])
def test_host_migration_requires_distinct_consent_without_caller_authority(change):
    value = params(expectedRevision=7, confirmed=True, consentVersion=2)
    value.update(change)
    with pytest.raises(methods.BoundaryError):
        methods._parameters('beginMigration', value)


def test_host_migration_dispatch_requires_fresh_owner_and_exact_revision(monkeypatch):
    calls = []
    original_context = methods._context
    def context(*args, require_fresh):
        assert require_fresh is True
        return object()
    def begin(revision, *, confirm_host_identity_change=False):
        calls.append((revision, confirm_host_identity_change))
        if revision != 7:
            raise RuntimeError('revision_conflict')
        return {'stage': 'pending'}
    monkeypatch.setattr(methods, '_context', context)
    monkeypatch.setattr(methods, '_service', lambda *args: SimpleNamespace(begin=begin))
    assert methods.dispatch('beginMigration', params(expectedRevision=7, confirmed=True, consentVersion=2),
                            Transport(), object()) == {'stage': 'pending'}
    assert calls == [(7, True)]
    with pytest.raises(RuntimeError, match='revision_conflict'):
        methods.dispatch('beginMigration', params(expectedRevision=6, confirmed=True, consentVersion=2),
                         Transport(), object())
    assert calls == [(7, True), (6, True)]
    calls.clear()
    monkeypatch.setattr(methods, '_context', original_context)
    monkeypatch.setattr(credentials, 'owner_home', lambda *args: None)
    with pytest.raises(methods.BoundaryError):
        methods.dispatch('beginMigration', params(expectedRevision=7, confirmed=True, consentVersion=2),
                         Transport(), object())
    assert calls == []


def test_host_migration_dispatch_uses_worker_with_captured_owner_transport(monkeypatch):
    from tui_gateway import server
    from tui_gateway.transport import current_transport
    submitted, writes, calls = [], [], []
    transport = SimpleNamespace(write=writes.append, closed=False)

    def begin(revision, *, confirm_host_identity_change):
        assert current_transport() is transport
        calls.append((revision, confirm_host_identity_change))
        return {'stage': 'pending'}

    monkeypatch.setattr(server, '_pool', SimpleNamespace(submit=submitted.append))
    monkeypatch.setattr(methods, '_context', lambda *args, **kwargs: object())
    monkeypatch.setattr(methods, '_service', lambda *args: SimpleNamespace(begin=begin))
    request = {'id': 1, 'method': 'organization.beginSetupSubscriptionHostMigration',
               'params': params(expectedRevision=7, confirmed=True, consentVersion=2)}
    assert server.dispatch(request, transport) is None
    assert len(submitted) == 1 and calls == [] and writes == []
    submitted[0]()
    assert calls == [(7, True)]
    assert writes == [{'jsonrpc': '2.0', 'id': 1, 'result': {'stage': 'pending'}}]
