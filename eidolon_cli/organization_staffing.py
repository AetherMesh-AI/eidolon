"""Persisted activation of configured organization staff; never creates profiles."""
from __future__ import annotations

import json

from eidolon_cli.organization_roster import configured_staff, configured_workers, select_staff_activation, staff_unavailability


STAFF_SCHEMA = """
CREATE TABLE IF NOT EXISTS staff_state (
 agent_id TEXT PRIMARY KEY REFERENCES agents(id), active INTEGER NOT NULL,
 source TEXT NOT NULL, policy TEXT NOT NULL);
"""


class OrganizationStaffingStore:
    def _sync_staff(self, conn):
        mode = 'logical' if self.settings.roster is None else 'configured'
        configured = configured_staff(self.settings)
        for staff in configured:
            prior_agent = conn.execute('SELECT id,name FROM agents WHERE id=?', (staff.id,)).fetchone()
            prior = conn.execute('SELECT * FROM staff_state WHERE agent_id=?', (staff.id,)).fetchone()
            active = bool(prior['active']) if prior and prior['source'] == mode else (
                mode == 'logical' and (prior_agent is not None or staff.id == 'worker-1'))
            if staff.role != 'Worker':
                active = staff.enabled
            if not staff.enabled:
                active = False
            name = prior_agent['name'] if mode == 'logical' and prior_agent and prior_agent['name'] not in {f'Logical worker {staff.id.removeprefix("worker-")}', staff.name} else staff.name
            conn.execute('INSERT INTO agents VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET '
                         'name=excluded.name,role=excluded.role,manager_id=excluded.manager_id,team=excluded.team,accepts=excluded.accepts',
                         (staff.id, name, staff.role, staff.manager_id, staff.team, json.dumps(staff.capabilities)))
            policy = json.dumps({'provider': staff.provider, 'model': staff.model, 'tools': staff.tool_grants})
            conn.execute('INSERT INTO staff_state VALUES (?,?,?,?) ON CONFLICT(agent_id) DO UPDATE SET '
                         'active=excluded.active,source=excluded.source,policy=excluded.policy',
                         (staff.id, int(active), mode, policy))
        current_ids = {staff.id for staff in configured}
        # Retain history/claim authors while withdrawing removed identities.
        for row in conn.execute('SELECT * FROM agents'):
            if (row['id'].startswith('worker-') or conn.execute(
                    'SELECT 1 FROM staff_state WHERE agent_id=?', (row['id'],)).fetchone()) and row['id'] not in current_ids:
                conn.execute("INSERT INTO staff_state VALUES (?,0,'retired','{}') ON CONFLICT(agent_id) "
                             "DO UPDATE SET active=0,source='retired'", (row['id'],))
        for request in conn.execute("SELECT * FROM requests WHERE status='running'").fetchall():
            reason = self._staff_reason(conn, {'id': request['agent_id']}, request['type'])
            if reason:
                self._pending(conn, request, 'Staffing policy changed during execution. ' + reason)
        # Reviews keep the work's team, rather than broadening back to general.
        for team in dict.fromkeys([self.settings.team, *(staff.team for staff in configured)]):
            if team == self.settings.team:
                continue
            import hashlib
            ident = 'reviewer-' + hashlib.sha256(team.encode()).hexdigest()[:16]
            conn.execute('INSERT OR IGNORE INTO agents VALUES (?,?,?,?,?,?)',
                         (ident, f'Reviewer · {team}', 'Worker', 'manager', team, '["request.review"]'))
            applier_id = 'control:apply:' + hashlib.sha256(team.encode()).hexdigest()[:16]
            conn.execute('INSERT OR IGNORE INTO agents VALUES (?,?,?,?,?,?)',
                         (applier_id, f'Workspace applier · {team}', 'Worker', 'manager', team, '["request.apply","request.validate"]'))
            conn.execute('UPDATE agents SET accepts=? WHERE id=?', ('["request.apply","request.validate"]', applier_id))

    def _staff(self, agent_id):
        return next((staff for staff in configured_staff(self.settings) if staff.id == agent_id), None)

    def _active_staff(self, conn):
        return {row[0] for row in conn.execute("SELECT s.agent_id FROM staff_state s JOIN agents a ON a.id=s.agent_id WHERE s.active=1 AND a.role='Worker'")}

    def _staff_reason(self, conn, agent, request_type):
        state = conn.execute('SELECT * FROM staff_state WHERE agent_id=?', (agent['id'],)).fetchone()
        if state is None:
            return None
        staff = self._staff(agent['id'])
        unavailable = staff_unavailability(staff, self.settings, request_type=request_type)
        if unavailable:
            return unavailable
        if not state['active']:
            return 'Configured staff is available but not activated; request.hire is required.'
        return None

    def _tool_policy(self, conn, request):
        staff = self._staff(request['agent_id'])
        tools = []
        if (staff and request['type'] in {'work.inspect', 'work.edit'}
                and not self._staff_reason(conn, {'id': staff.id}, request['type'])):
            tools = [tool for tool in staff.tool_grants if tool in {'read_file', 'list_files', 'search_files'} and tool in self.settings.tool_grants]
        policy = {'tools': tools, 'readRoots': list(self.settings.read_roots),
                  'maxToolCalls': self.settings.max_tool_calls,
                  'maxResultChars': self.settings.max_tool_result_chars}
        from eidolon_cli.organization_projects import task_project, validate_objective_projects
        projects = validate_objective_projects(conn, request['objective_id'], self.settings)
        if projects:
            project = task_project(conn, request['task_id']) if request['task_id'] else None
            policy['readRootAliases'] = [project['root']] if project in projects else []
            if not policy['readRootAliases']:
                policy['tools'] = []
        return policy

    def _staffing_context(self):
        return [{'id': staff.id, 'name': staff.name, 'team': staff.team,
                 'capabilities': list(staff.capabilities), 'tools': list(staff.tool_grants),
                 'provider': staff.provider, 'model': staff.model,
                 'enabled': staff.enabled, 'role': staff.role, 'managerId': staff.manager_id,
                 'responsibilities': list(staff.responsibilities), 'purpose': staff.purpose,
                 'authority': list(staff.authority), 'managedTeams': list(staff.managed_teams), 'scope': staff.scope, 'availableReason': staff_unavailability(staff, self.settings)}
                for staff in configured_staff(self.settings)]

    def _route_reason(self, conn, request):
        typed = self._typed_context(conn, request)['requestContract']
        if typed.get('parentRequestId'):
            return f"No eligible persistent agent accepts {request['type']} for team {request['team']} with {typed['requiredAuthority']}. Owner response is required."
        if request['type'] in {'request.project_test', 'request.source_integrate'}:
            return self.project_stage_unavailability(conn, request) or 'The exact backend project controller is unavailable.'
        if request['type'] == 'request.merge':
            return 'The reviewed output is in the managed workspace. Merging it into the source project requires owner intervention; no source file was overwritten.'
        if request['type'] == 'request.validate':
            reason = self.edit_validate_unavailability(conn, request)
            if reason:
                return reason
        if request['type'] == 'request.apply':
            reason = self.edit_apply_unavailability(conn, request)
            if reason:
                return reason
        matching = [staff for staff in configured_workers(self.settings)
                    if staff.team == request['team'] and request['type'] in staff.capabilities]
        detail = [self._staff_reason(conn, {'id': staff.id}, request['type']) for staff in matching]
        reason = next((value for value in detail if value), 'No configured staff has that exact type and team.')
        return f"No eligible agent accepts {request['type']} for team {request['team']}. {reason}"

    def _hiring_pending(self, conn, request):
        if request['type'].startswith('request.'):
            return False
        return conn.execute("SELECT 1 FROM requests WHERE objective_id=? AND type='request.hire' "
                            "AND status IN ('queued','running')", (request['objective_id'],)).fetchone() is not None

    def _ensure_route_hire(self, conn, request):
        """Re-activate restored definitions on an owner-retried work request.

        An earlier intervention remains a gate; this never creates a fresh hire
        to evade its retry budget. Ordinary retries still preserve task identity.
        """
        if self.settings.roster is None or request['type'].startswith('request.'):
            return False
        if conn.execute("SELECT 1 FROM requests WHERE objective_id=? AND type='request.hire' "
                        "AND status NOT IN ('completed','cancelled')", (request['objective_id'],)).fetchone():
            return False
        active = self._active_staff(conn)
        available = [staff for staff in configured_workers(self.settings)
                     if staff.id not in active and staff.team == request['team']
                     and staff_unavailability(staff, self.settings, request['type']) is None
                     and self._assignment_allows(conn, request, {'id': staff.id, 'manager_id': staff.manager_id})]
        if not available:
            return False
        self._request(conn, request['objective_id'], 'request.hire', self.settings.team, request['priority'],
                      payload={'workers': max(1, len(active)), 'routes': [{'team': request['team'], 'type': request['type']}]})
        self._event(conn, request['objective_id'], 'Configured staff activation requested for this exact work route.', 'delegation')
        return True

    def _queue_staffing(self, conn, request, workers, normalized):
        routes = list(dict.fromkeys((task[5], task[4]) for task in normalized))
        active = self._active_staff(conn)
        task_requests = conn.execute("SELECT * FROM requests WHERE objective_id=? AND task_id IS NOT NULL "
                                     "AND type NOT LIKE 'request.%' AND status='queued'", (request['objective_id'],)).fetchall()
        missing_route = any(not any(staff.id in active and staff.team == task_request['team']
                                    and not staff_unavailability(staff, self.settings, task_request['type'])
                                    and self._assignment_allows(conn, task_request, {'id': staff.id, 'manager_id': staff.manager_id})
                                    for staff in configured_workers(self.settings)) for task_request in task_requests)
        if workers > len(active) or (self.settings.roster is not None and missing_route):
            self._request(conn, request['objective_id'], 'request.hire', self.settings.team, request['priority'],
                          payload={'workers': workers, 'routes': [{'team': team, 'type': kind} for team, kind in routes]},
                          requester_id=request['agent_id'])

    def _finish_hire(self, conn, request, result):
        payload = json.loads(request['payload'])
        if 'managementProposal' in payload:
            self.apply_management(conn, request, payload['managementProposal'], request['agent_id'])
            self._record_response(conn, request, request['agent_id'], 'approved', 'Applied the exact authorized staffing/reorganization proposal.')
            return
        routes = [(route['team'], route['type']) for route in payload.get('routes', [])]
        active = self._active_staff(conn)
        needed = []
        workers = configured_workers(self.settings)
        for task_request in conn.execute("SELECT * FROM requests WHERE objective_id=? AND task_id IS NOT NULL "
                                         "AND type NOT LIKE 'request.%' AND status IN ('queued','pending_intervention')",
                                         (request['objective_id'],)):
            candidates = [staff for staff in workers if staff.team == task_request['team']
                          and not staff_unavailability(staff, self.settings, task_request['type'])
                          and self._assignment_allows(conn, task_request, {'id': staff.id, 'manager_id': staff.manager_id})]
            if not candidates:
                raise ValueError(self._route_reason(conn, task_request) + ' No eligible worker matches its persistent assignment.')
            if any(staff.id in active for staff in candidates):
                continue
            selected = next((staff for staff in candidates if staff in needed), candidates[0])
            if selected not in needed:
                needed.append(selected)
        new_staff = (*needed, *select_staff_activation(self.settings, active | {staff.id for staff in needed}, payload['workers'], routes))
        for staff in new_staff:
            conn.execute('UPDATE staff_state SET active=1 WHERE agent_id=?', (staff.id,))
        names = ', '.join(staff.name for staff in new_staff)
        text = f'Manager activated persistent staff: {names}.' if new_staff else 'The requested configured staff is already active.'
        self._event(conn, request['objective_id'], text, 'delegation', request['agent_id'])
