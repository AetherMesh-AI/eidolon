"""One final-fence reader lifetime; never cache an authority verdict.

The caller's existing write transaction excludes other committed writers. A
separate autocommit reader retains the committed pre-transition view while every
checkpoint still performs its complete policy/configuration/lease queries.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
import os
import sqlite3
import stat
import threading

_SCOPE = ContextVar('prepared_final_checkpoint_reader', default=None)


@dataclass(frozen=True)
class _Reader:
    store: object
    token: object
    writer: object
    connection: object
    identity: tuple
    thread: int


def _identity(store):
    path = Path(store.path)
    info = os.stat(path, follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode) or path.absolute() != path.resolve():
        raise ValueError('Prepared checkpoint ledger path changed')
    return info.st_dev, info.st_ino


def _validate(scope, store):
    from eidolon_cli.organization_prepared_validation_finish import _ACTIVE, _require_token
    if (scope.store is not store or scope.thread != threading.get_ident()
            or _ACTIVE.get() is not scope.token or not scope.token.entered):
        raise ValueError('Prepared checkpoint reader belongs to another finish')
    _require_token(scope.token, store, scope.writer)
    conn = scope.connection
    if (not scope.writer.in_transaction or conn is scope.writer or conn.in_transaction
            or conn.row_factory is not sqlite3.Row or conn.execute('PRAGMA query_only').fetchone()[0] != 1
            or _identity(store) != scope.identity):
        raise ValueError('Prepared checkpoint reader lost its autocommit read-only boundary')


@contextmanager
def final_checkpoint_reader(store, token):
    from eidolon_cli.organization_prepared_validation_finish import _ACTIVE, _require_token
    _require_token(token, store, token.connection)
    if (_SCOPE.get() is not None or _ACTIVE.get() is not token or not token.entered
            or token.connection is None or not token.connection.in_transaction):
        raise ValueError('Prepared checkpoint reader requires its one active final fence')
    identity = _identity(store)
    with store._connect() as conn:
        databases = conn.execute('PRAGMA database_list').fetchall()
        if (len(databases) != 1 or databases[0][1] != 'main'
                or Path(databases[0][2]).resolve() != Path(store.path).resolve()):
            raise ValueError('Prepared checkpoint reader belongs to another ledger')
        conn.execute('PRAGMA query_only=ON')
        scope = _Reader(store, token, token.connection, conn, identity, threading.get_ident())
        _validate(scope, store)
        context = _SCOPE.set(scope)
        try:
            yield
            _validate(scope, store)
        finally:
            _SCOPE.reset(context)


@contextmanager
def checkpoint_connection(store):
    scope = _SCOPE.get()
    if scope is None:
        with store._connect() as conn:
            yield conn
        return
    _validate(scope, store)
    try:
        yield scope.connection
    finally:
        _validate(scope, store)
