"""Bounded coordination for organization writers and send-authority reads.

This is scheduling, not storage or identity authority. SQLite remains the fence
for other processes, inode aliases and writers outside these cooperating paths.
Callers keep publication -> coordinator -> SQLite order. Send-authority reads
also hold this gate through connection close to exclude WAL cleanup/recovery;
ordinary observation readers remain concurrent. No network I/O runs under this
gate. The registry owns no idle gates and the queue cannot grow unbounded.
"""
from collections import deque
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import threading
import time
import weakref

PUBLICATION_WAIT_SECONDS = .25
ACCOUNTING_WAIT_SECONDS = 5
_MAX_WAITERS = 64
_CANCEL_POLL_SECONDS = .025
_registry_lock = threading.Lock()
_gates = weakref.WeakValueDictionary()


class LedgerWriterUnavailable(sqlite3.OperationalError):
    def __init__(self, reason):
        self.reason = reason
        self.sqlite_errorcode = sqlite3.SQLITE_BUSY
        self.sqlite_errorname = 'SQLITE_BUSY'
        super().__init__('Organization ledger writer unavailable: ' + reason)


class _Gate:
    def __init__(self):
        self.condition = threading.Condition()
        self.owner = None
        self.waiters = deque()

    def _wait_for_turn(self, ticket, deadline, cancelled):
        while True:
            if cancelled is not None and cancelled():
                raise LedgerWriterUnavailable('cancelled')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LedgerWriterUnavailable('deadline')
            if self.owner is None and self.waiters[0] is ticket:
                return
            self.condition.wait(min(remaining, _CANCEL_POLL_SECONDS))

    def acquire(self, deadline, cancelled):
        ticket = object()
        with self.condition:
            if self.owner == threading.get_ident():
                raise LedgerWriterUnavailable('reentrant')
            if len(self.waiters) >= _MAX_WAITERS:
                raise LedgerWriterUnavailable('queue_full')
            self.waiters.append(ticket)
            try:
                self._wait_for_turn(ticket, deadline, cancelled)
                self.owner = threading.get_ident()
            finally:
                self.waiters.remove(ticket)
                self.condition.notify_all()

    def release(self):
        with self.condition:
            if self.owner != threading.get_ident():
                raise RuntimeError('Ledger writer released by another thread')
            self.owner = None
            self.condition.notify_all()


@contextmanager
def ledger_writer(path, *, deadline, cancelled=None):
    # Keep the existing callers' symlink/identity checks. Canonical pathname
    # coalescing does not pretend to detect external replacement or hard links.
    key = str(Path(path).resolve())
    with _registry_lock:
        gate = _gates.get(key)
        if gate is None:
            gate = _Gate()
            _gates[key] = gate
    gate.acquire(deadline, cancelled)
    try:
        yield
    finally:
        gate.release()
