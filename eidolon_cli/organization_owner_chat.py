"""Owner-only, identity-bound conversations. This ledger never schedules work."""
from __future__ import annotations

import hashlib
import json
import time
import uuid

MAX_CALLS = 32
INPUT_TOKENS = 32768
OUTPUT_TOKENS = 2048
TURN_TOKENS = INPUT_TOKENS + OUTPUT_TOKENS
MAX_TOKENS = MAX_CALLS * TURN_TOKENS
MAX_TEXT = 6000
TIMEOUT_SECONDS = 90
LIVE = frozenset({'pending', 'running'})

OWNER_CHAT_SCHEMA = """
CREATE TABLE IF NOT EXISTS owner_chat_threads (
 id TEXT PRIMARY KEY, identity_id TEXT NOT NULL UNIQUE REFERENCES agent_identity(identity_id),
 agent_id TEXT NOT NULL REFERENCES agents(id), public_identity TEXT NOT NULL,
 created REAL NOT NULL, max_calls INTEGER NOT NULL, max_tokens INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS owner_chat_turns (
 id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES owner_chat_threads(id),
 idempotency_key TEXT NOT NULL UNIQUE, input_hash TEXT NOT NULL,
 owner_message_id TEXT NOT NULL, reply_message_id TEXT, status TEXT NOT NULL,
 reason TEXT, created REAL NOT NULL, finished REAL, policy_generation INTEGER NOT NULL,
 calls_reserved INTEGER NOT NULL, tokens_reserved INTEGER NOT NULL,
 dispatched INTEGER NOT NULL DEFAULT 0, input_tokens INTEGER, output_tokens INTEGER,
 provider TEXT, model TEXT, selected_provider TEXT);
CREATE INDEX IF NOT EXISTS owner_chat_turn_order ON owner_chat_turns(thread_id,created,id);
CREATE UNIQUE INDEX IF NOT EXISTS owner_chat_live_turn ON owner_chat_turns(thread_id)
 WHERE status IN ('pending','running');
CREATE TABLE IF NOT EXISTS owner_chat_messages (
 id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES owner_chat_threads(id),
 turn_id TEXT NOT NULL REFERENCES owner_chat_turns(id), role TEXT NOT NULL,
 text TEXT NOT NULL, reply_to_message_id TEXT REFERENCES owner_chat_messages(id), created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS owner_chat_message_order ON owner_chat_messages(thread_id,created,id);
"""


def exact_id(value, label):
    if (not isinstance(value, str) or not value or len(value) > 128
            or value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError(f'{label} must be an exact canonical identifier')
    return value


def _id(kind):
    return f'owner_{kind}_{uuid.uuid4().hex}'


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT:
        raise ValueError(f'Owner chat text must contain 1–{MAX_TEXT} characters')
    return value.strip()


class OrganizationOwnerChatStore:
    @staticmethod
    def _migrate_owner_chat(conn):
        columns = {row['name'] for row in conn.execute('PRAGMA table_info(owner_chat_turns)')}
        if 'selected_provider' not in columns:
            conn.execute('ALTER TABLE owner_chat_turns ADD COLUMN selected_provider TEXT')

    def _owner_chat_recipient(self, conn, agent_id, identity_id):
        from eidolon_cli.organization_identity import agent_identity_view
        row = conn.execute('SELECT a.* FROM agents a JOIN agent_identity i ON i.agent_id=a.id '
                           'WHERE a.id=? AND i.identity_id=?', (agent_id, identity_id)).fetchone()
        if row is None or row['role'] == 'Owner' or agent_id.startswith('control:'):
            raise ValueError('Owner chat recipient does not match a persistent member in this profile')
        staff = self._communication_staff(conn, agent_id)
        state = conn.execute('SELECT active,source FROM staff_state WHERE agent_id=?', (agent_id,)).fetchone()
        # Built-in evidence reviewers are persistent public members too. Their
        # owner thread does not inherit internal-peer communication authority.
        if (staff is None and state is None and row['role'] == 'Worker'
                and (agent_id == 'reviewer' or agent_id.startswith('reviewer-'))
                and set(json.loads(row['accepts'])).issubset({'request.review', 'request.test_review'})):
            from eidolon_cli.organization_roster import OrganizationStaff
            staff = OrganizationStaff(row['id'], row['name'], row['team'], tuple(json.loads(row['accepts'])))
        retired = staff is None or (state is not None and state['source'] == 'retired')
        disabled = staff is not None and not staff.enabled
        inactive = state is not None and not state['active']
        lifecycle = 'retired' if retired else 'disabled' if disabled else 'available' if inactive else 'active'
        identity = agent_identity_view(conn, agent_id)
        recipient = {'id': row['id'], 'identityId': identity_id, 'name': row['name'],
                     'role': row['role'], 'team': row['team'], 'lifecycle': lifecycle,
                     'provider': staff.provider if staff else None, 'model': staff.model if staff else None}
        public = {**{k: recipient[k] for k in ('id', 'identityId', 'name', 'role', 'team')},
                  'responsibilities': identity['responsibilities'], 'purpose': identity['purpose']}
        reason = None if lifecycle == 'active' else 'This exact member is inactive or retired; its history remains readable.'
        return recipient, public, reason

    def _owner_chat_thread(self, conn, thread_id, identity_id):
        exact_id(thread_id, 'threadId')
        exact_id(identity_id, 'identityId')
        row = conn.execute('SELECT * FROM owner_chat_threads WHERE id=? AND identity_id=?',
                           (thread_id, identity_id)).fetchone()
        if row is None:
            raise ValueError('Owner conversation does not belong to this exact identity in this profile')
        return row

    def owner_chat_open(self, agent_id, identity_id):
        exact_id(agent_id, 'agentId')
        exact_id(identity_id, 'identityId')
        with self._write() as conn:
            _, public, _ = self._owner_chat_recipient(conn, agent_id, identity_id)
            conn.execute('INSERT OR IGNORE INTO owner_chat_threads VALUES (?,?,?,?,?,?,?)',
                         (_id('thread'), identity_id, agent_id, json.dumps(public, sort_keys=True),
                          time.time(), MAX_CALLS, MAX_TOKENS))
            return conn.execute('SELECT id FROM owner_chat_threads WHERE identity_id=?', (identity_id,)).fetchone()[0]

    def owner_chat_read(self, thread_id, identity_id):
        from eidolon_cli.organization_store import _iso
        with self._connect() as conn:
            conn.execute('BEGIN')
            thread = self._owner_chat_thread(conn, thread_id, identity_id)
            recipient, _, reason = self._owner_chat_recipient(conn, thread['agent_id'], identity_id)
            turns = conn.execute('SELECT * FROM owner_chat_turns WHERE thread_id=? ORDER BY rowid', (thread_id,)).fetchall()
            messages = conn.execute('SELECT * FROM owner_chat_messages WHERE thread_id=? ORDER BY rowid', (thread_id,)).fetchall()
            calls, tokens = sum(t['calls_reserved'] for t in turns), sum(t['tokens_reserved'] for t in turns)
            active = next((t['id'] for t in turns if t['status'] in LIVE), None)
            if self._policy_paused(conn) or not self._policy_current(conn):
                reason = 'Organization configuration must be current before sending owner chat.'
            if not reason and (calls >= thread['max_calls'] or tokens + TURN_TOKENS > thread['max_tokens']):
                reason = 'This identity’s finite owner-chat budget is exhausted; reservations are never reset or refunded.'
            if not reason and active:
                reason = 'A reply is already pending for this member.'
            return {'id': thread_id, 'identityId': identity_id, 'agentId': thread['agent_id'],
                    'recipient': recipient, 'messages': [
                        {'id': m['id'], 'role': m['role'], 'text': m['text'], 'turnId': m['turn_id'],
                         'replyToMessageId': m['reply_to_message_id'], 'createdAt': _iso(m['created'])} for m in messages],
                    'turns': [{'id': t['id'], 'idempotencyKey': t['idempotency_key'], 'ownerMessageId': t['owner_message_id'],
                               'replyMessageId': t['reply_message_id'], 'status': t['status'], 'reason': t['reason'],
                               'createdAt': _iso(t['created']), 'finishedAt': _iso(t['finished']),
                               'provider': t['provider'], 'model': t['model'], 'selectedProvider': t['selected_provider'],
                               'usage': {'inputTokens': t['input_tokens'], 'outputTokens': t['output_tokens']}} for t in turns],
                    'activeTurnId': active, 'canSend': reason is None, 'unavailableReason': reason,
                    'budget': {'maxCalls': thread['max_calls'], 'callsReserved': calls,
                               'maxTokens': thread['max_tokens'], 'tokensReserved': tokens,
                               'remainingCalls': max(0, thread['max_calls'] - calls),
                               'remainingTokens': max(0, thread['max_tokens'] - tokens)},
                    'limits': {'maxMessageChars': MAX_TEXT, 'maxOutputTokens': OUTPUT_TOKENS,
                               'timeoutSeconds': TIMEOUT_SECONDS}}

    @staticmethod
    def _owner_chat_digest(thread_id, identity_id, text, reply_to):
        return hashlib.sha256(json.dumps([thread_id, identity_id, text, reply_to]).encode()).hexdigest()

    def owner_chat_recorded(self, thread_id, identity_id, text, reply_to, key):
        text, key = _text(text), exact_id(key, 'idempotencyKey')
        if reply_to is not None:
            exact_id(reply_to, 'replyToMessageId')
        with self._connect() as conn:
            self._owner_chat_thread(conn, thread_id, identity_id)
            old = conn.execute('SELECT * FROM owner_chat_turns WHERE idempotency_key=?', (key,)).fetchone()
            if old and old['input_hash'] != self._owner_chat_digest(thread_id, identity_id, text, reply_to):
                raise ValueError('Idempotency key already belongs to a different owner-chat send')
            return old['id'] if old else None

    def owner_chat_reserve(self, thread_id, identity_id, text, reply_to, key):
        text, key = _text(text), exact_id(key, 'idempotencyKey')
        if reply_to is not None:
            exact_id(reply_to, 'replyToMessageId')
        digest = self._owner_chat_digest(thread_id, identity_id, text, reply_to)
        with self._write() as conn:
            thread = self._owner_chat_thread(conn, thread_id, identity_id)
            old = conn.execute('SELECT * FROM owner_chat_turns WHERE idempotency_key=?', (key,)).fetchone()
            if old:
                if old['input_hash'] != digest:
                    raise ValueError('Idempotency key already belongs to a different owner-chat send')
                return old['id'], False
            self._require_current_policy(conn)
            _, _, reason = self._owner_chat_recipient(conn, thread['agent_id'], identity_id)
            if reason:
                raise ValueError(reason)
            if conn.execute("SELECT 1 FROM owner_chat_turns WHERE thread_id=? AND status IN ('pending','running')", (thread_id,)).fetchone():
                raise ValueError('A reply is already pending for this member')
            last = conn.execute('SELECT id FROM owner_chat_messages WHERE thread_id=? ORDER BY rowid DESC LIMIT 1', (thread_id,)).fetchone()
            if reply_to != (last['id'] if last else None):
                raise ValueError('Conversation changed; read the latest message before sending')
            calls, tokens = conn.execute('SELECT coalesce(sum(calls_reserved),0),coalesce(sum(tokens_reserved),0) FROM owner_chat_turns WHERE thread_id=?', (thread_id,)).fetchone()
            if calls >= thread['max_calls'] or tokens + TURN_TOKENS > thread['max_tokens']:
                raise ValueError('This identity’s finite owner-chat budget is exhausted')
            turn_id, message_id, now = _id('turn'), _id('message'), time.time()
            from eidolon_cli.organization_owner_chat_executor import SYSTEM, prompt
            from eidolon_cli.organization_evidence import prompt_input_bound, CONTEXT_RESERVE_TOKENS
            history = [{'id': m['id'], 'role': m['role'], 'text': m['text'], 'replyToMessageId': m['reply_to_message_id']}
                       for m in conn.execute('SELECT * FROM owner_chat_messages WHERE thread_id=? ORDER BY rowid', (thread_id,))]
            history.append({'id': message_id, 'role': 'owner', 'text': text, 'replyToMessageId': reply_to})
            submitted = prompt({'identity': json.loads(thread['public_identity']), 'conversation': {
                'id': thread_id, 'identityId': identity_id, 'replyToMessageId': message_id, 'messages': history}})
            if prompt_input_bound(submitted, SYSTEM) + CONTEXT_RESERVE_TOKENS > INPUT_TOKENS:
                raise ValueError('This exact owner conversation exceeds its bounded input window; no history was omitted and no send was reserved')
            conn.execute('INSERT INTO owner_chat_turns (id,thread_id,idempotency_key,input_hash,owner_message_id,status,created,policy_generation,calls_reserved,tokens_reserved) '
                         "VALUES (?,?,?,?,?,'pending',?,?,1,?)",
                         (turn_id, thread_id, key, digest, message_id, now, self._policy_generation, TURN_TOKENS))
            conn.execute('INSERT INTO owner_chat_messages VALUES (?,?,?,?,?,?,?)',
                         (message_id, thread_id, turn_id, 'owner', text, reply_to, now))
            return turn_id, True

    def _owner_chat_authorized(self, conn, turn):
        thread = conn.execute('SELECT * FROM owner_chat_threads WHERE id=?', (turn['thread_id'],)).fetchone()
        self._require_current_policy(conn)
        if turn['policy_generation'] != self._policy_generation:
            raise ValueError('Member configuration changed during the owner-chat turn')
        _, _, reason = self._owner_chat_recipient(conn, thread['agent_id'], thread['identity_id'])
        if reason:
            raise ValueError(reason)
        return thread

    def owner_chat_context(self, turn_id):
        with self._write() as conn:
            turn = conn.execute('SELECT * FROM owner_chat_turns WHERE id=?', (turn_id,)).fetchone()
            if turn is None or turn['status'] not in LIVE:
                raise ValueError('Owner-chat turn is no longer active')
            thread = self._owner_chat_authorized(conn, turn)
            recipient, _, _ = self._owner_chat_recipient(conn, thread['agent_id'], thread['identity_id'])
            conn.execute("UPDATE owner_chat_turns SET status='running' WHERE id=?", (turn_id,))
            return {'agent': recipient, 'identity': json.loads(thread['public_identity']),
                    'conversation': {'id': thread['id'], 'identityId': thread['identity_id'],
                                     'replyToMessageId': turn['owner_message_id'],
                                     'messages': [{'id': row['id'], 'role': row['role'], 'text': row['text'],
                                                   'replyToMessageId': row['reply_to_message_id']} for row in
                                                  conn.execute('SELECT * FROM owner_chat_messages WHERE thread_id=? ORDER BY rowid', (thread['id'],))]}}

    def owner_chat_dispatch(self, turn_id, *, provider, model, input_limit, output_limit, selected_provider=None):
        if (type(input_limit) is not int or type(output_limit) is not int
                or not 0 < input_limit <= INPUT_TOKENS or not 0 < output_limit <= OUTPUT_TOKENS):
            raise ValueError('Owner-chat input or output exceeds its reserved token ceiling')
        with self._write() as conn:
            turn = conn.execute('SELECT * FROM owner_chat_turns WHERE id=?', (turn_id,)).fetchone()
            if turn is None or turn['status'] != 'running' or turn['dispatched']:
                raise ValueError('Owner chat permits exactly one physical provider call per explicit send')
            thread = self._owner_chat_authorized(conn, turn)
            recipient, _, _ = self._owner_chat_recipient(conn, thread['agent_id'], thread['identity_id'])
            from eidolon_cli.providers import normalize_provider
            if ((recipient['provider'] and normalize_provider(recipient['provider']) != normalize_provider(selected_provider or provider))
                    or (recipient['model'] and recipient['model'] != model)):
                raise ValueError('The exact configured member route changed before owner-chat dispatch')
            conn.execute('UPDATE owner_chat_turns SET dispatched=1,provider=?,model=?,selected_provider=? WHERE id=?',
                         (str(provider)[:300], str(model)[:300], str(selected_provider or provider)[:300], turn_id))

    def owner_chat_finish(self, turn_id, *, status, reason=None, reply=None, usage=None):
        if status not in {'completed', 'blocked', 'cancelled', 'timed_out', 'uncertain'}:
            raise ValueError('Invalid owner-chat terminal status')
        if reply is not None:
            reply = _text(reply)
        with self._write() as conn:
            turn = conn.execute('SELECT * FROM owner_chat_turns WHERE id=?', (turn_id,)).fetchone()
            if turn is None:
                raise ValueError('Owner-chat turn not found')
            if turn['status'] not in LIVE:
                return False
            if status == 'completed':
                self._owner_chat_authorized(conn, turn)
                if reply is None or not turn['dispatched']:
                    raise ValueError('A completed owner reply requires a reserved physical call and text')
                reply_id, now = _id('message'), time.time()
                conn.execute('INSERT INTO owner_chat_messages VALUES (?,?,?,?,?,?,?)',
                             (reply_id, turn['thread_id'], turn_id, 'agent', reply, turn['owner_message_id'], now))
            else:
                reply_id = None
            usage = usage if isinstance(usage, dict) else {}
            measured = [usage.get(key) if type(usage.get(key)) is int and 0 <= usage[key] <= bound else None
                        for key, bound in [('inputTokens', INPUT_TOKENS), ('outputTokens', OUTPUT_TOKENS)]]
            conn.execute('UPDATE owner_chat_turns SET status=?,reason=?,reply_message_id=?,finished=?,input_tokens=?,output_tokens=? WHERE id=?',
                         (status, reason[:2000] if reason else None, reply_id, time.time(), *measured, turn_id))
            return True

    def owner_chat_turn_state(self, turn_id):
        with self._connect() as conn:
            turn = conn.execute('SELECT * FROM owner_chat_turns WHERE id=?', (turn_id,)).fetchone()
            if turn is None:
                raise ValueError('Owner-chat turn not found')
            if turn['status'] in LIVE:
                self._owner_chat_authorized(conn, turn)
            return dict(turn)

    def owner_chat_recover(self, thread_id, identity_id):
        """Caller owns the OS fence: no provider from another host can still write."""
        with self._write() as conn:
            self._owner_chat_thread(conn, thread_id, identity_id)
            conn.execute("UPDATE owner_chat_turns SET status='uncertain',reason=?,finished=? WHERE thread_id=? AND status IN ('pending','running')",
                         ('Backend interrupted this send; provider outcome is unknown. It will never replay automatically.', time.time(), thread_id))
