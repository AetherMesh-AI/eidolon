"""Organization provider boundary: real parsing and guard code, no billed calls."""
import hashlib
import json
import threading
from types import SimpleNamespace

import pytest

from eidolon_cli import organization_executor as executor


@pytest.fixture
def runtime(monkeypatch):
    import run_agent
    from agent import chat_completion_helpers, chat_completion_nonstream
    from eidolon_cli import config, runtime_provider

    state = SimpleNamespace(
        instances=[], sent=[], resolves=[], wire={}, behavior=None,
        output={"summary": "A complete draft", "deliverable": "The supplied material, rewritten."},
        route={"provider": "custom:local", "requested_provider": "custom:local",
               "api_mode": "chat_completions", "base_url": "http://127.0.0.1:1/v1",
               "api_key": "fake-key", "request_overrides": {"temperature": 0.2}},
    )
    cfg = {"model": {"provider": "custom:local", "default": "configured-model"},
           "agent": {"reasoning_effort": "high"},
           "provider_routing": {"only": ["selected-backend"], "require_parameters": True}}
    state.config = cfg
    monkeypatch.setattr(config, "load_config_readonly", lambda: cfg)

    def resolve(**kwargs):
        state.resolves.append(kwargs)
        return dict(state.route)

    monkeypatch.setattr(runtime_provider, "resolve_runtime_provider", resolve)

    class FakeAIAgent:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.__dict__.update(kwargs)
            self.tools, self.valid_tool_names = [], set()
            self.owner = threading.get_ident()
            self.closed = threading.Event()
            self.interrupted = threading.Event()
            state.instances.append(self)

        def run_conversation(self, *, user_message):
            self.prompt = user_message
            if state.behavior:
                return state.behavior(self)
            self._interruptible_api_call(state.wire)
            return {"final_response": json.dumps(state.output)}

        def _resolved_api_call_timeout(self):
            return 1000

        def interrupt(self, message, *, hard_cancel):
            assert hard_cancel is True
            self.interrupted.set()

        def close(self):
            assert threading.get_ident() == self.owner
            self.closed.set()

    monkeypatch.setattr(run_agent, "AIAgent", FakeAIAgent)

    class FakeRequest:
        def __init__(self, agent, kwargs, *unused):
            self.kwargs = kwargs
            self.thread = self.worker = None

        def run(self):
            state.sent.append(self.kwargs)

    monkeypatch.setattr(chat_completion_nonstream, "_NonStreamRequest", FakeRequest)
    monkeypatch.setattr(chat_completion_helpers, "_StreamingCall", FakeRequest)
    return state


def test_configured_provider_and_strict_stage_outputs(runtime):
    context = {"objective": {"title": "Summarize my material", "description": "Submitted text"},
               "maxTasks": 12, "maxWorkers": 2}
    cancel = threading.Event()
    result = executor.execute({"type": "work.draft"}, context, cancel)
    assert result == runtime.output
    agent = runtime.instances[-1]
    assert agent.closed.is_set()
    assert runtime.resolves[-1] == {"requested": "custom:local", "target_model": "configured-model"}
    assert agent.kwargs["api_key"] == "fake-key"
    assert agent.kwargs["provider"] == "custom:local"
    assert agent.kwargs["enabled_toolsets"] == []
    assert "kanban" in agent.kwargs["disabled_toolsets"]
    assert agent.kwargs["skip_memory"] and agent.kwargs["skip_context_files"]
    assert agent.kwargs["skip_background_review"]
    assert agent.kwargs["providers_allowed"] == ["selected-backend"]
    assert agent.kwargs["provider_require_parameters"] is True
    assert agent.kwargs["fallback_model"] is None
    assert 0 < agent._resolved_api_call_timeout() <= 180
    assert "fake-key" not in agent.prompt

    # The same provider resolver handles native wire protocols without selecting
    # a preferred vendor or silently substituting credentials/model defaults.
    for mode in ("anthropic_messages", "codex_responses", "bedrock_converse"):
        runtime.route.update(provider="selected-provider", api_mode=mode)
        assert executor.execute({"type": "work.analyze"}, context, cancel) == runtime.output
        assert runtime.instances[-1].kwargs["api_mode"] == mode

    tasks = [{"title": "Analyze", "description": "Find the key points", "type": "work.analyze",
              "team": "general", "dependsOn": []},
             {"title": "Draft", "description": "Write from the key points", "type": "work.draft",
              "team": "general", "dependsOn": [0]}]
    runtime.output = {"tasks": tasks, "workers": 2}
    assert executor.execute({"type": "request.plan"}, context, cancel) == runtime.output
    # No silent general-worker fallback for unavailable capabilities.
    tasks[1]["type"], tasks[1]["team"] = "work.publish", "publishing"
    assert executor.execute({"type": "request.plan"}, context, cancel)["tasks"][1] == tasks[1]
    for dependency in (1, -1, True, "0"):
        tasks[1]["dependsOn"] = [dependency]
        assert "intervention" in executor.execute({"type": "request.plan"}, context, cancel)
    tasks[1]["dependsOn"] = [0]
    runtime.output["workers"] = 3
    assert executor.execute({"type": "request.plan"}, context, cancel)["workers"] == 3
    runtime.output["workers"] = 9
    assert "intervention" in executor.execute({"type": "request.plan"}, context, cancel)

    evidence = {"id": "artifact-1", "content": "The artifact bytes"}
    evidence["sha256"] = hashlib.sha256(evidence["content"].encode()).hexdigest()
    review_context = {**context, "evidence": [evidence]}
    runtime.output = {"approved": True, "summary": "Meets the supplied requirements", "evidenceIds": [evidence["id"]]}
    assert executor.execute({"kind": "request.review"}, review_context, cancel) == runtime.output
    for bad_ids in ([], ["made-up"], ["artifact-1", "artifact-1"]):
        runtime.output["evidenceIds"] = bad_ids
        assert "intervention" in executor.execute({"type": "request.review"}, review_context, cancel)
    sent = len(runtime.sent)
    evidence["content"] = "Tampered after persistence"
    assert "hash" in executor.execute({"type": "request.review"}, review_context, cancel)["intervention"]
    assert len(runtime.sent) == sent
    with pytest.raises(executor.OrganizationExecutionError, match="JSON"):
        executor._parse_output("not json", "work.draft", context)
    assert executor._parse_output('```json\n{"intervention":"Need source text"}\n```', "work.draft", context) == {
        "intervention": "Need source text"}


def test_tool_and_native_transport_boundaries_and_cancellation(runtime):
    cancel = threading.Event()
    context = {"objective": {"title": "Draft", "description": "Only submitted text"}}
    # Native server tools injected after config resolution still never reach a
    # provider. Both streaming and nonstreaming final-send seams are covered.
    for wire in ({"tools": [{"type": "web_search"}]},
                 {"extra_body": {"tools": [{"type": "code_interpreter"}]}},
                 {"extra_body": {"search_parameters": {"mode": "auto"}}},
                 {"model": "gpt-5-search-api"},
                 {"model": "safe-text-model", "extra_body": {"model": "openai/gpt-5-search-api:some-variant"}},
                 {"toolConfig": {"tools": [{"toolSpec": {"name": "shell"}}]}}):
        runtime.wire = wire
        for stream in (False, True):
            def send(agent):
                method = agent._interruptible_streaming_api_call if stream else agent._interruptible_api_call
                method(runtime.wire)
                pytest.fail("A tool-enabled request escaped the boundary")
            runtime.behavior = send
            assert "intervention" in executor.execute({"type": "work.draft"}, context, cancel)
    assert runtime.sent == []
    runtime.wire = {"messages": [{"role": "user", "content": '{"tools":["text only"]}'}]}
    runtime.behavior = None
    assert executor.execute({"type": "work.draft"}, context, cancel) == runtime.output

    def attempt_tool(agent):
        agent._execute_tool_calls(None, [], "org-task")
        pytest.fail("A tool dispatch escaped the boundary")
    runtime.behavior = attempt_tool
    assert "no tool was executed" in executor.execute({"type": "work.draft"}, context, cancel)["intervention"]
    runtime.behavior = None
    before = len(runtime.instances)
    for route in ({"api_mode": "codex_app_server"}, {"base_url": "acp://external"},
                  {"command": "external-agent"}, {"request_overrides": {"tools": [{"type": "web_search"}]}}):
        original = dict(runtime.route)
        runtime.route.update(route)
        assert "intervention" in executor.execute({"type": "work.draft"}, context, cancel)
        runtime.route = original
    assert len(runtime.instances) == before


    for model in ("gpt-5-search-api", "openai/gpt-5-search-api", "gpt-4o-search-preview-2025-03-11"):
        assert "search-enabled" in executor.execute({"type": "work.draft"}, {
            **context, "agent": {"model": model}}, cancel)["intervention"]
    assert len(runtime.instances) == before

    # Cancellation uses the real interrupt contract; the occupied worker does
    # not return until the provider unwinds and closes on its owner thread.
    entered = threading.Event()
    def blocked(agent):
        entered.set()
        assert agent.interrupted.wait(3)
        return {"final_response": json.dumps(runtime.output)}
    runtime.behavior = blocked
    result = []
    worker = threading.Thread(target=lambda: result.append(executor.execute({"type": "work.draft"}, context, cancel)))
    worker.start()
    assert entered.wait(3)
    cancel.set()
    worker.join(3)
    assert not worker.is_alive()
    assert "cancelled" in result[0]["intervention"]
    assert runtime.instances[-1].closed.is_set()
    cancel.clear()
    result = executor.execute({"type": "work.draft"}, {**context, "timeoutSeconds": 0.1}, cancel)
    assert "timed out" in result["intervention"]
    assert runtime.instances[-1].closed.is_set()
    before = len(runtime.instances)
    cancel.set()
    assert "intervention" in executor.execute({"type": "work.draft"}, context, cancel)
    assert len(runtime.instances) == before


def test_strict_profile_blocks_global_sdk_identities_before_resolution(runtime, monkeypatch):
    from agent.secret_scope import reset_secret_scope, reset_secret_scope_required, set_secret_scope, set_secret_scope_required

    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "other-profile-fake-key")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "other-profile-fake-secret")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/other-profile/fake.json")
    scope = set_secret_scope({})
    strict = set_secret_scope_required(True)
    try:
        for provider in ("bedrock", "aws", "vertex", "google-vertex", "azure-foundry", "auto"):
            runtime.config["model"].update(provider=provider, auth_mode="entra_id")
            before = len(runtime.resolves)
            result = executor.execute({"type": "work.draft"}, {}, threading.Event())
            assert "profile" in result["intervention"]
            assert len(runtime.resolves) == before
        runtime.config["model"].update(provider="custom:local", auth_mode="api_key")
        for route in ({"provider": "bedrock"}, {"auth_mode": "entra_id"},
                      {"api_mode": "bedrock_converse"},
                      {"base_url": "https://bedrock-mantle.us-east-1.api.aws/openai/v1", "api_key": "aws-sdk"}):
            original = dict(runtime.route)
            runtime.route.update(route)
            assert "profile" in executor.execute({"type": "work.draft"}, {}, threading.Event())["intervention"]
            runtime.route = original
        assert not runtime.instances
        assert executor.execute({"type": "work.draft"}, {}, threading.Event()) == runtime.output
    finally:
        reset_secret_scope_required(strict)
        reset_secret_scope(scope)


def test_real_agent_and_profile_resolution_against_local_provider(tmp_path, monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path

    received = []
    output = {"summary": "Analyzed the submitted text", "deliverable": "The supplied text has two findings."}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"data": [{"id": "organization-test"}]}).encode())

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append((self.path, body))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            response = {"id": "local-response", "object": "chat.completion", "created": 1,
                        "model": "organization-test", "choices": [{"index": 0,
                        "message": {"role": "assistant", "content": json.dumps(output)},
                        "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 30, "completion_tokens": 20, "total_tokens": 50}}
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
    (home / "config.yaml").write_text(json.dumps({
        "model": {"provider": "custom", "default": "organization-test",
                  "base_url": f"http://127.0.0.1:{server.server_port}/v1",
                  "api_mode": "chat_completions", "streaming": False, "context_length": 128_000},
        "agent": {"environment_probe": False}, "compression": {"enabled": False},
    }))
    try:
        # Exercise the real Desktop serve preparation, not only a module import.
        # A minimal fresh profile must retain ordinary backend registrations
        # without enabling side-effectful turn hooks or suppressing the guard.
        from types import SimpleNamespace
        from eidolon_cli.main import _dashboard_prepare_runtime
        from eidolon_cli.plugins import get_plugin_manager
        from eidolon_cli.web_server_messaging import _messaging_platform_catalog
        monkeypatch.setenv('HERMES_DESKTOP', '1')
        assert _dashboard_prepare_runtime(SimpleNamespace(), True)
        assert any(platform['id'] == 'raft' for platform in _messaging_platform_catalog())
        assert any(plugin.enabled and plugin.manifest.kind == 'backend'
                   for plugin in get_plugin_manager()._plugins.values())
        result = executor.execute({"type": "work.analyze"}, {
            "objective": {"title": "Analyze", "description": "First finding. Second finding."},
            "timeoutSeconds": 20,
        }, threading.Event())
        assert {key: value for key, value in result.items() if key != "usage"} == output
        assert result["usage"] == {"inputTokens": 30, "outputTokens": 20}
        assert received
        assert all(path == "/v1/chat/completions" for path, _ in received)
        for _, body in received:
            assert body["model"] == "organization-test"
            assert not body.get("tools")
            assert not body.get("functions")
            assert any("First finding. Second finding." in str(message.get("content")) for message in body["messages"])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


@pytest.mark.parametrize("streaming", [False, True])
def test_real_cancelled_provider_keeps_scheduler_fence_until_it_exits(tmp_path, monkeypatch, streaming):
    from dataclasses import replace
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path
    import time
    import run_agent
    from eidolon_cli.organization_config import OrganizationSettings
    from eidolon_cli.organization_service import OrganizationService
    from eidolon_cli.organization_store import OrganizationStore

    entered, aborted, release, second = [threading.Event() for _ in range(4)]
    received = []

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
                entered.set()
                assert release.wait(15), "The test must release its delayed provider response"
            else:
                second.set()
            # Don't send even headers until released: a provider stuck in
            # admission may not yet have supplied a cancellable stream handle.
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream" if body.get("stream") else "application/json")
            self.end_headers()
            output = json.dumps({"intervention": "Fixture stops after this provider call"})
            envelope = {"id": "local-cancel-test", "model": "organization-test", "created": 1}
            if body.get("stream"):
                for delta, finish in (({"role": "assistant", "content": output}, None), ({}, "stop")):
                    chunk = {**envelope, "object": "chat.completion.chunk",
                             "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                    self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
                self.wfile.write(b"data: [DONE]\n\n")
            else:
                self.wfile.write(json.dumps({**envelope, "object": "chat.completion", "choices": [{
                    "index": 0, "message": {"role": "assistant", "content": output}, "finish_reason": "stop"}]}).encode())

        def log_message(self, *_):
            pass

    # Simulate a provider/transport which cannot acknowledge socket cancellation.
    # Every other layer, including HTTP, AIAgent, and the scheduler, is real.
    def ignore_abort(agent, client, *, reason):
        aborted.set()
    monkeypatch.setattr(run_agent.AIAgent, "_abort_request_openai_client", ignore_abort)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    (home / "config.yaml").write_text(json.dumps({
        "model": {"provider": "custom", "default": "organization-test",
                  "base_url": f"http://127.0.0.1:{server.server_port}/v1",
                  "api_mode": "chat_completions", "streaming": streaming, "context_length": 128_000},
        "agent": {"environment_probe": False}, "compression": {"enabled": False},
    }))
    settings = replace(OrganizationSettings(), max_inflight=1, timeout_seconds=20)
    store = OrganizationStore(home / "organization" / "state.db", settings)
    first = store.create_objective("First", idempotency_key="first")
    store.create_objective("Second", idempotency_key="second")
    service = OrganizationService(store, home=home, settings=settings, poll_seconds=0.01)
    peer = OrganizationService(store, home=home, settings=settings, poll_seconds=0.01)
    try:
        service.start()
        assert entered.wait(5)
        assert service.cancel(first["id"])
        assert aborted.wait(5)
        with service._lock:
            running = next(iter(service._running.values())).thread
        # Exceed even the generic Relay teardown's 2s grace. The real provider
        # thread is still blocked on HTTP, so the caller must retain its slot.
        running.join(timeout=2.5)
        assert running.is_alive()
        peer._fill_slots()
        assert not peer._running
        assert len(received) == 1
        release.set()
        assert second.wait(5)
        deadline = time.monotonic() + 5
        while service._running and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not service._running
        assert len(received) == 2
        assert all(bool(body.get("stream")) is streaming for body in received)
    finally:
        release.set()
        assert service.stop()
        assert peer.stop()
        server.shutdown()
        server.server_close()
        server_thread.join(3)


def test_configured_hooks_and_prompt_middleware_are_not_invoked(runtime):
    from eidolon_cli import plugins
    from eidolon_cli.plugins_manifest import PluginManifest

    calls = []
    def hook(**kwargs):
        calls.append("hook")
        return {"context": "Material fetched outside submitted context"}
    def prompt(info):
        calls.append("prompt")
        return "Unsubmitted profile context"

    plugins.discover_plugins()
    manager = plugins.get_plugin_manager()
    plugin = plugins.PluginContext(PluginManifest(name="organization-boundary-test"), manager)
    registrations = [
        lambda: plugin.register_hook("pre_llm_call", hook),
        lambda: plugin.register_hook("post_llm_call", hook),
        lambda: plugin.register_hook("on_session_end", hook),
        lambda: plugin.register_system_prompt_section("organization-test", prompt),
        lambda: plugin.register_middleware("llm_request", hook),
        lambda: plugin.register_middleware("llm_execution", hook),
    ]
    for register in registrations:
        handle = register()
        try:
            result = executor.execute({"type": "work.draft"}, {}, threading.Event())
            assert "plugin" in result["intervention"]
            assert 'organization-boundary-test' in result['intervention']
            assert len(result['intervention']) <= 2000
            assert not calls and not runtime.instances and not runtime.sent
        finally:
            handle.dispose()
    runtime.config["hooks"] = {"pre_llm_call": [{"command": "echo unsafe-context"}]}
    assert "shell hooks" in executor.execute({"type": "work.draft"}, {}, threading.Event())["intervention"]
    assert not calls and not runtime.instances
    runtime.config.pop("hooks")
    unrelated = plugin.register_hook("pre_tool_call", hook)
    try:
        assert executor.execute({"type": "work.draft"}, {}, threading.Event()) == runtime.output
        assert not calls
    finally:
        unrelated.dispose()
