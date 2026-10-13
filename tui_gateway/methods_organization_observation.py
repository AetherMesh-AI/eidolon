"""Read-only organization observation restricted to the authenticated launch home."""
from .method_ctx import HandlerRegistry, bind_module

_registry = HandlerRegistry()
method = _registry.method


@method('organization.observe')
def _organization_observe(rid, params):
    return _organization_observe_impl(rid, params, detail=False)


@method('organization.observeRead')
def _organization_observe_read(rid, params):
    return _organization_observe_impl(rid, params, detail=True)


def _organization_observe_impl(rid, params, *, detail):
    from pathlib import Path
    from eidolon_cli.organization_observation import ObservationError, read_observation
    from eidolon_cli.profiles import get_active_profile_name
    from eidolon_constants import set_eidolon_home_override, reset_eidolon_home_override
    transport = current_transport()
    if transport is None or getattr(transport, 'closed', False):
        return _err(rid, 4001, 'Organization observation requires an active backend transport')
    if not isinstance(params, dict) or (set(params) != {'method', 'params'} if detail else bool(params)):
        return _err(rid, -32602, 'Unsupported organization observation parameters; served profile only')
    home = Path(_eidolon_home).resolve()
    path = home / 'organization' / 'state.db'
    try:
        if path.resolve() != path:
            return _err(rid, 4001, 'Organization observation is outside the served profile')
        token = set_eidolon_home_override(home)
        try:
            profile = get_active_profile_name()
            if detail:
                from eidolon_cli.organization_observation_reads import read_detail
                result = read_detail(home, params['method'], params['params'])
            else:
                result = read_observation(home)
        finally:
            reset_eidolon_home_override(token)
        return _ok(rid, {**result, 'scope': {'kind': 'served-profile', 'profile': profile}})
    except (ValueError, TypeError):
        return _err(rid, -32602, 'Invalid organization observation read')
    except ObservationError as exc:
        return _err(rid, 5073, str(exc), {'reason': exc.reason})
    except (OSError, RuntimeError):
        return _err(rid, 5073, 'Organization observation is unreadable', {'reason': 'unreadable'})


def register(server):
    bind_module(globals(), server)
