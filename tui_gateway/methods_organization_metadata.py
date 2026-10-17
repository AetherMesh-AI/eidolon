"""Display metadata is restricted to this transport's served launch profile.

Unlike older organization RPCs, neither a profile parameter nor a UUID grants
sibling routing. Shared-backend clients must use a separately authorized route.
"""
from .method_ctx import HandlerRegistry

_registry = HandlerRegistry()
method = _registry.method


def _metadata_method(name, allowed):
    def decorate(fn):
        @method(name)
        def handler(rid, params):
            from pathlib import Path
            from eidolon_cli.organization_metadata import MetadataError
            from eidolon_cli.profiles import get_active_profile_name
            from eidolon_constants import set_eidolon_home_override, reset_eidolon_home_override
            transport = current_transport()
            if transport is None or getattr(transport, 'closed', False):
                return _err(rid, 4001, 'Organization metadata requires an active backend transport')
            if not isinstance(params, dict) or set(params) != allowed:
                return _err(rid, -32602, 'Unsupported organization metadata parameters; served profile only')
            # The immutable launch home, not request parameters or an inherited
            # ContextVar override, is the authenticated endpoint's authority.
            home = Path(_eidolon_home).resolve()
            path = home / 'organization' / 'state.db'
            try:
                try:
                    resolved = path.resolve()
                except RuntimeError:
                    raise MetadataError('unreadable') from None
                if resolved != path:
                    return _err(rid, 4001, 'Organization metadata is outside the served profile')
                token = set_eidolon_home_override(home)
                try:
                    profile = get_active_profile_name()
                finally:
                    reset_eidolon_home_override(token)
                result = fn(path, params)
                return _ok(rid, {**result, 'scope': {'kind': 'served-profile', 'profile': profile}})
            except ValueError as exc:
                return _err(rid, -32602, str(exc))
            except MetadataError as exc:
                return _err(rid, 5072, str(exc), {'reason': exc.reason})
            except OSError:
                return _err(rid, 5072, 'Organization metadata is unreadable', {'reason': 'unreadable'})
        return handler
    return decorate


@_metadata_method('organization.descriptor', set())
def _(path, params):
    from eidolon_cli.organization_metadata import read_descriptor
    from eidolon_cli.profile_isolation import isolation_view
    result = read_descriptor(path)
    isolation = isolation_view(path.parent.parent)
    return {**result, 'isolation': isolation} if isolation else result


@_metadata_method('organization.saveMetadata', {
    'displayName', 'ownerDisplayName', 'expectedOrganizationId', 'expectedRevision', 'idempotencyKey'})
def _(path, params):
    from eidolon_cli.organization_metadata import save_metadata
    return save_metadata(path, display_name=params['displayName'], owner_display_name=params['ownerDisplayName'],
                         expected_organization_id=params['expectedOrganizationId'],
                         expected_revision=params['expectedRevision'], idempotency_key=params['idempotencyKey'])


def register(server):
    _registry.install(server)
