"""Native real-provider project loop: copied tests are never model-produced receipts."""
from tests.organization_package_helpers import decomposition_result
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_service import OrganizationService
from eidolon_cli.organization_store import OrganizationStore


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
CRITERION = "Addition handles positive, negative and zero operands; reviewed tests pass on the exact integrated source branch."


def _git(source, *args):
    return subprocess.check_output(["git", "--no-optional-locks", "-C", str(source), *args], text=True).strip()


def _exact(value, context):
    if isinstance(value, str):
        return value
    text = context["evidenceBodies"][value["bodySha256"]]
    assert hashlib.sha256(text.encode()).hexdigest() == value["bodySha256"]
    assert len(text.encode()) == value["utf8Bytes"]
    return text


def _native_project_loop(tmp_path, monkeypatch, *, create=False):
    from eidolon_cli import config
    from tools import file_tools

    source, home = tmp_path / "source", tmp_path / "profile"
    source.mkdir()
    home.mkdir()
    if create:
        (source / "README.md").write_text("Implement integer addition with regression tests.\n")
    else:
        (source / "app.py").write_bytes(BEFORE.encode())
        (source / "test_app.py").write_bytes(TESTS.encode())
    _git(source, "init", "--initial-branch=main")
    _git(source, "-c", "user.name=Local fixture", "-c", "user.email=fixture@localhost", "add", *( ["README.md"] if create else ["app.py", "test_app.py"]))
    _git(source, "-c", "user.name=Local fixture", "-c", "user.email=fixture@localhost", "commit", "-m", "Exact starting project")
    source_head = _git(source, "rev-parse", "HEAD")
    source_index = (source / ".git" / "index").read_bytes()
    received, stages, errors = [], [], []
    test_evidence = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"data":[{"id":"organization-test"}]}')

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append(body)
            try:
                prompt = next(m["content"] for m in body["messages"]
                              if m["role"] == "user" and "Submitted context:\n" in m.get("content", ""))
                data = json.loads(prompt.split("Submitted context:\n", 1)[1])
                kind, context = data["request"]["type"], data["context"]
                stages.append({"kind": kind, "agent": context["agent"]["id"]})
                evidence = context.get("evidence", [])
                bodies = [_exact(row["content"], context) for row in evidence]
                for row, content in zip(evidence, bodies):
                    assert hashlib.sha256(content.encode()).hexdigest() == row["sha256"]
                evidence_ids = [row["id"] for row in evidence]
                if kind == 'request.decompose':
                    output = decomposition_result(context)
                elif kind == "request.plan":
                    assert not body.get("tools")
                    output = {"workers": 1, "tasks": [
                        {"title": "Inspect addition and its tests", "type": "work.inspect", "team": "general",
                         "agentId": "editor", "description": ("Read root0/README.md and identify the implementation requirements." if create else "Read root0/app.py and root0/test_app.py and identify the subtraction bug."), "dependsOn": []},
                        {"title": "Fix addition without weakening tests", "type": "work.edit", "team": "general",
                         "agentId": "editor", "description": ("Create root0/app.py and root0/test_app.py with addition and regression assertions." if create else "Change left - right to left + right in root0/app.py; retain all tests."), "dependsOn": [0]}]}
                elif kind in {"work.inspect", "work.edit"}:
                    assert [tool["function"]["name"] for tool in body["tools"]] == ["read_file"]
                    results = [m for m in body["messages"] if m["role"] == "tool"]
                    paths = (["root0/README.md"] if create else ["root0/app.py", "root0/test_app.py"]) if kind == "work.inspect" else (["root0/app.py", "root0/test_app.py"] if create else ["root0/app.py"])
                    if len(results) < len(paths):
                        self.respond({"role": "assistant", "content": None, "tool_calls": [{
                            "id": f"read-{kind}-{len(results)}", "type": "function",
                            "function": {"name": "read_file", "arguments": json.dumps({"path": paths[len(results)]})}}]}, "tool_calls")
                        return
                    result = json.loads(results[0]["content"])
                    if kind == "work.inspect" and create:
                        assert "Implement integer addition" in result["content"]
                        output = {"summary": "Addition and regression coverage are required", "deliverable":
                                  "root0/README.md requests integer addition. Create implementation and independent positive, negative and zero cases."}
                    elif kind == "work.edit" and create:
                        edits = []
                        for path, message, content in zip(paths, results, (AFTER, TESTS)):
                            observed = json.loads(message["content"])
                            assert observed["sourceExists"] is False and observed["content"] == ""
                            edits.append({"operation": "create", "path": path,
                                          "baseRevision": observed["workspaceRevision"],
                                          "baseSha256": observed["sourceSha256"], "newText": content})
                        output = {"summary": "Create addition and three regression cases", "edits": edits,
                                  "validations": [{"kind": "python_syntax", "path": path} for path in paths]}
                    elif kind == "work.inspect":
                        assert result["content"] == "1|def add(left, right):\n2|    return left - right"
                        assert "self.assertEqual(add(2, 3), 5)" in json.loads(results[1]["content"])["content"]
                        output = {"summary": "Found subtraction where addition is required", "deliverable":
                                  "root0/app.py lines 1–2 subtracts operands. root0/test_app.py lines 1–10 tests positive, negative and zero addition. Change only the implementation."}
                    else:
                        assert result["content"] == BEFORE and result["sourceSha256"] == hashlib.sha256(BEFORE.encode()).hexdigest()
                        output = {"summary": "Fix addition, retaining the independent regression cases", "edits": [{
                            "path": "root0/app.py", "baseRevision": result["workspaceRevision"], "baseSha256": result["sourceSha256"],
                            "oldText": "left - right", "newText": "left + right"}],
                            "validations": [{"kind": "python_syntax", "path": "root0/app.py"}]}
                elif kind == "request.review":
                    assert not body.get("tools")
                    output = {"approved": True, "summary": "The exact read receipts support the findings and the requested narrow correction.", "evidenceIds": evidence_ids}
                    proposal = evidence[0].get("editProposal")
                    if proposal:
                        files = proposal.get("files", [proposal])
                        if create:
                            assert len(files) == 2 and all(item["operation"] == "create" for item in files)
                            assert all(_exact(item["baseContent"], context) == "" for item in files)
                            assert {_exact(item["newContent"], context) for item in files} == {AFTER, TESTS}
                        else:
                            assert len(files) == 1
                            assert _exact(files[0]["baseContent"], context) == BEFORE
                            assert _exact(files[0]["newContent"], context) == AFTER
                        output.update(proposalId=proposal["id"], proposalSha256=proposal["proposalSha256"])
                elif kind == "request.test_review":
                    assert not body.get("tools") and len(evidence) == 1
                    exact = json.loads(bodies[0])
                    execution = exact["execution"]
                    assert execution["status"] == "passed" and execution["exitCode"] == 0
                    assert execution["testCount"] == 3 and execution["isolation"]["established"] is True
                    assert {row["path"]: row["content"] for row in exact["snapshot"]} == {"root0/app.py": AFTER, "root0/test_app.py": TESTS}
                    assert "test_positive" in execution["stderr"] and "test_negative" in execution["stderr"]
                    test_evidence.append(evidence[0]["id"])
                    output = {"approved": True, "summary": "Three genuine assertions exercise positive, negative and zero operands on the exact copied implementation. Isolation excludes host/network access; this is bounded unittest coverage.", "evidenceIds": evidence_ids}
                elif kind == "request.integrate":
                    assert not body.get("tools")
                    assert any(row.get("kind") == "project_execution" for row in evidence)
                    assert any(row.get("kind") == "source_integration" for row in evidence)
                    output = {"summary": "Reviewed addition fix and tested source branch", "deliverable":
                              "Addition now handles positive, negative and zero operands. Three real isolated unittest cases passed on the exact reviewed bytes, independently reviewed before new local Git-branch integration. The original worktree and index are unchanged. No remote push or deployment occurred."}
                else:
                    assert kind == "request.accept" and not body.get("tools")
                    output = {"approved": True, "summary": "Exact test evidence and verified source-branch receipt satisfy every criterion.",
                              "evidenceIds": evidence_ids, "criteriaResults": [{"criterion": item, "satisfied": True,
                              "evidenceIds": evidence_ids, "reason": "Exact reviewed source, regression assertions, actual isolated run and source branch agree."}
                              for item in context["objective"]["acceptanceCriteria"]], "conflicts": []}
                self.respond({"role": "assistant", "content": json.dumps(output)}, "stop")
            except Exception as error:
                errors.append(repr(error))
                self.respond({"role": "assistant", "content": '{"intervention":"Local fixture assertion failed"}'}, "stop")

        def respond(self, message, finish):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"id": "local-project-flow", "object": "chat.completion", "created": 1,
                "model": "organization-test", "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                "usage": {"prompt_tokens": 30, "completion_tokens": 20, "total_tokens": 50}}).encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    grants = ["read_file", "patch", "run_tests", "integrate_source"]
    cfg = {"model": {"provider": "custom", "default": "organization-test",
                     "base_url": f"http://127.0.0.1:{server.server_port}/v1", "api_mode": "chat_completions",
                     "streaming": False, "context_length": 128000},
           "agent": {"environment_probe": False}, "compression": {"enabled": False},
           "organization": {"capabilities": ["work.inspect", "work.edit"], "tool_grants": grants,
                            "read_roots": [str(source)], "max_workers": 1, "max_inflight": 1,
                            "max_context_tokens": 65536, "max_output_tokens": 2048,
                            "project_grants": [{"id": "addition", "files": ["root0/app.py", "root0/test_app.py"],
                                                "execution": {"recipe": "python_unittest", "timeout_seconds": 10}}],
                            "roster": [{"id": "editor", "name": "Project editor", "team": "general",
                                        "capabilities": ["work.inspect", "work.edit"], "tool_grants": grants}]}}
    (home / "config.yaml").write_text(json.dumps(cfg), encoding="utf-8")
    (home / ".env").write_text("OPENAI_API_KEY=local-fixture-only\n", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    config._LOAD_CONFIG_CACHE.clear()
    monkeypatch.setattr(file_tools, "_get_file_ops", lambda *_: pytest.fail("No model shell or general file backend is authorized"))
    settings = OrganizationSettings.from_config(cfg)
    store = OrganizationStore(home / "organization" / "state.db", settings)
    objective = store.create_objective("Fix and verify addition", CRITERION, idempotency_key="project-once",
        acceptance_criteria=[CRITERION], delivery_mode="source_project",
        required_checks=["project_tests", "managed_validation", "source_integration"])
    service = OrganizationService(store, home=home, settings=settings, poll_seconds=0.01)
    try:
        service.start()
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            snapshot = store.snapshot()
            if snapshot["objectives"][0]["status"] in {"completed", "needs_input"}:
                break
            time.sleep(0.02)
        assert not errors, errors
        item = snapshot["objectives"][0]
        run = item["projectExecution"]
        assert str(source) not in json.dumps(received)
        assert "resolveWorkspaceSource" not in json.dumps(received)
        assert _git(source, "rev-parse", "HEAD") == source_head
        assert (source / ".git" / "index").read_bytes() == source_index
        if create:
            assert not (source / "app.py").exists() and not (source / "test_app.py").exists()
        else:
            assert (source / "app.py").read_bytes() == BEFORE.encode()
            assert (source / "test_app.py").read_bytes() == TESTS.encode()
        assert _git(source, "status", "--porcelain") == ""
        if sys.platform == "linux" and os.environ.get("EIDOLON_REQUIRE_PROJECT_SANDBOX") == "1":
            assert run and run["status"] == "passed", snapshot["requests"]
        if run and run["status"] == "passed":
            assert sys.platform == "linux"
            assert item["status"] == "completed", snapshot["requests"]
            assert run["receipt"]["isolation"]["established"] is True
            assert run["receipt"]["testCount"] == 3
            assert run["review"]["approved"] is True and run["review"]["reviewerId"] != "editor"
            receipt = run["sourceIntegration"]
            assert receipt["status"] == "integrated" and receipt["sourceBaseCommit"] == source_head
            assert receipt["workingTreeWritesPerformed"] is False and receipt["indexWritesPerformed"] is False
            assert receipt["remotePushPerformed"] is False
            if create:
                assert all(item["operation"] == "create" and item["sourceBlob"] is None for item in receipt["files"])
            assert _git(source, "rev-parse", receipt["ref"]) == receipt["commit"]
            assert _git(source, "show", receipt["commit"] + ":app.py") == AFTER.strip()
            assert _git(source, "show", receipt["commit"] + ":test_app.py") == TESTS.strip()
            proof = store.evidence(test_evidence[0])
            assert json.loads(proof["content"])["execution"]["snapshotSha256"]
            authored = {row["agent"] for row in stages if row["kind"].startswith("work.")}
            assert run["review"]["reviewerId"] not in authored
            assert next(row["agent"] for row in stages if row["kind"] == "request.accept") != next(row["agent"] for row in stages if row["kind"] == "request.integrate")
            request_kinds = [row["type"] for row in sorted(snapshot["requests"], key=lambda row: row["createdAt"])]
            for earlier, later in zip(["request.project_test", "request.test_review", "request.source_integrate", "request.integrate"],
                                      ["request.test_review", "request.source_integrate", "request.integrate", "request.accept"]):
                assert request_kinds.index(earlier) < request_kinds.index(later)
        else:
            assert item["status"] == "needs_input", snapshot["requests"]
            assert not test_evidence
            assert not any(row["type"] in {"request.source_integrate", "request.integrate", "request.accept"} for row in snapshot["requests"])
            assert _git(source, "for-each-ref", "--format=%(refname)", "refs/heads/").splitlines() == ["refs/heads/main"]
            if sys.platform != "win32":
                assert run and run["status"] == "unsupported", snapshot["requests"]
                assert run["receipt"]["isolation"]["established"] is False
                assert run["sourceIntegration"] is None
            else:
                assert run is None
                assert any("POSIX no-follow directory-descriptor support" in str(row["reason"])
                           for row in snapshot["requests"] if row["status"] == "pending_intervention")
        assert service.stop()
        calls = len(received)
        restarted = OrganizationService(OrganizationStore(home / "organization" / "state.db", settings), home=home, settings=settings, poll_seconds=0.01)
        try:
            restarted.start()
            # A stopped terminal/intervention ledger has no runnable claims on restart.
            assert restarted.store.claim_next() is None
            assert restarted.store.snapshot()["objectives"][0]["projectExecution"] == run
            assert len(received) == calls
        finally:
            assert restarted.stop()
        replay = store.create_objective("Fix and verify addition", CRITERION, idempotency_key="project-once",
            acceptance_criteria=[CRITERION], delivery_mode="source_project",
            required_checks=["project_tests", "managed_validation", "source_integration"])
        assert replay["id"] == objective["id"] and len(received) == calls
    finally:
        service.stop()
        server.shutdown()
        server.server_close()
        thread.join(3)
        config._LOAD_CONFIG_CACHE.clear()


@pytest.mark.linux_only
@pytest.mark.parametrize("create", [False, True])
def test_native_linux_project_goal_inspect_edit_test_review_integrate_accept(tmp_path, monkeypatch, create):
    _native_project_loop(tmp_path, monkeypatch, create=create)


@pytest.mark.macos_only
def test_native_macos_project_loop_retains_explicit_unsupported_receipt(tmp_path, monkeypatch):
    _native_project_loop(tmp_path, monkeypatch)


@pytest.mark.windows_only
def test_native_windows_project_loop_blocks_without_hidden_execution(tmp_path, monkeypatch):
    _native_project_loop(tmp_path, monkeypatch)


def _unsupported_runner_never_starts_a_child(monkeypatch):
    from eidolon_cli.organization_project_runner import run_project_tests
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: pytest.fail("Unsupported host launched a process"))
    receipt = run_project_tests([{"path": "root0/test_app.py", "content": TESTS,
        "sha256": hashlib.sha256(TESTS.encode()).hexdigest(), "revision": 0}], {"recipe": "python_unittest"})
    assert receipt["status"] == "unsupported"
    assert receipt["isolation"]["established"] is False
    assert receipt["exitCode"] is None and receipt["testCount"] == 0
    assert receipt["sourceWritesPerformed"] is False and receipt["command"] == []
    assert "no host fallback" in receipt["reason"]


@pytest.mark.macos_only
def test_native_macos_recipe_never_falls_back_to_host_python(monkeypatch):
    _unsupported_runner_never_starts_a_child(monkeypatch)


@pytest.mark.windows_only
def test_native_windows_recipe_never_falls_back_to_host_python(monkeypatch):
    _unsupported_runner_never_starts_a_child(monkeypatch)
