"""Owner-selected project identities, pinned to existing roots and exact recipes.

Bindings restrict existing authority; they never add filesystem or tool grants.
The model may route work only among the projects selected for its objective.
"""
from dataclasses import asdict, dataclass
import hashlib
import json
import re


PROJECTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS objective_projects (
 objective_id TEXT NOT NULL REFERENCES objectives(id), project_id TEXT NOT NULL,
 binding TEXT NOT NULL, PRIMARY KEY(objective_id,project_id));
CREATE TABLE IF NOT EXISTS task_projects (
 task_id TEXT PRIMARY KEY REFERENCES tasks(id), objective_id TEXT NOT NULL,
 project_id TEXT NOT NULL,
 FOREIGN KEY(objective_id,project_id) REFERENCES objective_projects(objective_id,project_id));
"""
for _table in ('objective_projects', 'task_projects'):
    for _operation in ('UPDATE', 'DELETE'):
        PROJECTS_SCHEMA += (f'CREATE TRIGGER IF NOT EXISTS immutable_{_table}_{_operation.lower()} '
            f'BEFORE {_operation} ON {_table} BEGIN SELECT RAISE(ABORT, \'Immutable project binding\'); END;\n')


@dataclass(frozen=True)
class OrganizationProject:
    id: str
    root: str
    recipe: str
    team: str


def parse_projects(value, roots, grants):
    if not isinstance(value, list) or len(value) > 8:
        raise ValueError('organization.projects must list at most 8 explicit project bindings')
    projects, ids, aliases = [], set(), set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {'id', 'root', 'recipe', 'team'}:
            raise ValueError('Each project requires exactly id, root, recipe and team')
        identifier, root, recipe, team = (item[key] for key in ('id', 'root', 'recipe', 'team'))
        if not isinstance(identifier, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', identifier) or identifier in ids:
            raise ValueError('Project IDs must be unique canonical names')
        if not isinstance(root, str) or not re.fullmatch(r'root[0-7]', root) or int(root[4:]) >= len(roots) or root in aliases:
            raise ValueError('Projects must bind distinct explicitly configured root aliases')
        if not isinstance(recipe, str) or not any(grant.id == recipe and grant.execution['root'] == root for grant in grants):
            raise ValueError('A project recipe must be an existing exact grant for its root')
        if not isinstance(team, str) or not team.strip() or team != team.strip() or len(team) > 64:
            raise ValueError('A project team must be a nonempty bounded routing name')
        projects.append(OrganizationProject(identifier, root, recipe, team))
        ids.add(identifier)
        aliases.add(root)
    return tuple(projects)


def _binding(project, settings):
    grant = next((grant for grant in settings.project_grants if grant.id == project.recipe), None)
    if grant is None or grant.execution['root'] != project.root:
        raise ValueError('Project recipe is no longer explicitly configured for its root')
    index = int(project.root[4:])
    if index >= len(settings.read_roots):
        raise ValueError('Project root is no longer explicitly configured')
    return {**asdict(project), 'readRoot': settings.read_roots[index],
            'recipeSha256': hashlib.sha256(json.dumps(asdict(grant), sort_keys=True).encode()).hexdigest()}


def selected_projects(project_ids, settings):
    if project_ids is None:
        return []
    if (not isinstance(project_ids, list) or len(project_ids) > 8
            or any(not isinstance(value, str) for value in project_ids)
            or len(set(project_ids)) != len(project_ids)):
        raise ValueError('projectIds must select at most 8 unique configured project IDs')
    registry = {project.id: project for project in settings.projects}
    if any(identifier not in registry for identifier in project_ids):
        raise ValueError('projectIds must select only explicitly configured projects')
    return [_binding(registry[identifier], settings) for identifier in sorted(project_ids)]


def objective_projects(conn, objective_id):
    return [json.loads(row['binding']) for row in conn.execute(
        'SELECT binding FROM objective_projects WHERE objective_id=? ORDER BY project_id', (objective_id,))]


def task_project(conn, task_id):
    row = conn.execute('SELECT p.binding FROM task_projects t JOIN objective_projects p '
        'ON p.objective_id=t.objective_id AND p.project_id=t.project_id WHERE t.task_id=?', (task_id,)).fetchone()
    return json.loads(row['binding']) if row else None


def validate_objective_projects(conn, objective_id, settings):
    bindings = objective_projects(conn, objective_id)
    registry = {project.id: project for project in settings.projects}
    for binding in bindings:
        project = registry.get(binding['id'])
        if project is None or _binding(project, settings) != binding:
            raise ValueError('Objective project binding changed or was revoked; restore its exact owner-approved root, recipe and team before continuing')
    return bindings


def bind_task_project(conn, task_id, specification, settings):
    task = conn.execute('SELECT objective_id,type,team FROM tasks WHERE id=?', (task_id,)).fetchone()
    bindings = validate_objective_projects(conn, task['objective_id'], settings)
    identifier = specification.get('projectId')
    if identifier is None:
        if (bindings or settings.projects) and task['type'] in {'work.inspect', 'work.edit'}:
            raise ValueError('File work in a project-bound objective requires an explicit projectId')
        return
    binding = next((item for item in bindings if item['id'] == identifier), None)
    if binding is None:
        raise ValueError('Task projectId must select an owner-approved objective project')
    if task['team'] != binding['team']:
        raise ValueError('Task team must match its owner-approved project team')
    if task['type'] == 'work.edit':
        paths = specification.get('writePaths')
        if not isinstance(paths, list) or not paths or any(not isinstance(path, str) or path.split('/')[0] != binding['root'] for path in paths):
            raise ValueError('Project edits require exact writePaths confined to their bound project root')
    conn.execute('INSERT INTO task_projects VALUES (?,?,?)', (task_id, task['objective_id'], identifier))


def public_project(binding):
    """Expose identity and aliases, never captured host paths or private policy hashes."""
    return {key: binding[key] for key in ('id', 'root', 'recipe', 'team')}
