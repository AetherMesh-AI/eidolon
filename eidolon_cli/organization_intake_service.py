"""Explicit owner submission starts inference; reads never start or replay work."""
from contextlib import ExitStack
from dataclasses import dataclass, field
import threading


@dataclass
class _Run:
    id: str
    cancel: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None


class IntakeService:
    def __init__(self, organization):
        self.organization = organization
        self.store = organization.store
        self._lock = threading.RLock()
        self._runs = {}

    @property
    def active_count(self):
        with self._lock:
            return len(self._runs)

    def _recover_orphans(self):
        # Admission lock serializes begin + execution-lock acquisition across
        # processes. A free intake lock therefore proves no sender owns this row.
        from eidolon_cli.organization_service import _ExecutionLock
        for intake_id in self.store.unfinished_intake_ids():
            with _ExecutionLock(self.organization.home / 'organization' / 'intake-locks', intake_id) as acquired:
                if acquired:
                    self.store.fail_intake(intake_id,
                        'Intake was interrupted. Its charge is retained if dispatch began; no automatic replay.')

    def read(self, intake_id):
        from eidolon_cli.organization_intake_observation import read_intakes
        return read_intakes(self.organization.home, intake_id=intake_id)

    def recent(self):
        from eidolon_cli.organization_intake_observation import read_intakes
        return read_intakes(self.organization.home)

    def recover(self):
        """Explicit authenticated recovery retains charges and never replays a send."""
        from eidolon_cli.organization_service import _ExecutionLock
        with self._lock, _ExecutionLock(self.organization.home / 'organization' / 'intake-locks', 'admission') as admitted:
            if not admitted:
                raise ValueError('Another intake admission is active; retry recovery later')
            self._recover_orphans()
        return self.recent()

    def submit(self, text, *, idempotency_key, owner_priority=None, parent_intake_id=None, force_separate=False):
        from eidolon_cli.organization_service import _ExecutionLock
        # Dispatch callbacks may count other services under their admission locks.
        # Never invoke them while holding this service's running-record lock.
        if not self.organization.can_dispatch():
            raise ValueError('Organization admission is unavailable')
        with self._lock, _ExecutionLock(self.organization.home / 'organization' / 'intake-locks', 'admission') as admitted:
            if not admitted:
                raise ValueError('Another intake admission is active; retry with the same key')
            self._recover_orphans()
            if self.organization._stop.is_set():
                raise ValueError('Organization is stopping; no intake was admitted')
            intake, created = self.store.begin_intake(text, idempotency_key=idempotency_key, owner_priority=owner_priority, parent_intake_id=parent_intake_id, force_separate=force_separate)
            if not created:
                return intake
            locks = ExitStack()
            launched = False
            try:
                if not locks.enter_context(_ExecutionLock(self.organization.home / 'organization' / 'intake-locks', intake['id'])):
                    raise ValueError('Intake is already being processed')
                for index in range(2):
                    if locks.enter_context(_ExecutionLock(self.organization.home / 'organization' / 'intake-locks', f'slot-{index}')):
                        break
                else:
                    raise ValueError('Two intake calls are already active or stopping')
                record = _Run(intake['id'])
                record.thread = threading.Thread(target=self.organization._context.copy().run,
                    args=(self._work, record, locks), name='organization-intake', daemon=True)
                self._runs[record.id] = record
                record.thread.start()
                launched = True
            except BaseException:
                self._runs.pop(intake['id'], None)
                self.store.fail_intake(intake['id'], 'Intake could not start; no automatic retry')
                raise
            finally:
                if not launched:
                    locks.close()
            return self.store.intake_read(intake['id'])

    def _work(self, record, locks):
        from eidolon_cli.organization_intake_executor import execute
        received = False
        try:
            if record.cancel.is_set() or self.organization._stop.is_set():
                raise ValueError('Organization stopped before intake dispatch')
            self.store.prepare_intake_inspection(record.id, cancel=record.cancel)
            result = execute(self.store.intake_prompt(record.id), cancel=record.cancel,
                reserve_model_call=lambda **values: self.store.reserve_intake_call(record.id, **values))
            received = True
            self.store.record_intake_usage(record.id, result.get('usage') or {})
            if record.cancel.is_set() or self.organization._stop.is_set():
                raise ValueError('Organization stopped before accepting intake response')
            value = self.store.finish_intake(record.id, result['raw'], usage=result.get('usage'))
            if value['status'] == 'proposed':
                # Existing objective admission owns every project/authority/budget
                # check. Start scheduling only after its transaction has committed.
                admitted = self.store.admit_intake(record.id)
                if admitted['status'] == 'admitted':
                    self.organization.start()
        except Exception:
            # Provider/internal exception strings may contain endpoint details;
            # persist only a bounded generic user-safe reason.
            self.store.fail_intake(record.id,
                'Intake could not complete. Review its retained receipt and use manual submission if needed.',
                known_response=received)
        finally:
            locks.close()
            with self._lock:
                self._runs.pop(record.id, None)

    def stop(self, timeout=2.0):
        import time
        deadline = time.monotonic() + timeout
        with self._lock:
            runs = list(self._runs.values())
            for record in runs:
                record.cancel.set()
        for record in runs:
            self.store.fail_intake(record.id, 'Organization stopped during intake; no automatic replay')
            if record.thread is not None and record.thread is not threading.current_thread():
                record.thread.join(timeout=max(0, deadline - time.monotonic()))
        return self.active_count == 0
