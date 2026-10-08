"""Real organization execution with bounded, predeclared owner responses.

The loopback provider is deliberately synthetic, including its first wrong bucket
candidate. It exercises runtime behavior and is never intelligence evidence. Live
routes use the very same default OrganizationService executor, with no injected
plan, test result, review, integration, or acceptance.
"""
from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import threading
import time

from scripts.eval_organization_project import _git, configuration, isolated_profile
from scripts.organization_trial_scenarios import TrialScenario

TRIAL_LIMITS = {
    "max_workers": 1, "max_inflight": 1, "max_tasks": 2, "max_attempts": 2,
    "max_revisions": 1, "max_replans": 1, "max_stages": 32,
    "max_context_tokens": 65536, "max_output_tokens": 2048,
    "max_model_calls": 24, "max_total_tokens": 1572864,
    "timeout_seconds": 60, "objective_timeout_seconds": 180,
    "max_tool_calls": 3, "max_project_runs": 2, "max_owner_resolutions": 3,
}
_REPLAN_TEXT = (
    "The retained public project tests genuinely failed. Inspect their exact failure evidence, "
    "repair the implementation to the unchanged specification, preserve every public test byte, "
    "and use the one remaining bounded project run. Do not weaken criteria or expand permissions."
)


def trial_configuration(source: Path, route: dict, scenario: TrialScenario, limits: dict) -> dict:
    from scripts.organization_trial_policy import budget_configuration
    cfg = configuration(source, route)
    organization = cfg["organization"]
    organization.update(limits)
    organization.update(budget_configuration(route))
    organization["project_grants"] = [{
        "id": scenario.scenario_id,
        "files": ["root0/" + name for name in (*scenario.source_files, *scenario.public_test_files)],
        "execution": {"recipe": "python_unittest", "timeout_seconds": 10},
    }]
    return cfg


def _genuine_failure(run: dict | None) -> bool:
    receipt = (run or {}).get("receipt", {})
    return bool(receipt.get("status") == "failed" and receipt.get("exitCode") not in {None, 0}
                and receipt.get("testCount", 0) > 0 and receipt.get("isolation", {}).get("established"))


def permitted_owner_response(scenario: TrialScenario, request: dict, objective: dict,
                             actions: list[dict]) -> dict | None:
    """Only scenario-declared clarifications and one actual test-failure replan."""
    if request["status"] != "pending_intervention":
        return None
    permitted = {item["action"] for item in request.get("allowedResolutions", [])}
    if scenario.owner_answer and not any(item["action"] in {"answer_request", "provide_input"} for item in actions):
        question = " ".join(str(request.get(key) or "") for key in ("requestedOutcome", "reason")).lower()
        topic = bool(re.search(r"\b(duplicates?|deduplication|deduplicate|retention)\b", question))
        topic = topic or bool(re.search(r"\b(ids?|identifiers?|keys?)\b", question)
                              and re.search(r"\b(records?|rows?|occurrences?|repeated|collisions?)\b", question))
        topic = topic or bool(re.search(r"\b(first|earliest)\b", question)
                              and re.search(r"\b(last|latest)\b", question))
        choice = bool(re.search(r"\b(which|first|last|earliest|latest|keep|retain|survive|survives)\b", question))
        if topic and choice:
            if request["type"] == "request.question" and "answer_request" in permitted:
                return {"action": "answer_request", "text": scenario.owner_answer}
            if "provide_input" in permitted:
                return {"action": "provide_input", "text": scenario.owner_answer}
    already_replanned = any(item["action"] == "request_replan" for item in actions)
    if (request["type"] == "request.project_failed" and "request_replan" in permitted
            and not already_replanned and _genuine_failure(objective.get("projectExecution"))):
        # A clarification scenario may also make one bounded repair; no hidden
        # oracle feedback is ever returned to the model through this path.
        return {"action": "request_replan", "text": _REPLAN_TEXT}
    return None


def _checkpoint(store) -> dict:
    with store._connect() as conn:
        return {
            "completedRequests": [row[0] for row in conn.execute("SELECT id FROM requests WHERE status='completed' ORDER BY id")],
            "projectReceipts": [list(row) for row in conn.execute("SELECT run_id,sha256 FROM project_run_results ORDER BY run_id")],
            "toolReceipts": [list(row) for row in conn.execute(
                "SELECT id,status,result_sha256 FROM tool_receipts WHERE completed IS NOT NULL ORDER BY id")],
            "ownerReceipts": [list(row) for row in conn.execute("SELECT idempotency_key,input_hash FROM owner_resolutions ORDER BY idempotency_key")],
            "responseReceipts": [list(row) for row in conn.execute("SELECT idempotency_key,input_hash FROM request_response_receipts ORDER BY idempotency_key")],
        }


def _apply_response(service, request_id: str, payload: dict, key: str) -> bool:
    if payload["action"] == "answer_request":
        return service.respond(request_id, text=payload["text"], decision="answered", idempotency_key=key)
    return service.resolve(request_id, action=payload["action"], text=payload["text"], idempotency_key=key)


def _project_history(store) -> list[dict]:
    with store._connect() as conn:
        identifiers = [row[0] for row in conn.execute("SELECT id FROM project_run_starts ORDER BY created,id")]
    return [store.evidence(identifier) for identifier in identifiers]


def run_trial(root: Path, route: dict, scenario: TrialScenario, *, live: bool = False,
              limits: dict | None = None) -> dict:
    """Run a fresh trial, retaining source/profile for the caller's private oracle.

    The caller performs native isolation preflight and freezes its oracle before
    opening a provider. This function never executes private tests or deletes root.
    """
    from eidolon_cli.organization_config import OrganizationSettings
    from eidolon_cli.organization_service import OrganizationService
    from eidolon_cli.organization_store import OrganizationStore

    limits = {**TRIAL_LIMITS, **(limits or {})}
    root = Path(root).resolve()
    source, home = root / "source", root / "profile"
    source.mkdir(parents=True)
    home.mkdir()
    files = {**scenario.source_files, **scenario.public_test_files}
    for name, content in files.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8"))
    _git(source, "init", "--initial-branch=main")
    _git(source, "add", "--", *files)
    _git(source, "-c", "user.name=Local evaluation", "-c", "user.email=evaluation@localhost", "commit", "-m", "Disposable semantic fixture")
    original_commit = _git(source, "rev-parse", "HEAD")
    original_index = (source / ".git" / "index").read_bytes()
    cfg = trial_configuration(source, route, scenario, limits)
    (home / "config.yaml").write_text(json.dumps(cfg), encoding="utf-8")
    actions, restart = [], {"exercised": False, "boundary": scenario.restart_boundary,
                            "sameProfile": False, "receiptsPreserved": False}
    stop_reason, service_stopped = None, False
    with isolated_profile(home, route["secret"]):
        settings = OrganizationSettings.from_config(cfg)
        state_path = home / "organization" / "state.db"
        store = OrganizationStore(state_path, settings)
        store.create_objective(
            "Semantic trial: " + scenario.scenario_id, scenario.objective,
            idempotency_key="semantic-trial-once", acceptance_criteria=list(scenario.acceptance_criteria),
            delivery_mode="source_project", required_checks=["project_tests", "managed_validation", "source_integration"],
        )
        service = OrganizationService(store, home=home, settings=settings, poll_seconds=0.01)
        deadline = time.monotonic() + limits["objective_timeout_seconds"]
        try:
            service.start()
            while time.monotonic() < deadline:
                snapshot = store.snapshot()
                objective = snapshot["objectives"][0]
                if objective["status"] in {"completed", "cancelled"}:
                    break
                pending = [item for item in snapshot["requests"] if item["status"] == "pending_intervention"]
                if not pending:
                    time.sleep(0.03)
                    continue
                # Stop/reopen only at a durable idle boundary. No in-flight call
                # is deliberately cancelled to manufacture restart coverage.
                if any(item["status"] == "running" for item in snapshot["requests"]):
                    time.sleep(0.03)
                    continue
                choices = [(item, permitted_owner_response(scenario, item, objective, actions)) for item in pending]
                choice = next(((item, payload) for item, payload in choices if payload), None)
                if choice is None:
                    stop_reason = "Unexpected or unsupported intervention; no predeclared owner response applies"
                    break
                request, payload = choice
                if len(actions) >= limits["max_owner_resolutions"]:
                    stop_reason = "Owner response limit reached"
                    break
                boundary_matches = (
                    scenario.restart_boundary == "owner_clarification_pending" and payload["action"] in {"answer_request", "provide_input"}
                ) or (
                    scenario.restart_boundary == "failed_project_test_pending" and payload["action"] == "request_replan"
                )
                if boundary_matches and not restart["exercised"]:
                    if not service.stop(timeout=limits["timeout_seconds"] + 10):
                        stop_reason = "Service did not stop at restart boundary"
                        break
                    before = _checkpoint(store)
                    store = OrganizationStore(state_path, settings)
                    service = OrganizationService(store, home=home, settings=settings, poll_seconds=0.01)
                    restart.update(exercised=True, sameProfile=True, receiptsPreserved=before == _checkpoint(store),
                                   requestId=request["id"], checkpoint=before)
                    if not restart["receiptsPreserved"]:
                        stop_reason = "Persisted receipts changed across service restart"
                        break
                with store._connect() as conn:
                    edits_before_answer = conn.execute("SELECT count(*) FROM edit_applications").fetchone()[0]
                key = "trial-owner-" + str(len(actions))
                changed = _apply_response(service, request["id"], payload, key)
                actions.append({**payload, "requestId": request["id"], "parentRequestId": request.get("parentRequestId"),
                                "idempotencyKey": key, "recorded": changed, "managedEditsBeforeResponse": edits_before_answer})
            else:
                stop_reason = "Finite trial deadline reached"
        finally:
            service_stopped = service.stop(timeout=limits["timeout_seconds"] + 10)
        snapshot = store.snapshot()
        objective = snapshot["objectives"][0]
        # Reopen after execution and replay exact owner actions. No new action or
        # scheduling is permitted: every receipt must already be recorded.
        reopened = OrganizationStore(state_path, settings)
        receipt_replay = True
        for action in actions:
            kwargs = {"text": action["text"], "idempotency_key": action["idempotencyKey"]}
            if action["action"] == "answer_request":
                recorded = reopened.response_recorded(action["requestId"], decision="answered", **kwargs)
                replay = reopened.respond(action["requestId"], decision="answered", **kwargs) if recorded else True
            else:
                recorded = reopened.resolution_recorded(action["requestId"], action=action["action"], **kwargs)
                replay = reopened.resolve(action["requestId"], action=action["action"], **kwargs) if recorded else True
            receipt_replay = receipt_replay and recorded and replay is False
        runs = _project_history(store)

    original_unchanged = (
        _git(source, "rev-parse", "HEAD") == original_commit
        and (source / ".git" / "index").read_bytes() == original_index
        and all((source / name).read_bytes() == content.encode("utf-8") for name, content in files.items())
        and not _git(source, "status", "--porcelain")
    )
    run = objective["projectExecution"]
    integration = run["sourceIntegration"] if run else None
    delivered_commit = integration["commit"] if integration and integration["status"] == "integrated" else None
    delivered = {name: _git(source, "show", delivered_commit + ":" + name, exact=True)
                 for name in files} if delivered_commit else None
    tests_preserved = bool(delivered and all(delivered.get(name) == content for name, content in scenario.public_test_files.items()))
    accepted = objective["status"] == "completed"
    clarification = next((item for item in actions if item["action"] in {"answer_request", "provide_input"}), None)
    resumed_id = (clarification.get("parentRequestId") or clarification["requestId"]) if clarification else None
    clarification_resumed = bool(resumed_id and any(row["id"] == resumed_id and row["status"] == "completed" for row in snapshot["requests"]))
    outcomes = [item["projectExecution"] for item in runs if item]
    recovery = bool(any(item["action"] == "request_replan" for item in actions)
                    and len(outcomes) > 1 and outcomes[0]["status"] == "failed" and outcomes[-1]["status"] == "passed")
    checks = {"originalSourceAndIndexUnchanged": original_unchanged, "serviceStopped": service_stopped,
              "publicTestsUnchanged": tests_preserved, "ownerReceiptReplayIdempotent": receipt_replay,
              "objectiveAccepted": accepted, "noRemotePush": not integration or integration["remotePushPerformed"] is False,
              "verifiedNewSourceBranch": bool(integration and integration["status"] == "integrated"
                  and integration["sourceBaseCommit"] == original_commit
                  and _git(source, "rev-parse", integration["ref"]) == delivered_commit
                  and integration["workingTreeWritesPerformed"] is False and integration["indexWritesPerformed"] is False)}
    task_success = all(checks.values())
    return {
        "store": store, "source": source, "home": home, "objective_id": objective["id"],
        "original_commit": original_commit, "delivered_commit": delivered_commit, "snapshot": snapshot,
        "mode": "live_model" if live else "synthetic_runtime_trial", "scenarioId": scenario.scenario_id,
        "taskSuccess": task_success, "checks": checks, "stopReason": stop_reason,
        "coverage": {"clarification": bool(clarification and clarification_resumed
                                                  and clarification["managedEditsBeforeResponse"] == 0),
                     "recovery": recovery, "restart": bool(restart["exercised"] and restart["receiptsPreserved"] and accepted)},
        "restart": restart, "ownerActions": actions, "projectRuns": runs,
        "deliveredFiles": delivered, "sourceIntegration": integration,
        "limits": limits, "usage": objective["usage"],
        "limitations": "Task success here means accepted local delivery; independent private-oracle grading is still required. "
                       "First-try correctness does not prove recovery. Synthetic provider output is runtime coverage only.",
    }


def _submitted(body: dict) -> dict:
    prompt = next(item["content"] for item in body["messages"]
                  if item["role"] == "user" and "Submitted context:\n" in item.get("content", ""))
    return json.loads(prompt.split("Submitted context:\n", 1)[1])


class SyntheticTrialResponder:
    """Explicit test double at the provider boundary, never a real-mode fallback."""

    def __init__(self, scenario: TrialScenario, *, clarification_mode: str = "typed", repair_first_try: bool = False):
        if clarification_mode not in {"typed", "generic"}:
            raise ValueError("Clarification mode must be typed or generic")
        self.scenario = scenario
        self.clarification_mode = clarification_mode
        self.repair_first_try = repair_first_try
        self.edit_candidates = 0
        self.owner_answer_seen = False

    def __call__(self, body: dict):
        data = _submitted(body)
        kind, context = data["request"]["type"], data["context"]
        replies = context.get("requestResponses", [])
        owner_inputs = context.get("ownerInputs", [])
        if replies or owner_inputs:
            self.owner_answer_seen = True
        if kind == "request.decompose":
            output = {"workPackages": [{"title": "Deliver the project outcome", "description": context["objective"]["description"],
                "managerId": context["objective"]["managerId"], "criterionIndexes": list(range(len(context["objective"]["acceptanceCriteria"]))),
                "projectIds": [p["id"] for p in context["objective"].get("projects", [])], "dependsOn": [], "maxTasks": context["maxTasks"]}]}
        elif kind == "request.plan":
            if self.scenario.owner_answer and not self.owner_answer_seen:
                question = "For duplicate IDs, should deduplicate retain the earliest or latest occurrence?"
                output = ({"requests": [{"type": "request.question", "requestedOutcome": question}]}
                          if self.clarification_mode == "typed" else {"intervention": question})
            else:
                output = {"workers": 1, "tasks": [{"title": "Repair the specified function", "type": "work.edit",
                    "team": "general", "agentId": "editor", "description":
                    "Read root0/app.py and root0/test_app.py. Repair the function according to the objective and any owner clarification; preserve tests.",
                    "dependsOn": []}]}
        elif kind == "work.edit":
            results = [item for item in body["messages"] if item["role"] == "tool"]
            if len(results) < 2:
                path = ("root0/app.py", "root0/test_app.py")[len(results)]
                return kind, {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "read-" + str(len(results)), "type": "function", "function": {
                        "name": "read_file", "arguments": json.dumps({"path": path})}}]}, "tool_calls"
            source = json.loads(results[0]["content"])
            self.edit_candidates += 1
            if self.scenario.scenario_id == "signed_bucket":
                expression = "n // d" if self.repair_first_try or self.edit_candidates > 1 else "abs(n) // d"
                replacement = "def bucket(n, d):\n    return " + expression + "\n"
            else:
                assign = "        retained[row['id']] = row\n"
                if self.scenario.retention == "earliest":
                    assign = "        if row['id'] not in retained:\n            retained[row['id']] = row\n"
                replacement = "def deduplicate(rows):\n    retained = {}\n    for row in rows:\n" + assign + "    return list(retained.values())\n"
            output = {"summary": "Propose candidate implementation; real backend tests still required.",
                      "edits": [{"path": "root0/app.py", "baseRevision": source["workspaceRevision"],
                                 "baseSha256": source["sourceSha256"], "oldText": source["content"], "newText": replacement}],
                      "validations": [{"kind": "python_syntax", "path": "root0/app.py"}]}
        else:
            output = self._review_or_integrate(kind, context)
        return kind, {"role": "assistant", "content": json.dumps(output)}, "stop"

    @staticmethod
    def _review_or_integrate(kind: str, context: dict) -> dict:
        evidence = context.get("evidence", [])
        ids = [item["id"] for item in evidence]
        if kind in {"request.review", "request.test_review"}:
            output = {"approved": True, "summary": "Synthetic reviewer; actual backend checks remain mandatory.", "evidenceIds": ids}
            proposal = evidence[0].get("editProposal")
            if proposal:
                output.update(proposalId=proposal["id"], proposalSha256=proposal["proposalSha256"])
            if kind == "request.test_review":
                body = evidence[0]["content"]
                if not isinstance(body, str):
                    body = context["evidenceBodies"][body["bodySha256"]]
                exact = json.loads(body)
                output["approved"] = exact["execution"]["status"] == "passed" and exact["execution"]["testCount"] > 0
            return output
        if kind == "request.integrate":
            return {"summary": "Locally integrated reviewed candidate", "deliverable":
                    "The preserved public tests ran on the reviewed implementation and passed. "
                    "A new local Git branch retains the result. Original source and index remain unchanged; no remote publication. "
                    "These public tests alone do not establish general correctness."}
        if kind == "request.accept":
            return {"approved": True, "summary": "Synthetic acceptance of retained runtime evidence.", "evidenceIds": ids,
                    "criteriaResults": [{"criterion": item, "satisfied": True, "evidenceIds": ids,
                        "reason": "Synthetic runtime trial; private oracle remains independently required."}
                        for item in context["objective"]["acceptanceCriteria"]], "conflicts": []}
        raise ValueError("Unexpected synthetic trial stage: " + kind)


@contextmanager
def synthetic_provider(scenario: TrialScenario, *, clarification_mode: str = "typed", repair_first_try: bool = False):
    responder = SyntheticTrialResponder(scenario, clarification_mode=clarification_mode, repair_first_try=repair_first_try)
    diagnostics = {"calls": [], "errors": [], "transcript": []}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                kind, message, finish = responder(body)
                diagnostics["calls"].append(kind)
                diagnostics["transcript"].append({"request": body, "response": message})
            except Exception as error:
                diagnostics["errors"].append(type(error).__name__)
                message, finish = {"role": "assistant", "content": '{"intervention":"Synthetic trial protocol mismatch"}'}, "stop"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"id": "synthetic-trial", "object": "chat.completion", "created": 1,
                "model": "organization-semantic-fixture", "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                "usage": {"prompt_tokens": 30, "completion_tokens": 20, "total_tokens": 50}}).encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    prior = {key: os.environ.get(key) for key in ("NO_PROXY", "no_proxy")}
    for key, value in prior.items():
        os.environ[key] = ",".join(filter(None, (value, "127.0.0.1", "::1", "localhost")))
    try:
        yield {"model": "organization-semantic-fixture", "base_url": f"http://127.0.0.1:{server.server_port}/v1",
               "secret": "nonbillable-loopback-fixture"}, diagnostics
    finally:
        try:
            server.shutdown()
            server.server_close()
            thread.join(3)
        finally:
            for key, value in prior.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
