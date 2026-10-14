"""Owner policy RPCs use only the authenticated endpoint's immutable launch home."""
from .method_ctx import HandlerRegistry

_registry = HandlerRegistry()


def _owner_policy_method(name, action, operation):
    allowed = {'expectedOrganizationId'}
    if operation != 'read':
        allowed |= {'expectedGeneration', 'sourceRevision', 'observation' if action == 'budgetObservation' else 'configuration'}
    if operation == 'apply':
        allowed |= {'previewVersion', 'idempotencyKey', 'confirmed'}

    @_registry.method(name)
    def handler(rid, params):
        from pathlib import Path
        from eidolon_cli.organization_owner_policy import OwnerPolicyError, owner_policy, preview_policy, apply_policy
        from eidolon_cli.organization_metadata import MetadataError
        from eidolon_cli.profiles import get_active_profile_name
        from eidolon_constants import set_eidolon_home_override, reset_eidolon_home_override
        transport = current_transport()
        if transport is None or getattr(transport, 'closed', False):
            return _err(rid, 4001, 'Owner policy requires an active backend transport')
        if not isinstance(params, dict) or set(params) != allowed:
            return _err(rid, -32602, 'Unsupported owner policy parameters; served profile only')
        home = Path(_eidolon_home).resolve()
        path = home / 'organization' / 'state.db'
        try:
            if path.resolve() != path:
                return _err(rid, 4001, 'Owner policy is outside the served profile')
            token = set_eidolon_home_override(home)
            try:
                profile = get_active_profile_name()
                operations = {'read': lambda: owner_policy(home, params['expectedOrganizationId']),
                              'preview': lambda: preview_policy(home, action, params),
                              'apply': lambda: apply_policy(home, action, params)}
                result = operations[operation]()
            finally:
                reset_eidolon_home_override(token)
            return _ok(rid, {**result, 'scope': {'kind': 'served-profile', 'profile': profile}})
        except ValueError as exc:
            return _err(rid, -32602, str(exc))
        except (OwnerPolicyError, MetadataError) as exc:
            return _err(rid, 5074, str(exc), {'reason': exc.reason})
        except (OSError, RuntimeError):
            return _err(rid, 5074, 'Owner policy is unreadable', {'reason': 'unreadable'})
    return handler


_owner_policy_method('organization.ownerPolicy', None, 'read')
for _action, _preview, _apply in (
    ('modelRouting', 'previewModelRouting', 'configureModelRouting'),
    ('budgetPolicy', 'previewBudgetPolicy', 'configureBudgetPolicy'),
    ('budgetObservation', 'previewBudgetObservation', 'recordBudgetObservation'),
):
    _owner_policy_method('organization.' + _preview, _action, 'preview')
    _owner_policy_method('organization.' + _apply, _action, 'apply')


def register(server):
    _registry.install(server)
