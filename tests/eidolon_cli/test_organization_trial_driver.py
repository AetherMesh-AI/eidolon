"""Semantic trial driver boundaries and nonbillable real-executor continuation."""
import os
from pathlib import Path
import socket
import sys

import pytest

from scripts.organization_trial_driver import permitted_owner_response, run_trial, synthetic_provider
from scripts.organization_trial_scenarios import get_scenario


def _exception_shape(error):
    """Metadata only: never format exception messages, locals or provider data."""
    if error is None:
        return {"exception": "unavailable"}
    root = Path(__file__).resolve().parents[2]
    frames, trace = [], error.__traceback__
    while trace is not None and len(frames) < 8:
        path = Path(trace.tb_frame.f_code.co_filename)
        try:
            name = str(path.resolve().relative_to(root))
        except ValueError:
            name = "external"
        frames.append({"file": name[:200], "function": trace.tb_frame.f_code.co_name[:80],
                       "line": trace.tb_lineno})
        trace = trace.tb_next
    code = getattr(error, "sqlite_errorcode", None)
    name = getattr(error, "sqlite_errorname", None)
    return {"exception": type(error).__name__[:80], "module": type(error).__module__[:80],
            "sqliteCode": code if type(code) is int else None,
            "sqliteName": name if isinstance(name, str) and name.startswith("SQLITE_")
                and name.replace("_", "").isalnum() and len(name) <= 80 else None,
            "frames": frames}


def _capture_service_warning(warning, failures):
    def capture_warning(message, *args, **kwargs):
        if message == "Organization execution failed for request %s" and len(failures) < 8:
            try:
                failures.append(_exception_shape(sys.exception()))
            except Exception:
                failures.append({"exception": "diagnostic_capture_failed"})
        return warning(message, *args, **kwargs)
    return capture_warning


def test_diagnostic_capture_failure_preserves_original_warning(monkeypatch):
    calls, failures = [], []
    def broken_shape(error):
        raise ValueError("synthetic-secret-metadata-error")
    monkeypatch.setitem(globals(), "_exception_shape", broken_shape)
    wrapper = _capture_service_warning(lambda *args, **kwargs: calls.append((args, kwargs)), failures)
    try:
        raise RuntimeError("synthetic-private-provider-message")
    except RuntimeError:
        wrapper("Organization execution failed for request %s", "synthetic-request")
    assert failures == [{"exception": "diagnostic_capture_failed"}]
    assert calls == [(("Organization execution failed for request %s", "synthetic-request"), {})]


def test_exception_diagnostics_never_include_error_messages():
    import json
    try:
        raise RuntimeError("synthetic-secret-value-must-not-appear")
    except RuntimeError as error:
        result = _exception_shape(error)
    assert result["exception"] == "RuntimeError"
    assert "synthetic-secret-value" not in json.dumps(result)
    assert all(not row["file"].startswith("/") for row in result["frames"])


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
    from eidolon_cli import organization_service
    failures = []
    warning = organization_service.logger.warning
    monkeypatch.setattr(organization_service.logger, "warning", _capture_service_warning(warning, failures))
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
                or (sys.platform == "win32" and any("POSIX no-follow" in row["reason"] for row in pending))), {
                    "serviceExceptions": failures,
                    "pending": [{"type": row.get("type"), "status": row.get("status"),
                        "unexpectedExecutionFailure": row.get("reason") ==
                            "Execution failed unexpectedly; review provider configuration before retrying"}
                        for row in pending[:8]],
                    "projectRunCount": len(result["projectRuns"]),
                    "taskSuccess": result["taskSuccess"],
                }
        assert not result["coverage"]["recovery"]
        assert result["delivered_commit"] is None
        assert result["sourceIntegration"] is None
        assert not any(row["action"] == "request_replan" for row in result["ownerActions"])
