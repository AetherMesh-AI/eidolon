"""Owner-only, identity-bound conversations. This ledger never schedules work."""
from __future__ import annotations

import hashlib
import json
import time
import uuid

MAX_CALLS = 32
MAX_CUMULATIVE_CALLS = 1_000_000
MAX_CONTEXT_MESSAGES = 100
MAX_SAFE_INTEGER = 2**53 - 1
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
CREATE TABLE IF NOT EXISTS owner_chat_renewals (
 id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES owner_chat_threads(id),
 idempotency_key TEXT NOT NULL UNIQUE, input_hash TEXT NOT NULL,
 additional_calls INTEGER NOT NULL, budget_version INTEGER NOT NULL,
 policy_generation INTEGER NOT NULL, created REAL NOT NULL,
 UNIQUE(thread_id,budget_version));
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
        for name, kind in [('context_message_ids', 'TEXT'), ('context_omitted_count', 'INTEGER')]:
            if name not in columns:
                conn.execute(f'ALTER TABLE owner_chat_turns ADD COLUMN {name} {kind}')

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

    @staticmethod
    def _owner_chat_budget(conn, thread):
        calls, tokens = conn.execute('SELECT coalesce(sum(calls_reserved),0),coalesce(sum(tokens_reserved),0) FROM owner_chat_turns WHERE thread_id=?', (thread['id'],)).fetchone()
        added, version = conn.execute('SELECT coalesce(sum(additional_calls),0),count(*) FROM owner_chat_renewals WHERE thread_id=?', (thread['id'],)).fetchone()
        max_calls, max_tokens = thread['max_calls'] + added, thread['max_tokens'] + added * TURN_TOKENS
        return {'maxCalls': max_calls, 'maxTokens': max_tokens, 'callsReserved': calls,
                'tokensReserved': tokens, 'remainingCalls': max(0, max_calls - calls),
                'remainingTokens': max(0, max_tokens - tokens), 'version': version}

    def owner_chat_read(self, thread_id, identity_id, before_message_id=None, limit=50):
        from eidolon_cli.organization_store import _iso
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('limit must be an integer from 1 to 100')
        if before_message_id is not None:
            exact_id(before_message_id, 'beforeMessageId')
        with self._connect() as conn:
            conn.execute('BEGIN')
            thread = self._owner_chat_thread(conn, thread_id, identity_id)
            recipient, _, reason = self._owner_chat_recipient(conn, thread['agent_id'], identity_id)
            if self._policy_paused(conn) or not self._policy_current(conn):
                reason = 'Organization configuration must be current before sending owner chat.'
            budget = self._owner_chat_budget(conn, thread)
            max_add = max(0, min(MAX_CALLS - budget['remainingCalls'],
                                 MAX_CUMULATIVE_CALLS - budget['maxCalls']))
            renewal_reason = reason or (None if max_add else 'The outstanding allowance is full or the cumulative ceiling was reached.')
            renewal_receipt = conn.execute('SELECT * FROM owner_chat_renewals WHERE thread_id=? ORDER BY budget_version DESC LIMIT 1', (thread_id,)).fetchone()
            latest = conn.execute('SELECT id FROM owner_chat_messages WHERE thread_id=? ORDER BY rowid DESC LIMIT 1', (thread_id,)).fetchone()
            cursor = None
            if before_message_id is not None:
                cursor = conn.execute('SELECT rowid FROM owner_chat_messages WHERE id=? AND thread_id=?', (before_message_id, thread_id)).fetchone()
                if cursor is None:
                    raise ValueError('History cursor does not belong to this exact owner conversation')
            rows = conn.execute('SELECT * FROM owner_chat_messages WHERE thread_id=?' + (' AND rowid<?' if cursor else '') + ' ORDER BY rowid DESC LIMIT ?',
                                (thread_id, *([cursor[0]] if cursor else []), limit + 1)).fetchall()
            has_more = len(rows) > limit
            messages = list(reversed(rows[:limit]))
            active_row = conn.execute("SELECT id FROM owner_chat_turns WHERE thread_id=? AND status IN ('pending','running')", (thread_id,)).fetchone()
            active = active_row['id'] if active_row else None
            turn_ids = list(dict.fromkeys([m['turn_id'] for m in messages] + ([active] if active else [])))
            turns = conn.execute('SELECT * FROM owner_chat_turns WHERE thread_id=? AND id IN (' + ','.join('?' for _ in turn_ids) + ') ORDER BY rowid', (thread_id, *turn_ids)).fetchall() if turn_ids else []
            if not reason and (budget['remainingCalls'] < 1 or budget['remainingTokens'] < TURN_TOKENS):
                reason = 'This identity’s owner-chat allowance is exhausted; explicitly renew to continue. Reservations are never refunded.'
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
                               'context': {'includedMessageIds': json.loads(t['context_message_ids']) if t['context_message_ids'] else None,
                                           'omittedMessageCount': t['context_omitted_count'],
                                           'oldestIncludedMessageId': json.loads(t['context_message_ids'])[0] if t['context_message_ids'] else None},
                               'usage': {'inputTokens': t['input_tokens'], 'outputTokens': t['output_tokens']}} for t in turns],
                    'activeTurnId': active, 'canSend': reason is None, 'unavailableReason': reason,
                    'budget': budget, 'policyGeneration': self._policy_generation,
                    'renewal': {'canRenew': renewal_reason is None, 'maxAdditionalCalls': max_add,
                                'maxOutstandingCalls': MAX_CALLS, 'maxCumulativeCalls': MAX_CUMULATIVE_CALLS, 'tokensPerCall': TURN_TOKENS,
                                'unavailableReason': renewal_reason},
                    'renewalReceipt': self._owner_chat_renewal_receipt(renewal_receipt) if renewal_receipt else None,
                    'latestMessageId': latest['id'] if latest else None,
                    'history': {'hasMore': has_more, 'oldestMessageId': messages[0]['id'] if messages else None},
                    'limits': {'maxMessageChars': MAX_TEXT, 'maxOutputTokens': OUTPUT_TOKENS,
                               'maxContextMessages': MAX_CONTEXT_MESSAGES,
                               'timeoutSeconds': TIMEOUT_SECONDS}}

    def owner_chat_renew(self, thread_id, identity_id, key, expected_version, expected_generation, additional_calls):
        exact_id(key, 'idempotencyKey')
        if (type(expected_version) is not int or not 0 <= expected_version <= MAX_SAFE_INTEGER
                or type(expected_generation) is not int or not 0 <= expected_generation <= MAX_SAFE_INTEGER
                or type(additional_calls) is not int or not 1 <= additional_calls <= MAX_CALLS):
            raise ValueError('Renewal requires exact nonnegative versions and additionalCalls from 1 to 32')
        digest = hashlib.sha256(json.dumps([thread_id, identity_id, expected_version, expected_generation, additional_calls]).encode()).hexdigest()
        with self._write() as conn:
            thread = self._owner_chat_thread(conn, thread_id, identity_id)
            if conn.execute('SELECT 1 FROM owner_chat_turns WHERE idempotency_key=?', (key,)).fetchone():
                raise ValueError('Idempotency key already belongs to a different owner-chat action')
            receipt = conn.execute('SELECT * FROM owner_chat_renewals WHERE idempotency_key=?', (key,)).fetchone()
            if receipt:
                if receipt['input_hash'] != digest:
                    raise ValueError('Idempotency key already belongs to a different owner-chat renewal')
            else:
                self._require_current_policy(conn)
                _, _, reason = self._owner_chat_recipient(conn, thread['agent_id'], identity_id)
                if reason:
                    raise ValueError(reason)
                budget = self._owner_chat_budget(conn, thread)
                if expected_version != budget['version'] or expected_generation != self._policy_generation:
                    raise ValueError('Owner-chat allowance or configuration changed; read before renewing')
                if (budget['remainingCalls'] + additional_calls > MAX_CALLS
                        or budget['maxCalls'] + additional_calls > MAX_CUMULATIVE_CALLS):
                    raise ValueError('Renewal exceeds the finite outstanding or cumulative allowance ceiling')
                receipt_id = _id('renewal')
                conn.execute('INSERT INTO owner_chat_renewals VALUES (?,?,?,?,?,?,?,?)',
                             (receipt_id, thread_id, key, digest, additional_calls, expected_version + 1, expected_generation, time.time()))
                receipt = conn.execute('SELECT * FROM owner_chat_renewals WHERE id=?', (receipt_id,)).fetchone()
            return self._owner_chat_renewal_receipt(receipt)

    @staticmethod
    def _owner_chat_renewal_receipt(receipt):
        from eidolon_cli.organization_store import _iso
        return {'id': receipt['id'], 'idempotencyKey': receipt['idempotency_key'],
                'additionalCalls': receipt['additional_calls'], 'budgetVersion': receipt['budget_version'],
                'policyGeneration': receipt['policy_generation'], 'createdAt': _iso(receipt['created'])}

    @staticmethod
    def _owner_chat_digest(thread_id, identity_id, text, reply_to):
        return hashlib.sha256(json.dumps([thread_id, identity_id, text, reply_to]).encode()).hexdigest()

    def owner_chat_recorded(self, thread_id, identity_id, text, reply_to, key):
        text, key = _text(text), exact_id(key, 'idempotencyKey')
        if reply_to is not None:
            exact_id(reply_to, 'replyToMessageId')
        with self._connect() as conn:
            self._owner_chat_thread(conn, thread_id, identity_id)
            if conn.execute('SELECT 1 FROM owner_chat_renewals WHERE idempotency_key=?', (key,)).fetchone():
                raise ValueError('Idempotency key already belongs to a different owner-chat action')
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
            if conn.execute('SELECT 1 FROM owner_chat_renewals WHERE idempotency_key=?', (key,)).fetchone():
                raise ValueError('Idempotency key already belongs to a different owner-chat action')
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
            budget = self._owner_chat_budget(conn, thread)
            if budget['remainingCalls'] < 1 or budget['remainingTokens'] < TURN_TOKENS:
                raise ValueError('This identity’s finite owner-chat budget is exhausted')
            turn_id, message_id, now = _id('turn'), _id('message'), time.time()
            from eidolon_cli.organization_owner_chat_executor import SYSTEM, prompt
            from eidolon_cli.organization_evidence import prompt_input_bound, CONTEXT_RESERVE_TOKENS
            recent = conn.execute('SELECT * FROM owner_chat_messages WHERE thread_id=? ORDER BY rowid DESC LIMIT ?',
                                  (thread_id, MAX_CONTEXT_MESSAGES - 1)).fetchall()
            history = [{'id': m['id'], 'role': m['role'], 'text': m['text'], 'replyToMessageId': m['reply_to_message_id']}
                       for m in reversed(recent)]
            if history and history[0]['role'] == 'agent':
                history.pop(0)
            prior_count = conn.execute('SELECT count(*) FROM owner_chat_messages WHERE thread_id=?', (thread_id,)).fetchone()[0]
            prior_omitted = prior_count - len(history)
            history.append({'id': message_id, 'role': 'owner', 'text': text, 'replyToMessageId': reply_to})
            # Remove whole oldest turns only. The exact latest target and its
            # owner/agent pair are mandatory, even when they cannot fit.
            minimum = len(history) - 2
            if minimum >= 0 and history[minimum]['role'] == 'agent':
                minimum -= 1
            minimum = max(0, minimum)
            omitted = 0
            while True:
                submitted = prompt({'identity': json.loads(thread['public_identity']), 'conversation': {
                    'id': thread_id, 'identityId': identity_id, 'replyToMessageId': message_id, 'messages': history[omitted:],
                    'context': {'omittedMessageCount': prior_omitted + omitted, 'oldestIncludedMessageId': history[omitted]['id']}}})
                if prompt_input_bound(submitted, SYSTEM) + CONTEXT_RESERVE_TOKENS <= INPUT_TOKENS:
                    break
                next_start = omitted + 1
                if next_start < len(history) and history[next_start]['role'] == 'agent':
                    next_start += 1
                if next_start > minimum:
                    raise ValueError('The exact latest reply target and new message exceed the bounded input window; no send was reserved')
                omitted = next_start
            included_ids = [m['id'] for m in history[omitted:]]
            conn.execute('INSERT INTO owner_chat_turns (id,thread_id,idempotency_key,input_hash,owner_message_id,status,created,policy_generation,calls_reserved,tokens_reserved,context_message_ids,context_omitted_count) '
                         "VALUES (?,?,?,?,?,'pending',?,?,1,?,?,?)",
                         (turn_id, thread_id, key, digest, message_id, now, self._policy_generation, TURN_TOKENS,
                          json.dumps(included_ids), prior_omitted + omitted))
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
            if turn['context_message_ids']:
                ids = json.loads(turn['context_message_ids'])
                rows = conn.execute('SELECT * FROM owner_chat_messages WHERE thread_id=? AND id IN (' + ','.join('?' for _ in ids) + ')', (thread['id'], *ids)).fetchall()
                by_id = {row['id']: row for row in rows}
                if any(message_id not in by_id for message_id in ids):
                    raise ValueError('Reserved owner-chat context is no longer intact')
                rows = [by_id[message_id] for message_id in ids]
            else:
                rows = conn.execute('SELECT * FROM owner_chat_messages WHERE thread_id=? ORDER BY rowid', (thread['id'],)).fetchall()
            conn.execute("UPDATE owner_chat_turns SET status='running' WHERE id=?", (turn_id,))
            return {'agent': recipient, 'identity': json.loads(thread['public_identity']),
                    'conversation': {'id': thread['id'], 'identityId': thread['identity_id'],
                                     'replyToMessageId': turn['owner_message_id'],
                                     'context': {'omittedMessageCount': turn['context_omitted_count'],
                                                 'oldestIncludedMessageId': rows[0]['id'] if rows else None},
                                     'messages': [{'id': row['id'], 'role': row['role'], 'text': row['text'],
                                                   'replyToMessageId': row['reply_to_message_id']} for row in rows]}}

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

    def owner_chat_validate_turn(self, thread_id, identity_id, turn_id):
        """Cancellation verifies ownership even when the turn's authority expired."""
        exact_id(turn_id, 'turnId')
        with self._connect() as conn:
            self._owner_chat_thread(conn, thread_id, identity_id)
            if not conn.execute('SELECT 1 FROM owner_chat_turns WHERE id=? AND thread_id=?', (turn_id, thread_id)).fetchone():
                raise ValueError('Turn does not belong to this exact owner conversation')

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
