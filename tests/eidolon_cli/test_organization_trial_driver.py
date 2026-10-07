"""Semantic trial driver boundaries and nonbillable real-executor continuation."""
import os
import socket
import sys

import pytest

from scripts.organization_trial_driver import permitted_owner_response, run_trial, synthetic_provider
from scripts.organization_trial_scenarios import get_scenario


def test_owner_driver_only_answers_predeclared_question_or_genuine_failed_test():
    scenario = get_scenario("duplicate_retention", retention="latest")
    question = {"id": "question", "type": "request.question", "status": "pending_intervention",
                "requestedOutcome": "Which duplicate record should we retain?", "reason": "No eligible handler",
                "allowedResolutions": [{"action": "answer_request"}]}
    expected = {"action": "answer_request", "text": scenario.owner_answer}
    assert permitted_owner_response(scenario, question, {}, []) == expected
    for wording in ("Which row wins when an ID repeats?", "Should I keep the first or last?",
                    "For repeated identifiers, which occurrence should survive?"):
        assert permitted_owner_response(scenario, {**question, "requestedOutcome": wording}, {}, []) == expected
    assert permitted_owner_response(scenario, question, {}, [expected]) is None
    assert permitted_owner_response(scenario, {**question, "type": "request.permission",
           "allowedResolutions": [{"action": "approve_request"}]}, {}, []) is None
    assert permitted_owner_response(scenario, {**question, "requestedOutcome": "May I publish externally?"}, {}, []) is None
    generic = {**question, "type": "request.plan", "allowedResolutions": [{"action": "provide_input"}]}
    assert permitted_owner_response(scenario, generic, {}, [])["action"] == "provide_input"
    failed = {"id": "failed", "type": "request.project_failed", "status": "pending_intervention",
              "allowedResolutions": [{"action": "request_replan"}]}
    receipt = {"status": "failed", "exitCode": 1, "testCount": 5, "isolation": {"established": True}}
    objective = {"projectExecution": {"receipt": receipt}}
    replan = permitted_owner_response(scenario, failed, objective, [])
    assert replan["action"] == "request_replan"
    assert permitted_owner_response(scenario, failed, objective, [replan]) is None
    for change in ({"status": "unsupported"}, {"exitCode": 0}, {"testCount": 0}, {"isolation": {"established": False}}):
        assert permitted_owner_response(scenario, failed, {"projectExecution": {"receipt": {**receipt, **change}}}, []) is None
    assert permitted_owner_response(scenario, {**failed, "allowedResolutions": []}, objective, []) is None


@pytest.mark.parametrize("scenario_id,retention,clarification_mode,first_try", [
    ("duplicate_retention", "earliest", "typed", False),
    ("duplicate_retention", "latest", "typed", False),
    ("duplicate_retention", "latest", "generic", False),
    ("signed_bucket", "earliest", "typed", False),
    ("signed_bucket", "earliest", "typed", True),
])
def test_synthetic_trial_uses_real_executor_preserves_history_and_distinguishes_recovery(
        tmp_path, monkeypatch, scenario_id, retention, clarification_mode, first_try):
    connect = socket.socket.connect

    def loopback_only(sock, address):
        assert isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}, address
        return connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", loopback_only)
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-used")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://must-not-be-contacted.invalid")
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(key, "http://198.51.100.23:9")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    scenario = get_scenario(scenario_id, retention=retention)
    with synthetic_provider(scenario, clarification_mode=clarification_mode, repair_first_try=first_try) as (route, diagnostics):
        result = run_trial(tmp_path, route, scenario)
    assert diagnostics["errors"] == [], diagnostics
    assert diagnostics["calls"] and result["mode"] == "synthetic_runtime_trial"
    assert result["checks"]["originalSourceAndIndexUnchanged"]
    assert result["checks"]["serviceStopped"] and result["checks"]["noRemotePush"]
    assert result["checks"]["ownerReceiptReplayIdempotent"]
    assert result["usage"]["modelCalls"] <= result["limits"]["max_model_calls"]
    assert len(result["projectRuns"]) <= result["limits"]["max_project_runs"]
    assert len(result["ownerActions"]) <= result["limits"]["max_owner_resolutions"]
    assert os.environ["NO_PROXY"] == os.environ["no_proxy"] == ""
    if scenario_id == "duplicate_retention":
        assert result["coverage"]["clarification"]
        assert result["restart"]["exercised"] and result["restart"]["sameProfile"]
        assert result["restart"]["receiptsPreserved"]
        assert result["ownerActions"][0]["text"] == scenario.owner_answer
    if result["taskSuccess"]:
        assert all(result["checks"].values())
        assert result["delivered_commit"] != result["original_commit"]
        assert result["deliveredFiles"]["test_app.py"] == scenario.public_test_files["test_app.py"]
        assert result["coverage"]["recovery"] == (scenario_id == "signed_bucket" and not first_try)
        assert result["coverage"]["restart"] == (scenario_id == "duplicate_retention" or not first_try)
        if result["coverage"]["recovery"]:
            assert [run["projectExecution"]["status"] for run in result["projectRuns"]] == ["failed", "passed"]
            assert result["ownerActions"][0]["action"] == "request_replan"
            assert result["restart"]["checkpoint"]["projectReceipts"]
    else:
        pending = [row for row in result["snapshot"]["requests"] if row["status"] == "pending_intervention"]
        assert pending
        # Native isolation is fail-closed. An unsupported host is never a
        # successful trial and must never trigger the failure-repair shortcut.
        assert ((result["projectRuns"] and result["projectRuns"][-1]["projectExecution"]["status"] == "unsupported")
                or (sys.platform == "win32" and any("POSIX no-follow" in row["reason"] for row in pending))), result
        assert not result["coverage"]["recovery"]
        assert result["delivered_commit"] is None
        assert result["sourceIntegration"] is None
        assert not any(row["action"] == "request_replan" for row in result["ownerActions"])
