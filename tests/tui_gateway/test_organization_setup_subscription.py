"""Synthetic owner/adapter proof. No browser, native vault or network operations."""
from pathlib import Path
from types import SimpleNamespace
import ast
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


def test_authority_capture_precedes_dotenv_and_rpc_remains_unregistered():
    # Parse only: importing the server can initialize unrelated application state.
    source = (Path(__file__).resolve().parents[2] / 'tui_gateway' / 'server.py').read_text()
    tree = ast.parse(source)
    capture = source.index('_setup_subscription_authority = HostAuthority.capture(')
    dotenv = source.index('load_hermes_dotenv(hermes_home=')
    assert capture < dotenv
    assert 'get_default_hermes_root, get_process_eidolon_home)' in source
    assert not any(isinstance(node, ast.ImportFrom)
                   and node.module == 'tui_gateway.methods_organization_setup_subscription'
                   for node in ast.walk(tree))
    assert 'methods_organization_setup_subscription.register(' not in source


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


@pytest.mark.parametrize('operation,extra,requires_fresh', [
    ('status', {}, False), ('cancel', {'attemptId': 'attempt'}, False),
    ('prepareOperation', {'kind': 'revoke', 'expectedRevision': 1, 'confirmed': True}, False),
    ('begin', {'expectedRevision': 0, 'confirmed': True, 'consentVersion': 1}, True),
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
