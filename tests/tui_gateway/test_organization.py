"""Real RPC → durable ledger → scheduler contracts, with deterministic model turns."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from agent.secret_scope import get_secret
from eidolon_constants import get_eidolon_home
from eidolon_cli import organization_service as services
from eidolon_cli.organization_store import OrganizationStore
import tui_gateway.server as server


def _wait(check, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.02)
    raise AssertionError("Organization did not reach the expected state")


def _rpc(method, **params):
    response = server.dispatch({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    assert "error" not in response, response
    return response["result"]


def _deterministic(request, context, cancel):
    kind = request["type"]
    if kind == "request.plan":
        return {"workers": 2, "tasks": [
            {"title": "Draft supplied context", "description": "Use the supplied context", "type": "work.draft"}]}
    if kind == 'request.accept':
        ids = [item['id'] for item in context['evidence']]
        return {'approved': True, 'summary': 'Integrated objective accepted against its criteria.', 'evidenceIds': ids,
                'conflicts': [], 'criteriaResults': [{'criterion': criterion, 'satisfied': True, 'evidenceIds': ids,
                'reason': 'Retained final deliverable satisfies the supplied criterion.'} for criterion in context['objective']['acceptanceCriteria']]}
    if kind == "request.review":
        return {"approved": True, "summary": "Reviewed the persisted text",
                "evidenceIds": [item["id"] for item in context["evidence"]]}
    return {"summary": "Prepared requested draft", "deliverable": "A real deliverable from supplied context"}


@pytest.fixture(autouse=True)
def isolated_services(monkeypatch):
    services.stop_services()
    monkeypatch.setattr(services, "_services", {})
    monkeypatch.setattr(services, "_execute", _deterministic)
    yield
    assert services.stop_services()


def test_rpc_executes_and_reviews_once_without_viewer_connection():
    initial = _rpc("organization.snapshot")
    assert initial["objectives"] == []
    assert not services.get_service().running
    accepted = _rpc("organization.create", title="Write a summary", description="Source facts", idempotencyKey="once")
    duplicate = _rpc("organization.create", title="Write a summary", description="Source facts", idempotencyKey="once")
    assert duplicate["objective"]["id"] == accepted["objective"]["id"]
    # No UI transport is retained. Completion is driven solely by the backend.
    _wait(lambda: services.get_service().store.snapshot()["objectives"][0]["status"] == "completed")
    final = services.get_service().store.snapshot()
    assert len(final["objectives"]) == 1
    assert final["tasks"][0]["review"] == "approved"
    evidence = final["tasks"][0]["evidence"][0]
    full = _rpc("organization.evidence", id=evidence["id"])
    assert 'editProposal' not in full
    assert full["content"] == evidence["content"]
    assert full["sha256"] == evidence["sha256"]
    requests = final["requests"]
    worker = next(r for r in requests if r["type"] == "work.draft")
    assert _rpc('organization.toolReceipts', id=worker['id']) == []
    reviewer = next(r for r in requests if r["type"] == "request.review")
    assert worker["agentId"] != reviewer["agentId"]
    assert all(r["attempts"] == 1 for r in requests)


def test_rpc_rejects_unbound_transport_and_invalid_control_or_profile_payloads():
    req = {"jsonrpc": "2.0", "id": 1, "method": "organization.snapshot", "params": {}}
    assert server.handle_request(req)["error"]["code"] == 4001
    for method, params in [
        ("organization.snapshot", {"path": "/tmp/another-profile.db"}),
        ("organization.snapshot", {"profile": "../../private"}),
        ("organization.snapshot", {"profile": {"path": "/tmp"}}),
        ("organization.toolReceipts", {"id": 'missing', "path": '/tmp/another.db'}),
        ("organization.create", {"title": "x", "idempotencyKey": "k", "status": "completed"}),
        ("organization.create", {"title": "x" * 501, "idempotencyKey": "k"}),
        ("organization.create", {"title": "x", "idempotencyKey": "k", "priority": []}),
    ]:
        response = server.dispatch({**req, "method": method, "params": params})
        assert response["error"]["code"] == -32602, response
    for method in ("organization.complete", "organization.status", "organization.request.finish"):
        assert server.dispatch({**req, "method": method})["error"]["code"] == -32601
    assert not services.state_path().exists()
    response = server.dispatch({**req, 'method': 'organization.toolReceipts', 'params': {'id': 'missing'}})
    assert response['error']['code'] == -32602
    assert not services.get_service().running


def test_profile_route_copies_home_and_secrets_without_mutating_environment(tmp_path, monkeypatch):
    import os
    from eidolon_cli import profiles

    root = tmp_path / "profiles-root"
    root.mkdir()
    monkeypatch.setattr(profiles, "_get_default_eidolon_home", lambda: root)
    homes = {name: root / "profiles" / name for name in ("alpha", "beta", "empty")}
    for name, home in homes.items():
        home.mkdir(parents=True)
        if name != "empty":
            (home / ".env").write_text(f"OPENAI_API_KEY={name}-secret\n", encoding="utf-8")
        (home / "config.yaml").write_text(
            "model:\n  provider: openai-api\n  default: gpt-4.1\n", encoding="utf-8")
    seen = {}
    resolved = {}
    lock = threading.Lock()

    def execute(request, context, cancel):
        name = context["objective"]["title"]
        from eidolon_cli.organization_executor import _runtime_kwargs
        try:
            runtime = _runtime_kwargs(context, 10)
            resolution = runtime.get("api_key")
        except Exception as exc:
            resolution = type(exc).__name__
        with lock:
            seen[name] = (get_eidolon_home().resolve(), get_secret("OPENAI_API_KEY"))
            resolved[name] = resolution
        return {"intervention": "Deterministic stop"}

    monkeypatch.setattr(services, "_execute", execute)
    monkeypatch.setenv("OPENAI_API_KEY", "launch-profile-secret")
    before = dict(os.environ)
    for name, home in homes.items():
        # These rows predate the current UI connection. Selecting a named
        # profile resumes its queued work even outside startup multiplexing.
        OrganizationStore(home / "organization" / "state.db").create_objective(name, idempotency_key="same-key")
        _rpc("organization.snapshot", profile=name)
    _wait(lambda: len(seen) == len(homes))
    for name, home in homes.items():
        assert seen[name] == (home.resolve(), None if name == "empty" else f"{name}-secret")
        if name == "empty":
            assert resolved[name] in {None, "AuthError", "ValueError"}
        else:
            assert resolved[name] == f"{name}-secret"
        snapshot = _rpc("organization.snapshot", profile=name)
        assert [o["title"] for o in snapshot["objectives"]] == [name]
        assert snapshot["runtime"]["profile"] == name
    assert services.stop_services()
    for name, home in homes.items():
        snapshot = _rpc("organization.snapshot", profile=name)
        assert all(r["status"] == "pending_intervention" for r in snapshot["requests"])
        assert not services._services[home.resolve() / "organization" / "state.db"].running
    assert len(seen) == len(homes)
    assert dict(os.environ) == before


def test_web_lifespan_resumes_queued_rows_and_stops_scheduler(monkeypatch):
    from fastapi.testclient import TestClient
    from eidolon_cli import web_server
    from tui_gateway import methods_groups
    from eidolon_cli.local_runtime import bootstrap

    monkeypatch.setattr(web_server, "_warm_gateway_module", lambda: None)
    monkeypatch.setattr(web_server, "_eager_reconcile_own_session_db", lambda: None)
    monkeypatch.setattr(methods_groups, "start_hosted_room_service", lambda: None)
    monkeypatch.setattr(methods_groups, "stop_hosted_room_service", lambda **kw: True)
    monkeypatch.setattr(bootstrap, "ensure_local_runtime", lambda *a, **kw: None)
    monkeypatch.setattr(bootstrap, "shutdown_local_runtime", lambda: None)
    store = OrganizationStore(services.state_path())
    first = store.create_objective("Queued before boot", idempotency_key="boot-1")
    with TestClient(web_server.app):
        service = services.get_service()
        # Web startup recovers organization state on a background thread.
        _wait(lambda: service.running)
        _wait(lambda: store.snapshot()["objectives"][0]["status"] == "completed")
    assert not service.running
    store.create_objective("Queued before restart", idempotency_key="boot-2")
    with TestClient(web_server.app):
        restarted = services.get_service()
        assert restarted is not service
        _wait(lambda: all(o["status"] == "completed" for o in store.snapshot()["objectives"]))
    assert not restarted.running
    assert any(o["id"] == first["id"] for o in store.snapshot()["objectives"])


def test_retry_and_cancel_acknowledgements_are_idempotent(monkeypatch):
    monkeypatch.setattr(services, '_execute', lambda *_: {'intervention': 'Provider unavailable'})
    organization = services.get_service()
    objective = organization.store.create_objective('Draft source facts', idempotency_key='objective')
    claim = organization.store.claim_next()
    organization.store.fail(claim, 'Initial provider failure')
    missing_key = server.dispatch({'jsonrpc': '2.0', 'id': 1, 'method': 'organization.retry', 'params': {'id': claim['id']}})
    assert missing_key['error']['code'] == -32602
    _rpc('organization.retry', id=claim['id'], idempotencyKey='retry-once')
    _wait(lambda: organization.store.snapshot()['requests'][0]['attempts'] == 2
          and organization.store.snapshot()['requests'][0]['status'] == 'pending_intervention')
    replay = _rpc('organization.retry', id=claim['id'], idempotencyKey='retry-once')
    assert replay['requests'][0]['attempts'] == 2
    assert replay['requests'][0]['status'] == 'pending_intervention'
    first = _rpc('organization.cancel', id=objective['id'])
    second = _rpc('organization.cancel', id=objective['id'])
    assert first == second


def test_create_rpc_stopping_service_does_not_admit_or_consume_objective_identity(monkeypatch):
    organization = services.get_service()
    assert organization.stop()
    params = {'title': 'Admit after reconnect', 'idempotencyKey': 'shutdown-admission'}
    with monkeypatch.context() as stopped:
        # A request can already hold this service when shutdown wins its lock.
        stopped.setattr(services, 'get_service', lambda: organization)
        response = server.dispatch({'jsonrpc': '2.0', 'id': 1, 'method': 'organization.create', 'params': params})
    assert response['error']['code'] == 5071 and 'stopping' in response['error']['message']
    assert organization.store.snapshot()['objectives'] == []
    first = _rpc('organization.create', **params)
    duplicate = _rpc('organization.create', **params)
    assert first['objective']['id'] == duplicate['objective']['id']
    _wait(lambda: services.get_service().store.snapshot()['objectives'][0]['status'] == 'completed')
    assert len(services.get_service().store.snapshot()['objectives']) == 1
