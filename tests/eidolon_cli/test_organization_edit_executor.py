"""Pure edit-proposal parsing and actual scoped dispatch without model write tools."""
import copy
import hashlib
import json
import threading
import time
from types import SimpleNamespace

import pytest

from eidolon_cli import organization_executor as executor
from eidolon_cli.organization_tool_executor import public_tool_policy, tool_execution


@pytest.fixture
def proposal():
    return {"summary": "Replace the exact first line", "edit": {
        "path": "root0/notes.txt", "baseRevision": 0, "baseSha256": hashlib.sha256(b"before\r\n").hexdigest(),
        "oldText": "before\r\n", "newText": "after\r\n"}}


def test_edit_parser_preserves_bom_whitespace_line_endings_and_empty_deletion(proposal):
    for old, new in (("\ufeff before\r\n  ", "\ufeff after\r\n\n  "), ("\n", ""), (" ", "\t"), ("雪" * 10922, "x" * 32768)):
        proposal["edit"].update(oldText=old, newText=new)
        result = executor._parse_output(json.dumps(proposal), "work.edit", {})
        assert result == proposal
        assert result["edit"]["oldText"].encode() == old.encode()
        assert result["edit"]["newText"].encode() == new.encode()


def test_edit_parser_bounds_decoded_bytes_equally_for_escaped_and_literal_json(proposal):
    proposal["edit"].update(oldText="é" * 16384, newText="ñ" * 16384)
    literal = json.dumps(proposal, ensure_ascii=False)
    escaped = json.dumps(proposal, ensure_ascii=True)
    assert len(literal) < 128000 < len(escaped)
    assert executor._parse_output(literal, "work.edit", {}) == proposal
    assert executor._parse_output(escaped, "work.edit", {}) == proposal
    proposal["edit"]["newText"] += "é"
    for ensure_ascii in (False, True):
        with pytest.raises(executor.OrganizationExecutionError, match="32768-byte"):
            executor._parse_output(json.dumps(proposal, ensure_ascii=ensure_ascii), "work.edit", {})


@pytest.mark.parametrize("change", [
    lambda p: p.update(diff="invented diff"), lambda p: p.update(receipt={}),
    lambda p: p.update(apply=True), lambda p: p.update(edit=[p["edit"]]),
    lambda p: p["edit"].update(diff="invented diff"), lambda p: p["edit"].pop("baseRevision"),
    lambda p: p["edit"].update(baseRevision=True), lambda p: p["edit"].update(baseRevision=-1),
    lambda p: p["edit"].update(baseRevision="0"), lambda p: p["edit"].update(baseSha256="not a source hash"),
    lambda p: p["edit"].update(oldText=""), lambda p: p["edit"].update(oldText=None),
    lambda p: p["edit"].update(newText=None), lambda p: p["edit"].update(newText="雪" * 10923),
    lambda p: p["edit"].update(oldText="x" * 32769), lambda p: p["edit"].update(oldText="\ud800"),
    lambda p: p["edit"].update(path="/tmp/source.txt"), lambda p: p["edit"].update(path="root0/../source.txt"),
    lambda p: p["edit"].update(path="root0/source.txt\n"), lambda p: p["edit"].update(path="root00/source.txt"),
    lambda p: p["edit"].update(path=" root0/source.txt"), lambda p: p["edit"].update(path="root0/~user/file"),
])
def test_edit_parser_rejects_invented_authority_ambiguous_paths_and_invalid_byte_shapes(proposal, change):
    change(proposal)
    with pytest.raises(executor.OrganizationExecutionError):
        executor._parse_output(json.dumps(proposal), "work.edit", {})


def test_edit_review_binds_approval_and_rejection_to_exact_persisted_proposal():
    content = "Managed edit proposal descriptor"
    proposal = {"id": "proposal_one", "proposalSha256": "a" * 64}
    ctx = {"task": {"type": "work.edit"}, "evidence": [{"id": "artifact_one", "content": content,
           "sha256": hashlib.sha256(content.encode()).hexdigest(), "editProposal": proposal}]}
    for approved in (True, False):
        output = {"approved": approved, "summary": "Exact proposal checked", "evidenceIds": ["artifact_one"], "proposalId": proposal["id"], "proposalSha256": proposal["proposalSha256"]}
        assert executor._parse_output(json.dumps(output), "request.review", ctx) == output
        for key in ("proposalId", "proposalSha256"):
            changed = {**output, key: "invented"}
            with pytest.raises(executor.OrganizationExecutionError, match="exact proposal"):
                executor._parse_output(json.dumps(changed), "request.review", ctx)


@pytest.fixture
def managed(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    original = "\ufeffbefore\r\nline two\n"
    (root / "notes.txt").write_bytes(original.encode())
    rows, captured = [], {}

    def start(call_id, name, args):
        row = {"id": f"tool_{len(rows)}", "toolCallId": call_id, "toolName": name,
               "arguments": args, "status": "running"}
        rows.append(row)
        return {**row, "created": True}

    def finish(identifier, result, status):
        row = next(row for row in rows if row["id"] == identifier)
        row.update(status=status, result=result, resultSha256=hashlib.sha256(result.encode()).hexdigest())
        return row

    def resolve(path, loader):
        if path not in captured:
            content = loader()
            captured[path] = {"content": content, "workspaceId": "obj_one", "workspaceRevision": 0,
                              "sourceSha256": hashlib.sha256(content.encode()).hexdigest()}
        return captured[path]

    context = {"objective": {"id": "obj_one", "title": "Revise the first line"},
               "toolPolicy": {"tools": ["read_file", "patch"], "readRoots": [str(root)],
                              "maxToolCalls": 3, "maxResultChars": 1000},
               "recordToolStart": start, "recordToolFinish": finish, "requestToolReceipts": lambda: rows,
               "resolveWorkspaceSource": resolve}
    return context, rows, root, original


def _agent(execution):
    class Agent(executor._ToolFreeBoundary):
        pass
    agent = Agent()
    agent.provider, agent.api_mode, agent.model = "custom", "chat_completions", "fixture"
    agent.base_url, agent.request_overrides = "http://127.0.0.1:1/v1", {}
    agent.session_id = "organization-edit-fixture"
    agent._organization_cancel, agent._organization_deadline = threading.Event(), time.monotonic() + 20
    agent._organization_tool_execution = execution
    agent._organization_intervention = None
    execution.install(agent)
    return agent


def _call(*, name="read_file", args=None):
    return SimpleNamespace(id="real-read", function=SimpleNamespace(name=name, arguments=json.dumps(args or {"path": "root0/notes.txt", "limit": 1})))


@pytest.mark.linux_only
def test_patch_grant_never_exposes_dispatch_and_proposal_requires_actual_base_receipt(managed, monkeypatch):
    from tools import file_tools
    context, rows, root, original = managed
    monkeypatch.setattr(file_tools, "_get_file_ops", lambda *_: pytest.fail("No generic backend allowed"))
    with tool_execution(context, "work.edit") as execution:
        agent, messages = _agent(execution), []
        assert agent.valid_tool_names == {"read_file"}
        assert public_tool_policy(context) == {"tools": ["read_file"], "readRoots": ["root0"]}
        assert [item["function"]["name"] for item in agent.tools] == ["read_file"]
        for name in ("patch", "apply_patch", "write_file", "request.apply", "terminal"):
            agent = _agent(execution)
            with pytest.raises(executor.OrganizationExecutionError, match="outside"):
                agent._execute_tool_calls(SimpleNamespace(tool_calls=[_call(name=name)]), messages, "task_one")
        assert not rows
        agent = _agent(execution)
        with pytest.raises(executor.OrganizationExecutionError, match="outside the exact"):
            agent._organization_check({"tools": [*agent.tools, {"type": "function", "function": {"name": "patch"}}]})
        agent = _agent(execution)
        agent._execute_tool_calls(SimpleNamespace(tool_calls=[_call()]), messages, "task_one")
        result = json.loads(messages[0]["content"])
        assert result["content"] == "\ufeffbefore\r\n" and result["truncated"] is True
        edit = {"path": "root0/notes.txt", "baseRevision": result["workspaceRevision"],
                "baseSha256": result["sourceSha256"], "oldText": "before", "newText": "after"}
        execution.verify_completion(edit)
        for key, value in (("path", "root0/other.txt"), ("baseRevision", 1), ("baseSha256", "0" * 64)):
            with pytest.raises(executor.OrganizationExecutionError, match="completed managed source read"):
                execution.verify_completion({**edit, key: value})
        row = rows[0]
        parsed = json.loads(row["result"])
        for field, value in (("workspaceId", "obj_other"), ("content", ""), ("contentFormat", "numbered")):
            row["result"] = json.dumps({**parsed, field: value})
            row["resultSha256"] = hashlib.sha256(row["result"].encode()).hexdigest()
            with pytest.raises(executor.OrganizationExecutionError, match="completed managed source read"):
                execution.verify_completion(edit)
    assert (root / "notes.txt").read_bytes() == original.encode()


@pytest.mark.linux_only
def test_edit_needs_read_grant_and_resolver_but_patch_is_only_for_later_apply(managed):
    context, rows, _, _ = managed
    without_patch = copy.deepcopy({key: value for key, value in context.items() if key == "toolPolicy"})
    without_patch["toolPolicy"]["tools"] = ["read_file"]
    with tool_execution({**context, **without_patch}, "work.edit") as execution:
        assert execution.names == {"read_file"}
    for changed in ({"toolPolicy": {**context["toolPolicy"], "tools": ["patch"]}},
                    {"resolveWorkspaceSource": None}):
        result = executor.execute({"type": "work.edit"}, {**context, **changed}, threading.Event())
        assert "intervention" in result and not rows
    context["toolPolicy"]["tools"] = ["read_file"]
    with tool_execution(context, "work.inspect") as execution:
        assert execution.scope.resolve_workspace_source is None
        assert "1|\ufeffbefore" in execution.scope.read_file("root0/notes.txt")
    assert "intervention" in executor.execute({"type": "request.apply"}, context, threading.Event())


@pytest.mark.linux_only
@pytest.mark.parametrize("provider_tool", ["read_file", "patch"])
def test_real_agent_localhost_edit_round_cannot_dispatch_configured_patch(managed, tmp_path, monkeypatch, provider_tool):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path
    from tools import file_tools
    from eidolon_cli import config

    context, rows, root, original = managed
    received, errors, outputs = [], [], []

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
                assert [item["function"]["name"] for item in body["tools"]] == ["read_file"]
                results = [message for message in body["messages"] if message["role"] == "tool"]
                if not results:
                    message = {"role": "assistant", "content": None, "tool_calls": [{"id": "managed-source", "type": "function",
                               "function": {"name": provider_tool, "arguments": '{"path":"root0/notes.txt"}'}}]}
                    finish = "tool_calls"
                else:
                    source = json.loads(results[0]["content"])
                    assert source["content"] == original
                    assert source["contentFormat"] == "raw"
                    assert source["sourceSha256"] == hashlib.sha256(original.encode()).hexdigest()
                    assert source["workspaceRevision"] == 0 and source["workspaceId"] == "obj_one"
                    assert rows[0]["status"] == "completed" and rows[0]["result"] == results[0]["content"]
                    output = {"summary": "Proposed an exact first-line replacement", "edit": {
                        "path": "root0/notes.txt", "baseRevision": source["workspaceRevision"],
                        "baseSha256": source["sourceSha256"], "oldText": "\ufeffbefore\r\n", "newText": "\ufeffafter\r\n"}}
                    outputs.append(output)
                    message, finish = {"role": "assistant", "content": json.dumps(output)}, "stop"
            except Exception as error:
                errors.append(str(error))
                message, finish = {"role": "assistant", "content": '{"intervention":"Local fixture assertion failed"}'}, "stop"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"id": "local-edit", "object": "chat.completion", "created": 1,
                "model": "organization-test", "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                "usage": {"prompt_tokens": 30, "completion_tokens": 20, "total_tokens": 50}}).encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("OPENAI_API_KEY", "local-test-only")
    monkeypatch.setenv("HERMES_YOLO_MODE", "1")
    monkeypatch.setattr(file_tools, "_get_file_ops", lambda *_: pytest.fail("No shell/backend creation allowed"))
    (home / "config.yaml").write_text(json.dumps({
        "model": {"provider": "custom", "default": "organization-test", "base_url": f"http://127.0.0.1:{server.server_port}/v1",
                  "api_mode": "chat_completions", "streaming": False, "context_length": 128000},
        "agent": {"environment_probe": False}, "compression": {"enabled": False}, "approvals": {"mode": "off"},
    }), encoding="utf-8")
    config._LOAD_CONFIG_CACHE.clear()
    try:
        result = executor.execute({"type": "work.edit"}, {**context, "timeoutSeconds": 20}, threading.Event())
        assert not errors
        system = next(message['content'] for message in received[0]['messages'] if message['role'] in {'system', 'developer'})
        assert 'persistent member' in system and 'openQuestions' in system
        assert "cannot modify another agent's context" in system
        if provider_tool == "read_file":
            assert {key: value for key, value in result.items() if key != "usage"} == outputs[0]
            assert result["usage"] == {"inputTokens": 60, "outputTokens": 40}
            assert len(received) == 2 and len(rows) == 1 and rows[0]["status"] == "completed"
            assert received[0]["tools"] == received[1]["tools"]
        else:
            assert "intervention" in result
            assert "outside the configured organization grant" in result["intervention"]
            assert len(received) == 1 and not rows
        assert str(root) not in json.dumps(received)
        assert "resolveWorkspaceSource" not in json.dumps(received)
        assert "recordToolStart" not in json.dumps(received)
        assert (root / "notes.txt").read_bytes() == original.encode()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
        config._LOAD_CONFIG_CACHE.clear()


@pytest.mark.windows_only
def test_native_windows_edit_execution_fails_closed_before_reads_or_provider(managed, monkeypatch):
    from tools import organization_file_read
    context, receipts, root, original = managed
    monkeypatch.setattr(organization_file_read, '_open_root',
                        lambda *_: pytest.fail('Unsupported Windows scope must not open a source root'))
    monkeypatch.setattr(executor, '_create_agent',
                        lambda *_args, **_kwargs: pytest.fail('Unsupported Windows scope must not start a provider'))
    result = executor.execute({'type': 'work.edit'}, context, threading.Event())
    assert 'POSIX no-follow' in result['intervention']
    assert receipts == []
    assert organization_file_read.get_organization_file_read_scope() is None
    assert (root / 'notes.txt').read_bytes() == original.encode('utf-8')
