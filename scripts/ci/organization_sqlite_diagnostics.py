"""Opt-in test diagnostics; never record database paths, SQL, parameters or errors."""
import functools
import itertools
import sqlite3
import threading
import time


def install(record):
    original = sqlite3.connect
    identifiers = itertools.count(1)

    def emit(value):
        try:
            record(value)
        except Exception:
            pass  # Diagnostics must not change a database operation's outcome.

    def timed(phase, identifier, operation):
        fields = {'event': 'sqlite', 'phase': phase, 'connection': identifier,
                  'thread': threading.get_ident()}
        emit({**fields, 'boundary': 'start'})
        started = time.monotonic()
        error = {}
        try:
            return operation()
        except BaseException as exc:
            error = {'errorType': type(exc).__name__,
                     'sqliteErrorCode': getattr(exc, 'sqlite_errorcode', None)}
            raise
        finally:
            emit({**fields, 'boundary': 'end',
                    'duration': time.monotonic() - started, **error})

    class Connection(sqlite3.Connection):
        def commit(self):
            operation = super().commit
            return timed('commit', getattr(self, '_diagnostic_id', None), operation)

        def close(self):
            operation = super().close
            return timed('close', getattr(self, '_diagnostic_id', None), operation)

    @functools.wraps(original)
    def connect(*args, **kwargs):
        identifier = next(identifiers)
        # Preserve explicit custom factories, including positional ones.
        default_factory = len(args) <= 5 and 'factory' not in kwargs
        if default_factory:
            kwargs = {**kwargs, 'factory': Connection}
        result = timed('connect', identifier, lambda: original(*args, **kwargs))
        if default_factory:
            result._diagnostic_id = identifier
        return result

    sqlite3.connect = connect

    def restore():
        if sqlite3.connect is connect:
            sqlite3.connect = original

    return restore
