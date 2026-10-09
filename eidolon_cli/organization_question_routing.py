"""Forward-only management routing for newly raised routine questions.

Absence of a routing row means a retained legacy contract, never an implicit
migration. Leadership is an assignment, not permission to answer a question.
"""
from __future__ import annotations

import json
import time


QUESTION_ROUTING_SCHEMA = """
CREATE TABLE IF NOT EXISTS question_routes (
 request_id TEXT PRIMARY KEY REFERENCES requests(id), version INTEGER NOT NULL,
 assignment TEXT NOT NULL, leaders TEXT NOT NULL, cursor INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS question_route_receipts (
 request_id TEXT NOT NULL REFERENCES requests(id), position INTEGER NOT NULL,
 agent_id TEXT NOT NULL REFERENCES agents(id), outcome TEXT NOT NULL,
 text TEXT NOT NULL, created REAL NOT NULL,
 PRIMARY KEY(request_id,position));
"""


def route_view(conn, request_id):
    from eidolon_cli.organization_store import _iso
    route = conn.execute('SELECT * FROM question_routes WHERE request_id=?', (request_id,)).fetchone()
    if route is None:
        return None
    return {'version': route['version'], 'leaderIds': json.loads(route['leaders']),
            'cursor': route['cursor'], 'receipts': [
                {'agentId': row['agent_id'], 'outcome': row['outcome'], 'text': row['text'],
                 'createdAt': _iso(row['created'])}
                for row in conn.execute('SELECT * FROM question_route_receipts WHERE request_id=? ORDER BY position',
                                        (request_id,))]}


def assignment_route(conn, request):
    """Resolve the exact origin; malformed or ambiguous ownership fails closed."""
    contract = conn.execute('SELECT * FROM request_contracts WHERE request_id=?', (request['id'],)).fetchone()
    requester = conn.execute('SELECT * FROM agents WHERE id=?', (contract['requester_id'],)).fetchone()
    origin = request
    seen = set()
    while origin['type'] in {'request.question', 'request.decision', 'request.hire', 'request.permission'}:
        if origin['id'] in seen or len(seen) >= 25:
            return {}, []
        seen.add(origin['id'])
        parent = conn.execute('SELECT parent_request_id FROM request_contracts WHERE request_id=?',
                              (origin['id'],)).fetchone()
        origin = conn.execute('SELECT * FROM requests WHERE id=? AND objective_id=?',
                              (parent[0] if parent else None, request['objective_id'])).fetchone()
        if origin is None:
            return {}, []
    objective = conn.execute('SELECT * FROM objective_assignments WHERE objective_id=?',
                             (request['objective_id'],)).fetchone()
    if requester is None or objective is None:
        return {}, []
    from eidolon_cli.organization_packages import request_package
    package = request_package(conn, origin)
    task = conn.execute('SELECT * FROM task_assignments WHERE task_id=?', (origin['task_id'],)).fetchone()
    manager_id = task['manager_id'] if task else package['manager_id'] if package else objective['manager_id']
    manager = conn.execute('SELECT * FROM agents WHERE id=?', (manager_id,)).fetchone()
    executive = conn.execute('SELECT * FROM agents WHERE id=?', (objective['executive_id'],)).fetchone()
    binding = {'originId': origin['id'], 'requesterId': requester['id'], 'role': requester['role'],
               'requesterManagerId': requester['manager_id'], 'objective': dict(objective),
               'task': dict(task) if task else None,
               'package': {'id': package['id'], 'managerId': package['manager_id']} if package else None,
               'managerId': manager_id, 'managerExecutiveId': manager['manager_id'] if manager else None}
    if (manager is None or executive is None or manager['role'] != 'Manager'
            or executive['role'] != 'Executive' or manager['manager_id'] != executive['id']):
        return binding, []
    if requester['role'] == 'Worker' and task and requester['manager_id'] == manager_id:
        return binding, [manager_id, executive['id']]
    if requester['role'] == 'Manager' and requester['id'] == manager_id:
        return binding, [executive['id']]
    return binding, []


class OrganizationQuestionRoutingStore:
    @staticmethod
    def _initialize_question_route(conn, request_id):
        request = conn.execute('SELECT * FROM requests WHERE id=?', (request_id,)).fetchone()
        if request['type'] == 'request.question':
            binding, leaders = assignment_route(conn, request)
            conn.execute('INSERT INTO question_routes VALUES (?,1,?,?,0)',
                         (request_id, json.dumps(binding, sort_keys=True), json.dumps(leaders)))

    @staticmethod
    def _question_route_current(conn, request, route):
        binding, leaders = assignment_route(conn, request)
        return (route['version'] == 1 and binding == json.loads(route['assignment'])
                and leaders == json.loads(route['leaders']))

    def _question_candidates(self, conn, request, candidates):
        route = conn.execute('SELECT * FROM question_routes WHERE request_id=?', (request['id'],)).fetchone()
        if route is None:
            return candidates
        if not self._question_route_current(conn, request, route):
            return []
        eligible = {agent['id']: agent for agent in candidates}
        for identifier in json.loads(route['leaders'])[route['cursor']:]:
            if identifier in eligible:
                # A busy Manager retains priority; capacity is not a refusal.
                return [eligible[identifier]]
        return []

    @staticmethod
    def _question_receipt(conn, request, route, agent_id, outcome, text):
        conn.execute('INSERT INTO question_route_receipts VALUES (?,?,?,?,?,?)',
                     (request['id'], route['cursor'], agent_id, outcome, text, time.time()))
        conn.execute('UPDATE question_routes SET cursor=cursor+1 WHERE request_id=?', (request['id'],))

    def _stamp_question_route(self, conn, request, candidates):
        route = conn.execute('SELECT * FROM question_routes WHERE request_id=?', (request['id'],)).fetchone()
        if route is None or not self._question_route_current(conn, request, route):
            return
        selected = candidates[0]['id'] if candidates else None
        for identifier in json.loads(route['leaders'])[route['cursor']:]:
            if identifier == selected:
                break
            agent = conn.execute('SELECT * FROM agents WHERE id=?', (identifier,)).fetchone()
            reason = self._staff_reason(conn, agent, request['type'])
            reason = reason or 'Leader lacks the required explicit authority, question capability, team scope or continuation eligibility.'
            self._question_receipt(conn, request, route, identifier, 'ineligible', reason)
            route = conn.execute('SELECT * FROM question_routes WHERE request_id=?', (request['id'],)).fetchone()

    def _finish_routed_question(self, conn, request, result):
        """Return true when the result was handled without completing an answer."""
        route = conn.execute('SELECT * FROM question_routes WHERE request_id=?', (request['id'],)).fetchone()
        if route is None:
            return False
        candidates, _ = self._eligible(conn, request)
        if not candidates or candidates[0]['id'] != request['agent_id']:
            self._pending(conn, request, 'Question leadership or authority changed; owner response is required.')
            return True
        if 'cannot_answer' in result:
            from eidolon_cli.organization_store import _text
            if set(result) - {'cannot_answer', 'usage'}:
                raise ValueError('Cannot-answer must not also answer, deny, or create work')
            reason = _text(result['cannot_answer'], 'Cannot-answer reason', 2000)
            self._question_receipt(conn, request, route, request['agent_id'], 'cannot_answer', reason)
            candidates, _ = self._eligible(conn, request)
            self._stamp_question_route(conn, request, candidates)
            if candidates:
                conn.execute("UPDATE requests SET status='queued',agent_id=NULL,token=NULL,lease=NULL,reason=NULL WHERE id=?",
                             (request['id'],))
                self._event(conn, request['objective_id'], 'Question escalated to the next authorized leader.', 'question', request['agent_id'])
            else:
                self._pending(conn, request, 'No authorized next leader can answer; owner response is required.')
            return True
        if result.get('decision') == 'denied':
            from eidolon_cli.organization_store import _text
            text = _text(result.get('answer'), 'Denial reason', 12000)
            self._question_receipt(conn, request, route, request['agent_id'], 'denied', text)
            self._pending(conn, request, 'The leader denied this question; owner response is required.')
            return True
        if 'requests' in result or 'messages' in result:
            self._pending(conn, request, 'Question handler needs further information; owner response is required.')
            return True
        return False
