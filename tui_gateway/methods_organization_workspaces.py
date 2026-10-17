"""Fresh workspace RPCs use the authenticated endpoint's immutable launch home."""
from .method_ctx import HandlerRegistry

_registry = HandlerRegistry()


def _workspace_method(name, operation, *, read=False):
    @_registry.method('organization.' + name)
    def handler(rid, params):
        from pathlib import Path
        from eidolon_cli import organization_workspaces as workspaces
        from eidolon_cli.organization_metadata import MetadataError
        from eidolon_cli.profile_isolation import ProfileIsolationError
        transport = current_transport()
        if transport is None or getattr(transport, 'closed', False):
            return _err(rid, 4001, 'Workspace creation requires an active backend transport')
        if not isinstance(params, dict) or (read and params):
            return _err(rid, -32602, 'Unsupported workspace parameters; served profile only')
        try:
            home = Path(_eidolon_home).absolute()
            result = getattr(workspaces, operation)(home) if read else getattr(workspaces, operation)(home, params)
            return _ok(rid, result)
        except ValueError as exc:
            return _err(rid, -32602, str(exc))
        except (workspaces.WorkspaceError, MetadataError) as exc:
            return _err(rid, 5079, str(exc), {'reason': exc.reason})
        except (ProfileIsolationError, OSError, RuntimeError):
            return _err(rid, 5079, 'Workspace creation is unavailable', {'reason': 'unavailable'})
    return handler


_workspace_method('workspaceCreation', 'workspace_creation', read=True)
_workspace_method('createWorkspace', 'create_workspace')
_workspace_method('workspaceCreationStatus', 'workspace_creation_status')


def register(server):
    _registry.install(server)
