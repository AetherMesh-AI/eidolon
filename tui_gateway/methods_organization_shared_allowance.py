"""Installation allowance controls are bound to the immutable served launch home.

A local profile owner is not an installation administrator. The shared ledger
validates launch-root authority before any edit and limits member reads to their
own enrollment; IDs in requests are compare-and-swap preconditions only.
"""
from .method_ctx import HandlerRegistry

_registry = HandlerRegistry()


def _shared_allowance_method(name, action, operation):
    allowed = {'expectedOrganizationId'}
    if operation != 'read':
        allowed |= {'expectedOwnerId', 'expectedGeneration', 'sourceRevision',
                    'observation' if action == 'observation' else 'configuration'}
    if operation == 'apply':
        allowed |= {'previewVersion', 'idempotencyKey', 'confirmed'}

    @_registry.method(name)
    def handler(rid, params):
        from pathlib import Path
        from eidolon_cli.organization_shared_allowance import SharedAllowanceError, resolve_authority
        from eidolon_cli.organization_shared_allowance_controls import (
            shared_allowance, preview_shared_allowance, apply_shared_allowance,
        )
        from eidolon_cli.organization_metadata import MetadataError
        from eidolon_constants import set_eidolon_home_override, reset_eidolon_home_override

        transport = current_transport()
        if transport is None or getattr(transport, 'closed', False):
            return _err(rid, 4001, 'Shared allowance requires an active backend transport')
        if not isinstance(params, dict) or set(params) != allowed:
            return _err(rid, -32602, 'Unsupported shared allowance parameters; served launch home only')
        try:
            # Do not resolve first: doing so would erase symlink evidence and
            # turn a named/aliased launch home into an installation admin route.
            home = Path(_eidolon_home).absolute()
            if home.resolve() != home:
                return _err(rid, 4001, 'Shared allowance requires a canonical served launch home')
            token = set_eidolon_home_override(home)
            try:
                profile = resolve_authority(home)['profile']
                operations = {
                    'read': lambda: shared_allowance(home, params['expectedOrganizationId']),
                    'preview': lambda: preview_shared_allowance(home, action, params),
                    'apply': lambda: apply_shared_allowance(home, action, params),
                }
                result = operations[operation]()
            finally:
                reset_eidolon_home_override(token)
            return _ok(rid, {**result, 'scope': {'kind': 'served-profile', 'profile': profile}})
        except (SharedAllowanceError, MetadataError) as exc:
            return _err(rid, 5075, 'Shared allowance request could not be completed', {'reason': exc.reason})
        except (ValueError, TypeError):
            return _err(rid, -32602, 'Invalid shared allowance request')
        except (OSError, RuntimeError):
            return _err(rid, 5075, 'Shared allowance is unreadable', {'reason': 'unreadable'})
    return handler


_shared_allowance_method('organization.sharedAllowance', None, 'read')
for _action, _preview, _apply in (
    ('configuration', 'previewSharedAllowance', 'configureSharedAllowance'),
    ('observation', 'previewSharedAllowanceObservation', 'recordSharedAllowanceObservation'),
):
    _shared_allowance_method('organization.' + _preview, _action, 'preview')
    _shared_allowance_method('organization.' + _apply, _action, 'apply')


def register(server):
    _registry.install(server)
