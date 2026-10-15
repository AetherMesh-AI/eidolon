"""Served-owner folder review and atomic explicit access registration."""
from contextlib import ExitStack
from dataclasses import replace
import json
from pathlib import Path
import time

from eidolon_cli.organization_owner_policy import (
    OwnerPolicyError, _connection, _hash, _identity, _schema, _source, _writer, _refresh_existing,
)
from eidolon_cli.organization_metadata import _uuid, _text
from eidolon_cli.organization_project_setup import _blocked
from eidolon_cli.organization_root_grants import (
    ROOT_GRANT_SCHEMA, check_new_root, roots_managed, source_roots,
)


def _available(conn, settings):
    if roots_managed():
        raise OwnerPolicyError('managed_configuration')
    if _blocked(conn):
        raise OwnerPolicyError('active_objectives')
    if settings.project_registry_conflicts:
        raise OwnerPolicyError('registry_conflict')
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='organization_team_generations'").fetchone():
        if conn.execute("SELECT 1 FROM organization_team_generations WHERE status IN ('running','uncertain') LIMIT 1").fetchone():
            raise OwnerPolicyError('team_generation_in_progress_or_uncertain')


def _view(conn, home, identity, policy):
    settings, revision, _ = _source(home, conn)
    blockers = []
    try:
        _available(conn, settings)
    except OwnerPolicyError as exc:
        blockers.append(exc.reason)
    if len(settings.read_roots) >= 8:
        blockers.append('root_capacity')
    return {'capabilityVersion': 1, 'organizationId': identity['organizationId'],
            'ownerId': identity['owner']['ownerId'], 'policyGeneration': policy['generation'],
            'sourceRevision': revision, 'blockers': blockers,
            'rootAliases': [f'root{i}' for i in range(len(settings.read_roots))],
            'teams': sorted({settings.team, *(staff.team for staff in settings.roster or ())}),
            'inspectionEnabled': 'read_file' in settings.tool_grants and 'work.inspect' in settings.capabilities}, settings


def folder_setup(home, expected_organization_id):
    _uuid(expected_organization_id, 'expectedOrganizationId')
    with _connection(home) as conn:
        identity = _identity(conn, expected_organization_id)
        result, _ = _view(conn, home, identity, _schema(conn))
        return result


def _preview(conn, home, params, identity, policy):
    view, settings = _view(conn, home, identity, policy)
    if type(params.get('expectedGeneration')) is not int or params['expectedGeneration'] != policy['generation']:
        raise OwnerPolicyError('generation_conflict')
    if params.get('sourceRevision') != view['sourceRevision']:
        raise OwnerPolicyError('source_conflict')
    _available(conn, settings)
    folder = params.get('folder')
    if not isinstance(folder, dict) or set(folder) != {'path', 'projectId', 'team'}:
        raise ValueError('Folder requires exactly path, projectId and team')
    identity_on_disk = check_new_root(folder['path'], settings)
    project = {'id': folder['projectId'], 'root': f'root{len(settings.read_roots)}',
               'recipe': None, 'team': folder['team']}
    from eidolon_cli.organization_projects import parse_projects
    from dataclasses import asdict
    parse_projects([*(asdict(p) for p in settings.projects), project],
                   (*settings.read_roots, folder['path']), settings.project_grants)
    if project['team'] not in view['teams']:
        raise ValueError('Select an existing organization team')
    candidate = {'folder': dict(folder), 'project': project, 'directoryIdentity': identity_on_disk}
    return {**view, 'candidate': candidate,
            'previewVersion': _hash([identity['organizationId'], identity['owner']['ownerId'],
                                     policy['generation'], view['sourceRevision'], candidate]),
            'accessDescription': 'Approves this folder as a bounded organization root. Existing tool and staff grants still apply. No edits, tests, execution recipe or background work is enabled.'}, settings


def preview_folder(home, params):
    _uuid(params.get('expectedOrganizationId'), 'expectedOrganizationId')
    with _connection(home) as conn:
        identity = _identity(conn, params['expectedOrganizationId'])
        result, _ = _preview(conn, home, params, identity, _schema(conn))
        return result


def register_folder(home, params):
    if params.get('confirmed') is not True:
        raise ValueError('Review and explicitly confirm folder access before registration')
    _uuid(params.get('expectedOrganizationId'), 'expectedOrganizationId')
    key = _text(params.get('idempotencyKey'), 'idempotencyKey', 128)
    digest = _hash(params)
    with _connection(home, write=True) as conn, ExitStack() as locks:
        identity = _identity(conn, params['expectedOrganizationId'])
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='organization_folder_receipts'").fetchone():
            old = conn.execute('SELECT * FROM organization_folder_receipts WHERE idempotency_key=?', (key,)).fetchone()
            if old:
                if old['organization_id'] != identity['organizationId'] or old['owner_id'] != identity['owner']['ownerId']:
                    raise OwnerPolicyError('identity_conflict')
                if old['input_hash'] != digest:
                    raise OwnerPolicyError('idempotency_conflict')
                result = json.loads(old['result_json'])
                expected = {'idempotencyKey': key, 'organizationId': identity['organizationId'],
                            'ownerId': identity['owner']['ownerId'],
                            'policyGeneration': params.get('expectedGeneration', 0) + 1,
                            'sourceRevision': params.get('sourceRevision'),
                            'previewVersion': params.get('previewVersion'), 'candidate': params.get('candidate')}
                if result != {'capabilityVersion': 1, 'receipt': expected}:
                    raise OwnerPolicyError('receipt_conflict')
                return result
        policy = _schema(conn)
        candidate = params.get('candidate')
        if not isinstance(candidate, dict):
            raise ValueError('Registration requires the exact reviewed candidate')
        preview, settings = _preview(conn, home, {**params, 'folder': candidate.get('folder')}, identity, policy)
        if candidate != preview['candidate'] or params.get('previewVersion') != preview['previewVersion']:
            raise OwnerPolicyError('preview_conflict')
        from eidolon_cli.organization_policy import persisted_settings
        if persisted_settings(conn) != settings:
            raise OwnerPolicyError('source_policy_conflict')
        from eidolon_cli.organization_service import _ExecutionLock
        for index in range(4):
            if not locks.enter_context(_ExecutionLock(Path(home) / 'organization' / 'capacity-locks', str(index))):
                raise OwnerPolicyError('execution_still_stopping')
        # No executescript: that would commit this transaction implicitly.
        for statement in ROOT_GRANT_SCHEMA.split(';\n'):
            if statement.strip():
                conn.execute(statement)
        store = _writer(conn, home, settings, policy)
        store._require_current_policy(conn)
        project = candidate['project']
        disk = check_new_root(candidate['folder']['path'], settings)
        if disk != candidate['directoryIdentity'] or roots_managed():
            raise OwnerPolicyError('preview_conflict')
        conn.execute('INSERT INTO organization_root_grants VALUES (?,?,?,?,?)',
                     (project['root'], candidate['folder']['path'], json.dumps(source_roots(settings)), disk['device'], disk['inode']))
        expanded = replace(settings, read_roots=(*settings.read_roots, candidate['folder']['path']))
        from eidolon_cli.organization_projects import parse_projects
        from eidolon_cli.organization_project_registry import binding_hash
        parsed = parse_projects([project], expanded.read_roots, expanded.project_grants)[0]
        conn.execute('INSERT INTO organization_project_registry VALUES (?,?,?,?)',
                     (project['id'], json.dumps(project, sort_keys=True), binding_hash(parsed, expanded), time.time()))
        store._reload_configuration(conn, settings)
        if store.settings.project_registry_conflicts:
            raise OwnerPolicyError('registry_conflict')
        receipt = {'idempotencyKey': key, 'organizationId': identity['organizationId'],
                   'ownerId': identity['owner']['ownerId'], 'policyGeneration': store._policy_generation,
                   'sourceRevision': params['sourceRevision'], 'previewVersion': params['previewVersion'], 'candidate': candidate}
        result = {'capabilityVersion': 1, 'receipt': receipt}
        conn.execute('INSERT INTO organization_folder_receipts VALUES (?,?,?,?,?)',
                     (key, identity['organizationId'], identity['owner']['ownerId'], digest, json.dumps(result, sort_keys=True)))
    try:
        _refresh_existing(home)
    except Exception:
        pass  # The durable receipt establishes commit; refresh never replays it.
    return result
