"""Selective recovery for scheduler claims with a durable dispatch boundary.

An absent receipt is never evidence of safety. Only claims explicitly journaled
by the scheduler qualify. Unknown dispatched and legacy attempts retain all
usage and require intervention. Exact source receipts permit reconciliation only;
completion remains an atomic store commit.
"""
from __future__ import annotations

import hashlib
import json
import time

RECOVERY_SCHEMA = """
CREATE TABLE IF NOT EXISTS organization_execution_journal (
 request_id TEXT NOT NULL REFERENCES requests(id), token TEXT NOT NULL,
 input_hash TEXT NOT NULL, policy_generation INTEGER NOT NULL,
 dispatched INTEGER NOT NULL DEFAULT 0 CHECK(dispatched IN (0,1)),
 PRIMARY KEY(request_id,token));
CREATE TABLE IF NOT EXISTS organization_reconcile_only (
 request_id TEXT PRIMARY KEY REFERENCES requests(id), receipt_sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS organization_project_reconcile_only (
 request_id TEXT PRIMARY KEY REFERENCES requests(id), run_id TEXT NOT NULL,
 recipe TEXT NOT NULL, artifact_sha256 TEXT NOT NULL);
"""


def input_hash(request):
    values = {key: request[key] for key in ('id', 'objective_id', 'task_id', 'type', 'team', 'payload')}
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


class OrganizationRecoveryStore:
    def _project_reconcile_guard(self, conn, request, row=None, artifact=None):
        """A recovered test may only consume its exact retained typed receipt."""
        marker = conn.execute('SELECT * FROM organization_project_reconcile_only WHERE request_id=?',
                              (request['id'],)).fetchone()
        if marker is not None:
            recipe = json.loads(row['grant_record'])['execution']['recipe'] if row is not None else None
            if (row is None or artifact is None or row['id'] != marker['run_id']
                    or recipe != marker['recipe'] or artifact['sha256'] != marker['artifact_sha256']):
                raise ValueError('Recovery requires its exact durable project receipt; execution cannot be repeated')
        return marker

    def _journal_claim(self, conn, request_id, token):
        request = conn.execute('SELECT * FROM requests WHERE id=?', (request_id,)).fetchone()
        conn.execute('INSERT INTO organization_execution_journal VALUES (?,?,?,?,0)',
                     (request_id, token, input_hash(request), self._policy_generation))

    def begin_dispatch(self, claim):
        """Fence the old process durably before executing any runtime callback."""
        with self._write() as conn:
            request = self._owned(conn, claim)
            if request is None:
                return False
            from eidolon_cli.organization_objective_dependencies import objective_dependencies_satisfied, ObjectiveDependencyError
            if not objective_dependencies_satisfied(conn, request['objective_id']):
                raise ObjectiveDependencyError('stale_dependency_before_dispatch')
            row = conn.execute('SELECT * FROM organization_execution_journal WHERE request_id=? AND token=?',
                               (claim['id'], claim['token'])).fetchone()
            if row is None:
                # A directly admitted/legacy claim can still enter the runtime,
                # but gets no pre-dispatch recovery privilege retroactively.
                conn.execute('INSERT INTO organization_execution_journal VALUES (?,?,?,?,1)',
                             (claim['id'], claim['token'], input_hash(request), self._policy_generation))
                return True
            if row['dispatched'] or row['input_hash'] != input_hash(request):
                return False
            conn.execute('UPDATE organization_execution_journal SET dispatched=1 WHERE request_id=? AND token=?',
                         (claim['id'], claim['token']))
            return True

    def _recover_expired(self, conn, request):
        row = conn.execute('SELECT * FROM organization_execution_journal WHERE request_id=? AND token=?',
                           (request['id'], request['token'])).fetchone()
        eligible = (row is not None and self._policy_current(conn)
                and row['policy_generation'] == self._policy_generation
                and row['input_hash'] == input_hash(request)
                and request['attempts'] < self.settings.max_attempts)
        durable_source = False
        if eligible and row['dispatched'] and request['type'] == 'request.source_integrate':
            receipt = conn.execute('SELECT * FROM project_source_receipts WHERE request_id=?',
                                   (request['id'],)).fetchone()
            if receipt is not None:
                from eidolon_cli.organization_project_execution import digest
                try:
                    durable_source = (receipt['run_id'] == json.loads(request['payload']).get('runId')
                                      and digest(json.loads(receipt['record'])) == receipt['sha256'])
                except (TypeError, ValueError):
                    durable_source = False
        durable_project = None
        if eligible and row['dispatched'] and request['type'] == 'request.project_test':
            try:
                # Preflight includes unknown-cleanup blockers and exact grants.
                if self.project_stage_unavailability(conn, request) is None:
                    retained = self._reusable_project_run(conn, request)
                    if retained is not None:
                        from eidolon_cli.organization_project_execution import project_execution_artifact
                        artifact = project_execution_artifact(conn, retained['id'])
                        marker = self._project_reconcile_guard(conn, request, retained, artifact)
                        recipe = json.loads(retained['grant_record'])['execution']['recipe']
                        if (recipe in {'python_unittest', 'python_pytest', 'node_test'}
                                and (retained['token'] == request['token'] or marker is not None)):
                            durable_project = (retained['id'], recipe, artifact['sha256'])
            except (ValueError, TypeError, KeyError):
                durable_project = None
        safe = eligible and (not row['dispatched'] or durable_source or durable_project is not None)
        # Defense in depth for direct store consumers: any admitted model/tool/
        # project operation overrides even an untouched scheduler marker.
        if safe and not durable_source and durable_project is None:
            safe = not any(conn.execute(query, args).fetchone() for query, args in (
                ('SELECT 1 FROM organization_model_calls WHERE request_id=? AND token=?', (request['id'], request['token'])),
                ('SELECT 1 FROM tool_receipts WHERE request_id=? AND attempt=?', (request['id'], request['execution_count'])),
                ('SELECT 1 FROM project_run_starts WHERE request_id=? AND token=?', (request['id'], request['token'])),
            ))
        if safe:
            if durable_project is not None:
                conn.execute('INSERT OR IGNORE INTO organization_project_reconcile_only VALUES (?,?,?,?)',
                             (request['id'], *durable_project))
            if durable_source:
                conn.execute('INSERT OR IGNORE INTO organization_reconcile_only VALUES (?,?)',
                             (request['id'], receipt['sha256']))
            # Admission on the next claim rechecks cancellation, budget, grants,
            # source state, routing and dependencies. Never refund reservations,
            # erase prior attempts or advance the old execution's lease.
            conn.execute("UPDATE requests SET status='queued',token=NULL,lease=NULL,agent_id=NULL,available=?,reason=? WHERE id=?",
                         (time.time() + 1, 'Reconciling an exact durable project receipt.' if durable_source or durable_project is not None else 'Recovered a recorded attempt that never reached dispatch.', request['id']))
            if request['task_id']:
                conn.execute("UPDATE tasks SET status='queued' WHERE id=?", (request['task_id'],))
            self._event(conn, request['objective_id'], 'Recorded execution recovered; original limits remain in force.', 'planning')
        else:
            self._pending(conn, request, 'Execution lease expired or the backend restarted. Its outcome is unconfirmed; review before retrying.')
