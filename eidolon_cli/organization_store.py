"""Authoritative organization state. Model output proposes; transactions decide.

Kanban intentionally permits manual/self-certified completion and fallback profile
routing. Organization requests have stricter role, review and evidence contracts,
so they use their own profile-scoped ledger while sharing the AIAgent runtime.
"""
from __future__ import annotations

import contextlib
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
import uuid
from datetime import datetime, timezone

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_attention import ATTENTION_SCHEMA, OrganizationAttentionStore
from eidolon_cli.organization_history import HISTORY_SCHEMA, OrganizationHistoryStore, history_counts
from eidolon_cli.organization_coordination import COORDINATION_SCHEMA, OrganizationCoordinationStore, coordination_view
from eidolon_cli.organization_budget import BUDGET_SCHEMA, OrganizationBudgetStore, budget_reason, budget_view
from eidolon_cli.organization_acceptance import ACCEPTANCE_SCHEMA, OrganizationAcceptanceStore, final_artifact
from eidolon_cli.organization_owner import OWNER_SCHEMA, OrganizationOwnerStore
from eidolon_cli.organization_project_workspace import project_validation_view, project_validation_artifact
from eidolon_cli.organization_receipts import (
    RECEIPT_SCHEMA, OrganizationReceiptStore, evidence_receipts, fence_receipts,
)
from eidolon_cli.organization_staffing import STAFF_SCHEMA, OrganizationStaffingStore
from eidolon_cli.organization_edits import EDIT_SCHEMA, OrganizationEditStore, evidence_proposal
from eidolon_cli.organization_project_execution import (PROJECT_EXECUTION_SCHEMA, OrganizationProjectExecutionStore,
    project_execution_artifact, project_execution_view)
from eidolon_cli.organization_policy import POLICY_SCHEMA, OrganizationPolicyStore, resolve_settings
from eidolon_cli.organization_requests import (REQUEST_SCHEMA, OrganizationRequestStore)
from eidolon_cli.organization_management import MANAGEMENT_SCHEMA, OrganizationManagementStore
from eidolon_cli.organization_identity import (
    IDENTITY_SCHEMA, OrganizationIdentityStore, agent_context_view, agent_identity_view,
    task_assignment_view, objective_assignment_view,
)


_TERMINAL = {"completed", "cancelled"}
_PRIORITY = {"low": 1, "normal": 3, "high": 5, "P5": 1, "P4": 2, "P3": 3, "P2": 4, "P1": 5}
_TYPE = re.compile(r"^[a-z][a-z0-9_]{0,31}\.[a-z][a-z0-9_]{0,31}$")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS objectives (
 id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE, input_hash TEXT NOT NULL,
 title TEXT NOT NULL, description TEXT NOT NULL, priority INTEGER NOT NULL,
 created REAL NOT NULL, cancelled INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS agents (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL, manager_id TEXT,
 team TEXT NOT NULL, accepts TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tasks (
 id TEXT PRIMARY KEY, objective_id TEXT NOT NULL REFERENCES objectives(id), title TEXT NOT NULL,
 description TEXT NOT NULL, type TEXT NOT NULL, team TEXT NOT NULL, priority INTEGER NOT NULL,
 status TEXT NOT NULL, dependencies TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
 author_id TEXT, result TEXT, feedback TEXT);
CREATE TABLE IF NOT EXISTS requests (
 id TEXT PRIMARY KEY, objective_id TEXT NOT NULL REFERENCES objectives(id), task_id TEXT REFERENCES tasks(id),
 type TEXT NOT NULL, team TEXT NOT NULL, priority INTEGER NOT NULL, status TEXT NOT NULL,
 created REAL NOT NULL, agent_id TEXT REFERENCES agents(id), token TEXT, lease REAL,
 attempts INTEGER NOT NULL DEFAULT 0, reason TEXT, available REAL NOT NULL DEFAULT 0,
 payload TEXT NOT NULL DEFAULT '{}');
CREATE INDEX IF NOT EXISTS request_queue ON requests(status, priority DESC, created);
CREATE INDEX IF NOT EXISTS request_objective_status ON requests(objective_id,status);
CREATE TABLE IF NOT EXISTS evidence (
 id TEXT PRIMARY KEY, objective_id TEXT NOT NULL, task_id TEXT NOT NULL REFERENCES tasks(id),
 request_id TEXT NOT NULL UNIQUE REFERENCES requests(id), content TEXT NOT NULL,
 sha256 TEXT NOT NULL, summary TEXT NOT NULL, created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS evidence_task_latest ON evidence(task_id,created DESC,id DESC);
CREATE TABLE IF NOT EXISTS retry_receipts (
 idempotency_key TEXT PRIMARY KEY, request_id TEXT NOT NULL REFERENCES requests(id), created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS reviews (
 request_id TEXT PRIMARY KEY REFERENCES requests(id), task_id TEXT NOT NULL REFERENCES tasks(id),
 agent_id TEXT NOT NULL, approved INTEGER NOT NULL, summary TEXT NOT NULL,
 evidence_ids TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, objective_id TEXT, agent_id TEXT,
 kind TEXT NOT NULL, text TEXT NOT NULL, created REAL NOT NULL);
"""


def _id(prefix):
    return f"{prefix}_{uuid.uuid4().hex}"


def _iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat() if value is not None else None


def _text(value, field, limit=10000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{field} must be nonempty text of at most {limit} characters")
    return value.strip()


class OrganizationStore(OrganizationAttentionStore, OrganizationHistoryStore, OrganizationCoordinationStore, OrganizationProjectExecutionStore, OrganizationBudgetStore, OrganizationRequestStore, OrganizationManagementStore, OrganizationIdentityStore, OrganizationAcceptanceStore, OrganizationOwnerStore, OrganizationStaffingStore, OrganizationReceiptStore, OrganizationEditStore, OrganizationPolicyStore):
    def __init__(self, path: Path | str, settings: OrganizationSettings | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA + RECEIPT_SCHEMA + STAFF_SCHEMA + EDIT_SCHEMA + POLICY_SCHEMA + ACCEPTANCE_SCHEMA + OWNER_SCHEMA + IDENTITY_SCHEMA + REQUEST_SCHEMA + MANAGEMENT_SCHEMA + BUDGET_SCHEMA + PROJECT_EXECUTION_SCHEMA + COORDINATION_SCHEMA + HISTORY_SCHEMA + ATTENTION_SCHEMA)
            self.settings = resolve_settings(conn, settings)
        with self._write() as conn:
            self._migrate_reservations(conn)
            self._migrate_acceptance(conn)
            self._migrate_budgets(conn)
            self._migrate_project_execution_budgets(conn)
            self._adopt_policy(conn)
            roles = [("owner", "Owner", "Owner", None, []),
                     ("executive", "Executive", "Executive", "owner", ["request.accept"]),
                     ("director", "Staffing manager", "Manager", "executive", ["request.hire"]),
                     ("manager", "Manager", "Manager", "executive", ["request.plan", "request.integrate"]),
                     ("reviewer", "Reviewer", "Worker", "manager", ["request.review", "request.test_review"]),
                     ("control:project", "Project executor", "Worker", "manager", ["request.project_test", "request.source_integrate"]),
                     ("control:apply", "Workspace applier", "Worker", "manager", ["request.apply", "request.validate"])]
            for ident, name, role, manager, accepts in roles:
                conn.execute("INSERT OR IGNORE INTO agents VALUES (?,?,?,?,?,?)",
                             (ident, name, role, manager, self.settings.team, json.dumps(accepts)))
            # Capture legacy identity scope before synchronizing current routes.
            self._migrate_identities(conn)
            conn.execute("UPDATE agents SET team=? WHERE id IN ('owner','executive','director','manager','reviewer','control:apply','control:project')", (self.settings.team,))
            for ident, accepts in [('reviewer', ['request.review', 'request.test_review']), ('control:project', ['request.project_test', 'request.source_integrate']), ('executive', ['request.accept']), ('manager', ['request.plan', 'request.integrate']), ('control:apply', ['request.apply', 'request.validate'])]:
                conn.execute('UPDATE agents SET accepts=? WHERE id=?', (json.dumps(accepts), ident))
            self._sync_staff(conn)
            self._migrate_identities(conn)
            self._migrate_request_contracts(conn)
            self.migrate_open_project_validation(conn)
            self._migrate_attention(conn)
            for row in conn.execute("SELECT objective_id FROM objective_control WHERE status='pending'").fetchall():
                self._maybe_integrate(conn, row['objective_id'])

    @contextlib.contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA journal_mode=WAL")
            yield conn
        finally:
            conn.close()

    @contextlib.contextmanager
    def _write(self):
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous = (self.settings, getattr(self, '_policy_generation', None), getattr(self, '_policy_fingerprint', None))
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                self.settings, self._policy_generation, self._policy_fingerprint = previous
                raise

    @staticmethod
    def _event(conn, objective, text, kind="system", agent=None):
        conn.execute("INSERT INTO events(objective_id,agent_id,kind,text,created) VALUES (?,?,?,?,?)",
                     (objective, agent, kind, text, time.time()))

    @staticmethod
    def _request(conn, objective, request_type, team, priority, task=None, payload=None, *, requester_id=None):
        ident = _id("req")
        payload = dict(payload or {})
        if payload.get('evidenceIds'):
            hashes = {}
            for evidence_id in payload['evidenceIds']:
                artifact = conn.execute('SELECT sha256 FROM evidence WHERE id=? AND objective_id=?', (evidence_id, objective)).fetchone()
                if artifact is None:
                    artifact = conn.execute('SELECT sha256 FROM objective_deliverables WHERE id=? AND objective_id=?', (evidence_id, objective)).fetchone()
                if artifact is None:
                    project = project_validation_artifact(conn, evidence_id)
                    if project is not None and project['objectiveId'] == objective:
                        artifact = project
                if artifact is None:
                    project = project_execution_artifact(conn, evidence_id)
                    if project is not None and project['objectiveId'] == objective:
                        artifact = project
                if artifact is None:
                    raise ValueError('A new evidence-bound request requires persisted artifact hashes')
                hashes[evidence_id] = artifact['sha256']
            payload['evidenceHashes'] = hashes
        conn.execute("INSERT INTO requests(id,objective_id,task_id,type,team,priority,status,created,payload) VALUES (?,?,?,?,?,?,'queued',?,?)",
                     (ident, objective, task, request_type, team, priority, time.time(), json.dumps(payload or {})))
        OrganizationRequestStore._insert_request_contract(conn, ident, requester_id=requester_id)
        return ident

    def create_objective(self, title, description=None, priority="normal", *, idempotency_key, acceptance_criteria=None, delivery_mode="source_project", required_checks=None, executive_id=None, manager_id=None):
        title = _text(title, "Title", 500)
        description = _text(description or title, "Description", 30000)
        from eidolon_cli.organization_acceptance import acceptance_criteria as normalize_criteria
        criteria = normalize_criteria(acceptance_criteria, title if description == title else title + '\n\n' + description)
        from eidolon_cli.organization_acceptance import required_checks as normalize_checks
        checks = normalize_checks(required_checks if required_checks is not None else [])
        if delivery_mode not in ('managed_artifact', 'source_project'):
            raise ValueError('deliveryMode must be managed_artifact or source_project')
        key = _text(idempotency_key, "Idempotency key", 128)
        if not isinstance(priority, str) or priority not in _PRIORITY:
            raise ValueError("Unknown priority")
        level = _PRIORITY[priority]
        identity = ([title, description, level] if acceptance_criteria is None and delivery_mode == 'source_project' and not checks else
                    {'title': title, 'description': description, 'priority': level, 'acceptanceCriteria': criteria,
                     'deliveryMode': delivery_mode, 'requiredChecks': checks})
        if executive_id is not None or manager_id is not None:
            identity = {'objective': identity, 'executiveId': executive_id, 'managerId': manager_id}
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        with self._write() as conn:
            self._require_current_policy(conn)
            old = conn.execute("SELECT * FROM objectives WHERE idempotency_key=?", (key,)).fetchone()
            if old:
                if old["input_hash"] != digest:
                    raise ValueError("Idempotency key already belongs to a different objective")
                ident = old["id"]
            else:
                count = conn.execute("SELECT count(DISTINCT objective_id) FROM requests WHERE status NOT IN ('completed','cancelled')").fetchone()[0]
                if count >= self.settings.max_open_objectives:
                    raise ValueError("Organization is at its open-objective limit; finish or cancel existing work first")
                history = history_counts(conn)
                if history["current"] >= history["currentLimit"]:
                    raise ValueError("Current-objective capacity reached; archive completed or cancelled history before admitting new work")
                ident = _id("obj")
                conn.execute("INSERT INTO objectives VALUES (?,?,?,?,?,?,?,0)",
                             (ident, key, digest, title, description, level, time.time()))
                conn.execute('INSERT INTO project_execution_budgets VALUES (?,?)', (ident, self.settings.max_project_runs))
                manager = self._assign_objective(conn, ident, executive_id, manager_id)
                conn.execute("INSERT INTO objective_control(objective_id,criteria,status,round,max_replans,max_stages,delivery_mode,required_checks) VALUES (?,?,'pending',0,?,?,?,?)",
                             (ident, json.dumps(criteria), self.settings.max_replans, self.settings.max_stages, delivery_mode, json.dumps(checks)))
                self._initialize_budget(conn, ident)
                self._request(conn, ident, "request.plan", manager['team'], level)
                self._event(conn, ident, "Objective accepted. Manager planning is queued.", "planning")
        # A duplicate may be older than the UI's settled-history window.
        from eidolon_cli.organization_snapshot import build_snapshot
        with self._connect() as conn:
            return build_snapshot(conn, self.settings, objective_id=ident, resolution_options=self.allowed_owner_resolutions)["objectives"][0]

    def _eligible(self, conn, request):
        if request['type'] in {'request.project_test', 'request.source_integrate'} and self.project_stage_unavailability(conn, request):
            return [], None
        if request['type'] == 'request.validate' and self.edit_validate_unavailability(conn, request):
            return [], None
        if request['type'] == 'request.apply' and self.edit_apply_unavailability(conn, request):
            return [], None
        busy = {r[0] for r in conn.execute("SELECT agent_id FROM requests WHERE status='running'")}
        author = None
        if request["task_id"]:
            author = conn.execute("SELECT author_id FROM tasks WHERE id=?", (request["task_id"],)).fetchone()[0]
        staff = conn.execute("SELECT * FROM agents ORDER BY id").fetchall()
        candidates = []
        for agent in staff:
            if not self._assignment_allows(conn, request, agent) or not self._continuation_allows(conn, request, agent):
                continue
            typed = self._typed_eligible(conn, request, agent)
            if typed is False or self._staff_reason(conn, agent, request['type']):
                continue
            if (typed is not True and agent["team"] != request["team"]) or request["type"] not in json.loads(agent["accepts"]):
                continue
            if request["type"] == "request.test_review" and conn.execute(
                    "SELECT 1 FROM tasks WHERE objective_id=? AND author_id=?",
                    (request["objective_id"], agent["id"])).fetchone():
                continue
            if request["type"] == "request.review" and agent["id"] == author:
                continue
            candidates.append(agent)
        return candidates, next((a for a in candidates if a["id"] not in busy), None)

    @staticmethod
    def _dependencies_ready(conn, request):
        if not OrganizationRequestStore._request_dependencies_ready(conn, request):
            return False
        if not request["task_id"]:
            return True
        row = conn.execute("SELECT dependencies FROM tasks WHERE id=?", (request["task_id"],)).fetchone()
        return all(conn.execute("SELECT status FROM tasks WHERE id=?", (ident,)).fetchone()[0] == "completed"
                   for ident in json.loads(row[0]))

    def claim_next(self):
        with self._write() as conn:
            if not self._policy_current(conn):
                return None
            running = conn.execute("SELECT type,payload FROM requests WHERE status='running'").fetchall()
            # Membership changes have one short exclusive ledger stage. Queue
            # behind existing work, rather than converting capacity into an
            # unnecessary owner approval or fencing unrelated assignments.
            if len(running) >= self.settings.max_inflight or any(
                    row['type'] == 'request.hire' and 'managementProposal' in json.loads(row['payload'])
                    for row in running):
                return None
            rows = conn.execute("SELECT r.* FROM requests r JOIN objectives o ON o.id=r.objective_id WHERE r.status='queued' AND o.cancelled=0 AND r.available<=? ORDER BY r.priority + CAST(MAX(0, ? - r.created)/60 AS INTEGER) DESC,r.created,r.id", (time.time(), time.time())).fetchall()
            for request in rows:
                if running and request['type'] == 'request.hire' and 'managementProposal' in json.loads(request['payload']):
                    continue
                if not self._dependencies_ready(conn, request) or not self._coordination_ready(conn, request):
                    continue
                candidates, agent = self._eligible(conn, request)
                if not candidates:
                    if self._hiring_pending(conn, request):
                        continue
                    if self._ensure_route_hire(conn, request):
                        continue
                    self._pending(conn, request, self._route_reason(conn, request))
                    continue
                if agent is None:
                    continue
                reason = budget_reason(conn, request["objective_id"], self.settings)
                if reason:
                    self._pending(conn, request, reason)
                    continue
                if request["attempts"] >= self.settings.max_attempts:
                    self._pending(conn, request, "Attempt limit reached; automatic execution has stopped.")
                    continue
                control = conn.execute('SELECT * FROM objective_control WHERE objective_id=?', (request['objective_id'],)).fetchone()
                stages = conn.execute('SELECT count(*) FROM objective_usage WHERE objective_id=?', (request['objective_id'],)).fetchone()[0]
                if stages >= min(control['max_stages'], self.settings.max_stages):
                    self._pending(conn, request, 'Objective stage budget exhausted. Create a revised objective to authorize more work.')
                    continue
                if not self._validate_claim_write_scope(conn, request):
                    continue
                token = uuid.uuid4().hex
                lease = time.time() + self.settings.lease_seconds
                self._stamp_claim_policy(conn, request['id'])
                payload = json.loads(request['payload'])
                if payload.get('evidenceIds') and 'evidenceHashes' not in payload:
                    # Legacy open reviews are bound when first dispatched by the
                    # upgraded runtime, before any model sees the artifact bytes.
                    hashes = {}
                    for identifier in payload['evidenceIds']:
                        artifact = conn.execute('SELECT sha256 FROM evidence WHERE id=? AND objective_id=?',
                                                (identifier, request['objective_id'])).fetchone()
                        if artifact is None:
                            hashes = None
                            break
                        hashes[identifier] = artifact['sha256']
                    if hashes is None:
                        self._pending(conn, request, 'Legacy request evidence is missing; restore its exact artifact or request a bounded replan.')
                        continue
                    payload['evidenceHashes'] = hashes
                    conn.execute('UPDATE requests SET payload=? WHERE id=?', (json.dumps(payload), request['id']))
                self._reserve_task(conn, request)
                conn.execute("UPDATE requests SET status='running',agent_id=?,token=?,lease=?,attempts=attempts+1,reason=NULL WHERE id=?",
                             (agent["id"], token, lease, request["id"]))
                conn.execute('INSERT INTO objective_usage(request_id,token,objective_id,stage) VALUES (?,?,?,?)',
                             (request['id'], token, request['objective_id'], request['type']))
                if request["task_id"]:
                    status = "review" if request["type"] == "request.review" else "working"
                    conn.execute("UPDATE tasks SET status=? WHERE id=?", (status, request["task_id"]))
                if not request["reason"]:
                    self._event(conn, request["objective_id"], f"{agent['name']} claimed {request['type']}.", "delegation", agent["id"])
                return dict(conn.execute("SELECT * FROM requests WHERE id=?", (request["id"],)).fetchone())
        return None

    def _owned(self, conn, claim):
        if not self._policy_current(conn):
            return None
        return conn.execute("SELECT r.* FROM requests r JOIN objectives o ON o.id=r.objective_id JOIN request_policy p ON p.request_id=r.id WHERE r.id=? AND r.token=? AND r.status='running' AND r.lease>? AND o.cancelled=0 AND p.generation=?",
                            (claim["id"], claim["token"], time.time(), self._policy_generation)).fetchone()

    def heartbeat(self, claim):
        with self._write() as conn:
            request = self._owned(conn, claim)
            if request is None:
                return False
            if time.time() >= budget_view(conn, request['objective_id'], self.settings)['deadlineTimestamp']:
                self._pending(conn, request, 'Objective deadline reached; the in-flight result will not be accepted.')
                return False
            conn.execute("UPDATE requests SET lease=? WHERE id=?", (time.time() + self.settings.lease_seconds, claim["id"]))
            return True

    def context(self, claim):
        with self._connect() as conn:
            request = self._owned(conn, claim)
            if request is None:
                raise ValueError("Request lease is no longer owned")
            objective = dict(conn.execute("SELECT id,title,description FROM objectives WHERE id=?", (request["objective_id"],)).fetchone())
            objective.update(objective_assignment_view(conn, request['objective_id']))
            control = conn.execute('SELECT * FROM objective_control WHERE objective_id=?', (request['objective_id'],)).fetchone()
            objective['acceptanceCriteria'] = json.loads(control['criteria'])
            objective['round'] = control['round']
            objective['deliveryMode'] = control['delivery_mode']
            objective['requiredChecks'] = json.loads(control['required_checks'])
            objective['projectValidation'] = project_validation_view(conn, request['objective_id'])
            objective['projectExecution'] = project_execution_view(conn, request['objective_id'])
            if control['amended_scope']:
                objective['originalDescription'] = objective['description']
                objective['description'] = control['amended_scope']
            budget = budget_view(conn, request['objective_id'], self.settings)
            owner_inputs = [dict(row) for row in conn.execute('SELECT action,text,created FROM owner_resolutions WHERE objective_id=? ORDER BY created', (request['objective_id'],))]
            task = conn.execute("SELECT * FROM tasks WHERE id=?", (request["task_id"],)).fetchone()
            task = dict(task) if task else None
            if task:
                task.update(task_assignment_view(conn, task['id']))
                scope = conn.execute('SELECT paths FROM task_write_scopes WHERE task_id=?', (task['id'],)).fetchone()
                task['writePaths'] = json.loads(scope['paths']) if scope else None
                task['coordination'] = coordination_view(conn, task['id'])
            dependencies = []
            if task:
                for ident in json.loads(task["dependencies"]):
                    ev = conn.execute("SELECT * FROM evidence WHERE task_id=? ORDER BY created DESC LIMIT 1", (ident,)).fetchone()
                    if ev:
                        dependencies.append({"evidenceId": ev["id"], "taskId": ident, "summary": ev["summary"], "deliverable": ev["content"], "sha256": ev["sha256"],
                                             "toolReceipts": evidence_receipts(conn, ev['id']),
                                             'editProposal': evidence_proposal(conn, ev['id'])})
            payload = json.loads(request["payload"])
            if task and request["type"] != "request.review":
                previous = conn.execute("SELECT id FROM evidence WHERE task_id=? ORDER BY created DESC LIMIT 1", (task["id"],)).fetchone()
                if previous:
                    payload["evidenceIds"] = [previous[0]]
            evidence = []
            for ident in payload.get("evidenceIds", []):
                row = conn.execute("SELECT * FROM evidence WHERE id=? AND objective_id=?", (ident, request["objective_id"])).fetchone()
                if row is None:
                    artifact = final_artifact(conn, ident)
                    if artifact and artifact['objective_id'] == request['objective_id']:
                        evidence.append(artifact)
                    elif artifact is None:
                        project = project_validation_artifact(conn, ident) or project_execution_artifact(conn, ident)
                        if project is not None and project['objectiveId'] == request['objective_id']:
                            evidence.append(project)
                if row:
                    evidence.append({**dict(row), 'toolReceipts': evidence_receipts(conn, row['id']),
                                     'editProposal': evidence_proposal(conn, row['id'])})
            agent = dict(conn.execute("SELECT id,name,role,team,manager_id AS managerId FROM agents WHERE id=?", (request["agent_id"],)).fetchone())
            agent.update(agent_identity_view(conn, request['agent_id']))
            staff = self._staff(agent['id'])
            if staff:
                agent.update({'provider': staff.provider, 'model': staff.model, 'scope': staff.scope,
                              'authority': list(staff.authority), 'managedTeams': list(staff.managed_teams)})
            organization = {'agents': [
                {'id': row['id'], 'name': row['name'], 'role': row['role'], 'managerId': row['manager_id'],
                 'team': row['team'], 'capabilities': json.loads(row['accepts']),
                 'responsibilities': agent_identity_view(conn, row['id'])['responsibilities']}
                for row in conn.execute('SELECT * FROM agents ORDER BY id')]}
            return {"objective": objective, "task": task, "agent": agent,
                    **self._typed_context(conn, request),
                    "agentContext": agent_context_view(conn, request['agent_id']), "dependencies": dependencies,
                    "organization": organization, "maxInflight": self.settings.max_inflight,
                    "projectPolicy": {'recipes': [asdict(grant) for grant in self.settings.project_grants],
                                      'runner': 'Linux bubblewrap; fixed single-process Python unittest; no network or host writes'},
                    "managementPolicy": {"maxMembers": self.settings.max_members,
                        "memberCount": len(self._staffing_context()),
                        "maxRequestDepth": self.settings.max_request_depth,
                        "maxRequestsPerStage": self.settings.max_requests_per_stage,
                        "newMemberTools": [], "globalGrantExpansion": False},
                    "evidence": evidence, "feedback": task["feedback"] if task else payload.get("feedback", control["summary"]),
                    "ownerInputs": owner_inputs, "maxOutputTokens": self.settings.max_output_tokens,
                    "maxContextTokens": self.settings.max_context_tokens,
                    "costBudgetEnabled": budget["configuredCostLimitUsd"] is not None,
                    "toolReceipts": [receipt for item in evidence for receipt in item['toolReceipts']],
                    "toolPolicy": self._tool_policy(conn, request),
                    "staffing": self._staffing_context(),
                    "capabilities": list(self.settings.capabilities), "maxTasks": self.settings.max_tasks,
                    "maxWorkers": self.settings.max_workers, "timeoutSeconds": max(0.001, min(self.settings.timeout_seconds, budget["deadlineTimestamp"] - time.time())),
                    "workers": payload.get("workers")}

    @staticmethod
    def _pending(conn, request, reason):
        fence_receipts(conn, request['id'], 'Execution stopped before the tool outcome was confirmed.')
        conn.execute("UPDATE requests SET status='pending_intervention',reason=?,token=NULL,lease=NULL WHERE id=?", (reason[:2000], request["id"]))
        if request["task_id"]:
            conn.execute("UPDATE tasks SET status='blocked' WHERE id=?", (request["task_id"],))
        OrganizationStore._event(conn, request["objective_id"], reason[:2000], "blocker", request["agent_id"])

    def fail(self, claim, reason, retryable=False):
        reason = _text(reason, "Failure reason", 2000)
        with self._write() as conn:
            request = self._owned(conn, claim)
            if request is None:
                return False
            if retryable and request["attempts"] < self.settings.max_attempts:
                fence_receipts(conn, request['id'], 'Execution failed before the tool outcome was confirmed.')
                conn.execute("UPDATE requests SET status='queued',reason=?,token=NULL,lease=NULL,available=? WHERE id=?",
                             (reason, time.time() + 5 * request["attempts"], request["id"]))
                if request["task_id"]:
                    conn.execute("UPDATE tasks SET status='queued' WHERE id=?", (request["task_id"],))
                self._event(conn, request["objective_id"], f"Retry queued: {reason}", "blocker", request["agent_id"])
            else:
                self._pending(conn, request, reason)
            return True

    def defer(self, claim, reason):
        """Release admission when an occupied OS slot prevented any model call.

        Capacity waits are not failed attempts and need no owner intervention.
        Only the scheduler invokes this before entering an executor.
        """
        reason = _text(reason, "Capacity reason", 2000)
        with self._write() as conn:
            request = self._owned(conn, claim)
            if request is None:
                return False
            conn.execute("UPDATE requests SET status='queued',reason=?,token=NULL,lease=NULL,agent_id=NULL,attempts=MAX(0,attempts-1),available=? WHERE id=?",
                         (reason, time.time() + 1, request["id"]))
            conn.execute('DELETE FROM objective_usage WHERE request_id=? AND token=?', (request['id'], request['token']))
            if request["task_id"]:
                conn.execute("UPDATE tasks SET status='queued' WHERE id=?", (request["task_id"],))
            return True

    def recover_expired(self):
        with self._write() as conn:
            rows = conn.execute("SELECT * FROM requests WHERE status='running' AND lease<=?", (time.time(),)).fetchall()
            for request in rows:
                self._pending(conn, request, "Execution lease expired or the backend restarted. Its outcome is unconfirmed; review before retrying.")
            return len(rows)

    def cancel(self, objective_id):
        with self._write() as conn:
            row = conn.execute("SELECT * FROM objectives WHERE id=?", (objective_id,)).fetchone()
            if row is None:
                raise ValueError("Objective not found")
            if row["cancelled"]:
                return False
            if conn.execute("SELECT 1 FROM objective_history WHERE objective_id=? AND archived=1", (objective_id,)).fetchone():
                raise ValueError("Archived history cannot be cancelled; restore its history visibility first")
            for request in conn.execute("SELECT id FROM requests WHERE objective_id=?", (objective_id,)):
                fence_receipts(conn, request['id'], 'Objective cancelled before the tool outcome was confirmed.')
            conn.execute("UPDATE objectives SET cancelled=1 WHERE id=?", (objective_id,))
            conn.execute("UPDATE requests SET status='cancelled',token=NULL,lease=NULL,reason='Cancelled by owner' WHERE objective_id=? AND status NOT IN ('completed','cancelled')", (objective_id,))
            conn.execute("UPDATE tasks SET status='cancelled' WHERE objective_id=? AND status!='completed'", (objective_id,))
            self._event(conn, objective_id, "Owner cancelled this objective. In-flight model calls are being interrupted; no further results will be accepted.")
            return True

    def retry(self, request_id, *, idempotency_key=None):
        key = _text(idempotency_key or uuid.uuid4().hex, "Retry idempotency key", 128)
        with self._write() as conn:
            self._require_current_policy(conn)
            receipt = conn.execute("SELECT request_id FROM retry_receipts WHERE idempotency_key=?", (key,)).fetchone()
            if receipt is not None:
                if receipt[0] != request_id:
                    raise ValueError("Retry idempotency key belongs to a different request")
                return False
            row = conn.execute("SELECT r.*,o.cancelled FROM requests r JOIN objectives o ON o.id=r.objective_id WHERE r.id=?", (request_id,)).fetchone()
            if row is None:
                raise ValueError("Request not found")
            if row["cancelled"]:
                raise ValueError("Cancelled objectives cannot be retried")
            if row["status"] != "pending_intervention":
                return False
            reason = budget_reason(conn, row["objective_id"], self.settings)
            if reason:
                raise ValueError(reason)
            if row["attempts"] >= self.settings.max_attempts:
                raise ValueError("Attempt limit reached. Create a revised objective instead of replaying this request.")
            from eidolon_cli.organization_owner import resolution_count
            if resolution_count(conn, row['objective_id']) >= self.settings.max_owner_resolutions:
                raise ValueError('Owner resolution limit reached; create a revised objective')
            if self._typed_context(conn, row)['requestContract'].get('parentRequestId') is not None:
                raise ValueError('Linked requests require an exact response, not a replay')
            if row['type'] == 'request.project_failed':
                raise ValueError('Failed project tests require a bounded replan and new reviewed snapshot; this diagnostic gate cannot be replayed')
            if row['type'] == 'request.merge':
                raise ValueError('Source handoffs require an exact record_handoff resolution, not a replay')
            conn.execute("UPDATE requests SET status='queued',reason=NULL,available=0,agent_id=NULL WHERE id=?", (request_id,))
            conn.execute("INSERT INTO retry_receipts VALUES (?,?,?)", (key, request_id, time.time()))
            if row["task_id"]:
                conn.execute("UPDATE tasks SET status='queued' WHERE id=?", (row["task_id"],))
            self._event(conn, row["objective_id"], "Owner requested a bounded retry.", "planning")
            return True

    def retry_recorded(self, request_id, idempotency_key):
        if not idempotency_key:
            return False
        with self._connect() as conn:
            row = conn.execute("SELECT request_id FROM retry_receipts WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if row is not None and row[0] != request_id:
                raise ValueError("Retry idempotency key belongs to a different request")
            return row is not None

    def finish(self, claim, result):
        if not isinstance(result, dict):
            raise ValueError("Agent result must be an object")
        if result.get("intervention"):
            reason = _text(result["intervention"], "Intervention reason", 2000)
            with self._write() as conn:
                request = self._owned(conn, claim)
                if request is None:
                    return False
                if time.time() >= budget_view(conn, request['objective_id'], self.settings)['deadlineTimestamp']:
                    self._pending(conn, request, 'Objective deadline reached; the result was not accepted.')
                    return False
                self._record_usage(conn, request, result)
                if request['type'] == 'request.plan':
                    self._merge_required_checks(conn, request['objective_id'], result.get('requiredChecks', []))
                self._pending(conn, request, reason)
                return True
        with self._write() as conn:
            request = self._owned(conn, claim)
            if request is None:
                return False
            if time.time() >= budget_view(conn, request['objective_id'], self.settings)['deadlineTimestamp']:
                self._pending(conn, request, 'Objective deadline reached; the result was not accepted.')
                return False
            self._verify_context_completion(conn, request, result)
            if 'requests' in result:
                if set(result) - {'requests', 'usage', 'memory'}:
                    raise ValueError('A paused stage may only return its typed requests')
                self._record_usage(conn, request, result)
                self._raise_requests(conn, request, result['requests'])
                return True
            handlers = {"request.question": self._finish_response, "request.decision": self._finish_response,
                        "request.plan": self._finish_plan, "request.review": self._finish_review,
                        "request.hire": self._finish_hire, 'work.edit': self._finish_edit_work,
                        'request.apply': self._finish_apply,
                        'request.project_test': self._finish_project_test, 'request.test_review': self._finish_test_review,
                        'request.source_integrate': self._finish_source_integration,
                        'request.integrate': self._finish_integrate, 'request.accept': self._finish_accept}
            if request['type'] == 'request.validate':
                handler = self._finish_validate
            else:
                handler = handlers.get(request["type"], self._finish_work)
            self._record_usage(conn, request, result)
            handler(conn, request, {key: value for key, value in result.items() if key not in {'usage', 'memory'}})
            self._remember_agent_finish(conn, request, result)
            conn.execute("UPDATE requests SET status='completed',token=NULL,lease=NULL,reason=NULL WHERE id=?", (request["id"],))
            self._event(conn, request["objective_id"], f"{request['type']} finished.", "review" if request["type"] == "request.review" else "completion", request["agent_id"])
            self._resume_answered_parent(conn, request)
            self._maybe_integrate(conn, request['objective_id'])
            return True

    def _finish_plan(self, conn, request, result):
        self._merge_required_checks(conn, request['objective_id'], result.get('requiredChecks', []))
        tasks = result.get("tasks")
        if not isinstance(tasks, list) or not 1 <= len(tasks) <= self.settings.max_tasks:
            raise ValueError(f"Plan must contain 1–{self.settings.max_tasks} tasks")
        from eidolon_cli.organization_loop import reject_duplicate_tasks
        reject_duplicate_tasks(tasks)
        normalized = []
        ids = [_id("task") for _ in tasks]
        for index, task in enumerate(tasks):
            if not isinstance(task, dict):
                raise ValueError("Each planned task must be an object")
            request_type = _text(task.get("type"), "Request type", 65)
            if not _TYPE.fullmatch(request_type) or request_type.startswith("request."):
                raise ValueError("Planned tasks must use a work capability type; control requests are backend-owned")
            dependencies = task.get("dependsOn", [])
            if not isinstance(dependencies, list) or any(type(i) is not int or not 0 <= i < index for i in dependencies) or len(set(dependencies)) != len(dependencies):
                raise ValueError("Task dependencies must reference unique earlier task indexes")
            team = _text(task.get("team", self.settings.team), "Team", 64)
            normalized.append((ids[index], request["objective_id"], _text(task.get("title"), "Task title", 500),
                               _text(task.get("description"), "Task description", 10000), request_type, team,
                               request["priority"], "queued", json.dumps([ids[i] for i in dependencies])))
        workers = result.get("workers", 1)
        if type(workers) is not int or workers < 1 or workers > 64:
            raise ValueError("Requested worker count must be an integer from 1 to 64")
        for values, specification in zip(normalized, tasks):
            conn.execute("INSERT INTO tasks(id,objective_id,title,description,type,team,priority,status,dependencies) VALUES (?,?,?,?,?,?,?,?,?)", values)
            self._assign_task(conn, values[0], specification, request['agent_id'])
            self._set_write_scope(conn, values[0], specification)
            conn.execute('INSERT INTO objective_task_rounds SELECT ?,objective_id,round FROM objective_control WHERE objective_id=?', (values[0], request['objective_id']))
            self._request(conn, request["objective_id"], values[4], values[5], values[6], values[0],
                          requester_id=request["agent_id"])
        self._queue_staffing(conn, request, workers, normalized)
        self._event(conn, request["objective_id"], f"Manager created {len(tasks)} scoped tasks with explicit dependencies.", "planning", request["agent_id"])

    def _finish_work(self, conn, request, result):
        if request["type"] not in self.settings.capabilities or request["task_id"] is None:
            raise ValueError("No executor is authorized for this request type")
        summary = _text(result.get("summary"), "Work summary", 10000)
        content = _text(result.get("deliverable"), "Deliverable", 100000)
        evidence = _id("evidence")
        conn.execute("INSERT INTO evidence VALUES (?,?,?,?,?,?,?,?)",
                     (evidence, request["objective_id"], request["task_id"], request["id"], content,
                      hashlib.sha256(content.encode()).hexdigest(), summary, time.time()))
        self._link_tool_evidence(conn, request, evidence)
        conn.execute("UPDATE tasks SET status='review',author_id=?,result=? WHERE id=?", (request["agent_id"], summary, request["task_id"]))
        self._request(conn, request["objective_id"], "request.review", request["team"], request["priority"], request["task_id"], {"evidenceIds": [evidence]},
                      requester_id=request["agent_id"])
        self._event(conn, request["objective_id"], "Deliverable saved; independent evidence-bound review requested.", "review", request["agent_id"])

    def _finish_review(self, conn, request, result):
        payload = json.loads(request["payload"])
        expected = payload["evidenceIds"]
        actual = result.get("evidenceIds")
        if (not expected or not isinstance(actual, list) or any(not isinstance(item, str) for item in actual)
                or set(actual) != set(expected) or len(actual) != len(expected)):
            raise ValueError("Review must identify exactly the persisted deliverable evidence")
        approved = result.get("approved")
        if type(approved) is not bool:
            raise ValueError("Review decision must be a boolean")
        summary = _text(result.get("summary"), "Review summary", 10000)
        task = conn.execute("SELECT * FROM tasks WHERE id=?", (request["task_id"],)).fetchone()
        if task["author_id"] == request["agent_id"]:
            raise ValueError("Workers cannot approve their own deliverable")
        for ident in expected:
            ev = conn.execute("SELECT * FROM evidence WHERE id=? AND task_id=?", (ident, task["id"])).fetchone()
            if ev is None or hashlib.sha256(ev["content"].encode()).hexdigest() != ev["sha256"] or (payload.get("evidenceHashes") is not None and payload["evidenceHashes"].get(ident) != ev["sha256"]):
                raise ValueError("Persisted evidence is missing or has changed")
            self._verify_tool_evidence(conn, ident, require_read=task['type'] in {'work.inspect', 'work.edit'})
        self.validate_edit_review(conn, request, result)
        conn.execute("INSERT INTO reviews VALUES (?,?,?,?,?,?,?)",
                     (request["id"], task["id"], request["agent_id"], int(approved), summary, json.dumps(expected), time.time()))
        if self.on_edit_review(conn, request, task, approved):
            return
        if approved:
            conn.execute("UPDATE tasks SET status='completed',feedback=? WHERE id=?", (summary, task["id"]))
        elif task["revision"] < self.settings.max_revisions:
            conn.execute("UPDATE tasks SET status='queued',revision=revision+1,feedback=? WHERE id=?", (summary, task["id"]))
            self._request(conn, request["objective_id"], task["type"], task["team"], task["priority"], task["id"],
                          requester_id=request["agent_id"])
            self._event(conn, request["objective_id"], "Reviewer requested changes; a bounded revision is queued.", "review", request["agent_id"])
        else:
            ident = self._request(conn, request["objective_id"], task["type"], task["team"], task["priority"], task["id"],
                                  requester_id=request["agent_id"])
            pending = conn.execute("SELECT * FROM requests WHERE id=?", (ident,)).fetchone()
            self._pending(conn, pending, "Review revision limit reached. Owner intervention is required: " + summary[:1500])
            # This intervention is terminal for this objective: retries must not bypass the revision budget.
            conn.execute("UPDATE requests SET attempts=? WHERE id=?", (self.settings.max_attempts, ident))

    def snapshot(self):
        from eidolon_cli.organization_snapshot import build_snapshot
        with self._connect() as conn:
            conn.execute("BEGIN")
            result = build_snapshot(conn, self.settings, resolution_options=self.allowed_owner_resolutions)
            result['runtime']['management'] = self.management_view(conn)
            if not self._policy_current(conn):
                for request in result['requests']:
                    request['allowedResolutions'] = []
                result['runtime'].update({'state': 'policy_changed', 'capabilities': [], 'readRoots': [],
                    'readFileEnabled': False, 'workspaceApplyEnabled': False,
                    'scope': 'Organization grants or routing changed. Wait for active work to stop, then refresh Organization before submitting or retrying work.'})
            return result

    def evidence(self, evidence_id):
        ident = _text(evidence_id, "Evidence ID", 128)
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM evidence WHERE id=?", (ident,)).fetchone()
            if row is None:
                final = final_artifact(conn, ident)
                if final is None:
                    project = project_validation_artifact(conn, ident)
                    if project is not None:
                        return {**project, 'taskId': None, 'kind': 'project_validation'}
                    execution = project_execution_artifact(conn, ident)
                    if execution is not None:
                        return execution
                    raise ValueError("Evidence not found in this organization")
                return {'id': final['id'], 'objectiveId': final['objective_id'], 'taskId': None,
                        'content': final['content'], 'summary': final['summary'], 'sha256': final['sha256'],
                        'toolReceipts': [], 'createdAt': _iso(final['created']), 'kind': 'integrated_deliverable'}
            proposal = evidence_proposal(conn, row['id'])
            return {"id": row["id"], "objectiveId": row["objective_id"], "taskId": row["task_id"],
                    "content": row["content"], "summary": row["summary"], "sha256": row["sha256"],
                    "toolReceipts": evidence_receipts(conn, row['id']),
                    **({'editProposal': proposal} if proposal is not None else {}),
                    "createdAt": _iso(row["created"])}
