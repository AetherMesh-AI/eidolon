"""Explicit owner-reviewed folder roots, below administrator configuration.

Aliases are append-only and pinned to the exact source prefix. No tools, staff
capabilities, execution recipes or scheduling are granted by this layer.
"""
from dataclasses import replace
import json
import os
from pathlib import Path

ROOT_GRANT_TABLES = {'organization_root_grants', 'organization_folder_receipts', 'organization_root_revocations', 'organization_root_revocation_reviews'}
ROOT_GRANT_SCHEMA = '''
CREATE TABLE IF NOT EXISTS organization_root_grants (
 alias TEXT PRIMARY KEY, path TEXT NOT NULL, source_prefix TEXT NOT NULL,
 device INTEGER NOT NULL, inode INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS organization_folder_receipts (
 idempotency_key TEXT PRIMARY KEY, organization_id TEXT NOT NULL,
 owner_id TEXT NOT NULL, input_hash TEXT NOT NULL, result_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS organization_root_revocations (
 alias TEXT PRIMARY KEY REFERENCES organization_root_grants(alias),
 organization_id TEXT NOT NULL, owner_id TEXT NOT NULL, grant_hash TEXT NOT NULL,
 idempotency_key TEXT NOT NULL UNIQUE, input_hash TEXT NOT NULL, receipt TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS organization_root_revocation_reviews (
 alias TEXT PRIMARY KEY REFERENCES organization_root_revocations(alias), owner_id TEXT NOT NULL,
 review_hash TEXT NOT NULL, receipt TEXT NOT NULL, created REAL NOT NULL);
CREATE TRIGGER IF NOT EXISTS organization_root_revocation_reviews_no_update
 BEFORE UPDATE ON organization_root_revocation_reviews BEGIN SELECT RAISE(ABORT,'Revocation review is immutable'); END;
CREATE TRIGGER IF NOT EXISTS organization_root_revocation_reviews_no_delete
 BEFORE DELETE ON organization_root_revocation_reviews BEGIN SELECT RAISE(ABORT,'Revocation review is immutable'); END;
CREATE TRIGGER IF NOT EXISTS organization_root_revocations_no_update
 BEFORE UPDATE ON organization_root_revocations BEGIN SELECT RAISE(ABORT,'Root revocation is immutable'); END;
CREATE TRIGGER IF NOT EXISTS organization_root_revocations_no_delete
 BEFORE DELETE ON organization_root_revocations BEGIN SELECT RAISE(ABORT,'Root revocation is immutable'); END;
CREATE TRIGGER IF NOT EXISTS organization_root_grants_no_update
 BEFORE UPDATE ON organization_root_grants BEGIN SELECT RAISE(ABORT,'Folder grant is immutable'); END;
CREATE TRIGGER IF NOT EXISTS organization_root_grants_no_delete
 BEFORE DELETE ON organization_root_grants BEGIN SELECT RAISE(ABORT,'Folder grant is immutable'); END;
CREATE TRIGGER IF NOT EXISTS organization_folder_receipts_no_update
 BEFORE UPDATE ON organization_folder_receipts BEGIN SELECT RAISE(ABORT,'Folder receipt is immutable'); END;
CREATE TRIGGER IF NOT EXISTS organization_folder_receipts_no_delete
 BEFORE DELETE ON organization_folder_receipts BEGIN SELECT RAISE(ABORT,'Folder receipt is immutable'); END;
'''


def roots_managed():
    from eidolon_cli import managed_scope
    from eidolon_cli.config_primitives import managed_install_system
    from eidolon_constants import get_eidolon_home
    managed_scope.invalidate_managed_cache()
    if managed_install_system(get_eidolon_home()) is not None:
        return True
    return any(key == 'organization' or key in {'organization.read_roots', 'organization.projects'}
               or key.startswith(('organization.read_roots.', 'organization.projects.'))
               for key in managed_scope.managed_config_keys())


def directory_identity(path):
    from tools.organization_file_read import _require_posix_support, _open_root, _profile_credential_prefixes
    _require_posix_support()
    if not isinstance(path, str) or path == '/':
        raise ValueError('Choose an existing project folder')
    try:
        fd = _open_root(path, _profile_credential_prefixes())
        try:
            info = os.fstat(fd)
            return {'device': info.st_dev, 'inode': info.st_ino}
        finally:
            os.close(fd)
    except OSError:
        raise ValueError('Project folder is missing, inaccessible, or contains a symbolic link') from None


def root_rows(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='organization_root_grants'").fetchone():
        return []
    return conn.execute('SELECT * FROM organization_root_grants ORDER BY alias').fetchall()


def source_roots(settings):
    inherited = settings.ledger_read_roots
    if inherited:
        if tuple(settings.read_roots[-len(inherited):]) != tuple(inherited):
            raise ValueError('Folder grant provenance requires owner repair')
        return tuple(settings.read_roots[:-len(inherited)])
    return tuple(settings.read_roots)


def check_new_root(path, settings):
    identity = directory_identity(path)
    candidate = Path(path)
    for index, current in enumerate(settings.read_roots):
        other = Path(current)
        retained = expected_root_identities(settings).get(f'root{index}')
        if f'root{index}' in settings.revoked_root_aliases:
            other_identity = {'device': retained[0], 'inode': retained[1]} if retained else None
        else:
            other_identity = directory_identity(current)
        if candidate == other or candidate in other.parents or other in candidate.parents or other_identity == identity:
            raise ValueError('Project folders must be distinct and must not contain one another')
    if len(settings.read_roots) >= 8:
        raise ValueError('At most eight project roots are supported')
    return identity


def resolve_root_grants(conn, settings):
    rows = root_rows(conn)
    prefix = source_roots(settings)
    if not rows:
        if settings.ledger_read_roots:
            raise ValueError('Folder grant records require owner repair')
        return settings
    if roots_managed():
        raise ValueError('Folder grants conflict with administrator configuration')
    revoked = revoked_aliases(conn)
    roots = list(prefix)
    for row in rows:
        if (json.loads(row['source_prefix']) != list(prefix) or row['alias'] != f'root{len(roots)}'
                or len(roots) >= 8):
            raise ValueError('Folder source aliases changed; restore the approved configuration')
        if row['alias'] in revoked:
            roots.append(row['path'])
            continue
        identity = directory_identity(row['path'])
        if identity != {'device': row['device'], 'inode': row['inode']}:
            raise ValueError('Approved folder identity changed; owner review is required')
        check_new_root(row['path'], replace(settings, read_roots=tuple(roots), revoked_root_aliases=revoked,
            ledger_read_roots=tuple(roots[len(prefix):]),
            ledger_root_identities=tuple((prior['device'], prior['inode']) for prior in rows[:len(roots)-len(prefix)])))
        roots.append(row['path'])
    return replace(settings, read_roots=tuple(roots), ledger_read_roots=tuple(roots[len(prefix):]),
                   ledger_root_identities=tuple((row['device'], row['inode']) for row in rows), revoked_root_aliases=revoked)


def expected_root_identities(settings):
    start = len(settings.read_roots) - len(settings.ledger_read_roots)
    return {f'root{start+i}': identity for i, identity in enumerate(settings.ledger_root_identities)}


def revoked_aliases(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='organization_root_revocations'").fetchone():
        return ()
    return tuple(row[0] for row in conn.execute('SELECT alias FROM organization_root_revocations ORDER BY alias'))


def active_root_aliases(settings):
    return tuple(f'root{i}' for i in range(len(settings.read_roots)) if f'root{i}' not in settings.revoked_root_aliases)


def pending_revocations(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='organization_root_revocation_reviews'").fetchone():
        return False
    return bool(conn.execute('SELECT 1 FROM organization_root_revocations r LEFT JOIN organization_root_revocation_reviews v ON v.alias=r.alias WHERE v.alias IS NULL').fetchone())
