"""Typed, permission-neutral owner input with immutable idempotency receipts."""
from __future__ import annotations

import hashlib
import json
import time

OWNER_SCHEMA = """
CREATE TABLE IF NOT EXISTS owner_resolutions (
 id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE, input_hash TEXT NOT NULL,
 request_id TEXT NOT NULL REFERENCES requests(id), objective_id TEXT NOT NULL REFERENCES objectives(id),
 action TEXT NOT NULL, text TEXT, evidence_ids TEXT NOT NULL, created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS owner_resolution_objective ON owner_resolutions(objective_id,created);
CREATE TABLE IF NOT EXISTS owner_scope_amendments (
 resolution_id TEXT PRIMARY KEY REFERENCES owner_resolutions(id), record TEXT NOT NULL, sha256 TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS owner_scope_amendment_immutable_update
 BEFORE UPDATE ON owner_scope_amendments BEGIN SELECT RAISE(ABORT,'Owner scope audit is immutable'); END;
CREATE TRIGGER IF NOT EXISTS owner_scope_amendment_immutable_delete
 BEFORE DELETE ON owner_scope_amendments BEGIN SELECT RAISE(ABORT,'Owner scope audit is immutable'); END;
"""


def scope_state(conn, objective_id):
    row = conn.execute('SELECT c.*,o.description FROM objective_control c JOIN objectives o ON o.id=c.objective_id WHERE c.objective_id=?', (objective_id,)).fetchone()
    return {'scope': row['amended_scope'] or row['description'], 'acceptanceCriteria': json.loads(row['criteria']),
            'requiredChecks': json.loads(row['required_checks']), 'round': row['round']}


def scope_amendment_view(conn, resolution_id):
    row = conn.execute('SELECT * FROM owner_scope_amendments WHERE resolution_id=?', (resolution_id,)).fetchone()
    return {**json.loads(row['record']), 'sha256': row['sha256']} if row else None


def resolution_count(conn, objective_id):
    explicit = conn.execute('SELECT count(*) FROM owner_resolutions WHERE objective_id=?', (objective_id,)).fetchone()[0]
    legacy = conn.execute('SELECT count(*) FROM retry_receipts rr JOIN requests r ON r.id=rr.request_id WHERE r.objective_id=?', (objective_id,)).fetchone()[0]
    typed = conn.execute('SELECT count(*) FROM request_response_receipts rr JOIN requests r ON r.id=rr.request_id WHERE r.objective_id=?', (objective_id,)).fetchone()[0]
    return explicit + legacy + typed


def allowed_resolutions(conn, request, settings):
    if request['status'] != 'pending_intervention':
        return []
    from eidolon_cli.organization_budget import budget_reason
    if budget_reason(conn, request['objective_id'], settings):
        return []
    objective = conn.execute('SELECT * FROM objectives WHERE id=?', (request['objective_id'],)).fetchone()
    control = conn.execute('SELECT * FROM objective_control WHERE objective_id=?', (request['objective_id'],)).fetchone()
    count = resolution_count(conn, request['objective_id'])
    if objective['cancelled'] or control['status'] in {'accepted', 'legacy_completed'} or count >= settings.max_owner_resolutions:
        return []
    if conn.execute('SELECT count(*) FROM objective_usage WHERE objective_id=?', (request['objective_id'],)).fetchone()[0] >= min(control['max_stages'], settings.max_stages):
        return []
    def descriptor(action, label, text=False):
        return {'action': action, 'label': label, 'requiresText': text, 'requiresEvidence': False}
    result = []
    if request['type'] not in {'request.merge', 'request.project_failed'} and request['attempts'] < settings.max_attempts and not json.loads(request['payload']).get('budgetExhausted'):
        result.append(descriptor('retry_configuration', 'Retry after fixing configuration'))
        if request['type'] in {'request.decompose', 'request.plan', 'request.review', 'request.integrate', 'request.accept'} or request['type'].startswith('work.'):
            result.append(descriptor('provide_input', 'Provide missing input', True))
    if control['round'] < min(control['max_replans'], settings.max_replans):
        result.extend([descriptor('amend_scope', 'Amend scope and replan', True),
                       descriptor('request_replan', 'Request bounded replan', True)])
    return result


class OrganizationOwnerStore:
    def request_objective_id(self, request_id):
        from eidolon_cli.organization_store import _text
        identifier = _text(request_id, 'Request ID', 128)
        with self._connect() as conn:
            row = conn.execute('SELECT objective_id FROM requests WHERE id=?', (identifier,)).fetchone()
            if row is None:
                raise ValueError('Request not found in this profile')
            return row['objective_id']

    def objective_request_ids(self, objective_id):
        from eidolon_cli.organization_store import _text
        identifier = _text(objective_id, 'Objective ID', 128)
        with self._connect() as conn:
            return [row['id'] for row in conn.execute('SELECT id FROM requests WHERE objective_id=?', (identifier,))]

    def resolution_recorded(self, request_id, action, text=None, evidence_ids=None, *, idempotency_key,
                            required_checks=None, acceptance_criteria=None):
        _, key, _, _, digest, _, _ = self._resolution_input(
            request_id, action, text, evidence_ids, idempotency_key, required_checks, acceptance_criteria)
        with self._connect() as conn:
            old = conn.execute('SELECT input_hash FROM owner_resolutions WHERE idempotency_key=?', (key,)).fetchone()
            if old and old['input_hash'] != digest:
                raise ValueError('Resolution idempotency key belongs to different input')
            return old is not None

    @staticmethod
    def _resolution_input(request_id, action, text, evidence_ids, idempotency_key, checks=None, criteria=None):
        from eidolon_cli.organization_store import _text
        identifier = _text(request_id, 'Request ID', 128)
        key = _text(idempotency_key, 'Resolution idempotency key', 128)
        if not isinstance(action, str) or action not in {'provide_input', 'amend_scope', 'retry_configuration', 'request_replan', 'record_handoff'}:
            raise ValueError('Unsupported owner resolution action')
        body = _text(text, 'Resolution text', 12000) if text is not None else None
        ids = evidence_ids if evidence_ids is not None else []
        if not isinstance(ids, list) or len(ids) > 24 or any(not isinstance(item, str) or not 1 <= len(item) <= 128 for item in ids) or len(set(ids)) != len(ids):
            raise ValueError('Resolution evidence IDs must be a bounded unique list')
        if action in {'provide_input', 'amend_scope', 'request_replan', 'record_handoff'} and body is None:
            raise ValueError('This owner resolution requires text')
        if action != 'record_handoff' and ids:
            raise ValueError('Only an exact external handoff may supply evidence IDs')
        if action != 'amend_scope' and (checks is not None or criteria is not None):
            raise ValueError('Only amend_scope may replace requiredChecks or acceptanceCriteria')
        from eidolon_cli.organization_acceptance import acceptance_criteria, required_checks
        normalized_checks = required_checks(checks) if checks is not None else None
        normalized_criteria = acceptance_criteria(criteria, body) if criteria is not None else [body] if action == 'amend_scope' else None
        identity = [identifier, action, body, sorted(ids)]
        # Preserve legacy receipt identity when both new choices are omitted.
        if checks is not None or criteria is not None:
            identity.append({'requiredChecks': normalized_checks, 'acceptanceCriteria': normalized_criteria if criteria is not None else None})
        digest = hashlib.sha256(json.dumps(identity, separators=(',', ':')).encode()).hexdigest()
        return identifier, key, body, ids, digest, normalized_checks, normalized_criteria

    def allowed_owner_resolutions(self, conn, request):
        from eidolon_cli.organization_requests import response_options
        typed = response_options(conn, request)
        if typed is not None:
            cancelled = conn.execute('SELECT cancelled FROM objectives WHERE id=?', (request['objective_id'],)).fetchone()[0]
            from eidolon_cli.organization_budget import budget_reason
            if cancelled or budget_reason(conn, request['objective_id'], self.settings) or resolution_count(conn, request['objective_id']) >= self.settings.max_owner_resolutions:
                return []
            # An owner may be unable to answer within the existing scope. Use
            # the same bounded objective recovery, never replay a linked request
            # or manufacture an answer/approval to unblock its parent.
            recovery = [item for item in allowed_resolutions(conn, request, self.settings)
                        if item['action'] in {'amend_scope', 'request_replan'}]
            return [*typed, *recovery]
        result = allowed_resolutions(conn, request, self.settings)
        if request['status'] == 'pending_intervention' and request['type'] == 'request.merge':
            # The edit module validates immutable proposal/application identity;
            # submission performs a new exact source read, never accepts a claim.
            descriptor = self.owner_handoff_descriptor(conn, request)
            control = conn.execute('SELECT * FROM objective_control WHERE objective_id=?', (request['objective_id'],)).fetchone()
            cancelled = conn.execute('SELECT cancelled FROM objectives WHERE id=?', (request['objective_id'],)).fetchone()[0]
            count = resolution_count(conn, request['objective_id'])
            if descriptor is not None and not cancelled and control['status'] not in {'accepted', 'legacy_completed'} and count < self.settings.max_owner_resolutions:
                result.append(descriptor)
        return result

    def resolve(self, request_id, action, text=None, evidence_ids=None, *, idempotency_key,
                required_checks=None, acceptance_criteria=None):
        from eidolon_cli.organization_store import _id
        identifier, key, body, ids, digest, checks, criteria = self._resolution_input(
            request_id, action, text, evidence_ids, idempotency_key, required_checks, acceptance_criteria)
        with self._write() as conn:
            self._require_current_policy(conn)
            old = conn.execute('SELECT input_hash FROM owner_resolutions WHERE idempotency_key=?', (key,)).fetchone()
            if old:
                if old['input_hash'] != digest:
                    raise ValueError('Resolution idempotency key belongs to different input')
                return False
            request = conn.execute('SELECT * FROM requests WHERE id=?', (identifier,)).fetchone()
            if request is None:
                raise ValueError('Request not found in this profile')
            if conn.execute("SELECT 1 FROM requests WHERE objective_id=? AND status='running'", (request['objective_id'],)).fetchone():
                raise ValueError('An objective execution is still active; resolve after it exits')
            descriptor = next((item for item in self.allowed_owner_resolutions(conn, request) if item['action'] == action), None)
            if descriptor is None:
                raise ValueError('This resolution is unavailable for the current request or its bounded budget')
            amendment = None
            if action == 'record_handoff':
                self.record_owner_handoff(conn, request, body, ids)
                conn.execute("UPDATE requests SET status='completed',token=NULL,lease=NULL,reason=NULL WHERE id=?", (identifier,))
            elif action in {'amend_scope', 'request_replan'}:
                if action == 'amend_scope':
                    from eidolon_cli.organization_acceptance import acceptance_criteria as normalize_criteria
                    criteria = normalize_criteria(criteria, body)
                    before = scope_state(conn, request['objective_id'])
                    conn.execute('UPDATE objective_control SET amended_scope=?,criteria=?,required_checks=? WHERE objective_id=?',
                                 (body, json.dumps(criteria), json.dumps(checks if checks is not None else before['requiredChecks']), request['objective_id']))
                self._replan(conn, request['objective_id'], body)
                if action == 'amend_scope':
                    amendment = {'inputSha256': digest,
                                 'choices': {'requiredChecks': checks, 'acceptanceCriteria': criteria if acceptance_criteria is not None else None},
                                 'before': before, 'after': scope_state(conn, request['objective_id'])}
            else:
                payload = json.loads(request['payload'])
                if body:
                    payload.setdefault('ownerInputIds', []).append(key)
                conn.execute("UPDATE requests SET status='queued',reason=NULL,token=NULL,lease=NULL,available=0,agent_id=NULL,payload=? WHERE id=?", (json.dumps(payload), identifier))
                if request['task_id']:
                    conn.execute("UPDATE tasks SET status='queued' WHERE id=?", (request['task_id'],))
            resolution_id = _id('resolution')
            conn.execute('INSERT INTO owner_resolutions VALUES (?,?,?,?,?,?,?,?,?)',
                         (resolution_id, key, digest, identifier, request['objective_id'], action, body, json.dumps(ids), time.time()))
            if amendment is not None:
                record = json.dumps(amendment, sort_keys=True, separators=(',', ':'))
                conn.execute('INSERT INTO owner_scope_amendments VALUES (?,?,?)',
                             (resolution_id, record, hashlib.sha256(record.encode()).hexdigest()))
            self._event(conn, request['objective_id'], 'Owner resolution recorded: ' + action.replace('_', ' ') + '.', 'planning', 'owner')
            self._maybe_integrate(conn, request['objective_id'])
            return True
