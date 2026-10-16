"""Prepared validation setup is scoped to the authenticated served owner home."""
from .method_ctx import HandlerRegistry

_registry = HandlerRegistry()


def _prepared_method(name, operation):
    allowed = {'expectedOrganizationId'}
    if operation != 'read':
        allowed |= {'expectedOwnerId', 'expectedGeneration', 'expectedSidecarRevision'}
    if operation in {'preview', 'save'}:
        allowed |= {'sourceRevision'}
    if operation == 'preview':
        allowed |= {'selection'}
    if operation == 'save':
        allowed |= {'candidate', 'candidateHash', 'previewVersion', 'idempotencyKey', 'confirmed'}
    if operation == 'revoke':
        allowed |= {'projectId', 'registrationRevision', 'registrationHash', 'idempotencyKey', 'confirmed'}

    @_registry.method(name)
    def handler(rid, params):
        from pathlib import Path
        from eidolon_cli.organization_metadata import MetadataError
        from eidolon_cli.organization_owner_policy import OwnerPolicyError
        from eidolon_cli.organization_prepared_validation_setup import (
            prepared_validation_setup, preview_prepared_validation,
            revoke_prepared_validation, save_prepared_validation,
        )
        from eidolon_cli.profiles import get_active_profile_name
        from eidolon_constants import reset_eidolon_home_override, set_eidolon_home_override
        transport = current_transport()
        if transport is None or getattr(transport, 'closed', False):
            return _err(rid, 4001, 'Prepared validation setup requires an active backend transport')
        if type(params) is not dict or set(params) != allowed:
            return _err(rid, -32602, 'Unsupported prepared validation parameters; served profile only')
        home = Path(_eidolon_home).resolve()
        path = home / 'organization' / 'state.db'
        try:
            if path.resolve() != path:
                return _err(rid, 4001, 'Prepared validation setup is outside the served profile')
            token = set_eidolon_home_override(home)
            try:
                profile = get_active_profile_name()
                operations = {
                    'read': lambda: prepared_validation_setup(home, params['expectedOrganizationId']),
                    'preview': lambda: preview_prepared_validation(home, params),
                    'save': lambda: save_prepared_validation(home, params),
                    'revoke': lambda: revoke_prepared_validation(home, params),
                }
                result = operations[operation]()
            finally:
                reset_eidolon_home_override(token)
            return _ok(rid, {**result, 'scope': {'kind': 'served-profile', 'profile': profile}})
        except ValueError as exc:
            return _err(rid, -32602, str(exc))
        except (OwnerPolicyError, MetadataError) as exc:
            return _err(rid, 5075, str(exc), {'reason': exc.reason})
        except (OSError, RuntimeError):
            return _err(rid, 5075, 'Prepared validation setup is unreadable', {'reason': 'unreadable'})
    return handler


_prepared_method('organization.preparedValidationSetup', 'read')
_prepared_method('organization.previewPreparedValidation', 'preview')
_prepared_method('organization.savePreparedValidation', 'save')
_prepared_method('organization.revokePreparedValidation', 'revoke')


def register(server):
    _registry.install(server)
