"""Portable retained ledgers preserve WAL evidence without carrying credentials."""
import json
import shutil
import sqlite3

import pytest

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_store import OrganizationStore
from scripts.organization_trial_evidence import backup_ledger, retain_trial_evidence, verify_trial_artifact
from scripts.organization_trial_oracle import freeze_scenario_oracle, git_bytes
from scripts.organization_trial_scenarios import get_scenario


def _fixture(tmp_path):
    scenario = get_scenario("duplicate_retention")
    source = tmp_path / "source"
    source.mkdir()
    for name, content in {**scenario.source_files, **scenario.public_test_files}.items():
        (source / name).write_text(content)
    git_bytes(source, "init", "--initial-branch=main")
    git_bytes(source, "add", ".")
    git_bytes(source, "-c", "user.name=Test", "-c", "user.email=test@localhost", "commit", "-m", "Fixture")
    commit = git_bytes(source, "rev-parse", "HEAD").decode().strip()
    frozen = freeze_scenario_oracle(tmp_path / "private", scenario, read_roots=[source])
    store = OrganizationStore(tmp_path / "profile" / "state.db", OrganizationSettings(read_roots=(str(source),)))
    store.create_objective("Synthetic evidence", idempotency_key="once", delivery_mode="managed_artifact")
    return dict(store=store, source=source, scenario=scenario, frozen_oracle=frozen,
                original_commit=commit, delivered_commit=None, report={"status": "not_run"}, service_stopped=True)


def test_online_backup_and_portable_artifact_keep_exact_committed_evidence(tmp_path):
    # An open WAL writer makes a raw state.db copy stale. Backup sees committed rows.
    database = tmp_path / "live.db"
    with sqlite3.connect(database) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE receipts(value TEXT)")
        conn.commit()
        conn.execute("INSERT INTO receipts VALUES ('retained WAL receipt')")
        conn.commit()
        backup_ledger(database, tmp_path / "backup.db")
        with sqlite3.connect(tmp_path / "backup.db") as copied:
            assert copied.execute("SELECT value FROM receipts").fetchone()[0] == "retained WAL receipt"
    kwargs = _fixture(tmp_path)
    target = tmp_path / "audit"
    receipt = retain_trial_evidence(target, **kwargs)
    assert receipt["bundleRestoreVerified"] is True
    exports = json.loads((target / "ledger-evidence.json").read_bytes())
    assert exports["snapshot"]["objectives"][0]["title"] == "Synthetic evidence"
    assert exports["executionAudit"] and exports["toolReceipts"]
    shutil.rmtree(kwargs["source"])
    shutil.rmtree(tmp_path / "profile")
    moved = tmp_path / "relocated"
    target.rename(moved)
    assert verify_trial_artifact(moved)["scenarioSha256"] == kwargs["frozen_oracle"].scenario_sha256
    report = moved / "report.json"
    report.chmod(0o600)
    report.write_text("{}")
    with pytest.raises(ValueError, match="hash changed"):
        verify_trial_artifact(moved)


def test_retention_refuses_secrets_running_service_and_overwrite(tmp_path):
    kwargs = _fixture(tmp_path)
    for number, changes, pattern in [
        (0, {"service_stopped": False}, "Stop"),
        (1, {"report": {"message": "known-secret-value"}, "forbidden_values": ("known-secret-value",)}, "Credential"),
        (2, {"report": {"message": "Authorization: Bearer leaked-token-value"}}, "Credential"),
    ]:
        target = tmp_path / f"refused-{number}"
        with pytest.raises(ValueError, match=pattern):
            retain_trial_evidence(target, **{**kwargs, **changes})
        assert not target.exists()
        assert not list(tmp_path.glob(".trial-audit-*"))
    target = tmp_path / "sealed"
    retain_trial_evidence(target, **kwargs)
    with pytest.raises(ValueError, match="write-once"):
        retain_trial_evidence(target, **kwargs)
