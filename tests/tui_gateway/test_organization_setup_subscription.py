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
    ('disconnect', {'expectedRevision': 1, 'confirmed': True}, False),
    ('begin', {'expectedRevision': 0, 'confirmed': True, 'consentVersion': 1}, True),
])
def test_only_begin_requires_fresh_setup(monkeypatch, operation, extra, requires_fresh):
    seen = []
    def context(*args, require_fresh):
        seen.append(require_fresh)
        if require_fresh:
            raise methods.BoundaryError()
        return object()
    service = SimpleNamespace(status=lambda: {}, cancel=lambda attempt: {},
                              disconnect_current=lambda revision: {})
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
