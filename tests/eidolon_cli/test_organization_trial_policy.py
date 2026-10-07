"""Trial admission is free, explicit, and uses durable physical-send reservations."""
from decimal import Decimal, ROUND_CEILING
import hashlib
import json
import sqlite3

import pytest

from scripts import eval_organization_project as evaluation
from scripts import organization_trial_policy as policy


def selected_config():
    return {"provider": "custom", "model": "selected-trial-model",
            "base_url": "https://provider.example.invalid/v1", "api_mode": "chat_completions",
            "key_env": "SELECTED_TRIAL_API_KEY", "model_costs": [{"provider": "custom",
                "model": "selected-trial-model", "input_usd_per_million": "2",
                "output_usd_per_million": "8"}]}


def live_arguments(path, cap="1"):
    return ["--live-model", "--acknowledge-billing", "--provider-config", str(path), "--max-cost-usd", cap]


_DELETE = object()


@pytest.mark.parametrize("path,value,cap", [
    (("model_costs",), _DELETE, "1"),
    (("model_costs",), [], "1"),
    (("model_costs", 0, "input_usd_per_million"), _DELETE, "1"),
    (("model_costs", 0, "output_usd_per_million"), _DELETE, "1"),
    (("model_costs", 0, "input_usd_per_million"), None, "1"),
    (("model_costs", 0, "output_usd_per_million"), None, "1"),
    (("model_costs", 0, "model"), "unselected-model", "1"),
    (("model_costs", 0, "provider"), "unselected-provider", "1"),
    (("model_costs", 0, "model"), "*", "1"),
    (("model_costs",), selected_config()["model_costs"] * 2, "1"),
    (("model_costs", 0, "input_usd_per_million"), "-1", "1"),
    (("model_costs", 0, "output_usd_per_million"), "NaN", "1"),
    ((), None, "0"),
    ((), None, "Infinity"),
    (("base_url",), "http://provider.example.invalid/v1", "1"),
    (("api_key",), "must-not-be-read", "1"),
], ids=["missing_prices", "no_prices", "missing_input", "missing_output", "unknown_input",
        "unknown_output", "wrong_model", "wrong_provider", "wildcard", "duplicate_route",
        "negative_input", "nonfinite_output", "zero_cap", "nonfinite_cap", "http", "inline_secret"])
def test_live_policy_rejects_unknown_or_inexact_costs_before_credentials_or_providers(tmp_path, monkeypatch, path, value, cap):
    from agent import secret_scope

    def forbidden(*_args, **_kwargs):
        pytest.fail("Rejected live configuration accessed a credential or started execution")

    monkeypatch.setattr(secret_scope, "get_secret", forbidden)
    monkeypatch.setattr(evaluation, "native_preflight", forbidden)
    monkeypatch.setattr(evaluation, "run_evaluation", forbidden)
    raw = selected_config()
    if path:
        target = raw
        for part in path[:-1]:
            target = target[part]
        if value is _DELETE:
            target.pop(path[-1])
        else:
            target[path[-1]] = value
    config_path = tmp_path / "provider.json"
    config_path.write_text(json.dumps(raw))
    with pytest.raises(SystemExit) as error:
        evaluation.main(live_arguments(config_path, cap))
    assert error.value.code == 2


@pytest.mark.parametrize("live", [False, True])
def test_unavailable_native_preflight_blocks_all_provider_and_credential_activity(tmp_path, monkeypatch, capsys, live):
    from agent import secret_scope
    from eidolon_cli import organization_project_runner as runner

    observed = []

    def unsupported(files, grant, cancel=None):
        assert len(files) == 1
        snapshot = files[0]
        assert snapshot["sha256"] == hashlib.sha256(snapshot["content"].encode()).hexdigest()
        assert snapshot["path"].endswith(".py") and len(snapshot["content"]) < 1000
        assert runner.normalize_execution_grant(grant).recipe == "python_unittest"
        observed.append(files)
        return {"status": "unsupported", "exitCode": None, "testCount": 0, "skippedCount": 0,
                "isolation": {"established": False}, "reason": "Native sandbox unavailable in this synthetic test"}

    def forbidden(*_args, **_kwargs):
        pytest.fail("Unsupported preflight must stop before any credential/provider activity")

    monkeypatch.setattr(runner, "run_project_tests", unsupported)
    monkeypatch.setattr(secret_scope, "get_secret", forbidden)
    monkeypatch.setattr(evaluation, "local_provider", forbidden)
    monkeypatch.setattr(evaluation, "run_evaluation", forbidden)
    path = tmp_path / "provider.json"
    path.write_text(json.dumps(selected_config()))
    assert evaluation.main(live_arguments(path) if live else []) == 2
    report = json.loads(capsys.readouterr().out)
    assert observed and report["outcome"] == "unsupported"
    assert report["providerCalls"] == report["preflight"]["providerCalls"] == 0
    assert report["preflight"]["credentialAccessed"] is False
    assert report["objectiveStatus"] == "not_started" and not report["executionStarted"]
    assert not report["fixtureStages"] and report["finalDeliverable"] is None
    with pytest.raises(ValueError, match="preflight"):
        policy.load_live_secret(selected_config(), report["preflight"])


def test_preflight_only_success_is_not_a_model_evaluation_and_explicit_zero_rates_are_known(tmp_path, monkeypatch, capsys):
    from eidolon_cli import organization_project_runner as runner
    from agent import secret_scope

    def forbidden(*_args, **_kwargs):
        pytest.fail("Preflight-only must never access keys or evaluate a model")

    monkeypatch.setattr(secret_scope, "get_secret", forbidden)
    monkeypatch.setattr(evaluation, "run_evaluation", forbidden)
    monkeypatch.setattr(evaluation, "local_provider", forbidden)
    monkeypatch.setattr(runner, "run_project_tests", lambda *_args, **_kwargs: {
        "status": "passed", "exitCode": 0, "testCount": 1, "skippedCount": 0,
        "isolation": {"established": True}})
    raw = selected_config()
    raw["model_costs"][0].update(input_usd_per_million="0", output_usd_per_million="0")
    path = tmp_path / "provider.json"
    path.write_text(json.dumps(raw))
    arguments = live_arguments(path)
    route = policy.selected_live_route(evaluation.parser().parse_args(arguments))
    budget = policy.budget_configuration(route)
    assert budget["model_costs"] == raw["model_costs"] and Decimal(budget["max_cost_usd"]) > 0
    assert evaluation.main([*arguments, "--preflight"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["mode"] == "sandbox_preflight" and not report["checks"]["objectiveAccepted"]
    assert report["providerCalls"] == 0 and report["finalDeliverable"] is None
    assert "not an invoice guarantee" in report["limitations"]
    with pytest.raises(ValueError, match="no unlimited fallback"):
        policy.budget_configuration({"model": route["model"], "base_url": route["base_url"]})


@pytest.mark.parametrize("cap", ["0.000000001", "1"])
def test_live_cost_reservations_cover_input_output_and_actual_loopback_retries(tmp_path, monkeypatch, cap):
    # Real organization execution, OpenAI SDK and HTTP transport, but a purely
    # synthetic loopback provider. The first physical send is a retryable 500.
    physical_sends = []
    make_server = evaluation.ThreadingHTTPServer

    def retry_once(address, handler):
        original_post = handler.do_POST

        def post(self):
            physical_sends.append(self.path)
            if len(physical_sends) == 1:
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"Synthetic retry","type":"server_error"}}')
            else:
                original_post(self)

        handler.do_POST = post
        return make_server(address, handler)

    monkeypatch.setattr(evaluation, "ThreadingHTTPServer", retry_once)
    with evaluation.local_provider() as (route, stages, errors):
        route.update(max_cost_usd=cap, model_costs=[{"provider": "custom", "model": route["model"],
            "input_usd_per_million": "2", "output_usd_per_million": "8"}])
        report = evaluation.run_evaluation(tmp_path, route)
    with sqlite3.connect(tmp_path / "profile" / "organization" / "state.db") as connection:
        calls = connection.execute("SELECT input_limit,output_limit,reserved_cost_usd FROM organization_model_calls").fetchall()
    assert report["usage"]["modelCalls"] == len(calls) == len(physical_sends)
    reserved = Decimal(0)
    for input_limit, output_limit, cost in calls:
        assert input_limit > 0 and output_limit == evaluation.LIMITS["max_output_tokens"]
        expected = ((input_limit * Decimal(2) + output_limit * Decimal(8)) / 1_000_000).quantize(
            Decimal("0.000000001"), rounding=ROUND_CEILING)
        assert Decimal(cost) == expected
        reserved += expected
    assert Decimal(report["usage"]["configuredCostReservedUsd"]) == reserved <= Decimal(cap)
    if cap == "1":
        assert len(physical_sends) > 1 and len(physical_sends) == len(stages) + 1 and not errors
    else:
        assert not physical_sends and report["objectiveStatus"] == "needs_input"
        assert any("configured-cost budget" in reason for reason in report["interventions"])
