"""Objective-level synthesis, independent acceptance and bounded round history."""
from __future__ import annotations

import hashlib
import json
import time


ACCEPTANCE_SCHEMA = """
CREATE TABLE IF NOT EXISTS objective_control (
 objective_id TEXT PRIMARY KEY REFERENCES objectives(id), criteria TEXT NOT NULL,
 status TEXT NOT NULL, round INTEGER NOT NULL DEFAULT 0, max_replans INTEGER NOT NULL,
 max_stages INTEGER NOT NULL, summary TEXT, deliverable_id TEXT,
 delivery_mode TEXT NOT NULL DEFAULT 'source_project', amended_scope TEXT, required_checks TEXT NOT NULL DEFAULT '[]');
CREATE TABLE IF NOT EXISTS objective_task_rounds (
 task_id TEXT PRIMARY KEY REFERENCES tasks(id), objective_id TEXT NOT NULL REFERENCES objectives(id),
 round INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS objective_deliverables (
 id TEXT PRIMARY KEY, objective_id TEXT NOT NULL REFERENCES objectives(id), round INTEGER NOT NULL,
 request_id TEXT NOT NULL UNIQUE REFERENCES requests(id), author_id TEXT NOT NULL,
 content TEXT NOT NULL, sha256 TEXT NOT NULL, summary TEXT NOT NULL, evidence_ids TEXT NOT NULL,
 created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS objective_acceptances (
 request_id TEXT PRIMARY KEY REFERENCES requests(id), objective_id TEXT NOT NULL REFERENCES objectives(id),
 round INTEGER NOT NULL, agent_id TEXT NOT NULL, approved INTEGER NOT NULL,
 summary TEXT NOT NULL, evidence_ids TEXT NOT NULL, created REAL NOT NULL, criteria_results TEXT NOT NULL, conflicts TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS objective_usage (
 request_id TEXT NOT NULL REFERENCES requests(id), token TEXT NOT NULL,
 objective_id TEXT NOT NULL REFERENCES objectives(id), stage TEXT NOT NULL,
 input_tokens INTEGER, output_tokens INTEGER, completed INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(request_id,token));
CREATE INDEX IF NOT EXISTS objective_usage_objective ON objective_usage(objective_id);
CREATE INDEX IF NOT EXISTS objective_task_round_lookup ON objective_task_rounds(objective_id,round);
CREATE INDEX IF NOT EXISTS objective_acceptance_round_lookup ON objective_acceptances(objective_id,round,created DESC);
"""


def acceptance_criteria(value, description):
    from eidolon_cli.organization_store import _text
    if value is None:
        return [description]
    if not isinstance(value, list) or not 1 <= len(value) <= 12:
        raise ValueError('Acceptance criteria must contain 1–12 nonempty strings')
    return [_text(item, 'Acceptance criterion', 2000) for item in value]


def required_checks(value):
    if not isinstance(value, list) or len(value) > 3 or any(item not in ('project_tests', 'managed_validation', 'source_integration') for item in value) or len(set(value)) != len(value):
        raise ValueError('requiredChecks must select unique project_tests, managed_validation or source_integration')
    return value


def current_tasks(conn, objective_id):
    return conn.execute('SELECT t.* FROM tasks t JOIN objective_task_rounds r ON r.task_id=t.id '
                        'JOIN objective_control c ON c.objective_id=r.objective_id '
                        'WHERE t.objective_id=? AND r.round=c.round ORDER BY t.rowid', (objective_id,)).fetchall()


def final_artifact(conn, identifier):
    row = conn.execute('SELECT * FROM objective_deliverables WHERE id=?', (identifier,)).fetchone()
    return {**dict(row), 'toolReceipts': [], 'kind': 'integrated_deliverable'} if row else None


def usage_view(conn, control, settings):
    rows = conn.execute('SELECT * FROM objective_usage WHERE objective_id=?', (control['objective_id'],)).fetchall()
    return {'stages': len(rows), 'stageLimit': min(control['max_stages'], settings.max_stages),
            'inputTokens': sum(row['input_tokens'] or 0 for row in rows),
            'outputTokens': sum(row['output_tokens'] or 0 for row in rows),
            'usageComplete': all(row['input_tokens'] is not None and row['output_tokens'] is not None for row in rows),
            'perCallOutputLimit': settings.max_output_tokens,
            'scope': 'Observed token totals; interrupted or unreported usage is unknown. Stage admission and per-call output are bounded; this is not a spending cap.'}


def validate_acceptance_result(result, criteria, expected):
    from eidolon_cli.organization_store import _text
    checks, conflicts = result.get('criteriaResults'), result.get('conflicts')
    if not isinstance(checks, list) or len(checks) != len(criteria):
        raise ValueError('Acceptance must evaluate every exact objective criterion')
    for criterion, check in zip(criteria, checks):
        if not isinstance(check, dict) or check.get('criterion') != criterion or type(check.get('satisfied')) is not bool:
            raise ValueError('Acceptance criterion judgments must match the current objective in order')
        ids = check.get('evidenceIds')
        if (not isinstance(ids, list) or any(not isinstance(item, str) or item not in expected for item in ids)
                or len(set(ids)) != len(ids) or (check['satisfied'] and not ids)):
            raise ValueError('Satisfied criteria must cite exact supplied evidence')
        _text(check.get('reason'), 'Criterion acceptance reason', 2000)
    if not isinstance(conflicts, list) or len(conflicts) > 24:
        raise ValueError('Acceptance must report a bounded conflicts list')
    for conflict in conflicts:
        _text(conflict, 'Conflicting evidence', 2000)
    if result.get('approved') is True and (conflicts or not all(check['satisfied'] for check in checks)):
        raise ValueError('An objective with conflicting evidence or unmet criteria cannot be accepted')


class OrganizationAcceptanceStore:
    @staticmethod
    def _merge_required_checks(conn, objective_id, additions):
        added = required_checks(additions)
        control = conn.execute('SELECT required_checks FROM objective_control WHERE objective_id=?', (objective_id,)).fetchone()
        checks = list(dict.fromkeys([*json.loads(control['required_checks']), *added]))
        conn.execute('UPDATE objective_control SET required_checks=? WHERE objective_id=?', (json.dumps(checks), objective_id))

    def _migrate_acceptance(self, conn):
        # Record the old meaning once. A previously settled ledger is not reopened.
        for row in conn.execute('SELECT o.* FROM objectives o LEFT JOIN objective_control c ON c.objective_id=o.id WHERE c.objective_id IS NULL').fetchall():
            tasks = conn.execute('SELECT status FROM tasks WHERE objective_id=?', (row['id'],)).fetchall()
            requests = conn.execute('SELECT status FROM requests WHERE objective_id=?', (row['id'],)).fetchall()
            settled = bool(tasks) and all(t['status'] == 'completed' for t in tasks) and all(r['status'] == 'completed' for r in requests)
            conn.execute('INSERT INTO objective_control(objective_id,criteria,status,round,max_replans,max_stages) VALUES (?,?,?,0,?,?)',
                         (row['id'], json.dumps([row['description']]), 'legacy_completed' if settled else 'pending',
                          self.settings.max_replans, self.settings.max_stages))
            conn.execute('INSERT INTO objective_task_rounds SELECT id,objective_id,0 FROM tasks WHERE objective_id=?', (row['id'],))
            for request in conn.execute('SELECT * FROM requests WHERE objective_id=?', (row['id'],)).fetchall():
                for attempt in range(request['attempts']):
                    conn.execute('INSERT INTO objective_usage(request_id,token,objective_id,stage,completed) VALUES (?,?,?,?,?)',
                                 (request['id'], 'legacy-attempt-' + str(attempt), row['id'], request['type'], int(request['status'] == 'completed')))

    def _current_evidence(self, conn, objective_id):
        rows = []
        for task in current_tasks(conn, objective_id):
            if task['status'] != 'completed':
                raise ValueError('Objective acceptance requires every current task to complete independent review')
            evidence = conn.execute('SELECT * FROM evidence WHERE task_id=? ORDER BY created DESC,id DESC LIMIT 1', (task['id'],)).fetchone()
            if evidence is None or hashlib.sha256(evidence['content'].encode()).hexdigest() != evidence['sha256']:
                raise ValueError('Objective acceptance evidence is missing or has changed')
            review = conn.execute('SELECT * FROM reviews WHERE task_id=? AND approved=1 ORDER BY created DESC LIMIT 1', (task['id'],)).fetchone()
            if (review is None or evidence['id'] not in json.loads(review['evidence_ids'])
                    or review['agent_id'] == task['author_id']):
                raise ValueError('Objective acceptance requires independent approval of every exact current artifact')
            review_request = conn.execute('SELECT payload FROM requests WHERE id=?', (review['request_id'],)).fetchone()
            bound = json.loads(review_request['payload']).get('evidenceHashes') if review_request else None
            if bound is not None and bound.get(evidence['id']) != evidence['sha256']:
                raise ValueError('Objective evidence no longer matches its independent review hash')
            self._verify_tool_evidence(conn, evidence['id'], require_read=task['type'] in {'work.inspect', 'work.edit'})
            if task['type'] == 'work.edit':
                from eidolon_cli.organization_edits import evidence_proposal
                proposal = evidence_proposal(conn, evidence['id'])
                if proposal is None or not proposal.get('validationReceipt') or proposal['validationReceipt'].get('status') != 'passed':
                    raise ValueError('Objective acceptance requires exact applied and validated edit evidence')
            rows.append(evidence)
        if not rows:
            raise ValueError('Objective acceptance requires current task evidence')
        from eidolon_cli.organization_project_workspace import project_validation_view, project_validation_artifact
        proof = project_validation_view(conn, objective_id)
        applied = conn.execute('SELECT 1 FROM edit_applications a JOIN edit_proposals p ON p.id=a.proposal_id WHERE p.objective_id=?', (objective_id,)).fetchone()
        if applied and proof is None:
            raise ValueError('Objective acceptance requires its retained current-round aggregate project validation')
        if proof is not None:
            rows.append(project_validation_artifact(conn, proof['id']))
        return rows

    def _maybe_integrate(self, conn, objective_id):
        control = conn.execute('SELECT * FROM objective_control WHERE objective_id=?', (objective_id,)).fetchone()
        if control['status'] in {'accepted', 'legacy_completed', 'integrating', 'reviewing', 'blocked'}:
            return
        objective = conn.execute('SELECT * FROM objectives WHERE id=?', (objective_id,)).fetchone()
        if objective['cancelled'] or conn.execute("SELECT 1 FROM requests WHERE objective_id=? AND status NOT IN ('completed','cancelled')", (objective_id,)).fetchone():
            return
        tasks = current_tasks(conn, objective_id)
        if not tasks or any(task['status'] != 'completed' for task in tasks):
            return
        if self.ensure_source_handoff(conn, objective_id):
            return
        self.prepare_project_acceptance(conn, objective_id)
        evidence = self._current_evidence(conn, objective_id)
        self._request(conn, objective_id, 'request.integrate', self.settings.team, objective['priority'],
                      payload={'evidenceIds': [row['id'] for row in evidence], 'round': control['round']})
        conn.execute("UPDATE objective_control SET status='integrating' WHERE objective_id=?", (objective_id,))
        self._event(conn, objective_id, 'Task reviews complete. Integrated deliverable and independent executive acceptance are required.', 'review')

    def _verify_final_inputs(self, conn, request, *, include_final=False):
        control = conn.execute('SELECT * FROM objective_control WHERE objective_id=?', (request['objective_id'],)).fetchone()
        payload = json.loads(request['payload'])
        if payload.get('round') != control['round']:
            raise ValueError('Objective acceptance round is stale')
        source = self._current_evidence(conn, request['objective_id'])
        expected = [row['id'] for row in source]
        hashes = {row['id']: row['sha256'] for row in source}
        if include_final:
            final = final_artifact(conn, control['deliverable_id'])
            if (final is None or final['round'] != control['round']
                    or hashlib.sha256(final['content'].encode()).hexdigest() != final['sha256']
                    or json.loads(final['evidence_ids']) != expected):
                raise ValueError('Integrated deliverable is missing, stale or changed')
            if final['author_id'] == request['agent_id']:
                raise ValueError('The integrator cannot accept its own final deliverable')
            expected.append(final['id'])
            hashes[final['id']] = final['sha256']
        if payload.get('evidenceIds') != expected or payload.get('evidenceHashes') != hashes:
            raise ValueError('Final review must use exactly the current persisted objective evidence')
        return expected

    def _finish_integrate(self, conn, request, result):
        from eidolon_cli.organization_store import _id, _text
        expected = self._verify_final_inputs(conn, request)
        content = _text(result.get('deliverable'), 'Integrated deliverable', 100000)
        summary = _text(result.get('summary'), 'Integration summary', 10000)
        ident = _id('outcome')
        control = conn.execute('SELECT * FROM objective_control WHERE objective_id=?', (request['objective_id'],)).fetchone()
        conn.execute('INSERT INTO objective_deliverables VALUES (?,?,?,?,?,?,?,?,?,?)',
                     (ident, request['objective_id'], control['round'], request['id'], request['agent_id'],
                      content, hashlib.sha256(content.encode()).hexdigest(), summary, json.dumps(expected), time.time()))
        conn.execute("UPDATE objective_control SET status='reviewing',deliverable_id=? WHERE objective_id=?", (ident, request['objective_id']))
        self._request(conn, request['objective_id'], 'request.accept', self.settings.team, request['priority'],
                      payload={'evidenceIds': [*expected, ident], 'round': control['round']})

    def _finish_accept(self, conn, request, result):
        from eidolon_cli.organization_store import _text
        expected = self._verify_final_inputs(conn, request, include_final=True)
        actual = result.get('evidenceIds')
        if (not isinstance(actual, list) or any(not isinstance(item, str) for item in actual)
                or len(actual) != len(expected) or set(actual) != set(expected)):
            raise ValueError('Acceptance must identify exactly every task artifact and integrated deliverable')
        approved = result.get('approved')
        if type(approved) is not bool:
            raise ValueError('Objective acceptance requires an explicit boolean decision')
        if request['agent_id'] != 'executive':
            raise ValueError('Only the independent executive can accept the objective')
        control = conn.execute('SELECT * FROM objective_control WHERE objective_id=?', (request['objective_id'],)).fetchone()
        validate_acceptance_result(result, json.loads(control['criteria']), expected)
        summary = _text(result.get('summary'), 'Acceptance summary', 10000)
        control = conn.execute('SELECT * FROM objective_control WHERE objective_id=?', (request['objective_id'],)).fetchone()
        if approved:
            checks = json.loads(control['required_checks'])
            applied = conn.execute('SELECT 1 FROM edit_proposals p JOIN edit_applications a ON a.proposal_id=p.id WHERE p.objective_id=?', (request['objective_id'],)).fetchone()
            if control['delivery_mode'] == 'source_project' and applied and 'source_integration' not in checks:
                checks.append('source_integration')
            self._verify_required_checks(conn, request['objective_id'], checks)
        conn.execute('INSERT INTO objective_acceptances VALUES (?,?,?,?,?,?,?,?,?,?)',
                     (request['id'], request['objective_id'], control['round'], request['agent_id'],
                      int(approved), summary, json.dumps(expected), time.time(), json.dumps(result['criteriaResults']), json.dumps(result['conflicts'])))
        if approved:
            conn.execute("UPDATE objective_control SET status='accepted',summary=? WHERE objective_id=?", (summary, request['objective_id']))
        elif control['round'] < min(control['max_replans'], self.settings.max_replans):
            self._replan(conn, request['objective_id'], summary, completing_request=request['id'])
        else:
            conn.execute("UPDATE objective_control SET status='blocked',summary=? WHERE objective_id=?", (summary, request['objective_id']))
            ident = self._request(conn, request['objective_id'], 'request.accept', self.settings.team, request['priority'],
                                  payload={'evidenceIds': expected, 'round': control['round'], 'budgetExhausted': True})
            self._pending(conn, conn.execute('SELECT * FROM requests WHERE id=?', (ident,)).fetchone(),
                          'Objective replan limit reached. Final deliverable rejected: ' + summary[:1500])
            conn.execute('UPDATE requests SET attempts=? WHERE id=?', (self.settings.max_attempts, ident))

    def _verify_required_checks(self, conn, objective_id, checks):
        if 'project_tests' in checks:
            raise ValueError('Required project tests have not been executed by an authorized runtime; model approval cannot satisfy this check')
        # The project module validates current per-path heads across rounds.
        # Historical failed/superseded proposals remain evidence, not obligations
        # that can only be satisfied by falsifying their original observations.
        self.verify_project_objective_acceptance(conn, objective_id,
                                                 require_source='source_integration' in checks,
                                                 require_validation='managed_validation' in checks)

    def _replan(self, conn, objective_id, feedback, *, completing_request=None):
        from eidolon_cli.organization_receipts import fence_receipts
        control = conn.execute('SELECT * FROM objective_control WHERE objective_id=?', (objective_id,)).fetchone()
        if control['round'] >= min(control['max_replans'], self.settings.max_replans):
            raise ValueError('Objective replan limit reached; create a revised objective')
        previous = [row['id'] for row in conn.execute(
            'SELECT e.id FROM evidence e JOIN objective_task_rounds r ON r.task_id=e.task_id WHERE r.objective_id=? AND r.round=? '
            'AND e.id=(SELECT latest.id FROM evidence latest WHERE latest.task_id=e.task_id ORDER BY created DESC,id DESC LIMIT 1)',
            (objective_id, control['round']))]
        from eidolon_cli.organization_project_workspace import project_validation_view
        proof = project_validation_view(conn, objective_id)
        if proof:
            previous.append(proof['id'])
        if control['deliverable_id']:
            previous.append(control['deliverable_id'])
        for row in conn.execute("SELECT * FROM requests WHERE objective_id=? AND status NOT IN ('completed','cancelled')", (objective_id,)).fetchall():
            if row['id'] == completing_request:
                continue
            fence_receipts(conn, row['id'], 'Execution superseded by an owner-authorized objective replan.')
            conn.execute("UPDATE requests SET status='cancelled',token=NULL,lease=NULL,reason='Superseded by bounded replan' WHERE id=?", (row['id'],))
        conn.execute("UPDATE tasks SET status='cancelled' WHERE objective_id=? AND status!='completed'", (objective_id,))
        conn.execute("UPDATE objective_control SET round=round+1,status='replanning',summary=?,deliverable_id=NULL WHERE objective_id=?", (feedback, objective_id))
        objective = conn.execute('SELECT * FROM objectives WHERE id=?', (objective_id,)).fetchone()
        self._request(conn, objective_id, 'request.plan', self.settings.team, objective['priority'],
                      payload={'round': control['round'] + 1, 'feedback': feedback, 'evidenceIds': previous})
        self._event(conn, objective_id, 'A bounded objective replan is queued. Previous artifacts and decisions remain in history.', 'planning')

    def _record_usage(self, conn, request, result):
        usage = result.get('usage')
        values = [None, None]
        if usage is not None:
            if not isinstance(usage, dict) or set(usage) != {'inputTokens', 'outputTokens'}:
                raise ValueError('Usage must contain inputTokens and outputTokens')
            values = [usage[key] for key in ('inputTokens', 'outputTokens')]
            if any(value is not None and (type(value) is not int or not 0 <= value <= 2**63-1) for value in values):
                raise ValueError('Token usage must be nonnegative integers or unknown')
        if request['type'] in {'request.hire', 'request.apply', 'request.validate'}:
            values = [0, 0]
        conn.execute('UPDATE objective_usage SET input_tokens=?,output_tokens=?,completed=1 WHERE request_id=? AND token=?', (*values, request['id'], request['token']))
