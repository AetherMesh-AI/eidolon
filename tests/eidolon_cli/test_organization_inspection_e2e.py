"""Full real organization scheduler/provider/read/receipt/review flow, on localhost."""
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
def test_manager_hire_read_and_evidence_bound_review_complete(tmp_path, monkeypatch):
    from eidolon_cli import config
    from tools import file_tools

    source = tmp_path / "source"
    source.mkdir()
    (source / "facts.txt").write_text("A costs 40 dollars.\nB costs 70 dollars.\n", encoding="utf-8")
    home = tmp_path / "profile"
    home.mkdir()
    received, reviewed, errors = [], [], []
    outcome = {"summary": "Source costs compared", "deliverable": "root0/facts.txt lines 1–2: A costs 40 dollars, B costs 70 dollars. A is 30 dollars cheaper."}

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
                kind, ctx = data["request"]["type"], data["context"]
                if kind == "request.plan":
                    output = {"workers": 2, "tasks": [{"title": "Inspect cost source", "type": "work.inspect", "team": "general",
                              "description": "Read root0/facts.txt and compare both exact costs, citing lines.", "dependsOn": []}]}
                    assert not body.get("tools")
                    assert any("read_file" in s["tools"] for s in ctx["staffing"])
                elif kind == 'request.integrate':
                    assert not body.get('tools')
                    output = {'summary': 'Integrated cost comparison', 'deliverable': outcome['deliverable']}
                elif kind == 'request.accept':
                    assert not body.get('tools')
                    ids = [item['id'] for item in ctx['evidence']]
                    output = {'approved': True, 'summary': 'The complete objective is satisfied by the retained integrated analysis.',
                              'evidenceIds': ids, 'conflicts': [], 'criteriaResults': [
                                  {'criterion': criterion, 'satisfied': True, 'evidenceIds': ids,
                                   'reason': 'The exact retained source supports the full cost comparison.'}
                                  for criterion in ctx['objective']['acceptanceCriteria']]}
                elif kind == "request.review":
                    proof = ctx["evidence"][0]
                    assert proof["content"] == outcome["deliverable"]
                    receipt = next(row for row in ctx['toolReceipts'] if row['id'] == proof['toolReceiptIds'][0])
                    assert receipt["status"] == "completed" and receipt["toolName"] == "read_file"
                    assert receipt["resultSha256"] == hashlib.sha256(receipt["result"].encode()).hexdigest()
                    assert "A costs 40 dollars" in receipt["result"]
                    assert proof['toolReceiptIds'] == [row['id'] for row in ctx['toolReceipts']]
                    assert not body.get("tools")
                    reviewed.append(proof)
                    output = {"approved": True, "summary": "The retained read supports both costs and their difference.",
                              "evidenceIds": [proof["id"]]}
                else:
                    assert kind == "work.inspect"
                    results = [m for m in body["messages"] if m["role"] == "tool"]
                    if not results:
                        message = {"role": "assistant", "content": None, "tool_calls": [{"id": "actual-file-read", "type": "function",
                                   "function": {"name": "read_file", "arguments": '{"path":"root0/facts.txt","offset":1,"limit":2}'}}]}
                        self.respond(message, "tool_calls")
                        return
                    with store._connect() as conn:
                        row = conn.execute("SELECT status,result FROM tool_receipts").fetchone()
                    assert row["status"] == "completed" and row["result"] == results[0]["content"]
                    output = outcome
                self.respond({"role": "assistant", "content": json.dumps(output)}, "stop")
            except Exception as error:
                errors.append(str(error))
                self.respond({"role": "assistant", "content": '{"intervention":"Local fixture assertion failed"}'}, "stop")

        def respond(self, message, finish):
            response = {"id": "local-flow", "object": "chat.completion", "created": 1, "model": "organization-test",
                        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                        "usage": {"prompt_tokens": 30, "completion_tokens": 20, "total_tokens": 50}}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    cfg = {"model": {"provider": "custom", "default": "organization-test",
                     "base_url": f"http://127.0.0.1:{server.server_port}/v1", "api_mode": "chat_completions",
                     "streaming": False, "context_length": 128000},
           "agent": {"environment_probe": False}, "compression": {"enabled": False},
           "organization": {"capabilities": ["work.inspect"], "tool_grants": ["read_file"],
                            "read_roots": [str(source)], "max_workers": 2, "max_inflight": 1}}
    (home / "config.yaml").write_text(json.dumps(cfg), encoding="utf-8")
    (home / ".env").write_text("OPENAI_API_KEY=local-fixture-only\n", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    config._LOAD_CONFIG_CACHE.clear()
    # The registered read handler is real; backend creation must remain unused.
    def no_backend(*_):
        raise AssertionError("Organization read attempted to create a shell backend")
    monkeypatch.setattr(file_tools, "_get_file_ops", no_backend)
    settings = OrganizationSettings.from_config(cfg)
    store = OrganizationStore(home / "organization" / "state.db", settings)
    objective = store.create_objective("Compare costs", "Read root0/facts.txt and compare the costs.", idempotency_key="real-inspect-once")
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
        assert snapshot["objectives"][0]["status"] == "completed", snapshot["requests"]
        assert len(received) == 6 and len(reviewed) == 1
        assert all(row["status"] == "completed" for row in snapshot["requests"])
        assert {row["type"] for row in snapshot["requests"]} == {"request.plan", "request.hire", "work.inspect", "request.review", "request.integrate", "request.accept"}
        assert str(source) not in json.dumps(received)
        proof = store.evidence(reviewed[0]["id"])
        assert proof["content"] == outcome["deliverable"] and proof["toolReceipts"][0]["status"] == "completed"
        replay = store.create_objective("Compare costs", "Read root0/facts.txt and compare the costs.", idempotency_key="real-inspect-once")
        assert replay["id"] == objective["id"]
        assert len(received) == 6
    finally:
        assert service.stop()
        server.shutdown()
        server.server_close()
        thread.join(3)
        config._LOAD_CONFIG_CACHE.clear()
