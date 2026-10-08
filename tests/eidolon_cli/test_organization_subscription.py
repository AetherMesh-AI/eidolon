"""Subscription diagnostics must never become credential or execution discovery."""
import builtins
import io
import socket
import subprocess

import pytest

from eidolon_cli import organization_executor as executor
from eidolon_cli import organization_subscription as subscription


def test_readiness_is_inert_and_does_not_price_subscription_usage_as_zero(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Readiness attempted file, network or process access")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(io, "open", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    result = subscription.subscription_readiness()
    assert result["status"] == "blocked"
    assert result["providerCalls"] == 0 and result["credentialAccessed"] is False
    assert all(route["status"] == "blocked" and route["reason"] for route in result["routes"])
    usage = result["usage"]
    assert usage["category"] == "subscription_allowance"
    assert usage["remaining"] is None and usage["costUsd"] is None
    assert usage["hardOutputTokenCeilingSupported"] is False
    # A caller cannot mutate later readiness results into an enabled route.
    result["routes"][0]["status"] = "ready"
    assert subscription.subscription_readiness()["routes"][0]["status"] == "blocked"


def test_app_server_rejected_at_admission_and_execution_even_with_sandbox_claims():
    for controls in ({}, {"sandbox": "read-only", "approval_policy": "never"},
                     {"tools": [], "features": {"shell_tool": False}}):
        with pytest.raises(executor.OrganizationExecutionError) as rejected:
            executor._guard_route({"provider": "openai-codex", "api_mode": "codex_app_server",
                                   "request_overrides": controls}, "selected-model")
        assert str(rejected.value) == subscription.APP_SERVER_BLOCKER
    boundary = executor._ToolFreeBoundary()
    with pytest.raises(executor.OrganizationExecutionError) as rejected:
        boundary._run_codex_app_server_turn()
    assert str(rejected.value) == boundary._organization_intervention == subscription.APP_SERVER_BLOCKER
    # The diagnostic does not disable existing bounded direct API admission.
    executor._guard_route({"provider": "custom", "api_mode": "chat_completions"}, "selected-model")
