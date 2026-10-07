"""Frozen, evaluator-owned holdouts for disposable organization trials.

Only the existing OS-isolated project runner executes submitted source. Oracle
files live outside every agent read root and are introduced after completion.
These small holdouts measure fixture semantics, not adversarial-code immunity.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def scenario_data(scenario):
    return asdict(scenario) if is_dataclass(scenario) else dict(scenario)


def scenario_digest(scenario):
    return hashlib.sha256(canonical(scenario_data(scenario))).hexdigest()


def require_outside_roots(path, read_roots):
    resolved = Path(path).resolve()
    roots = tuple(Path(root).resolve() for root in read_roots)
    if not roots or any(resolved == root or resolved.is_relative_to(root) or root.is_relative_to(resolved) for root in roots):
        raise ValueError("Evaluator-private evidence must be outside every agent read root")
    return resolved


def _relative(path):
    if (not isinstance(path, str) or not path or len(path) > 1024 or "\\" in path
            or path.startswith("-") or any(ord(c) < 32 or ord(c) == 127 for c in path)
            or PurePosixPath(path).is_absolute() or any(p in ("", ".", "..", ".git") for p in path.split("/"))):
        raise ValueError("Only canonical relative project paths are allowed")
    return path


def git_bytes(source, *args):
    """Git plumbing only; never check out or execute project hooks/code."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_NO_REPLACE_OBJECTS="1",
               GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    return subprocess.check_output(
        ["git", "-C", str(Path(source).resolve()), "-c", "core.hooksPath=" + os.devnull,
         "-c", "commit.gpgsign=false", "-c", "core.fsmonitor=false", *args],
        env=env, stderr=subprocess.PIPE, timeout=30)


def commit_files(source, commit, paths):
    """Read exact regular UTF-8 Git blobs, refusing symlinks and unselected files."""
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
        raise ValueError("Delivered source requires a full immutable Git commit ID")
    expected = set(map(_relative, paths))
    if not expected or len(expected) != len(paths):
        raise ValueError("Delivered source requires unique selected paths")
    resolved = git_bytes(source, "rev-parse", "--verify", commit + "^{commit}").decode().strip()
    if resolved != commit:
        raise ValueError("Delivered commit identity changed")
    entries = git_bytes(source, "ls-tree", "-rz", "--full-tree", commit).split(b"\0")
    objects = {}
    for entry in filter(None, entries):
        metadata, name = entry.split(b"\t", 1)
        mode, kind, oid = metadata.decode("ascii").split()
        path = name.decode("utf-8")
        if mode not in {"100644", "100755"} or kind != "blob":
            raise ValueError("Trial commits may contain only regular project files")
        objects[_relative(path)] = oid
    if set(objects) != expected:
        raise ValueError("Commit tree disagrees with the complete selected project paths")
    files, size = {}, 0
    for path, oid in sorted(objects.items()):
        if size + int(git_bytes(source, "cat-file", "-s", oid)) > 524288:
            raise ValueError("Trial commit exceeds the bounded project size")
        blob = git_bytes(source, "cat-file", "blob", oid)
        size += len(blob)
        if size > 524288:
            raise ValueError("Trial commit exceeds the bounded project size")
        files[path] = blob.decode("utf-8")
    return files


def source_manifest(files):
    return [{"path": path, "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
             "bytes": len(content.encode("utf-8"))} for path, content in sorted(files.items())]


_OBSERVATION_PREFIX = "EIDOLON_TRIAL_OBSERVATIONS_V1:"
_OBSERVATION_HARNESS = '''import copy
import json
import sys
import unittest

# Bind trusted helpers before importing submitted code. This is not a claim
# that malicious code in the same interpreter cannot introspect the harness.
_copy = copy.deepcopy
_dumps = json.dumps
_write = sys.stdout.write
_type = type

class IndependentObservations(unittest.TestCase):
    def test_collect_observations(self):
        from app import FUNCTION as candidate
        observed = []
        for case in CASES:
            arguments = _copy(case['arguments'])
            try:
                value = candidate(*arguments)
                observed.append({'caseId': case['caseId'], 'value': value,
                                 'resultType': _type(value).__name__, 'after': arguments})
            except Exception as error:
                observed.append({'caseId': case['caseId'], 'exception': _type(error).__name__})
        _write(PREFIX + _dumps(observed, separators=(',', ':'), allow_nan=False) + '\\n')
'''


@dataclass(frozen=True)
class FrozenOracle:
    directory: Path
    manifest_sha256: str
    scenario_sha256: str
    oracle_id: str


def oracle_for_scenario(scenario_id, variant=None):
    """Frozen expected data stays outside the sandbox; it grades observations."""
    cases, expected = [], []
    if scenario_id == "duplicate_retention":
        if variant not in {"earliest", "latest"}:
            raise ValueError("Duplicate oracle requires explicit earliest/latest retention")
        examples = [[], [{'id': 'unique', 'payload': [1, 2]}],
            [{'id': 'z', 'payload': {'version': 11}}, {'id': 'a', 'payload': 3},
             {'id': 'z', 'payload': {'version': 29}}, {'id': 'q', 'extra': [1, 2]},
             {'id': 'a', 'payload': 19}, {'id': 'z', 'last': True}],
            [{'id': 8, 'x': 1}, {'id': '8', 'x': 2}, {'id': None, 'x': 3},
             {'id': 8, 'x': 4}, {'id': None, 'x': 5}],
            [{'id': i % 7, 'payload': i} for i in range(53)]]
        indices = [[], [0], [0, 1, 3], [0, 1, 2], list(range(7))] if variant == "earliest" else [
            [], [0], [5, 4, 3], [3, 1, 4], [49, 50, 51, 52, 46, 47, 48]]
        for number, (rows, selected) in enumerate(zip(examples, indices)):
            identity = f"retention-{number}"
            cases.append({"caseId": identity, "arguments": [rows]})
            expected.append({"caseId": identity, "value": [rows[i] for i in selected],
                             "resultType": "list", "after": [rows]})
        function, oracle_id = "deduplicate", "duplicate_retention_" + variant
    elif scenario_id == "signed_bucket":
        inputs = [(value, width) for width in range(1, 10) for value in range(-31, 32)]
        for width in (3, 17, 31, 1000000007):
            for anchor in (2**54 + 1, 2**100 + 37, 10**110 + 71):
                inputs.extend((value, width) for value in (
                    anchor, -anchor, anchor * width - 1, -anchor * width - 1))
        for number, (value, width) in enumerate(inputs):
            identity = str(number)
            cases.append({"caseId": identity, "arguments": [value, width]})
            expected.append({"caseId": identity, "value": value // width,
                             "resultType": "int", "after": [value, width]})
        function, oracle_id = "bucket", "signed_bucket_floor"
    else:
        raise ValueError("No independent holdout exists for this validated scenario")
    content = (f"CASES = {cases!r}\nPREFIX = {_OBSERVATION_PREFIX!r}\n"
               + _OBSERVATION_HARNESS.replace("FUNCTION", function))
    return {"oracle_id": oracle_id, "files": {"test_trial_holdout.py": content},
            "expected_test_count": 1, "expected_observations": expected}


def freeze_scenario_oracle(private_dir, scenario, *, read_roots):
    """Call before starting the service/provider; never put this return in prompts."""
    spec = scenario_data(scenario)
    oracle = oracle_for_scenario(spec["scenario_id"], spec.get("retention"))
    oracle_id, count, files = oracle["oracle_id"], oracle["expected_test_count"], oracle["files"]
    if spec.get("oracle_id") != oracle_id:
        raise ValueError("Scenario and trusted oracle identifiers disagree")
    directory = require_outside_roots(private_dir, read_roots)
    directory.mkdir(parents=True, exist_ok=False)
    manifest = {"schemaVersion": 1, "oracleId": oracle_id, "scenarioSha256": scenario_digest(spec),
                "frozenAt": datetime.now(timezone.utc).isoformat(), "expectedTestCount": count,
                "freezePhase": "before_provider_calls", "files": source_manifest(files),
                "implementationPaths": sorted(spec["source_files"]),
                "expectedObservations": oracle["expected_observations"]}
    for path, text in files.items():
        target = directory / path
        target.write_bytes(text.encode("utf-8"))
        target.chmod(0o400)
    encoded = canonical(manifest)
    target = directory / "oracle-manifest.json"
    target.write_bytes(encoded)
    target.chmod(0o400)
    return FrozenOracle(directory, hashlib.sha256(encoded).hexdigest(), manifest["scenarioSha256"], oracle_id)


def read_frozen_oracle(frozen, *, read_roots):
    directory = require_outside_roots(frozen.directory, read_roots)
    if (directory / "oracle-manifest.json").is_symlink():
        raise ValueError("Frozen oracle manifest may not be a symlink")
    encoded = (directory / "oracle-manifest.json").read_bytes()
    if hashlib.sha256(encoded).hexdigest() != frozen.manifest_sha256:
        raise ValueError("Frozen oracle manifest changed after admission")
    manifest = json.loads(encoded)
    if manifest["scenarioSha256"] != frozen.scenario_sha256 or manifest["oracleId"] != frozen.oracle_id:
        raise ValueError("Frozen oracle identity disagrees with admission")
    files = {}
    for entry in manifest["files"]:
        path = directory / _relative(entry["path"])
        if path.is_symlink():
            raise ValueError("Frozen oracle may not be a symlink")
        files[entry["path"]] = path.read_bytes().decode("utf-8")
    if source_manifest(files) != manifest["files"]:
        raise ValueError("Frozen oracle test bytes changed after admission")
    return manifest, files


def evaluate_delivered_commit(source, commit, frozen, *, app_completed, delivered_paths, read_roots):
    """Grade app acceptance and independent semantics as distinct observations."""
    manifest, tests = read_frozen_oracle(frozen, read_roots=read_roots)
    result = {"schemaVersion": 1, "oracleId": frozen.oracle_id, "oracleSha256": frozen.manifest_sha256,
              "scenarioSha256": frozen.scenario_sha256, "applicationAccepted": app_completed is True,
              "semanticCorrect": None, "status": "not_run", "commit": commit,
              "deliveredManifest": [], "execution": None,
              "limitations": "Frozen finite holdouts test exact delivered implementation bytes, not arbitrary correctness. "
                  "Expected results stay outside the sandbox and are compared to bounded serialized observations. "
                  "Untrusted code shares the interpreter and can introspect or forge output; this is not adversarial-proof. "
                  "Recovery coverage is a separate driver observation; passing semantics alone does not establish it."}
    if app_completed is not True or not commit:
        result["reason"] = "Independent holdouts run only after the application reports completion with a delivered commit."
        return result
    delivered = commit_files(source, commit, delivered_paths)
    result["deliveredManifest"] = source_manifest(delivered)
    implementation = {path: delivered[path] for path in manifest["implementationPaths"]}
    if implementation.keys() & tests.keys():
        raise ValueError("Submitted source collides with the evaluator-owned holdout")
    files = [{"path": "root0/" + path, "content": content,
              "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(), "revision": 0}
             for path, content in sorted({**implementation, **tests}.items())]
    from eidolon_cli.organization_project_runner import ProjectExecutionGrant, run_project_tests, snapshot_digest
    execution = run_project_tests(files, ProjectExecutionGrant(pattern="test_trial_holdout.py", timeout_seconds=20, output_bytes=65536))
    observation_lines = [line[len(_OBSERVATION_PREFIX):] for line in execution["stdout"].splitlines()
                         if line.startswith(_OBSERVATION_PREFIX)]
    observed = None
    if len(observation_lines) == 1:
        try:
            observed = json.loads(observation_lines[0])
        except (ValueError, RecursionError):
            observed = None
    try:
        observations_match = canonical(observed) == canonical(manifest["expectedObservations"])
    except (ValueError, UnicodeError, RecursionError):
        observations_match = False
    passed = (observations_match and execution["status"] == "passed" and execution["exitCode"] == 0
              and execution["testCount"] == manifest["expectedTestCount"] and execution.get("skippedCount", 0) == 0
              and execution["isolation"]["established"] is True
              and execution["snapshotSha256"] == snapshot_digest(files))
    unsupported = execution["status"] == "unsupported"
    inconclusive = execution["status"] in {"blocked", "cancelled", "timed_out"}
    result.update(status="passed" if passed else "unsupported" if unsupported else "inconclusive" if inconclusive else "failed",
                  semanticCorrect=True if passed else None if unsupported or inconclusive else False, execution=execution,
                  observationsMatch=observations_match, expectedObservationCount=len(manifest["expectedObservations"]))
    return result
