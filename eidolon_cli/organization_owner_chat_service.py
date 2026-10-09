"""Explicit-send-only owner chat. Reads recover abandoned calls, never replay them."""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass, field
import logging
import threading
import time

from eidolon_cli.organization_owner_chat import LIVE, TIMEOUT_SECONDS, exact_id

logger = logging.getLogger(__name__)


def _execute(context, cancel):
    from eidolon_cli.organization_owner_chat_executor import execute
    return execute(context, cancel)


@dataclass
class _Turn:
    id: str
    cancel: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    started: float = field(default_factory=time.monotonic)


class OwnerChatService:
    def __init__(self, organization):
        self.organization = organization
        self.store = organization.store
        self._lock = threading.RLock()
        self._running_lock = threading.Lock()
        self._running = {}

    @property
    def active_count(self):
        with self._running_lock:
            return len(self._running)

    def _fence(self, key):
        from eidolon_cli.organization_service import _ExecutionLock
        return _ExecutionLock(self.organization.home / 'organization' / 'owner-chat-locks', key)

    def open(self, agent_id, identity_id):
        thread_id = self.store.owner_chat_open(agent_id, identity_id)
        return self.read(thread_id, identity_id)

    def read(self, thread_id, identity_id):
        # Validate profile/identity before touching any fence name.
        view = self.store.owner_chat_read(thread_id, identity_id)
        with self._fence(thread_id) as available:
            if available:
                self.store.owner_chat_recover(thread_id, identity_id)
                view = self.store.owner_chat_read(thread_id, identity_id)
            elif view['canSend']:
                view.update(canSend=False, unavailableReason='The previous reply is still stopping; wait for its provider to exit.')
        from eidolon_cli.organization_owner_chat_executor import readiness_reason
        reason = readiness_reason(view['recipient'])
        if reason and view['canSend']:
            view.update(canSend=False, unavailableReason=reason)
        if self.organization._stop.is_set():
            view.update(canSend=False, unavailableReason='Backend is stopping; reconnect after its active replies exit.')
        return view

    def send(self, thread_id, identity_id, text, reply_to, key):
        with self._lock:
            if self.store.owner_chat_recorded(thread_id, identity_id, text, reply_to, key):
                return self.read(thread_id, identity_id)
            view = self.store.owner_chat_read(thread_id, identity_id)
            from eidolon_cli.organization_owner_chat_executor import readiness_reason
            reason = readiness_reason(view['recipient'])
            if reason:
                raise ValueError(reason)
            if self.organization._stop.is_set() or not self.organization.can_dispatch():
                raise RuntimeError('Backend is stopping; no owner-chat send was admitted')
            locks = ExitStack()
            started = False
            try:
                if not locks.enter_context(self._fence(thread_id)):
                    # Another UI may have committed this exact send while we
                    # waited for the lock: return its receipt, never resend.
                    if self.store.owner_chat_recorded(thread_id, identity_id, text, reply_to, key):
                        return self.store.owner_chat_read(thread_id, identity_id)
                    raise ValueError('This member already has an active or stopping owner-chat turn')
                self.store.owner_chat_recover(thread_id, identity_id)
                for index in range(2):
                    if locks.enter_context(self._fence(f'profile-slot-{index}')):
                        break
                else:
                    raise ValueError('Two owner-chat replies are active or stopping; wait before sending')
                turn_id, created = self.store.owner_chat_reserve(thread_id, identity_id, text, reply_to, key)
                if not created:
                    return self.store.owner_chat_read(thread_id, identity_id)
                record = _Turn(turn_id)
                record.thread = threading.Thread(target=self.organization._context.copy().run,
                    args=(self._work, record, locks), name='organization-owner-chat', daemon=True)
                try:
                    with self._running_lock:
                        stopped = self.organization._stop.is_set()
                        if not stopped:
                            self._running[turn_id] = record
                            try:
                                record.thread.start()
                            except BaseException:
                                self._running.pop(turn_id, None)
                                raise
                    if stopped:
                        self.store.owner_chat_finish(turn_id, status='uncertain', reason='Backend stopped before dispatch; the reserved send will not replay automatically.')
                        return self.store.owner_chat_read(thread_id, identity_id)
                except BaseException:
                    self.store.owner_chat_finish(turn_id, status='uncertain', reason='Backend could not start the reserved send; it will not replay automatically.')
                    raise
                started = True
            finally:
                if not started:
                    locks.close()
            return self.store.owner_chat_read(thread_id, identity_id)

    def cancel(self, thread_id, identity_id, turn_id):
        exact_id(turn_id, 'turnId')
        with self._lock:
            view = self.store.owner_chat_read(thread_id, identity_id)
            if not any(turn['id'] == turn_id for turn in view['turns']):
                raise ValueError('Turn does not belong to this exact owner conversation')
            self.store.owner_chat_finish(turn_id, status='cancelled', reason='Owner cancelled this reply; provider usage may already have occurred.')
            with self._running_lock:
                if turn_id in self._running:
                    self._running[turn_id].cancel.set()
        return self.read(thread_id, identity_id)

    def _work(self, record, locks):
        watcher = None
        done = threading.Event()
        try:
            context = self.store.owner_chat_context(record.id)
            context['reserveModelCall'] = lambda **kwargs: self.store.owner_chat_dispatch(record.id, **kwargs)

            def watch():
                while not done.wait(0.05):
                    try:
                        state = self.store.owner_chat_turn_state(record.id)
                        if state['status'] not in LIVE:
                            record.cancel.set()
                            return
                        if time.monotonic() - record.started >= TIMEOUT_SECONDS:
                            record.cancel.set()
                            self.store.owner_chat_finish(record.id, status='timed_out', reason='Reply timed out; provider usage may be unknown. It will not replay automatically.')
                            return
                    except Exception:
                        record.cancel.set()
                        try:
                            self.store.owner_chat_finish(record.id, status='blocked', reason='Owner-chat authority or storage changed; no reply was accepted.')
                        except Exception:
                            logger.warning('Could not persist fenced owner-chat turn %s', record.id)
                        return

            watcher = threading.Thread(target=self.organization._context.copy().run, args=(watch,),
                                       name='owner-chat-lease', daemon=True)
            watcher.start()
            if record.cancel.is_set() or self.organization._stop.is_set():
                self.store.owner_chat_finish(record.id, status='uncertain', reason='Backend stopped before dispatch; the reserved send will not replay automatically.')
                return
            result = _execute(context, record.cancel)
            if record.cancel.is_set():
                return
            if time.monotonic() - record.started >= TIMEOUT_SECONDS:
                self.store.owner_chat_finish(record.id, status='timed_out', reason='Reply timed out; provider usage may be unknown. It will not replay automatically.')
                return
            if 'reply' in result:
                self.store.owner_chat_finish(record.id, status='completed', reply=result['reply'], usage=result.get('usage'))
            else:
                state = self.store.owner_chat_turn_state(record.id)
                self.store.owner_chat_finish(record.id, status='uncertain' if state['dispatched'] else 'blocked',
                                             reason=result.get('error', 'No complete owner-chat reply was received.'))
        except Exception:
            try:
                state = self.store.owner_chat_turn_state(record.id)
                dispatched = state['dispatched']
            except Exception:
                dispatched = True
            try:
                self.store.owner_chat_finish(record.id, status='uncertain' if dispatched else 'blocked',
                                             reason='Owner-chat execution stopped; no reply was accepted and no retry will run automatically.')
            except Exception:
                logger.warning('Could not persist owner-chat failure %s', record.id)
        finally:
            done.set()
            if watcher is not None:
                watcher.join(timeout=0.2)
            locks.close()
            with self._running_lock:
                self._running.pop(record.id, None)

    def close_admission(self):
        with self._running_lock:
            for record in self._running.values():
                record.cancel.set()

    def stop(self, timeout):
        with self._running_lock:
            records = list(self._running.values())
            for record in records:
                record.cancel.set()
        persisted = True
        for record in records:
            try:
                self.store.owner_chat_finish(record.id, status='uncertain', reason='Backend stopped during this reply; it will never replay automatically.')
            except Exception:
                persisted = False
                logger.warning('Could not persist stopped owner-chat turn %s', record.id)
        deadline = time.monotonic() + max(0, timeout)
        for record in records:
            if record.thread is not threading.current_thread():
                record.thread.join(timeout=max(0, deadline - time.monotonic()))
        return not self.active_count and persisted
