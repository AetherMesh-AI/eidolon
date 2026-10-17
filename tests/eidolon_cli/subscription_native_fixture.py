"""Synthetic TLS/native-vault edges for the real, host-native subscription path.

Production guards, owner admission and send decisions are not replaced. Optional
recovery faults hold a real writer or change one owned directory's mode so the
unchanged production guard refuses it. Never install outside disposable tests.
The private RSA key and vault values are synthetic and live only in this process.
"""
from collections import deque
from contextlib import closing
from functools import wraps
import hashlib
import http.client
import io
import ipaddress
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import socket
import threading
import time
import traceback
from types import CodeType, FunctionType
from urllib.parse import parse_qs, urlencode, urlsplit
import uuid
import webbrowser

from cryptography.hazmat.primitives.asymmetric import rsa
import jwt
import yaml

MODEL = 'synthetic-subscription-model'
CLIENT = 'synthetic-subscription-client-never-real'
ACCESS = 'synthetic-subscription-access-never-real'
TEAM = {
    'executive': {'name': 'Ash', 'purpose': 'Coordinate synthetic studio work',
                  'responsibilities': ['Review outcomes']},
    'members': [
        {'id': 'studio-lead', 'name': 'Ember', 'role': 'Manager',
         'manager_id': 'executive', 'team': 'studio',
         'capabilities': ['request.plan', 'request.integrate'],
         'purpose': 'Plan studio drafts', 'responsibilities': ['Coordinate drafts']},
        {'id': 'studio-writer', 'name': 'Flint', 'role': 'Worker',
         'manager_id': 'studio-lead', 'team': 'studio', 'capabilities': ['work.draft'],
         'purpose': 'Write studio drafts', 'responsibilities': ['Produce reviewed drafts']},
    ],
}
SUGGESTIONS = {'jobs': 'Prepare clear studio drafts for owner review.',
               'goals': 'Deliver reviewed drafts with explicit acceptance criteria.'}


def snapshot(home):
    """Read evidence independently of the gateway without initializing anything."""
    home = Path(home)
    with closing(sqlite3.connect((home / 'organization' / 'state.db').as_uri() + '?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        def rows(table):
            return [dict(row) for row in conn.execute('SELECT * FROM ' + table)] if table in tables else []
        return {'model': (yaml.safe_load((home / 'config.yaml').read_text()) or {}).get('model', {}),
                'metadata': rows('organization_metadata'),
                'executionPolicy': rows('organization_policy'),
                'budget': rows('organization_owner_budget_policy'),
                'generations': rows('organization_team_generations'),
                'teams': rows('organization_team_setup'),
                'subscriptionPolicy': rows('organization_setup_subscription_policy_receipts'),
                'attempts': rows('organization_setup_subscription_attempts'),
                'dispatches': rows('organization_setup_subscription_dispatches'),
                'outcomes': rows('organization_setup_subscription_outcomes'),
                'agents': rows('agents'),
                'counts': {table: len(rows(table)) for table in (
                    'objectives', 'requests', 'tasks', 'organization_model_calls', 'owner_chat_threads')}}


def seed_profile(home, *, legacy=False):
    """Empty means no real organization work, including when old setup debt exists."""
    from eidolon_cli.organization_metadata import save_metadata
    from eidolon_cli.organization_store import OrganizationStore
    home = Path(home).resolve(strict=True)
    home.chmod(0o700)
    path = home / 'organization' / 'state.db'
    identity = save_metadata(path, display_name='Synthetic Subscription Studio',
        owner_display_name='Synthetic Owner', expected_organization_id=None,
        expected_revision=None, idempotency_key='synthetic-native-identity')['metadata']
    OrganizationStore(path)
    if legacy:
        with closing(sqlite3.connect(path)) as conn:
            conn.execute('INSERT INTO organization_team_generations '
                '(idempotency_key,organization_id,input_hash,source_revision,status,dispatched,reserved_tokens,created) '
                'VALUES (?,?,?,?,?,?,?,0)', ('synthetic-legacy', identity['organizationId'],
                    'synthetic-old-input', 'synthetic-old-source', 'failed', 1, 200))
            conn.commit()
    return identity


class Vault:
    """The only replaced storage layer is the native key-value primitive."""
    def __init__(self):
        self.values = {}
        self.lock = threading.Lock()
        self.put_count = 0
        self.delete_count = 0

    def put(self, binding, value):
        from eidolon_cli.organization_credential_native import binding_reference
        reference = binding_reference(binding)
        with self.lock:
            self.put_count += 1
            self.values[reference] = value
        return reference

    def consume(self, reference, binding, callback):
        from eidolon_cli.organization_credential_native import binding_reference
        assert reference == binding_reference(binding)
        with self.lock:
            value = self.values[reference]
        callback(value)

    def delete(self, reference, binding):
        from eidolon_cli.organization_credential_native import binding_reference
        assert reference == binding_reference(binding)
        with self.lock:
            self.delete_count += 1
            self.values.pop(reference, None)


class BoundaryDiagnostics:
    """Bounded test-only observation. Never inspect frame locals or stringify errors."""
    _STAGES = {
        'tui_gateway.methods_organization_setup_subscription': {
            '_method.<locals>.handler': 'gateway_rpc',
            'dispatch': 'gateway_dispatch', '_context': 'gateway_context', '_service': 'gateway_service',
            '_durable_scope': 'owner_scope', '_context.<locals>.guard': 'owner_guard',
            '_context.<locals>.setup_guard': 'setup_guard',
            'subscription_preflight': 'subscription_preflight',
            'subscription_preflight.<locals>.release': 'subscription_release'},
        'tui_gateway.methods_organization_team_setup': {'_team_method.<locals>.handler': 'team_rpc'},
        'eidolon_cli.organization_setup_subscription_catalog': {
            'SubscriptionCatalog.load': 'catalog_load', 'SubscriptionCatalog._guard': 'catalog_guard',
            'SubscriptionCatalog._descriptor': 'catalog_descriptor'},
        'eidolon_cli.organization_setup_subscription_auth': {
            'SubscriptionAuthService.status': 'auth_status', 'SubscriptionAuthService._guard': 'auth_guard',
            'SubscriptionAuthService.current_descriptor': 'auth_descriptor',
            'SubscriptionAuthService.lease_access_current': 'auth_lease',
            'SubscriptionAuthService._lease_guard': 'auth_lease_guard',
            'SubscriptionAuthService.release_lease': 'auth_release'},
        'eidolon_cli.organization_setup_subscription_store': {
            'SubscriptionStore._locked': 'store_lock', 'SubscriptionStore.status': 'store_status',
            '_file_identity': 'filesystem_identity',
            '_checked_acl_descriptor': 'acl_descriptor', '_check_darwin_acl': 'darwin_acl',
            'SubscriptionStore._check': 'filesystem_check',
            'SubscriptionStore.connection_descriptor': 'store_descriptor',
            'SubscriptionStore.grant_descriptor': 'store_grant', 'SubscriptionStore._decrypt': 'store_decrypt',
            'SubscriptionStore._fence': 'store_fence', 'SubscriptionStore.lease_access_token': 'store_lease',
            'TokenLease._check_locked': 'lease_fence', 'TokenLease.run': 'lease_run',
            'TokenLease.run_nonblocking': 'lease_run_nonblocking',
            'TokenLease.try_send_allowed': 'lease_send_gate', 'TokenLease.close': 'lease_close'},
        'eidolon_cli.organization_setup_subscription_http': {
            'request': 'catalog_http', 'request.<locals>.GuardedConnection.send': 'catalog_http_send'},
        'eidolon_cli.organization_setup_subscription_transport': {
            'send_once': 'inference_transport', 'prepare_subscription_send': 'prepare_send'},
        'eidolon_cli.organization_team_generation': {'complete_subscription': 'complete_subscription'},
        'eidolon_cli.organization_setup_subscription_usage': {
            'AttemptBindingV1.__post_init__': 'attempt_validation',
            'UsageObservationV1.__post_init__': 'usage_validation',
            'TransportObservationV1.__post_init__': 'observation_validation',
            'TransportResult.__post_init__': 'result_validation'},
        'eidolon_cli.organization_store': {
            'OrganizationStore._write': 'organization_write',
            'OrganizationStore.recover_expired': 'scheduler_recover',
            'OrganizationStore.claim_next': 'scheduler_claim'},
        'eidolon_cli.organization_goal_continuation': {
            'OrganizationGoalContinuationStore.advance_goal_continuation': 'scheduler_continue'},
        'eidolon_cli.organization_setup_subscription_admission': {
            '_ledger_connection': 'ledger_connection',
            '_Admission.try_send_allowed': 'admission_send_gate',
            '_Admission.__init__': 'admission_create', 'infer_subscription': 'infer_subscription',
            '_persist': 'accounting_wait', '_persist.<locals>.run': 'accounting_worker',
            '_failed_job': 'failed_job', '_dispatch_job': 'dispatch_job', '_outcome_job': 'outcome_job'},
    }
    _ENTRIES = {'gateway_dispatch', 'catalog_load', 'auth_status', 'auth_descriptor', 'auth_lease',
                'store_status', 'store_grant', 'store_lease', 'lease_run', 'lease_run_nonblocking',
                'catalog_http', 'catalog_http_send', 'inference_transport', 'infer_subscription',
                'prepare_send', 'complete_subscription', 'subscription_preflight', 'subscription_release',
                'admission_create', 'accounting_wait', 'accounting_worker', 'failed_job', 'dispatch_job', 'outcome_job'}
    _ERRORS = {'builtins': {'BlockingIOError', 'OSError', 'ValueError', 'TypeError', 'AssertionError',
                              'KeyError', 'FileNotFoundError', 'TimeoutError'},
        'sqlite3': {'OperationalError'},
        'eidolon_cli.organization_executor': {'OrganizationExecutionError'},
        'eidolon_cli.organization_owner_policy': {'OwnerPolicyError'},
        'tui_gateway.methods_organization_setup_subscription': {'BoundaryError'},
        'eidolon_cli.organization_setup_subscription_catalog': {'CatalogError'},
        'eidolon_cli.organization_setup_subscription_auth': {'AuthError'},
        'eidolon_cli.organization_setup_subscription_store': {'StoreError'},
        'eidolon_cli.organization_setup_subscription_http': {'HttpError'},
        'eidolon_cli.organization_setup_subscription_transport': {'TransportError'}}
    _REASONS = frozenset({'connection_unavailable', 'recursive_use', 'unsafe_filesystem',
        'filesystem_protection_unavailable', 'backend_unavailable', 'corrupt_store', 'recovery_required',
        'storage_recovery_required', 'operation_failed', 'stale_revision', 'stale_owner', 'stale_attempt',
        'setup_unavailable', 'owner_generation_changed', 'lease_cancelled', 'lease_used', 'grant_expired',
        'invalid_grant', 'invalid_binding', 'scope_denied', 'scope_not_authorized', 'invalid_lease_result',
        'invalid_request', 'request_failed', 'locked', 'corrupt', 'unreadable', 'source_conflict',
        'identity_conflict', 'unconfigured', 'cancelled', 'stale_binding', 'accounting_failed',
        'admission_denied', 'connect_failed', 'incomplete_stream', 'invalid_headers', 'resource_limit',
                'access_denied', 'attempt_expired', 'attempt_pending', 'auth_attempt_mismatch',
        'authentication_failed', 'browser_unavailable', 'disconnect_required', 'exchange_failed',
        'insufficient_scope', 'invalid_callback', 'invalid_identity', 'invalid_secret',
        'native_reference_mismatch', 'operation_history_exhausted', 'operation_mismatch', 'publication_failed',
        'publication_uncertain', 'reauthorization_required', 'refresh_pending', 'refresh_uncertain',
        'revision_conflict', 'revision_exhausted', 'revision_exists', 'ticket_conflict', 'ticket_retired',
        'ticket_sequence_exhausted', 'transition_pending', 'typed_grant_requires_handoff', 'value_too_large'})

    def __init__(self, evidence, *, home=None):
        self.path = Path(evidence) / 'subscription-boundaries.jsonl'
        self.home = home if type(home) is type(Path()) else None
        self.lock = threading.Lock()
        self.flush_lock = threading.Lock()
        self.lanes, self.holders, self.ledger_holders = {}, {}, {}
        self.codes = {}
        self.code_sources = {}
        for module, stages in self._STAGES.items():
            for qualname, label in stages.items():
                self.code_sources.setdefault(qualname, []).append((module, label))
        self.count = 0
        self.rows = deque(maxlen=512)
        self.enabled = False
        self.old_main = sys.gettrace()
        self.old_threads = threading.gettrace()

    def stage(self, frame):
        code = frame.f_code
        sources = self.code_sources.get(code.co_qualname)
        if sources is None:
            return None
        # HandlerRegistry rebinds split-module functions onto server globals.
        # Resolve only exact loaded code objects from the closed source list;
        # the frame's globals/name are not proof of the code's source module.
        for module_name, label in sources:
            module = sys.modules.get(module_name)
            if module is None:
                continue
            if frame.f_globals is not vars(module):
                server = sys.modules.get('tui_gateway.server')
                if (label not in {'gateway_rpc', 'team_rpc'} or server is None
                        or frame.f_globals is not vars(server)):
                    continue
            if code in self.codes:
                return self.codes[code]
            target = vars(module).get(code.co_qualname.split('.')[0])
            if isinstance(target, type):
                target = vars(target).get(code.co_qualname.split('.')[1])
            candidates = []
            for _ in range(8):
                if not isinstance(target, FunctionType):
                    break
                candidates.append(target.__code__)
                target = vars(target).get('__wrapped__')
            for _ in range(128):
                if not candidates:
                    break
                candidate = candidates.pop()
                if candidate is code:
                    if len(self.codes) < 128:
                        self.codes[code] = label
                    return label
                candidates.extend(value for value in candidate.co_consts if isinstance(value, CodeType))
        return None

    def identity_scope(self, path):
        # Synthetic fixture home is already captured. No filesystem lookups or
        # coercion of arbitrary objects; only concrete Path's lexical operations.
        if self.home is None or type(path) is not type(self.home):
            return 'other'
        private = self.home / '.setup-subscription-private'
        if path == private or path.is_relative_to(private):
            return 'private_store'
        if path == self.home:
            return 'profile'
        if path in self.home.parents:
            return 'ancestor'
        return 'other'

    def observe_identity(self, frame, value, lane):
        parent = frame.f_back
        sites = {('acl_descriptor', 148): 'initial_fd',
                 ('acl_descriptor', 151): 'final_fd',
                 ('acl_descriptor', 152): 'final_path',
                 ('darwin_acl', 203): 'native_acl'}
        site = sites.get((self.stage(parent), parent.f_lineno)) if parent else None
        if (site is None or not lane or type(value) is not tuple or len(value) != 7
                or not all(type(part) is int for part in value)):
            return
        # Renewed diagnostic scope: exactly two locals from this verified caller.
        # Exact stat_result prevents arbitrary getters; all comparison data dies
        # in this callback. No filesystem call, path coercion or tuple retention.
        before = parent.f_locals.get('before')
        if type(before) is not os.stat_result:
            return
        baseline = (before.st_dev, before.st_ino, before.st_mode, before.st_uid,
                    before.st_gid, before.st_nlink, before.st_ctime_ns)
        if not all(type(part) is int for part in baseline):
            return
        changed = tuple(a != b for a, b in zip(value, baseline))
        if not any(changed):
            return  # The right-hand operand and unchanged snapshots emit nothing.
        kind = ('directory' if stat.S_ISDIR(value[2]) and stat.S_ISDIR(baseline[2]) else
                'regular_file' if stat.S_ISREG(value[2]) and stat.S_ISREG(baseline[2]) else 'other')
        scope = self.identity_scope(parent.f_locals.get('path'))
        self.emit({'lane': lane, 'stage': 'filesystem_identity', 'line': parent.f_lineno,
            'event': 'identity_mismatch', 'identity': {'comparison': site, 'targetKind': kind,
                'targetScope': scope, 'changedFields': dict(zip(
                    ('device', 'inode', 'mode', 'owner', 'group', 'links', 'changeTime'), changed))}})

    @staticmethod
    def sqlite_primary_code(error):
        if type(error) is not sqlite3.OperationalError:
            return 'other'
        attributes = vars(error)
        code = attributes.get('sqlite_errorcode') if type(attributes) is dict else None
        if type(code) is not int or not 0 <= code <= 2**31 - 1:
            return 'other'
        return {sqlite3.SQLITE_BUSY: 'SQLITE_BUSY', sqlite3.SQLITE_LOCKED: 'SQLITE_LOCKED',
                sqlite3.SQLITE_CANTOPEN: 'SQLITE_CANTOPEN', sqlite3.SQLITE_READONLY: 'SQLITE_READONLY',
                sqlite3.SQLITE_IOERR: 'SQLITE_IOERR', sqlite3.SQLITE_FULL: 'SQLITE_FULL'}.get(code & 255, 'other')

    def emit(self, value):
        # Critical-frame callbacks touch bounded memory only, never the filesystem.
        with self.lock:
            self.count = min(self.count + 1, 2**31 - 1)
            self.rows.append({'sequence': self.count, **value})

    def flush(self):
        # Called only after the outer RPC handler has released its locks, or at
        # fixture installation/teardown. Never from a store/lease/send frame.
        with self.flush_lock:
            with self.lock:
                rows = list(self.rows)
                if self.count > 512:
                    rows.insert(0, {'sequence': 0, 'stage': 'observer', 'event': 'overflow'})
            try:
                self.path.write_text(''.join(json.dumps(row, sort_keys=True) + '\n' for row in rows), encoding='utf-8')
            except OSError:
                pass  # Diagnostic persistence must not change a production outcome.

    def trace(self, frame, event, arg):
        try:
            return self._trace(frame, event, arg)
        except Exception:
            # Observer failure or shutdown must never change the observed outcome.
            return None

    def _trace(self, frame, event, arg):
        if not self.enabled:
            return None
        stage = self.stage(frame)
        if stage is None:
            return None
        frame.f_trace_lines = False
        frame.f_trace_opcodes = False
        lane_key = threading.get_ident()
        with self.lock:
            if lane_key not in self.lanes and len(self.lanes) < 32:
                self.lanes[lane_key] = len(self.lanes) + 1
            lane = self.lanes.get(lane_key, 0)
        value = {'lane': lane, 'stage': stage, 'line': frame.f_lineno}
        if stage == 'filesystem_identity' and event == 'return':
            self.observe_identity(frame, arg, lane)
        # Generator return(True) is the acquired-lock yield; final return(None)
        # occurs after its finally releases flock. Only fixed caller labels live
        # in memory, never a retained frame, local value, path or identifier.
        if stage == 'store_lock' and event == 'return':
            if arg is True and lane:
                parent = frame.f_back
                owner = 'other'
                for _ in range(10):
                    if parent is None:
                        break
                    label = self.stage(parent)
                    if label is not None and label != 'store_lock':
                        owner = label
                        break
                    parent = parent.f_back
                with self.lock:
                    self.holders[lane_key] = {'lane': lane, 'stage': owner}
            elif arg is None:
                with self.lock:
                    self.holders.pop(lane_key, None)
        # This exact generator yields only after its BEGIN IMMEDIATE succeeds.
        # Keep only lane/caller enums; never retain or inspect the connection.
        if stage == 'organization_write' and event == 'return':
            if type(arg) is sqlite3.Connection and lane:
                parent, owner = frame.f_back, 'other'
                for _ in range(10):
                    if parent is None:
                        break
                    label = self.stage(parent)
                    if label in {'scheduler_recover', 'scheduler_claim', 'scheduler_continue'}:
                        owner = label
                        break
                    parent = parent.f_back
                with self.lock:
                    self.ledger_holders[lane_key] = {'lane': lane, 'stage': owner}
            elif arg is None:
                with self.lock:
                    self.ledger_holders.pop(lane_key, None)
        if event == 'return' and stage in {'gateway_rpc', 'team_rpc'}:
            self.flush()
        if event == 'call' and stage in self._ENTRIES:
            self.emit({**value, 'event': 'enter'})
        elif event == 'exception':
            error = arg[1]
            kind = type(error)
            if kind in {StopIteration, GeneratorExit}:
                return self.trace
            recognized = (kind.__name__ in self._ERRORS.get(kind.__module__, set())
                and vars(sys.modules[kind.__module__]).get(kind.__name__) is kind)
            attributes = vars(error) if recognized else {}
            if type(attributes) is not dict:
                attributes = {}
            reason = attributes.get('reason')
            if reason is None and recognized and kind.__name__ == 'TransportError':
                reason = attributes.get('code')
            with self.lock:
                holders = list(self.holders.values())[:8]
                ledger_holders = list(self.ledger_holders.values())[:8]
            self.emit({**value, 'event': 'exception',
                'errorClass': kind.__name__ if recognized else 'other',
                'reason': reason if type(reason) is str and reason in self._REASONS else 'other',
                'sqlitePrimaryCode': self.sqlite_primary_code(error),
                'holders': holders, 'ledgerHolders': ledger_holders})
        elif event == 'return' and arg is False:
            with self.lock:
                holders = list(self.holders.values())[:8]
            self.emit({**value, 'event': 'returned_false', 'holders': holders})
        return self.trace

    def install(self):
        if self.old_main is not None or self.old_threads is not None:
            self.emit({'stage': 'observer', 'event': 'existing_trace'})
            self.flush()
            return
        self.emit({'stage': 'observer', 'event': 'installed',
                   'existingThreadsCovered': hasattr(threading, 'settrace_all_threads')})
        self.flush()  # Precreate evidence before tracing any production frame.
        self.enabled = True
        threading.settrace(self.trace)
        if hasattr(threading, 'settrace_all_threads'):
            threading.settrace_all_threads(self.trace)
        sys.settrace(self.trace)

    def close(self):
        if self.enabled:
            threading.settrace(self.old_threads)
            if hasattr(threading, 'settrace_all_threads'):
                threading.settrace_all_threads(self.old_threads)
            sys.settrace(self.old_main)
            self.enabled = False
        self.flush()


class RecoveryFaults:
    """One real local refusal, confined to this disposable profile's operation.

    No result, stat value, ACL decision, admission or send gate is replaced.
    Counters remain in memory while production critical frames are active.
    """
    def __init__(self, home, kind):
        if kind not in {'sqlite_dispatch_once', 'acl_publication_once'}:
            raise ValueError('Unknown synthetic recovery fault')
        self.home = Path(home).resolve(strict=True)
        info = self.home.lstat()
        if not stat.S_ISDIR(info.st_mode) or (
                (os.name == 'posix' or kind == 'acl_publication_once')
                and stat.S_IMODE(info.st_mode) != 0o700):
            raise ValueError('Synthetic recovery home must be a private directory')
        self.identity = (info.st_dev, info.st_ino)
        self.kind = kind
        self.triggered = 0
        self.restored = 0
        self._local = threading.local()
        self._restore_fd = None
        self._lock = threading.Lock()
        self._claimed = False
        self.operation_thread = None

    def _home_store(self, value, store_type):
        return type(value) is store_type and value.profile == self.home

    def _restore_mode(self):
        if self._restore_fd is not None:
            fd, self._restore_fd = self._restore_fd, None
            try:
                os.fchmod(fd, 0o700)
                assert stat.S_IMODE(os.fstat(fd).st_mode) == 0o700
                self.restored += 1
            finally:
                os.close(fd)

    def _call_locked(self, function, prepared):
        # A real writer blocks the unchanged dispatch worker's BEGIN IMMEDIATE.
        # Its release precedes infer_subscription's actual failed-job cleanup.
        with closing(sqlite3.connect(self.home / 'organization' / 'state.db', timeout=0)) as writer:
            writer.execute('BEGIN IMMEDIATE')
            self.triggered += 1
            try:
                return function(prepared)
            finally:
                writer.rollback()
                self.restored += 1

    def install(self, patch):
        from eidolon_cli import organization_setup_subscription_store as store
        from eidolon_cli import organization_setup_subscription_admission as admission
        from eidolon_cli import organization_setup_subscription_transport as transport
        from eidolon_cli import organization_team_generation as generation
        if self.kind == 'sqlite_dispatch_once':
            original = generation.complete_subscription

            @wraps(original)
            def complete(prepared):
                selected = (type(prepared) is transport.PreparedSubscriptionSend
                    and type(prepared._admission) is admission._Admission
                    and prepared._admission.home == self.home)
                with self._lock:
                    inject = selected and not self._claimed
                    if inject:
                        self._claimed = True
                        self.operation_thread = threading.get_ident()
                if not inject:
                    return original(prepared)
                return self._call_locked(original, prepared)

            patch(generation, 'complete_subscription', complete)
            return

        original_connect = store.SubscriptionStore._connect_ticket_grant
        original_decrypt = store.SubscriptionStore._decrypt
        original_open = os.open
        descriptor_code = store._checked_acl_descriptor.__wrapped__.__code__

        @wraps(original_connect)
        def connect(instance, binding, secret, state):
            selected = (self._home_store(instance, store.SubscriptionStore)
                and type(state) is dict and state.get('phase') == 'disconnected'
                and state.get('revision') == 0 and self.triggered == 0)
            previous = getattr(self._local, 'consent_store', None)
            if selected:
                self._local.consent_store = instance
            try:
                return original_connect(instance, binding, secret, state)
            finally:
                self._local.consent_store = previous

        @wraps(original_decrypt)
        def decrypt(instance, state):
            selected = (self._home_store(instance, store.SubscriptionStore)
                and getattr(self._local, 'consent_store', None) is instance)
            previous = getattr(self._local, 'decrypt_store', None)
            if selected:
                self._local.decrypt_store = instance
            try:
                return original_decrypt(instance, state)
            finally:
                if selected:
                    self._restore_mode()
                self._local.decrypt_store = previous

        @wraps(original_open)
        def opened(path, flags, *args, **kwargs):
            fd = original_open(path, flags, *args, **kwargs)
            selected = (type(path) in {str, type(self.home)} and path in {self.home, str(self.home)}
                and getattr(self._local, 'decrypt_store', None) is not None
                and sys._getframe(1).f_code is descriptor_code)
            if not selected:
                return fd
            with self._lock:
                if self.triggered:
                    return fd
                try:
                    info, current = os.fstat(fd), self.home.lstat()
                    assert (info.st_dev, info.st_ino) == self.identity
                    assert store._file_identity(info) == store._file_identity(current)
                    assert stat.S_IMODE(info.st_mode) == 0o700
                    self._restore_fd = os.dup(fd)
                    os.fchmod(self._restore_fd, 0o500)
                    self.triggered += 1
                except BaseException:
                    self._restore_mode()
                    os.close(fd)
                    raise
            return fd

        patch(store.SubscriptionStore, '_connect_ticket_grant', connect)
        patch(store.SubscriptionStore, '_decrypt', decrypt)
        patch(os, 'open', opened)

    def close(self):
        self._restore_mode()


class SyntheticEdges:
    def __init__(self, home, *, evidence=None, mode='complete', recovery_fault=None):
        self.home = Path(home).resolve(strict=True)
        self.evidence = Path(evidence) if evidence else None
        self.mode = mode
        self.vault = Vault()
        self.events = []
        self.codes = {}
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.lock = threading.Lock()
        self.restores = []
        self.recovery_fault = RecoveryFaults(self.home, recovery_fault) if recovery_fault else None
        self._fault_reported = False
        self.boundary_diagnostics = BoundaryDiagnostics(self.evidence, home=self.home) if self.evidence else None

    def record(self, kind, **detail):
        value = {'kind': kind, 'physicalNetworkSend': False, **detail}
        with self.lock:
            self.events.append(value)
            if self.evidence:
                with (self.evidence / 'subscription-events.jsonl').open('a') as stream:
                    stream.write(json.dumps(value, sort_keys=True) + '\n')

    def report_recovery_fault(self, *, checkpoint=False):
        fault = self.recovery_fault
        if fault is None or (not checkpoint and (self._fault_reported or fault.triggered != 1)):
            return
        assert all(type(value) is int and 0 <= value <= 1000
                   for value in (self.vault.put_count, self.vault.delete_count))
        self.record('recovery-checkpoint' if checkpoint else 'recovery-fault',
            fault=fault.kind, triggered=fault.triggered == 1, restored=fault.restored == 1,
            nativePutCount=self.vault.put_count, nativeDeleteCount=self.vault.delete_count)
        if not checkpoint:
            self._fault_reported = True

    def record_denial(self, kind, operation, destination):
        # Function names locate cold-import/background callers without recording
        # arguments, source text, URLs, headers, tokens or user profile paths.
        frames = traceback.extract_stack(limit=16)[:-1]
        public_hosts = {'raw.githubusercontent.com', 'models.dev', 'huggingface.co',
                        'openaipublic.blob.core.windows.net'}
        self.record(kind, operation=operation,
                    knownPublicHost=destination if destination in public_hosts else '<redacted>',
                    destinationHash=hashlib.sha256(str(destination).encode()).hexdigest(),
                    stack=[{'file': Path(frame.filename).name, 'function': frame.name[:120],
                            'line': frame.lineno} for frame in frames])

    def patch(self, obj, name, value):
        self.restores.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def install(self):
        from eidolon_cli import organization_setup_subscription_store as store
        # Keep backend=None, _synthetic=False and every native filesystem/ACL check.
        self.patch(store, 'native_backend', lambda directory: self.vault)
        self.patch(webbrowser, 'open', self.browser_open)
        edges = self
        def connect(connection):
            if connection.host not in {'auth.openai.com', 'api.openai.com'}:
                edges.record_denial('denied-tls', 'HTTPSConnection.connect', connection.host)
                raise AssertionError('Unexpected external TLS endpoint')
            connection.sock = SyntheticSocket(edges, connection.host)
        self.patch(http.client.HTTPSConnection, 'connect', connect)
        self.install_network_guards()
        if self.recovery_fault is not None:
            self.recovery_fault.install(self.patch)
            if self.recovery_fault.kind == 'sqlite_dispatch_once':
                from eidolon_cli import organization_setup_subscription_admission as admission
                original_infer = admission.infer_subscription

                @wraps(original_infer)
                def infer(*args, **kwargs):
                    try:
                        return original_infer(*args, **kwargs)
                    finally:
                        # The real infer finally released the lease and finished
                        # failed-job handling before this evidence write.
                        if self.recovery_fault.operation_thread == threading.get_ident():
                            self.report_recovery_fault()

                self.patch(admission, 'infer_subscription', infer)
            else:
                from eidolon_cli.organization_setup_subscription_auth import SubscriptionAuthService
                original_recover = SubscriptionAuthService.recover_operation_current

                @wraps(original_recover)
                def recover(service):
                    result = original_recover(service)
                    # The real service/store/publication contexts have exited.
                    if service._store.profile == self.home:
                        self.report_recovery_fault(checkpoint=True)
                    return result

                self.patch(SubscriptionAuthService, 'recover_operation_current', recover)
        if self.boundary_diagnostics is not None:
            self.boundary_diagnostics.install()
        self.record('installed', nativeStoreMode='production', platform=__import__('sys').platform)
        return self

    def install_network_guards(self):
        # Also installed in registered-RPC pytest, where no desktop bootstrap runs.
        # A regression through another SDK or urllib cannot reach a real provider.
        def loopback(host):
            try:
                if isinstance(host, bytes):
                    host = host.decode('ascii')
                return ipaddress.ip_address(host).is_loopback
            except (ValueError, TypeError, UnicodeError):
                return False

        def deny(operation, destination):
            self.record_denial('denied-network', operation, destination)
            raise AssertionError('Synthetic fixture denied non-loopback network')

        def check(sock, address, operation):
            if sock.family != getattr(socket, 'AF_UNIX', None) and (not isinstance(address, tuple) or not loopback(address[0])):
                deny(operation, address[0] if isinstance(address, tuple) else address)

        original_connect, original_connect_ex = socket.socket.connect, socket.socket.connect_ex
        original_sendto, original_getaddrinfo = socket.socket.sendto, socket.getaddrinfo
        def connect(sock, address):
            check(sock, address, 'socket.connect')
            return original_connect(sock, address)
        def connect_ex(sock, address):
            check(sock, address, 'socket.connect_ex')
            return original_connect_ex(sock, address)
        def sendto(sock, data, *args):
            check(sock, args[-1], 'socket.sendto')
            return original_sendto(sock, data, *args)
        def getaddrinfo(host, *args, **kwargs):
            if host is not None and not loopback(host):
                deny('getaddrinfo', host)
            return original_getaddrinfo(host, *args, **kwargs)
        def gethostbyname(host):
            if not loopback(host):
                deny('gethostbyname', host)
            return host.decode('ascii') if isinstance(host, bytes) else host
        def gethostbyname_ex(host):
            address = gethostbyname(host)
            return address, [], [address]
        def getnameinfo(address, flags):
            if not isinstance(address, tuple) or not loopback(address[0]):
                deny('getnameinfo', address[0] if isinstance(address, tuple) else address)
            return str(address[0]), str(address[1])
        self.patch(socket.socket, 'connect', connect)
        self.patch(socket.socket, 'connect_ex', connect_ex)
        self.patch(socket.socket, 'sendto', sendto)
        self.patch(socket, 'getaddrinfo', getaddrinfo)
        self.patch(socket, 'gethostbyname', gethostbyname)
        self.patch(socket, 'gethostbyname_ex', gethostbyname_ex)
        self.patch(socket, 'gethostbyaddr', gethostbyname_ex)
        self.patch(socket, 'getnameinfo', getnameinfo)
        if hasattr(socket.socket, 'sendmsg'):
            original_sendmsg = socket.socket.sendmsg
            def sendmsg(sock, buffers, ancdata=(), flags=0, address=None):
                if address is not None:
                    check(sock, address, 'socket.sendmsg')
                return original_sendmsg(sock, buffers, ancdata, flags, address)
            self.patch(socket.socket, 'sendmsg', sendmsg)

    def close(self):
        try:
            if self.recovery_fault is not None:
                self.recovery_fault.close()
        finally:
            try:
                if self.boundary_diagnostics is not None:
                    self.boundary_diagnostics.close()
            finally:
                for obj, name, original in reversed(self.restores):
                    setattr(obj, name, original)
                self.restores.clear()

    def browser_open(self, url):
        from eidolon_cli.organization_setup_subscription_auth import AUTHORIZE, RESOURCE
        endpoint = urlsplit(url)
        assert endpoint.scheme + '://' + endpoint.netloc + endpoint.path == AUTHORIZE
        query = {key: value[0] for key, value in parse_qs(endpoint.query, strict_parsing=True).items()}
        assert query['resource'] == RESOURCE and query['code_challenge_method'] == 'S256'
        assert query['client_id'] in {'dynamic_agent_client', CLIENT}
        callback = urlsplit(query['redirect_uri'])
        assert callback.hostname == '127.0.0.1' and callback.path == '/auth/callback'
        code = 'synthetic-code-' + str(uuid.uuid4())
        self.codes[code] = query
        from tui_gateway import server
        authority = server._subscription_installation_authority()
        assert authority is not None and authority.root == self.home and authority.profile == self.home
        self.record('consent', dynamicRegistration=query['client_id'] == 'dynamic_agent_client',
                    capturedHostRoot=str(authority.root), capturedProfile=str(authority.profile))
        connection = http.client.HTTPConnection('127.0.0.1', callback.port, timeout=10)
        try:
            connection.request('GET', callback.path + '?' + urlencode({
                'state': query['state'], 'code': code, 'client_id': CLIENT}))
            response = connection.getresponse()
            assert response.status == 200 and response.read() == b'Return to Eidolon.'
        finally:
            connection.close()
        self.report_recovery_fault()
        return True

    def oauth(self, path, body):
        from eidolon_cli.organization_setup_subscription_auth import ISSUER, SCOPES
        if path == '/.well-known/jwks.json':
            assert body == b''
            self.record('jwks')
            return {'keys': [{**json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key())),
                              'kid': 'synthetic-key', 'alg': 'RS256', 'use': 'sig'}]}
        assert path == '/api/accounts/oauth/token'
        form = {key: value[0] for key, value in parse_qs(body.decode('ascii'), strict_parsing=True).items()}
        assert form['grant_type'] == 'authorization_code' and form['client_id'] == CLIENT
        query = self.codes.pop(form['code'])
        import base64
        challenge = base64.urlsafe_b64encode(hashlib.sha256(form['code_verifier'].encode()).digest()).rstrip(b'=').decode()
        assert challenge == query['code_challenge'] and form['redirect_uri'] == query['redirect_uri']
        now = int(time.time())
        identity = jwt.encode({'iss': ISSUER, 'aud': CLIENT, 'sub': 'synthetic-account',
            'iat': now, 'exp': now + 3600, 'nonce': query['nonce']}, self.key,
            algorithm='RS256', headers={'kid': 'synthetic-key'})
        self.record('oauth-exchange')
        return {'access_token': ACCESS, 'refresh_token': 'synthetic-refresh-never-real',
                'id_token': identity, 'token_type': 'Bearer', 'expires_in': 3600, 'scope': SCOPES}

    def inference(self, body):
        from eidolon_cli.organization_team_generation import SYSTEM, IMPROVE_SYSTEM
        value = json.loads(body)
        assert set(value) == {'model', 'instructions', 'input', 'stream', 'store'}
        assert value['model'] == MODEL and value['stream'] is True and value['store'] is False
        assert len(value['input']) == 1 and value['input'][0]['role'] == 'user'
        inputs = json.loads(value['input'][0]['content'])
        field = inputs.get('field')
        assert value['instructions'] == (IMPROVE_SYSTEM if field else SYSTEM)
        evidence = snapshot(self.home)
        rows = [row for row in evidence['generations'] if row['status'] == 'running']
        assert len(rows) == 1 and rows[0]['dispatched'] == 1
        attempts = {row['idempotency_key']: row for row in evidence['attempts']}
        markers = {row['idempotency_key']: row for row in evidence['dispatches']}
        assert len(attempts) == len(evidence['attempts'])
        assert len(markers) == len(evidence['dispatches'])
        assert set(markers) == {row['idempotency_key'] for row in evidence['generations']
                                if row['dispatched'] and row['idempotency_key'] in attempts}
        assert rows[0]['idempotency_key'] in markers
        for key, marker in markers.items():
            attempt = attempts[key]
            binding = json.loads(attempt['binding_json'])
            assert marker['binding_digest'] == attempt['binding_digest']
            assert marker['dispatch_nonce_digest'] == attempt['dispatch_nonce_digest']
            assert marker['request_sha256'] == binding['request_sha256']
        assert markers[rows[0]['idempotency_key']]['request_sha256'] == hashlib.sha256(body).hexdigest()
        assert not evidence['teams'] and not any(evidence['counts'].values())
        self.record('responses', operation='improve_' + field if field else 'generate',
                    requestSha256=hashlib.sha256(body).hexdigest(),
                    idempotencyKey=rows[0]['idempotency_key'],
                    sourceRevision=rows[0]['source_revision'],
                    durableDispatch=True, model=value['model'])
        if self.mode == 'uncertain':
            event = {'type': 'response.created', 'sequence_number': 1,
                     'response': {'id': 'resp_synthetic', 'status': 'in_progress', 'output': []}}
        else:
            text = json.dumps({'suggestion': SUGGESTIONS[field], 'assumptions': []} if field else TEAM)
            event = {'type': 'response.completed', 'sequence_number': 7, 'response': {
                'id': 'resp_synthetic', 'status': 'completed', 'output': [
                    {'id': 'msg_synthetic', 'type': 'message', 'role': 'assistant',
                     'content': [{'type': 'output_text', 'text': text}]}],
                'usage': {'input_tokens': 10, 'output_tokens': 20, 'total_tokens': 30}}}
        return ('data: ' + json.dumps(event) + '\n\n').encode()


class SyntheticSocket:
    """Decrypted TLS bytes only: production HTTP/SSE parsers read the result."""
    def __init__(self, edges, host):
        self.edges, self.host = edges, host
        self.sent = bytearray()
        self.data = None
        self.closed = False

    def sendall(self, data):
        assert not self.closed
        self.sent.extend(data)

    def _response(self):
        if self.data is not None:
            return
        head, separator, body = bytes(self.sent).partition(b'\r\n\r\n')
        assert separator
        lines = head.decode('ascii').split('\r\n')
        method, path, protocol = lines[0].split(' ')
        headers = dict(line.lower().split(': ', 1) for line in lines[1:])
        assert protocol == 'HTTP/1.1' and headers['host'] == self.host
        assert len(body) == int(headers.get('content-length', '0'))
        content_type = 'application/json'
        if self.host == 'auth.openai.com':
            assert method == ('GET' if path == '/.well-known/jwks.json' else 'POST')
            data = json.dumps(self.edges.oauth(path, body)).encode()
        else:
            assert headers['authorization'] == 'bearer ' + ACCESS
            if path == '/v1/models':
                assert method == 'GET' and not body
                self.edges.record('models')
                data = json.dumps({'models': [{'slug': MODEL, 'display_name': 'Synthetic subscription model',
                                               'visibility': 'list'}]}).encode()
            else:
                assert path == '/v1/responses' and method == 'POST'
                content_type = 'text/event-stream'
                data = self.edges.inference(body)
        self.data = io.BytesIO(b'HTTP/1.1 200 OK\r\nContent-Type: ' + content_type.encode()
            + b'\r\nContent-Length: ' + str(len(data)).encode() + b'\r\n\r\n' + data)

    def makefile(self, mode, *args, **kwargs):
        assert mode == 'rb'
        self._response()
        return self.data

    def recv(self, count):
        assert not self.closed and 0 < count <= 8192
        self._response()
        return self.data.read(min(count, 137))

    def shutdown(self, how):
        self.closed = True

    def close(self):
        self.closed = True
