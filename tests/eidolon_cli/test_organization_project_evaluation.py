"""Opt-in billing and exact, nonbillable real-pipeline fixture evaluation."""
import json
import logging
import os
from pathlib import Path
import socket
from types import SimpleNamespace

import pytest

from scripts import eval_organization_project as evaluation
from scripts import organization_trial_policy as policy


@pytest.mark.parametrize("arguments", [
    ["--live-model"], ["--acknowledge-billing"],
    ["--provider-config", "not-read.json"],
    ["--live-model", "--provider-config", "not-read.json"],
    ["--live-model", "--acknowledge-billing"],
    ["--live-model", "--acknowledge-billing", "--provider-config", "not-read.json"],
    ["--max-cost-usd", "1"],
])
def test_billable_route_requires_all_explicit_choices(arguments, monkeypatch):
    monkeypatch.setattr(Path, "read_text", lambda *_args, **_kwargs: pytest.fail("Opt-in checked too late"))
    monkeypatch.setattr(evaluation, "run_evaluation", lambda *_args, **_kwargs: pytest.fail("Evaluation started without opt-in"))
    with pytest.raises(SystemExit) as error:
        evaluation.main(arguments)
    assert error.value.code == 2


def test_explicit_live_route_uses_only_current_selected_secret_and_disposable_config(tmp_path, monkeypatch):
    from agent.secret_scope import (get_secret, reset_secret_scope, reset_secret_scope_required,
                                   set_secret_scope, set_secret_scope_required)
    from eidolon_constants import get_eidolon_home
    from eidolon_cli.runtime_provider import resolve_runtime_provider

    selected = {"provider": "custom", "model": "deliberately-selected-model",
                "base_url": "https://provider.example.invalid/v1", "api_mode": "chat_completions",
                "key_env": "SELECTED_API_KEY", "model_costs": [{"provider": "custom",
                    "model": "deliberately-selected-model", "input_usd_per_million": "2",
                    "output_usd_per_million": "8"}]}
    path = tmp_path / "provider.json"
    path.write_text(json.dumps(selected))
    args = evaluation.parser().parse_args(["--live-model", "--acknowledge-billing", "--provider-config", str(path),
                                         "--max-cost-usd", "0.5"])
    admitted = {"outcome": "passed", "execution": {"status": "passed", "isolation": {"established": True}}}
    monkeypatch.setenv("SELECTED_API_KEY", "wrong-global-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "unselected-global-secret")
    monkeypatch.setenv("NO_PROXY", "live-owner.example")
    monkeypatch.setenv("no_proxy", "live-owner.example")
    scope = set_secret_scope({"SELECTED_API_KEY": "selected-scoped-secret", "OTHER_API_KEY": "unselected-scoped-secret"})
    required = set_secret_scope_required(True)
    original_home = get_eidolon_home()
    try:
        route = policy.load_live_secret(policy.selected_live_route(args), admitted)
        assert route["secret"] == "selected-scoped-secret"
        home, source = tmp_path / "disposable-profile", tmp_path / "disposable-source"
        home.mkdir()
        source.mkdir()
        cfg = evaluation.configuration(source, route)
        assert cfg["organization"]["max_cost_usd"] == "0.5"
        assert cfg["organization"]["model_costs"] == selected["model_costs"]
        (home / "config.yaml").write_text(json.dumps(cfg))
        assert "secret" not in (home / "config.yaml").read_text()
        with evaluation.isolated_profile(home, route["secret"]):
            assert os.environ["NO_PROXY"] == os.environ["no_proxy"] == "live-owner.example"
            assert get_secret("OPENAI_API_KEY") is None
            assert get_secret("OTHER_API_KEY") is None
            runtime = resolve_runtime_provider(requested="custom:evaluation", target_model=route["model"])
            assert runtime["api_key"] == "selected-scoped-secret"
            assert runtime["base_url"] == selected["base_url"]
            assert runtime["model"] == selected["model"]
            assert runtime["api_mode"] == "chat_completions"
            assert runtime.get("credential_pool") is None
        assert get_eidolon_home() == original_home
        assert get_secret("OTHER_API_KEY") == "unselected-scoped-secret"
        assert not (home / ".env").exists()
        missing = set_secret_scope({})
        try:
            with pytest.raises(ValueError, match="unavailable"):
                policy.load_live_secret(policy.selected_live_route(args), admitted)
        finally:
            reset_secret_scope(missing)
        for changed in ({"api_key": "must-not-be-copied"}, {"provider": "auto"},
                        {"base_url": "http://provider.example.invalid/v1"},
                        {"base_url": "https://key@provider.example.invalid/v1"},
                        {"base_url": "https://provider.example.invalid/v1?key=secret"}):
            path.write_text(json.dumps({**selected, **changed}))
            with pytest.raises(ValueError):
                policy.selected_live_route(args)
    finally:
        reset_secret_scope_required(required)
        reset_secret_scope(scope)


@pytest.mark.parametrize("symlinked_parent", [
    pytest.param(False, id="plain_temp"),
    pytest.param(True, id="linux_symlink_temp", marks=pytest.mark.linux_only),
    pytest.param(True, id="macos_symlink_temp", marks=pytest.mark.macos_only),
])
def test_default_evaluation_runs_real_local_pipeline_without_credentials_or_external_network(tmp_path, monkeypatch, capsys, symlinked_parent):
    if symlinked_parent:
        real_parent, alias_parent = tmp_path / "real-temp-parent", tmp_path / "alias-temp-parent"
        real_parent.mkdir()
        alias_parent.symlink_to(real_parent.resolve(), target_is_directory=True)
        temporary_directory = evaluation.tempfile.TemporaryDirectory
        # Change only this entrypoint's factory, not the product runner's temp paths.
        monkeypatch.setattr(evaluation, "tempfile", SimpleNamespace(
            TemporaryDirectory=lambda **kwargs: temporary_directory(dir=alias_parent, **kwargs)))
    monkeypatch.setenv("OPENAI_API_KEY", "must-never-be-used")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://must-never-be-contacted.invalid")
    # A TEST-NET proxy would receive loopback requests without the scoped bypass.
    # The socket guard below rejects it before any real external connection.
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(key, "http://198.51.100.23:9")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    connect = socket.socket.connect
    connections = []

    def loopback_only(sock, address):
        assert isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}, address
        connections.append(address)
        return connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", loopback_only)
    run = evaluation.run_evaluation
    temporary_roots = []

    def inspect_disposable_files(root, route, **kwargs):
        report = run(root, route, **kwargs)
        temporary_roots.append(root)
        import eidolon_logging as logs
        assert not any(getattr(handler, "baseFilename", None)
                       and Path(handler.baseFilename).resolve().is_relative_to(root)
                       for handler in logs._queued_file_handlers)
        assert not (root / "profile" / ".env").exists()
        for file in root.rglob("*"):
            if file.is_file():
                assert route["secret"].encode() not in file.read_bytes(), file
        return report

    monkeypatch.setattr(evaluation, "run_evaluation", inspect_disposable_files)
    report_path = tmp_path / "evaluation.json"
    code = evaluation.main(["--report", str(report_path)])
    assert os.environ["NO_PROXY"] == os.environ["no_proxy"] == ""
    assert os.environ["HTTPS_PROXY"] == "http://198.51.100.23:9"
    report = json.loads(report_path.read_text())
    assert json.loads(capsys.readouterr().out) == report
    assert all(not root.exists() for root in temporary_roots)
    assert report["mode"] == "deterministic_local_fixture"
    assert report["fixtureErrors"] == []
    if report["preflight"]["outcome"] != "passed":
        assert not temporary_roots and not connections and not report["fixtureStages"]
        assert report["billing"] == "no_provider_calls" and report["providerCalls"] == 0
        assert report["projectExecution"] is None
    else:
        assert temporary_roots and connections and report["billing"] == "nonbillable_loopback_only"
        assert "work.inspect" in report["fixtureStages"] and "work.edit" in report["fixtureStages"], report["interventions"]
        assert "request.review" in report["fixtureStages"]
    assert report["checks"]["originalSourceAndIndexUnchanged"]
    assert report["checks"]["serviceStopped"] and report["checks"]["noRemotePush"]
    assert "must-never-be-used" not in report_path.read_text()
    if report["outcome"] == "passed":
        assert code == 0 and all(report["checks"].values())
        assert report["deliveredFiles"] == {"app.py": evaluation.AFTER, "test_app.py": evaluation.TESTS}
        assert report["finalDeliverable"] and report["testReview"]["approved"]
    else:
        assert code == 2 and report["outcome"] == "unsupported"
        assert report["objectiveStatus"] == "not_started"
        assert report["sourceIntegration"] is None and report["deliveredFiles"] is None
        assert report["testReview"] is None and report["finalDeliverable"] is None
        assert not report["checks"]["objectiveAccepted"]
        assert not report["checks"]["isolatedTestsPassed"]
        assert "request.test_review" not in report["fixtureStages"]
        assert "request.integrate" not in report["fixtureStages"]


def test_disposable_profile_log_cleanup_preserves_callers_logging(tmp_path):
    import eidolon_logging as logs

    profile = tmp_path / "disposable"
    profile.mkdir()
    caller = logging.FileHandler(tmp_path / "caller.log")
    owned = logging.FileHandler(profile / "evaluation.log")
    logs._register_queued_handler(caller)
    logs._register_queued_handler(owned)
    try:
        logging.getLogger("evaluation-test").warning("Flush before disposing")
        evaluation.close_profile_logs(profile)
        assert owned.stream is None and owned not in logs._queued_file_handlers
        assert caller in logs._queued_file_handlers and caller.stream is not None
        logging.getLogger("evaluation-test").warning("Caller logging still works")
        logs.flush_log_queue()
        assert "Flush before disposing" in (profile / "evaluation.log").read_text()
        assert "Caller logging still works" in (tmp_path / "caller.log").read_text()
    finally:
        evaluation.close_profile_logs(tmp_path)
