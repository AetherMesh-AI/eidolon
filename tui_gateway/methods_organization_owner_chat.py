"""Owner-chat RPCs reuse the organization's authenticated profile boundary."""
from .method_ctx import HandlerRegistry
from .methods_organization import _organization_method, _organization_params

_registry = HandlerRegistry()


def _view(value):
    from eidolon_cli.profiles import get_active_profile_name
    return {**value, 'profile': get_active_profile_name()}


@_organization_method('organization.ownerChat.open', registry=_registry)
def _open(params, service):
    _organization_params(params, {'agentId', 'identityId'})
    return _view(service().owner_chat.open(params.get('agentId'), params.get('identityId')))


@_organization_method('organization.ownerChat.read', registry=_registry)
def _read(params, service):
    _organization_params(params, {'threadId', 'identityId'})
    return _view(service().owner_chat.read(params.get('threadId'), params.get('identityId')))


@_organization_method('organization.ownerChat.send', registry=_registry)
def _send(params, service):
    _organization_params(params, {'threadId', 'identityId', 'text', 'replyToMessageId', 'idempotencyKey'})
    if 'replyToMessageId' not in params:
        raise ValueError('replyToMessageId must be explicit; use null only for an empty conversation')
    return _view(service().owner_chat.send(params.get('threadId'), params.get('identityId'), params.get('text'),
                                          params['replyToMessageId'], params.get('idempotencyKey')))


@_organization_method('organization.ownerChat.cancel', registry=_registry)
def _cancel(params, service):
    _organization_params(params, {'threadId', 'identityId', 'turnId'})
    return _view(service().owner_chat.cancel(params.get('threadId'), params.get('identityId'), params.get('turnId')))


def register(server):
    # These handlers need no server-global helpers of their own. Their shared
    # organization wrapper is rebound by the registry to the authenticated server.
    _registry.install(server)
