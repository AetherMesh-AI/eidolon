"""Durable agent-to-agent requests; an answer is context, never a tool grant.

The scheduler owns requester identity, links and authority. Model output may
propose a bounded question or staffing change but cannot forge its provenance.
"""
from __future__ import annotations

import hashlib
import json
import time

REQUEST_AUTHORITY = {
    'request.question': 'answer.question',
    'request.decision': 'answer.decision',
    'request.hire': 'staff.manage',
    'request.permission': 'human.permission',
}
REQUEST_SCHEMA = """
CREATE TABLE IF NOT EXISTS request_contracts (
 request_id TEXT PRIMARY KEY REFERENCES requests(id), requester_id TEXT NOT NULL REFERENCES agents(id),
 requested_outcome TEXT NOT NULL, required_authority TEXT NOT NULL,
 parent_request_id TEXT REFERENCES requests(id), dependencies TEXT NOT NULL,
 evidence_ids TEXT NOT NULL, depth INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS request_parent ON request_contracts(parent_request_id);
CREATE TABLE IF NOT EXISTS request_continuations (
 request_id TEXT PRIMARY KEY REFERENCES requests(id), agent_id TEXT NOT NULL REFERENCES agents(id));
CREATE TABLE IF NOT EXISTS request_responses (
 request_id TEXT PRIMARY KEY REFERENCES requests(id), responder_id TEXT NOT NULL REFERENCES agents(id),
 decision TEXT NOT NULL, text TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS request_response_receipts (
 idempotency_key TEXT PRIMARY KEY, input_hash TEXT NOT NULL,
 request_id TEXT NOT NULL REFERENCES requests(id), created REAL NOT NULL);
"""


def normalize_requests(value):
    """Shape validation is also used at the provider boundary; ledger validates links."""
    from eidolon_cli.organization_store import _text
    if not isinstance(value, list) or not 1 <= len(value) <= 4:
        raise ValueError('A stage may raise between 1 and 4 typed requests')
    result = []
    allowed = {'type', 'requestedOutcome', 'team', 'requiredAuthority', 'dependencyIds',
               'evidenceIds', 'managementProposal'}
    for item in value:
        if not isinstance(item, dict) or set(item) - allowed:
            raise ValueError('Typed requests contain unsupported fields; requester identity is backend-owned')
        kind = item.get('type')
        if not isinstance(kind, str) or kind not in REQUEST_AUTHORITY:
            raise ValueError('Typed request must be question, decision, hire or permission')
        outcome = _text(item.get('requestedOutcome'), 'Requested outcome', 6000)
        authority = item.get('requiredAuthority', REQUEST_AUTHORITY[kind])
        if authority != REQUEST_AUTHORITY[kind]:
            raise ValueError('Typed request authority cannot be weakened or replaced')
        normalized = {'type': kind, 'requestedOutcome': outcome, 'requiredAuthority': authority}
        if 'team' in item:
            normalized['team'] = _text(item['team'], 'Request team', 64)
        for field in ('dependencyIds', 'evidenceIds'):
            ids = item.get(field, [])
            if (not isinstance(ids, list) or len(ids) > 12 or any(
                    not isinstance(ident, str) or not 1 <= len(ident) <= 128 for ident in ids)
                    or len(set(ids)) != len(ids)):
                raise ValueError(f'{field} must be a bounded unique list of IDs')
            normalized[field] = list(ids)
        proposal = item.get('managementProposal')
        if kind == 'request.hire':
            if (not isinstance(proposal, dict) or set(proposal) - {'members', 'transfers'}
                    or not isinstance(proposal.get('members', []), list)
                    or not isinstance(proposal.get('transfers', []), list)
                    or not (proposal.get('members') or proposal.get('transfers'))
                    or len(proposal.get('members', [])) > 8 or len(proposal.get('transfers', [])) > 8):
                raise ValueError('Hire requests require a bounded explicit members/transfers proposal')
            # Full role/provider/tool/scope validation occurs under the handler's
            # authority at application time. Invalid proposals stay visible.
            if len(json.dumps(proposal, ensure_ascii=False)) > 24000:
                raise ValueError('Management proposal exceeds its bounded size')
            normalized['managementProposal'] = proposal
        elif proposal is not None:
            raise ValueError('Only a hire request may propose organization changes')
        result.append(normalized)
    return result


def canonical_management_proposal(proposal, team):
    """Show the exact defaults to a human before accepting the stored proposal."""
    defaults = {'Worker': [], 'Manager': ['request.plan', 'request.integrate', 'request.hire'],
                'Executive': ['request.decompose', 'request.accept']}
    leaders = {'Worker': 'manager', 'Manager': 'executive', 'Executive': 'owner'}
    members = []
    for member in proposal.get('members', []):
        if not isinstance(member, dict):
            raise ValueError('Management members must be objects')
        role = member.get('role', 'Worker')
        if not isinstance(role, str):
            raise ValueError('Management member role must be text')
        members.append({'team': team, 'role': role, 'manager_id': leaders.get(role, ''),
                        'capabilities': defaults.get(role, []), 'enabled': True,
                        'provider': None, 'model': None, 'tool_grants': [],
                        'responsibilities': [], 'purpose': '', 'scope': '',
                        'authority': [], 'managed_teams': [], **member})
    transfers = []
    for transfer in proposal.get('transfers', []):
        if not isinstance(transfer, dict):
            raise ValueError('Management transfers must be objects')
        transfers.append({'taskIds': [], 'workPackageIds': [], 'includeMemory': False, **transfer})
    text_fields = {'id': 64, 'name': 100, 'team': 64, 'role': 20, 'manager_id': 64,
                   'purpose': 3000, 'scope': 3000}
    array_fields = {'capabilities': 16, 'tool_grants': 8, 'responsibilities': 12,
                    'authority': 3, 'managed_teams': 64}
    permitted = {*text_fields, *array_fields, 'enabled', 'provider', 'model'}
    for member in members:
        if set(member) - permitted:
            raise ValueError('Management members contain unsupported fields')
        if any(not isinstance(member.get(field), str) or len(member[field]) > maximum
               for field, maximum in text_fields.items()):
            raise ValueError('Management member identity, scope and purpose must be bounded text')
        if any(not isinstance(member.get(field), list) or len(member[field]) > maximum
               or any(not isinstance(item, str) or len(item) > 500 for item in member[field])
               for field, maximum in array_fields.items()):
            raise ValueError('Management member routes, authority and duties must be bounded text lists')
        if type(member['enabled']) is not bool or any(
                member[field] is not None and (not isinstance(member[field], str) or len(member[field]) > 300)
                for field in ('provider', 'model')):
            raise ValueError('Management member execution fields have invalid types')
    selected_packages = set()
    for transfer in transfers:
        if set(transfer) - {'fromAgentId', 'toAgentId', 'taskIds', 'objectiveIds', 'workPackageIds', 'includeMemory'}:
            raise ValueError('Management transfers contain unsupported fields')
        if (any(not isinstance(transfer.get(field), str) or not transfer[field]
                or len(transfer[field]) > 64 for field in ('fromAgentId', 'toAgentId'))
                or not isinstance(transfer['taskIds'], list) or len(transfer['taskIds']) > 100
                or any(not isinstance(item, str) or not item or len(item) > 128 for item in transfer['taskIds'])
                or not isinstance(transfer.get('objectiveIds', []), list)
                or len(transfer.get('objectiveIds', [])) > 100
                or any(not isinstance(item, str) or not item or len(item) > 128 for item in transfer.get('objectiveIds', []))
                or not isinstance(transfer['workPackageIds'], list) or len(transfer['workPackageIds']) > 100
                or any(not isinstance(item, str) or not item or len(item) > 128 for item in transfer['workPackageIds'])
                or type(transfer['includeMemory']) is not bool):
            raise ValueError('Management transfers require exact typed identities, task/objective/work package IDs and memory choice')
        packages = transfer['workPackageIds']
        if len(set(packages)) != len(packages) or selected_packages.intersection(packages):
            raise ValueError('Management transfers must select each work package only once')
        selected_packages.update(packages)
        if not (transfer['taskIds'] or transfer.get('objectiveIds') or packages or transfer['includeMemory']):
            raise ValueError('Management transfers must select tasks, objectives, work packages or memory')
    return {'members': members, 'transfers': transfers}


def request_contract_view(conn, request):
    row = conn.execute('SELECT * FROM request_contracts WHERE request_id=?', (request['id'],)).fetchone()
    if row is None:
        return {}
    from eidolon_cli.organization_store import _iso
    answer = conn.execute('SELECT * FROM request_responses WHERE request_id=?', (request['id'],)).fetchone()
    payload = json.loads(request['payload'])
    return {'requesterId': row['requester_id'], 'requestedOutcome': row['requested_outcome'],
            'requiredAuthority': row['required_authority'], 'parentRequestId': row['parent_request_id'],
            'dependencyIds': json.loads(row['dependencies']), 'evidenceIds': json.loads(row['evidence_ids']),
            'managementProposal': payload.get('managementProposal'),
            'response': ({'responderId': answer['responder_id'], 'decision': answer['decision'],
                          'text': answer['text'], 'createdAt': _iso(answer['created'])} if answer else None)}


def response_options(conn, request):
    row = conn.execute('SELECT parent_request_id FROM request_contracts WHERE request_id=?', (request['id'],)).fetchone()
    if row is None or row['parent_request_id'] is None or request['status'] != 'pending_intervention':
        return None
    if request['type'] in {'request.question', 'request.decision'}:
        return [{'action': 'answer_request', 'label': 'Answer request', 'requiresText': True, 'requiresEvidence': False}]
    return [{'action': 'approve_request', 'label': 'Approve exact request', 'requiresText': True, 'requiresEvidence': False},
            {'action': 'deny_request', 'label': 'Deny request', 'requiresText': True, 'requiresEvidence': False}]


class OrganizationRequestStore:
    def _migrate_request_contracts(self, conn):
        for row in conn.execute('SELECT r.* FROM requests r LEFT JOIN request_contracts c ON c.request_id=r.id WHERE c.request_id IS NULL').fetchall():
            self._insert_request_contract(conn, row['id'])

    @staticmethod
    def _insert_request_contract(conn, request_id, *, requester_id=None, requested_outcome=None,
                                 required_authority=None, parent_request_id=None, dependencies=(), depth=0):
        request = conn.execute('SELECT * FROM requests WHERE id=?', (request_id,)).fetchone()
        payload = json.loads(request['payload'])
        assignment = conn.execute('SELECT * FROM objective_assignments WHERE objective_id=?', (request['objective_id'],)).fetchone()
        requester = requester_id or ('owner' if request['type'] in {'request.decompose', 'request.plan'} else
                                    assignment['manager_id'] if assignment else 'manager')
        task = conn.execute('SELECT title FROM tasks WHERE id=?', (request['task_id'],)).fetchone()
        outcome = requested_outcome or (task['title'] if task else request['type'].replace('.', ': '))
        conn.execute('INSERT INTO request_contracts VALUES (?,?,?,?,?,?,?,?)',
                     (request_id, requester, outcome, required_authority or 'route:' + request['type'],
                      parent_request_id, json.dumps(list(dependencies)), json.dumps(payload.get('evidenceIds', [])), depth))

    def _raise_requests(self, conn, parent, specifications):
        proposals = normalize_requests(specifications)
        if conn.execute("SELECT 1 FROM tool_receipts WHERE request_id=? AND attempt=? "
                        "AND status IN ('running','unknown','blocked')",
                        (parent['id'], parent['execution_count'])).fetchone():
            raise ValueError('Unresolved or blocked tool calls require outcome review before this stage can pause')
        contract = conn.execute('SELECT * FROM request_contracts WHERE request_id=?', (parent['id'],)).fetchone()
        if len(proposals) > self.settings.max_requests_per_stage:
            raise ValueError('Stage typed-request capacity reached')
        if contract['depth'] >= self.settings.max_request_depth:
            raise ValueError('Request dependency depth limit reached; human intervention is required')
        count = conn.execute('SELECT count(*) FROM request_contracts c JOIN requests r ON r.id=c.request_id WHERE r.objective_id=? AND c.parent_request_id IS NOT NULL', (parent['objective_id'],)).fetchone()[0]
        if count + len(proposals) > 24:
            raise ValueError('Objective typed-request capacity reached; human intervention is required')
        from eidolon_cli.organization_loop import reject_repeated_requests
        for item in proposals:
            if 'managementProposal' in item:
                item['managementProposal'] = canonical_management_proposal(
                    item['managementProposal'], item.get('team', parent['team']))
        ancestors = {parent['id']}
        ancestor = contract['parent_request_id']
        while ancestor:
            if ancestor in ancestors:
                raise ValueError('Request ancestry contains a cycle; owner intervention is required')
            ancestors.add(ancestor)
            ancestor = conn.execute('SELECT parent_request_id FROM request_contracts WHERE request_id=?', (ancestor,)).fetchone()[0]
        reject_repeated_requests(conn, parent, proposals, ancestors)
        for item in proposals:
            for dependency in item['dependencyIds']:
                target = conn.execute('SELECT r.objective_id,r.status,c.parent_request_id FROM requests r JOIN request_contracts c ON c.request_id=r.id WHERE r.id=?', (dependency,)).fetchone()
                if target is None or target['objective_id'] != parent['objective_id'] or target['status'] == 'cancelled' or self._dependency_reaches(conn, dependency, ancestors):
                    raise ValueError('Request dependencies must be prior acyclic non-ancestor requests in this objective')
            payload = {'evidenceIds': item['evidenceIds']}
            if 'managementProposal' in item:
                payload['managementProposal'] = item['managementProposal']
            ident = self._request(conn, parent['objective_id'], item['type'], item.get('team', parent['team']),
                                  parent['priority'], payload=payload)
            conn.execute('DELETE FROM request_contracts WHERE request_id=?', (ident,))
            self._insert_request_contract(conn, ident, requester_id=parent['agent_id'],
                requested_outcome=item['requestedOutcome'], required_authority=item['requiredAuthority'],
                parent_request_id=parent['id'], dependencies=item['dependencyIds'], depth=contract['depth'] + 1)
            self._event(conn, parent['objective_id'], item['requestedOutcome'], 'question', parent['agent_id'])
        conn.execute('INSERT INTO request_continuations VALUES (?,?) ON CONFLICT(request_id) DO UPDATE SET agent_id=excluded.agent_id', (parent['id'], parent['agent_id']))
        from eidolon_cli.organization_receipts import fence_receipts
        fence_receipts(conn, parent['id'], 'Stage paused awaiting its linked request responses.')
        conn.execute("UPDATE requests SET status='waiting_response',token=NULL,lease=NULL,reason='Awaiting linked request responses',attempts=MAX(0,attempts-1) WHERE id=?", (parent['id'],))
        if parent['task_id']:
            conn.execute("UPDATE tasks SET status='blocked' WHERE id=?", (parent['task_id'],))

    @staticmethod
    def _dependency_reaches(conn, start, targets):
        todo, visited = [start], set()
        while todo:
            identifier = todo.pop()
            if identifier in targets:
                return True
            if identifier in visited:
                continue
            visited.add(identifier)
            request = conn.execute('SELECT status,task_id FROM requests WHERE id=?', (identifier,)).fetchone()
            if request is None or request['status'] in {'completed', 'cancelled'}:
                continue
            if request['task_id']:
                from eidolon_cli.organization_packages import package_dependency_requests
                todo.extend(package_dependency_requests(conn, request['task_id']))
                task = conn.execute('SELECT dependencies FROM tasks WHERE id=?', (request['task_id'],)).fetchone()
                from eidolon_cli.organization_coordination import coordination_view
                blockers = coordination_view(conn, request['task_id'])['blockingTaskIds']
                for dependency in set(json.loads(task['dependencies']) + blockers):
                    todo.extend(row[0] for row in conn.execute("SELECT id FROM requests WHERE task_id=? AND status NOT IN ('completed','cancelled')", (dependency,)))
            contract = conn.execute('SELECT dependencies FROM request_contracts WHERE request_id=?', (identifier,)).fetchone()
            if contract:
                todo.extend(json.loads(contract[0]))
            todo.extend(row[0] for row in conn.execute("SELECT c.request_id FROM request_contracts c JOIN requests r ON r.id=c.request_id WHERE c.parent_request_id=? AND r.status NOT IN ('completed','cancelled')", (identifier,)))
        return False

    @staticmethod
    def _continuation_allows(conn, request, agent):
        continuation = conn.execute('SELECT agent_id FROM request_continuations WHERE request_id=?', (request['id'],)).fetchone()
        return continuation is None or continuation[0] == agent['id']

    def _typed_eligible(self, conn, request, agent):
        contract = conn.execute('SELECT * FROM request_contracts WHERE request_id=?', (request['id'],)).fetchone()
        if contract is None or contract['parent_request_id'] is None:
            return None
        if request['type'] == 'request.permission' or agent['id'] == contract['requester_id']:
            return False
        staff = self._staff(agent['id'])
        if staff is None or not staff.enabled or contract['required_authority'] not in staff.authority:
            return False
        if request['type'] == 'request.hire':
            return request['team'] in {staff.team, *staff.managed_teams} or '*' in staff.managed_teams
        return agent['team'] == request['team']

    @staticmethod
    def _request_dependencies_ready(conn, request):
        row = conn.execute('SELECT dependencies FROM request_contracts WHERE request_id=?', (request['id'],)).fetchone()
        return row is None or all(conn.execute("SELECT status FROM requests WHERE id=?", (ident,)).fetchone()[0] == 'completed'
                                  for ident in json.loads(row[0]))

    def refresh_unhandled_requests(self):
        """A new configured handler may answer a never-executed request safely."""
        with self._write() as conn:
            self._require_current_policy(conn)
            for request in conn.execute("SELECT r.* FROM requests r JOIN request_contracts c ON c.request_id=r.id JOIN objectives o ON o.id=r.objective_id WHERE r.status='pending_intervention' AND r.attempts=0 AND c.parent_request_id IS NOT NULL AND o.cancelled=0").fetchall():
                candidates, _ = self._eligible(conn, request)
                if candidates:
                    conn.execute("UPDATE requests SET status='queued',reason=NULL,available=0 WHERE id=?", (request['id'],))

    def _typed_context(self, conn, request):
        contract = request_contract_view(conn, request)
        replies = []
        for row in conn.execute('SELECT r.* FROM requests r JOIN request_contracts c ON c.request_id=r.id JOIN request_responses a ON a.request_id=r.id WHERE c.parent_request_id=? ORDER BY a.created,r.id', (request['id'],)):
            replies.append({'id': row['id'], 'type': row['type'], 'team': row['team'], **request_contract_view(conn, row)})
        # Every downstream assignment needs the exact clarification, even when
        # its planner did not repeat it in a task description or memory summary.
        # The existing per-objective typed-request limit bounds this collection.
        clarifications = []
        current_round = conn.execute('SELECT round FROM objective_control WHERE objective_id=?',
                                     (request['objective_id'],)).fetchone()[0]
        for row in conn.execute(
                "SELECT r.*,p.type AS parent_type,p.status AS parent_status,p.task_id AS parent_task_id,"
                "p.payload AS parent_payload,t.round AS task_round FROM requests r "
                "JOIN request_contracts c ON c.request_id=r.id "
                "JOIN request_responses a ON a.request_id=r.id "
                "JOIN requests p ON p.id=c.parent_request_id "
                "LEFT JOIN objective_task_rounds t ON t.task_id=p.task_id "
                "WHERE r.objective_id=? AND r.type IN ('request.question','request.decision','request.permission') "
                "ORDER BY a.created,r.id", (request['objective_id'],)):
            record = request_contract_view(conn, row)
            origin = row
            # Nested questions inherit their originating assignment's round,
            # not round zero merely because a question has no task of its own.
            ancestor = record['parentRequestId']
            while origin['parent_type'] in REQUEST_AUTHORITY:
                origin = conn.execute(
                    'SELECT p.type AS parent_type,p.payload AS parent_payload,t.round AS task_round,'
                    'c.parent_request_id FROM request_contracts c JOIN requests p ON p.id=c.parent_request_id '
                    'LEFT JOIN objective_task_rounds t ON t.task_id=p.task_id WHERE c.request_id=?',
                    (ancestor,)).fetchone()
                if origin is None:
                    raise ValueError('Clarification origin is missing')
                ancestor = origin['parent_request_id']
            origin_payload = json.loads(origin['parent_payload'])
            if origin['parent_type'] == 'request.test_review':
                run = conn.execute('SELECT round FROM project_run_starts WHERE id=? AND objective_id=?',
                                   (origin_payload.get('runId'), request['objective_id'])).fetchone()
                if run is None:
                    raise ValueError('Clarification project review origin is missing')
                round_number = run['round']
            else:
                round_number = (origin['task_round'] if origin['task_round'] is not None else
                                origin_payload.get('round', 0))
            clarifications.append({
                'id': row['id'], 'type': row['type'], 'team': row['team'],
                **{key: record[key] for key in ('requesterId', 'requestedOutcome', 'requiredAuthority',
                                               'parentRequestId', 'response')},
                'parentRequestType': row['parent_type'], 'parentStatus': row['parent_status'],
                'taskId': row['parent_task_id'], 'round': round_number, 'originRequestId': ancestor,
                'historical': round_number != current_round or row['parent_status'] == 'cancelled',
            })
        return {'requestContract': contract, 'requestResponses': replies,
                'objectiveClarifications': clarifications}

    def _finish_response(self, conn, request, result):
        from eidolon_cli.organization_store import _text
        if request['type'] not in {'request.question', 'request.decision'}:
            raise ValueError('Only eligible question and decision handlers may answer automatically')
        agent = conn.execute('SELECT * FROM agents WHERE id=?', (request['agent_id'],)).fetchone()
        if not self._typed_eligible(conn, request, agent):
            raise ValueError('Responder lacks exact request authority and team scope')
        text = _text(result.get('answer'), 'Request answer', 12000)
        decision = result.get('decision', 'answered')
        if not isinstance(decision, str) or decision not in {'answered', 'approved', 'denied'} or (request['type'] == 'request.question' and decision != 'answered'):
            raise ValueError('Unsupported typed response decision')
        self._record_response(conn, request, request['agent_id'], decision, text)

    def _record_response(self, conn, request, responder_id, decision, text):
        conn.execute('INSERT INTO request_responses VALUES (?,?,?,?,?)',
                     (request['id'], responder_id, decision, text, time.time()))
        self._event(conn, request['objective_id'], f"Response recorded for {request['type']}: {decision}.", 'decision', responder_id)

    def _resume_answered_parent(self, conn, request):
        contract = conn.execute('SELECT parent_request_id FROM request_contracts WHERE request_id=?', (request['id'],)).fetchone()
        if contract is None or contract[0] is None:
            return
        parent_id = contract[0]
        if conn.execute("SELECT 1 FROM requests r JOIN request_contracts c ON c.request_id=r.id WHERE c.parent_request_id=? AND r.status!='completed'", (parent_id,)).fetchone():
            return
        parent = conn.execute("SELECT * FROM requests WHERE id=? AND status='waiting_response'", (parent_id,)).fetchone()
        if parent:
            conn.execute("UPDATE requests SET status='queued',reason=NULL,available=0,agent_id=NULL WHERE id=?", (parent_id,))
            if parent['task_id']:
                conn.execute("UPDATE tasks SET status='queued' WHERE id=?", (parent['task_id'],))
            self._event(conn, parent['objective_id'], 'Linked responses are durable; the affected assignment is ready to resume.', 'planning')

    @staticmethod
    def _response_input(request_id, text, decision, idempotency_key):
        from eidolon_cli.organization_store import _text
        identifier = _text(request_id, 'Request ID', 128)
        key = _text(idempotency_key, 'Response idempotency key', 128)
        answer = _text(text, 'Request answer', 12000)
        if not isinstance(decision, str) or decision not in {'answered', 'approved', 'denied'}:
            raise ValueError('Response decision must be answered, approved or denied')
        digest = hashlib.sha256(json.dumps([identifier, answer, decision]).encode()).hexdigest()
        return identifier, answer, key, digest

    def response_recorded(self, request_id, text, decision, *, idempotency_key):
        _, _, key, digest = self._response_input(request_id, text, decision, idempotency_key)
        with self._connect() as conn:
            receipt = conn.execute('SELECT input_hash FROM request_response_receipts WHERE idempotency_key=?', (key,)).fetchone()
            if receipt and receipt[0] != digest:
                raise ValueError('Response idempotency key belongs to different input')
            return receipt is not None

    def respond(self, request_id, text, decision='answered', *, idempotency_key):
        identifier, answer, key, digest = self._response_input(request_id, text, decision, idempotency_key)
        with self._write() as conn:
            self._require_current_policy(conn)
            receipt = conn.execute('SELECT input_hash FROM request_response_receipts WHERE idempotency_key=?', (key,)).fetchone()
            if receipt:
                if receipt[0] != digest:
                    raise ValueError('Response idempotency key belongs to different input')
                return False
            request = conn.execute('SELECT * FROM requests WHERE id=?', (identifier,)).fetchone()
            if request is not None:
                from eidolon_cli.organization_budget import budget_reason
                reason = budget_reason(conn, request['objective_id'], self.settings)
                if reason:
                    raise ValueError(reason)
            if request is None or response_options(conn, request) is None:
                raise ValueError('This typed request is not awaiting an owner response')
            if conn.execute('SELECT cancelled FROM objectives WHERE id=?', (request['objective_id'],)).fetchone()[0]:
                raise ValueError('Cancelled work cannot receive a new answer')
            from eidolon_cli.organization_owner import resolution_count
            if resolution_count(conn, request['objective_id']) >= self.settings.max_owner_resolutions:
                raise ValueError('Owner response capacity reached; create a revised objective')
            if request['type'] in {'request.hire', 'request.permission'} and decision == 'answered':
                raise ValueError('Staffing and permission requests require explicit approval or denial')
            if request['type'] == 'request.question' and decision != 'answered':
                raise ValueError('A question requires an answer')
            if request['type'] == 'request.hire' and decision == 'approved':
                self.apply_management(conn, request, json.loads(request['payload'])['managementProposal'], 'owner')
            # Permission approval deliberately records only the exact decision.
            # Credentials, filesystem roots and tool grants remain unchanged.
            self._record_response(conn, request, 'owner', decision, answer)
            conn.execute('INSERT INTO request_response_receipts VALUES (?,?,?,?)', (key, digest, identifier, time.time()))
            conn.execute("UPDATE requests SET status='completed',token=NULL,lease=NULL,reason=NULL WHERE id=?", (identifier,))
            self._resume_answered_parent(conn, request)
            self._maybe_integrate(conn, request['objective_id'])
            return True
