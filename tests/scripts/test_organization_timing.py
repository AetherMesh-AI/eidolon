"""Immediate bounded CI failure evidence survives a missing pytest summary."""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
import yaml

from scripts.ci import organization_timing as timing


def report(outcome="failed", when="call", text="AssertionError: conserved tokens differ"):
    return SimpleNamespace(when=when, duration=1.25, outcome=outcome,
                           nodeid="test_conservation.py::test_tokens",
                           failed=outcome == "failed", longreprtext=text)


@pytest.mark.parametrize("phase", ["setup", "call", "teardown"])
def test_failed_phase_is_persisted_immediately(tmp_path, monkeypatch, capsys, phase):
    monkeypatch.setattr(timing, "_output_directory", tmp_path)
    timing.pytest_runtest_logreport(report(when=phase))
    path = tmp_path / f"organization-timing-{os.getpid()}.jsonl"
    value = json.loads(path.read_text(encoding="utf-8"))
    assert value["event"] == phase and value["outcome"] == "failed"
    assert value["failureText"] == "AssertionError: conserved tokens differ"
    assert value["failureTruncated"] is False
    assert value["failureOriginalBytes"] == len(value["failureText"].encode("utf-8"))
    assert value["duration"] == 1.25 and value["time"] > 0
    assert json.loads(capsys.readouterr().out.removeprefix("ATTENTION_TIMING ")) == value


@pytest.mark.parametrize("outcome", ["passed", "skipped"])
def test_nonfailure_does_not_format_traceback(tmp_path, monkeypatch, outcome):
    monkeypatch.setattr(timing, "_output_directory", tmp_path)
    value = report(outcome=outcome)
    del value.longreprtext
    timing.pytest_runtest_logreport(value)
    record = json.loads(next(tmp_path.glob("*.jsonl")).read_text())
    assert record["outcome"] == outcome
    assert not any(key.startswith("failure") for key in record)


@pytest.mark.parametrize("body", ["x", "\u754c\U0001f9ea"])
def test_large_failure_keeps_origin_and_exception_with_byte_bound(body):
    text = "TRACEBACK ORIGIN\n" + body * timing._MAX_FAILURE_BYTES + "\nAssertionError: tokens differ"
    value = timing._failure_details(report(text=text))
    assert value["failureTruncated"] is True
    assert value["failureOriginalBytes"] == len(text.encode("utf-8"))
    assert len(value["failureText"].encode("utf-8")) <= timing._MAX_FAILURE_BYTES
    assert value["failureText"].startswith("TRACEBACK ORIGIN\n")
    assert value["failureText"].endswith("AssertionError: tokens differ")
    assert "failure traceback truncated" in value["failureText"]
    assert "\ufffd" not in value["failureText"]


def test_exact_failure_byte_bound_is_not_truncated():
    text = "x" * timing._MAX_FAILURE_BYTES
    value = timing._failure_details(report(text=text))
    assert value["failureText"] == text
    assert value["failureTruncated"] is False


def test_record_reaches_disk_before_stdout_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(timing, "_output_directory", tmp_path)

    def broken_stdout(*args, **kwargs):
        raise BrokenPipeError("diagnostic output closed")

    monkeypatch.setattr("builtins.print", broken_stdout)
    with pytest.raises(BrokenPipeError):
        timing.pytest_runtest_logreport(report())
    record = json.loads(next(tmp_path.glob("*.jsonl")).read_text())
    assert record["failureText"] == report().longreprtext


def test_record_survives_process_exit_without_terminal_summary(tmp_path):
    # Exercise the actual reporting hook in a separate interpreter, then leave
    # without Python shutdown or a terminal summary, as a later file kill does.
    script = """
import os, sys
from pathlib import Path
from types import SimpleNamespace
from scripts.ci import organization_timing as timing
timing._output_directory = Path(sys.argv[1])
timing.pytest_runtest_logreport(SimpleNamespace(
    when='call', duration=1.0, outcome='failed', nodeid='test_tokens',
    failed=True, longreprtext='AssertionError: retained before later timeout'))
os._exit(9)
"""
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)],
                            cwd=Path(__file__).resolve().parents[2],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 9, result.stderr
    value = json.loads(next(tmp_path.glob("*.jsonl")).read_text())
    assert value["failureText"] == "AssertionError: retained before later timeout"
    assert value["outcome"] == "failed"


def test_manual_windows_lane_keeps_concurrency_assertions_and_bounds():
    root = Path(__file__).resolve().parents[2]
    path = root / ".github/workflows/windows-shared-runtime-diagnostics.yml"
    # BaseLoader keeps YAML's `on` key a string rather than a YAML 1.1 boolean.
    workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
    assert set(workflow["on"]) == {"workflow_dispatch", "push"}
    assert workflow["on"]["push"]["branches"] == ["fix/windows-failure-diagnostics"]
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["env"]["HERMES_TEST_FILE_RETRIES"] == "0"
    job = workflow["jobs"]["shared-runtime"]
    assert job["runs-on"] == "windows-latest"
    step = next(step for step in job["steps"] if "Diagnose" in step.get("name", ""))
    command = step["run"]
    assert "scripts/run_tests.sh tests/eidolon_cli/test_organization_shared_runtime.py" in command
    assert "-j 1 --file-timeout 300 --file-retries 0" in command
    assert "--maxfail=1 --tb=long" in command
    assert "--organization-timing-dir=organization-timing" in command
    evidence = job["steps"][-1]
    assert evidence["if"] == "always()"
    assert "organization-timing/*.jsonl" in evidence["with"]["path"]
