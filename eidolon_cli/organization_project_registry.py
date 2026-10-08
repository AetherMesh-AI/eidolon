"""Owner project identities in the ledger, never new filesystem authority.

The ledger owns registration and receipts atomically. YAML remains the source
of grants. Its independently edited bytes are observed, never overwritten.
"""
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path

from eidolon_cli.organization_projects import _binding, parse_projects

REGISTRY_SCHEMA = """
CREATE TABLE IF NOT EXISTS organization_project_registry (
 id TEXT PRIMARY KEY, project TEXT NOT NULL, binding_hash TEXT NOT NULL,
 created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS organization_project_registration_receipts (
 idempotency_key TEXT PRIMARY KEY, input_hash TEXT NOT NULL, result TEXT NOT NULL,
 created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS organization_project_registry_conflicts (
 id INTEGER PRIMARY KEY CHECK(id=1), reasons TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS request_requires_valid_project_registry
BEFORE UPDATE OF status ON requests WHEN NEW.status='running' AND EXISTS (
 SELECT 1 FROM organization_project_registry_conflicts WHERE id=1)
BEGIN SELECT RAISE(ABORT, 'Organization project registration requires repair'); END;
"""


def registry_rows(conn):
    # Setup's read-only view also supports a ledger created by an older runtime.
    if conn is None or conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='organization_project_registry'").fetchone() is None:
        return []
    return conn.execute('SELECT * FROM organization_project_registry ORDER BY id').fetchall()


def binding_hash(project, settings, *, canonical_root=None):
    binding = _binding(project, settings)
    binding['readRoot'] = canonical_root if canonical_root is not None else str(Path(binding['readRoot']).resolve())
    return hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()


def resolve_registry(conn, settings):
    """Merge exactly once logically, retaining raw-vs-effective provenance.

    Config parsing never accepts these provenance fields from YAML. An effective
    seed can therefore shed only its own prior ledger layer, whereas an identical
    ID in an actual YAML seed remains a real conflict.
    """
    rows = registry_rows(conn)
    inherited = set(settings.ledger_project_ids)
    projects = [asdict(project) for project in settings.projects if project.id not in inherited]
    conflicts, ids = [], []
    teams = {settings.team, *(member.team for member in settings.roster or ())}
    for row in rows:
        try:
            project = json.loads(row['project'])
            if not isinstance(project, dict) or project.get('id') != row['id']:
                raise ValueError('Invalid retained identity')
            parsed = parse_projects([*projects, project], settings.read_roots, settings.project_grants)
            if project['team'] not in teams or binding_hash(parsed[-1], settings) != row['binding_hash']:
                raise ValueError('Retained binding no longer matches source authority')
            projects.append(project)
            ids.append(row['id'])
        except (ValueError, TypeError, KeyError, OSError):
            # No paths, recipe internals, source values or parser excerpts escape.
            conflicts.append(f"Project {row['id']} conflicts with current configuration; restore its approved root, recipe and team or remove the conflicting YAML identity")
    return replace(settings, projects=parse_projects(projects, settings.read_roots, settings.project_grants),
                   ledger_project_ids=tuple(ids), project_registry_conflicts=tuple(conflicts))


def adopt_registry_status(conn, settings):
    before = conn.execute('SELECT reasons FROM organization_project_registry_conflicts WHERE id=1').fetchone()
    if settings.project_registry_conflicts:
        reasons = json.dumps(settings.project_registry_conflicts)
        conn.execute('INSERT INTO organization_project_registry_conflicts VALUES (1,?) ON CONFLICT(id) DO UPDATE SET reasons=excluded.reasons', (reasons,))
        return before is None or before['reasons'] != reasons
    conn.execute('DELETE FROM organization_project_registry_conflicts WHERE id=1')
    return before is not None
