"""Temporary PR207 evidence collection; does not modify the pinned source tree."""
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

HEAD = "e03cfec410a280f8907277009a023376edf73664"
TREE = "cfdda332108dbcb303ab65bb6885be99eb4e9826"
SHELL_BLOB = "67e050ce111f13582eaf1d4c1617bf1416aff268"
FILES = [
    "tests/eidolon_cli/test_organization_attention.py",
    "tests/eidolon_cli/test_organization_outcomes.py",
    "tests/eidolon_cli/test_organization_outcomes_revisions.py",
    "tests/eidolon_cli/test_organization_outcomes_pagination.py",
    "tests/eidolon_cli/test_organization_outcomes_validation.py",
    "tests/gateway/test_organization_policy_reload.py",
    "tests/tui_gateway/test_organization_outcomes.py",
]
EXPECTED_PASSED = dict(zip(FILES, (29, 5, 4, 1, 19, 3, 18), strict=True))
ARMS = ("original-native-timer", "python-watchdog-only")


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


def shell_launcher(source: str, subject: Path, wrapper: Path, evidence: Path, arm: str) -> str:
    # Preserve the canonical venv selection, precompile and env -i allowlist.
    # Only its own location and its final runner target differ in this copy.
    anchor = 'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"'
    invocation = '"$PYTHON" "$SCRIPT_DIR/run_tests_parallel.py" "$@"'
    if source.count(anchor) != 1 or source.count(invocation) != 1:
        raise RuntimeError("Canonical launcher structure changed")
    replacement = ('"$PYTHON" ' + shlex.quote(wrapper.as_posix()) + " run --subject "
                   + shlex.quote(subject.as_posix()) + " --evidence "
                   + shlex.quote(evidence.as_posix()) + " --arm " + shlex.quote(arm))
    return source.replace(anchor, "SCRIPT_DIR=" + shlex.quote((subject / "scripts").as_posix())).replace(invocation, replacement)


def prepare(args) -> int:
    subject, evidence = args.subject.resolve(), args.evidence.resolve()
    identity = verify_source(subject)
    # Read Git's canonical bytes, avoiding Windows worktree newline conversion.
    source = subprocess.check_output(["git", "-C", str(subject), "show", "HEAD:scripts/run_tests.sh"])
    blob = hashlib.sha1(b"blob " + str(len(source)).encode() + b"\0" + source).hexdigest()
    if blob != SHELL_BLOB:
        raise RuntimeError("Canonical shell blob mismatch")
    launcher = shell_launcher(source.decode(), subject, Path(__file__).resolve(), evidence, args.arm)
    args.launcher.parent.mkdir(parents=True, exist_ok=True)
    args.launcher.write_text(launcher, encoding="utf-8", newline="\n")
    write_json(evidence / "launcher-receipt.json", {
        **identity, "canonicalShellBlob": blob, "arm": args.arm,
        "launcherSha256": hashlib.sha256(launcher.encode()).hexdigest(),
        "wrapperSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "changes": ["Set canonical SCRIPT_DIR for external copy", "Invoke external receipt wrapper"],
        "runner": {key: os.environ.get(key) for key in
                   ("ImageOS", "ImageVersion", "RUNNER_OS", "RUNNER_ARCH", "GITHUB_RUN_ID",
                    "GITHUB_RUN_ATTEMPT", "GITHUB_SHA")},
    })
    return 0


def pytest_args(evidence: Path, arm: str) -> list[str]:
    return [*FILES, "-j", "3", "--file-retries", "0", "--file-timeout", "300",
            "--file-timeout-override", "tests/eidolon_cli/test_organization_artifact_retention.py=600",
            "--", "-vv", "-s", "-p", "scripts.ci.organization_timing",
            "--organization-timing-dir=" + (evidence / "timing").as_posix(),
            "-o", "faulthandler_timeout=" + ("30" if arm == ARMS[0] else "0")]


def run(args) -> int:
    subject, evidence = args.subject.resolve(), args.evidence.resolve()
    before = verify_source(subject)
    if sys.platform != "win32" or sys.version_info[:3] != (3, 12, 10):
        raise RuntimeError("Diagnostic requires the observed Windows CPython 3.12.10")
    import pytest
    if pytest.__version__ != "9.1.1":
        raise RuntimeError("Diagnostic requires the observed pytest 9.1.1")
    evidence.mkdir(parents=True, exist_ok=True)
    runner_path = subject / "scripts" / "run_tests_parallel.py"
    spec = importlib.util.spec_from_file_location("pr207_canonical_runner", runner_path)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    if runner._DEFAULT_FILE_TIMEOUT_SECONDS != 300.0:
        raise RuntimeError("Canonical file budget changed")
    command_args = pytest_args(evidence, args.arm)
    write_json(evidence / "runtime.json", {
        **before, "arm": args.arm, "python": sys.version, "platform": platform.platform(),
        "sqlite": sqlite3.sqlite_version, "pytest": pytest.__version__,
        "fileBudgetSeconds": 300, "workers": 3, "fileRetries": 0,
        "expectedFiles": FILES, "expectedPassed": 79, "expectedPassedByFile": EXPECTED_PASSED,
        "nativeDiagnosticTimerSeconds": 30 if args.arm == ARMS[0] else 0,
        "pythonDiagnosticWatchdogSeconds": 30,
        "note": "Diagnostic comparison only. An altered arm is not canonical CI proof.",
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
            try:
                return super().communicate(*communication_args, **kwargs)
            finally:
                if self._receipt_file and not self._receipt_done and self.returncode is not None:
                    self._receipt_done = True
                    value = {"file": self._receipt_file, "pid": self.pid,
                             "nativeReturnCode": self.returncode,
                             "nativeReturnCodeHex": "0x%08X" % (self.returncode & 0xFFFFFFFF),
                             "wallSeconds": time.monotonic() - self._receipt_started}
                    exits.append(value)
                    append("native-exits.jsonl", value)

    def sanitized(text):
        # Never dump the environment or database contents. Hide machine roots
        # in the synthetic pytest output, as the existing stack collector does.
        roots = [(str(subject), "<subject>"), (str(evidence), "<evidence>")]
        for key in ("TEMP", "TMP", "USERPROFILE"):
            if os.environ.get(key):
                roots.append((os.environ[key], "<" + key.lower() + ">"))
        for root, replacement in sorted(roots, key=lambda item: len(item[0]), reverse=True):
            text = text.replace(root, replacement).replace(root.replace("\\", "/"), replacement)
        return text

    def run_once(*run_args, **kwargs):
        result = original_run_once(*run_args, **kwargs)
        file, rc, output, summary, elapsed = result
        relative = relative_file(file)
        value = {"file": relative, "runnerReturnCode": rc, "summary": summary, "wallSeconds": elapsed}
        results.append(value)
        append("file-results.jsonl", value)
        log_path = evidence / "pytest-output" / (relative.replace("/", "__") + ".log")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(sanitized(output), encoding="utf-8")
        return result

    subprocess.Popen = ReceiptPopen
    runner._run_one_file_once = run_once
    previous_argv = sys.argv
    exit_code = 1
    try:
        sys.argv = [str(runner_path), *command_args]
        exit_code = runner.main()
    finally:
        subprocess.Popen = original_popen
        runner._run_one_file_once = original_run_once
        sys.argv = previous_argv
        after = verify_source(subject)
        passed = sum(row["summary"].get("passed", 0) for row in results)
        complete = (len(results) == len(exits) == len(FILES)
                    and sorted(row["file"] for row in results) == sorted(FILES)
                    and sorted(row["file"] for row in exits) == sorted(FILES)
                    and passed == 79
                    and all(row["summary"].get("passed", 0) == EXPECTED_PASSED[row["file"]]
                            for row in results)
                    and all(row["nativeReturnCode"] == 0 for row in exits)
                    and all(row["runnerReturnCode"] == 0 for row in results)
                    and all(not row["summary"].get(key, 0) for row in results
                            for key in ("failed", "errors", "skipped", "xfailed", "xpassed")))
        write_json(evidence / "summary.json", {
            "arm": args.arm, "sourceBefore": before, "sourceAfter": after,
            "canonicalRunnerExit": exit_code, "completeExpectedCoverage": complete,
            "passed": passed, "filesRecorded": len(results), "nativeExitsRecorded": len(exits),
            "diagnosticOnly": True,
        })
    return exit_code if exit_code else (0 if complete else 1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("prepare", "run"))
    parser.add_argument("--subject", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--arm", choices=ARMS, required=True)
    parser.add_argument("--launcher", type=Path)
    args = parser.parse_args()
    if args.mode == "prepare" and args.launcher is None:
        parser.error("prepare requires --launcher")
    return prepare(args) if args.mode == "prepare" else run(args)


if __name__ == "__main__":
    raise SystemExit(main())
