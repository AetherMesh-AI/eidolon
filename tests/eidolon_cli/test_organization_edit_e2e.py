"""Real localhost model, scoped source reader, durable review and workspace commit."""
import hashlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_service import OrganizationService
from eidolon_cli.organization_store import OrganizationStore


@pytest.mark.linux_only
@pytest.mark.parametrize("patch_granted", [True, False])
def test_real_edit_proposal_review_and_separate_workspace_apply_gate(tmp_path, monkeypatch, patch_granted):
    from eidolon_cli import config
    from tools import file_tools

    original = "\ufeffTitle before\r\nKeep this line exactly.\r\nTrailing spaces  "
    replacement = "\ufeffTitle after\r\nKeep this line exactly.\r\nTrailing spaces  "
    source = tmp_path / "source"
    source.mkdir()
    target = source / "notes.txt"
    target.write_bytes(original.encode())
    home = tmp_path / "profile"
    home.mkdir()
    received, reviewed, errors = [], [], []

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
                if kind == "request.plan":
                    assert not body.get("tools")
                    assert any("work.edit" in row["capabilities"] and "read_file" in row["tools"] for row in context["staffing"])
                    output = {"workers": 1, "tasks": [{"title": "Revise exact first line", "type": "work.edit", "team": "general",
                              "description": "Read root0/notes.txt and replace Title before with Title after; preserve all other bytes.",
                              "dependsOn": []}]}
                elif kind == "work.edit":
                    assert [tool["function"]["name"] for tool in body["tools"]] == ["read_file"]
                    assert context["toolPolicy"] == {"tools": ["read_file"], "readRoots": ["root0"]}
                    results = [m for m in body["messages"] if m["role"] == "tool"]
                    if not results:
                        self.respond({"role": "assistant", "content": None, "tool_calls": [{"id": "read-managed-base", "type": "function",
                                     "function": {"name": "read_file", "arguments": '{"path":"root0/notes.txt","limit":1}'}}]}, "tool_calls")
                        return
                    result = json.loads(results[0]["content"])
                    assert result["content"] == "\ufeffTitle before\r\n" and result["truncated"] is True
                    assert result["contentFormat"] == "raw" and result["workspaceRevision"] == 0
                    assert result["workspaceId"] == objective["id"]
                    assert result["sourceSha256"] == hashlib.sha256(original.encode()).hexdigest()
                    with store._connect() as conn:
                        receipt = conn.execute("SELECT * FROM tool_receipts").fetchone()
                        base = conn.execute("SELECT * FROM workspace_revisions").fetchone()
                    assert receipt["status"] == "completed" and receipt["result"] == results[0]["content"]
                    assert base["content"].encode() == original.encode() and base["sha256"] == result["sourceSha256"]
                    output = {"summary": "Proposed the exact requested title replacement", "edit": {
                        "path": "root0/notes.txt", "baseRevision": result["workspaceRevision"],
                        "baseSha256": result["sourceSha256"], "oldText": "Title before", "newText": "Title after"}}
                else:
                    assert kind == "request.review" and not body.get("tools")
                    artifact = context["evidence"][0]
                    proposal = artifact["editProposal"]
                    for field in ("baseContent", "newContent", "diff"):
                        reference = proposal[field]
                        exact = context["evidenceBodies"][reference["bodySha256"]]
                        assert hashlib.sha256(exact.encode()).hexdigest() == reference["bodySha256"]
                        assert len(exact.encode()) == reference["utf8Bytes"]
                        proposal[field] = exact
                    assert proposal["baseContent"].encode() == original.encode()
                    assert proposal["newContent"].encode() == replacement.encode()
                    assert proposal["baseSha256"] == hashlib.sha256(original.encode()).hexdigest()
                    assert proposal["newSha256"] == hashlib.sha256(replacement.encode()).hexdigest()
                    assert "-\ufeffTitle before\r\n" in proposal["diff"] and "+\ufeffTitle after\r\n" in proposal["diff"]
                    assert proposal["status"] == "proposed" and proposal["baseRevision"] == 0
                    receipts = context["toolReceipts"]
                    assert artifact["toolReceiptIds"] == [row["id"] for row in receipts]
                    assert len(receipts) == 1 and receipts[0]["status"] == "completed"
                    assert receipts[0]["resultSha256"] == hashlib.sha256(receipts[0]["result"].encode()).hexdigest()
                    assert json.loads(receipts[0]["result"])["sourceSha256"] == proposal["baseSha256"]
                    reviewed.append(artifact)
                    output = {"approved": True, "summary": "Exact base and replacement preserve every byte outside the requested title.",
                              "evidenceIds": [artifact["id"]], "proposalId": proposal["id"], "proposalSha256": proposal["proposalSha256"]}
                self.respond({"role": "assistant", "content": json.dumps(output)}, "stop")
            except Exception as error:
                errors.append(repr(error))
                self.respond({"role": "assistant", "content": '{"intervention":"Local fixture assertion failed"}'}, "stop")

        def respond(self, message, finish):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"id": "local-edit-flow", "object": "chat.completion", "created": 1,
                "model": "organization-test", "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                "usage": {"prompt_tokens": 30, "completion_tokens": 20, "total_tokens": 50}}).encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    grants = ["read_file", "patch"] if patch_granted else ["read_file"]
    cfg = {"model": {"provider": "custom", "default": "organization-test",
                     "base_url": f"http://127.0.0.1:{server.server_port}/v1", "api_mode": "chat_completions",
                     "streaming": False, "context_length": 128000},
           "agent": {"environment_probe": False}, "compression": {"enabled": False},
           "organization": {"capabilities": ["work.edit"], "tool_grants": grants,
                            "read_roots": [str(source)], "max_workers": 1, "max_inflight": 1,
                            "roster": [{"id": "editor", "name": "Configured editor", "team": "general",
                                        "capabilities": ["work.edit"], "tool_grants": grants}]}}
    (home / "config.yaml").write_text(json.dumps(cfg), encoding="utf-8")
    (home / ".env").write_text("OPENAI_API_KEY=local-fixture-only\n", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    config._LOAD_CONFIG_CACHE.clear()
    monkeypatch.setattr(file_tools, "_get_file_ops", lambda *_: pytest.fail("No shell/backend creation allowed"))
    settings = OrganizationSettings.from_config(cfg)
    store = OrganizationStore(home / "organization" / "state.db", settings)
    objective = store.create_objective("Change the title", "Change Title before to Title after in root0/notes.txt.", idempotency_key="edit-once")
    service = OrganizationService(store, home=home, settings=settings, poll_seconds=0.01)
    try:
        service.start()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            snapshot = store.snapshot()
            if snapshot["objectives"][0]["status"] in {"completed", "needs_input"}:
                break
            time.sleep(0.01)
        assert not errors
        assert snapshot["objectives"][0]["status"] == "needs_input", snapshot["requests"]
        assert len(received) == 4 and len(reviewed) == 1, snapshot["requests"]
        assert str(source) not in json.dumps(received)
        assert "resolveWorkspaceSource" not in json.dumps(received)
        proof = store.evidence(reviewed[0]["id"])
        proposal = proof["editProposal"]
        assert proposal["reviewStatus"] == "approved"
        assert proof["toolReceipts"][0]["status"] == "completed"
        assert proposal["baseContent"].encode() == original.encode()
        assert proposal["newContent"].encode() == replacement.encode()
        with store._connect() as conn:
            revisions = conn.execute("SELECT * FROM workspace_revisions ORDER BY revision").fetchall()
            application = conn.execute("SELECT * FROM edit_applications").fetchone()
            review = conn.execute("SELECT * FROM edit_reviews").fetchone()
            head = conn.execute("SELECT * FROM workspace_heads").fetchone()
        assert review["approved"] == 1 and review["proposal_sha256"] == proposal["proposalSha256"]
        assert revisions[0]["content"].encode() == original.encode() and revisions[0]["revision"] == 0
        gate = next(row for row in snapshot["requests"] if row["type"] == ("request.merge" if patch_granted else "request.apply"))
        assert gate["status"] == "pending_intervention"
        if patch_granted:
            assert proposal["status"] == "applied" and proposal["appliedRevision"] == 1
            assert len(revisions) == 2 and revisions[1]["content"].encode() == replacement.encode()
            assert head["revision"] == application["applied_revision"] == 1
            assert application["proposal_sha256"] == review["proposal_sha256"]
            assert application["review_request_id"] == review["request_id"]
            assert application["applied_sha256"] == revisions[1]["sha256"] == proposal["newSha256"]
            assert "source files are unchanged" in gate["reason"].lower()
        else:
            assert proposal["status"] == "approved" and len(revisions) == 1
            assert head["revision"] == 0 and application is None
            assert "patch" in gate["reason"]
            assert not any(row["type"] == "request.merge" for row in snapshot["requests"])
        assert target.read_bytes() == original.encode()
        replay = store.create_objective("Change the title", "Change Title before to Title after in root0/notes.txt.", idempotency_key="edit-once")
        assert replay["id"] == objective["id"] and len(received) == 4
    finally:
        assert service.stop()
        server.shutdown()
        server.server_close()
        thread.join(3)
        config._LOAD_CONFIG_CACHE.clear()
