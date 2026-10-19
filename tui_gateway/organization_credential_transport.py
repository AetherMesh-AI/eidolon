"""Server-only owner capability for the credential surface.

This is issued only after the WS route's existing authentication/origin gates.
Forwarded, shared, externally bound and unknown transports are unsupported.
Client params and auth_identity dictionaries never create this capability.
"""
from contextlib import contextmanager
from pathlib import Path
import ipaddress
import threading
import sys
import weakref

_lock = threading.Lock()
_owners = weakref.WeakKeyDictionary()
_publication_locks = weakref.WeakKeyDictionary()
_publication_thread = threading.local()


def _publication_lock(transport):
    # Stable across unregister/re-register. Never wait for it under _lock.
    with _lock:
        result = _publication_locks.get(transport)
        if result is None:
            result = threading.Lock()
            _publication_locks[transport] = result
        return result


def _reject_publication_reentry(transport):
    if id(transport) in getattr(_publication_thread, 'active', ()):
        raise RuntimeError('Owner publication lifecycle is non-reentrant')


@contextmanager
def owner_publication(transport, home, generation):
    """Local publication fence only; never hold across HTTP/browser/listener join.

    Order: service -> publication authority -> durable attempt -> store.
    Registry lookup is brief and never waits for publication or service locks.
    """
    _reject_publication_reentry(transport)
    publication = _publication_lock(transport)
    with publication:
        with _lock:
            current = _owners.get(transport)
            if (getattr(transport, 'closed', True) or current is None
                    or current[1] is not generation or current[0] != Path(home).resolve()):
                raise RuntimeError('Owner publication authority is retired')
        active = set(getattr(_publication_thread, 'active', ()))
        active.add(id(transport))
        _publication_thread.active = active
        try:
            yield
        finally:
            active.remove(id(transport))
            _publication_thread.active = active



def secure_local_owner_home(ws, app):
    if getattr(app.state, 'auth_required', None) is not False:
        return None
    bound = getattr(app.state, 'bound_host', None)
    client = getattr(ws, 'client', None)
    scope = getattr(ws, 'scope', {})
    server = scope.get('server')
    if not bound or client is None or not server:
        return None
    headers = getattr(ws, 'headers', {})
    if any(name in headers for name in ('forwarded', 'x-forwarded-for', 'x-forwarded-host', 'x-forwarded-proto')):
        return None
    try:
        if not all(ipaddress.ip_address(host).is_loopback for host in (bound, client.host, server[0])):
            return None
    except ValueError:
        return None
    from tui_gateway import server as gateway
    return Path(gateway._eidolon_home).resolve()


def register_owner(transport, home):
    if home is not None:
        _reject_publication_reentry(transport)
        publication = _publication_lock(transport)
        with publication:
            with _lock:
                previous = _owners.get(transport)
                _owners[transport] = (Path(home).resolve(), object())
        # No service cancellation while registry/publication authority is held.
        from eidolon_cli.organization_credential_service import secure_inputs
        secure_inputs.cancel_transport(transport)
        if previous is not None:
            _retire_setup_subscription(transport, previous[1])


def owner_home(transport):
    if transport is None or getattr(transport, 'closed', True):
        return None
    with _lock:
        try:
            registration = _owners.get(transport)
            return registration[0] if registration else None
        except TypeError:
            return None


def owner_generation(transport):
    if transport is None or getattr(transport, 'closed', True):
        return None
    with _lock:
        try:
            registration = _owners.get(transport)
            return registration[1] if registration else None
        except TypeError:
            return None


def retire_owner(transport):
    _reject_publication_reentry(transport)
    publication = _publication_lock(transport)
    with publication:
        with _lock:
            previous = _owners.pop(transport, None)
    from eidolon_cli.organization_credential_service import secure_inputs
    secure_inputs.cancel_transport(transport)
    if previous is not None:
        _retire_setup_subscription(transport, previous[1])


def _retire_setup_subscription(transport, generation):
    # A delayed retirement of A must never cancel a newly registered B service.
    from eidolon_cli.organization_setup_subscription_auth import cancel_transport
    try:
        cancel_transport(transport, generation=generation)
    finally:
        adapter = sys.modules.get('tui_gateway.methods_organization_setup_subscription')
        if adapter is not None:
            adapter.retire_service(transport, generation)
