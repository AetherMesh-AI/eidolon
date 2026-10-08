"""Occupied worker slots, lease renewal and live-execution retry fencing."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import threading
import time

import pytest

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_service import OrganizationService
from eidolon_cli.organization_store import OrganizationStore


def _wait(check, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.01)
    raise AssertionError("Scheduler did not reach expected state")


def test_cancel_renews_lease_and_keeps_slot_until_executor_really_exits(tmp_path, monkeypatch):
    # Native Windows SQLite/filesystem work can exceed a subsecond lease.
    # Wait for the actual heartbeat rather than requiring a quiet host.
    settings = replace(OrganizationSettings(), max_inflight=1, lease_seconds=10, timeout_seconds=60)
    store = OrganizationStore(tmp_path / "organization" / "state.db", settings)
    first = store.create_objective("First", idempotency_key="first")
    store.create_objective("Second", idempotency_key="second")
    entered, release, renewed, cancelled = [threading.Event() for _ in range(4)]
    calls = []
    heartbeat = store.heartbeat

    def renewal(claim):
        result = heartbeat(claim)
        if result:
            renewed.set()
        return result

    monkeypatch.setattr(store, "heartbeat", renewal)

    def execute(request, context, cancel):
        calls.append(context["objective"]["title"])
        if len(calls) == 1:
            entered.set()
            assert cancel.wait(30)
            cancelled.set()
            assert release.wait(30)
        return {"intervention": "Stop after deterministic execution"}

    service = OrganizationService(store, home=tmp_path, settings=settings, executor=execute, poll_seconds=0.01)
    expanded = replace(settings, max_inflight=2)
    peer = OrganizationService(OrganizationStore(store.path, expanded), home=tmp_path,
                               settings=expanded, executor=execute, poll_seconds=0.01)
    try:
        service.start()
        assert entered.wait(30)
        assert renewed.wait(30)
        assert service.cancel(first["id"])
        assert cancelled.wait(30)
        with service._lock:
            assert len(service._running) == 1
            assert calls == ["First"]
            assert any(r["status"] == "queued" for r in store.snapshot()["requests"])
        # Another backend's own local slot count is empty, but the shared OS
        # capacity still includes the cancelled-yet-live execution.
        capacity_peer = OrganizationService(store, home=tmp_path, settings=settings, executor=execute)
        capacity_peer._fill_slots()
        assert not capacity_peer._running
        assert calls == ["First"]
        # Even a backend allowed a second global capacity slot cannot reuse
        # the same logical manager while its cancelled call still lives.
        peer.start()
        _wait(lambda: any(r["status"] == "queued" and r["reason"]
                          for r in store.snapshot()["requests"]), timeout=30)
        blocked = next(r for r in store.snapshot()["requests"] if r["status"] == "queued")
        assert "Assigned agent is still stopping" in blocked["reason"]
        assert blocked["attempts"] == 0
        assert calls == ["First"]
        release.set()
        _wait(lambda: len(calls) == 2, timeout=30)
        assert store.snapshot()["objectives"][1]["status"] == "cancelled"
    finally:
        release.set()
        assert service.stop()
        assert peer.stop()


def test_timeout_survives_shutdown_and_blocks_other_service_from_replaying_live_call(tmp_path):
    settings = replace(OrganizationSettings(), max_inflight=1, timeout_seconds=0.15, lease_seconds=1)
    store = OrganizationStore(tmp_path / "organization" / "state.db", settings)
    store.create_objective("Uncertain request", idempotency_key="once")
    entered, interrupted, release = [threading.Event() for _ in range(3)]
    calls = []

    def execute(request, context, cancel):
        calls.append(request["id"])
        entered.set()
        assert cancel.wait(5)
        interrupted.set()
        assert release.wait(5)
        return {"tasks": [{"title": "Late result", "description": "Must never commit", "type": "work.draft"}]}

    first = OrganizationService(store, home=tmp_path, settings=settings, executor=execute, poll_seconds=0.01)
    # A second process with a newly increased concurrency setting can acquire
    # another capacity slot, but still cannot replay this particular live call.
    expanded = replace(settings, max_inflight=2)
    second = OrganizationService(OrganizationStore(store.path, expanded), home=tmp_path,
                                 settings=expanded, executor=execute, poll_seconds=0.01)
    try:
        first.start()
        assert entered.wait(5)
        assert interrupted.wait(5)
        _wait(lambda: store.snapshot()["requests"][0]["status"] == "pending_intervention")
        request_id = calls[0]
        assert first.stop(timeout=0.01) is False
        with pytest.raises(ValueError, match="still stopping"):
            first.retry(request_id)
        # A separate backend instance can see/retry the durable row, but its
        # OS execution fence still refuses to duplicate the live provider call.
        assert second.retry(request_id)
        _wait(lambda: store.snapshot()["requests"][0]["status"] == "queued"
              and "still active" in (store.snapshot()["requests"][0]["reason"] or ""))
        assert calls == [request_id]
        assert store.snapshot()["requests"][0]["attempts"] == 1
        assert second.stop()
        release.set()
        _wait(lambda: not first.running)
        assert store.snapshot()["tasks"] == []
    finally:
        release.set()
        assert first.stop()
        assert second.stop()


def test_startup_recovers_only_configured_profiles_with_their_own_secrets(tmp_path, monkeypatch):
    from agent.secret_scope import get_secret, current_secret_scope, reset_secret_scope, set_secret_scope
    from eidolon_constants import get_eidolon_home
    from eidolon_cli import organization_service as services, profiles

    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setattr(profiles, "_get_default_eidolon_home", lambda: root)
    monkeypatch.setattr(services, "_services", {})
    (root / "config.yaml").write_text(
        "gateway:\n  multiplex_profiles: true\n  multiplex_profile_allowlist: [alpha]\n", encoding="utf-8")
    homes = {name: root / "profiles" / name for name in ("alpha", "beta")}
    for name, home in homes.items():
        home.mkdir(parents=True)
        (home / ".env").write_text(f"OPENAI_API_KEY={name}-credential\n", encoding="utf-8")
        OrganizationStore(home / "organization" / "state.db").create_objective(name, idempotency_key=name)
    seen = []

    def execute(request, context, cancel):
        seen.append((get_eidolon_home().resolve(), get_secret("OPENAI_API_KEY")))
        return {"intervention": "Deterministic startup stop"}

    monkeypatch.setattr(services, "_execute", execute)
    token = set_secret_scope({"OPENAI_API_KEY": "caller-credential"})
    try:
        started = services.start_existing_services()
        assert len(started) == 1
        _wait(lambda: bool(seen))
        assert seen == [(homes["alpha"].resolve(), "alpha-credential")]
        assert get_eidolon_home().resolve() == root.resolve()
        assert current_secret_scope() == {"OPENAI_API_KEY": "caller-credential"}
        beta = OrganizationStore(homes["beta"] / "organization" / "state.db").snapshot()
        assert beta["requests"][0]["status"] == "queued"
        assert not (root / "organization" / "state.db").exists()
    finally:
        reset_secret_scope(token)
        assert services.stop_services()
