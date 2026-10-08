"""Durable execution and review gates for exact owner-authorized project snapshots.

The runner and Git integrator are backend operations, never model tool handlers.
Starts commit before dispatch. An interrupted operation remains unknown rather
than replaying silently; retained outcomes are evidence, not owner authority.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import re
import time
import uuid

from eidolon_cli.organization_project_validation import canonical, digest


PROJECT_EXECUTION_SCHEMA = """
CREATE TABLE IF NOT EXISTS project_execution_budgets (
 objective_id TEXT PRIMARY KEY REFERENCES objectives(id), max_runs INTEGER NOT NULL CHECK(max_runs>0));
CREATE TABLE IF NOT EXISTS project_run_starts (
 id TEXT PRIMARY KEY, request_id TEXT NOT NULL REFERENCES requests(id), token TEXT NOT NULL,
 objective_id TEXT NOT NULL REFERENCES objectives(id), round INTEGER NOT NULL,
 snapshot TEXT NOT NULL, snapshot_sha256 TEXT NOT NULL, grant_record TEXT NOT NULL,
 source_base TEXT, policy_generation INTEGER NOT NULL, created REAL NOT NULL,
 UNIQUE(request_id,token));
CREATE TABLE IF NOT EXISTS project_run_results (
 run_id TEXT PRIMARY KEY REFERENCES project_run_starts(id),
 record TEXT NOT NULL, sha256 TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS project_run_reviews (
 request_id TEXT PRIMARY KEY REFERENCES requests(id), run_id TEXT NOT NULL REFERENCES project_run_results(run_id),
 result_sha256 TEXT NOT NULL, reviewer_id TEXT NOT NULL REFERENCES agents(id),
 approved INTEGER NOT NULL CHECK(approved IN (0,1)), summary TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS project_source_receipts (
 request_id TEXT PRIMARY KEY REFERENCES requests(id), run_id TEXT NOT NULL REFERENCES project_run_results(run_id),
 record TEXT NOT NULL, sha256 TEXT NOT NULL, created REAL NOT NULL);
"""
for _table in ('project_execution_budgets', 'project_run_starts', 'project_run_results', 'project_run_reviews', 'project_source_receipts'):
    for _operation in ('UPDATE', 'DELETE'):
        PROJECT_EXECUTION_SCHEMA += (f'CREATE TRIGGER IF NOT EXISTS immutable_{_table}_{_operation.lower()} '
            f'BEFORE {_operation} ON {_table} BEGIN SELECT RAISE(ABORT, \'Immutable project execution record\'); END;\n')


def _runner_files(snapshot):
    return [{key: item[key] for key in ('path', 'content', 'sha256', 'revision')} for item in snapshot]


def _run_row(conn, identifier):
    return conn.execute('SELECT s.*,r.record,r.sha256 AS result_sha256,r.created AS finished '
        'FROM project_run_starts s LEFT JOIN project_run_results r ON r.run_id=s.id WHERE s.id=?',
        (identifier,)).fetchone()


def _latest_run(conn, objective_id, project_id=None):
    return conn.execute('SELECT s.id FROM project_run_starts s JOIN objective_control c '
        'ON c.objective_id=s.objective_id AND c.round=s.round WHERE s.objective_id=? '
        "AND json_extract((SELECT payload FROM requests WHERE id=s.request_id),'$.projectId') IS ? "
        'ORDER BY s.created DESC,s.id DESC LIMIT 1', (objective_id, project_id)).fetchone()


def _run_project_id(conn, row):
    request = conn.execute('SELECT objective_id,payload FROM requests WHERE id=?', (row['request_id'],)).fetchone()
    if request is None or request['objective_id'] != row['objective_id']:
        raise ValueError('Project run request does not match its objective')
    return json.loads(request['payload']).get('projectId')


def project_execution_artifact(conn, identifier):
    if isinstance(identifier, str) and identifier.startswith('project_source_'):
        return source_integration_artifact(conn, identifier)
    row = _run_row(conn, identifier)
    if row is None or row['record'] is None:
        return None
    snapshot, record = json.loads(row['snapshot']), json.loads(row['record'])
    if digest(snapshot) != row['snapshot_sha256'] or digest(record) != row['result_sha256']:
        raise ValueError('Project execution evidence is missing or changed')
    # Exact source and test bytes accompany the actual outcome for independent
    # review. Hashes alone are not substitutes for reviewing the tested code.
    body = canonical({'snapshot': snapshot, 'execution': record,
                      'sourceBase': json.loads(row['source_base']) if row['source_base'] else None,
                      'grant': json.loads(row['grant_record']),
                      **({'projectId': _run_project_id(conn, row)} if _run_project_id(conn, row) is not None else {})})
    return {'id': row['id'], 'objectiveId': row['objective_id'], 'taskId': None,
            'kind': 'project_execution', 'content': body, 'sha256': hashlib.sha256(body.encode()).hexdigest(),
            'summary': f"Project test execution: {record['status']}. Exact snapshot and bounded runtime receipt retained.",
            'toolReceipts': [], 'createdAt': datetime.fromtimestamp(row['finished'], timezone.utc).isoformat(), 'projectExecution': record}


def source_integration_artifact(conn, identifier):
    if not isinstance(identifier, str) or not identifier.startswith('project_source_'):
        return None
    row = conn.execute('SELECT r.*,s.objective_id FROM project_source_receipts r '
                       'JOIN project_run_starts s ON s.id=r.run_id WHERE r.request_id=?',
                       (identifier.removeprefix('project_source_'),)).fetchone()
    if row is None:
        return None
    receipt = json.loads(row['record'])
    if digest(receipt) != row['sha256']:
        raise ValueError('Source integration receipt is missing or changed')
    return {'id': identifier, 'objectiveId': row['objective_id'], 'taskId': None,
            'kind': 'source_integration', 'content': row['record'], 'sha256': row['sha256'],
            'summary': 'Reviewed and tested source integrated into a new Git branch; the original working tree and index were not changed.',
            'toolReceipts': [], 'createdAt': datetime.fromtimestamp(row['created'], timezone.utc).isoformat()}


def replan_project_history(conn, objective_id, round_number):
    """Bind bounded prior attempts to their actual artifacts, never inferred outcomes.

    Keep earlier rounds too: an amendment before another run must not erase the
    failure being repaired. Current-run verification deliberately stays separate.
    """
    history = []
    for start in conn.execute('SELECT id FROM project_run_starts WHERE objective_id=? AND round<=? '
                              'ORDER BY round,created,id', (objective_id, round_number)):
        row = _run_row(conn, start['id'])
        artifact = project_execution_artifact(conn, row['id'])
        review = conn.execute('SELECT * FROM project_run_reviews WHERE run_id=? ORDER BY created DESC LIMIT 1',
                              (row['id'],)).fetchone()
        evidence_ids = [row['id']] if artifact else []
        evidence_ids.extend('project_source_' + source['request_id'] for source in conn.execute(
            'SELECT request_id FROM project_source_receipts WHERE run_id=? ORDER BY created,request_id', (row['id'],)))
        history.append({'runId': row['id'], 'requestId': row['request_id'],
                        'projectId': _run_project_id(conn, row), 'round': row['round'],
                        'status': artifact['projectExecution']['status'] if artifact else 'unknown',
                        'snapshotSha256': row['snapshot_sha256'], 'evidenceIds': evidence_ids,
                        'review': {'approved': bool(review['approved']), 'reviewerId': review['reviewer_id'],
                                   'summary': review['summary'], 'requestId': review['request_id']} if review else None})
    return history


def project_execution_view(conn, objective_id, *, full=False, project_id=None):
    from eidolon_cli.organization_projects import objective_projects
    projects = objective_projects(conn, objective_id)
    if projects and project_id is None:
        return {'projects': [dict(projectId=project['id'], execution=project_execution_view(
            conn, objective_id, full=full, project_id=project['id'])) for project in projects]}
    latest = _latest_run(conn, objective_id, project_id)
    if latest is None:
        return None
    row = _run_row(conn, latest['id'])
    artifact = project_execution_artifact(conn, row['id'])
    review = conn.execute('SELECT * FROM project_run_reviews WHERE run_id=? ORDER BY created DESC LIMIT 1',
                          (row['id'],)).fetchone()
    source = conn.execute('SELECT * FROM project_source_receipts WHERE run_id=? ORDER BY created DESC LIMIT 1',
                          (row['id'],)).fetchone()
    integration = None
    if source:
        integration = json.loads(source['record'])
        if digest(integration) != source['sha256']:
            raise ValueError('Source integration receipt is changed')
        integration['evidenceId'] = 'project_source_' + source['request_id']
    receipt = artifact['projectExecution'] if artifact else {'status': 'unknown'}
    return {'id': row['id'], 'requestId': row['request_id'], 'round': row['round'],
            'snapshotSha256': row['snapshot_sha256'], 'status': receipt['status'],
            'receipt': receipt if full else {key: receipt.get(key) for key in (
                'status', 'exitCode', 'testCount', 'durationSeconds', 'command', 'reason', 'isolation')},
            'review': {'approved': bool(review['approved']), 'reviewerId': review['reviewer_id'],
                       'summary': review['summary'], 'requestId': review['request_id']} if review else None,
            'sourceIntegration': integration,
            'files': [{key: item[key] for key in ('path', 'sha256', 'revision')} for item in json.loads(row['snapshot'])]}


class OrganizationProjectExecutionStore:
    def _migrate_project_execution_budgets(self, conn):
        conn.execute('INSERT OR IGNORE INTO project_execution_budgets SELECT id,? FROM objectives',
                     (self.settings.max_project_runs,))

    @staticmethod
    def _wants_source(conn, objective_id):
        row = conn.execute('SELECT delivery_mode,required_checks FROM objective_control WHERE objective_id=?',
                           (objective_id,)).fetchone()
        return row['delivery_mode'] == 'source_project' or 'source_integration' in json.loads(row['required_checks'])

    def _automatic_source(self, conn, objective_id):
        return 'integrate_source' in self.settings.tool_grants and self._wants_source(conn, objective_id)

    def _execution_required(self, conn, objective_id):
        checks = json.loads(conn.execute('SELECT required_checks FROM objective_control WHERE objective_id=?',
                                        (objective_id,)).fetchone()[0])
        return 'project_tests' in checks or self._automatic_source(conn, objective_id)

    def _execution_projects(self, conn, objective_id):
        from eidolon_cli.organization_projects import validate_objective_projects
        return validate_objective_projects(conn, objective_id, self.settings)

    def verify_project_coverage(self, conn, objective_id):
        from eidolon_cli.organization_acceptance import current_tasks
        from eidolon_cli.organization_projects import task_project
        required = {project['id'] for project in self._execution_projects(conn, objective_id)}
        if not required:
            return
        tasks = [task['id'] for task in current_tasks(conn, objective_id)
                 if task['type'] in {'work.inspect', 'work.edit'} and task['status'] == 'completed']
        # Retained, independently reviewed applied edits still contribute to the
        # objective after a replan. Their full lineage is verified by acceptance.
        tasks.extend(row['task_id'] for row in conn.execute(
            'SELECT DISTINCT p.task_id FROM edit_proposals p JOIN edit_applications a '
            'ON a.proposal_id=p.id JOIN tasks t ON t.id=p.task_id '
            "WHERE p.objective_id=? AND t.status='completed'", (objective_id,)))
        covered = {(task_project(conn, task_id) or {}).get('id') for task_id in tasks}
        if required - covered:
            raise ValueError('Every selected project requires independently reviewed file work before objective integration')

    def _execution_project(self, conn, objective_id, project_id):
        projects = self._execution_projects(conn, objective_id)
        if not projects and project_id is None:
            return None
        for project in projects:
            if project['id'] == project_id:
                return project
        raise ValueError('Project stage requires an exact owner-bound projectId')

    def _project_grant(self, conn, objective_id, project_id=None):
        from eidolon_cli.organization_acceptance import current_tasks
        project = self._execution_project(conn, objective_id, project_id)
        from eidolon_cli.organization_projects import task_project
        tasks = [task for task in current_tasks(conn, objective_id)
                 if project is None or (task_project(conn, task['id']) or {}).get('id') == project_id]
        authors = {task['author_id']: task['type'] for task in tasks if task['type'] in {'work.inspect', 'work.edit'}}
        # Earlier same-path changes remain in later revisions even when the
        # latest proposal has a different author. Conservatively retain every
        # applied contributor's grant requirement across objective rounds.
        for item in conn.execute('SELECT DISTINCT p.author_id,p.task_id FROM edit_proposals p JOIN edit_applications a '
                                 'ON a.proposal_id=p.id WHERE p.objective_id=?', (objective_id,)):
            if project is None or (task_project(conn, item['task_id']) or {}).get('id') == project_id:
                authors[item['author_id']] = 'work.edit'
        if not authors:
            raise ValueError('Project execution requires an independently reviewed inspection or edit by an explicitly granted worker')
        required = {'read_file', 'run_tests'}
        if self._automatic_source(conn, objective_id):
            required.add('integrate_source')
        if not required.issubset(self.settings.tool_grants):
            raise ValueError('Project execution needs explicit organization read_file and run_tests grants')
        for author in authors:
            staff = self._staff(author)
            if (staff is None or not staff.enabled or not required.issubset(staff.tool_grants)
                    or self._staff_reason(conn, {'id': author}, authors[author])):
                raise ValueError('Every project author requires explicit current read_file/run_tests and, for source integration, integrate_source grants')
        if project is not None:
            grants = [grant for grant in self.settings.project_grants
                      if grant.id == project['recipe'] and grant.execution['root'] == project['root']]
            if len(grants) != 1:
                raise ValueError('Bound project requires its exact current owner recipe')
            return grants[0]
        roots = {row['path'].split('/')[0] for row in conn.execute(
            'SELECT path FROM workspace_heads WHERE objective_id=?', (objective_id,))}
        grants = [grant for grant in self.settings.project_grants
                  if not roots or roots == {grant.execution['root']}]
        if len(grants) != 1:
            raise ValueError('Configure exactly one matching bounded project_grants recipe for this objective root; no command or file set is inferred')
        return grants[0]

    def _project_snapshot(self, conn, objective_id, grant):
        from tools.organization_file_read import organization_file_read_scope
        from eidolon_cli.organization_edits import _revision, _verify_revision
        from eidolon_cli.organization_project_workspace import final_source_manifest
        applied = conn.execute('SELECT 1 FROM edit_applications a JOIN edit_proposals p ON p.id=a.proposal_id '
                               'WHERE p.objective_id=?', (objective_id,)).fetchone()
        managed = {item['path']: item for item in final_source_manifest(conn, objective_id)[0]} if applied else {}
        if self._execution_projects(conn, objective_id):
            managed = {path: item for path, item in managed.items()
                       if path.split('/')[0] == grant.execution['root']}
        source_verified = False
        if managed and not self._automatic_source(conn, objective_id):
            manifest, _, _, checked = final_source_manifest(conn, objective_id)
            source_verified = self._verified_final_source(conn, objective_id, manifest, checked)
        if set(managed) - set(grant.files):
            raise ValueError('The exact project recipe must include every reviewed changed file before tests or source integration')
        snapshot, total = [], 0
        with organization_file_read_scope(self.settings.read_roots, self.settings.max_tool_result_chars) as scope:
            for path in grant.files:
                baseline = _verify_revision(_revision(conn, objective_id, path, 0)) if path in managed else None
                created = baseline is not None and not baseline['source_exists']
                source = scope.read_exact_source(path, allow_missing=created and not source_verified)
                content, sha256, revision = source['content'], source['sha256'], 0
                base_sha256 = source['sha256']
                if path in managed:
                    current = managed[path]
                    expected_source = current['sha256'] if source_verified else baseline['sha256']
                    expected_exists = source_verified or not created
                    if source.get('exists', True) != expected_exists or source['sha256'] != expected_source:
                        raise ValueError('Source preimage changed after inspection; replan against current source before execution')
                    base_sha256 = None if created else baseline['sha256']
                    content, sha256, revision = current['content'], current['sha256'], current['revision']
                    self._verify_root(conn, objective_id, path)
                total += len(content.encode('utf-8'))
                snapshot.append({'path': path, 'content': content, 'sha256': sha256, 'revision': revision,
                                 'base_sha256': base_sha256, **({'operation': 'create'} if created else {})})
        if total > 524288:
            raise ValueError('Project execution snapshot exceeds its 512 KiB total byte limit')
        return snapshot

    def _verify_current_snapshot(self, conn, row):
        grant = self._project_grant(conn, row['objective_id'], _run_project_id(conn, row))
        if canonical(asdict(grant)) != row['grant_record']:
            raise ValueError('Project execution recipe or file grant changed; run a newly reviewed snapshot')
        if digest(self._project_snapshot(conn, row['objective_id'], grant)) != row['snapshot_sha256']:
            raise ValueError('The tested snapshot no longer matches current reviewed revisions or source preimages')
        control = conn.execute('SELECT round FROM objective_control WHERE objective_id=?', (row['objective_id'],)).fetchone()
        if row['round'] != control['round']:
            raise ValueError('Project execution belongs to a superseded objective round')

    def _verify_project_run(self, conn, identifier, *, review=True):
        row = _run_row(conn, identifier)
        if row is None or row['record'] is None:
            raise ValueError('Project execution is unconfirmed; inspect the retained start before any retry')
        latest = _latest_run(conn, row['objective_id'], _run_project_id(conn, row))
        if latest is None or latest['id'] != identifier:
            raise ValueError('Project execution is not the latest current project run')
        artifact = project_execution_artifact(conn, identifier)
        result = artifact['projectExecution']
        from eidolon_cli.organization_project_runner import snapshot_digest
        isolation, runtime = result.get('isolation', {}), result.get('runtime', {})
        if (result.get('runner') != 'eidolon.isolated-python-unittest' or result.get('runnerVersion') != 1
                or not isinstance(isolation, dict) or isolation.get('established') is not True
                or isolation.get('backend') != 'linux-bubblewrap-seccomp'
                or isolation.get('sourceReadOnly') is not True or isolation.get('runtimeReadOnly') is not True
                or isolation.get('network') != 'none' or isolation.get('processLimit') != 1
                or isolation.get('seccompInstalled') is not True or isolation.get('namespaceCreationDenied') is not True
                or not isinstance(runtime, dict) or runtime.get('stdlibOnly') is not True
                or any(not isinstance(runtime.get(key), str) or not re.fullmatch(r'[0-9a-f]{64}', runtime[key])
                       for key in ('executableSha256', 'runtimeSha256'))
                or result.get('status') != 'passed' or result.get('exitCode') != 0
                or type(result.get('testCount')) is not int or result['testCount'] < 1
                or result.get('snapshotSha256') != snapshot_digest(_runner_files(json.loads(row['snapshot'])))):
            raise ValueError('Required project tests need a real successful nonempty run over the exact snapshot')
        self._verify_current_snapshot(conn, row)
        if review:
            proof = conn.execute('SELECT v.*,q.type,q.status,q.payload FROM project_run_reviews v '
                'JOIN requests q ON q.id=v.request_id WHERE v.run_id=? ORDER BY v.created DESC LIMIT 1', (identifier,)).fetchone()
            authors = {item[0] for item in conn.execute('SELECT DISTINCT author_id FROM tasks WHERE objective_id=?',
                                                       (row['objective_id'],))}
            if (proof is None or not proof['approved'] or proof['type'] != 'request.test_review'
                    or proof['status'] != 'completed' or proof['reviewer_id'] in authors
                    or json.loads(proof['payload']).get('projectId') != _run_project_id(conn, row)
                    or json.loads(proof['payload']).get('runId') != identifier
                    or proof['result_sha256'] != artifact['sha256']
                    or json.loads(proof['payload']).get('evidenceHashes') != {identifier: artifact['sha256']}):
                raise ValueError('Project test acceptance requires a distinct reviewer of the exact retained run and snapshot')
        return row, artifact

    def ensure_project_execution(self, conn, objective_id):
        if not self._execution_required(conn, objective_id):
            return False
        projects = self._execution_projects(conn, objective_id) or [None]
        objective = conn.execute('SELECT priority FROM objectives WHERE id=?', (objective_id,)).fetchone()
        control = conn.execute('SELECT round FROM objective_control WHERE objective_id=?', (objective_id,)).fetchone()
        queued = False
        for project in projects:
            project_id = project['id'] if project else None
            latest = _latest_run(conn, objective_id, project_id)
            if latest:
                self._verify_project_run(conn, latest['id'])
                continue
            self._request(conn, objective_id, 'request.project_test', self.settings.team,
                          objective['priority'], payload={'round': control['round'], **({'projectId': project_id} if project else {})})
            queued = True
        return queued

    def project_stage_unavailability(self, conn, request):
        try:
            self._project_grant(conn, request['objective_id'], json.loads(request['payload']).get('projectId'))
            if request['type'] == 'request.project_test':
                if self._reusable_project_run(conn, request) is not None:
                    return None
                ceiling = conn.execute('SELECT max_runs FROM project_execution_budgets WHERE objective_id=?',
                                       (request['objective_id'],)).fetchone()
                if ceiling is None:
                    raise ValueError('Project execution budget is missing; restore the objective budget before running')
                if conn.execute('SELECT count(*) FROM project_run_starts WHERE objective_id=?',
                                (request['objective_id'],)).fetchone()[0] >= min(ceiling['max_runs'], self.settings.max_project_runs):
                    raise ValueError('Project execution run budget exhausted; retained failed and interrupted runs count toward the limit')
                if conn.execute('SELECT 1 FROM project_run_starts s LEFT JOIN project_run_results r ON r.run_id=s.id '
                                'WHERE s.request_id=? AND r.run_id IS NULL', (request['id'],)).fetchone():
                    raise ValueError('An earlier project run has an unknown outcome; replan after reviewing its retained start, not an automatic replay')
        except ValueError as error:
            return str(error)
        return None

    def _reusable_project_run(self, conn, request):
        previous = conn.execute('SELECT s.id,r.record FROM project_run_starts s LEFT JOIN project_run_results r '
                                'ON r.run_id=s.id WHERE s.request_id=? ORDER BY s.created DESC LIMIT 1',
                                (request['id'],)).fetchone()
        if previous is not None and previous['record'] is not None and json.loads(previous['record']).get('status') == 'passed':
            row, _ = self._verify_project_run(conn, previous['id'], review=False)
            return row
        return None

    @staticmethod
    def _require_project_execution_controller(request):
        if request['agent_id'] != 'control:project' or request['type'] not in {'request.project_test', 'request.source_integrate'}:
            raise ValueError('Only the backend project controller may execute tests or integrate source')

    def run_project_stage(self, claim, cancel):
        if claim['type'] == 'request.source_integrate':
            return self._run_source_stage(claim, cancel)
        from eidolon_cli.organization_project_runner import run_project_tests
        with self._write() as conn:
            request = self._owned(conn, claim)
            if request is None or cancel.is_set() or request['type'] != 'request.project_test':
                raise ValueError('Project test execution requires a current uncancelled backend lease')
            self._require_project_execution_controller(request)
            reason = self.project_stage_unavailability(conn, request)
            if reason:
                raise ValueError(reason)
            if self._reusable_project_run(conn, request) is not None:
                return {}
            grant = self._project_grant(conn, request['objective_id'], json.loads(request['payload']).get('projectId'))
            snapshot = self._project_snapshot(conn, request['objective_id'], grant)
            source_base = None
            if self._automatic_source(conn, request['objective_id']):
                from eidolon_cli.organization_source_integration import prepare_source_integration
                source_base = prepare_source_integration(self.settings.read_roots, snapshot)
            from eidolon_cli.organization_budget import budget_view
            if (cancel.is_set() or self._owned(conn, claim) is None
                    or time.time() >= budget_view(conn, request['objective_id'], self.settings)['deadlineTimestamp']):
                raise ValueError('Project execution was cancelled or its lease/deadline expired during snapshot preparation')
            control = conn.execute('SELECT round FROM objective_control WHERE objective_id=?', (request['objective_id'],)).fetchone()
            identifier = 'project_run_' + uuid.uuid4().hex
            conn.execute('INSERT INTO project_run_starts VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (identifier, request['id'], request['token'], request['objective_id'], control['round'],
                 canonical(snapshot), digest(snapshot), canonical(asdict(grant)),
                 canonical(source_base) if source_base else None, self._policy_generation, time.time()))
        # Only this backend calls the runner. The model cannot submit an outcome
        # and service.finish cannot create execution evidence from returned JSON.
        result = run_project_tests(_runner_files(snapshot), grant.execution, cancel)
        with self._write() as conn:
            conn.execute('INSERT INTO project_run_results VALUES (?,?,?,?)',
                         (identifier, canonical(result), digest(result), time.time()))
        return {}

    def _finish_project_test(self, conn, request, result):
        self._require_project_execution_controller(request)
        if result:
            raise ValueError('Backend project execution accepts no model-provided test result')
        row = conn.execute('SELECT id FROM project_run_starts WHERE request_id=? AND token=?',
                           (request['id'], request['token'])).fetchone()
        if row is None:
            row = self._reusable_project_run(conn, request)
            if row is None:
                raise ValueError('No authorized project test execution was recorded for this lease')
        artifact = project_execution_artifact(conn, row['id'])
        if artifact is None:
            raise ValueError('Project run outcome is unknown; it cannot satisfy acceptance')
        outcome = artifact['projectExecution']
        if outcome['status'] != 'passed':
            gate = self._request(conn, request['objective_id'], 'request.project_failed', request['team'], request['priority'],
                                 payload={'evidenceIds': [row['id']], 'runId': row['id'],
                                          **({'projectId': json.loads(request['payload'])['projectId']} if 'projectId' in json.loads(request['payload']) else {})})
            self._pending(conn, conn.execute('SELECT * FROM requests WHERE id=?', (gate,)).fetchone(),
                          'Project tests did not pass: ' + str(outcome.get('reason') or outcome['status'])[:1200]
                          + '. Inspect the retained outcome and replan; no source integration was performed.')
            return
        self._verify_project_run(conn, row['id'], review=False)
        self._request(conn, request['objective_id'], 'request.test_review', request['team'], request['priority'],
                      payload={'evidenceIds': [row['id']], 'runId': row['id'],
                                          **({'projectId': json.loads(request['payload'])['projectId']} if 'projectId' in json.loads(request['payload']) else {})})

    def _finish_test_review(self, conn, request, result):
        from eidolon_cli.organization_store import _text
        payload = json.loads(request['payload'])
        row, artifact = self._verify_project_run(conn, payload.get('runId'), review=False)
        if (row['objective_id'] != request['objective_id']
                or _run_project_id(conn, row) != payload.get('projectId')
                or type(result.get('approved')) is not bool or result.get('evidenceIds') != [row['id']]
                or payload.get('evidenceHashes') != {row['id']: artifact['sha256']}
                or request['agent_id'] in {item[0] for item in conn.execute(
                    'SELECT DISTINCT author_id FROM tasks WHERE objective_id=?', (request['objective_id'],))}):
            raise ValueError('Project review requires exact evidence and an independent reviewer')
        summary = _text(result.get('summary'), 'Project execution review', 10000)
        conn.execute('INSERT INTO project_run_reviews VALUES (?,?,?,?,?,?,?)',
                     (request['id'], row['id'], artifact['sha256'], request['agent_id'],
                      int(result['approved']), summary, time.time()))
        if not result['approved']:
            control = conn.execute('SELECT round,max_replans FROM objective_control WHERE objective_id=?',
                                   (request['objective_id'],)).fetchone()
            if control['round'] < min(control['max_replans'], self.settings.max_replans):
                self._replan(conn, request['objective_id'], summary, completing_request=request['id'], requester_id=request['agent_id'])
            else:
                gate = self._request(conn, request['objective_id'], 'request.project_failed', request['team'], request['priority'],
                                     payload={'evidenceIds': [row['id']], 'budgetExhausted': True})
                self._pending(conn, conn.execute('SELECT * FROM requests WHERE id=?', (gate,)).fetchone(),
                              'Independent project test review rejected the result and the replan budget is exhausted. ' + summary[:1000])

    def verify_project_test_runs(self, conn, objective_id):
        projects = self._execution_projects(conn, objective_id) or [None]
        return [self.verify_project_tests(conn, objective_id, project['id'] if project else None)
                for project in projects]

    def verify_project_tests(self, conn, objective_id, project_id=None):
        projects = self._execution_projects(conn, objective_id)
        if projects and project_id is None:
            return self.verify_project_test_runs(conn, objective_id)[0]
        self._execution_project(conn, objective_id, project_id)
        row = _latest_run(conn, objective_id, project_id)
        if row is None:
            raise ValueError('Required project tests have not been executed by an authorized runtime; model approval cannot satisfy this check')
        return self._verify_project_run(conn, row['id'])

    def ensure_source_integration(self, conn, objective_id):
        if not self._automatic_source(conn, objective_id):
            return False
        projects = self._execution_projects(conn, objective_id) or [None]
        objective = conn.execute('SELECT priority FROM objectives WHERE id=?', (objective_id,)).fetchone()
        queued = False
        for project in projects:
            project_id = project['id'] if project else None
            row, _ = self.verify_project_tests(conn, objective_id, project_id)
            if self.verified_source_integration(conn, objective_id, project_id):
                continue
            self._request(conn, objective_id, 'request.source_integrate', self.settings.team,
                          objective['priority'], payload={'runId': row['id'], 'evidenceIds': [row['id']],
                          **({'projectId': project_id} if project else {})})
            queued = True
        return queued

    def _run_source_stage(self, claim, cancel):
        from eidolon_cli.organization_source_integration import integrate_source, source_manifest_sha256, source_base_sha256
        with self._write() as conn:
            request = self._owned(conn, claim)
            if request is None or cancel.is_set() or not self._automatic_source(conn, request['objective_id']):
                raise ValueError('Source integration requires a current uncancelled explicitly granted lease')
            self._require_project_execution_controller(request)
            payload = json.loads(request['payload'])
            self._execution_project(conn, request['objective_id'], payload.get('projectId'))
            row, _ = self.verify_project_tests(conn, request['objective_id'], payload.get('projectId'))
            if row['id'] != payload.get('runId') or not row['source_base']:
                raise ValueError('Source integration has no exact reviewed tested source base')
            snapshot, base = json.loads(row['snapshot']), json.loads(row['source_base'])
            def before_publish():
                from eidolon_cli.organization_budget import budget_view
                if (cancel.is_set() or self._owned(conn, claim) is None
                        or time.time() >= budget_view(conn, request['objective_id'], self.settings)['deadlineTimestamp']):
                    raise ValueError('Source integration was cancelled or its lease/deadline expired before publication')
            before_publish()
            previous = conn.execute('SELECT * FROM project_source_receipts WHERE request_id=?',
                                    (request['id'],)).fetchone()
            if previous is not None:
                if previous['run_id'] != row['id'] or digest(json.loads(previous['record'])) != previous['sha256']:
                    raise ValueError('The existing source integration receipt conflicts with this exact request')
                from eidolon_cli.organization_source_integration import verify_source_integration
                verify_source_integration(self.settings.read_roots, base, snapshot, json.loads(previous['record']))
                return {}
            receipt = integrate_source(self.settings.read_roots, base, snapshot,
                grant={'sourceIntegration': True, 'sourceBaseSha256': source_base_sha256(base),
                       'manifestSha256': source_manifest_sha256(snapshot)}, integration_id=request['id'],
                before_publish=before_publish, cancel=cancel)
            conn.execute('INSERT INTO project_source_receipts VALUES (?,?,?,?,?)',
                         (request['id'], row['id'], canonical(receipt), digest(receipt), time.time()))
        return {}

    def _finish_source_integration(self, conn, request, result):
        self._require_project_execution_controller(request)
        if result or not conn.execute('SELECT 1 FROM project_source_receipts WHERE request_id=?', (request['id'],)).fetchone():
            raise ValueError('Source integration requires its exact backend-persisted receipt')
        if not self.verified_source_integration(conn, request['objective_id'], json.loads(request['payload']).get('projectId')):
            raise ValueError('Source integration receipt does not match current reviewed test evidence')

    def verified_source_integration(self, conn, objective_id, project_id=None):
        projects = self._execution_projects(conn, objective_id)
        if projects and project_id is None:
            return all(self.verified_source_integration(conn, objective_id, project['id']) for project in projects)
        self._execution_project(conn, objective_id, project_id)
        latest = _latest_run(conn, objective_id, project_id)
        if latest is None:
            return False
        row = conn.execute('SELECT * FROM project_source_receipts WHERE run_id=? ORDER BY created DESC LIMIT 1',
                           (latest['id'],)).fetchone()
        if row is None:
            return False
        run, _ = self.verify_project_tests(conn, objective_id, project_id)
        receipt = json.loads(row['record'])
        from eidolon_cli.organization_source_integration import source_manifest_sha256, verify_source_integration
        if (digest(receipt) != row['sha256'] or receipt.get('status') != 'integrated'
                or receipt.get('manifestSha256') != source_manifest_sha256(json.loads(run['snapshot']))):
            raise ValueError('Source integration receipt is missing, stale or changed')
        verify_source_integration(self.settings.read_roots, json.loads(run['source_base']),
                                  json.loads(run['snapshot']), receipt)
        return True

    def current_source_evidences(self, conn, objective_id):
        projects = self._execution_projects(conn, objective_id) or [None]
        return [source for project in projects if (source := self.current_source_evidence(
            conn, objective_id, project['id'] if project else None)) is not None]

    def current_source_evidence(self, conn, objective_id, project_id=None):
        if not self._automatic_source(conn, objective_id):
            return None
        if not self.verified_source_integration(conn, objective_id, project_id):
            raise ValueError('Current objective source branch integration is not verified')
        row = _latest_run(conn, objective_id, project_id)
        source = conn.execute('SELECT request_id FROM project_source_receipts WHERE run_id=? ORDER BY created DESC LIMIT 1',
                              (row['id'],)).fetchone()
        return source_integration_artifact(conn, 'project_source_' + source['request_id'])
