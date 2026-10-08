"""Portable organization identities without policy, ledger history or activation."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path


PROJECTS_EXPORT_FILENAME = "organization-projects.json"
# Recovery archives can contain the entire ledger even when organization/ is omitted.
PROJECTS_PRIVATE_EXPORT_ROOTS = frozenset({"organization", "backups", "state-snapshots"})
_PROJECT_FIELDS = ("id", "root", "recipe", "team")


def stage_project_registry(profile_home: Path, staged: Path) -> None:
    """Read one SQLite snapshot without initializing or migrating the live store.

    Missing/pre-registry databases are normal. An unreadable current registry fails
    the export rather than publishing an archive that silently lost project identities.
    Previously imported metadata stays available when there are no saved identities.
    """
    database = profile_home / "organization" / "state.db"
    if not database.exists():
        return
    try:
        with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            # Schema and rows must come from the same committed read snapshot. Opening
            # OrganizationStore here would adopt policy and create/migrate schema.
            conn.execute("BEGIN")
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='organization_project_registry'"
            ).fetchone()
            if exists is None:
                return
            rows = conn.execute("SELECT id, project FROM organization_project_registry ORDER BY id").fetchall()
    except sqlite3.Error as exc:
        raise ValueError("Cannot export organization project identities: the registry could not be read") from exc

    projects = []
    for identifier, raw in rows:
        try:
            source = json.loads(raw)
            # An allow-list also prevents future ledger-only fields leaking into exports.
            project = {key: source[key] for key in _PROJECT_FIELDS}
            if project["id"] != identifier or any(not isinstance(value, str) for value in project.values()):
                raise ValueError("Invalid project identity")
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Cannot export organization project identities: a registry entry is invalid") from exc
        projects.append(project)
    if not projects:
        return

    target = staged / PROJECTS_EXPORT_FILENAME
    # A profile can hold a symlink at this name. Never write through it into live state.
    if target.is_symlink():
        target.unlink()
    target.write_text(json.dumps({
        "format": "eidolon-organization-projects", "version": 1,
        "requiresOwnerReview": True, "projects": projects,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
