"""Bounded UI projections of the authoritative organization ledger."""
import json
from eidolon_cli.organization_store import _iso
from eidolon_cli.organization_receipts import receipt_view
from eidolon_cli.organization_roster import configured_workers, staff_unavailability
from eidolon_cli.organization_edits import evidence_proposal
from eidolon_cli.organization_acceptance import usage_view
from eidolon_cli.organization_owner import allowed_resolutions
from eidolon_cli.organization_project_workspace import project_validation_view


def build_snapshot(conn, settings, objective_id=None, resolution_options=None):
    # Keep every open objective visible; only completed history is windowed.
    rows = (conn.execute("SELECT * FROM objectives WHERE id=?", (objective_id,)).fetchall()
            if objective_id else conn.execute("SELECT * FROM objectives ORDER BY created DESC LIMIT 1000").fetchall())
    requests = [dict(row) for row in conn.execute("SELECT * FROM requests ORDER BY created")]
    tasks = [dict(row) for row in conn.execute("SELECT * FROM tasks ORDER BY rowid")]
    task_groups, request_groups = {}, {}
    for task in tasks:
        task_groups.setdefault(task['objective_id'], []).append(task)
    for request in requests:
        request_groups.setdefault(request['objective_id'], []).append(request)
    controls = {row['objective_id']: row for row in conn.execute('SELECT * FROM objective_control')}
    rounds = {row['task_id']: row['round'] for row in conn.execute('SELECT * FROM objective_task_rounds')}
    objectives = []
    history_count = 0
    for row in rows:
        control = controls[row['id']]
        work = [task for task in task_groups.get(row['id'], []) if rounds.get(task['id']) == control['round']]
        queue = request_groups.get(row['id'], [])
        if row['cancelled']:
            status = 'cancelled'
        elif any(r['status'] == 'pending_intervention' for r in queue):
            status = 'needs_input'
        elif control['status'] in {'accepted', 'legacy_completed'}:
            status = 'completed'
        elif not work:
            status = 'planning'
        elif any(r['status'] == 'running' for r in queue):
            status = 'active'
        else:
            status = 'waiting'
        if status in {'completed', 'cancelled'}:
            history_count += 1
            if history_count > 25:
                continue
        final = conn.execute('SELECT * FROM objective_deliverables WHERE id=?', (control['deliverable_id'],)).fetchone()
        resolutions = [{'id': item['id'], 'requestId': item['request_id'], 'action': item['action'], 'text': item['text'], 'evidenceIds': json.loads(item['evidence_ids']), 'createdAt': _iso(item['created'])} for item in conn.execute('SELECT * FROM owner_resolutions WHERE objective_id=? ORDER BY created', (row['id'],))]
        acceptance_review = conn.execute('SELECT * FROM objective_acceptances WHERE objective_id=? AND round=? ORDER BY created DESC LIMIT 1', (row['id'], control['round'])).fetchone()
        done = sum(t['status'] == 'completed' for t in work)
        objectives.append({'id': row['id'], 'title': row['title'], 'description': control['amended_scope'] or row['description'],
                           'originalDescription': row['description'], 'deliveryMode': control['delivery_mode'],
                           'requiredChecks': json.loads(control['required_checks']),
                           'projectValidation': project_validation_view(conn, row['id'], full=False),
                           'acceptance': {'status': control['status'], 'criteria': json.loads(control['criteria']),
                                          'round': control['round'], 'maxReplans': min(control['max_replans'], settings.max_replans),
                                          'summary': control['summary'], 'deliverableId': control['deliverable_id'],
                                          'criteriaResults': json.loads(acceptance_review['criteria_results']) if acceptance_review else [],
                                          'conflicts': json.loads(acceptance_review['conflicts']) if acceptance_review else []},
                           'usage': usage_view(conn, control, settings), 'ownerResolutions': resolutions,
                           'status': status, 'source': 'runtime', 'createdAt': _iso(row['created']),
                           'ownerId': 'manager' if control['status'] == 'legacy_completed' else 'executive', 'priority': f"P{6-row['priority']}",
                           'progress': round(100 * done / len(work)) if work else 0,
                           'result': (final['content'] if final else '\n\n'.join(t['result'] or '' for t in work)) if status == 'completed' else None,
                           'phase': 'Legacy reviewed outcome' if control['status'] == 'legacy_completed' else 'Accepted integrated outcome' if status == 'completed' else 'Integrated outcome acceptance' if control['status'] in {'integrating', 'reviewing'} else 'Pending intervention' if status == 'needs_input' else 'Manager planning' if not work else 'Execution and review'})
    visible = {o['id'] for o in objectives}
    tasks = [t for t in tasks if t['objective_id'] in visible]
    requests = [r for r in requests if r['objective_id'] in visible]
    evidence = []
    if visible:
        placeholders = ','.join('?' for _ in visible)
        # Every visible task keeps its latest artifact ID; an arbitrary global
        # limit must never make a reviewed deliverable inaccessible. Bytes are
        # previewed here and retrieved by exact ledger ID on inspection.
        evidence = [dict(row) for row in conn.execute(f"""
            SELECT e.id,e.objective_id,e.task_id,e.request_id,e.sha256,e.summary,e.created,
                   substr(e.content,1,2000) AS content,length(e.content)>2000 AS truncated
            FROM evidence e WHERE e.objective_id IN ({placeholders})
              AND e.id=(SELECT newer.id FROM evidence newer WHERE newer.task_id=e.task_id
                        ORDER BY newer.created DESC,newer.id DESC LIMIT 1)
            ORDER BY e.created DESC""", tuple(visible))]
    evidence_by_task = {}
    for item in evidence:
        evidence_by_task.setdefault(item['task_id'], []).append(item)
    ui_tasks = []
    for task in tasks:
        latest = next((r for r in reversed(requests) if r['task_id'] == task['id']), None)
        proof = evidence_by_task.get(task['id'], [])
        ui_tasks.append({'id': task['id'], 'objectiveId': task['objective_id'], 'title': task['title'],
                         'currentRound': rounds.get(task['id']) == controls[task['objective_id']]['round'],
                         'historical': rounds.get(task['id']) != controls[task['objective_id']]['round'],
                         'ownerId': task['author_id'] or (latest['agent_id'] or '' if latest else ''),
                         'reviewerId': latest['agent_id'] if latest and latest['type'] == 'request.review' else None,
                         'status': task['status'], 'dependsOn': json.loads(task['dependencies']),
                         'assignedById': 'manager', 'priority': f"P{6-task['priority']}",
                         'requestType': task['type'], 'team': task['team'],
                         'review': 'approved' if task['status'] == 'completed' else 'required',
                         'inputs': [task['description']], 'results': [task['result']] if task['result'] else [],
                         'evidence': [{'id': p['id'], 'kind': 'deliverable', 'content': p['content'],
                                       'sha256': p['sha256'], 'createdAt': _iso(p['created']),
                                       'truncated': bool(p['truncated']),
                                       'editProposal': evidence_proposal(conn, p['id'], full=False)} for p in proof]})
    agents = []
    configured = {staff.id: staff for staff in configured_workers(settings)}
    staff_states = {row['agent_id']: row for row in conn.execute('SELECT * FROM staff_state')}
    for row in conn.execute('SELECT * FROM agents ORDER BY rowid'):
        running = next((r for r in requests if r['agent_id'] == row['id'] and r['status'] == 'running'), None)
        blocked = next((r for r in requests if r['agent_id'] == row['id'] and r['status'] == 'pending_intervention'), None)
        accepts = json.loads(row['accepts'])
        staff = configured.get(row['id'])
        state = staff_states.get(row['id'])
        reason = staff_unavailability(staff, settings) if state else None
        lifecycle = 'retired' if state and not staff else 'disabled' if reason else 'available' if state and not state['active'] else 'active'
        disabled = lifecycle in {'retired', 'disabled'}
        agents.append({'id': row['id'], 'name': row['name'], 'role': row['role'], 'managerId': row['manager_id'],
                       'team': row['team'], 'responsibilities': accepts or ['Scoped organizational authority'],
                       'capabilities': accepts, 'requestTypes': accepts,
                       'lifecycle': lifecycle, 'provider': staff.provider if staff else None,
                       'model': staff.model if staff else None, 'tools': list(staff.tool_grants) if staff else [],
                       'status': 'offline' if disabled else 'reviewing' if running and running['type'] in {'request.review', 'request.accept'} else 'executing' if running else 'needs_input' if blocked else 'idle',
                       'summary': reason if disabled else running['type'] if running else blocked['reason'] if blocked else 'Configured staff available for request.hire.' if lifecycle == 'available' else 'Configured organizational role; no work in progress.',
                       'objectiveId': (running or blocked)['objective_id'] if running or blocked else None})
    events = [{'id': f"event_{r['id']}", 'objectiveId': r['objective_id'], 'agentId': r['agent_id'],
               'kind': r['kind'], 'text': r['text'], 'timestamp': _iso(r['created']), 'source': 'runtime'}
              for r in conn.execute('SELECT * FROM events ORDER BY id DESC LIMIT 500') if r['objective_id'] in visible]
    receipt_groups = {}
    counts = {row['request_id']: row['total'] for row in conn.execute('SELECT request_id,count(*) AS total FROM tool_receipts GROUP BY request_id')}
    # Only bounded recent previews ride a poll. The exact-request RPC and the
    # artifact inspector retain every receipt, including failed/no-artifact work.
    visible_requests = {request['id'] for request in requests}
    for row in conn.execute('SELECT * FROM tool_receipts ORDER BY created DESC,id DESC LIMIT 200'):
        if row['request_id'] in visible_requests:
            receipt_groups.setdefault(row['request_id'], []).append(receipt_view(row))
    ui_requests = [{'id': r['id'], 'objectiveId': r['objective_id'], 'taskId': r['task_id'], 'type': r['type'],
                    'team': r['team'], 'priority': r['priority'], 'status': r['status'], 'agentId': r['agent_id'],
                    'allowedResolutions': resolution_options(conn, r) if resolution_options else allowed_resolutions(conn, r, settings),
                    'reason': r['reason'], 'attempts': r['attempts'], 'leaseExpiresAt': _iso(r['lease']),
                    'requestedRoutes': json.loads(r['payload']).get('routes', []),
                    'requestedWorkers': json.loads(r['payload']).get('workers'),
                    'toolReceipts': list(reversed(receipt_groups.get(r['id'], []))),
                    'toolReceiptCount': counts.get(r['id'], 0),
                    'toolReceiptsTruncated': counts.get(r['id'], 0) > len(receipt_groups.get(r['id'], [])),
                    'createdAt': _iso(r['created'])} for r in requests]
    knowledge = [{'id': r['id'], 'objectiveId': r['objective_id'], 'title': r['summary'][:120],
                  'kind': 'artifact', 'body': r['content'] + ('\n\n[Preview truncated. Open this artifact to read the full retained deliverable.]' if r['truncated'] else '')}
                 for r in evidence]
    return {'source': 'runtime', 'objectives': objectives, 'agents': agents, 'tasks': ui_tasks, 'activity': events,
            'knowledge': knowledge, 'decisions': [], 'requests': ui_requests,
            'runtime': {'state': 'ready', 'capabilities': list(settings.capabilities), 'maxWorkers': settings.max_workers,
                        'scope': 'Writing and analysis of submitted context; configured work.inspect staff may read explicitly granted local text files. Other tools and unsupported requests require intervention.',
                        'readFileEnabled': 'read_file' in settings.tool_grants and bool(settings.read_roots),
                        'readRoots': list(settings.read_roots), 'maxToolCalls': settings.max_tool_calls,
                        'supportsWorkspaceEdits': 'work.edit' in settings.capabilities,
                        'workspaceApplyEnabled': 'patch' in settings.tool_grants and settings.roster is not None,
                        'historyLimited': history_count > 25, 'artifactPreviewLimit': 2000}}
