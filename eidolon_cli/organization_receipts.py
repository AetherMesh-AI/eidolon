"""Durable, lease-fenced records of actual organization tool invocations.

Receipts are written before dispatch and finalized before results reach a model.
They are execution observations, never grants or model-provided evidence.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime, timezone


RECEIPT_SCHEMA = """
CREATE TABLE IF NOT EXISTS tool_receipts (
 id TEXT PRIMARY KEY, request_id TEXT NOT NULL REFERENCES requests(id),
 attempt INTEGER NOT NULL, tool_call_id TEXT NOT NULL, tool_name TEXT NOT NULL,
 arguments TEXT NOT NULL, status TEXT NOT NULL, result TEXT, result_sha256 TEXT,
 created REAL NOT NULL, completed REAL, reason TEXT,
 UNIQUE(request_id,attempt,tool_call_id));
CREATE INDEX IF NOT EXISTS receipt_request ON tool_receipts(request_id,attempt,created);
CREATE TABLE IF NOT EXISTS evidence_tools (
 evidence_id TEXT NOT NULL REFERENCES evidence(id),
 receipt_id TEXT NOT NULL REFERENCES tool_receipts(id),
 PRIMARY KEY(evidence_id,receipt_id));
"""


def _iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat() if value is not None else None


def receipt_view(row, *, full=False):
    result = row['result']
    value = {'id': row['id'], 'toolCallId': row['tool_call_id'], 'toolName': row['tool_name'],
             'arguments': json.loads(row['arguments']), 'status': row['status'],
             'resultSha256': row['result_sha256'], 'createdAt': _iso(row['created']),
             'completedAt': _iso(row['completed']), 'reason': row['reason'],
             'attempt': row['attempt'], 'requestId': row['request_id']}
    if result is not None:
        value['result' if full else 'resultPreview'] = result if full else result[:2000]
        value['truncated'] = not full and len(result) > 2000
    return value


def evidence_receipts(conn, evidence_id, *, full=True):
    return [receipt_view(row, full=full) for row in conn.execute(
        'SELECT r.* FROM tool_receipts r JOIN evidence_tools e ON e.receipt_id=r.id '
        'WHERE e.evidence_id=? ORDER BY r.created,r.id', (evidence_id,))]


def fence_receipts(conn, request_id, reason):
    conn.execute("UPDATE tool_receipts SET status='unknown',completed=?,reason=? "
                 "WHERE request_id=? AND status='running'", (time.time(), reason[:2000], request_id))


def successful_read(result):
    try:
        value = json.loads(result)
    except (ValueError, TypeError):
        return False
    return (isinstance(value, dict) and value.get('success') is True
            and not value.get('error') and not value.get('blocked')
            and isinstance(value.get('content'), str))


def successful_observation(result, name):
    if name == 'read_file':
        return successful_read(result)
    if name not in {'list_files', 'search_files'}:
        return False
    try:
        value = json.loads(result)
    except (ValueError, TypeError):
        return False
    return (isinstance(value, dict) and value.get('success') is True
            and not value.get('error') and not value.get('blocked') and value.get('operation') == name
            and isinstance(value.get('matches' if name == 'search_files' else 'files'), list))


def _safe_arguments(arguments, name='read_file'):
    fields = {'path', 'query', 'limit'} if name == 'search_files' else {'path', 'limit'} if name == 'list_files' else {'path', 'offset', 'limit'}
    if not isinstance(arguments, dict) or set(arguments) - fields:
        raise ValueError('Tool audit arguments must use the bounded read descriptor')
    path = arguments.get('path')
    if path is not None:
        if (not isinstance(path, str) or len(path) > 4096 or path.startswith(('/', '~', '\\'))
                or '\\' in path or '..' in path.split('/') or any(ord(char) < 32 for char in path)):
            raise ValueError('Tool audit paths must be sanitized relative grant descriptors')
    for key in ('offset', 'limit'):
        if key in arguments and (type(arguments[key]) is not int or not 1 <= arguments[key] <= 1_048_577):
            raise ValueError('Tool audit pagination is invalid')
    if 'query' in arguments and (not isinstance(arguments['query'], str) or not 1 <= len(arguments['query']) <= 256
                                 or any(ord(char) < 32 for char in arguments['query'])):
        raise ValueError('Tool audit search query must be bounded literal text')
    from agent.redact import redact_sensitive_text
    return json.dumps({key: redact_sensitive_text(value) if isinstance(value, str) else value
                       for key, value in arguments.items()}, sort_keys=True, ensure_ascii=False)


class OrganizationReceiptStore:
    """Mixin over OrganizationStore's transaction and ownership primitives."""

    def record_tool_start(self, claim, call_id, name, arguments):
        if not isinstance(call_id, str) or not call_id or len(call_id) > 200:
            raise ValueError('Tool call ID must be bounded nonempty text')
        if name not in {'read_file', 'list_files', 'search_files'}:
            raise ValueError('No organization execution grant exists for this tool')
        safe_arguments = _safe_arguments(arguments, name)
        with self._write() as conn:
            request = self._owned(conn, claim)
            if request is None:
                raise ValueError('Tool execution lease is no longer owned')
            if request['type'] not in {'work.inspect', 'work.edit'} or name not in self._tool_policy(conn, request)['tools']:
                raise ValueError('This request and agent do not have the required tool grant')
            old = conn.execute('SELECT * FROM tool_receipts WHERE request_id=? AND attempt=? AND tool_call_id=?',
                               (request['id'], request['attempts'], call_id)).fetchone()
            if old:
                if old['tool_name'] != name or old['arguments'] != safe_arguments:
                    raise ValueError('Tool call ID was reused with different arguments')
                return {**receipt_view(old, full=True), 'created': False}
            count = conn.execute('SELECT count(*) FROM tool_receipts WHERE request_id=? AND attempt=?',
                                 (request['id'], request['attempts'])).fetchone()[0]
            if count >= self.settings.max_tool_calls:
                raise ValueError('Tool call limit reached; no additional tool was dispatched')
            ident = 'tool_' + uuid.uuid4().hex
            conn.execute("INSERT INTO tool_receipts(id,request_id,attempt,tool_call_id,tool_name,arguments,status,created) "
                         "VALUES (?,?,?,?,?,?,'running',?)", (ident, request['id'], request['attempts'],
                         call_id, name, safe_arguments, time.time()))
            self._event(conn, request['objective_id'], f'{name} started under its configured read grant.',
                        'tool', request['agent_id'])
            row = conn.execute('SELECT * FROM tool_receipts WHERE id=?', (ident,)).fetchone()
            return {**receipt_view(row, full=True), 'created': True}

    def record_tool_finish(self, claim, receipt_id, result, status):
        if status not in {'completed', 'failed', 'blocked'}:
            raise ValueError('Invalid terminal tool receipt status')
        if not isinstance(result, str) or len(result) > self.settings.max_tool_result_chars:
            raise ValueError('Tool result exceeds its retained evidence budget')
        from agent.redact import redact_sensitive_text
        result = redact_sensitive_text(result, file_read=True)
        if len(result) > self.settings.max_tool_result_chars:
            raise ValueError('Redacted tool result exceeds its retained evidence budget')
        digest = hashlib.sha256(result.encode()).hexdigest()
        with self._write() as conn:
            if self._owned(conn, claim) is None:
                raise ValueError('Tool execution lease is no longer owned; outcome is unconfirmed')
            row = conn.execute('SELECT * FROM tool_receipts WHERE id=? AND request_id=? AND attempt=?',
                               (receipt_id, claim['id'], claim['attempts'])).fetchone()
            if row is None:
                raise ValueError('Tool receipt does not belong to this request attempt')
            if status == 'completed' and not successful_observation(result, row['tool_name']):
                raise ValueError('Completed receipts require an actual successful granted observation')
            if row['tool_name'] not in self._tool_policy(conn, self._owned(conn, claim))['tools']:
                raise ValueError('The tool grant was revoked before the result could be committed')
            if row['status'] != 'running':
                if row['status'] == status and row['result_sha256'] == digest:
                    return receipt_view(row, full=True)
                raise ValueError('Tool receipt is already finalized; replay was rejected')
            conn.execute('UPDATE tool_receipts SET status=?,result=?,result_sha256=?,completed=? WHERE id=?',
                         (status, result, digest, time.time(), receipt_id))
            self._event(conn, claim['objective_id'], f'{row["tool_name"]} {status}; receipt retained.',
                        'tool', claim['agent_id'])
            return receipt_view(conn.execute('SELECT * FROM tool_receipts WHERE id=?', (receipt_id,)).fetchone(), full=True)

    def request_tool_receipts(self, claim):
        with self._connect() as conn:
            if self._owned(conn, claim) is None:
                raise ValueError('Tool execution lease is no longer owned')
            return [receipt_view(row, full=True) for row in conn.execute(
                'SELECT * FROM tool_receipts WHERE request_id=? AND attempt=? ORDER BY created,id',
                (claim['id'], claim['attempts']))]

    def tool_receipts(self, request_id):
        if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
            raise ValueError('Request ID must be bounded nonempty text')
        with self._connect() as conn:
            if not conn.execute('SELECT 1 FROM requests WHERE id=?', (request_id,)).fetchone():
                raise ValueError('Request not found in this organization')
            return [receipt_view(row, full=True) for row in conn.execute(
                'SELECT * FROM tool_receipts WHERE request_id=? ORDER BY created,id', (request_id,))]

    def _link_tool_evidence(self, conn, request, evidence_id):
        receipts = conn.execute('SELECT * FROM tool_receipts WHERE request_id=? AND attempt=? ORDER BY created,id',
                                (request['id'], request['attempts'])).fetchall()
        if request['type'] in {'work.inspect', 'work.edit'}:
            if (not any(r['status'] == 'completed' and r['tool_name'] == 'read_file'
                        and successful_read(r['result']) for r in receipts)
                    or any(r['status'] in {'running', 'unknown', 'blocked'} for r in receipts)):
                raise ValueError('File inspection requires completed tool evidence and no unresolved or blocked calls')
        for row in receipts:
            if row['result'] is None or hashlib.sha256(row['result'].encode()).hexdigest() != row['result_sha256']:
                raise ValueError('Tool evidence is incomplete or does not match its retained digest')
            if row['status'] == 'completed' and not successful_observation(row['result'], row['tool_name']):
                raise ValueError('Tool evidence does not describe a successful read')
            conn.execute('INSERT INTO evidence_tools VALUES (?,?)', (evidence_id, row['id']))

    @staticmethod
    def _verify_tool_evidence(conn, evidence_id, *, require_read=False):
        receipts = evidence_receipts(conn, evidence_id)
        if require_read and not any(row['status'] == 'completed' and row['toolName'] == 'read_file'
                                    and successful_read(row.get('result')) for row in receipts):
            raise ValueError('Persisted inspection evidence is missing its successful tool receipts')
        for row in receipts:
            if row['status'] not in {'completed', 'failed'} or hashlib.sha256(row['result'].encode()).hexdigest() != row['resultSha256']:
                raise ValueError('Persisted tool evidence is missing or has changed')
