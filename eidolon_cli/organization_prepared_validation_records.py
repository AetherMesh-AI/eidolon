"""One-write static JSON validation reuse, never execution or proof authority.

Preload only immutable ledger records in a bounded read transaction. Every use
under the write lock still compares complete fresh SQL TEXT and its hash, then
returns a detached JSON value. Live authority and runtime evidence remain the
caller's obligation. Cleanup can preload grants without opening revoked roots.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import json
import re
import threading
import uuid


_SCOPE = ContextVar('prepared_static_record_operation', default=None)
_MAX_STATIC_RECORD_BYTES = 128 * 1024**2


@dataclass
class _StaticRecordToken:
    store: object
    objective_id: str
    projects: tuple
    include_current_registration: bool
    static_records: dict = field(default_factory=dict)
    static_bytes: int = 0
    connection: object = None
    database: str = ''
    thread: int = field(default_factory=threading.get_ident)
    phase: str = 'preflight'
    alive: bool = True


def _invalidate(token):
    token.alive = False
    token.static_records.clear()
    token.static_bytes = 0
    token.connection = None


def _require_no_finish_scope():
    from eidolon_cli import organization_prepared_validation_finish as finish
    if finish._ACTIVE.get() is not None or finish._PREFLIGHT.get() is not None:
        raise ValueError('Prepared static records require a nonnested operation')


def _require_token(token, store):
    if (type(token) is not _StaticRecordToken or not token.alive
            or token.store is not store or token.thread != threading.get_ident()
            or _SCOPE.get() is not token):
        raise ValueError('Prepared static record operation is expired, foreign or already consumed')


def _database(conn):
    return next((row[2] for row in conn.execute('PRAGMA database_list') if row[1] == 'main'), '')


def _preload(token, conn):
    from eidolon_cli.organization_prepared_validation_ledger import _binding
    for project in token.projects:
        _binding(conn, token.objective_id, project)
    if not token.include_current_registration:
        return
    from eidolon_cli.organization_prepared_validation_setup import (
        _TABLE, _RECEIPTS, _MAX_REGISTRATIONS, _sidecar_rows, _record,
    )
    from eidolon_cli.organization_prepared_validation_contract import _MAX_INTEGER
    from eidolon_cli.organization_prepared_validation_verification import _scalar_bounds
    # The sidecar's own validator checks global revision order. Fence its small
    # fields before that metadata read; candidate bodies stay SQL size-gated.
    _scalar_bounds(conn, _TABLE, '1=1', (), {
        'project_id': 64, 'organization_id': 36, 'owner_id': 36,
        'source_revision': 64, 'candidate_hash': 64, 'receipt_hash': 64})
    _scalar_bounds(conn, _RECEIPTS, '1=1', (), {'idempotency_key': 128, 'input_hash': 64})
    # INTEGER affinity alone also accepts unbounded TEXT/BLOB values. Reject
    # those in SQLite before _sidecar_rows materializes numeric metadata.
    maximum = _MAX_REGISTRATIONS + 8
    if (conn.execute(f'SELECT 1 FROM {_TABLE} WHERE '
            "typeof(sequence)<>'integer' OR sequence NOT BETWEEN 1 AND ? OR "
            "typeof(revision)<>'integer' OR revision NOT BETWEEN 1 AND ? OR "
            "typeof(enabled)<>'integer' OR enabled NOT IN (0,1) OR "
            "typeof(policy_generation)<>'integer' OR policy_generation NOT BETWEEN 1 AND ? LIMIT 1",
            (maximum, maximum, _MAX_INTEGER)).fetchone()
            or conn.execute(f'SELECT 1 FROM {_RECEIPTS} WHERE '
                "typeof(sequence)<>'integer' OR sequence NOT BETWEEN 1 AND ? LIMIT 1",
                (maximum,)).fetchone()):
        raise ValueError('Prepared static registration scalar metadata is malformed')
    latest = {}
    for row in _sidecar_rows(conn):
        if row['project_id'] in token.projects:
            latest[row['project_id']] = row
    for project in token.projects:
        if project not in latest:
            raise ValueError('Prepared static preload requires the selected registration')
        _record(conn, latest[project])


@contextmanager
def prepare_static_record_scope(store, objective_id, project_ids, *, include_current_registration=True):
    """Prepare one explicit operation before BEGIN IMMEDIATE, with no root I/O.

    The 128 MiB cap counts retained UTF-8 bytes, not total Python heap usage.
    Tokens cannot survive an operation, cross stores/threads, or wrap two writes.
    """
    from eidolon_cli.organization_prepared_validation_contract import _id
    from eidolon_cli.organization_prepared_validation_verification import _read_view
    _require_no_finish_scope()
    if _SCOPE.get() is not None:
        raise ValueError('Prepared static records require a nonnested operation')
    _id(objective_id)
    if (type(project_ids) not in (tuple, list) or not 1 <= len(project_ids) <= 8
            or any(type(project) is not str or re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', project) is None
                   for project in project_ids)
            or len(set(project_ids)) != len(project_ids) or type(include_current_registration) is not bool):
        raise ValueError('Prepared static records require an explicit bounded project selection')
    token = _StaticRecordToken(store, objective_id, tuple(project_ids), include_current_registration)
    scope = _SCOPE.set(token)
    try:
        with store._connect() as conn, _read_view(conn):
            token.connection = conn
            token.database = _database(conn)
            if not token.database:
                raise ValueError('Prepared static records require the exact served ledger')
            _preload(token, conn)
        token.connection = None
        token.phase = 'prepared'
        yield token
    finally:
        _invalidate(token)
        _SCOPE.reset(scope)


@contextmanager
def bind_static_record_scope(token, store, conn):
    """Consume a preloaded operation exactly once in its caller-owned write."""
    from eidolon_cli.organization_prepared_validation_ledger import _require_write_transaction
    _require_token(token, store)
    try:
        _require_no_finish_scope()
        _require_write_transaction(conn)
        if token.phase != 'prepared' or _database(conn) != token.database:
            raise ValueError('Prepared static record operation requires one exact nonnested transaction')
        token.phase = 'bound'
        token.connection = conn
        # SQLite exposes no transaction identity. A private savepoint must still
        # exist at exit, so rollback/commit followed by BEGIN cannot reuse a token
        # and commit a second write. The identifier contains no caller input.
        marker = 'prepared_static_' + uuid.uuid4().hex
        conn.execute('SAVEPOINT ' + marker)
        yield
        conn.execute('RELEASE SAVEPOINT ' + marker)
    finally:
        _invalidate(token)


def _slot(conn, identity):
    token = _SCOPE.get()
    if token is None:
        return None, None
    _require_token(token, token.store)
    if (conn is not token.connection or token.phase not in {'preflight', 'bound'}
            or not conn.in_transaction):
        raise ValueError('Prepared static observation belongs to a foreign or ended transaction')
    if type(identity) is not tuple or len(identity) != 3:
        raise ValueError('Prepared static observation has a foreign identity')
    kind, owner, project = identity
    if kind == 'candidate':
        project, sequence = owner, project
        if not token.include_current_registration or type(sequence) is not int or sequence < 1:
            raise ValueError('Prepared static registration identity is malformed or unselected')
    elif kind != 'grant' or owner != token.objective_id:
        raise ValueError('Prepared static observation has no selected authority')
    if project not in token.projects:
        raise ValueError('Prepared static observation has no exact selected project')
    return token, (kind, project)


def observed_static_record(conn, identity, record, sha256):
    """Return detached JSON only after exact fresh raw TEXT/hash comparison."""
    token, key = _slot(conn, identity)
    if token is None:
        return None
    retained = token.static_records.get(key)
    if retained is None:
        if token.phase == 'bound':
            raise ValueError('Prepared static record was not observed before the write transaction')
        return None
    if (type(record) is not str or type(sha256) is not str
            or retained != (identity, record, sha256)):
        raise ValueError('Prepared static record bytes or hash changed after preflight')
    return json.loads(record)


def retain_static_record(conn, identity, record, sha256):
    """Retain only after the ordinary bounded JSON/hash validation succeeds."""
    token, key = _slot(conn, identity)
    if token is None:
        return
    maximum = (80 if identity[0] == 'candidate' else 40) * 1024**2
    size = len(record.encode('utf-8')) if type(record) is str else 0
    if (token.phase != 'preflight' or not 0 < size <= maximum or type(sha256) is not str
            or re.fullmatch(r'[0-9a-f]{64}', sha256) is None):
        raise ValueError('Prepared static observation requires bounded validated preflight bytes')
    value = (identity, record, sha256)
    if key in token.static_records and token.static_records[key] != value:
        raise ValueError('Prepared static record changed during preflight')
    if key not in token.static_records:
        if token.static_bytes + size > _MAX_STATIC_RECORD_BYTES:
            raise ValueError('Prepared static records exceed the 128 MiB aggregate raw-byte capacity')
        token.static_bytes += size
    token.static_records[key] = value
