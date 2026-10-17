"""Owner-only setup connection adapter; deliberately not registered by the server.

This surface cannot activate a budget policy or enable general model routing.
Client parameters never supply filesystem, owner, registration or token identity.
"""
from pathlib import Path
import threading
import weakref

from .method_ctx import HandlerRegistry

_registry = HandlerRegistry()
_services = weakref.WeakKeyDictionary()
_services_lock = threading.Lock()
_COMMON = {'expectedOrganizationId', 'sourceRevision'}
_FIELDS = {
    'status': _COMMON,
    'begin': _COMMON | {'expectedRevision', 'confirmed', 'consentVersion'},
    'cancel': _COMMON | {'attemptId'},
    'disconnect': _COMMON | {'expectedRevision', 'confirmed'},
}


class BoundaryError(RuntimeError):
    def __init__(self):
        super().__init__('Setup subscription owner scope is unavailable')


def _parameters(operation, params):
    if type(params) is not dict or set(params) != _FIELDS[operation]:
        raise BoundaryError()
    from eidolon_cli.organization_metadata import _uuid
    try:
        _uuid(params['expectedOrganizationId'], 'expectedOrganizationId')
    except (ValueError, TypeError):
        raise BoundaryError() from None
    if (type(params['sourceRevision']) is not str
            or not 1 <= len(params['sourceRevision']) <= 256):
        raise BoundaryError()
    if operation in {'begin', 'disconnect'}:
        if (params['confirmed'] is not True or type(params['expectedRevision']) is not int
                or not 0 <= params['expectedRevision'] < 2**63):
            raise BoundaryError()
    if operation == 'begin' and (type(params['consentVersion']) is not int or params['consentVersion'] != 1):
        raise BoundaryError()
    if operation == 'cancel' and (type(params['attemptId']) is not str or not 1 <= len(params['attemptId']) <= 128):
        raise BoundaryError()


def _durable_scope(home, expected_id, require_fresh=False):
    from eidolon_cli.organization_owner_policy import _connection
    from eidolon_cli.organization_team_setup import _current, _fresh
    with _connection(home) as conn:
        identity, _, settings, revision = _current(conn, home, expected_id)
        if require_fresh and not _fresh(conn, settings):
            raise BoundaryError()
        return identity['organizationId'], identity['owner']['ownerId'], revision


def _context(transport, params, authority, *, require_fresh=False):
    from eidolon_cli.organization_setup_subscription_auth import OwnerContext
    from tui_gateway.organization_credential_transport import owner_home, owner_generation, owner_publication
    home, generation = owner_home(transport), owner_generation(transport)
    if home is None or generation is None or authority is None:
        raise BoundaryError()
    home = Path(home)
    org, owner, revision = _durable_scope(home, params['expectedOrganizationId'], require_fresh)
    if revision != params['sourceRevision']:
        raise BoundaryError()

    def guard():
        try:
            return (owner_generation(transport) is generation and owner_home(transport) == home
                    and _durable_scope(home, org) == (org, owner, revision))
        except (OSError, RuntimeError, ValueError):
            return False

    def setup_guard():
        try:
            return guard() and _durable_scope(home, org, True) == (org, owner, revision)
        except (OSError, RuntimeError, ValueError):
            return False

    return OwnerContext(str(home), org, owner, revision, generation, transport, guard,
                        publication_authority=lambda: owner_publication(transport, home, generation),
                        setup_guard=setup_guard)


def _service(transport, context, authority):
    from eidolon_cli.organization_setup_subscription_auth import SubscriptionAuthService
    from eidolon_cli.organization_setup_subscription_host import PendingAttempts
    from eidolon_cli.organization_setup_subscription_store import SubscriptionStore
    key = (context.profile_home, context.organization_id, context.owner_id,
           context.source_digest, context.owner_generation)
    with _services_lock:
        previous = _services.get(transport)
        if previous is not None and previous[0] == key:
            return previous[1]
    # Cancellation may take attempt/store locks; never call it under registry lock.
    if previous is not None:
        from eidolon_cli.organization_setup_subscription_auth import cancel_transport
        cancel_transport(transport, generation=previous[0][-1])
    candidate = SubscriptionAuthService(SubscriptionStore(context.profile_home),
        PendingAttempts(authority, context.profile_home), context)
    with _services_lock:
        current = _services.get(transport)
        if current is not previous:
            raise BoundaryError()
        if context.owner_guard() is not True:
            raise BoundaryError()
        _services[transport] = (key, candidate)
    return candidate


def retire_service(transport, generation):
    """Drop only the retired generation's strong service/context references."""
    with _services_lock:
        current = _services.get(transport)
        if current is not None and current[0][-1] is generation:
            _services.pop(transport, None)


def dispatch(operation, params, transport, authority):
    """Backend-only entry point; production registration is a later review gate."""
    _parameters(operation, params)
    context = _context(transport, params, authority, require_fresh=(operation == 'begin'))
    service = _service(transport, context, authority)
    if operation == 'status':
        return service.status()
    if operation == 'cancel':
        return service.cancel(params['attemptId'])
    if operation == 'begin':
        return service.begin(params['expectedRevision'])
    return service.disconnect_current(params['expectedRevision'])


def _method(name, operation):
    @_registry.method('organization.' + name)
    def handler(rid, params):
        try:
            result = dispatch(operation, params, current_transport(), _subscription_installation_authority())
            return _ok(rid, result)
        except (OSError, RuntimeError, ValueError, TypeError):
            # Never stringify auth/native/HTTP exceptions into RPC or crash logs.
            return _err(rid, 5077, 'Setup subscription connection is unavailable',
                        {'reason': 'setup_subscription_unavailable'})
    return handler


_method('setupSubscriptionStatus', 'status')
_method('beginSetupSubscription', 'begin')
_method('cancelSetupSubscription', 'cancel')
_method('disconnectSetupSubscription', 'disconnect')


def register(server):
    _registry.install(server)
