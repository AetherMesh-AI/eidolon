"""Server-only owner capability for the credential surface.

This is issued only after the WS route's existing authentication/origin gates.
Forwarded, shared, externally bound and unknown transports are unsupported.
Client params and auth_identity dictionaries never create this capability.
"""
from pathlib import Path
import ipaddress
import threading
import weakref

_lock = threading.Lock()
_owners = weakref.WeakKeyDictionary()


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
        with _lock:
            _owners[transport] = (Path(home).resolve(), object())
        # Re-registering the same object is a new connection generation. Never
        # restore input authority from its retired generation.
        from eidolon_cli.organization_credential_service import secure_inputs
        secure_inputs.cancel_transport(transport)


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
    with _lock:
        _owners.pop(transport, None)
    from eidolon_cli.organization_credential_service import secure_inputs
    secure_inputs.cancel_transport(transport)
