"""Explicit owner-reviewed folder roots, below administrator configuration.

Aliases are append-only and pinned to the exact source prefix. No tools, staff
capabilities, execution recipes or scheduling are granted by this layer.
"""
from dataclasses import replace
import json
import os
from pathlib import Path

ROOT_GRANT_TABLES = {'organization_root_grants', 'organization_folder_receipts'}
ROOT_GRANT_SCHEMA = '''
CREATE TABLE IF NOT EXISTS organization_root_grants (
 alias TEXT PRIMARY KEY, path TEXT NOT NULL, source_prefix TEXT NOT NULL,
 device INTEGER NOT NULL, inode INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS organization_folder_receipts (
 idempotency_key TEXT PRIMARY KEY, organization_id TEXT NOT NULL,
 owner_id TEXT NOT NULL, input_hash TEXT NOT NULL, result_json TEXT NOT NULL);
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
    for current in settings.read_roots:
        other = Path(current)
        if candidate == other or candidate in other.parents or other in candidate.parents or directory_identity(current) == identity:
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
    roots = list(prefix)
    for row in rows:
        if (json.loads(row['source_prefix']) != list(prefix) or row['alias'] != f'root{len(roots)}'
                or len(roots) >= 8):
            raise ValueError('Folder source aliases changed; restore the approved configuration')
        identity = directory_identity(row['path'])
        if identity != {'device': row['device'], 'inode': row['inode']}:
            raise ValueError('Approved folder identity changed; owner review is required')
        check_new_root(row['path'], replace(settings, read_roots=tuple(roots)))
        roots.append(row['path'])
    return replace(settings, read_roots=tuple(roots), ledger_read_roots=tuple(roots[len(prefix):]),
                   ledger_root_identities=tuple((row['device'], row['inode']) for row in rows))


def expected_root_identities(settings):
    start = len(settings.read_roots) - len(settings.ledger_read_roots)
    return {f'root{start+i}': identity for i, identity in enumerate(settings.ledger_root_identities)}
