"""Actual read dispatch, durable receipt fences and direct-API tool boundaries."""
import copy
import contextvars
import hashlib
import json
import threading
import time
from types import SimpleNamespace

import pytest

from eidolon_cli import organization_executor as executor
from eidolon_cli.organization_tool_executor import tool_execution


class Receipts:
    def __init__(self):
        self.rows = []
        self.events = []
        self.fenced = False

    def start(self, call_id, name, args):
        self.events.append("start")
        for row in self.rows:
            if row["toolCallId"] == call_id:
                if row["arguments"] != args:
                    raise ValueError("call ID reused")
                return {**row, "created": False}
        row = {"id": "receipt-" + str(len(self.rows)), "toolCallId": call_id,
               "toolName": name, "arguments": args, "status": "running"}
        self.rows.append(row)
        return {**row, "created": True}

    def finish(self, ident, result, status):
        if self.fenced:
            raise ValueError("lease fenced")
        self.events.append("finish")
        row = next(row for row in self.rows if row["id"] == ident)
        row.update(result=result, status=status, resultSha256=hashlib.sha256(result.encode()).hexdigest())
        return dict(row)


@pytest.fixture
def inspection(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "notes.txt").write_text("Option A costs $40.\nOption B costs $70.\n", encoding="utf-8")
    receipts = Receipts()
    context = {"toolPolicy": {"tools": ["read_file"], "readRoots": [str(root)],
                               "maxToolCalls": 8, "maxResultChars": 12000},
               "recordToolStart": receipts.start, "recordToolFinish": receipts.finish,
               "requestToolReceipts": lambda: list(receipts.rows)}
    return context, receipts, root


def _agent(execution):
    class Agent(executor._ToolFreeBoundary):
        pass
    agent = Agent()
    agent.provider, agent.api_mode, agent.model = "custom", "chat_completions", "fixture"
    agent.base_url, agent.request_overrides = "http://127.0.0.1:1/v1", {}
    agent.session_id = "organization-fixture"
    agent._organization_cancel, agent._organization_deadline = threading.Event(), time.monotonic() + 20
    agent._organization_tool_execution = execution
    agent._organization_intervention = None
    execution.install(agent)
    return agent


def _call(path="root0/notes.txt", *, name="read_file", call_id="read-1", **extra):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps({"path": path, **extra})))


@pytest.mark.linux_only
def test_real_registry_read_is_persisted_before_model_and_reused(inspection, monkeypatch):
    context, receipts, _ = inspection
    from tools import file_tools
    monkeypatch.setattr(file_tools, "_get_file_ops", lambda *_: pytest.fail("No shell/backend creation allowed"))
    with tool_execution(context, "work.inspect") as execution:
        agent, messages = _agent(execution), []
        agent._execute_tool_calls(SimpleNamespace(tool_calls=[_call()]), messages, "task-fixture")
        assert receipts.events == ["start", "finish"]
        assert len(messages) == 1 and "Option A costs $40" in messages[0]["content"]
        assert receipts.rows[0]["result"] == messages[0]["content"]
        assert receipts.rows[0]["arguments"] == {"path": "root0/notes.txt", "offset": 1, "limit": 2000}
        execution.verify_completion()
        agent._execute_tool_calls(SimpleNamespace(tool_calls=[_call()]), messages, "task-fixture")
        assert receipts.events == ["start", "finish", "start"]
        assert messages[1]["content"] == messages[0]["content"]


@pytest.mark.parametrize("change", [lambda c: c.pop("toolPolicy"),
                                     lambda c: c["toolPolicy"].update(tools=[]),
                                     lambda c: c["toolPolicy"].update(tools=["terminal"]),
                                     lambda c: c["toolPolicy"].update(readRoots=[]),
                                     lambda c: c.pop("recordToolStart")])
def test_missing_grants_or_audit_fails_before_provider(inspection, change):
    context, receipts, _ = inspection
    change(context)
    result = executor.execute({"type": "work.inspect"}, context, threading.Event())
    assert "intervention" in result and receipts.rows == []


@pytest.mark.linux_only
def test_forbidden_calls_invalid_args_and_empty_evidence_never_succeed(inspection):
    context, receipts, _ = inspection
    with tool_execution(context, "work.inspect") as execution:
        agent = _agent(execution)
        with pytest.raises(executor.OrganizationExecutionError, match="persisted successful"):
            execution.verify_completion()
        for name in ("terminal", "tool_call", "execute_code", "write_file", "mcp_read_file"):
            agent = _agent(execution)
            with pytest.raises(executor.OrganizationExecutionError, match="outside"):
                agent._execute_tool_calls(SimpleNamespace(tool_calls=[_call(name=name)]), [], "fixture")
        assert not receipts.rows
        agent = _agent(execution)
        with pytest.raises(executor.OrganizationExecutionError, match="blocked"):
            agent._execute_tool_calls(SimpleNamespace(tool_calls=[_call("/secret/not-for-audit")]), [], "fixture")
        assert receipts.rows[0]["status"] == "blocked"
        assert "/secret/not-for-audit" not in json.dumps(receipts.rows)
        agent = _agent(execution)
        with pytest.raises(executor.OrganizationExecutionError, match="blocked"):
            agent._execute_tool_calls(SimpleNamespace(tool_calls=[_call(call_id="extra", command="do not execute")]), [], "fixture")
        assert receipts.rows[1]["arguments"] == {}
        with pytest.raises(executor.OrganizationExecutionError, match="blocked or unresolved"):
            execution.verify_completion()


@pytest.mark.linux_only
def test_fenced_results_cannot_enter_model_or_count_as_evidence(inspection):
    context, receipts, _ = inspection
    receipts.fenced = True
    with tool_execution(context, "work.inspect") as execution:
        agent, messages = _agent(execution), []
        with pytest.raises(executor.OrganizationExecutionError, match="durably committed"):
            agent._execute_tool_calls(SimpleNamespace(tool_calls=[_call()]), messages, "fixture")
        assert not messages and receipts.rows[0]["status"] == "running"
        agent = _agent(execution)
        with pytest.raises(executor.OrganizationExecutionError, match="unresolved"):
            agent._execute_tool_calls(SimpleNamespace(tool_calls=[_call()]), messages, "fixture")


@pytest.mark.linux_only
def test_cancellation_and_budget_stop_before_another_read(inspection):
    context, receipts, _ = inspection
    context["toolPolicy"]["maxToolCalls"] = 1
    with tool_execution(context, "work.inspect") as execution:
        agent, messages = _agent(execution), []
        agent._execute_tool_calls(SimpleNamespace(tool_calls=[_call()]), messages, "fixture")
        with pytest.raises(executor.OrganizationExecutionError, match="budget"):
            agent._execute_tool_calls(SimpleNamespace(tool_calls=[_call(call_id="second")]), messages, "fixture")
        assert len(messages) == 1 and receipts.events.count("finish") == 1
        agent._organization_cancel.set()
        with pytest.raises(InterruptedError):
            agent._execute_tool_calls(SimpleNamespace(tool_calls=[_call(call_id="cancelled")]), messages, "fixture")
        assert len(receipts.rows) == 2


@pytest.mark.linux_only
def test_plugin_replacement_cannot_borrow_the_builtin_grant(inspection, monkeypatch):
    from tools.registry import registry
    context, receipts, _ = inspection
    with tool_execution(context, "work.inspect") as execution:
        agent = _agent(execution)
        row = registry.get_entry("read_file")
        monkeypatch.setattr(row, "handler", lambda *args, **kwargs: pytest.fail("Replacement must not execute"))
        with pytest.raises(executor.OrganizationExecutionError, match="plugin replaced"):
            agent._execute_tool_calls(SimpleNamespace(tool_calls=[_call()]), [], "fixture")
        assert receipts.rows == []


@pytest.mark.linux_only
def test_stalled_read_does_not_lock_unrelated_registry_access(inspection, monkeypatch):
    from tools.registry import registry
    context, receipts, _ = inspection
    entered, release, queried = [threading.Event() for _ in range(3)]
    errors = []
    with tool_execution(context, "work.inspect") as execution:
        agent = _agent(execution)
        real_read = execution.scope.read_file

        def stalled_read(*args, **kwargs):
            entered.set()
            assert release.wait(3)
            return real_read(*args, **kwargs)

        monkeypatch.setattr(execution.scope, "read_file", stalled_read)

        def read():
            try:
                agent._execute_tool_calls(SimpleNamespace(tool_calls=[_call()]), [], "fixture")
            except Exception as error:
                errors.append(error)

        worker = threading.Thread(target=contextvars.copy_context().run, args=(read,))
        lookup = threading.Thread(target=lambda: (registry.get_entry("read_file"), queried.set()))
        worker.start()
        try:
            assert entered.wait(2)
            lookup.start()
            assert queried.wait(1), "A bounded read must not hold the global registry lock during I/O"
        finally:
            release.set()
            worker.join(3)
            if lookup.ident is not None:
                lookup.join(3)
        assert not errors and receipts.rows[0]["status"] == "completed"


def test_registry_expected_handler_refuses_replacement():
    from tools.registry import ToolRegistry
    registry = ToolRegistry()
    calls = []
    original = lambda args, **kw: (calls.append("original"), '{"success":true}')[1]
    replacement = lambda args, **kw: (calls.append("replacement"), '{}')[1]
    schema = {"name": "read_file", "parameters": {"type": "object"}}
    registry.register("read_file", "file", schema, original)
    assert json.loads(registry.dispatch("read_file", {}, expected_handler=original))["success"]
    registry.register("read_file", "file", schema, replacement)
    assert "refused" in registry.dispatch("read_file", {}, expected_handler=original)
    assert calls == ["original"]


def test_review_requires_inspection_receipts_and_projects_each_result_once():
    result = json.dumps({'success': True, 'content': 'uniquely observed source ' + 'x' * 6000})
    receipt = {'id': 'receipt', 'toolName': 'read_file', 'status': 'completed', 'result': result,
               'resultSha256': hashlib.sha256(result.encode()).hexdigest()}
    evidence = {'id': 'artifact', 'content': 'A supported inspection.',
                'sha256': hashlib.sha256(b'A supported inspection.').hexdigest()}
    context = {'task': {'type': 'work.inspect'}, 'evidence': [evidence]}
    with pytest.raises(executor.OrganizationExecutionError, match='successful file-read receipts'):
        executor._prompt({'type': 'request.review'}, context, 'request.review')
    evidence['toolReceipts'] = [receipt]
    context['toolReceipts'] = [receipt]
    context['dependencies'] = [{'taskId': 'prior', 'toolReceipts': [receipt]}]
    prompt = executor._prompt({'type': 'request.review'}, context, 'request.review')
    assert prompt.count('uniquely observed source') == 1
    payload = json.loads(prompt.split('Submitted context:\n', 1)[1])['context']
    assert payload['evidence'][0]['toolReceiptIds'] == [receipt['id']]
    assert payload['dependencies'][0]['toolReceiptIds'] == [receipt['id']]
    assert payload['toolReceipts'] == [receipt]


def test_tool_hooks_are_rejected_and_staff_provider_cannot_fallback(monkeypatch):
    from eidolon_cli import config, plugins, runtime_provider
    from eidolon_cli.plugins_manifest import PluginManifest

    cfg = {"model": {"provider": "custom:configured", "default": "configured-model"}}
    monkeypatch.setattr(config, "load_config_readonly", lambda: cfg)
    monkeypatch.setattr(plugins, "discover_plugins", lambda: None)
    manager = plugins.PluginManager()
    plugin = plugins.PluginContext(PluginManifest(name="organization-tool-boundary-test"), manager)
    handle = plugin.register_hook("pre_tool_call", lambda **_: pytest.fail("Blocked hook must not run"))
    monkeypatch.setattr(plugins, "get_plugin_manager", lambda: manager)
    try:
        executor._guard_plugin_integrations()
        with pytest.raises(executor.OrganizationExecutionError, match="plugin hooks") as error:
            executor._guard_plugin_integrations(tool_mode=True)
        assert "organization-tool-boundary-test" in str(error.value)
    finally:
        handle.dispose()
    monkeypatch.setattr(runtime_provider, "resolve_runtime_provider", lambda **kwargs: {
        "provider": "openrouter", "api_mode": "chat_completions", "base_url": "https://example.invalid/v1"})
    with pytest.raises(executor.OrganizationExecutionError, match="different provider"):
        executor._runtime_kwargs({"agent": {"provider": "custom:configured", "model": "configured-model"}}, 20)


@pytest.mark.linux_only
def test_exact_wire_schemas_and_nested_native_controls(inspection):
    context, _, _ = inspection
    with tool_execution(context, "work.inspect") as execution:
        agent = _agent(execution)
        agent._organization_check({"tools": copy.deepcopy(agent.tools), "tool_choice": "auto"})
        cache_tools = copy.deepcopy(agent.tools)
        cache_tools[0]["cache_control"] = {"type": "ephemeral", "ttl": "5m"}
        agent._organization_check({"tools": cache_tools})
        cache_tools[0]["cache_control"]["tools"] = [{"type": "web_search"}]
        with pytest.raises(executor.OrganizationExecutionError, match="cache controls"):
            agent._organization_check({"tools": cache_tools})
        for extra in ({"extra_body": {"tools": [{"type": "web_search"}]}},
                      {"extra_body": {"search_parameters": {"mode": "auto"}}},
                      {"functions": [{"name": "shell"}]},
                      {"tools": [{"type": "web_search"}]},
                      {"tools": [*agent.tools, {"type": "function", "function": {"name": "write_file"}}]}):
            agent = _agent(execution)
            with pytest.raises(executor.OrganizationExecutionError):
                agent._organization_check({"tools": copy.deepcopy(agent.tools), **extra})
        agent = _agent(execution)
        from agent.anthropic_message_convert import convert_tools_to_anthropic
        from agent.codex_responses_adapter import _responses_tools
        from agent.bedrock_adapter import convert_tools_to_converse
        for mode, wire in (
            ("anthropic_messages", {"tools": convert_tools_to_anthropic(agent.tools)}),
            ("codex_responses", {"tools": _responses_tools(agent.tools), "tool_choice": "auto"}),
            ("bedrock_converse", {"toolConfig": {"tools": convert_tools_to_converse(agent.tools)}}),
        ):
            agent.api_mode = mode
            agent._organization_check(wire)
        agent.tools.append({"type": "function", "function": {"name": "terminal"}})
        with pytest.raises(executor.OrganizationExecutionError, match="grant or schema"):
            agent._organization_check()


def test_tampered_tool_receipts_are_rejected_before_review_prompt():
    content = "Analysis of the inspected source."
    context = {"evidence": [{"id": "evidence-one", "content": content,
                             "sha256": hashlib.sha256(content.encode()).hexdigest(),
                             "toolReceipts": [{"id": "receipt-one", "toolName": "read_file", "status": "completed",
                                               "result": '{"success":true,"content":"altered"}', "resultSha256": "wrong"}]}]}
    with pytest.raises(executor.OrganizationExecutionError, match="result hash"):
        executor._prompt({"type": "request.review"}, context, "request.review")


@pytest.mark.linux_only
def test_real_agent_tool_round_against_local_http_provider(inspection, tmp_path, monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path
    from tools import file_tools
    from eidolon_cli import config

    context, receipts, root = inspection
    received = []
    output = {"summary": "Inspected the cost notes", "deliverable": "root0/notes.txt lines 1–2: A costs $40; B costs $70.",
              "memory": {"facts": ["The submitted notes compare options A and B."]}}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"data": [{"id": "organization-test"}]}).encode())

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append(body)
            if len(received) == 1:
                message = {"role": "assistant", "content": None, "tool_calls": [{"id": "inspect-source", "type": "function",
                           "function": {"name": "read_file", "arguments": '{"path":"root0/notes.txt"}'}}]}
                finish = "tool_calls"
            else:
                assert receipts.rows[0]["status"] == "completed"
                message, finish = {"role": "assistant", "content": json.dumps(output)}, "stop"
            response = {"id": "local-read", "object": "chat.completion", "created": 1,
                        "model": "organization-test", "choices": [{"index": 0, "message": message, "finish_reason": finish}],
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
        result = executor.execute({"type": "work.inspect"}, {**context, "timeoutSeconds": 20,
            "objective": {"title": "Inspect source", "description": "Inspect root0/notes.txt and compare the costs."}}, threading.Event())
        assert {key: value for key, value in result.items() if key != "usage"} == output
        assert result["usage"] == {"inputTokens": 60, "outputTokens": 40}
        assert len(received) == 2
        system = next(message['content'] for message in received[0]['messages'] if message['role'] in {'system', 'developer'})
        assert 'persistent member' in system and 'openQuestions' in system
        assert "cannot modify another agent's context" in system
        assert [tool["function"]["name"] for tool in received[0]["tools"]] == ["read_file"]
        assert received[1]["tools"] == received[0]["tools"]
        tool_results = [m for m in received[1]["messages"] if m["role"] == "tool"]
        assert len(tool_results) == 1 and "Option A costs $40" in tool_results[0]["content"]
        assert str(root) not in json.dumps(received)
        assert "recordToolStart" not in json.dumps(received)
        assert len(receipts.rows) == 1 and receipts.rows[0]["status"] == "completed"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
        config._LOAD_CONFIG_CACHE.clear()


@pytest.mark.linux_only
def test_explicit_discovery_grants_have_durable_receipts_and_cannot_replace_source_read(inspection):
    context, receipts, root = inspection
    context['toolPolicy']['tools'] += ['list_files', 'search_files']
    with tool_execution(context, 'work.inspect') as execution:
        agent, messages = _agent(execution), []
        calls = [_call('root0', name='list_files', call_id='list'),
                 _call('root0', name='search_files', call_id='search', query='costs')]
        agent._execute_tool_calls(SimpleNamespace(tool_calls=calls), messages, 'task-fixture')
        assert [row['toolName'] for row in receipts.rows] == ['list_files', 'search_files']
        assert all(row['status'] == 'completed' for row in receipts.rows)
        with pytest.raises(executor.OrganizationExecutionError, match='persisted successful'):
            execution.verify_completion()
        agent._execute_tool_calls(SimpleNamespace(tool_calls=[_call()]), messages, 'task-fixture')
        execution.verify_completion()
        from eidolon_cli.organization_tool_executor import validate_retained_receipts
        validate_retained_receipts(receipts.rows)
        agent._execute_tool_calls(SimpleNamespace(tool_calls=calls), messages, 'task-fixture')
        assert len(receipts.rows) == 3
        assert [row['content'] for row in messages[:2]] == [row['content'] for row in messages[3:]]


@pytest.mark.linux_only
def test_discovery_without_its_explicit_grant_never_starts_a_receipt(inspection):
    context, receipts, _ = inspection
    with tool_execution(context, 'work.inspect') as execution:
        agent = _agent(execution)
        with pytest.raises(executor.OrganizationExecutionError, match='outside'):
            agent._execute_tool_calls(SimpleNamespace(tool_calls=[_call('root0', name='list_files')]), [], 'task')
    assert receipts.rows == []


@pytest.mark.linux_only
@pytest.mark.parametrize('name,extra', [('read_file', {}), ('list_files', {}), ('search_files', {'query': 'project'})])
def test_project_scope_keeps_original_alias_and_blocks_other_roots(inspection, tmp_path, name, extra):
    from eidolon_cli.organization_tool_executor import public_tool_policy

    context, receipts, root = inspection
    second = tmp_path / 'second-project'
    second.mkdir()
    (second / 'notes.txt').write_text('second project evidence\n', encoding='utf-8')
    (root / 'notes.txt').write_text('other project evidence\n', encoding='utf-8')
    context['toolPolicy'].update(tools=['read_file', 'list_files', 'search_files'],
                                 readRoots=[str(root), str(second)], readRootAliases=['root1'])
    assert public_tool_policy(context)['readRoots'] == ['root1']
    with tool_execution(context, 'work.inspect') as execution:
        assert execution.scope.root_aliases == ('root1',)
        agent, messages = _agent(execution), []
        path = 'root1/notes.txt' if name == 'read_file' else 'root1'
        agent._execute_tool_calls(SimpleNamespace(tool_calls=[_call(path, name=name, **extra)]), messages, 'task-project')
        assert json.loads(messages[0]['content'])['success'] is True
        assert 'other project evidence' not in messages[0]['content']
        if name == 'read_file':
            assert 'second project evidence' in messages[0]['content']
        else:
            assert 'root1/notes.txt' in messages[0]['content']
    # Each denied attempt is durable and cannot use the ordinary file backend.
    for denied in (('root0/notes.txt', str(root / 'notes.txt')) if name == 'read_file'
                   else ('root0', str(root))):
        with tool_execution(context, 'work.inspect') as execution:
            agent = _agent(execution)
            with pytest.raises(executor.OrganizationExecutionError, match='blocked'):
                agent._execute_tool_calls(SimpleNamespace(tool_calls=[_call(denied, name=name, call_id='deny-' + denied, **extra)]), [], 'task-project')
            assert receipts.rows[-1]['status'] == 'blocked'


@pytest.mark.linux_only
@pytest.mark.parametrize('aliases', [None, [], ['root2'], ['root0', 'root0'], [0], '/host/root'])
def test_invalid_project_root_restrictions_never_restore_legacy_grants(inspection, aliases):
    context, _, _ = inspection
    context['toolPolicy']['readRootAliases'] = aliases
    with pytest.raises(executor.OrganizationExecutionError):
        with tool_execution(context, 'work.inspect'):
            pytest.fail('Invalid restriction granted filesystem access')


@pytest.mark.linux_only
def test_persisted_project_binding_controls_real_tools_and_revocation(tmp_path):
    from dataclasses import replace
    from eidolon_cli.organization_config import OrganizationSettings
    from eidolon_cli.organization_store import OrganizationStore
    from eidolon_cli.organization_tool_executor import public_tool_policy

    roots = [tmp_path / 'first', tmp_path / 'second']
    for index, root in enumerate(roots):
        root.mkdir()
        (root / 'notes.txt').write_text(f'project {index} source\n', encoding='utf-8')
    grants = ['read_file', 'list_files', 'search_files']
    settings = OrganizationSettings.from_config({'organization': {
        'capabilities': ['work.inspect'], 'tool_grants': grants,
        'read_roots': [str(root) for root in roots],
        'project_grants': [{'id': 'second-tests', 'files': ['root1/notes.txt'],
                            'execution': {'recipe': 'python_unittest', 'root': 'root1'}}],
        'projects': [{'id': 'second', 'root': 'root1', 'recipe': 'second-tests', 'team': 'engineering'}],
        'roster': [{'id': 'reader', 'name': 'Reader', 'team': 'engineering',
                    'capabilities': ['work.inspect'], 'tool_grants': grants}]}})
    store = OrganizationStore(tmp_path / 'state.db', settings)
    store.create_objective('Inspect selected project', idempotency_key='bound-read', project_ids=['second'])
    planner = store.claim_next()
    assert public_tool_policy(store.context(planner)) == {'tools': [], 'readRoots': []}
    store.finish(planner, {'workers': 1, 'tasks': [{'title': 'Read second project',
        'description': 'Inspect the selected source.', 'type': 'work.inspect',
        'team': 'engineering', 'projectId': 'second', 'dependsOn': []}]})
    hire = store.claim_next()
    assert hire['type'] == 'request.hire'
    store.finish(hire, {})
    claim = store.claim_next()
    context = store.context(claim)
    assert context['task']['projectId'] == 'second'
    assert public_tool_policy(context)['readRoots'] == ['root1']
    context.update(recordToolStart=lambda call, name, args: store.record_tool_start(claim, call, name, args),
                   recordToolFinish=lambda receipt, result, status: store.record_tool_finish(claim, receipt, result, status),
                   requestToolReceipts=lambda: store.tool_receipts(claim['id']))
    with tool_execution(context, 'work.inspect') as execution:
        agent, messages = _agent(execution), []
        calls = [_call('root1/notes.txt'), _call('root1', name='list_files', call_id='list'),
                 _call('root1', name='search_files', call_id='search', query='project')]
        agent._execute_tool_calls(SimpleNamespace(tool_calls=calls), messages, claim['task_id'])
        assert all(json.loads(message['content'])['success'] for message in messages)
        assert 'project 1 source' in messages[0]['content']
        assert len(store.tool_receipts(claim['id'])) == 3
        for name, path, extra in [('read_file', 'root0/notes.txt', {}), ('list_files', 'root0', {}),
                                  ('search_files', 'root0', {'query': 'project'})]:
            agent = _agent(execution)
            with pytest.raises(executor.OrganizationExecutionError, match='blocked'):
                agent._execute_tool_calls(SimpleNamespace(tool_calls=[_call(path, name=name, call_id='deny-' + name, **extra)]), [], claim['task_id'])
            assert store.tool_receipts(claim['id'])[-1]['status'] == 'blocked'
        # A running worker cannot continue under a revoked owner binding, even
        # with a previously captured policy and still-open source descriptor.
        assert len(store.tool_receipts(claim['id'])) == 6
        agent = _agent(execution)
        store.settings = replace(settings, projects=())
        with pytest.raises(ValueError, match='binding changed or was revoked'):
            store.context(claim)
        with pytest.raises(executor.OrganizationExecutionError, match='Tool audit could not start'):
            agent._execute_tool_calls(SimpleNamespace(tool_calls=[_call('root1/notes.txt', call_id='revoked')]), [], claim['task_id'])
