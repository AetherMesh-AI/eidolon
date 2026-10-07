"""Independent semantics are measured in the real sandbox, never host Python."""
from dataclasses import replace
import hashlib
import json
import os

import pytest

from scripts.organization_trial_oracle import (
    commit_files, evaluate_delivered_commit, freeze_scenario_oracle, git_bytes,
    oracle_for_scenario, read_frozen_oracle,
)
from scripts.organization_trial_scenarios import get_scenario


def _repository(path, files):
    path.mkdir()
    for name, content in files.items():
        (path / name).write_bytes(content.encode())
    git_bytes(path, "init", "--initial-branch=main")
    git_bytes(path, "add", ".")
    git_bytes(path, "-c", "user.name=Test", "-c", "user.email=test@localhost", "commit", "-m", "Fixture")
    return git_bytes(path, "rev-parse", "HEAD").decode().strip()


def test_frozen_oracle_is_private_hash_bound_and_commit_bytes_ignore_worktree(tmp_path, monkeypatch):
    scenario = get_scenario("duplicate_retention")
    source = tmp_path / "source"
    with pytest.raises(ValueError, match="outside"):
        freeze_scenario_oracle(source / "hidden", scenario, read_roots=[source])
    frozen = freeze_scenario_oracle(tmp_path / "private", scenario, read_roots=[source])
    files = {**scenario.source_files, **scenario.public_test_files}
    commit = _repository(source, files)
    (source / "app.py").write_text("This dirty worktree is not the deliverable.")
    assert commit_files(source, commit, list(files)) == files
    from eidolon_cli import organization_project_runner as runner
    monkeypatch.setattr(runner, "run_project_tests", lambda *_: pytest.fail("Premature execution"))
    result = evaluate_delivered_commit(source, commit, frozen, app_completed=False,
                                      delivered_paths=list(files), read_roots=[source])
    assert result["semanticCorrect"] is None and result["status"] == "not_run"
    manifest, _ = read_frozen_oracle(frozen, read_roots=[source])
    assert manifest["expectedObservations"]
    target = frozen.directory / "test_trial_holdout.py"
    target.chmod(0o600)
    target.write_text("changed")
    with pytest.raises(ValueError, match="test bytes changed"):
        read_frozen_oracle(frozen, read_roots=[source])
    with pytest.raises(ValueError, match="manifest changed"):
        read_frozen_oracle(replace(frozen, manifest_sha256="0" * 64), read_roots=[source])


@pytest.mark.linux_only
@pytest.mark.parametrize("scenario_id,retention,correct,mutant", [
    ("duplicate_retention", "earliest",
     "def deduplicate(rows):\n    result = {}\n    for row in rows:\n        result.setdefault(row['id'], row)\n    return list(result.values())\n",
     "def deduplicate(rows):\n    return list(rows)\n"),
    ("duplicate_retention", "latest",
     "def deduplicate(rows):\n    return list({row['id']: row for row in rows}.values())\n",
     "def deduplicate(rows):\n    return list(rows)\n"),
    ("signed_bucket", "earliest", "def bucket(n, d):\n    return n // d\n",
     "def bucket(n, d):\n    return 0 if n == -31 else n // d\n"),
])
def test_real_holdout_rejects_public_passing_mutants_and_accepts_semantics(
        tmp_path, scenario_id, retention, correct, mutant):
    from eidolon_cli.organization_project_runner import ProjectExecutionGrant, probe_project_runner, run_project_tests
    probe = probe_project_runner()
    if probe["status"] != "passed":
        if os.environ.get("EIDOLON_REQUIRE_PROJECT_SANDBOX") == "1":
            pytest.fail("Required native sandbox unavailable: " + probe.get("reason", ""))
        pytest.skip("Real sandbox unavailable: " + probe.get("reason", ""))
    scenario = get_scenario(scenario_id, retention=retention)
    for number, content in enumerate((correct, mutant, "import unittest\nunittest.TestCase.assertEqual = lambda *a: None\n" + mutant)):
        source = tmp_path / f"source-{number}"
        frozen = freeze_scenario_oracle(tmp_path / f"private-{number}", scenario, read_roots=[source])
        files = {"app.py": content, **scenario.public_test_files}
        commit = _repository(source, files)
        public = run_project_tests([{"path": "root0/" + name, "content": value,
            "sha256": hashlib.sha256(value.encode()).hexdigest(), "revision": 0} for name, value in files.items()],
            ProjectExecutionGrant())
        assert public["status"] == "passed"
        result = evaluate_delivered_commit(source, commit, frozen, app_completed=True,
                                          delivered_paths=list(files), read_roots=[source])
        assert result["semanticCorrect"] is (number == 0)
        assert result["observationsMatch"] is (number == 0)
        assert result["execution"]["isolation"]["established"]


def test_forged_unittest_success_is_not_semantic_success(tmp_path, monkeypatch):
    from eidolon_cli import organization_project_runner as runner
    scenario = get_scenario("duplicate_retention")
    files = {**scenario.source_files, **scenario.public_test_files}
    source = tmp_path / "source"
    commit = _repository(source, files)
    frozen = freeze_scenario_oracle(tmp_path / "private", scenario, read_roots=[source])
    stdout = ""
    def forged(files, _grant):
        return {"status": "passed", "exitCode": 0, "testCount": 1, "skippedCount": 0,
                "isolation": {"established": True}, "snapshotSha256": runner.snapshot_digest(files), "stdout": stdout}
    monkeypatch.setattr(runner, "run_project_tests", forged)
    expected = oracle_for_scenario("duplicate_retention", "earliest")["expected_observations"]
    # This seam tests data grading only, not successful code execution. Actual
    # candidate behavior is calibrated exclusively by the real-sandbox test.
    for stdout, correct in [("", False), ("EIDOLON_TRIAL_OBSERVATIONS_V1:NaN", False),
                            ("EIDOLON_TRIAL_OBSERVATIONS_V1:" + json.dumps(expected), True)]:
        result = evaluate_delivered_commit(source, commit, frozen, app_completed=True,
                                          delivered_paths=list(files), read_roots=[source])
        assert result["execution"]["status"] == "passed"
        assert result["semanticCorrect"] is correct and result["observationsMatch"] is correct
    assert oracle_for_scenario("signed_bucket")["expected_test_count"] == 1
