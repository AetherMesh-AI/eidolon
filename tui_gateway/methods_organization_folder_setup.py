"""Folder access RPCs use only the authenticated endpoint's immutable launch home."""
from .method_ctx import HandlerRegistry

_registry = HandlerRegistry()


def _folder_method(name, operation):
    allowed = {'expectedOrganizationId'}
    if operation in {'preview', 'apply'}:
        allowed |= {'expectedGeneration', 'sourceRevision', 'candidate' if operation == 'apply' else 'folder'}
    if operation == 'apply':
        allowed |= {'previewVersion', 'idempotencyKey', 'confirmed'}
    if operation in {'revoke', 'review'}:
        allowed |= {'expectedGeneration', 'alias', 'grantHash', 'confirmed'}
        allowed |= {'idempotencyKey'} if operation == 'revoke' else {'reviewVersion', 'acknowledgeUncertain'}

    @_registry.method(name)
    def handler(rid, params):
        from pathlib import Path
        from eidolon_cli.organization_owner_policy import OwnerPolicyError
        from eidolon_cli.organization_folder_setup import folder_setup, preview_folder, register_folder
        from eidolon_cli.organization_folder_revocation import folder_access, revoke_folder, review_revocation
        from eidolon_cli.organization_metadata import MetadataError
        from eidolon_cli.profiles import get_active_profile_name
        from eidolon_constants import set_eidolon_home_override, reset_eidolon_home_override
        transport = current_transport()
        if transport is None or getattr(transport, 'closed', False):
            return _err(rid, 4001, 'Folder setup requires an active backend transport')
        if not isinstance(params, dict) or set(params) != allowed:
            return _err(rid, -32602, 'Unsupported folder setup parameters; served profile only')
        home = Path(_eidolon_home).resolve()
        path = home / 'organization' / 'state.db'
        try:
            if path.resolve() != path:
                return _err(rid, 4001, 'Folder setup is outside the served profile')
            token = set_eidolon_home_override(home)
            try:
                profile = get_active_profile_name()
                operations = {'read': lambda: folder_setup(home, params['expectedOrganizationId']),
                              'preview': lambda: preview_folder(home, params),
                              'apply': lambda: register_folder(home, params),
                              'access': lambda: folder_access(home, params['expectedOrganizationId']),
                              'revoke': lambda: revoke_folder(home, params),
                              'review': lambda: review_revocation(home, params)}
                result = operations[operation]()
            finally:
                reset_eidolon_home_override(token)
            return _ok(rid, {**result, 'scope': {'kind': 'served-profile', 'profile': profile}})
        except ValueError as exc:
            return _err(rid, -32602, str(exc))
        except (OwnerPolicyError, MetadataError) as exc:
            return _err(rid, 5074, str(exc), {'reason': exc.reason})
        except (OSError, RuntimeError):
            return _err(rid, 5074, 'Folder setup is unreadable', {'reason': 'unreadable'})
    return handler


_folder_method('organization.folderSetup', 'read')
_folder_method('organization.previewProjectFolder', 'preview')
_folder_method('organization.registerProjectFolder', 'apply')
_folder_method('organization.folderAccess', 'access')
_folder_method('organization.revokeProjectFolder', 'revoke')
_folder_method('organization.reviewFolderRevocation', 'review')


def register(server):
    _registry.install(server)
