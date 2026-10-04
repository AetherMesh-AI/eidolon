"""Native behavior of the Windows updater's invocation and relaunch boundaries.

Every invocation runs through the production job-object launcher against an
isolated Python module. No source extraction, real update, or GUI launch occurs.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.windows_only

REPO_ROOT = Path(__file__).resolve().parent.parent
WINDOWS_PS1 = REPO_ROOT / "scripts" / "desktop-update" / "windows.ps1"


def _run_native_fixture(root: Path, flag: str, prefix: str) -> list[dict]:
    powershell = shutil.which("powershell.exe")
    assert powershell, "Native updater tests require Windows PowerShell"
    env = dict(os.environ, HERMES_HOME=str(root / "profile"), TEMP=str(root), TMP=str(root))
    env.pop("EIDOLON_HOME", None)
    result = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(WINDOWS_PS1),
         "-InstallRoot", str(root), "-Branch", "native-fixture-branch", "-NoUi", flag],
        env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90,
    )
    evidence = result.stdout + "\n" + result.stderr
    log = root / "profile/logs/desktop-update-handoff.log"
    if log.exists():
        evidence += "\n" + log.read_text(encoding="utf-8", errors="replace")
    assert result.returncode == 0, evidence
    lines = [line.removeprefix(prefix) for line in result.stdout.splitlines() if line.startswith(prefix)]
    assert len(lines) == 1, evidence
    return json.loads(lines[0])


def test_update_retry_and_rebuild_use_native_python_module_through_job_launcher(tmp_path):
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(tmp_path / "venv")],
                   check=True, capture_output=True, text=True, timeout=60)
    python = tmp_path / "venv/Scripts/python.exe"
    # A wrong console-shim route cannot silently succeed: this file isn't a PE.
    (python.parent / "eidolon.exe").write_text("must not execute this shim", encoding="utf-8")
    module = tmp_path / "eidolon_cli"
    module.mkdir()
    (module / "__init__.py").write_text("", encoding="utf-8")
    (module / "main.py").write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "calls = Path('fixture-invocations.jsonl')\n"
        "previous = calls.read_text().splitlines() if calls.exists() else []\n"
        "record = dict(executable=sys.executable, argv=sys.argv[1:], cwd=os.getcwd(), "
        "package=__package__, module=__file__)\n"
        "with calls.open('a') as output: output.write(json.dumps(record) + '\\n')\n"
        "print(json.dumps(record), flush=True)\n"
        "sys.exit(7 if not previous else 0)\n",
        encoding="utf-8",
    )

    results = _run_native_fixture(tmp_path, "-SelfTestPythonInvocation", "SELF-TEST PYTHON INVOCATION: ")
    assert [result["Name"] for result in results] == ["update", "retry", "rebuild", "verify"]
    assert [result["Code"] for result in results] == [7, 0, 0, 0]
    expected_update = ["update", "--yes", "--gateway", "--force", "--branch", "native-fixture-branch", "--keep-stash"]
    expected_args = [expected_update, expected_update, ["desktop", "--force-build", "--build-only"]]
    records = [json.loads(result["Output"].strip()) for result in results]
    assert [record["argv"] for record in records[:3]] == expected_args
    assert records[3]["argv"] == ["verify-fixture"]
    assert len((tmp_path / "fixture-invocations.jsonl").read_text().splitlines()) == 3
    for result, record in zip(results, records):
        assert result["StartedAfterJobAssignment"] is True
        assert Path(record["executable"]).samefile(python)
    for record in records[:3]:
        assert record["package"] == "eidolon_cli"
        assert Path(record["module"]).samefile(module / "main.py")
        assert Path(record["cwd"]).samefile(tmp_path)


def test_relaunch_wait_handles_present_delayed_missing_and_absent_target(tmp_path):
    results = _run_native_fixture(tmp_path, "-SelfTestRelaunchWait", "SELF-TEST RELAUNCH WAIT: ")
    by_name = {result["Name"]: result for result in results}
    assert set(by_name) == {"present", "delayed", "missing", "empty"}
    assert by_name["present"]["Ready"] is True
    assert by_name["present"]["Slept"] == 0
    assert by_name["delayed"]["Ready"] is True
    assert by_name["delayed"]["Slept"] == 1500
    assert by_name["missing"]["Ready"] is False
    assert by_name["missing"]["Slept"] == 120_000
    assert by_name["empty"]["Ready"] is False
    assert by_name["empty"]["Slept"] == 0
    for result in results:
        assert all(interval == 500 for interval in result["Intervals"])
    assert not list(tmp_path.glob("relaunch-*.exe"))
