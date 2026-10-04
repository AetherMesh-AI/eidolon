"""Persisted activation of configured organization staff; never creates profiles."""
from __future__ import annotations

import json

from eidolon_cli.organization_roster import configured_workers, select_staff_activation, staff_unavailability


STAFF_SCHEMA = """
CREATE TABLE IF NOT EXISTS staff_state (
 agent_id TEXT PRIMARY KEY REFERENCES agents(id), active INTEGER NOT NULL,
 source TEXT NOT NULL, policy TEXT NOT NULL);
"""


class OrganizationStaffingStore:
    def _sync_staff(self, conn):
        mode = 'logical' if self.settings.roster is None else 'configured'
        configured = configured_workers(self.settings)
        for staff in configured:
            prior_agent = conn.execute('SELECT id FROM agents WHERE id=?', (staff.id,)).fetchone()
            prior = conn.execute('SELECT * FROM staff_state WHERE agent_id=?', (staff.id,)).fetchone()
            active = bool(prior['active']) if prior and prior['source'] == mode else (
                mode == 'logical' and (prior_agent is not None or staff.id == 'worker-1'))
            if not staff.enabled:
                active = False
            conn.execute('INSERT INTO agents VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET '
                         'name=excluded.name,team=excluded.team,accepts=excluded.accepts',
                         (staff.id, staff.name, 'Employee', 'manager', staff.team, json.dumps(staff.capabilities)))
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
        active = self._active_staff(conn)
        retained = [staff.id for staff in configured if staff.id in active][:self.settings.max_workers]
        for ident in active - set(retained):
            conn.execute('UPDATE staff_state SET active=0 WHERE agent_id=?', (ident,))
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
                         (ident, f'Reviewer · {team}', 'Employee', 'manager', team, '["request.review"]'))
            applier_id = 'control:apply:' + hashlib.sha256(team.encode()).hexdigest()[:16]
            conn.execute('INSERT OR IGNORE INTO agents VALUES (?,?,?,?,?,?)',
                         (applier_id, f'Workspace applier · {team}', 'Employee', 'manager', team, '["request.apply","request.validate"]'))
            conn.execute('UPDATE agents SET accepts=? WHERE id=?', ('["request.apply","request.validate"]', applier_id))

    def _staff(self, agent_id):
        return next((staff for staff in configured_workers(self.settings) if staff.id == agent_id), None)

    def _active_staff(self, conn):
        return {row[0] for row in conn.execute('SELECT agent_id FROM staff_state WHERE active=1')}

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
        return {'tools': tools, 'readRoots': list(self.settings.read_roots),
                'maxToolCalls': self.settings.max_tool_calls,
                'maxResultChars': self.settings.max_tool_result_chars}

    def _staffing_context(self):
        return [{'id': staff.id, 'name': staff.name, 'team': staff.team,
                 'capabilities': list(staff.capabilities), 'tools': list(staff.tool_grants),
                 'enabled': staff.enabled, 'availableReason': staff_unavailability(staff, self.settings)}
                for staff in configured_workers(self.settings)]

    def _route_reason(self, conn, request):
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
                     and staff_unavailability(staff, self.settings, request['type']) is None]
        if not available:
            return False
        self._request(conn, request['objective_id'], 'request.hire', self.settings.team, request['priority'],
                      payload={'workers': max(1, len(active)), 'routes': [{'team': request['team'], 'type': request['type']}]})
        self._event(conn, request['objective_id'], 'Configured staff activation requested for this exact work route.', 'delegation')
        return True

    def _queue_staffing(self, conn, request, workers, normalized):
        routes = list(dict.fromkeys((task[5], task[4]) for task in normalized))
        active = self._active_staff(conn)
        eligible_active = {staff.id for staff in configured_workers(self.settings) if staff.id in active
                           and any(staff.team == team and kind in staff.capabilities
                                   and not staff_unavailability(staff, self.settings, request_type=kind)
                                   for team, kind in routes)}
        # One hire request retains every requested route. Impossible staffing is
        # visible, never filled with a worker from a different team/capability.
        missing_route = any(not any(staff.id in active and staff.team == team and kind in staff.capabilities
                                   and not staff_unavailability(staff, self.settings, request_type=kind)
                                   for staff in configured_workers(self.settings)) for team, kind in routes)
        needs_hire = workers > len(active) if self.settings.roster is None else (workers > len(eligible_active) or missing_route)
        if needs_hire:
            self._request(conn, request['objective_id'], 'request.hire', self.settings.team, request['priority'],
                          payload={'workers': workers, 'routes': [{'team': team, 'type': kind} for team, kind in routes]})

    def _finish_hire(self, conn, request, result):
        payload = json.loads(request['payload'])
        routes = [(route['team'], route['type']) for route in payload.get('routes', [])]
        new_staff = select_staff_activation(self.settings, self._active_staff(conn), payload['workers'], routes)
        for staff in new_staff:
            conn.execute('UPDATE staff_state SET active=1 WHERE agent_id=?', (staff.id,))
        names = ', '.join(staff.name for staff in new_staff)
        text = f'Director activated configured staff: {names}.' if new_staff else 'The requested configured staff is already active.'
        self._event(conn, request['objective_id'], text, 'delegation', request['agent_id'])
