"""Internal conversation parsing and real, local-only provider boundaries."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from eidolon_cli import organization_executor as executor


def _message():
    return {"recipientId": "advisor", "subject": "Coordinate the draft", "body": "Which structure do you suggest?"}


def _conversation():
    return {"id": "thread-1", "subject": "Coordinate the draft", "objectiveId": "objective-1",
            "taskId": "task-1", "projectId": None, "status": "open",
            "participants": [{"id": "writer", "name": "Writer", "team": "general"},
                             {"id": "advisor", "name": "Advisor", "team": "general"}],
            "messages": [{"id": "message-1", "senderId": "writer", "recipientId": "advisor",
                          "body": "Which structure do you suggest?", "createdAt": 1,
                          "readAt": None, "replyToId": None}],
            "scopedContext": []}


def test_messages_are_exclusive_pauses_and_replies_have_only_scoped_context():
    outgoing = {"messages": [_message()]}
    # Every model-run stage can pause without first inventing a completion result.
    for kind in executor._REQUEST_TYPES - {"request.message"}:
        parsed = executor._parse_output(json.dumps(outgoing), kind, {})
        assert parsed["messages"][0] == _message()
        with_memory = {**outgoing, "memory": {"facts": ["Waiting for a peer reply."]}}
        assert executor._parse_output(json.dumps(with_memory), kind, {})["memory"]["facts"] == with_memory["memory"]["facts"]
    for extra in ({"summary": "Done", "deliverable": "Done"}, {"requests": []},
                  {"intervention": "Blocked"}, {"approved": True}, {"tasks": []},
                  {"reply": "Already answered"}, {"tools": []}):
        with pytest.raises(executor.OrganizationExecutionError, match="message-paused"):
            executor._parse_output(json.dumps({**outgoing, **extra}), "work.draft", {})
    for messages in (None, [], {}, [_message()] * 5,
                     [{**_message(), "body": "x" * 6001}],
                     [{**_message(), "subject": "x" * 201}],
                     [{**_message(), "senderId": "someone-else"}],
                     [{**_message(), "recipientId": " advisor"}],
                     [{**_message(), "threadId": "thread-1 "}]):
        with pytest.raises(executor.OrganizationExecutionError):
            executor._parse_output(json.dumps({"messages": messages}), "work.draft", {})

    reply = {"reply": "Start with the findings, then supporting material."}
    assert executor._parse_output(json.dumps(reply), "request.message", {}) == reply
    assert executor._parse_output('{"intervention":"Need the selected thread."}', "request.message", {}) == {
        "intervention": "Need the selected thread."}
    for invalid in ({}, {"reply": ""}, {"reply": "x" * 6001}, outgoing,
                    {**reply, "memory": {}}, {**reply, "requests": []},
                    {**reply, "intervention": "Blocked"}, {"intervention": "Blocked", "memory": {}},
                    {**reply, "summary": "Done"}, {"requests": []}):
        with pytest.raises(executor.OrganizationExecutionError):
            executor._parse_output(json.dumps(invalid), "request.message", {})

    own = {"id": "advisor", "team": "general", "name": "Advisor"}
    continuity = {"memory": {}, "recentHistory": [], "contextSummary": ""}
    omitted = {key: "unshared-sender-context" for key in (
        "objective", "task", "workPackage", "workPackages", "dependencies", "evidence", "toolReceipts",
        "projectPolicy", "projectExecutionHistory", "staffing", "feedback", "ownerInputs",
        "requestContract", "requestResponses", "objectiveClarifications", "internalConversations",
        "messagingDirectory", "toolPolicy", "managementPolicy")}
    context = {**omitted, "conversation": _conversation(), "agent": own, "agentContext": continuity}
    prompt = executor._prompt({"type": "request.message", "title": "unshared-sender-context",
                               "description": "unshared-sender-context"}, context, "request.message")
    assert "unshared-sender-context" not in prompt
    submitted = json.loads(prompt.split("Submitted context:\n", 1)[1])
    assert submitted["request"] == {"type": "request.message"}
    assert set(submitted["context"]) == {"conversation", "agent", "agentContext"}
    assert submitted["context"]["conversation"] == context["conversation"]
    assert submitted["context"]["agent"] == own
    assert submitted["context"]["agentContext"]["memory"] == {}
    regular = {"agent": own, "internalConversations": [_conversation()],
               "messagingDirectory": [{"id": "writer", "available": True}]}
    prompt = executor._prompt({"type": "work.draft"}, regular, "work.draft")
    submitted = json.loads(prompt.split("Submitted context:\n", 1)[1])["context"]
    assert submitted["internalConversations"] == regular["internalConversations"]
    assert submitted["messagingDirectory"] == regular["messagingDirectory"]


def _exercise_local_provider_reply(tmp_path, monkeypatch, kind):
    from eidolon_cli import config

    home, source = (tmp_path / "profile").resolve(), (tmp_path / "source").resolve()
    home.mkdir()
    source.mkdir()
    received, tool_calls, reports = [], [], []
    state = {"tool_attempt": False}
    expected = {"reply": "Start with the findings."} if kind == "request.message" else {"messages": [_message()]}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"data":[{"id":"organization-test"}]}')

        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            message = {"role": "assistant", "content": json.dumps(expected)}
            if state["tool_attempt"]:
                message = {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "forbidden-read", "type": "function", "function": {
                        "name": "read_file", "arguments": '{"path":"root0/private.txt"}'}}]}
            response = {"id": "internal-conversation", "object": "chat.completion", "created": 1,
                        "model": "organization-test", "choices": [{"index": 0,
                        "finish_reason": "tool_calls" if state["tool_attempt"] else "stop", "message": message}]}
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
                    "base_url": f"http://127.0.0.1:{server.server_port}/v1",
                    "api_mode": "chat_completions", "streaming": False, "context_length": 128000},
           "agent": {"environment_probe": False}, "compression": {"enabled": False}}
    (home / "config.yaml").write_text(json.dumps(cfg), encoding="utf-8")
    (home / ".env").write_text("OPENAI_API_KEY=local-fixture-only\n", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    config._LOAD_CONFIG_CACHE.clear()
    context = {"agent": {"id": "advisor", "team": "general"},
               "agentContext": {"memory": {}, "recentHistory": []}, "conversation": _conversation(),
               "toolPolicy": {"tools": ["read_file"], "readRoots": [str(source)],
                              "maxToolCalls": 4, "maxResultChars": 2000},
               "recordToolStart": lambda *args: tool_calls.append(args),
               "recordToolFinish": lambda *args: tool_calls.append(args),
               "requestToolReceipts": lambda: [],
               "resolveWorkspaceSource": lambda *args: pytest.fail("A message pause must not read files."),
               "recordContextReceipt": reports.append}
    if kind == "request.message":
        # Even an accidentally overbroad caller cannot copy work/evidence into the reply.
        context.update(objective={"description": "unshared-sender-context"},
                       task={"description": "unshared-sender-context"}, evidence=[{"malformed": True}],
                       dependencies=[{"malformed": True}], toolReceipts=[{"malformed": True}])
    try:
        result = executor.execute({"type": kind, "description": "unshared-sender-context"},
                                  context, threading.Event())
        assert "intervention" not in result, result
        assert all(result[key] == value for key, value in expected.items())
        assert len(received) == 1
        assert tool_calls == []
        if kind == "request.message":
            assert not received[0].get("tools")
            assert "unshared-sender-context" not in json.dumps(received[0])
            assert reports[0]["artifacts"] == reports[0]["dependencies"] == reports[0]["toolReceipts"] == []
            state["tool_attempt"] = True
            blocked = executor.execute({"type": kind}, context, threading.Event())
            assert "intervention" in blocked, blocked
            assert tool_calls == []
            assert all(not wire.get("tools") for wire in received)
        else:
            assert any(tool["function"]["name"] == "read_file" for tool in received[0]["tools"])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
        config._LOAD_CONFIG_CACHE.clear()


def test_local_provider_reply_stays_tool_free_on_native_host(tmp_path, monkeypatch):
    _exercise_local_provider_reply(tmp_path, monkeypatch, "request.message")


@pytest.mark.linux_only
@pytest.mark.parametrize("kind", ["work.inspect", "work.edit"])
def test_linux_file_workers_can_pause_before_reading(tmp_path, monkeypatch, kind):
    _exercise_local_provider_reply(tmp_path, monkeypatch, kind)


@pytest.mark.macos_only
@pytest.mark.parametrize("kind", ["work.inspect", "work.edit"])
def test_macos_file_workers_can_pause_before_reading(tmp_path, monkeypatch, kind):
    _exercise_local_provider_reply(tmp_path, monkeypatch, kind)
