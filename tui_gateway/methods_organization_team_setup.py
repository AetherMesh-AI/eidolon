"""Starter-team mutations are bound to the authenticated served launch profile."""
from .method_ctx import HandlerRegistry

_registry = HandlerRegistry()


def _team_method(name, operation, fields):
    @_registry.method('organization.' + name)
    def handler(rid, params):
        from pathlib import Path
        from eidolon_cli import organization_team_setup as setup
        from eidolon_cli.organization_owner_policy import OwnerPolicyError
        from eidolon_cli.organization_metadata import MetadataError
        from eidolon_cli.profiles import get_active_profile_name
        from eidolon_constants import set_eidolon_home_override, reset_eidolon_home_override
        transport = current_transport()
        if transport is None or getattr(transport, 'closed', False):
            return _err(rid, 4001, 'Team setup requires an active backend transport')
        if not isinstance(params, dict) or set(params) != fields | {'expectedOrganizationId'}:
            return _err(rid, -32602, 'Unsupported team setup parameters; served profile only')
        home = Path(_eidolon_home).resolve()
        path = home / 'organization' / 'state.db'
        try:
            if path.resolve() != path:
                return _err(rid, 4001, 'Team setup is outside the served profile')
            token = set_eidolon_home_override(home)
            try:
                profile = get_active_profile_name()
                from agent.secret_scope import (build_profile_secret_scope, set_secret_scope, reset_secret_scope)
                secret_token = set_secret_scope(build_profile_secret_scope(home))
                try:
                    if operation in {'generate_team', 'improve_team_input'}:
                        class TransportCancellation:
                            def is_set(self):
                                return bool(getattr(transport, 'closed', False))
                        result = getattr(setup, operation)(home, params, cancel=TransportCancellation())
                    else:
                        result = getattr(setup, operation)(home,
                            params['expectedOrganizationId'] if operation in {'team_setup', 'setup_budget_policy'} else params)
                    if operation == 'improve_team_input' and (
                            getattr(transport, 'closed', False)
                            or Path(_eidolon_home).resolve() != home
                            or path.resolve() != path
                            or get_active_profile_name() != profile):
                        return _err(rid, 4001, 'Team setup is outside the active served profile')
                finally:
                    reset_secret_scope(secret_token)
            finally:
                reset_eidolon_home_override(token)
            return _ok(rid, {**result, 'scope': {'kind': 'served-profile', 'profile': profile}})
        except ValueError as exc:
            return _err(rid, -32602, str(exc))
        except (OwnerPolicyError, MetadataError) as exc:
            return _err(rid, 5076, str(exc), {'reason': exc.reason})
        except (OSError, RuntimeError):
            return _err(rid, 5076, 'Team setup is unavailable', {'reason': 'unavailable'})
    return handler


_team_method('teamSetup', 'team_setup', set())
_team_method('previewTeam', 'preview_team', {'sourceRevision', 'team'})
_team_method('createTeam', 'create_team', {'sourceRevision', 'team', 'previewVersion', 'idempotencyKey', 'confirmed'})
_team_method('generateTeam', 'generate_team', {'sourceRevision', 'jobs', 'goals', 'namingStyle', 'idempotencyKey'})
_team_method('improveTeamInput', 'improve_team_input', {'sourceRevision', 'field', 'jobs', 'goals', 'namingStyle', 'idempotencyKey'})

_team_method('setupBudgetPolicy', 'setup_budget_policy', set())
_team_method('previewSetupBudget', 'preview_setup_budget', {'sourceRevision', 'expectedGeneration', 'configuration'})
_team_method('applySetupBudget', 'apply_setup_budget', {'sourceRevision', 'expectedGeneration', 'configuration', 'previewVersion', 'idempotencyKey', 'confirmed'})


def register(server):
    _registry.install(server)
