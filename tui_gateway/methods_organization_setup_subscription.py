"""Local-owner setup connection, catalog and preflight adapter.

Connection consent is separate from reviewed setup policy activation. Client
parameters never supply filesystem, owner, registration or token identity.
"""
from copy import deepcopy
from pathlib import Path
import re
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
    'prepareOperation': _COMMON | {'kind', 'expectedRevision', 'confirmed'},
    'executeOperation': _COMMON | {'ticket'},
    'reconcileOperation': _COMMON | {'ticket'},
    'cancelOperation': _COMMON | {'ticket'},
    'recoverOperation': _COMMON | {'confirmed'},
    'models': _COMMON | {'expectedRevision'},
}


class BoundaryError(RuntimeError):
    def __init__(self):
        super().__init__('Setup subscription owner scope is unavailable')


def _parameters(operation, params):
    if operation not in _FIELDS or type(params) is not dict or set(params) != _FIELDS[operation]:
        raise BoundaryError()
    from eidolon_cli.organization_metadata import _uuid
    try:
        _uuid(params['expectedOrganizationId'], 'expectedOrganizationId')
    except (ValueError, TypeError):
        raise BoundaryError() from None
    if (type(params['sourceRevision']) is not str
            or not 1 <= len(params['sourceRevision']) <= 256):
        raise BoundaryError()
    if operation in {'begin', 'prepareOperation'}:
        if (params['confirmed'] is not True or type(params['expectedRevision']) is not int
                or not 0 <= params['expectedRevision'] < 2**63):
            raise BoundaryError()
    if operation == 'begin' and (type(params['consentVersion']) is not int or params['consentVersion'] != 1):
        raise BoundaryError()
    if operation == 'cancel' and (type(params['attemptId']) is not str or not 1 <= len(params['attemptId']) <= 128):
        raise BoundaryError()
    if operation == 'models' and (type(params['expectedRevision']) is not int
            or not 1 <= params['expectedRevision'] < 2**63):
        raise BoundaryError()
    if operation == 'prepareOperation' and (type(params['kind']) is not str
            or params['kind'] not in {'refresh', 'revoke'}):
        raise BoundaryError()
    if operation == 'recoverOperation' and params['confirmed'] is not True:
        raise BoundaryError()
    if operation in {'executeOperation', 'reconcileOperation', 'cancelOperation'}:
        _ticket(params['ticket'], params['sourceRevision'] if operation == 'executeOperation' else None)


def _ticket(ticket, source_revision):
    from eidolon_cli.organization_metadata import _uuid
    fields = {'version', 'sequence', 'nonce', 'kind', 'bindingDigest',
              'authorizationEpoch', 'priorStoreRevision', 'sourceRevision'}
    if type(ticket) is not dict or set(ticket) != fields:
        raise BoundaryError()
    sequence = ticket['sequence']
    if (type(ticket['version']) is not int or ticket['version'] != 1
            or type(sequence) is not str or re.fullmatch(r'[1-9][0-9]{0,18}', sequence) is None
            or int(sequence) >= 2**63 or type(ticket['kind']) is not str
            or ticket['kind'] not in {'refresh', 'revoke'}
            or type(ticket['priorStoreRevision']) is not int
            or not 1 <= ticket['priorStoreRevision'] < 2**63
            or (source_revision is not None and ticket['sourceRevision'] != source_revision)):
        raise BoundaryError()
    for field in ('nonce', 'bindingDigest', 'sourceRevision'):
        if type(ticket[field]) is not str or re.fullmatch(r'[a-f0-9]{64}', ticket[field]) is None:
            raise BoundaryError()
    try:
        _uuid(ticket['authorizationEpoch'], 'authorizationEpoch')
    except (ValueError, TypeError):
        raise BoundaryError() from None


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

    def lease_generation_guard():
        # Registry reads only: NEVER SQLite or publication/service/attempt locks.
        return owner_generation(transport) is generation and owner_home(transport) == home

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
                        setup_guard=setup_guard, lease_generation_guard=lease_generation_guard)


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


def _catalog(service, context):
    from eidolon_cli.organization_setup_subscription_catalog import SubscriptionCatalog
    with _services_lock:
        catalog = getattr(service, '_setup_model_catalog', None)
        if catalog is None:
            catalog = SubscriptionCatalog(service, context)
            service._setup_model_catalog = catalog
        return catalog


def _load_catalog(service, context, revision):
    catalog = _catalog(service, context)
    result = catalog.load(revision)
    # A renderer catalog ID is never authority. Keep the exact server-returned
    # identifier beside this context; require() revalidates its account and TTL.
    with _services_lock:
        service._setup_catalog_evidence = deepcopy(result)
    return result


def _installation_authority():
    from tui_gateway import server
    return server._subscription_installation_authority()


def _current_context(transport, home, expected_id=None):
    from eidolon_cli.organization_metadata import _inspect
    from eidolon_cli.organization_owner_policy import _connection
    from eidolon_cli.organization_team_setup import _current
    from tui_gateway.organization_credential_transport import owner_home
    home = Path(home).resolve()
    if owner_home(transport) != home:
        raise BoundaryError()
    with _connection(home) as conn:
        descriptor = _inspect(conn)
        if descriptor['state'] != 'ready':
            raise BoundaryError()
        org = descriptor['metadata']['organizationId']
        if expected_id is not None and expected_id != org:
            raise BoundaryError()
        _, _, _, revision = _current(conn, home, org)
    authority = _installation_authority()
    context = _context(transport, {'expectedOrganizationId': org, 'sourceRevision': revision},
                       authority, require_fresh=True)
    return context, _service(transport, context, authority)


def subscription_preflight(transport, home, candidate, *, inference=False):
    """Capture exact evidence before publication/SQLite writer acquisition.

    Only explicit inference may discover an account catalog here. Policy review
    requires the model evidence already obtained by the owner's Models action.
    No implicit OAuth, credential refresh or general-runtime route is possible.
    """
    from eidolon_cli.organization_setup_subscription_policy import SubscriptionPreflight
    context, service = _current_context(transport, home)
    state = service.status()
    revision = state['connectionRevision']
    if state['connectionState'] != 'active' or state['cleanupPending']:
        raise BoundaryError()
    descriptor = service.current_descriptor(revision)
    binding = descriptor['binding']
    account = service._store._binding_digest(binding)
    if (candidate['connectionId'] != account
            or candidate['authorizationEpoch'] != descriptor['authorizationEpoch']):
        raise BoundaryError()
    catalog = _catalog(service, context)
    if inference:
        evidence = _load_catalog(service, context, revision)
    else:
        with _services_lock:
            evidence = deepcopy(getattr(service, '_setup_catalog_evidence', None))
        if evidence is None:
            raise BoundaryError()
    evidence = catalog.require(evidence['catalogId'], candidate['modelSlug'], revision)
    if (evidence['connectionId'] != account
            or evidence['authorizationEpoch'] != descriptor['authorizationEpoch']
            or evidence['sourceRevision'] != context.source_digest):
        raise BoundaryError()
    lease = service.lease_access_current(revision) if inference else None
    cancelled = service._lease_cancel
    generation_guard = context.lease_generation_guard

    def cheap_guard():
        # Capture only the Event and registry-only closure. Accounting workers
        # must not retain the service or its token-bearing lease collection.
        return not cancelled.is_set() and generation_guard() is True

    def release():
        if lease is not None:
            service.release_lease(lease)

    try:
        # This guard is registry/Event-only. The authoritative writer may use it
        # with the nonblocking store attestation, never current_descriptor().
        if cheap_guard() is not True:
            raise BoundaryError()
        return SubscriptionPreflight(binding=binding, store=service._store,
            revision=revision, epoch=descriptor['authorizationEpoch'],
            account_digest=account, model_slug=candidate['modelSlug'],
            catalog_evidence=deepcopy(evidence), owner_generation=context.owner_generation,
            cheap_guard=cheap_guard, lease=lease, release=release)
    except BaseException:
        release()
        raise


def turnover_subscription_context(transport, home, expected_id):
    """After commit, replace stale source-scoped auth/catalog state, without I/O to a provider."""
    context, _ = _current_context(transport, home, expected_id)
    return {'sourceRevision': context.source_digest, 'catalogState': 'not_requested'}


def _after_operation(service, context, result):
    # Only independently revalidated current refresh completion can chain one
    # discovery GET. A historical receipt never becomes catalog authority.
    revision = result.get('catalogRevision')
    if revision is None:
        return {**result, 'catalog': None, 'catalogState': 'not_requested'}
    if (type(revision) is not int or not 1 <= revision < 2**63
            or result.get('state') != 'completed'
            or result.get('verification') != 'verified_current'
            or type(result.get('receipt')) is not dict
            or result['receipt'].get('kind') != 'refresh'
            or result['receipt'].get('revision') != revision):
        raise BoundaryError()
    catalog = _catalog(service, context)
    catalog.invalidate()
    try:
        models = _load_catalog(service, context, revision)
        return {**result, 'catalog': models, 'catalogState': 'ready'}
    except (OSError, RuntimeError, ValueError, TypeError):
        return {**result, 'catalog': None, 'catalogState': 'unavailable'}


def dispatch(operation, params, transport, authority):
    """Closed owner-authorized operations; no inference or policy activation."""
    _parameters(operation, params)
    fresh = operation in {'begin', 'models'} or (
        operation == 'prepareOperation' and params['kind'] == 'refresh') or (
        operation == 'executeOperation' and params['ticket']['kind'] == 'refresh')
    context = _context(transport, params, authority, require_fresh=fresh)
    service = _service(transport, context, authority)
    if operation == 'status':
        return service.status()
    if operation == 'cancel':
        return service.cancel(params['attemptId'])
    if operation == 'begin':
        return service.begin(params['expectedRevision'])
    if operation == 'models':
        return _load_catalog(service, context, params['expectedRevision'])
    _catalog(service, context).invalidate()
    if operation == 'prepareOperation':
        return service.prepare_operation(params['kind'], params['expectedRevision'])
    if operation == 'cancelOperation':
        return service.cancel_operation(params['ticket'])
    if operation == 'executeOperation':
        result = service.execute_operation(params['ticket'])
    elif operation == 'reconcileOperation':
        result = service.reconcile_operation(params['ticket'])
    else:
        result = service.recover_operation_current()
    if result.get('catalogRevision') is not None:
        _ticket(result.get('ticket'), params['sourceRevision'])
    return _after_operation(service, context, result)


def _method(name, operation):
    @_registry.method('organization.' + name)
    def handler(rid, params):
        try:
            from tui_gateway.methods_organization_setup_subscription import dispatch
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
_method('prepareSetupSubscriptionOperation', 'prepareOperation')
_method('executeSetupSubscriptionOperation', 'executeOperation')
_method('reconcileSetupSubscriptionOperation', 'reconcileOperation')
_method('cancelSetupSubscriptionOperation', 'cancelOperation')
_method('recoverSetupSubscriptionOperation', 'recoverOperation')
_method('setupSubscriptionModels', 'models')


def register(server):
    _registry.install(server)

