"""Guided project identity drafts restricted to existing configured authority.

No file or policy write is exposed until configuration adoption supports a
crash-safe cross-process transaction. Owners receive only a validated list item
for explicit review, never new filesystem roots, tool grants, or credentials.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import sqlite3

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_policy import resolve_settings
from eidolon_cli.organization_projects import parse_projects


def _revision(content, settings, conn):
    policy = conn.execute('SELECT generation,fingerprint FROM organization_policy WHERE id=1').fetchone() if conn else None
    return hashlib.sha256(content + json.dumps([tuple(policy) if policy else None, asdict(settings)], sort_keys=True).encode()).hexdigest()


def _blocked(conn):
    if conn is None:
        return False
    return (conn.execute("SELECT 1 FROM objectives o LEFT JOIN objective_control c ON c.objective_id=o.id "
                         "WHERE o.cancelled=0 AND (c.status IS NULL OR c.status NOT IN ('accepted','legacy_completed')) LIMIT 1").fetchone() is not None
            or conn.execute("SELECT 1 FROM requests WHERE status NOT IN ('completed','cancelled') LIMIT 1").fetchone() is not None)


def _managed():
    from eidolon_cli import config
    return config.is_managed() or config.managed_scope.is_key_managed('organization.projects')


def _config_content(path):
    import yaml
    try:
        content = path.read_bytes() if path.exists() else b''
        parsed = yaml.safe_load(content)
        if parsed is not None and not isinstance(parsed, dict):
            raise ValueError('Profile configuration must be a YAML mapping; review it before preparing a repository draft')
        return content, parsed or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError('Profile configuration cannot be read safely; review its YAML before preparing a repository draft') from exc


@contextmanager
def _context():
    from eidolon_constants import get_eidolon_home
    from eidolon_cli import config
    with config._CONFIG_LOCK:
        home = get_eidolon_home()
        content, raw = _config_content(home / 'config.yaml')
        # Reuse profile-scoped expansion and managed-overlay precedence, but
        # not the recovery-oriented config loader (which can create backups).
        expanded, _ = config._merge_managed_overlay(config._expand_env_vars(
            {'organization': raw.get('organization', {})}))
        settings = OrganizationSettings.from_config(expanded)
        path = home / 'organization' / 'state.db'
        if not path.exists():
            yield content, settings, None
            return
        # Do not construct OrganizationStore or get_service here: their cold
        # startup adopts file policy and may fence live claims on another host.
        conn = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute('BEGIN')
            yield content, resolve_settings(conn, settings), conn
        finally:
            conn.close()


def _view(content, settings, conn):
    blockers = []
    if _blocked(conn):
        blockers.append('active_objectives')
    if _managed():
        blockers.append('managed_configuration')
    return {
        'version': 1, 'revision': _revision(content, settings, conn),
        'projects': [asdict(project) for project in settings.projects],
        'roots': [f'root{index}' for index in range(len(settings.read_roots))],
        'recipes': [{'id': grant.id, 'root': grant.execution['root']} for grant in settings.project_grants],
        'teams': sorted({settings.team, *(member.team for member in settings.roster or ())}),
        'blocked': bool(blockers), 'blockers': blockers,
    }


def project_setup():
    with _context() as (content, settings, conn):
        return _view(content, settings, conn)


def project_draft(project, expected_revision):
    """Validate one list item for owner review; never persist or activate it.

    File persistence is deliberately absent: serialized policy observers do not
    make filesystem replacement and ledger commits atomic. A durable pending
    intent and recovery protocol is still required before enabling writes.
    """
    import yaml
    if not isinstance(expected_revision, str) or len(expected_revision) != 64:
        raise ValueError('Refresh project setup before preparing a draft; expectedRevision is required')
    with _context() as (content, settings, conn):
        revision = _revision(content, settings, conn)
        if expected_revision != revision:
            raise ValueError('Project configuration changed; refresh setup and review your selection before preparing a draft')
        if _managed():
            raise ValueError('Project configuration is managed and cannot be changed here')
        if _blocked(conn):
            raise ValueError('Finish or cancel open objectives before configuring a project; their approved bindings must stay unchanged')
        existing = [asdict(item) for item in settings.projects]
        parse_projects([*existing, project], settings.read_roots, settings.project_grants)
        if project['team'] not in _view(content, settings, conn)['teams']:
            raise ValueError('Project team must select an existing configured team')
        # Emit only the newly validated identity. Never reveal private paths,
        # existing configuration, grant internals, or provider credentials.
        return {'version': 1, 'revision': revision, 'project': dict(project),
                'yaml': yaml.safe_dump([project], sort_keys=False, allow_unicode=True)}
