"""Read-only organization-local context for intake. No discovery or new access."""
from dataclasses import asdict
import hashlib
import json

from eidolon_cli.organization_identity import _leader_enabled
from eidolon_cli.organization_intake_contract import Context, Evidence, Route
from eidolon_cli.organization_projects import public_project, selected_projects


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def intake_context(store, conn, owner_priority=None, *, intake_id=None):
    store._require_current_policy(conn)
    rows = conn.execute('SELECT a.*,i.identity_id,i.purpose,i.responsibilities FROM agents a '
                        'JOIN agent_identity i ON i.agent_id=a.id ORDER BY a.id').fetchall()
    agents = {row['id']: row for row in rows}
    owner = agents.get('owner')
    if owner is None:
        raise ValueError('Organization owner identity is missing')
    projects = []
    for item in store.settings.projects:
        if item.root not in store.settings.revoked_root_aliases:
            projects.extend(selected_projects([item.id], store.settings))
    routes, descriptions = [], []
    for row in rows:
        if row['role'] != 'Manager' or not _leader_enabled(conn, row):
            continue
        if not {'request.plan', 'request.integrate'}.issubset(json.loads(row['accepts'])):
            continue
        executive = agents.get(row['manager_id'])
        if executive is None or executive['role'] != 'Executive' or not _leader_enabled(conn, executive):
            continue
        if 'request.accept' not in json.loads(executive['accepts']):
            continue
        permitted = tuple(p['id'] for p in projects if p['team'] == row['team'])
        route = Route(row['id'], executive['id'], row['id'], permitted)
        routes.append(route)
        descriptions.append({**asdict(route), 'managerName': row['name'], 'purpose': row['purpose'],
                             'responsibilities': json.loads(row['responsibilities']),
                             'executiveName': executive['name']})
    backlog = [dict(row) for row in conn.execute(
        'SELECT o.id,o.title,o.description,o.priority,c.status,a.manager_id,a.executive_id '
        'FROM objectives o JOIN objective_control c ON c.objective_id=o.id '
        'JOIN objective_assignments a ON a.objective_id=o.id '
        "LEFT JOIN objective_history h ON h.objective_id=o.id WHERE o.cancelled=0 AND "
        "coalesce(h.archived,0)=0 AND c.status NOT IN ('accepted','legacy_completed') ORDER BY o.id")]
    from eidolon_cli.organization_intake_duplicates import objective_scope
    for item in backlog:
        item['scopeSha256'] = digest(objective_scope(conn, item['id']))
    # Full bounded intake context, not a silent partial backlog. Provider boundary
    # checks actual prompt size before any reservation or physical send.
    payload = {'ownerIdentityId': owner['identity_id'], 'policyGeneration': store._policy_generation,
               'policyFingerprint': store._policy_fingerprint,
               'routes': descriptions, 'projects': projects, 'backlog': backlog,
               'ownerPriority': owner_priority}
    from eidolon_cli.organization_intake_evidence import collect_intake_evidence
    observations = collect_intake_evidence(store, conn)
    payload['retainedEvidenceBinding'] = observations['private']['sha256']
    from eidolon_cli.organization_intake_inspection_store import inspection_projection
    fresh, fresh_binding = inspection_projection(store, conn, intake_id)
    payload['freshInspectionBinding'] = fresh_binding
    snapshot_id = digest(payload)
    retained = tuple(Evidence(item['id'], route_ids=tuple(route.id for route in routes
                     if item['projectId'] in route.project_ids)) for item in observations['public']['items'])
    retained += tuple(Evidence(item['id'], route_ids=tuple(route.id for route in routes
                      if item['projectId'] in route.project_ids)) for item in fresh['items'])
    context = Context(snapshot_id, tuple(routes), (Evidence('owner-request'), *retained),
                      tuple(item['id'] for item in backlog), owner_priority)
    # Full private bindings contribute to stale-context detection but never enter
    # model prompts. Only the same public aliases used by ordinary project work
    # cross the inference boundary.
    public = {key: value for key, value in payload.items() if key != 'policyFingerprint'}
    public['projects'] = [public_project(project) for project in projects]
    public['retainedEvidence'] = observations['public']
    public['freshInspection'] = fresh
    return context, {'snapshotId': snapshot_id, **public}
