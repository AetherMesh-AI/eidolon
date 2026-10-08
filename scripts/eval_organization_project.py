#!/usr/bin/env python3
"""Evaluate the real organization pipeline on one disposable addition project.

Default: deterministic, nonbillable loopback provider. A real provider requires
both --live-model and --acknowledge-billing, an explicit credential-free provider
and price configuration, and --max-cost-usd. Native isolation is preflighted
before any credential access or provider startup. This is a narrow fixture
evaluation, not a production runner.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, redirect_stdout
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.organization_trial_policy import (
    COST_LIMITATION, add_live_arguments, budget_configuration, load_live_secret,
    native_preflight, selected_live_route,
)

BEFORE = "def add(left, right):\n    return left - right\n"
AFTER = "def add(left, right):\n    return left + right\n"
TESTS = """import unittest
from app import add

class AdditionTests(unittest.TestCase):
    def test_positive(self):
        self.assertEqual(add(2, 3), 5)
    def test_negative(self):
        self.assertEqual(add(-2, -3), -5)
    def test_zero(self):
        self.assertEqual(add(9, 0), 9)
"""
CRITERION = (
    "Inspect root0/app.py and root0/test_app.py, then change only left - right to left + right "
    "in app.py. Preserve every test byte. Addition must handle positive, negative and zero operands. "
    "Independently review genuine tests on the exact output, integrate into a new local Git branch, "
    "and report the reviewed result. Do not alter the original working tree or push remotely."
)
LIMITS = {"max_workers": 1, "max_inflight": 1, "max_tasks": 2, "max_attempts": 1,
          "max_revisions": 0, "max_replans": 0, "max_stages": 24,
          "max_context_tokens": 65536, "max_output_tokens": 2048,
          "max_model_calls": 16, "max_total_tokens": 1048576,
          "timeout_seconds": 60, "objective_timeout_seconds": 180,
          "max_tool_calls": 3, "max_project_runs": 1}
KEY_NAME = "EIDOLON_PROJECT_EVALUATION_API_KEY"


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    add_live_arguments(result)
    result.add_argument("--preflight", action="store_true", help=(
        "Check the native sandbox only, without reading credentials or starting any model provider."))
    result.add_argument("--report", type=Path, help="Write the structured JSON report here (also printed to stdout).")
    return result


def _exact(value, context):
    return value if isinstance(value, str) else context["evidenceBodies"][value["bodySha256"]]


def fixture_response(body):
    prompt = next(m["content"] for m in body["messages"]
                  if m["role"] == "user" and "Submitted context:\n" in m.get("content", ""))
    data = json.loads(prompt.split("Submitted context:\n", 1)[1])
    kind, context = data["request"]["type"], data["context"]
    evidence = context.get("evidence", [])
    ids = [item["id"] for item in evidence]
    if kind == "request.decompose":
        output = {"workPackages": [{"title": "Deliver the project outcome", "description": context["objective"]["description"],
            "managerId": context["objective"]["managerId"], "criterionIndexes": list(range(len(context["objective"]["acceptanceCriteria"]))),
            "projectIds": [p["id"] for p in context["objective"].get("projects", [])], "dependsOn": [], "maxTasks": context["maxTasks"]}]}
    elif kind == "request.plan":
        output = {"workers": 1, "tasks": [
            {"title": "Inspect addition", "type": "work.inspect", "team": "general", "agentId": "editor",
             "description": "Read root0/app.py and root0/test_app.py and identify the subtraction bug.", "dependsOn": []},
            {"title": "Fix addition", "type": "work.edit", "team": "general", "agentId": "editor",
             "description": "Replace left - right with left + right in root0/app.py; preserve tests.", "dependsOn": [0]}]}
    elif kind in {"work.inspect", "work.edit"}:
        results = [m for m in body["messages"] if m["role"] == "tool"]
        paths = ["root0/app.py", "root0/test_app.py"] if kind == "work.inspect" else ["root0/app.py"]
        if len(results) < len(paths):
            return kind, {"role": "assistant", "content": None, "tool_calls": [{
                "id": f"read-{kind}-{len(results)}", "type": "function", "function": {
                    "name": "read_file", "arguments": json.dumps({"path": paths[len(results)]})}}]}, "tool_calls"
        source = json.loads(results[0]["content"])
        output = ({"summary": "Found subtraction", "deliverable":
                   "root0/app.py lines 1–2 subtracts operands. root0/test_app.py lines 1–10 tests positive, negative and zero addition."}
                  if kind == "work.inspect" else {
                      "summary": "Correct addition without changing tests", "edits": [{"path": "root0/app.py",
                      "baseRevision": source["workspaceRevision"], "baseSha256": source["sourceSha256"],
                      "oldText": "left - right", "newText": "left + right"}],
                      "validations": [{"kind": "python_syntax", "path": "root0/app.py"}]})
    elif kind in {"request.review", "request.test_review"}:
        output = {"approved": True, "summary": "Exact fixture evidence supports the requested narrow change.", "evidenceIds": ids}
        proposal = evidence[0].get("editProposal")
        if proposal:
            output.update(proposalId=proposal["id"], proposalSha256=proposal["proposalSha256"])
        if kind == "request.test_review":
            exact = json.loads(_exact(evidence[0]["content"], context))
            valid = (exact["execution"]["status"] == "passed" and exact["execution"]["testCount"] == 3
                     and {row["path"]: row["content"] for row in exact["snapshot"]}
                     == {"root0/app.py": AFTER, "root0/test_app.py": TESTS})
            output.update(approved=valid, summary="Three genuine positive, negative and zero assertions on the exact fixture; bounded unittest coverage only.")
    elif kind == "request.integrate":
        output = {"summary": "Reviewed and tested addition fix", "deliverable":
                  "Addition is corrected. Three isolated unittest assertions passed on the exact reviewed bytes, "
                  "then a new local Git branch retained the result. The original worktree and index are unchanged. No remote push or deployment."}
    elif kind == "request.accept":
        output = {"approved": True, "summary": "Exact regression and branch evidence satisfy the fixture criterion.",
                  "evidenceIds": ids, "criteriaResults": [{"criterion": item, "satisfied": True,
                  "evidenceIds": ids, "reason": "Reviewed exact source, preserved tests and integration receipts agree."}
                  for item in context["objective"]["acceptanceCriteria"]], "conflicts": []}
    else:
        raise ValueError("Unexpected fixture stage")
    return kind, {"role": "assistant", "content": json.dumps(output)}, "stop"


@contextmanager
def local_provider():
    calls, errors = [], []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                kind, message, finish = fixture_response(body)
                calls.append(kind)
            except Exception:
                errors.append("The deterministic provider could not handle a pipeline request")
                message, finish = {"role": "assistant", "content": '{"intervention":"Fixture protocol mismatch"}'}, "stop"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"id": "local-evaluation", "object": "chat.completion", "created": 1,
                "model": "organization-evaluation-fixture", "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                "usage": {"prompt_tokens": 30, "completion_tokens": 20, "total_tokens": 50}}).encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # An inherited corporate/cloud proxy must never receive fixture traffic.
    # Both spellings matter: urllib and HTTP clients prefer lowercase values.
    proxy_bypass = {key: os.environ.get(key) for key in ("NO_PROXY", "no_proxy")}
    for key, value in proxy_bypass.items():
        os.environ[key] = ",".join(filter(None, (value, "127.0.0.1", "::1", "localhost")))
    try:
        yield {"model": "organization-evaluation-fixture", "base_url": f"http://127.0.0.1:{server.server_port}/v1",
               "secret": "nonbillable-loopback-fixture"}, calls, errors
    finally:
        try:
            server.shutdown()
            server.server_close()
            thread.join(3)
        finally:
            for key, value in proxy_bypass.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


@contextmanager
def isolated_profile(home, secret):
    from agent.secret_scope import set_secret_scope, reset_secret_scope, set_secret_scope_required, reset_secret_scope_required
    from eidolon_constants import set_eidolon_home_override, reset_eidolon_home_override
    home_token = set_eidolon_home_override(home)
    required_token = set_secret_scope_required(True)
    secret_token = set_secret_scope({KEY_NAME: secret})
    previous = {key: os.environ.get(key) for key in ("HERMES_HOME", "EIDOLON_HOME")}
    os.environ.update(HERMES_HOME=str(home), EIDOLON_HOME=str(home))
    try:
        yield
    finally:
        try:
            close_profile_logs(home)
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            reset_secret_scope(secret_token)
            reset_secret_scope_required(required_token)
            reset_eidolon_home_override(home_token)


def close_profile_logs(home):
    """Release this disposable profile's log/lock handles before Windows cleanup."""
    import eidolon_logging as logs
    with logs._queue_state_lock:
        owned = [handler for handler in logs._queued_file_handlers
                 if getattr(handler, "baseFilename", None)
                 and Path(handler.baseFilename).resolve().is_relative_to(home.resolve())]
        if not owned:
            return
        listener = logs._queue_listener
        if listener is not None:
            listener.stop()
            logs._queue_listener = None
        logs._queued_file_handlers[:] = [handler for handler in logs._queued_file_handlers if handler not in owned]
        try:
            for handler in owned:
                handler.close()
        finally:
            if listener is not None and logs._queued_file_handlers:
                logs._start_queue_listener_locked()


def configuration(source, route):
    grants = ["read_file", "patch", "run_tests", "integrate_source"]
    return {"model": {"provider": "custom:evaluation", "default": route["model"], "context_length": 128000, "streaming": False},
            "providers": {"evaluation": {"base_url": route["base_url"], "key_env": KEY_NAME,
                                         "default_model": route["model"], "api_mode": "chat_completions"}},
            "agent": {"environment_probe": False}, "compression": {"enabled": False},
            "organization": {**LIMITS, **budget_configuration(route),
                "capabilities": ["work.inspect", "work.edit"], "tool_grants": grants,
                "read_roots": [str(source)], "project_grants": [{"id": "addition",
                    "files": ["root0/app.py", "root0/test_app.py"],
                    "execution": {"recipe": "python_unittest", "timeout_seconds": 10}}],
                "roster": [{"id": "editor", "name": "Fixture editor", "team": "general",
                            "capabilities": ["work.inspect", "work.edit"], "tool_grants": grants}]}}


def _git(source, *args, exact=False):
    # Ignore user Git hooks/config and inherited repository overrides. Only the
    # throwaway repository is ever supplied to this helper.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    result = subprocess.check_output(["git", "-C", str(source), "-c", "core.hooksPath=" + os.devnull,
                                      "-c", "commit.gpgsign=false", *args], env=env, stderr=subprocess.PIPE).decode()
    return result if exact else result.strip()


def run_evaluation(root, route, *, live=False):
    source, home = root / "source", root / "profile"
    source.mkdir()
    home.mkdir()
    (source / "app.py").write_bytes(BEFORE.encode())
    (source / "test_app.py").write_bytes(TESTS.encode())
    _git(source, "init", "--initial-branch=main")
    _git(source, "add", "app.py", "test_app.py")
    _git(source, "-c", "user.name=Local evaluation", "-c", "user.email=evaluation@localhost", "commit", "-m", "Disposable fixture")
    original_head, original_index = _git(source, "rev-parse", "HEAD"), (source / ".git" / "index").read_bytes()
    cfg = configuration(source, route)
    (home / "config.yaml").write_text(json.dumps(cfg), encoding="utf-8")
    with isolated_profile(home, route["secret"]):
        from eidolon_cli.organization_config import OrganizationSettings
        from eidolon_cli.organization_service import OrganizationService
        from eidolon_cli.organization_store import OrganizationStore
        settings = OrganizationSettings.from_config(cfg)
        store = OrganizationStore(home / "organization" / "state.db", settings)
        store.create_objective("Fix and verify addition", CRITERION, idempotency_key="evaluation-once",
            acceptance_criteria=[CRITERION], delivery_mode="source_project",
            required_checks=["project_tests", "managed_validation", "source_integration"])
        service = OrganizationService(store, home=home, settings=settings, poll_seconds=0.01)
        stopped = False
        try:
            service.start()
            deadline = time.monotonic() + LIMITS["objective_timeout_seconds"]
            while time.monotonic() < deadline:
                snapshot = store.snapshot()
                if snapshot["objectives"][0]["status"] in {"completed", "needs_input", "cancelled"}:
                    break
                time.sleep(0.05)
        finally:
            stopped = service.stop(timeout=LIMITS["timeout_seconds"] + 10)
        snapshot = store.snapshot()
        item = snapshot["objectives"][0]
        run = item["projectExecution"]
        proof = json.loads(store.evidence(run["id"])["content"]) if run else None
    original_unchanged = (_git(source, "rev-parse", "HEAD") == original_head
        and (source / ".git" / "index").read_bytes() == original_index
        and (source / "app.py").read_bytes() == BEFORE.encode()
        and (source / "test_app.py").read_bytes() == TESTS.encode() and not _git(source, "status", "--porcelain"))
    integration = run["sourceIntegration"] if run else None
    delivered = None
    if integration and integration["status"] == "integrated":
        delivered = {name: _git(source, "show", integration["commit"] + ":" + name, exact=True)
                     for name in ("app.py", "test_app.py")}
    tested_files = {row["path"]: row["content"] for row in proof["snapshot"]} if proof else {}
    authors = {row["agentId"] for row in snapshot["requests"] if row["type"].startswith("work.")}
    integrators = {row["agentId"] for row in snapshot["requests"] if row["type"] == "request.integrate"}
    acceptors = {row["agentId"] for row in snapshot["requests"] if row["type"] == "request.accept"}
    checks = {"originalSourceAndIndexUnchanged": original_unchanged, "serviceStopped": stopped,
              "exactDeliveredSourceAndTests": delivered == {"app.py": AFTER, "test_app.py": TESTS},
              "exactTestedSnapshot": tested_files == {"root0/app.py": AFTER, "root0/test_app.py": TESTS},
              "isolatedTestsPassed": bool(proof and proof["execution"]["status"] == "passed"
                  and proof["execution"]["exitCode"] == 0 and proof["execution"]["testCount"] == 3
                  and proof["execution"]["isolation"]["established"]),
              "independentTestReview": bool(run and run["review"] and run["review"]["approved"] and run["review"]["reviewerId"] not in authors),
              "independentAcceptance": bool(acceptors and integrators and not acceptors & integrators),
              "objectiveAccepted": item["status"] == "completed",
              "verifiedNewSourceBranch": bool(integration and integration["status"] == "integrated"
                  and integration["sourceBaseCommit"] == original_head
                  and _git(source, "rev-parse", integration["ref"]) == integration["commit"]
                  and integration["workingTreeWritesPerformed"] is False and integration["indexWritesPerformed"] is False),
              "noRemotePush": not integration or integration["remotePushPerformed"] is False}
    reasons = [row["reason"] for row in snapshot["requests"] if row["status"] == "pending_intervention"]
    unsupported = bool(run and run["status"] == "unsupported") or (
        sys.platform == "win32" and not run and any(
            "POSIX no-follow directory-descriptor support" in str(reason) for reason in reasons))
    no_extra_branch = _git(source, "for-each-ref", "--format=%(refname)", "refs/heads/") == "refs/heads/main"
    safe_unsupported = unsupported and original_unchanged and stopped and not integration and no_extra_branch
    outcome = "passed" if all(checks.values()) else "unsupported" if safe_unsupported else "failed"
    return {"schemaVersion": 1, "mode": "live_model" if live else "deterministic_local_fixture",
            "billing": "explicitly_acknowledged_provider_calls" if live else "nonbillable_loopback_only",
            "model": route["model"], "outcome": outcome, "objectiveStatus": item["status"],
            "limits": {**LIMITS, **budget_configuration(route)}, "usage": item["usage"],
            "checks": checks, "interventions": reasons,
            "projectExecution": proof, "testReview": run["review"] if run else None,
            "sourceIntegration": integration, "deliveredFiles": delivered,
            "deliveredSha256": {name: hashlib.sha256(content.encode()).hexdigest() for name, content in (delivered or {}).items()},
            "finalDeliverable": item["result"],
            "acceptance": item["acceptance"], "temporaryProjectRemovedAfterReport": True,
            "limitations": "One small addition fixture; no production repository writes, remote push or deployment. "
                            "Fixture mode exercises the real pipeline but does not measure model intelligence. "
                            "Unsupported isolation is not a passing evaluation. " + COST_LIMITATION}


def preflight_report(preflight, *, live=False, only=False, route=None):
    """Keep failed admission visibly distinct from an attempted model evaluation."""
    return {"schemaVersion": 1, "mode": "sandbox_preflight" if only else
            "live_model" if live else "deterministic_local_fixture", "billing": "no_provider_calls",
            "model": route["model"] if route else "organization-evaluation-fixture",
            "outcome": preflight["outcome"], "objectiveStatus": "not_started",
            "limits": {**LIMITS, **budget_configuration(route)} if route else LIMITS,
            "preflight": preflight, "providerCalls": 0, "executionStarted": False,
            "usage": None, "checks": {"originalSourceAndIndexUnchanged": True, "serviceStopped": True,
                "exactDeliveredSourceAndTests": False, "exactTestedSnapshot": False,
                "isolatedTestsPassed": False, "independentTestReview": False, "independentAcceptance": False,
                "objectiveAccepted": False, "verifiedNewSourceBranch": False, "noRemotePush": True},
            "interventions": [] if preflight["outcome"] == "passed" else [preflight["execution"]["reason"]],
            "projectExecution": None, "testReview": None, "sourceIntegration": None, "deliveredFiles": None,
            "deliveredSha256": {}, "finalDeliverable": None, "acceptance": None,
            "fixtureStages": [], "fixtureErrors": [], "temporaryProjectRemovedAfterReport": True,
            "limitations": preflight["limitations"] + " " + COST_LIMITATION}


def main(argv=None):
    cli = parser()
    args = cli.parse_args(argv)
    try:
        route = selected_live_route(args)
    except (ValueError, OSError) as error:
        cli.error(str(error))
    with tempfile.TemporaryDirectory(prefix="eidolon-project-evaluation-") as directory, redirect_stdout(sys.stderr):
        # macOS temp paths may traverse /var -> /private/var. Canonicalize only
        # this freshly owned directory; granted project paths still fail closed.
        root = Path(directory).resolve()
        preflight = native_preflight()
        if args.preflight or preflight["outcome"] != "passed":
            report = preflight_report(preflight, live=args.live_model, only=args.preflight, route=route)
        elif route:
            try:
                route = load_live_secret(route, preflight)
            except ValueError as error:
                cli.error(str(error))
            report = run_evaluation(root, route, live=True)
        else:
            with local_provider() as (fixture, calls, errors):
                report = run_evaluation(root, fixture)
                report.update(fixtureStages=calls, fixtureErrors=errors)
                if errors:
                    report["outcome"] = "failed"
        report["preflight"] = preflight
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return {"passed": 0, "failed": 1, "unsupported": 2}[report["outcome"]]


if __name__ == "__main__":
    raise SystemExit(main())
