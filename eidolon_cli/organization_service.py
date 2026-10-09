"""Profile-local, durable organization scheduling outside any UI connection."""

from __future__ import annotations

import contextvars
from contextlib import ExitStack
import hashlib
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from agent.secret_scope import (build_profile_secret_scope, current_secret_scope,
                                is_secret_scope_required, reset_secret_scope,
                                reset_secret_scope_required, set_secret_scope,
                                set_secret_scope_required)
from eidolon_constants import get_eidolon_home, reset_eidolon_home_override, set_eidolon_home_override
from eidolon_cli.organization_config import OrganizationSettings, from_config
from eidolon_cli.organization_store import OrganizationStore

logger = logging.getLogger(__name__)
Executor = Callable[[dict, dict, threading.Event], dict]


def _execute(request: dict, context: dict, cancel: threading.Event) -> dict:
    from eidolon_cli.organization_executor import execute
    return execute(request, context, cancel)


class _ExecutionLock:
    """A live call keeps its fence even if its SQLite lease expires or is retried."""

    def __init__(self, directory: Path, request_id: str):
        directory.mkdir(parents=True, exist_ok=True)
        name = hashlib.sha256(request_id.encode()).hexdigest() + ".lock"
        self.handle = (directory / name).open("a+b")
        self.acquired = False

    def __enter__(self):
        try:
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.acquired = True
        except OSError:
            self.handle.close()
        return self.acquired

    def __exit__(self, *_):
        # Closing the descriptor releases the OS lock. Never unlink it: another
        # process may already have opened the inode before attempting its lock.
        self.handle.close()


@dataclass
class _Running:
    claim: dict
    cancel: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    started: float = field(default_factory=time.monotonic)
    last_heartbeat: float = field(default_factory=time.monotonic)
    result: dict | None = None
    error: str | None = None
    fenced: bool = False
    deferred: bool = False
    execution_slot: _ExecutionLock | None = None


class OrganizationService:
    def __init__(self, store: OrganizationStore, *, home: Path | None = None,
                 settings: OrganizationSettings | None = None, executor: Executor | None = None,
                 poll_seconds: float = 0.25, can_dispatch: Callable[[], bool] | None = None):
        self.store = store
        self.home = Path(home or get_eidolon_home()).resolve()
        self.settings = settings or store.settings
        self.executor = executor or _execute
        self.can_dispatch = can_dispatch or (lambda: True)
        self.poll_seconds = max(0.01, poll_seconds)
        self._lock = threading.RLock()
        self._admission_lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._running: dict[str, _Running] = {}
        # Copy only profile and credentials into a clean context. A background
        # organization must not inherit the requesting UI's transport/session.
        self._context = contextvars.Context()
        launch_home = self._context.run(get_eidolon_home).resolve()
        self._context.run(set_eidolon_home_override, self.home)
        self._context.run(set_secret_scope_required,
                          is_secret_scope_required() or self.home != launch_home)
        secrets = current_secret_scope() if get_eidolon_home().resolve() == self.home else None
        self._context.run(set_secret_scope, dict(secrets) if secrets is not None
                          else self._context.run(build_profile_secret_scope, self.home))
        from eidolon_cli.organization_owner_chat_service import OwnerChatService
        self.owner_chat = OwnerChatService(self)

    @property
    def active_execution_count(self) -> int:
        """Conservative in-memory count, including cancelled calls unwinding."""
        with self._admission_lock:
            return len(self._running) + self.owner_chat.active_count

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        with self._lock:
            if self._stop.is_set():
                raise RuntimeError("Organization service is stopping; wait for active work to finish")
            if not self.running:
                self._thread = threading.Thread(target=self._context.copy().run,
                                                args=(self._run,), daemon=True,
                                                name="organization-scheduler")
                self._thread.start()
        self._wake.set()

    def create_objective(self, *args, **kwargs):
        with self._lock:
            if self._stop.is_set():
                raise RuntimeError("Organization service is stopping; reconnect before creating work")
            objective = self.store.create_objective(*args, **kwargs)
            self.start()
            return objective

    def cancel(self, objective_id: str) -> bool:
        with self._lock:
            for record in self._running.values():
                if record.claim.get("objective_id") == objective_id:
                    record.cancel.set()
            changed = self.store.cancel(objective_id)
            self._wake.set()
            return changed

    def set_objective_archived(self, objective_id: str, archived: bool, *, expected_revision: int,
                               idempotency_key: str):
        """Never hide a cancelled provider that is still unwinding in any process."""
        with self._lock:
            if any(record.claim.get('objective_id') == objective_id for record in self._running.values()):
                raise ValueError('This objective still has an execution stopping or finishing; archive after it exits')
            with ExitStack() as locks:
                for ident in self.store.objective_request_ids(objective_id):
                    if not locks.enter_context(_ExecutionLock(self.home / 'organization' / 'execution-locks', ident)):
                        raise ValueError('This objective has an execution still active in another runtime; archive after it exits')
                return self.store.set_objective_archived(objective_id, archived,
                    expected_revision=expected_revision, idempotency_key=idempotency_key)

    def retry(self, request_id: str, *, idempotency_key: str | None = None) -> bool:
        with self._lock:
            if self.store.retry_recorded(request_id, idempotency_key):
                return False
            if request_id in self._running:
                raise ValueError("The previous execution is still stopping; retry after it exits")
            if self._stop.is_set():
                raise RuntimeError("Organization service is stopping; reconnect before retrying work")
            changed = self.store.retry(request_id, idempotency_key=idempotency_key)
            if changed:
                self.start()
            return changed

    def resolve(self, request_id: str, *, action: str, text: str | None = None,
                evidence_ids: list[str] | None = None, idempotency_key: str,
                required_checks: list[str] | None = None, acceptance_criteria: list[str] | None = None) -> bool:
        """Resolve a specific intervention only after its execution has stopped.

        The ledger validates the action, evidence, policy generation and request
        state. Shared request locks additionally fence a provider still unwinding
        in another process after its lease became pending intervention. A lost
        response can replay the exact committed resolution without another effect.
        """
        payload = dict(action=action, text=text, evidence_ids=evidence_ids,
                       idempotency_key=idempotency_key, required_checks=required_checks, acceptance_criteria=acceptance_criteria)
        with self._lock:
            if self.store.resolution_recorded(request_id, **payload):
                return False
            if self._stop.is_set():
                raise RuntimeError("Organization service is stopping; reconnect before resolving work")
            objective_id = self.store.request_objective_id(request_id)
            if any(record.claim.get("objective_id") == objective_id
                   for record in self._running.values()):
                raise ValueError("This objective still has an execution stopping or finishing; resolve it after that work exits")
            with ExitStack() as locks:
                for ident in self.store.objective_request_ids(objective_id):
                    available = locks.enter_context(_ExecutionLock(
                        self.home / "organization" / "execution-locks", ident))
                    if not available:
                        raise ValueError("This objective has an execution still active in another runtime; resolve it after that work exits")
                changed = self.store.resolve(request_id, **payload)
            if changed:
                self.start()
            return changed

    def respond(self, request_id: str, *, text: str, decision: str, idempotency_key: str) -> bool:
        with self._lock:
            payload = dict(text=text, decision=decision, idempotency_key=idempotency_key)
            if self.store.response_recorded(request_id, **payload):
                return False
            if self._stop.is_set():
                raise RuntimeError("Organization service is stopping; reconnect before responding")
            if request_id in self._running:
                raise ValueError("The request execution is still stopping; answer after it exits")
            with _ExecutionLock(self.home / "organization" / "execution-locks", request_id) as acquired:
                if not acquired:
                    raise ValueError("The request is still executing in another runtime")
                changed = self.store.respond(request_id, **payload)
                self.settings = self.store.settings
            if changed:
                self.start()
            return changed

    def reload_configuration(self, settings):
        """Apply validated file grants without replacing live execution records."""
        with self._lock:
            changed = self.store.reload_configuration(settings)
            self.settings = self.store.settings
            self.refresh_configuration()
            self._wake.set()
            return changed

    def reload_profile_configuration(self, *, require_sources=False):
        with self._lock:
            try:
                enabled = self.store.reload_profile_configuration(self.home, require_sources=require_sources)
            finally:
                self.refresh_configuration()
            self.settings = self.store.settings
            self._wake.set()
            return enabled

    def pause_configuration(self):
        self.close_admission()
        with self._lock:
            self.store.pause_configuration()

    def refresh_configuration(self):
        """Cancel revoked claims before adopting another host's durable policy."""
        from eidolon_cli.organization_policy import persisted_settings
        with self._lock:
            with self.store._connect() as conn:
                conn.execute('BEGIN')
                for record in self._running.values():
                    if not record.fenced and self.store._owned(conn, record.claim) is None:
                        record.cancel.set()
                        record.fenced = True
                if self.store._policy_paused(conn):
                    return False
                if self.store._policy_current(conn):
                    return False
                settings = persisted_settings(conn)
                policy = conn.execute('SELECT generation,fingerprint FROM organization_policy WHERE id=1').fetchone()
                if settings is None or policy is None:
                    raise ValueError('Organization configuration is unavailable')
                self.store.settings = self.settings = settings
                self.store._policy_generation = policy['generation']
                self.store._policy_fingerprint = policy['fingerprint']
            self._wake.set()
            return True

    def configure(self, configuration: dict, *, expected_generation: int, idempotency_key: str):
        with self._lock:
            if self.store.configuration_recorded(configuration, expected_generation=expected_generation,
                                                 idempotency_key=idempotency_key):
                return self.store.configure_organization(configuration, expected_generation=expected_generation,
                                                        idempotency_key=idempotency_key)
            if self._stop.is_set():
                raise RuntimeError("Organization service is stopping; reconnect before configuring")
            if self._running:
                raise ValueError("Organization executions are active or stopping; configure after they exit")
            result = self.store.configure_organization(configuration, expected_generation=expected_generation,
                                                       idempotency_key=idempotency_key)
            self.settings = self.store.settings
            self.store.refresh_unhandled_requests()
            self.start()
            return result

    def close_admission(self) -> None:
        """Fence new dispatch immediately, without waiting for SQLite or threads."""
        self._stop.set()
        self.owner_chat.close_admission()
        # This lock only protects the running-record map, never SQLite work or
        # provider setup. Teardown can signal calls even while the scheduler is
        # blocked reading/writing the ledger under its coordinator lock.
        with self._admission_lock:
            for record in self._running.values():
                record.cancel.set()
        self._wake.set()

    def stop(self, timeout: float = 5.0) -> bool:
        # Persist uncertain work before waiting; SIGKILL may follow the grace.
        started = time.monotonic()
        self.close_admission()
        persisted = self.owner_chat.stop(timeout)
        timeout = max(0.0, timeout - (time.monotonic() - started))
        with self._lock:
            self._stop.set()
            for record in self._running.values():
                record.cancel.set()
            for record in self._running.values():
                try:
                    self._fence(record, "Backend stopped during execution; outcome needs review before retry")
                except Exception:
                    # Cancel every call even when one ledger write fails. The
                    # still-live coordinator retries persistence before exiting.
                    persisted = False
                    logger.exception("Could not persist interrupted organization request")
        self._wake.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, timeout))
        return not self.running and persisted

    def _fence(self, record: _Running, reason: str) -> None:
        record.cancel.set()
        if not record.fenced:
            self.store.fail(record.claim, reason, retryable=False)
            record.fenced = True

    def _work(self, record: _Running) -> None:
        try:
            request_id = str(record.claim["id"])
            with _ExecutionLock(self.home / "organization" / "execution-locks", request_id) as acquired:
                if not acquired:
                    record.error = "A previous execution is still active; waiting for it to exit"
                    record.deferred = True
                    return
                if record.cancel.is_set() or self._stop.is_set():
                    record.cancel.set()
                    record.error = "Execution interrupted before it started"
                    return
                with _ExecutionLock(self.home / "organization" / "agent-locks",
                                    str(record.claim["agent_id"])) as agent_available:
                    if not agent_available:
                        record.error = "Assigned agent is still stopping previous work; waiting for it to exit"
                        record.deferred = True
                        return
                    context = self.store.context(record.claim)
                    context.update({
                        'recordToolStart': lambda call_id, name, args: self.store.record_tool_start(record.claim, call_id, name, args),
                        'recordToolFinish': lambda ident, result, status: self.store.record_tool_finish(record.claim, ident, result, status),
                        'requestToolReceipts': lambda: self.store.request_tool_receipts(record.claim),
                        'reserveModelCall': lambda **values: self.store.reserve_model_call(record.claim, **values),
                        'recordContextReceipt': lambda report: self.store.record_context_receipt(record.claim, report),
                        'recordEvidencePass': lambda report: self.store.record_evidence_pass(record.claim, report),
                    })
                    kind = record.claim.get("type", record.claim.get("kind"))
                    if kind == 'work.edit':
                        context['resolveWorkspaceSource'] = lambda path, loader: self.store.capture_workspace_source(record.claim, path, loader)
                    if record.cancel.is_set() or self._stop.is_set():
                        record.cancel.set()
                        record.error = "Execution interrupted before dispatch"
                        return
                    if not self.can_dispatch():
                        record.deferred = True
                        record.error = "Host is draining; waiting before dispatch"
                        return
                    if kind in {'request.project_test', 'request.source_integrate'}:
                        record.result = self.store.run_project_stage(record.claim, record.cancel)
                    else:
                        record.result = {} if kind in {'request.hire', 'request.apply', 'request.validate'} else self.executor(
                            record.claim, context, record.cancel)
        except ValueError as error:
            # The project boundary reports only bounded, sanitized policy errors.
            if record.claim.get("type") in {"request.project_test", "request.source_integrate"}:
                record.error = str(error)[:2000]
            else:
                record.error = "Execution failed unexpectedly; review provider configuration before retrying"
        except Exception:
            # Provider exceptions can contain credentials or private request URLs.
            record.error = "Execution failed unexpectedly; review provider configuration before retrying"
            logger.warning("Organization execution failed for request %s", record.claim.get("id"))
        finally:
            if record.execution_slot is not None:
                record.execution_slot.__exit__()
            self._wake.set()

    def _maintain(self) -> None:
        now = time.monotonic()
        heartbeat_interval = max(0.05, min(5.0, self.settings.lease_seconds / 3))
        for request_id, record in list(self._running.items()):
            if not record.thread.is_alive():
                if not record.fenced:
                    if record.deferred:
                        self.store.defer(record.claim, record.error)
                    elif record.error is not None:
                        self.store.fail(record.claim, record.error, retryable=False)
                    elif record.cancel.is_set() or self._stop.is_set():
                        self.store.fail(record.claim, "Execution interrupted; review outcome before retry", retryable=False)
                    else:
                        try:
                            self.store.finish(record.claim, record.result)
                            self.settings = self.store.settings
                        except ValueError as exc:
                            self.store.fail(record.claim, str(exc)[:2000], retryable=False)
                with self._admission_lock:
                    del self._running[request_id]
            elif self._stop.is_set():
                self._fence(record, "Backend stopped during execution; outcome needs review before retry")
            elif now - record.started >= self.settings.timeout_seconds:
                self._fence(record, "Execution timed out; outcome needs review before retry")
            elif not record.fenced and now - record.last_heartbeat >= heartbeat_interval:
                if not self.store.heartbeat(record.claim):
                    record.cancel.set()
                    record.fenced = True
                record.last_heartbeat = now

    def _execution_slot(self) -> _ExecutionLock | None:
        # SQLite limits leased requests; these process-independent locks also
        # count calls whose lease was fenced while their provider is still busy.
        for index in range(self.settings.max_inflight):
            slot = _ExecutionLock(self.home / "organization" / "capacity-locks", str(index))
            if slot.__enter__():
                return slot
        return None

    def _fill_slots(self) -> None:
        while not self._stop.is_set() and self.can_dispatch() and len(self._running) < self.settings.max_inflight:
            slot = self._execution_slot()
            if slot is None:
                return
            admitted = False
            try:
                claim = self.store.claim_next()
                if claim is None:
                    return
                if self._stop.is_set() or not self.can_dispatch():
                    self.store.defer(claim, "Runtime stopped or draining before execution; queued for the next host")
                    return
                request_id = str(claim["id"])
                if request_id in self._running:
                    self.store.defer(claim, "A previous execution is still active; waiting for it to exit")
                    return
                record = _Running(claim, execution_slot=slot)
                record.thread = threading.Thread(target=self._context.copy().run, args=(self._work, record),
                                                 daemon=True, name="organization-" + request_id[:12])
                with self._admission_lock:
                    self._running[request_id] = record
                    if self._stop.is_set():
                        record.cancel.set()
                record.thread.start()
                admitted = True
            finally:
                if not admitted:
                    slot.__exit__()

    def _run(self) -> None:
        while True:
            try:
                with self._lock:
                    if not self._stop.is_set():
                        self.refresh_configuration()
                    self._maintain()
                    if self._stop.is_set() and not self._running:
                        return
                    if not self._stop.is_set():
                        self.store.recover_expired()
                        self._fill_slots()
            except Exception:
                # A transient SQLite failure must not silently kill the sole
                # coordinator; next tick retries the transaction, never the call.
                logger.exception("Organization scheduler transaction failed")
            self._wake.wait(self.poll_seconds)
            self._wake.clear()


_services: dict[Path, OrganizationService] = {}
_services_lock = threading.RLock()


def state_path() -> Path:
    return Path(get_eidolon_home()).resolve() / "organization" / "state.db"


def get_service() -> OrganizationService:
    path = state_path()
    with _services_lock:
        service = _services.get(path)
        if service is None or (service._stop.is_set() and not service.running and not service.owner_chat.active_count):
            if not path.exists():
                # Invalid first-run configuration must not provision a ledger.
                # Existing ledgers validate under their write lock to pause peers.
                from eidolon_cli.organization_config import load_profile_configuration
                load_profile_configuration(path.parent.parent)
            store = OrganizationStore(path)
            store.reload_profile_configuration(path.parent.parent)
            service = OrganizationService(store, settings=store.settings, home=path.parent.parent)
            _services[path] = service
        else:
            service.refresh_configuration()
        return service


def start_existing_service() -> OrganizationService | None:
    """Startup recovery is opt-in through durable state, never a provider probe."""
    if not state_path().is_file():
        return None
    service = get_service()
    service.start()
    return service


def start_existing_services() -> list[OrganizationService]:
    """Recover launch state and only the additional profiles configured to serve."""
    from eidolon_cli.config import load_config
    from eidolon_cli.profiles import profiles_to_serve

    gateway = load_config().get("gateway") or {}
    homes = [Path(get_eidolon_home()).resolve()]
    if gateway.get("multiplex_profiles") is True:
        homes.extend(Path(home).resolve() for _, home in profiles_to_serve(
            True, gateway.get("multiplex_profile_allowlist")))
    started = []
    for home in dict.fromkeys(homes):
        if not (home / "organization" / "state.db").is_file():
            continue
        launch_home = contextvars.Context().run(get_eidolon_home).resolve()
        home_token = set_eidolon_home_override(home)
        required_token = set_secret_scope_required(is_secret_scope_required() or home != launch_home)
        secret_token = set_secret_scope(build_profile_secret_scope(home))
        try:
            service = start_existing_service()
            if service is not None:
                started.append(service)
        except Exception:
            # One corrupt ledger/config must not prevent the UI from starting
            # or stop other configured profiles from resuming their own work.
            logger.exception("Organization startup recovery failed")
        finally:
            reset_secret_scope(secret_token)
            reset_secret_scope_required(required_token)
            reset_eidolon_home_override(home_token)
    return started


def stop_services(timeout: float = 5.0) -> bool:
    with _services_lock:
        services = list(_services.values())
    deadline = time.monotonic() + max(0.0, timeout)
    complete = True
    for service in services:
        complete = service.stop(max(0.0, deadline - time.monotonic())) and complete
    return complete
