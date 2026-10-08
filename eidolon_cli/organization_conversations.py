"""Selected-text peer conversations, separate from authority-bearing requests.

All sends are accepted under an owned stage lease. A conversation never changes
assignment, tool authority, acceptance evidence, or another identity's memory.
"""
from __future__ import annotations

import hashlib
import json
import time

CONVERSATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS internal_threads (
 id TEXT PRIMARY KEY, objective_id TEXT NOT NULL REFERENCES objectives(id),
 task_id TEXT REFERENCES tasks(id), project_id TEXT, subject TEXT NOT NULL,
 participants TEXT NOT NULL, created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS internal_objective_threads ON internal_threads(objective_id,created,id);
CREATE TABLE IF NOT EXISTS internal_messages (
 id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES internal_threads(id),
 sender_id TEXT NOT NULL REFERENCES agents(id), recipient_id TEXT NOT NULL REFERENCES agents(id),
 body TEXT NOT NULL, created REAL NOT NULL, read_at REAL,
 reply_to_id TEXT REFERENCES internal_messages(id),
 delivery_request_id TEXT UNIQUE REFERENCES requests(id),
 parent_request_id TEXT NOT NULL REFERENCES requests(id), input_hash TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS internal_one_reply ON internal_messages(reply_to_id)
 WHERE reply_to_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS internal_dedup ON internal_messages(parent_request_id,input_hash)
 WHERE reply_to_id IS NULL;
CREATE INDEX IF NOT EXISTS internal_thread_messages ON internal_messages(thread_id,created,id);
CREATE INDEX IF NOT EXISTS internal_reply_inbox ON internal_messages(recipient_id,parent_request_id)
 WHERE reply_to_id IS NOT NULL;
"""


def normalize_messages(value):
    from eidolon_cli.organization_store import _text
    if not isinstance(value, list) or not 1 <= len(value) <= 4:
        raise ValueError('A stage may send between 1 and 4 internal messages')
    result = []
    for item in value:
        if not isinstance(item, dict) or set(item) - {'recipientId', 'subject', 'body', 'threadId'}:
            raise ValueError('Internal messages accept only recipientId, subject, body and optional threadId; sender and links are backend-owned')
        for key in ('recipientId', 'threadId'):
            if key in item and (not isinstance(item[key], str) or item[key] != item[key].strip() or any(ord(char) < 32 for char in item[key])):
                raise ValueError('Internal routing requires exact canonical IDs without surrounding whitespace')
        record = {key: _text(item.get(key), key, maximum) for key, maximum in (
            ('recipientId', 128), ('subject', 200), ('body', 6000))}
        if 'threadId' in item:
            record['threadId'] = _text(item['threadId'], 'threadId', 128)
        result.append(record)
    if len({json.dumps(row, sort_keys=True) for row in result}) != len(result):
        raise ValueError('Duplicate internal messages are not allowed')
    return result


def objective_participants(conn, objective_id):
    ids = set()
    row = conn.execute('SELECT executive_id,manager_id FROM objective_assignments WHERE objective_id=?', (objective_id,)).fetchone()
    if row:
        ids.update(row)
    for row in conn.execute('SELECT a.agent_id,a.manager_id,a.assigned_by_id FROM task_assignments a JOIN tasks t ON t.id=a.task_id WHERE t.objective_id=?', (objective_id,)):
        ids.update(value for value in row if value)
    ids.update(row[0] for row in conn.execute('SELECT manager_id FROM manager_work_packages WHERE objective_id=?', (objective_id,)))
    ids.update(row[0] for row in conn.execute("SELECT DISTINCT agent_id FROM requests WHERE objective_id=? AND type!='request.message' AND agent_id IS NOT NULL", (objective_id,)))
    return ids


def conversation_view(conn, thread, *, agent_id=None):
    """Owner sees provenance; agents see only participant threads and scoped links."""
    from eidolon_cli.organization_store import _iso
    participants = json.loads(thread['participants'])
    if agent_id is not None and agent_id not in {row['id'] for row in participants}:
        raise ValueError('Conversation belongs to other participants')
    messages = []
    waiting = None
    status = 'answered'
    delivery_states = []
    for row in conn.execute('SELECT * FROM internal_messages WHERE thread_id=? ORDER BY created,rowid', (thread['id'],)):
        messages.append({'id': row['id'], 'senderId': row['sender_id'], 'recipientId': row['recipient_id'],
                         'body': row['body'], 'createdAt': _iso(row['created']),
                         'readAt': _iso(row['read_at']), 'replyToId': row['reply_to_id']})
        if row['delivery_request_id']:
            request = conn.execute('SELECT status FROM requests WHERE id=?', (row['delivery_request_id'],)).fetchone()
            delivery_states.append((request['status'], row['recipient_id']))
    pending = [(state, recipient) for state, recipient in delivery_states if state not in {'completed', 'cancelled'}]
    if pending:
        intervention = next((item for item in pending if item[0] == 'pending_intervention'), None)
        status, waiting = ('needs_input', intervention[1]) if intervention else ('waiting_reply', pending[0][1])
    elif any(state == 'cancelled' for state, _ in delivery_states):
        status = 'cancelled'
    visible = agent_id is None or agent_id in objective_participants(conn, thread['objective_id'])
    # Task/project metadata is never disclosed to a newly contacted colleague.
    task_visible = agent_id is None
    if visible and agent_id is not None and thread['task_id']:
        assignment = conn.execute('SELECT agent_id,manager_id,assigned_by_id FROM task_assignments WHERE task_id=?', (thread['task_id'],)).fetchone()
        task_visible = assignment is not None and agent_id in set(assignment)
    return {'id': thread['id'], 'subject': thread['subject'],
            'objectiveId': thread['objective_id'] if visible else None,
            'taskId': thread['task_id'] if task_visible else None,
            'projectId': thread['project_id'] if task_visible else None,
            'participants': participants, 'messages': messages,
            'status': status, 'waitingAgentId': waiting}


def conversations_view(conn, objective_ids):
    return [conversation_view(conn, thread) for objective_id in sorted(objective_ids)
            for thread in conn.execute('SELECT * FROM internal_threads WHERE objective_id=? ORDER BY created,id', (objective_id,))]


class OrganizationConversationStore:
    def _communication_staff(self, conn, identifier):
        staff = self._staff(identifier)
        if staff is not None:
            return staff
        row = conn.execute("SELECT * FROM agents WHERE id IN ('manager','executive','director') AND id=?", (identifier,)).fetchone()
        if row is None:
            return None
        from eidolon_cli.organization_roster import OrganizationStaff
        return OrganizationStaff(row['id'], row['name'], row['team'], tuple(json.loads(row['accepts'])),
                                 role=row['role'], manager_id=row['manager_id'])

    def _communication_reason(self, conn, sender_id, recipient_id, objective_id):
        from eidolon_cli.organization_roster import staff_unavailability
        if self.settings.communication_scope == 'disabled':
            return 'Internal communication is disabled by organization policy.'
        if sender_id == recipient_id:
            return 'Internal messages require another persistent identity.'
        members = []
        for identifier in (sender_id, recipient_id):
            staff = self._communication_staff(conn, identifier)
            state = conn.execute('SELECT active FROM staff_state WHERE agent_id=?', (identifier,)).fetchone()
            if staff is None or not staff.enabled or (state is not None and not state['active']) or (state is None and identifier not in {'manager', 'executive', 'director'}):
                return 'The sender or recipient is inactive, removed, or unavailable.'
            reason = staff_unavailability(staff, self.settings)
            if reason:
                return reason
            members.append(staff)
        sender, recipient = members
        if sender.team == recipient.team:
            return None
        if self.settings.communication_scope == 'same_team':
            return 'Communication policy permits only the same department.'
        if ({sender_id, recipient_id} <= objective_participants(conn, objective_id)
                or (sender.role in {'Manager', 'Executive'} and recipient.team in {*sender.managed_teams})
                or (recipient.role in {'Manager', 'Executive'} and sender.team in {*recipient.managed_teams})
                or (sender.role in {'Manager', 'Executive'} and '*' in sender.managed_teams)
                or (recipient.role in {'Manager', 'Executive'} and '*' in recipient.managed_teams)):
            return None
        return 'Cross-department messaging requires existing shared objective participation or explicit managed-team scope.'

    def _messaging_directory(self, conn, request):
        # Public routing metadata only; never other people's purpose/memory/tool grants.
        return [{'id': staff.id, 'name': staff.name, 'team': staff.team, 'role': staff.role}
                for row in conn.execute('SELECT id FROM agents')
                if (staff := self._communication_staff(conn, row['id'])) is not None
                if not self._communication_reason(conn, request['agent_id'], staff.id, request['objective_id'])]

    def _internal_context(self, conn, request):
        threads = [conversation_view(conn, thread, agent_id=request['agent_id'])
                   for thread in conn.execute('SELECT * FROM internal_threads WHERE objective_id=? ORDER BY created,id', (request['objective_id'],))
                   if request['agent_id'] in {item['id'] for item in json.loads(thread['participants'])}]
        return {'internalConversations': threads, 'messagingDirectory': self._messaging_directory(conn, request)}

    def _send_internal_messages(self, conn, parent, value):
        from eidolon_cli.organization_store import _id
        from eidolon_cli.organization_projects import task_project
        from eidolon_cli.organization_receipts import fence_receipts
        if parent['type'] == 'request.message':
            raise ValueError('Message delivery may reply once; it cannot start recursive conversations')
        proposals = normalize_messages(value)
        if conn.execute("SELECT 1 FROM tool_receipts WHERE request_id=? AND attempt=? AND status IN ('running','unknown','blocked')", (parent['id'], parent['execution_count'])).fetchone():
            raise ValueError('Unresolved tool execution must be reviewed before waiting for a message')
        count = conn.execute('SELECT count(*) FROM internal_messages m JOIN internal_threads t ON t.id=m.thread_id WHERE t.objective_id=? AND m.reply_to_id IS NULL', (parent['objective_id'],)).fetchone()[0]
        stage_count = conn.execute('SELECT count(*) FROM internal_messages WHERE parent_request_id=? AND reply_to_id IS NULL', (parent['id'],)).fetchone()[0]
        if stage_count + len(proposals) > 4:
            raise ValueError('Stage internal-message budget exhausted; use retained answers or a formal request')
        if count + len(proposals) > 24:
            raise ValueError('Objective internal-message budget exhausted; human intervention is required')
        for proposal in proposals:
            reason = self._communication_reason(conn, parent['agent_id'], proposal['recipientId'], parent['objective_id'])
            if reason:
                raise ValueError(reason)
            digest = hashlib.sha256(json.dumps(proposal, sort_keys=True).encode()).hexdigest()
            if conn.execute('SELECT 1 FROM internal_messages WHERE parent_request_id=? AND input_hash=? AND reply_to_id IS NULL', (parent['id'], digest)).fetchone():
                raise ValueError('This stage already sent this message; use the retained answer instead of repeating it')
            thread = None
            if proposal.get('threadId'):
                thread = conn.execute('SELECT * FROM internal_threads WHERE id=? AND objective_id=?', (proposal['threadId'], parent['objective_id'])).fetchone()
                if thread is None or {row['id'] for row in json.loads(thread['participants'])} != {parent['agent_id'], proposal['recipientId']}:
                    raise ValueError('Replies must name an existing thread with exactly these participants in this objective')
                if thread['subject'] != proposal['subject']:
                    raise ValueError('A thread subject cannot be replaced')
                if conn.execute('SELECT count(*) FROM internal_messages WHERE thread_id=? AND reply_to_id IS NULL', (thread['id'],)).fetchone()[0] >= 8:
                    raise ValueError('Conversation turn limit reached; use a formal request for unresolved work')
            if thread is None:
                thread_id = _id('thread')
                participants = [dict(conn.execute('SELECT id,name,team FROM agents WHERE id=?', (identifier,)).fetchone()) for identifier in (parent['agent_id'], proposal['recipientId'])]
                project = task_project(conn, parent['task_id']) if parent['task_id'] else None
                conn.execute('INSERT INTO internal_threads VALUES (?,?,?,?,?,?,?)', (thread_id, parent['objective_id'], parent['task_id'], project['id'] if project else None, proposal['subject'], json.dumps(participants), time.time()))
            else:
                thread_id = thread['id']
            recipient = conn.execute('SELECT team FROM agents WHERE id=?', (proposal['recipientId'],)).fetchone()
            delivery = self._request(conn, parent['objective_id'], 'request.message', recipient['team'], parent['priority'], requester_id=parent['agent_id'])
            conn.execute('INSERT INTO internal_messages VALUES (?,?,?,?,?,?,?,?,?,?,?)', (_id('msg'), thread_id, parent['agent_id'], proposal['recipientId'], proposal['body'], time.time(), None, None, delivery, parent['id'], digest))
        conn.execute('INSERT INTO request_continuations VALUES (?,?) ON CONFLICT(request_id) DO UPDATE SET agent_id=excluded.agent_id', (parent['id'], parent['agent_id']))
        fence_receipts(conn, parent['id'], 'Stage paused awaiting a bounded internal conversation reply.')
        conn.execute("UPDATE requests SET status='waiting_response',token=NULL,lease=NULL,reason='Awaiting internal conversation replies',attempts=MAX(0,attempts-1) WHERE id=?", (parent['id'],))
        if parent['task_id']:
            conn.execute("UPDATE tasks SET status='blocked' WHERE id=?", (parent['task_id'],))
        self._event(conn, parent['objective_id'], 'Waiting for internal conversation replies.', 'question', parent['agent_id'])

    def _message_eligible(self, conn, request):
        message = conn.execute('SELECT * FROM internal_messages WHERE delivery_request_id=?', (request['id'],)).fetchone()
        if message is None or self._communication_reason(conn, message['sender_id'], message['recipient_id'], request['objective_id']):
            return [], None
        recipient = conn.execute('SELECT * FROM agents WHERE id=?', (message['recipient_id'],)).fetchone()
        busy = conn.execute("SELECT 1 FROM requests WHERE status='running' AND agent_id=?", (recipient['id'],)).fetchone()
        return [recipient], None if busy else recipient

    def _message_delivery_context(self, conn, request, *, mark_read=True):
        from eidolon_cli.organization_budget import budget_view
        from eidolon_cli.organization_identity import agent_identity_view
        message = conn.execute('SELECT * FROM internal_messages WHERE delivery_request_id=?', (request['id'],)).fetchone()
        reason = self._communication_reason(conn, message['sender_id'], message['recipient_id'], request['objective_id'])
        if reason or message['recipient_id'] != request['agent_id']:
            raise ValueError(reason or 'Message recipient does not own this delivery')
        thread = conn.execute('SELECT * FROM internal_threads WHERE id=?', (message['thread_id'],)).fetchone()
        conversation = conversation_view(conn, thread, agent_id=request['agent_id'])
        # Private memory has no project tags. Never leak it into reply generation.
        # Only the recipient's own retained work in this shared objective is input.
        participants = objective_participants(conn, request['objective_id'])
        conversation['scopedContext'] = []
        if {message['sender_id'], message['recipient_id']} <= participants:
            remaining = 6000
            for row in conn.execute("SELECT e.content,t.title FROM evidence e JOIN tasks t ON t.id=e.task_id WHERE e.objective_id=? AND t.author_id=? AND t.status='completed' AND e.id=(SELECT newer.id FROM evidence newer WHERE newer.task_id=t.id ORDER BY newer.created DESC,newer.id DESC LIMIT 1) ORDER BY e.created DESC LIMIT 4", (request['objective_id'], request['agent_id'])):
                if remaining <= 0:
                    break
                body = row['content'][:remaining]
                conversation['scopedContext'].append({'title': row['title'], 'body': body, 'truncated': len(body) < len(row['content'])})
                remaining -= len(body)
        agent = dict(conn.execute('SELECT id,name,role,team FROM agents WHERE id=?', (request['agent_id'],)).fetchone())
        staff = self._communication_staff(conn, agent['id'])
        agent.update(provider=staff.provider, model=staff.model)
        agent['identityId'] = agent_identity_view(conn, agent['id'])['identityId']
        budget = budget_view(conn, request['objective_id'], self.settings)
        if mark_read:
            conn.execute('UPDATE internal_messages SET read_at=COALESCE(read_at,?) WHERE id=?', (time.time(), message['id']))
        return {'conversation': conversation, 'agent': agent,
                'agentContext': {'memory': {}, 'recentHistory': [], 'contextSummary': 'Private memory is not shared with internal conversations.'},
                'toolPolicy': {'tools': [], 'readRoots': [], 'maxToolCalls': 0, 'maxResultChars': 0},
                'maxOutputTokens': self.settings.max_output_tokens, 'maxContextTokens': self.settings.max_context_tokens,
                'costBudgetEnabled': budget['configuredCostLimitUsd'] is not None,
                'timeoutSeconds': max(.001, min(self.settings.timeout_seconds, budget['deadlineTimestamp'] - time.time()))}

    def _finish_internal_reply(self, conn, request, result):
        from eidolon_cli.organization_store import _text, _id
        if set(result) != {'reply'}:
            raise ValueError('An internal delivery can only return one reply, never complete work or change authority')
        body = _text(result.get('reply'), 'Internal reply', 6000)
        message = conn.execute('SELECT * FROM internal_messages WHERE delivery_request_id=?', (request['id'],)).fetchone()
        reason = self._communication_reason(conn, message['sender_id'], message['recipient_id'], request['objective_id'])
        if reason or message['recipient_id'] != request['agent_id']:
            raise ValueError(reason or 'Reply sender must be the exact delivery recipient')
        conn.execute('INSERT INTO internal_messages VALUES (?,?,?,?,?,?,?,?,?,?,?)', (_id('msg'), message['thread_id'], request['agent_id'], message['sender_id'], body, time.time(), None, message['id'], None, message['parent_request_id'], hashlib.sha256(body.encode()).hexdigest()))

    def _resume_message_parent(self, conn, request):
        message = conn.execute('SELECT parent_request_id FROM internal_messages WHERE delivery_request_id=?', (request['id'],)).fetchone()
        if message is None:
            return
        pending = conn.execute("SELECT 1 FROM internal_messages m JOIN requests r ON r.id=m.delivery_request_id WHERE m.parent_request_id=? AND r.status!='completed'", (message['parent_request_id'],)).fetchone()
        if not pending:
            conn.execute("UPDATE requests SET status='queued',reason=NULL,available=0 WHERE id=? AND status='waiting_response'", (message['parent_request_id'],))
            conn.execute("UPDATE tasks SET status='queued' WHERE id=(SELECT task_id FROM requests WHERE id=?) AND status='blocked'", (message['parent_request_id'],))

    @staticmethod
    def _read_internal_replies(conn, request):
        conn.execute('UPDATE internal_messages SET read_at=COALESCE(read_at,?) WHERE recipient_id=? AND reply_to_id IS NOT NULL AND parent_request_id=?', (time.time(), request['agent_id'], request['id']))
