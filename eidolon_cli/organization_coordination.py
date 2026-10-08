"""Durable edit-task ownership, separate from renewable execution leases.

Reserve the whole declared set in the claim transaction, and keep it across
review and bounded revision. A waiting task owns nothing: there is no partial
acquisition or lock-order cycle. Uncertain work keeps ownership until the owner
resolves or cancels it; lease expiry alone never means a writer has stopped.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path
import time
import unicodedata


COORDINATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_write_scopes (
 task_id TEXT PRIMARY KEY REFERENCES tasks(id), paths TEXT NOT NULL,
 resources TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS task_reservations (
 task_id TEXT PRIMARY KEY REFERENCES tasks(id), acquired REAL NOT NULL);
"""


def normalize_write_paths(value, kind):
    if value is None:
        return None
    if kind != 'work.edit':
        raise ValueError('Only work.edit tasks may declare writePaths')
    if (not isinstance(value, list) or not 1 <= len(value) <= 64
            or any(not isinstance(path, str) for path in value)
            or len(set(value)) != len(value)):
        raise ValueError('writePaths must list 1–64 unique exact root-alias file paths')
    from eidolon_cli.organization_edits import _alias_path
    from tools.organization_file_read import _check_lexical_path
    for path in value:
        _alias_path(path)
        _check_lexical_path(path.split('/')[1:], ())
    return sorted(value)


def _metadata_path(path, prefix=''):
    """Read only bounded Git indirection metadata, never execute Git/config."""
    flags = os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError('Git ownership metadata must be a regular file')
        raw = b''
        while len(raw) <= 4096:
            chunk = os.read(descriptor, 4097 - len(raw))
            if not chunk:
                break
            raw += chunk
        if metadata.st_size > 4096 or len(raw) > 4096:
            raise ValueError('Git ownership metadata is too large')
    finally:
        os.close(descriptor)
    value = raw.decode('utf-8').strip()
    if prefix:
        if not value.startswith(prefix):
            raise ValueError('Git ownership metadata is invalid')
        value = value[len(prefix):].strip()
    if not value or '\n' in value or '\x00' in value:
        raise ValueError('Git ownership metadata is invalid')
    return (path.parent / value).resolve()


def resource_identity(root, relative):
    """Aliases/symlinks and Git worktrees name the same logical file resource.

    Git common-directory identity plus a repository-relative path unifies
    linked worktrees. Outside Git, the canonical filesystem path is the identity.
    This is scheduling metadata only, never a read/write permission grant.
    """
    path = (Path(root) / relative).resolve()
    for parent in path.parents:
        marker = parent / '.git'
        if marker.is_dir() or marker.is_file():
            gitdir = marker.resolve() if marker.is_dir() else _metadata_path(marker, 'gitdir:')
            common = _metadata_path(gitdir / 'commondir') if (gitdir / 'commondir').is_file() else gitdir
            identity = common.stat()
            if not stat.S_ISDIR(identity.st_mode):
                raise ValueError('Git ownership common directory must be a directory')
            # Case-folding is deliberately conservative on case-sensitive
            # volumes, and prevents split ownership on default macOS volumes.
            return [f'git:{identity.st_dev}:{identity.st_ino}', unicodedata.normalize('NFC', path.relative_to(parent).as_posix()).casefold()]
    return ['', unicodedata.normalize('NFC', os.path.normcase(str(path))).casefold()]


def _overlap(left, right):
    if not left or not right:  # A legacy edit has no trustworthy declared scope.
        return True
    return any(a[0] == b[0] and (a[1] == b[1] or a[1].startswith(b[1] + '/')
                               or b[1].startswith(a[1] + '/')) for a in left for b in right)


def ownership_blockers(conn, task_id):
    task = conn.execute('SELECT type FROM tasks WHERE id=?', (task_id,)).fetchone()
    if task['type'] != 'work.edit' or conn.execute(
            'SELECT 1 FROM task_reservations WHERE task_id=?', (task_id,)).fetchone():
        return []
    scope = conn.execute('SELECT resources FROM task_write_scopes WHERE task_id=?', (task_id,)).fetchone()
    resources = json.loads(scope['resources']) if scope else []
    return [row['id'] for row in conn.execute(
        "SELECT t.id,s.resources FROM task_reservations r JOIN tasks t ON t.id=r.task_id "
        "LEFT JOIN task_write_scopes s ON s.task_id=t.id WHERE t.status NOT IN ('completed','cancelled') AND t.id!=? "
        'ORDER BY r.acquired,t.id', (task_id,))
        if _overlap(resources, json.loads(row['resources']) if row['resources'] else [])]


def coordination_view(conn, task_id):
    task = conn.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
    scope = conn.execute('SELECT * FROM task_write_scopes WHERE task_id=?', (task_id,)).fetchone()
    if task['status'] in {'completed', 'cancelled'}:
        return {'state': 'released', 'reason': None, 'blockingTaskIds': []}
    dependencies = [ident for ident in json.loads(task['dependencies'])
                    if conn.execute('SELECT status FROM tasks WHERE id=?', (ident,)).fetchone()[0] != 'completed']
    # Include latent conflicts even while semantic dependencies are unfinished.
    # Otherwise a typed handoff can create a cycle that only appears later.
    blockers = ownership_blockers(conn, task_id)
    if dependencies or blockers:
        reason = ('Waiting for independently reviewed dependencies and conflicting file ownership.'
                  if dependencies and blockers else 'Waiting for independently reviewed dependencies.'
                  if dependencies else 'Waiting for conflicting file ownership to be released.')
        return {'state': 'waiting', 'reason': reason,
                'blockingTaskIds': list(dict.fromkeys(dependencies + blockers))}
    if conn.execute('SELECT 1 FROM task_reservations WHERE task_id=?', (task_id,)).fetchone():
        return {'state': 'reserved', 'reason': 'File ownership retained through review and application.',
                'blockingTaskIds': []}
    if task['type'] != 'work.edit' or scope:
        return {'state': 'ready', 'reason': None, 'blockingTaskIds': []}
    return {'state': 'unscoped',
            'reason': 'Legacy edit has unspecified paths; exclusive edit ownership is required.',
            'blockingTaskIds': []}


class OrganizationCoordinationStore:
    @staticmethod
    def _migrate_reservations(conn):
        # Pre-upgrade active edits may already have captured a base. Preserve
        # their conservative ownership instead of admitting a competing writer.
        conn.execute("INSERT OR IGNORE INTO task_reservations "
                     "SELECT t.id,MIN(r.created) FROM tasks t JOIN requests r ON r.task_id=t.id "
                     "WHERE t.type='work.edit' AND t.status NOT IN ('completed','cancelled') "
                     "AND r.attempts>0 GROUP BY t.id")

    def _set_write_scope(self, conn, task_id, specification):
        paths = normalize_write_paths(specification.get('writePaths'), specification['type'])
        if paths is not None:
            resources = self._write_resources(paths)
            conn.execute('INSERT INTO task_write_scopes VALUES (?,?,?)',
                         (task_id, json.dumps(paths), json.dumps(resources)))

    def _write_resources(self, paths):
        from eidolon_cli.organization_edits import _alias_path
        resources = []
        for path in paths:
            _, index = _alias_path(path)
            if index >= len(self.settings.read_roots):
                raise ValueError('writePaths must use an explicitly configured read root')
            try:
                resources.append(resource_identity(self.settings.read_roots[index], path.split('/', 1)[1]))
            except (OSError, RuntimeError, UnicodeError) as exc:
                raise ValueError('File ownership identity could not be established') from exc
        return resources

    @staticmethod
    def _coordination_ready(conn, request):
        return (not request['task_id'] or request['type'] != 'work.edit'
                or coordination_view(conn, request['task_id'])['state'] != 'waiting')

    def _validate_claim_write_scope(self, conn, request):
        if not request['task_id'] or request['type'] != 'work.edit':
            return True
        scope = conn.execute('SELECT * FROM task_write_scopes WHERE task_id=?', (request['task_id'],)).fetchone()
        try:
            if scope is not None and self._write_resources(json.loads(scope['paths'])) != json.loads(scope['resources']):
                raise ValueError('Declared file ownership changed; request a bounded replan before editing.')
        except ValueError as exc:
            self._pending(conn, request, str(exc))
            return False
        return True

    @staticmethod
    def _reserve_task(conn, request):
        if request['type'] == 'work.edit' and request['task_id']:
            conn.execute('INSERT OR IGNORE INTO task_reservations VALUES (?,?)', (request['task_id'], time.time()))

    def _verify_write_scope(self, conn, request, paths):
        scope = conn.execute('SELECT * FROM task_write_scopes WHERE task_id=?', (request['task_id'],)).fetchone()
        if scope is None:
            return
        declared = json.loads(scope['paths'])
        if not set(paths).issubset(declared):
            raise ValueError('Edit proposal exceeds the task writePaths; request a bounded replan')
        if self._write_resources(declared) != json.loads(scope['resources']):
            raise ValueError('Declared file ownership changed; request a bounded replan before editing')
