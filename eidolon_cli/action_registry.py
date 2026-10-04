"""Retained direct dashboard-action ownership, independent of latest-name UI state.

This is NOT process-tree containment. A complete drain describes only registered
ordinary direct handles, never late forks, descendants, or host-wide quiescence.
Recovery actions require the acknowledged handoff protocol and are never killed
by this registry. A stopped registry is terminal; construct a new one for a new
server lifecycle, not in response to a rejected request.
"""
from dataclasses import dataclass
import math
import threading
import time
from typing import Any


class ActionAdmissionClosed(RuntimeError):
    """No new action can be admitted after the shutdown fence."""


@dataclass(frozen=True)
class ActionDrainResult:
    status: str
    exited: tuple[int, ...]
    unresolved: tuple[int, ...]
    protected: tuple[int, ...]
    evidence_scope: str = "registered-direct-actions"
    transferred: tuple[int, ...] = ()


@dataclass(frozen=True)
class ShutdownIntent:
    transaction_id: str
    predecessor: Any
    successor: Any
    kind: str = "update"


@dataclass
class _Entry:
    instance_id: int
    name: str
    role: str
    handle: Any = None
    state: str = "reserved"
    cleanup_claimed: bool = False
    handoff: Any = None
    driver: Any = None
    launch_failure: dict[str, str] | None = None


class ActionRegistry:
    def __init__(self):
        self._lock = threading.RLock()
        self._entries = {}
        self._next_id = 0
        self._stopping = False
        self._result = None
        self._ordinary_requested = False
        self._handoff_instance = None
        self._shutdown_intent = None

    @property
    def shutdown_intent(self):
        with self._lock:
            return self._shutdown_intent

    def bind_handoff(self, instance_id, protocol):
        """Bind one live protocol object to the retained, RUN-authorized handle.

        This supplies registry adapters, not a server-exit callback. The channel
        driver must still deliver STOP_AUTHORIZED and request lifecycle shutdown.
        Disk records, role names and replacement protocol objects cannot bind an
        already-bound entry. Protocol incarnation verification runs outside locks.
        """
        from eidolon_cli.update_handoff import Handoff
        with self._lock:
            entry = self._entries[instance_id]
            if (self._ordinary_requested or self._stopping or entry.role != "recovery" or entry.state != "run-authorized"
                    or entry.handoff is not None or not isinstance(protocol, Handoff)
                    or protocol.role != "predecessor" or protocol.phase != "RUNNING"
                    or protocol.binding.action_instance_id != instance_id
                    or entry.handle.pid != protocol.binding.successor.pid):
                raise ActionAdmissionClosed("handoff does not bind this live recovery action")
            entry.handoff = protocol
            protocol.prepare = lambda plan: self._prepare_handoff(instance_id, protocol)
            protocol.acknowledge = lambda peer: self._acknowledge_handoff(instance_id, peer)
            protocol.authorize = lambda peer: self._authorize_handoff(instance_id, peer)

    def _prepare_handoff(self, instance_id, protocol):
        with self._lock:
            entry = self._entries[instance_id]
            if self._ordinary_requested or self._stopping or entry.handoff is not protocol:
                raise ActionAdmissionClosed("ordinary stop or another handoff won")
            self._stopping = True
            self._handoff_instance = instance_id
            entry.state = "transfer-pending"
            others = tuple(e for key, e in self._entries.items() if key != instance_id)
        deadline = time.monotonic() + 5.0
        for entry in others:
            self._cleanup(entry, deadline)
        with self._lock:
            result = self._snapshot()
            pending = set(result.unresolved) | (set(result.protected) - {instance_id})
            if self._ordinary_requested or pending:
                raise ActionAdmissionClosed("other direct actions unresolved or ordinary stop won")
        return {"status": "complete", "evidence_scope": "registered-direct-actions", "unresolved": []}

    def _acknowledge_handoff(self, instance_id, protocol):
        with self._lock:
            entry = self._entries[instance_id]
            if (entry.handoff is not protocol or self._handoff_instance != instance_id
                    or not protocol.acknowledged or protocol.phase != "ACKNOWLEDGED"):
                raise ActionAdmissionClosed("no exact durable live acknowledgment")
            # Keep HOLD protection until the immutable stop-intent race is won.
            entry.state = "acknowledged-transfer"

    def _authorize_handoff(self, instance_id, protocol):
        # Verification can perform I/O or reenter stop: never run it under the
        # registry lock. Recheck the ordinary-stop race after it returns.
        protocol._check()
        with self._lock:
            entry = self._entries[instance_id]
            if (self._ordinary_requested or self._result is not None
                    or self._handoff_instance != instance_id
                    or entry.handoff is not protocol or entry.state != "acknowledged-transfer"
                    or not protocol.acknowledged or protocol.phase != "STOP_AUTHORIZED"):
                return False
            result = self._snapshot()
            if (result.unresolved or set(result.protected) - {instance_id}
                    or protocol.clock() >= protocol.deadline):
                return False
            self._shutdown_intent = ShutdownIntent(protocol.binding.transaction_id,
                                                   protocol.binding.predecessor, protocol.binding.successor)
            # Publish all live authority in the same critical section. A stop
            # observer sees either protected pending recovery or committed transfer.
            protocol.stop_authorized = True
            entry.state = "transferred"
            return True

    def reserve(self, name: str, *, role: str = "ordinary") -> int:
        if role not in ("ordinary", "recovery"):
            raise ValueError("unknown action role")
        with self._lock:
            if self._ordinary_requested or self._stopping:
                raise ActionAdmissionClosed("dashboard action admission is closed")
            self._next_id += 1
            instance_id = self._next_id
            self._entries[instance_id] = _Entry(instance_id, name, role)
            return instance_id

    def attach(self, instance_id: int, handle: Any) -> None:
        """Publish ownership before any fallible UI publication.

        A Popen already in flight when stop fences admission remains unresolved
        in that stop receipt. On return, its handle is retained and ordinary
        cleanup is attempted; the historical receipt is not upgraded to clean.
        """
        with self._lock:
            entry = self._entries[instance_id]
            if entry.state != "reserved":
                raise RuntimeError("action reservation is not pending")
            entry.handle = handle
            entry.state = "bootstrapping" if entry.role == "recovery" else "running"
            stopped = self._ordinary_requested or self._stopping
        if stopped:
            self._cleanup(entry, time.monotonic())
            raise ActionAdmissionClosed("action returned after shutdown fence")

    def record_launch_failure(self, instance_id: int, *, error: str, message: str) -> None:
        """Keep refusal separate from liveness; never release owned recovery."""
        with self._lock:
            self._entries[instance_id].launch_failure = {"error": error, "message": message}

    def launch_failure(self, handle) -> dict[str, str] | None:
        with self._lock:
            for entry in self._entries.values():
                if entry.handle is handle and entry.launch_failure is not None:
                    return dict(entry.launch_failure)
        return None

    def bind_driver(self, instance_id, driver):
        from eidolon_cli.update_handoff_driver import ParentDriver
        with self._lock:
            entry = self._entries[instance_id]
            if (self._ordinary_requested or self._stopping or entry.state != "bootstrapping"
                    or entry.driver is not None or not isinstance(driver, ParentDriver)
                    or driver.handle is not entry.handle or driver.instance_id != instance_id):
                raise ActionAdmissionClosed("driver does not own this reserved recovery action")
            entry.driver = driver

    def authorize_run(self, instance_id: int) -> None:
        """Linearize RUN permission against stop, without pipe I/O under the lock.

        Once permission wins, EOF/write failure is uncertain recovery, not a
        cancellation license. Before permission, stop may close the inert gate.
        """
        with self._lock:
            entry = self._entries[instance_id]
            if self._ordinary_requested or self._stopping or entry.state != "bootstrapping":
                raise ActionAdmissionClosed("recovery RUN lost to shutdown or cancellation")
            entry.state = "run-authorized"

    def spawn_failed(self, instance_id: int) -> None:
        with self._lock:
            entry = self._entries[instance_id]
            if entry.state == "reserved":
                entry.state = "spawn-failed"

    def publication_failed(self, instance_id: int) -> None:
        # Retain ownership even if name-index/UI publication fails.
        with self._lock:
            entry = self._entries[instance_id]
        self._cleanup(entry, time.monotonic())

    def _cleanup(self, entry: _Entry, deadline: float) -> None:
        with self._lock:
            if entry.handle is None or entry.cleanup_claimed:
                return
            if entry.role == "recovery" and entry.state != "bootstrapping":
                return
            entry.cleanup_claimed = True
            handle = entry.handle
            recovery = entry.role == "recovery"
            if recovery:
                entry.state = "cancelled-bootstrap"
        # Never hold the admission lock while invoking a process method.
        try:
            if recovery:
                # No RUN has been granted: EOF is cooperative inert cancellation.
                # Keep retained ownership; closing a pipe does not prove exit.
                handle.stdin.close()
                handle.wait(timeout=max(0.0, deadline - time.monotonic()))
            elif handle.poll() is None:
                handle.terminate()
                handle.wait(timeout=max(0.0, deadline - time.monotonic()))
            exited = handle.poll() is not None
        except Exception:
            exited = False
        with self._lock:
            entry.state = ("cancelled-exited" if recovery else "exited") if exited else "unresolved"

    def _snapshot(self) -> ActionDrainResult:
        exited, unresolved, protected, transferred = [], [], [], []
        for key, entry in self._entries.items():
            if entry.state == "spawn-failed":
                continue
            if entry.state == "cancelled-exited":
                exited.append(key)
            elif entry.state == "transferred" and entry.handoff.stop_authorized:
                transferred.append(key)
            elif entry.role == "recovery":
                protected.append(key)
            elif entry.state == "exited":
                exited.append(key)
            else:
                unresolved.append(key)
        return ActionDrainResult(
            "incomplete" if unresolved or protected else "complete",
            tuple(exited), tuple(unresolved), tuple(protected), transferred=tuple(transferred),
        )

    def fence(self) -> None:
        """Close ordinary admission before fallible backend finalizers run."""
        with self._lock:
            if self._shutdown_intent is None:
                self._ordinary_requested = True

    def stop(self, *, timeout: float = 5.0) -> ActionDrainResult:
        """Fence admission and attempt bounded direct-handle drain once.

        All entries share one wait budget. No PID reconstruction, process scan,
        recursive kill, escalation, signal suppression, or persistence is used.
        Reentrant/concurrent callers freeze a conservative terminal snapshot
        rather than deadlocking on the caller performing cleanup. That receipt
        stays incomplete even if ongoing cleanup subsequently succeeds.
        """
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError("timeout must be finite and nonnegative")
        with self._lock:
            if self._shutdown_intent is None:
                self._ordinary_requested = True
            if self._result is not None:
                return self._result
            if self._stopping:
                self._result = self._snapshot()
                return self._result
            self._stopping = True
            entries = tuple(self._entries.values())
        deadline = time.monotonic() + timeout
        for entry in entries:
            self._cleanup(entry, deadline)
        with self._lock:
            if self._result is None:
                self._result = self._snapshot()
            return self._result
