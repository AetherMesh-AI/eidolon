"""Temporary PR207 ledger evidence collection; no subject or test behavior changes."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import shlex
import sqlite3
import subprocess
import sys
import threading
import time

HEAD = '6c071ecac21ee8143b2d34a43b1670e159f0811e'
TREE = '008fc3f0a6d66706aa3886a6e0ffed71c2682581'
SHELL_BLOB = "67e050ce111f13582eaf1d4c1617bf1416aff268"
EXPECTED = {
    "tests/eidolon_cli/test_organization_setup_subscription_filesystem.py": {
        "passed": 24,
        "skipped": 152
    },
    "tests/eidolon_cli/test_organization_setup_subscription_reconciliation.py": {
        "passed": 2,
        "skipped": 35
    },
    "tests/eidolon_cli/test_subscription_native_diagnostics.py": {
        "passed": 15,
        "skipped": 5
    },
    "tests/eidolon_cli/test_subscription_native_recovery_faults.py": {
        "passed": 19,
        "skipped": 52
    },
    "tests/eidolon_cli/test_subscription_native_lifecycle.py": {
        "passed": 29,
        "skipped": 64
    },
    "tests/tui_gateway/test_subscription_native_journey.py": {
        "passed": 3,
        "skipped": 7
    },
    "tests/eidolon_cli/test_organization_store.py": {
        "passed": 11,
        "skipped": 0
    },
    "tests/eidolon_cli/test_organization_schema_initialization.py": {
        "passed": 2,
        "skipped": 0
    },
    "tests/eidolon_cli/test_organization_budget_migration.py": {
        "passed": 3,
        "skipped": 0
    },
    "tests/eidolon_cli/test_organization_completion_reserve.py": {
        "passed": 13,
        "skipped": 0
    },
    "tests/eidolon_cli/test_organization_completion_reserve_policy.py": {
        "passed": 21,
        "skipped": 0
    },
    "tests/eidolon_cli/test_organization_completion_reserve_e2e.py": {
        "passed": 2,
        "skipped": 0
    },
    "tests/eidolon_cli/test_organization_pacing.py": {
        "passed": 31,
        "skipped": 0
    },
    "tests/eidolon_cli/test_organization_pacing_runtime.py": {
        "passed": 2,
        "skipped": 0
    },
    "tests/eidolon_cli/test_organization_pacing_cli.py": {
        "passed": 2,
        "skipped": 0
    },
    "tests/eidolon_cli/test_organization_model_routing.py": {
        "passed": 22,
        "skipped": 0
    },
    "tests/eidolon_cli/test_organization_model_routing_e2e.py": {
        "passed": 12,
        "skipped": 0
    },
    "tests/eidolon_cli/test_organization_routing_observations.py": {
        "passed": 8,
        "skipped": 0
    },
    "tests/eidolon_cli/test_organization_route_benchmarks.py": {
        "passed": 32,
        "skipped": 0
    },
    "tests/eidolon_cli/test_organization_owner_policy.py": {
        "passed": 92,
        "skipped": 0
    },
    "tests/eidolon_cli/test_organization_ledger_writer.py": {
        "passed": 21,
        "skipped": 8
    },
    "tests/eidolon_cli/test_organization_subscription_send_coordination.py": {
        "passed": 0,
        "skipped": 15
    },
    "tests/eidolon_cli/test_organization_team_setup.py": {
        "passed": 62,
        "skipped": 0
    },
    "tests/eidolon_cli/test_organization_team_admission.py": {
        "passed": 36,
        "skipped": 0
    },
    "tests/eidolon_cli/test_organization_team_generation.py": {
        "passed": 33,
        "skipped": 0
    },
    "tests/tui_gateway/test_organization_team_setup.py": {
        "passed": 13,
        "skipped": 0
    },
    "tests/eidolon_cli/test_organization_shared_allowance.py": {
        "passed": 21,
        "skipped": 0
    },
    "tests/eidolon_cli/test_organization_shared_allowance_cli.py": {
        "passed": 4,
        "skipped": 0
    },
    "tests/eidolon_cli/test_organization_shared_budget.py": {
        "passed": 9,
        "skipped": 2
    },
    "tests/eidolon_cli/test_organization_shared_runtime.py": {
        "passed": 28,
        "skipped": 0
    },
    "tests/eidolon_cli/test_organization_shared_contention.py": {
        "passed": 10,
        "skipped": 0
    },
    "tests/tui_gateway/test_organization_shared_allowance.py": {
        "passed": 15,
        "skipped": 1
    },
    "tests/tui_gateway/test_organization_owner_policy.py": {
        "passed": 25,
        "skipped": 1
    },
    "tests/agent/test_bounded_context.py": {
        "passed": 6,
        "skipped": 0
    },
    "tests/eidolon_cli/test_organization_artifact_retention.py": {
        "passed": 1,
        "skipped": 0
    },
    "tests/eidolon_cli/test_organization_identity.py": {
        "passed": 4,
        "skipped": 0
    },
    "tests/eidolon_cli/test_organization_metadata.py": {
        "passed": 13,
        "skipped": 0
    },
    "tests/eidolon_cli/test_organization_metadata_read.py": {
        "passed": 12,
        "skipped": 0
    },
    "tests/eidolon_cli/test_organization_phases.py": {
        "passed": 3,
        "skipped": 0
    },
    "tests/eidolon_cli/test_organization_observation.py": {
        "passed": 44,
        "skipped": 0
    },
    "tests/tui_gateway/test_organization_observation.py": {
        "passed": 20,
        "skipped": 1
    },
    "tests/tui_gateway/test_organization_metadata.py": {
        "passed": 12,
        "skipped": 1
    },
    "tests/eidolon_cli/test_organization_identity_memory.py": {
        "passed": 12,
        "skipped": 0
    },
    "tests/eidolon_cli/test_organization_identity_assignments.py": {
        "passed": 11,
        "skipped": 0
    },
    "tests/eidolon_cli/test_organization_history.py": {
        "passed": 17,
        "skipped": 0
    },
    "tests/eidolon_cli/test_organization_cross_feature_recovery.py": {
        "passed": 6,
        "skipped": 0
    },
    "tests/eidolon_cli/test_organization_request_recovery.py": {
        "passed": 16,
        "skipped": 0
    },
    "tests/eidolon_cli/test_organization_selective_recovery.py": {
        "passed": 15,
        "skipped": 25
    },
    "tests/eidolon_cli/test_organization_replacement.py": {
        "passed": 17,
        "skipped": 0
    },
    "tests/eidolon_cli/test_organization_tool_receipts.py": {
        "passed": 11,
        "skipped": 0
    }
}
FILES = list(EXPECTED)



def git(subject: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(subject), *args], text=True).strip()


def verify_source(subject: Path) -> dict:
    receipt = {"head": git(subject, "rev-parse", "HEAD"),
               "tree": git(subject, "rev-parse", "HEAD^{tree}")}
    if receipt != {"head": HEAD, "tree": TREE}:
        raise RuntimeError("Pinned source identity mismatch")
    if git(subject, "status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError("Pinned source has tracked changes")
    return receipt


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def shell_launcher(source: str, subject: Path, wrapper: Path, evidence: Path) -> str:
    # Preserve the canonical venv selection, precompile and env -i allowlist.
    # Only its own location and its final runner target differ in this copy.
    anchor = 'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"'
    invocation = '"$PYTHON" "$SCRIPT_DIR/run_tests_parallel.py" "$@"'
    if source.count(anchor) != 1 or source.count(invocation) != 1:
        raise RuntimeError("Canonical launcher structure changed")
    replacement = ('"$PYTHON" ' + shlex.quote(wrapper.as_posix()) + " run --subject "
                   + shlex.quote(subject.as_posix()) + " --evidence "
                   + shlex.quote(evidence.as_posix()))
    return source.replace(anchor, "SCRIPT_DIR=" + shlex.quote((subject / "scripts").as_posix())).replace(invocation, replacement)


def prepare(args) -> int:
    subject, evidence = args.subject.resolve(), args.evidence.resolve()
    identity = verify_source(subject)
    # Read Git's canonical bytes, avoiding Windows worktree newline conversion.
    source = subprocess.check_output(["git", "-C", str(subject), "show", "HEAD:scripts/run_tests.sh"])
    blob = hashlib.sha1(b"blob " + str(len(source)).encode() + b"\0" + source).hexdigest()
    if blob != SHELL_BLOB:
        raise RuntimeError("Canonical shell blob mismatch")
    launcher = shell_launcher(source.decode(), subject, Path(__file__).resolve(), evidence)
    args.launcher.parent.mkdir(parents=True, exist_ok=True)
    args.launcher.write_text(launcher, encoding="utf-8", newline="\n")
    write_json(evidence / "launcher-receipt.json", {
        **identity, "canonicalShellBlob": blob,
        "launcherSha256": hashlib.sha256(launcher.encode()).hexdigest(),
        "wrapperSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "changes": ["Set canonical SCRIPT_DIR for external copy", "Invoke external receipt wrapper"],
        "runner": {key: os.environ.get(key) for key in
                   ("ImageOS", "ImageVersion", "RUNNER_OS", "RUNNER_ARCH", "GITHUB_RUN_ID",
                    "GITHUB_RUN_ATTEMPT", "GITHUB_SHA")},
    })
    return 0


def pytest_args(evidence: Path) -> list[str]:
    return [*FILES, "-j", "3", "--file-retries", "0", "--file-timeout", "300",
            "--file-timeout-override", "tests/eidolon_cli/test_organization_artifact_retention.py=600",
            "--", "-vv", "-s", "-p", "scripts.ci.organization_timing",
            "--organization-timing-dir=" + (evidence / "timing").as_posix(),
            "-o", "faulthandler_timeout=30"]


def run(args) -> int:
    subject, evidence = args.subject.resolve(), args.evidence.resolve()
    before = verify_source(subject)
    if sys.platform != "win32" or sys.version_info[:2] != (3, 12):
        raise RuntimeError("Diagnostic requires canonical Windows CPython 3.12")
    import pytest
    if pytest.__version__ != "9.1.1":
        raise RuntimeError("Diagnostic requires locked pytest 9.1.1")
    evidence.mkdir(parents=True, exist_ok=True)
    runner_path = subject / "scripts" / "run_tests_parallel.py"
    spec = importlib.util.spec_from_file_location("pr207_ledger_canonical_runner", runner_path)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    if runner._DEFAULT_FILE_TIMEOUT_SECONDS != 300.0:
        raise RuntimeError("Canonical file budget changed")
    command_args = pytest_args(evidence)
    write_json(evidence / "runtime.json", {
        **before, "python": sys.version, "platform": platform.platform(),
        "sqlite": sqlite3.sqlite_version, "pytest": pytest.__version__,
        "fileBudgetSeconds": 300, "workers": 3, "fileRetries": 0,
        "expectedFiles": FILES, "expectedStarted": 1211, "expectedPassed": 842, "expectedSkipped": 369, "expectedByFile": EXPECTED,
        "nativeDiagnosticTimerSeconds": 30,
        "pythonDiagnosticWatchdogSeconds": 30,
        "note": "Observation-only diagnostic. This is not canonical CI proof.",
    })
    lock = threading.Lock()
    original_popen = subprocess.Popen
    original_run_once = runner._run_one_file_once
    exits, results = [], []

    def append(name, value):
        with lock:
            with (evidence / name).open("a", encoding="utf-8") as output:
                output.write(json.dumps(value) + "\n")

    def relative_file(value):
        path = Path(value)
        if path.is_absolute():
            return path.relative_to(subject).as_posix()
        return path.as_posix()

    class ReceiptPopen(original_popen):
        def __init__(self, command, *popen_args, **kwargs):
            self._receipt_file = None
            self._receipt_done = False
            self._receipt_started = time.monotonic()
            if isinstance(command, (list, tuple)) and len(command) >= 4 and list(command[1:3]) == ["-m", "pytest"]:
                self._receipt_file = relative_file(command[3])
            super().__init__(command, *popen_args, **kwargs)

        def communicate(self, *communication_args, **kwargs):
            captured = None
            try:
                captured = super().communicate(*communication_args, **kwargs)
                return captured
            finally:
                if self._receipt_file and not self._receipt_done and self.returncode is not None:
                    self._receipt_done = True
                    value = {"file": self._receipt_file, "pid": self.pid,
                             "nativeReturnCode": self.returncode,
                             "nativeReturnCodeHex": "0x%08X" % (self.returncode & 0xFFFFFFFF),
                             "wallSeconds": time.monotonic() - self._receipt_started}
                    if captured is not None and isinstance(captured[0], str):
                        raw = captured[0]
                        path = evidence / "native-output" / (self._receipt_file.replace("/", "__") + ".log")
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text(raw, encoding="utf-8", newline="")
                        value["outputSha256"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
                        value["outputBytes"] = len(raw.encode("utf-8"))
                    exits.append(value)
                    append("native-exits.jsonl", value)

    def run_once(*run_args, **kwargs):
        result = original_run_once(*run_args, **kwargs)
        file, rc, output, summary, elapsed = result
        relative = relative_file(file)
        value = {"file": relative, "runnerReturnCode": rc, "summary": summary, "wallSeconds": elapsed}
        results.append(value)
        log_path = evidence / "pytest-output" / (relative.replace("/", "__") + ".log")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(output, encoding="utf-8", newline="")
        value["outputSha256"] = hashlib.sha256(output.encode("utf-8")).hexdigest()
        value["outputBytes"] = len(output.encode("utf-8"))
        append("file-results.jsonl", value)
        return result

    subprocess.Popen = ReceiptPopen
    runner._run_one_file_once = run_once
    previous_argv = sys.argv
    exit_code = 1
    runner_error = None
    try:
        sys.argv = [str(runner_path), *command_args]
        exit_code = runner.main()
    except BaseException as exc:
        runner_error = type(exc).__name__
        raise
    finally:
        subprocess.Popen = original_popen
        runner._run_one_file_once = original_run_once
        sys.argv = previous_argv
        try:
            after = verify_source(subject)
        except Exception as exc:
            after = {"verificationError": type(exc).__name__, "message": str(exc)}
        phase_coverage = audit_phase_coverage(evidence / "timing")
        passed = sum(row["summary"].get("passed", 0) for row in results)
        output_complete = (verify_output_receipts(evidence / "pytest-output", results)
                           and verify_output_receipts(evidence / "native-output", exits))
        complete = (before == after and output_complete and runner_error is None
                    and phase_coverage["complete"]
                    and len(results) == len(exits) == len(FILES)
                    and sorted(row["file"] for row in results) == sorted(FILES)
                    and sorted(row["file"] for row in exits) == sorted(FILES)
                    and passed == 842
                    and all(all(row["summary"].get(key, 0) == EXPECTED[row["file"]][key] for key in ("passed", "skipped"))
                            for row in results)
                    and all(row["nativeReturnCode"] == 0 for row in exits)
                    and all(row["runnerReturnCode"] == 0 for row in results)
                    and all(not row["summary"].get(key, 0) for row in results
                            for key in ("failed", "errors", "xfailed", "xpassed")))
        write_json(evidence / "summary.json", {
            "sourceBefore": before, "sourceAfter": after,
            "canonicalRunnerExit": exit_code, "runnerException": runner_error,
            "completeExpectedCoverage": complete, "completeOutputReceipts": output_complete,
            "phaseCoverage": phase_coverage, "passed": passed, "filesRecorded": len(results), "nativeExitsRecorded": len(exits),
            "diagnosticOnly": True,
        })
    return exit_code if exit_code else (0 if complete else 1)


def verify_output_receipts(directory: Path, rows: list[dict]) -> bool:
    for row in rows:
        if "outputSha256" not in row:
            return False
        try:
            data = (directory / (row["file"].replace("/", "__") + ".log")).read_bytes()
        except OSError:
            return False
        if hashlib.sha256(data).hexdigest() != row["outputSha256"] or len(data) != row["outputBytes"]:
            return False
    return True


def audit_phase_coverage(directory: Path) -> dict:
    expected_nodes = {}
    started, teardown, bad = [], [], []
    for path in directory.glob("organization-timing-*.jsonl"):
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                bad.append({"file": path.name, "line": line_number, "error": "incomplete JSON record"})
                continue
            if row.get("event") == "start":
                started.append(row["nodeid"])
            elif row.get("event") == "teardown" and row.get("outcome") == "passed":
                teardown.append(row["nodeid"])
            elif row.get("event") in ("setup", "call", "teardown") and row.get("outcome") not in ("passed", "skipped"):
                bad.append({"nodeid": row["nodeid"], "event": row["event"], "outcome": row["outcome"]})
    for node in started:
        file = node.split("::", 1)[0]
        expected_nodes[file] = expected_nodes.get(file, 0) + 1
    exact_counts = (set(expected_nodes) == set(FILES)
                    and all(expected_nodes[file] == sum(EXPECTED[file].values()) for file in FILES))
    return {"started": len(started), "teardownPassed": len(teardown), "badOutcomes": bad,
            "complete": exact_counts and len(started) == len(set(started)) == 1211
                        and sorted(started) == sorted(teardown) and not bad}


def sanitize_evidence(args) -> int:
    import re
    directory = args.evidence.resolve() / "timing"
    paths = [*sorted(directory.glob("organization-timing-*.jsonl")),
             *sorted(directory.glob("organization-stacks-*.txt"))]
    if not paths:
        raise RuntimeError("Missing native timing evidence")
    target = directory / "sanitized"
    target.mkdir(parents=True, exist_ok=True)
    for path in paths:
        content = path.read_text(encoding="utf-8")
        if path.suffix == ".txt":
            content = re.sub(r'File "([^"\n]+)"',
                             lambda match: 'File "' + re.split(r'[/\\]', match.group(1))[-1] + '"', content)
        (target / path.name).write_text(content, encoding="utf-8")
    print(f"Retained {len(paths)} timing/stack files without echoing their full contents")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("prepare", "run", "sanitize"))
    parser.add_argument("--subject", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--launcher", type=Path)
    args = parser.parse_args()
    if args.mode == "prepare" and args.launcher is None:
        parser.error("prepare requires --launcher")
    if args.mode == "sanitize":
        return sanitize_evidence(args)
    return prepare(args) if args.mode == "prepare" else run(args)


if __name__ == "__main__":
    raise SystemExit(main())
