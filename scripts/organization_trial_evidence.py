"""Portable, hash-sealed audit artifacts without profile secrets or raw logs.

A SQLite online backup gives one consistent ledger even while WAL exists. Exact
read APIs are then evaluated against that copy. Known secret or credential-like
content aborts retention instead of silently changing evidence and its hashes.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile

from scripts.organization_trial_oracle import (
    canonical, commit_files, git_bytes, read_frozen_oracle, require_outside_roots,
    scenario_data, scenario_digest, source_manifest,
)


_CREDENTIAL = re.compile(
    rb"(?:authorization\s*[:=]\s*(?:bearer|basic)\s+\S+|"
    rb"(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\s*[\"']?\s*[:=]\s*[\"']?[^\s\"',}]{8,}|"
    rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    rb"(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}))", re.IGNORECASE)


def _scan(data, forbidden_values):
    if any(secret in data for secret in forbidden_values) or _CREDENTIAL.search(data):
        raise ValueError("Credential-like content detected; exact audit retention was refused")


def _json_file(directory, name, value, forbidden_values):
    encoded = canonical(value) + b"\n"
    _scan(encoded, forbidden_values)
    (directory / name).write_bytes(encoded)


def backup_ledger(source, destination):
    """SQLite's backup API includes committed WAL pages; copying state.db does not."""
    source, destination = Path(source).resolve(strict=True), Path(destination)
    if destination.exists():
        raise ValueError("Audit ledger destination already exists")
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=10) as incoming:
        with sqlite3.connect(destination, timeout=10) as outgoing:
            incoming.backup(outgoing, pages=256)
            if outgoing.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Audit SQLite backup failed its integrity check")
            outgoing.execute("PRAGMA journal_mode=DELETE")


def _read_only_store(store, path):
    from eidolon_cli.organization_store import OrganizationStore

    @contextmanager
    def connect():
        conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    copied = object.__new__(OrganizationStore)
    copied.path, copied.settings = path, store.settings
    copied._policy_generation, copied._policy_fingerprint = store._policy_generation, store._policy_fingerprint
    copied._connect = connect
    return copied


def _ledger_exports(store, forbidden_values):
    with store._connect() as conn:
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            for row in conn.execute("SELECT * FROM " + quoted):
                for value in row:
                    if isinstance(value, (str, bytes)):
                        _scan(value.encode("utf-8") if isinstance(value, str) else value, forbidden_values)
        requests = [row[0] for row in conn.execute("SELECT id FROM requests ORDER BY created,id")]
        # Public snapshots deliberately show only latest evidence and recent runs.
        # Enumerate all immutable artifact IDs from the retained ledger instead.
        selections = (("evidence", "id"), ("objective_deliverables", "id"),
                      ("objective_project_validations", "id"), ("project_run_results", "run_id"))
        ids = []
        for table, column in selections:
            if table in tables:
                ids.extend(row[0] for row in conn.execute(f'SELECT "{column}" FROM "{table}" ORDER BY "{column}"'))
        ids.extend("project_source_" + row[0] for row in conn.execute(
            "SELECT request_id FROM project_source_receipts ORDER BY request_id"))
    return {"snapshot": store.snapshot(), "evidence": [store.evidence(identifier) for identifier in dict.fromkeys(ids)],
            "toolReceipts": {identifier: store.tool_receipts(identifier) for identifier in requests},
            "executionAudit": {identifier: store.execution_audit(identifier) for identifier in requests}}


def _bundle_and_verify(source, destination, commits, paths, forbidden_values):
    reachable = git_bytes(source, "rev-list", "--all").decode().splitlines()
    if not reachable or len(reachable) > 64 or not set(commits) <= set(reachable):
        raise ValueError("Trial bundle requires bounded reachable original/delivered commits")
    for commit in reachable:
        for content in commit_files(source, commit, paths).values():
            _scan(content.encode("utf-8"), forbidden_values)
        _scan(git_bytes(source, "cat-file", "commit", commit), forbidden_values)
    refs = git_bytes(source, "for-each-ref", "--format=%(refname) %(objectname)").decode().splitlines()
    if any(not line.startswith("refs/heads/") for line in refs):
        raise ValueError("Disposable trial bundle may contain only local branch refs")
    git_bytes(source, "bundle", "create", str(destination), "--all")
    with tempfile.TemporaryDirectory(prefix="eidolon-trial-restore-") as temporary:
        restored = Path(temporary)
        git_bytes(restored, "init", "--bare")
        git_bytes(restored, "bundle", "verify", str(destination))
        git_bytes(restored, "fetch", "--no-tags", str(destination), "refs/heads/*:refs/heads/*")
        observed = git_bytes(restored, "for-each-ref", "--format=%(refname) %(objectname)").decode().splitlines()
        if observed != refs:
            raise ValueError("Restored source bundle has different branch identities")
        for commit in commits:
            if commit_files(restored, commit, paths) != commit_files(source, commit, paths):
                raise ValueError("Restored source bundle changed the exact project bytes")
    return {"verified": True, "method": "fresh_bare_repository_fetch_without_checkout",
            "refs": refs, "verifiedCommits": commits,
            "sha256": hashlib.sha256(destination.read_bytes()).hexdigest()}


def verify_trial_artifact(directory):
    """Read-only validation; a relocated artifact needs no original profile/repo."""
    root = Path(directory)
    manifest = json.loads((root / "artifact-manifest.json").read_bytes())
    expected = {row["path"] for row in manifest["files"]}
    observed = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    if observed != expected | {"artifact-manifest.json"}:
        raise ValueError("Retained audit file set changed")
    for row in manifest["files"]:
        path = root / row["path"]
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Retained audit path is not a regular local file")
        data = path.read_bytes()
        if len(data) != row["bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise ValueError("Retained audit file hash changed")
    database = root / "organization.sqlite3"
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro&immutable=1", uri=True) as conn:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Retained audit database is corrupt")
    return manifest


def retain_trial_evidence(artifact_dir, *, store, source, scenario, frozen_oracle,
                          original_commit, delivered_commit, report, service_stopped,
                          forbidden_values=()):
    """Publish one exact portable artifact only after all workers are stopped.

    Pass the active credential as a forbidden value, not inside report/scenario.
    A rejection removes staging output. It never redacts immutable ledger bytes.
    """
    if service_stopped is not True:
        raise ValueError("Stop the trial service before sealing its audit artifact")
    secrets = tuple(value.encode("utf-8") if isinstance(value, str) else value
                    for value in forbidden_values if value)
    if any(not isinstance(value, bytes) for value in secrets):
        raise ValueError("Secret omission checks require text or bytes")
    read_roots = store.settings.read_roots
    target = require_outside_roots(artifact_dir, read_roots)
    if target.exists():
        raise ValueError("Audit artifacts are write-once; choose a new destination")
    spec = scenario_data(scenario)
    if scenario_digest(spec) != frozen_oracle.scenario_sha256:
        raise ValueError("Scenario changed after the independent oracle was frozen")
    oracle_manifest, oracle_files = read_frozen_oracle(frozen_oracle, read_roots=read_roots)
    paths = list(spec["source_files"]) + list(spec["public_test_files"])
    before = commit_files(source, original_commit, paths)
    if before != {**spec["source_files"], **spec["public_test_files"]}:
        raise ValueError("Original Git commit disagrees with the frozen scenario inputs")
    after = commit_files(source, delivered_commit, paths) if delivered_commit else None
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".trial-audit-", dir=target.parent))
    try:
        backup_ledger(store.path, stage / "organization.sqlite3")
        exports = _ledger_exports(_read_only_store(store, stage / "organization.sqlite3"), secrets)
        _json_file(stage, "scenario.json", spec, secrets)
        _json_file(stage, "oracle-manifest.json", oracle_manifest, secrets)
        _json_file(stage, "oracle-files.json", oracle_files, secrets)
        _json_file(stage, "report.json", report, secrets)
        _json_file(stage, "ledger-evidence.json", exports, secrets)
        _json_file(stage, "source-manifests.json", {
            "originalCommit": original_commit, "deliveredCommit": delivered_commit,
            "before": source_manifest(before), "after": source_manifest(after) if after is not None else None}, secrets)
        commits = list(dict.fromkeys([original_commit] + ([delivered_commit] if delivered_commit else [])))
        restored = _bundle_and_verify(source, stage / "source.bundle", commits, paths, secrets)
        _json_file(stage, "bundle-restore-check.json", restored, secrets)
        # Scan free pages too: exact database retention must not preserve a
        # credential that was removed from a live logical row before backup.
        _scan((stage / "organization.sqlite3").read_bytes(), secrets)
        files = [{"path": path.relative_to(stage).as_posix(), "bytes": path.stat().st_size,
                  "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                 for path in sorted(stage.iterdir())]
        manifest = {"schemaVersion": 1, "scenarioSha256": frozen_oracle.scenario_sha256,
                    "oracleSha256": frozen_oracle.manifest_sha256, "files": files,
                    "retention": "consistent_sqlite_backup_and_exact_read_api_exports",
                    "immutability": "write_once_destination_read_only_files_and_sha256_manifest",
                    "excluded": ["profile configuration", "environment", "provider wire requests", "raw logs", "credentials"],
                    "limitations": "Secret checks reject supplied secret bytes and recognizable credential patterns; "
                        "unknown, transformed or unusually formatted secrets cannot be guaranteed absent. "
                        "Hashes and read-only modes expose accidental changes; they are not an external signature or "
                        "protection against an owner rewriting both files and manifest. "
                        "Usage records are admission reservations, not a provider invoice. "
                        "Historical absolute read-root paths are retained as provenance; replay uses the bundled source."}
        _json_file(stage, "artifact-manifest.json", manifest, secrets)
        verify_trial_artifact(stage)
        for path in stage.iterdir():
            path.chmod(0o444)
        # The destination is user-selected and checked above; never merge into
        # or overwrite an existing artifact directory.
        if target.exists():
            raise ValueError("Audit destination appeared while sealing")
        stage.rename(target)
        return {"directory": str(target), "manifestSha256": hashlib.sha256(
            (target / "artifact-manifest.json").read_bytes()).hexdigest(),
            "bundleRestoreVerified": True, "files": files}
    finally:
        if stage.exists():
            shutil.rmtree(stage)
