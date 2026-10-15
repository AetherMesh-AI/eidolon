"""Conservative runtime evidence reuse. Model declarations never prove isolation.

The first automatic adapter admits only tool-free, project-free managed work.
Everything else retains the existing bounded whole-objective replan path.
"""
from dataclasses import asdict
import hashlib
import json

REPAIR_TABLES = frozenset({'task_repair_specifications', 'task_repair_inputs',
                          'objective_repair_decisions', 'objective_reused_evidence', 'task_repair_lineage'})
SCHEMA = '''
CREATE TABLE IF NOT EXISTS task_repair_specifications (
 task_id TEXT PRIMARY KEY REFERENCES tasks(id), specification TEXT NOT NULL, sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS task_repair_lineage (
 task_id TEXT PRIMARY KEY REFERENCES tasks(id), source_task_id TEXT NOT NULL REFERENCES tasks(id));
CREATE TABLE IF NOT EXISTS task_repair_inputs (
 request_id TEXT NOT NULL REFERENCES requests(id), attempt INTEGER NOT NULL,
 input_sha256 TEXT NOT NULL, scope_sha256 TEXT NOT NULL, payload_sha256 TEXT NOT NULL, task_sha256 TEXT NOT NULL, dependencies TEXT NOT NULL,
 eligible INTEGER NOT NULL, PRIMARY KEY(request_id,attempt,input_sha256));
CREATE TABLE IF NOT EXISTS objective_repair_decisions (
 objective_id TEXT NOT NULL REFERENCES objectives(id), round INTEGER NOT NULL,
 record TEXT NOT NULL, sha256 TEXT NOT NULL, PRIMARY KEY(objective_id,round));
CREATE TABLE IF NOT EXISTS objective_reused_evidence (
 objective_id TEXT NOT NULL REFERENCES objectives(id), round INTEGER NOT NULL,
 task_id TEXT NOT NULL REFERENCES tasks(id), evidence_id TEXT NOT NULL REFERENCES evidence(id),
 record TEXT NOT NULL, sha256 TEXT NOT NULL, PRIMARY KEY(objective_id,round,task_id));
'''
for _table in REPAIR_TABLES:
    for _op in ('UPDATE', 'DELETE'):
        SCHEMA += (f'CREATE TRIGGER IF NOT EXISTS immutable_{_table}_{_op.lower()} BEFORE {_op} ON {_table} '
                   "BEGIN SELECT RAISE(ABORT,'Immutable repair provenance'); END;\n")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def present(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")} & REPAIR_TABLES
    if tables and tables != REPAIR_TABLES:
        raise ValueError('Selective repair storage is incomplete')
    return bool(tables)


def scope_digest(store, conn, objective_id):
    from eidolon_cli.organization_outcome_contracts import contract_view
    objective = dict(conn.execute('SELECT * FROM objectives WHERE id=?', (objective_id,)).fetchone())
    control = conn.execute('SELECT * FROM objective_control WHERE objective_id=?', (objective_id,)).fetchone()
    inputs = [dict(r) for r in conn.execute('SELECT * FROM owner_resolutions WHERE objective_id=? ORDER BY rowid', (objective_id,))]
    return digest({'objective': objective, 'contract': contract_view(conn, objective_id),
        'scope': {k: control[k] for k in ('criteria', 'delivery_mode', 'amended_scope', 'required_checks')},
        'ownerInputs': inputs, 'settings': asdict(store.settings), 'policyGeneration': store._policy_generation})


def record_specification(conn, task_id, specification):
    # Preserve presence: omitted dependencies are unknown, never independent.
    raw = json.dumps(specification, sort_keys=True)
    conn.execute('INSERT INTO task_repair_specifications VALUES (?,?,?)', (task_id, raw, digest(specification)))


def task_digest(conn, task):
    from eidolon_cli.organization_package_repair import task_package_binding
    return digest({'packageBinding': task_package_binding(conn, task['id']), 'task': {k: task[k] for k in ('id', 'objective_id', 'title', 'description', 'type', 'team', 'priority', 'dependencies', 'revision')}})


def capture_inputs(store, conn, request, context):
    if request['type'] not in {'work.draft', 'work.analyze', 'request.review'} or not request['task_id']:
        return
    if context['task']['type'] not in {'work.draft', 'work.analyze'}:
        return
    dependencies = {item['taskId'] for item in context['dependencies']}
    # Retained agent history is a real input edge, even when omitted by the planner.
    history = context['agentContext'].get('recentHistory', [])
    dependencies.update(item['taskId'] for item in history if item.get('taskId') and item['taskId'] != request['task_id'])
    memory = context['agentContext'].get('memory')
    own_review_evidence = (request['type'] == 'request.review' and bool(context['evidence'])
                           and all(e.get('task_id') == request['task_id'] for e in context['evidence']))
    history_bound = all(item.get('taskId') and item['taskId'] != request['task_id'] for item in history)
    eligible = (history_bound and not context['toolPolicy']['tools'] and not context['objective']['projects']
                and not any(memory.values()) and (not context['evidence'] or own_review_evidence))
    # Volatile allowance/lease counters are not authority or artifact inputs.
    stable = {k: v for k, v in context.items() if k not in {'timeoutSeconds', 'budget'}}
    conn.execute('INSERT OR IGNORE INTO task_repair_inputs VALUES (?,?,?,?,?,?,?,?)',
        (request['id'], request['execution_count'], digest(stable), scope_digest(store, conn, request['objective_id']),
         digest(json.loads(request['payload'])), task_digest(conn, context['task']), json.dumps(sorted(dependencies)), int(eligible)))


def dependency_closure(nodes, failed):
    """Invalidate semantic, actually consumed, and colliding-output dependencies."""
    invalid = set(failed)
    if not invalid <= nodes.keys():
        raise ValueError('Repair failure references unknown tasks')
    for ident, node in nodes.items():
        if not node['dependenciesDeclared'] or not set(node['dependencies']) <= nodes.keys():
            return set(nodes), 'missing_dependency_declaration'
    changed = True
    while changed:
        changed = False
        for ident, node in nodes.items():
            if ident in invalid:
                continue
            collision = any(_collides(node['outputs'], nodes[other]['outputs']) for other in invalid)
            if set(node['dependencies']) & invalid or collision:
                invalid.add(ident)
                changed = True
    return invalid, None


def _collides(left, right):
    # Canonical resource tuples are computed by the runtime, never model paths.
    return any(a[0] == b[0] and (a[1] == b[1] or a[1].startswith(b[1] + '/')
                               or b[1].startswith(a[1] + '/')) for a in left for b in right)


def _proof(store, conn, task, scope):
    evidence = conn.execute('SELECT * FROM evidence WHERE task_id=? ORDER BY created DESC,id DESC LIMIT 1', (task['id'],)).fetchone()
    if evidence is None or hashlib.sha256(evidence['content'].encode()).hexdigest() != evidence['sha256']:
        raise ValueError('changed_artifact')
    work = conn.execute('SELECT * FROM requests WHERE id=?', (evidence['request_id'],)).fetchone()
    inputs = conn.execute('SELECT * FROM task_repair_inputs WHERE request_id=? AND attempt=?', (work['id'], work['execution_count'])).fetchall()
    if (len(inputs) != 1 or not inputs[0]['eligible'] or inputs[0]['scope_sha256'] != scope
            or inputs[0]['payload_sha256'] != digest(json.loads(work['payload'])) or inputs[0]['task_sha256'] != task_digest(conn, task)):
        raise ValueError('missing_or_changed_runtime_inputs')
    if conn.execute('SELECT 1 FROM tool_receipts WHERE request_id IN (SELECT id FROM requests WHERE task_id=?)', (task['id'],)).fetchone():
        raise ValueError('external_tool_input')
    review = conn.execute('SELECT * FROM reviews WHERE task_id=? AND approved=1 ORDER BY created DESC,rowid DESC LIMIT 1', (task['id'],)).fetchone()
    if review is None or review['agent_id'] == task['author_id'] or evidence['id'] not in json.loads(review['evidence_ids']):
        raise ValueError('missing_independent_review')
    request = conn.execute('SELECT * FROM requests WHERE id=?', (review['request_id'],)).fetchone()
    if json.loads(request['payload']).get('evidenceHashes', {}).get(evidence['id']) != evidence['sha256']:
        raise ValueError('stale_review')
    review_inputs = conn.execute('SELECT * FROM task_repair_inputs WHERE request_id=? AND attempt=?',
                                 (request['id'], request['execution_count'])).fetchall()
    if (len(review_inputs) != 1 or not review_inputs[0]['eligible'] or review_inputs[0]['scope_sha256'] != scope
            or review_inputs[0]['payload_sha256'] != digest(json.loads(request['payload']))
            or review_inputs[0]['task_sha256'] != task_digest(conn, task)):
        raise ValueError('missing_or_changed_review_inputs')
    store._verify_tool_evidence(conn, evidence['id'], require_read=False)
    return {'taskId': task['id'], 'evidenceId': evidence['id'], 'evidenceSha256': evidence['sha256'],
            'reviewRequestId': review['request_id'], 'reviewSha256': digest(dict(review)),
            'reviewPayloadSha256': digest(json.loads(request['payload'])), 'workRequestId': work['id'],
            'workPayloadSha256': digest(json.loads(work['payload'])), 'inputSha256': inputs[0]['input_sha256'],
            'scopeSha256': scope, 'taskSha256': task_digest(conn, task), 'reviewInputSha256': review_inputs[0]['input_sha256'],
            'consumedTaskIds': sorted(set(json.loads(inputs[0]['dependencies'])) | set(json.loads(review_inputs[0]['dependencies'])))}


def plan_repair(store, conn, objective_id, result):
    """Return an auditable plan; conservative fallbacks are normal outcomes."""
    from eidolon_cli.organization_acceptance import current_tasks
    from eidolon_cli.organization_outcome_contracts import contract_view
    from eidolon_cli.organization_projects import objective_projects
    control = conn.execute('SELECT * FROM objective_control WHERE objective_id=?', (objective_id,)).fetchone()
    tasks = current_tasks(conn, objective_id)
    base = {'schemaVersion': 1, 'objectiveId': objective_id, 'fromRound': control['round'],
            'toRound': control['round'] + 1, 'mode': 'whole_replan', 'reused': [],
            'invalidatedTaskIds': [t['id'] for t in tasks],
            'invalidatedIntegrationChecks': json.loads(control['required_checks']),
            'synthesisRequired': True, 'budgetReset': False, 'attemptReset': False}
    def fallback(reason):
        return {**base, 'reason': reason}
    if not result or result.get('conflicts'):
        return fallback('ambiguous_failure_scope')
    contract = contract_view(conn, objective_id)
    if not contract or any(c['legacy'] or c['kind'] != 'deliverable' for c in contract['criteria']):
        return fallback('unsupported_contract')
    if objective_projects(conn, objective_id) or control['delivery_mode'] != 'managed_artifact' or json.loads(control['required_checks']):
        return fallback('integration_wide_or_source_inputs')
    allowed = {'request.decompose', 'request.plan', 'request.hire', 'work.draft', 'work.analyze', 'request.review', 'request.integrate', 'request.accept'}
    if any(r[0] not in allowed for r in conn.execute('SELECT type FROM requests WHERE objective_id=?', (objective_id,))):
        return fallback('cross_task_or_external_input')
    failed = {r['criterionId'] for r in result['criteriaResults'] if not r['satisfied']}
    criteria = {c['id']: c['revision'] for c in contract['criteria']}
    if not failed or not failed <= criteria.keys():
        return fallback('missing_failed_criterion_ids')
    nodes, proofs, specs = {}, {}, {}
    scope = scope_digest(store, conn, objective_id)
    try:
        for task in tasks:
            row = conn.execute('SELECT * FROM task_repair_specifications WHERE task_id=?', (task['id'],)).fetchone()
            if row is None:
                return fallback('missing_dependency_declaration')
            spec = json.loads(row['specification'])
            if digest(spec) != row['sha256'] or 'dependsOn' not in spec:
                return fallback('missing_dependency_declaration')
            ids = spec.get('criterionIds')
            if not isinstance(ids, list) or not ids or len(set(ids)) != len(ids) or not set(ids) <= criteria.keys():
                return fallback('missing_criterion_mapping')
            if task['status'] != 'completed' or task['type'] not in {'work.draft', 'work.analyze'}:
                return fallback('unsupported_task_or_incomplete_review')
            proof = _proof(store, conn, task, scope)
            write = conn.execute('SELECT resources FROM task_write_scopes WHERE task_id=?', (task['id'],)).fetchone()
            nodes[task['id']] = {'dependenciesDeclared': True,
                'dependencies': sorted(set(json.loads(task['dependencies'])) | set(proof['consumedTaskIds'])),
                'outputs': json.loads(write['resources']) if write else [], 'criterionIds': ids}
            proofs[task['id']] = {**proof, 'criterionRevisions': {i: criteria[i] for i in ids},
                                   'specificationSha256': row['sha256']}
            specs[task['id']] = spec
    except (ValueError, KeyError, TypeError):
        return fallback('unverified_or_stale_evidence')
    if set().union(*(set(n['criterionIds']) for n in nodes.values())) != criteria.keys():
        return fallback('incomplete_criterion_mapping')
    invalid, reason = dependency_closure(nodes, {i for i, n in nodes.items() if set(n['criterionIds']) & failed})
    if reason or not invalid or invalid == nodes.keys():
        return fallback(reason or 'no_independent_reusable_evidence')
    return {**base, 'mode': 'selective', 'reason': 'runtime_verified_unchanged_inputs',
            'scopeSha256': scope, 'contractSha256': contract['sha256'],
            'invalidatedTaskIds': sorted(invalid), 'reused': [proofs[i] for i in nodes if i not in invalid],
            'nodes': nodes, 'specifications': specs}


def record_decision(conn, plan):
    conn.execute('INSERT INTO objective_repair_decisions VALUES (?,?,?,?)',
                 (plan['objectiveId'], plan['toRound'], json.dumps(plan, sort_keys=True), digest(plan)))


def reused_tasks(conn, objective_id, round_number):
    if not present(conn):
        return []
    return conn.execute('SELECT t.* FROM tasks t JOIN objective_reused_evidence r ON r.task_id=t.id '
                        'WHERE r.objective_id=? AND r.round=? ORDER BY t.rowid', (objective_id, round_number)).fetchall()


def verify_reused(store, conn, objective_id, round_number):
    if not present(conn):
        return
    scope = scope_digest(store, conn, objective_id)
    for row in conn.execute('SELECT * FROM objective_reused_evidence WHERE objective_id=? AND round=?', (objective_id, round_number)):
        record = json.loads(row['record'])
        task = conn.execute('SELECT * FROM tasks WHERE id=?', (row['task_id'],)).fetchone()
        proof = _proof(store, conn, task, scope)
        if digest(record) != row['sha256'] or any(record.get(k) != v for k, v in proof.items()):
            raise ValueError('Reused evidence no longer matches immutable reviewed inputs')


def inherit_attempts(conn, request_id, task_id, request_type):
    source = conn.execute('SELECT source_task_id FROM task_repair_lineage WHERE task_id=?', (task_id,)).fetchone()
    if source is None:
        return
    previous = conn.execute('SELECT MAX(attempts),MAX(execution_count) FROM requests WHERE task_id IN (?,?) AND type=? AND id!=?',
                            (source[0], task_id, request_type, request_id)).fetchone()
    if previous and previous[0] is not None:
        conn.execute('UPDATE requests SET attempts=?,execution_count=? WHERE id=?', (*previous, request_id))


def apply_repair(store, conn, plan, feedback):
    """Create only replacement work; reuse never fabricates execution or review."""
    from eidolon_cli.organization_store import _id
    objective_id, round_number = plan['objectiveId'], plan['toRound']
    mapping = {item['taskId']: item['taskId'] for item in plan['reused']}
    mapping.update({ident: _id('task') for ident in plan['invalidatedTaskIds']})
    for proof in plan['reused']:
        conn.execute('INSERT INTO objective_reused_evidence VALUES (?,?,?,?,?,?)',
                     (objective_id, round_number, proof['taskId'], proof['evidenceId'],
                      json.dumps(proof, sort_keys=True), digest(proof)))
    # Insert every replacement before creating requests so dependency references
    # always point at durable rows, including runtime-discovered context edges.
    for ident in plan['invalidatedTaskIds']:
        task = conn.execute('SELECT * FROM tasks WHERE id=?', (ident,)).fetchone()
        dependencies = [mapping[dep] for dep in plan['nodes'][ident]['dependencies']]
        conn.execute('INSERT INTO tasks(id,objective_id,title,description,type,team,priority,status,dependencies,revision,feedback) '
                     "VALUES (?,?,?,?,?,?,?,'queued',?,?,?)",
                     (mapping[ident], objective_id, task['title'], task['description'], task['type'], task['team'],
                      task['priority'], json.dumps(dependencies), task['revision'] + 1, feedback))
        conn.execute('INSERT INTO objective_task_rounds VALUES (?,?,?)', (mapping[ident], objective_id, round_number))
        conn.execute('INSERT INTO task_repair_lineage VALUES (?,?)', (mapping[ident], ident))
        record_specification(conn, mapping[ident], plan['specifications'][ident])
        assignment = conn.execute('SELECT * FROM task_assignments WHERE task_id=?', (ident,)).fetchone()
        if assignment:
            columns = list(assignment.keys())
            values = [mapping[ident] if key == 'task_id' else assignment[key] for key in columns]
            conn.execute('INSERT INTO task_assignments (' + ','.join(columns) + ') VALUES (' + ','.join('?' for _ in values) + ')', values)
    from eidolon_cli.organization_package_repair import carry_packages
    carry_packages(conn, objective_id, plan['fromRound'], round_number, mapping)
    for ident in plan['invalidatedTaskIds']:
        task = conn.execute('SELECT * FROM tasks WHERE id=?', (mapping[ident],)).fetchone()
        assignment = conn.execute('SELECT manager_id FROM task_assignments WHERE task_id=?', (task['id'],)).fetchone()
        request_id = store._request(conn, objective_id, task['type'], task['team'], task['priority'], task['id'],
                       requester_id=assignment[0] if assignment else store._objective_agent(conn, objective_id, 'Manager')['id'])
        if task['revision'] > store.settings.max_revisions:
            request = conn.execute('SELECT * FROM requests WHERE id=?', (request_id,)).fetchone()
            store._pending(conn, request, 'Selective repair cannot reset the task revision limit.')
            conn.execute('UPDATE requests SET attempts=? WHERE id=?', (store.settings.max_attempts, request_id))
    store._event(conn, objective_id,
        f"Selective repair queued {len(plan['invalidatedTaskIds'])} replacement tasks; {len(plan['reused'])} exact reviewed artifacts are reused. Integration and acceptance will run again.", 'planning')


def reuse_view(conn, objective_id, round_number):
    if not present(conn):
        return {}
    result = {}
    for row in conn.execute('SELECT * FROM objective_reused_evidence WHERE objective_id=? AND round=?', (objective_id, round_number)):
        record = json.loads(row['record'])
        if digest(record) != row['sha256']:
            raise ValueError('Repair provenance is corrupt')
        result[row['task_id']] = {'disposition': 'reused', 'evidenceId': record['evidenceId'],
            'evidenceSha256': record['evidenceSha256'], 'reviewRequestId': record['reviewRequestId'],
            'inputSha256': record['inputSha256'], 'round': round_number}
    return result
