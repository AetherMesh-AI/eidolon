"""Executive-scoped manager plans inside one durable objective and budget.

The request owns its package identity. Model references can narrow work but never
create new authority. Existing objectives retain their original planning mode.
"""
from __future__ import annotations

import hashlib
import json

PACKAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS objective_planning (
 objective_id TEXT PRIMARY KEY REFERENCES objectives(id), version INTEGER NOT NULL,
 mode TEXT NOT NULL, max_tasks INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS manager_work_packages (
 id TEXT PRIMARY KEY, objective_id TEXT NOT NULL REFERENCES objectives(id), round INTEGER NOT NULL,
 manager_id TEXT NOT NULL REFERENCES agents(id), title TEXT NOT NULL, description TEXT NOT NULL,
 criterion_indexes TEXT NOT NULL, criteria_snapshot TEXT NOT NULL, project_ids TEXT NOT NULL, dependencies TEXT NOT NULL,
 max_tasks INTEGER NOT NULL, decomposition_request_id TEXT NOT NULL REFERENCES requests(id),
 plan_request_id TEXT NOT NULL UNIQUE REFERENCES requests(id), plan TEXT);
CREATE INDEX IF NOT EXISTS packages_objective_round ON manager_work_packages(objective_id,round);
CREATE TABLE IF NOT EXISTS task_work_packages (
 task_id TEXT PRIMARY KEY REFERENCES tasks(id), package_id TEXT NOT NULL REFERENCES manager_work_packages(id));
"""


def planning_mode(conn, objective_id):
    row = conn.execute('SELECT mode FROM objective_planning WHERE objective_id=?', (objective_id,)).fetchone()
    return row[0] if row else 'legacy'


def task_package_id(conn, task_id):
    row = conn.execute('SELECT package_id FROM task_work_packages WHERE task_id=?', (task_id,)).fetchone()
    return row[0] if row else None


def request_package(conn, request):
    if request['type'] == 'request.plan':
        return conn.execute('SELECT * FROM manager_work_packages WHERE plan_request_id=?', (request['id'],)).fetchone()
    identifier = task_package_id(conn, request['task_id']) if request['task_id'] else None
    return conn.execute('SELECT * FROM manager_work_packages WHERE id=?', (identifier,)).fetchone() if identifier else None


def package_tasks(conn, package_id):
    return conn.execute('SELECT t.* FROM tasks t JOIN task_work_packages p ON p.task_id=t.id '
                        'WHERE p.package_id=? ORDER BY t.rowid', (package_id,)).fetchall()


def package_view(conn, row):
    tasks = package_tasks(conn, row['id'])
    objective = conn.execute('SELECT o.cancelled,c.round FROM objectives o JOIN objective_control c ON c.objective_id=o.id WHERE o.id=?', (row['objective_id'],)).fetchone()
    request = conn.execute('SELECT status FROM requests WHERE id=?', (row['plan_request_id'],)).fetchone()
    current = objective['round'] == row['round']
    if row['plan'] is not None and tasks and all(t['status'] == 'completed' for t in tasks):
        status = 'completed'
    elif objective['cancelled'] or not current:
        status = 'cancelled'
    elif request['status'] in {'pending_intervention', 'waiting_response'} or any(t['status'] == 'blocked' for t in tasks):
        status = 'blocked'
    elif row['plan'] is None:
        status = 'planning'
    elif any(t['status'] != 'queued' for t in tasks):
        status = 'working'
    else:
        status = 'planned'
    return {'id': row['id'], 'objectiveId': row['objective_id'], 'round': row['round'],
            'managerId': row['manager_id'], 'title': row['title'], 'description': row['description'],
            'criterionIndexes': json.loads(row['criterion_indexes']), 'criteria': json.loads(row['criteria_snapshot']), 'projectIds': json.loads(row['project_ids']),
            'dependencyIds': json.loads(row['dependencies']), 'maxTasks': row['max_tasks'],
            'planRequestId': row['plan_request_id'], 'decompositionRequestId': row['decomposition_request_id'],
            'status': status, 'taskIds': [t['id'] for t in tasks], 'currentRound': current}


def packages_view(conn, objective_id):
    return [package_view(conn, row) for row in conn.execute(
        'SELECT * FROM manager_work_packages WHERE objective_id=? ORDER BY round,rowid', (objective_id,))]


def packages_complete(conn, objective_id):
    if planning_mode(conn, objective_id) == 'legacy':
        return True
    rows = [row for row in packages_view(conn, objective_id) if row['currentRound']]
    return bool(rows) and all(row['status'] == 'completed' for row in rows)


def package_dependency_requests(conn, task_id):
    """Include latent package edges in typed-request cycle detection."""
    identifier = task_package_id(conn, task_id)
    row = conn.execute('SELECT dependencies FROM manager_work_packages WHERE id=?', (identifier,)).fetchone() if identifier else None
    if row is None:
        return []
    requests = []
    for dependency in json.loads(row['dependencies']):
        requests.extend(item[0] for item in conn.execute(
            "SELECT r.id FROM requests r WHERE r.status NOT IN ('completed','cancelled') AND "
            '(r.id=(SELECT plan_request_id FROM manager_work_packages WHERE id=?) OR '
            'r.task_id IN (SELECT task_id FROM task_work_packages WHERE package_id=?))', (dependency, dependency)))
    return requests


def package_dependencies_ready(conn, request):
    package = request_package(conn, request)
    # Planning is independent; only actual work consumes upstream reviewed outputs.
    if package is None or request['type'] == 'request.plan':
        return True
    for identifier in json.loads(package['dependencies']):
        source = conn.execute('SELECT * FROM manager_work_packages WHERE id=?', (identifier,)).fetchone()
        if source is None or package_view(conn, source)['status'] != 'completed':
            return False
    return True


class OrganizationPackageStore:
    def _migrate_planning(self, conn):
        if conn.execute("SELECT 1 FROM objective_planning WHERE version!=1 OR mode NOT IN ('legacy','executive_packages')").fetchone():
            raise ValueError('Unsupported persisted objective planning version or mode')
        conn.execute("INSERT INTO objective_planning SELECT id,1,'legacy',? FROM objectives "
                     "WHERE id NOT IN (SELECT objective_id FROM objective_planning)", (self.settings.max_tasks,))

    def _initialize_planning(self, conn, objective_id):
        executive = self._objective_agent(conn, objective_id, 'Executive')
        mode = 'executive_packages' if 'request.decompose' in json.loads(executive['accepts']) else 'legacy'
        conn.execute('INSERT INTO objective_planning VALUES (?,1,?,?)', (objective_id, mode, self.settings.max_tasks))
        return mode

    def _queue_objective_planning(self, conn, objective_id, priority, *, payload=None, requester_id='owner'):
        hierarchical = planning_mode(conn, objective_id) == 'executive_packages'
        leader = self._objective_agent(conn, objective_id, 'Executive' if hierarchical else 'Manager')
        return self._request(conn, objective_id, 'request.decompose' if hierarchical else 'request.plan',
                             leader['team'], priority, payload=payload, requester_id=requester_id)

    def _finish_decompose(self, conn, request, result):
        from eidolon_cli.organization_store import _id
        from eidolon_cli.organization_executor import _parse_decomposition
        from eidolon_cli.organization_identity import _leader_enabled
        from eidolon_cli.organization_projects import objective_projects
        control = conn.execute('SELECT * FROM objective_control WHERE objective_id=?', (request['objective_id'],)).fetchone()
        policy = conn.execute('SELECT * FROM objective_planning WHERE objective_id=?', (request['objective_id'],)).fetchone()
        if (policy['mode'] != 'executive_packages' or json.loads(request['payload']).get('round', 0) != control['round']
                or request['agent_id'] != self._objective_agent(conn, request['objective_id'], 'Executive')['id']):
            raise ValueError('Decomposition requires the exact current executive and objective round')
        if conn.execute('SELECT 1 FROM manager_work_packages WHERE objective_id=? AND round=?',
                        (request['objective_id'], control['round'])).fetchone():
            raise ValueError('The complete package set is already persisted for this round')
        result = _parse_decomposition(result, {'maxTasks': min(policy['max_tasks'], self.settings.max_tasks)})
        specifications = result['workPackages']
        projects = {p['id'] for p in objective_projects(conn, request['objective_id'])}
        criteria = json.loads(control['criteria'])
        criterion_count = len(criteria)
        coverage, project_coverage = set(), set()
        ids = [_id('package') for _ in specifications]
        for index, item in enumerate(specifications):
            manager = conn.execute('SELECT * FROM agents WHERE id=?', (item['managerId'],)).fetchone()
            if (manager is None or manager['role'] != 'Manager' or not _leader_enabled(conn, manager)
                    or manager['manager_id'] != request['agent_id'] or 'request.plan' not in json.loads(manager['accepts'])):
                raise ValueError('Work packages require an enabled planning Manager reporting to the exact Executive')
            if any(i >= criterion_count for i in item['criterionIndexes']):
                raise ValueError('Work package criteria must reference exact root acceptance criteria')
            if not set(item['projectIds']).issubset(projects):
                raise ValueError('Work package projectIds must select owner-approved objective projects')
            coverage.update(item['criterionIndexes'])
            project_coverage.update(item['projectIds'])
            parent_payload = json.loads(request['payload'])
            inherited = {key: parent_payload[key] for key in ('feedback', 'evidenceIds', 'projectExecutionHistory') if key in parent_payload}
            plan_request = self._request(conn, request['objective_id'], 'request.plan', manager['team'], request['priority'],
                payload={**inherited, 'round': control['round'], 'workPackageId': ids[index]}, requester_id=request['agent_id'])
            conn.execute('INSERT INTO manager_work_packages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)',
                (ids[index], request['objective_id'], control['round'], manager['id'], item['title'], item['description'],
                 json.dumps(item['criterionIndexes']), json.dumps([criteria[i] for i in item['criterionIndexes']]), json.dumps(item['projectIds']),
                 json.dumps([ids[i] for i in item['dependsOn']]), item['maxTasks'], request['id'], plan_request))
        if coverage != set(range(criterion_count)) or project_coverage != projects:
            raise ValueError('Decomposition must cover every root criterion and selected project')
        self._merge_required_checks(conn, request['objective_id'], result.get('requiredChecks', []))
        self._event(conn, request['objective_id'], f'Executive delegated {len(ids)} durable manager work packages.', 'planning', request['agent_id'])

    def _validate_package_plan(self, conn, request, result):
        package = request_package(conn, request)
        if package is None:
            if planning_mode(conn, request['objective_id']) != 'legacy':
                raise ValueError('Hierarchical planning requires its exact persisted work package')
            return None
        control = conn.execute('SELECT round FROM objective_control WHERE objective_id=?', (request['objective_id'],)).fetchone()
        payload = json.loads(request['payload'])
        if payload.get('workPackageId') != package['id'] or type(payload.get('round')) is not int or payload['round'] != package['round']:
            raise ValueError('Plan request must retain its exact work package and round references')
        if package['round'] != control['round'] or package['manager_id'] != request['agent_id'] or package['plan'] is not None:
            raise ValueError('Work package plan is stale, already persisted or assigned to another Manager')
        refs = {'workPackageId': package['id'], 'managerId': package['manager_id'], 'round': package['round'],
                'projectIds': json.loads(package['project_ids']), 'criterionIndexes': json.loads(package['criterion_indexes'])}
        if any(key in result and json.dumps(result[key], sort_keys=True) != json.dumps(value, sort_keys=True) for key, value in refs.items()):
            raise ValueError('Plan references must match the exact request-pinned work package')
        tasks = result.get('tasks')
        if not isinstance(tasks, list) or not 1 <= len(tasks) <= package['max_tasks']:
            raise ValueError('Manager plan exceeds its work package task allocation')
        projects = set(json.loads(package['project_ids']))
        for task in tasks:
            if not isinstance(task, dict) or (task.get('projectId') is not None and
                    (not isinstance(task['projectId'], str) or task['projectId'] not in projects)):
                raise ValueError('Manager task projectId must stay inside its work package')
        policy = conn.execute('SELECT max_tasks FROM objective_planning WHERE objective_id=?', (request['objective_id'],)).fetchone()
        count = conn.execute('SELECT count(*) FROM objective_task_rounds WHERE objective_id=? AND round=?',
                             (request['objective_id'], control['round'])).fetchone()[0]
        if count + len(tasks) > min(policy['max_tasks'], self.settings.max_tasks):
            raise ValueError('Aggregate objective task capacity reached')
        return package

    def _package_context(self, conn, request):
        package = request_package(conn, request)
        return {'planningMode': planning_mode(conn, request['objective_id']),
                'workPackage': package_view(conn, package) if package else None,
                'workPackages': packages_view(conn, request['objective_id'])}

    def _package_dependency_evidence(self, conn, request):
        from eidolon_cli.organization_receipts import evidence_receipts
        from eidolon_cli.organization_edits import evidence_proposal
        package = request_package(conn, request)
        if package is None:
            return []
        result = []
        for identifier in json.loads(package['dependencies']):
            for task in package_tasks(conn, identifier):
                evidence = conn.execute('SELECT * FROM evidence WHERE task_id=? ORDER BY created DESC,id DESC LIMIT 1', (task['id'],)).fetchone()
                if task['status'] == 'completed':
                    review = conn.execute('SELECT * FROM reviews WHERE task_id=? AND approved=1 ORDER BY created DESC LIMIT 1', (task['id'],)).fetchone()
                    if (evidence is None or hashlib.sha256(evidence['content'].encode()).hexdigest() != evidence['sha256']
                            or review is None or evidence['id'] not in json.loads(review['evidence_ids'])
                            or review['agent_id'] == task['author_id']):
                        raise ValueError('Package dependency requires every exact independently reviewed artifact')
                    reviewed = conn.execute('SELECT payload FROM requests WHERE id=?', (review['request_id'],)).fetchone()
                    if json.loads(reviewed['payload']).get('evidenceHashes', {}).get(evidence['id']) != evidence['sha256']:
                        raise ValueError('Package dependency no longer matches its independent review hash')
                    self._verify_tool_evidence(conn, evidence['id'], require_read=task['type'] in {'work.inspect', 'work.edit'})
                    result.append({'evidenceId': evidence['id'], 'taskId': task['id'], 'workPackageId': identifier,
                        'summary': evidence['summary'], 'deliverable': evidence['content'], 'sha256': evidence['sha256'],
                        'toolReceipts': evidence_receipts(conn, evidence['id']), 'editProposal': evidence_proposal(conn, evidence['id'])})
        return result
