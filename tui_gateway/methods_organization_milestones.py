"""Explicit milestone planning over the authenticated endpoint's served home."""
from .method_ctx import HandlerRegistry

_registry = HandlerRegistry()


def _milestone_method(name, writing=False, receipt=False):
    @_registry.method(name)
    def handler(rid, params):
        from contextlib import closing
        from pathlib import Path
        import sqlite3
        from eidolon_cli.organization_metadata import read_descriptor
        from eidolon_cli.organization_milestones import MILESTONE_TABLES, milestone_projection
        from eidolon_cli.organization_projects import objective_projects
        from eidolon_constants import set_eidolon_home_override, reset_eidolon_home_override
        transport = current_transport()
        if transport is None or getattr(transport, 'closed', False):
            return _err(rid, 4001, 'Milestones require an active owner transport')
        allowed = {'expectedOrganizationId', 'expectedOwnerId'}
        if receipt:
            allowed.add('idempotencyKey')
        if writing:
            allowed |= {'milestoneId', 'title', 'projectIds', 'objectiveIds', 'dependencyIds',
                        'expectedRevision', 'idempotencyKey', 'confirmed'}
        if not isinstance(params, dict) or set(params) != allowed:
            return _err(rid, -32602, 'Unsupported milestone parameters; served profile only')
        try:
            home = Path(_eidolon_home).resolve()
            path = home / 'organization' / 'state.db'
            if path.resolve() != path:
                raise ValueError('Milestone storage is outside the served profile')
            metadata = read_descriptor(path).get('metadata') or {}
            if (not metadata or params['expectedOrganizationId'] != metadata.get('organizationId')
                    or params['expectedOwnerId'] != metadata.get('owner', {}).get('ownerId')):
                raise ValueError('Milestone organization or owner changed')
            token = set_eidolon_home_override(home)
            try:
                from eidolon_cli.profiles import get_active_profile_name
                result = {'version': 1, 'organizationId': metadata['organizationId'],
                          'ownerId': metadata['owner']['ownerId'], 'profile': get_active_profile_name()}
                if writing:
                    from eidolon_cli.organization_service import get_service
                    if params['confirmed'] is not True:
                        raise ValueError('Review and confirm this milestone revision')
                    service = get_service()
                    if service.store.path.resolve() != path:
                        raise ValueError('Milestone service is outside the served profile')
                    result['receipt'] = service.store.revise_milestone(params['milestoneId'], title=params['title'],
                        project_ids=params['projectIds'], objective_ids=params['objectiveIds'], dependency_ids=params['dependencyIds'],
                        expected_revision=params['expectedRevision'], idempotency_key=params['idempotencyKey'])
                else:
                    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=0.25)) as conn:
                        conn.row_factory = sqlite3.Row
                        conn.execute('PRAGMA query_only=ON'); conn.execute('BEGIN')
                        if receipt:
                            import json
                            key = params['idempotencyKey']
                            if not isinstance(key, str) or not key or len(key) > 128:
                                raise ValueError('Invalid milestone receipt key')
                            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                            if tables & MILESTONE_TABLES and not MILESTONE_TABLES <= tables:
                                raise ValueError('Milestone storage is incomplete')
                            row = conn.execute('SELECT result FROM organization_milestone_receipts WHERE idempotency_key=?', (key,)).fetchone() if 'organization_milestone_receipts' in tables else None
                            result['receipt'] = json.loads(row['result']) if row else None
                            return _ok(rid, result)
                        result['milestones'] = milestone_projection(conn)
                        rows = conn.execute('SELECT id,title FROM objectives ORDER BY created DESC LIMIT 1001').fetchall()
                        if len(rows) > 1000:
                            raise ValueError('Milestone objective selection exceeds this view capacity')
                        result['objectives'] = [{'id': row['id'], 'title': row['title'],
                            'projectIds': [project['id'] for project in objective_projects(conn, row['id'])]} for row in rows]
                return _ok(rid, result)
            finally:
                reset_eidolon_home_override(token)
        except ValueError as exc:
            return _err(rid, -32602, str(exc))
        except Exception:
            logger.exception('Milestone planning failed')
            return _err(rid, 5070, 'Milestone planning storage is unavailable')
    return handler


_milestone_method('organization.milestones')
_milestone_method('organization.reviseMilestone', True)
_milestone_method('organization.milestoneReceipt', receipt=True)


def register(server):
    _registry.install(server)
