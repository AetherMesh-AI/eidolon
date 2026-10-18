"""Dedicated setup dispatch capabilities; durable audit rows never grant sends.

Only accounting jobs run in bounded workers. They receive no token lease, form
text, sender callback, socket, or physical permit. A timed-out dispatch may leave
a charge, but can never deliver a permit later.
"""
from contextlib import closing, contextmanager, nullcontext
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import threading
import time
import uuid

from eidolon_cli.organization_owner_policy import OwnerPolicyError, _connection, _hash
from eidolon_cli.organization_ledger_writer import (
    ledger_writer, PUBLICATION_WAIT_SECONDS, ACCOUNTING_WAIT_SECONDS,
)
from eidolon_cli.organization_setup_subscription_usage import (
    AttemptBindingV1, ENDPOINT_IDENTITY, TransportObservationV1,
)

_PERSISTENCE_SLOTS = threading.BoundedSemaphore(4)
_PERMIT_KEY = object()


def _noop():
    return None


def _persist(function, args, deadline, *, retain_late=False, detailed=False):
    """Known commit only; slots remain occupied until abandoned workers exit."""
    if (not retain_late and time.monotonic() >= deadline
            or not _PERSISTENCE_SLOTS.acquire(blocking=False)):
        return 'not_committed' if detailed else False
    completed = threading.Event()
    result = ['unknown']

    def run():
        try:
            result[0] = 'committed' if function(*args) is True else 'not_committed'
        except Exception:
            result[0] = 'unknown'
        finally:
            completed.set()
            _PERSISTENCE_SLOTS.release()
    worker = threading.Thread(target=run, name='setup-subscription-accounting', daemon=True)
    try:
        worker.start()
    except Exception:
        _PERSISTENCE_SLOTS.release()
        return 'not_committed' if detailed else False
    if not completed.wait(max(0, deadline - time.monotonic())):
        return 'unknown' if detailed else False
    if result[0] == 'not_committed':
        return 'not_committed' if detailed else False
    if time.monotonic() >= deadline:
        return 'unknown' if detailed else False
    return result[0] if detailed else result[0] == 'committed'


class _Permit:
    def __init__(self, key):
        if key is not _PERMIT_KEY:
            raise TypeError('Private dispatch capability')
        self._lock, self._used = threading.Lock(), False

    def consume(self):
        with self._lock:
            if self._used:
                return False
            self._used = True
            return True

    def __reduce__(self):
        raise TypeError('Dispatch capability cannot be serialized')

    def __repr__(self):
        return '<setup dispatch capability>'


def _exact_attempt(conn, binding, nonce_digest, *, dispatched=None):
    from eidolon_cli.organization_setup_subscription_policy import validate_subscription_ledger
    validate_subscription_ledger(conn)
    row = conn.execute('SELECT * FROM organization_setup_subscription_attempts WHERE idempotency_key=?',
                       (binding.idempotency_key,)).fetchone()
    if (row is None or row['binding_json'] != binding.canonical_bytes().decode('ascii')
            or row['binding_digest'] != binding.binding_digest
            or row['dispatch_nonce_digest'] != nonce_digest):
        raise OwnerPolicyError('subscription_attempt_conflict')
    generation = conn.execute('SELECT * FROM organization_team_generations WHERE idempotency_key=?',
                              (binding.idempotency_key,)).fetchone()
    if (generation is None or generation['organization_id'] != binding.organization_id
            or generation['input_hash'] != binding.input_hash
            or generation['source_revision'] != binding.source_revision
            or dispatched is not None and generation['dispatched'] != int(dispatched)):
        raise OwnerPolicyError('subscription_attempt_conflict')
    return generation


def _snapshot(conn, home, transport, evidence, binding, *, allow_charged=False, admitted_catalog=False):
    from eidolon_cli.organization_setup_subscription_policy import build_authoritative_snapshot
    snapshot = build_authoritative_snapshot(conn, home, transport, evidence.owner_generation,
                                             evidence, completing_key=binding.idempotency_key, admitted_catalog=admitted_catalog)
    if (snapshot['sourceRevision'] != binding.source_revision
            or snapshot['policyGeneration'] != binding.policy_generation
            or snapshot['identity']['organizationId'] != binding.organization_id
            or snapshot['identity']['owner']['ownerId'] != binding.owner_id
            or snapshot['setupPolicy']['connectionId'] != binding.connection_id
            or snapshot['setupPolicy']['authorizationEpoch'] != binding.authorization_epoch
            or snapshot['setupPolicy']['modelSlug'] != binding.model_slug
            or binding.timeout_seconds > min(120, snapshot['settings'].objective_timeout_seconds,
                                              snapshot['setupPolicy']['requestTimeoutSeconds'])):
        raise OwnerPolicyError('source_conflict')
    if not allow_charged:
        from eidolon_cli.organization_team_budget import budget_view
        budget = budget_view(conn, snapshot['settings'], completing_key=binding.idempotency_key)
        bucket = 'generationCalls' if binding.operation == 'generate' else 'improveCalls'
        if budget['status'] != 'available' or budget['remaining'][bucket] <= 0:
            raise OwnerPolicyError('setup_budget_exhausted_or_blocked')
    return snapshot


class _KnownRollback(RuntimeError):
    pass


def _dispatch_job(home, transport, evidence, binding, nonce_digest, cancelled, deadline, caller_cancel=None):
    from tui_gateway.organization_credential_transport import owner_publication
    entered = False
    def stopped():
        return cancelled.is_set() or caller_cancel is not None and caller_cancel.is_set()
    try:
        with owner_publication(transport, home, evidence.owner_generation), _ledger_connection(
                home, write=True, classify_rollback=True,
                deadline=min(deadline, time.monotonic() + PUBLICATION_WAIT_SECONDS), cancelled=stopped) as conn:
            entered = True
            if stopped() or time.monotonic() >= deadline:
                return False
            row = _exact_attempt(conn, binding, nonce_digest, dispatched=False)
            if row['status'] != 'running':
                return False
            before = _snapshot(conn, home, transport, evidence, binding)
            conn.execute('INSERT INTO organization_setup_subscription_dispatches VALUES (?,?,?,?)',
                         (binding.idempotency_key, binding.binding_digest, nonce_digest, binding.request_sha256))
            conn.execute('UPDATE organization_team_generations SET dispatched=1 WHERE idempotency_key=?',
                         (binding.idempotency_key,))
            after = _snapshot(conn, home, transport, evidence, binding, allow_charged=True)
            if (before['authorityEvidenceDigest'] != after['authorityEvidenceDigest']
                    or stopped() or time.monotonic() >= deadline):
                raise OwnerPolicyError('source_conflict')
        return True
    except _KnownRollback:
        return False
    except Exception:
        if not entered:
            return False
        raise


def _outcome_job(home, binding, nonce_digest, observation):
    """Original accounting identity survives retirement; it cannot publish text."""
    with _ledger_connection(home, write=True) as conn:
        _exact_attempt(conn, binding, nonce_digest, dispatched=True)
        marker = conn.execute('SELECT * FROM organization_setup_subscription_dispatches WHERE idempotency_key=?',
                              (binding.idempotency_key,)).fetchone()
        if (marker is None or marker['binding_digest'] != binding.binding_digest
                or marker['dispatch_nonce_digest'] != nonce_digest
                or marker['request_sha256'] != binding.request_sha256):
            raise OwnerPolicyError('subscription_attempt_conflict')
        old = conn.execute('SELECT outcome_hash FROM organization_setup_subscription_outcomes WHERE idempotency_key=?',
                           (binding.idempotency_key,)).fetchone()
        terminal = observation.terminal_kind in {'completed', 'failed', 'incomplete'}
        if old:
            if terminal and old['outcome_hash'] != observation.outcome_hash:
                raise OwnerPolicyError('subscription_outcome_conflict')
            return True
        if terminal:
            conn.execute('INSERT INTO organization_setup_subscription_outcomes VALUES (?,?,?,?,?)',
                         (binding.idempotency_key, binding.binding_digest, nonce_digest,
                          observation.outcome_hash, observation.canonical_bytes().decode('ascii')))
        # Known terminal failure is safe for a new explicit bounded action. A
        # valid parsed result can separately advance failed -> completed later.
        conn.execute('UPDATE organization_team_generations SET status=? WHERE idempotency_key=? AND status!=?',
                     ('failed' if terminal else 'uncertain', binding.idempotency_key, 'completed'))
    return True


@contextmanager
def _ledger_connection(home, *, write=False, classify_rollback=False, deadline=None, cancelled=None):
    path = Path(home) / 'organization' / 'state.db'
    if path.resolve() != path.absolute():
        raise OwnerPolicyError('identity_conflict')
    if deadline is None:
        deadline = time.monotonic() + ACCOUNTING_WAIT_SECONDS
    with (ledger_writer(path, deadline=deadline, cancelled=cancelled) if write else nullcontext()), closing(sqlite3.connect(path.absolute().as_uri() + ('?mode=rw' if write else '?mode=ro'), uri=True,
                                 timeout=0, isolation_level=None)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON' if write else 'PRAGMA query_only=ON')
        conn.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
        try:
            yield conn
        except BaseException as error:
            if write:
                conn.rollback()
            if classify_rollback and isinstance(error, Exception):
                raise _KnownRollback() from None
            raise
        else:
            # A commit exception is ambiguous even if a later rollback succeeds.
            if write:
                conn.commit()


class _Admission:
    def __init__(self, home, transport, evidence, binding, nonce, cancel, shared_paths, authority_digest):
        self.home, self.transport, self.evidence = home, transport, evidence
        self.binding, self._nonce = binding, nonce
        self.dispatch_nonce_digest = hashlib.sha256(nonce).hexdigest()
        self.owner_generation = evidence.owner_generation
        self._cancel = cancel
        self._abandoned = threading.Event()
        self._shared_paths = shared_paths
        self._authority_digest = authority_digest
        self._dispatch_outcome = 'not_committed'
        self._lock, self._attempted, self._permit = threading.Lock(), False, None

    def __reduce__(self):
        raise TypeError('Subscription admission cannot be serialized')

    def __repr__(self):
        return '<subscription admission capability>'

    def network_cancelled(self):
        return self._cancel.is_set() or self._abandoned.is_set()

    def begin_dispatch(self, request_sha256, *, deadline):
        with self._lock:
            if self._attempted or request_sha256 != self.binding.request_sha256 or self.network_cancelled():
                return None
            self._attempted = True
        known = _persist(_dispatch_job, (self.home, self.transport, self.evidence, self.binding,
            self.dispatch_nonce_digest, self._abandoned, deadline, self._cancel), deadline, detailed=True)
        self._dispatch_outcome = known
        if known != 'committed' or self.network_cancelled():
            self._abandoned.set()
            return None
        with self._lock:
            if time.monotonic() >= deadline or self.network_cancelled():
                self._abandoned.set()
                return None
            self._permit = _Permit(_PERMIT_KEY)
            return self._permit

    def dispatch_definitely_not_sent(self):
        return self._dispatch_outcome == 'not_committed'

    def try_send_allowed(self):
        from eidolon_cli.config_primitives import _CONFIG_LOCK
        if self.network_cancelled() or self.evidence.cheap_guard() is not True:
            return False
        if any(Path(path).exists() for path in self._shared_paths):
            return False
        acquired = False
        try:
            with _ledger_connection(self.home) as conn:
                acquired = _CONFIG_LOCK.acquire(blocking=False)
                if not acquired:
                    return False
                row = _exact_attempt(conn, self.binding, self.dispatch_nonce_digest, dispatched=True)
                if row['status'] not in {'running', 'failed'}:
                    return False
                from eidolon_cli.organization_team_setup import _current, _fresh
                from eidolon_cli.organization_shared_budget import _read_scope_pin
                from eidolon_cli.organization_setup_subscription_policy import _closed_sources, require_policy_unpaused
                require_policy_unpaused(conn)
                if _closed_sources(conn, self.home)[2] != self._authority_digest:
                    return False
                _, policy, settings, revision = _current(conn, self.home, self.binding.organization_id)
                return (policy['generation'] == self.binding.policy_generation
                        and revision == self.binding.source_revision and _fresh(conn, settings)
                        and _read_scope_pin(conn) is None and self.evidence.cheap_guard() is True)
        except (OSError, RuntimeError, ValueError, sqlite3.Error):
            return False
        finally:
            if acquired:
                _CONFIG_LOCK.release()

    def record_transport_outcome(self, permit, observation, *, deadline):
        if (permit is None or permit is not self._permit or type(observation) is not TransportObservationV1
                or observation.attempt_id != self.binding.attempt_id
                or observation.binding_digest != self.binding.binding_digest
                or observation.dispatch_nonce_digest != self.dispatch_nonce_digest
                or observation.request_sha256 != self.binding.request_sha256
                or observation.dispatch_state != 'possible_send'):
            return False
        return _persist(_outcome_job, (self.home, self.binding, self.dispatch_nonce_digest, observation),
                        deadline, retain_late=True)

    def abandon(self):
        self._abandoned.set()


def _failed_job(home, binding, nonce_digest, attempted):
    with _ledger_connection(home, write=True) as conn:
        row = _exact_attempt(conn, binding, nonce_digest)
        outcome = conn.execute('SELECT 1 FROM organization_setup_subscription_outcomes WHERE idempotency_key=?',
                               (binding.idempotency_key,)).fetchone()
        # A committed failed/dispatched=0 row is a durable no-permit fence. A
        # late worker with an unknown acknowledgement must never reopen it.
        if row['status'] == 'failed' and row['dispatched'] == 0 and outcome is None:
            return True
        if row['status'] != 'completed':
            status = 'failed' if outcome or not (row['dispatched'] or attempted) else 'uncertain'
            conn.execute('UPDATE organization_team_generations SET status=? WHERE idempotency_key=?',
                         (status, binding.idempotency_key))
    return True


def retry_unsent_disposition(home, params, operation, *, transport):
    """Read-only proof for a separate explicit action, never a send capability.

    A failed row cannot become dispatchable again: _dispatch_job requires running
    in the same SQLite write serialization. Preserve every old row and charge.
    Any unavailable proof omits the offer without changing the original error.
    """
    evidence = None
    try:
        from eidolon_cli.organization_team_budget import read_policy
        from eidolon_cli.organization_team_setup import _current, _fresh
        from eidolon_cli.organization_setup_subscription_policy import (
            validate_subscription_ledger, require_subscription_receipt, _runtime_owner,
        )
        from eidolon_cli.organization_team_generation import subscription_request
        from tui_gateway.methods_organization_setup_subscription import subscription_preflight
        from tui_gateway.organization_credential_transport import owner_publication
        fields = {'expectedOrganizationId', 'sourceRevision', 'jobs', 'goals',
                  'namingStyle', 'idempotencyKey'}
        if operation == 'improveTeamInput':
            fields.add('field')
        elif operation != 'generateTeam':
            return None
        if type(params) is not dict or set(params) != fields:
            return None
        generation = _runtime_owner(home, transport)
        with _connection(home) as conn:
            candidate = read_policy(conn)
        if (candidate or {}).get('version') != 3:
            return None
        evidence = subscription_preflight(transport, home, candidate, inference=False)
        if evidence.owner_generation is not generation:
            return None
        key = params['idempotencyKey']
        with owner_publication(transport, home, generation), _connection(home) as conn:
            identity, _, settings, revision = _current(conn, home, params['expectedOrganizationId'])
            if revision != params['sourceRevision'] or not _fresh(conn, settings):
                return None
            validate_subscription_ledger(conn)
            row = conn.execute('SELECT * FROM organization_team_generations WHERE idempotency_key=?', (key,)).fetchone()
            attempt = conn.execute('SELECT * FROM organization_setup_subscription_attempts WHERE idempotency_key=?', (key,)).fetchone()
            if (row is None or attempt is None or row['status'] != 'failed' or row['dispatched'] != 0
                    or conn.execute("SELECT 1 FROM organization_team_generations WHERE status IN ('running','uncertain')").fetchone()
                    or conn.execute('SELECT 1 FROM organization_setup_subscription_dispatches WHERE idempotency_key=?', (key,)).fetchone()
                    or conn.execute('SELECT 1 FROM organization_setup_subscription_outcomes WHERE idempotency_key=?', (key,)).fetchone()):
                return None
            binding = AttemptBindingV1(**json.loads(attempt['binding_json']))
            request = subscription_request(operation, params['jobs'], params['goals'], params['namingStyle'],
                model_slug=evidence.model_slug, field=params.get('field'))
            receipt = require_subscription_receipt(conn, candidate)
            if (binding.idempotency_key != key or binding.input_hash != _hash({'operation': operation, 'input': params})
                    or binding.operation != request.operation or binding.request_sha256 != request.request_sha256
                    or binding.owner_id != identity['owner']['ownerId']
                    or binding.review_digest != receipt['previewDigest']
                    or binding.account_binding_digest != evidence.account_digest
                    or binding.authorization_epoch != evidence.epoch
                    or binding.store_revision != evidence.revision):
                return None
            _snapshot(conn, home, transport, evidence, binding)
            return {'version': 1, 'disposition': 'retry_unsent', 'idempotencyKey': key,
                    'operation': binding.operation, 'sourceRevision': binding.source_revision}
    except Exception:
        return None
    finally:
        if evidence is not None:
            try:
                evidence.release()
            except Exception:
                pass  # Proof cleanup must never replace the original safe error.


def infer_subscription(home, params, key, operation, finish, *, transport, cancel=None):
    from eidolon_cli.organization_team_budget import read_policy, record_operation
    from eidolon_cli.organization_team_setup import _current, _fresh, _generation_allowance
    from eidolon_cli.organization_setup_subscription_policy import (
        build_authoritative_snapshot, require_subscription_receipt, validate_subscription_ledger, _runtime_owner,
    )
    from eidolon_cli.organization_team_generation import subscription_request, complete_subscription
    from eidolon_cli.organization_setup_subscription_transport import prepare_subscription_send
    from tui_gateway.methods_organization_setup_subscription import subscription_preflight
    from tui_gateway.organization_credential_transport import owner_publication
    if transport is None:
        raise OwnerPolicyError('subscription_local_owner_required')
    generation = _runtime_owner(home, transport)
    input_hash = _hash({'operation': operation, 'input': params})
    with _connection(home) as conn:
        _, _, settings, revision = _current(conn, home, params['expectedOrganizationId'])
        candidate = read_policy(conn)
        if (candidate or {}).get('version') != 3:
            raise OwnerPolicyError('source_conflict')
        validate_subscription_ledger(conn)
        prior = conn.execute('SELECT * FROM organization_team_generations WHERE idempotency_key=?', (key,)).fetchone()
        if prior:
            if prior['organization_id'] != params['expectedOrganizationId'] or prior['input_hash'] != input_hash:
                raise OwnerPolicyError('idempotency_conflict')
            if prior['status'] != 'completed':
                raise OwnerPolicyError('generation_' + prior['status'])
            if revision != params['sourceRevision'] or not _fresh(conn, settings):
                raise OwnerPolicyError('source_conflict')
            replay = json.loads(prior['result_json'])
        else:
            replay = None
    evidence = subscription_preflight(transport, home, candidate, inference=replay is None)
    admission = None
    try:
        # The worker DTO cannot retain the lease or a release closure that owns it.
        nonsecret = replace(evidence, lease=None, release=_noop,
                            catalog_evidence=json.loads(json.dumps(evidence.catalog_evidence)))
        if evidence.owner_generation is not generation:
            raise OwnerPolicyError('subscription_local_owner_required')
        if replay is not None:
            with owner_publication(transport, home, generation), _connection(home) as conn:
                snapshot = build_authoritative_snapshot(conn, home, transport, generation, nonsecret,
                    completing_key=key, admitted_catalog=True)
                if snapshot['sourceRevision'] != params['sourceRevision']:
                    raise OwnerPolicyError('source_conflict')
                return {**replay, 'generationAllowance': _generation_allowance(conn, snapshot['settings'])}
        request = subscription_request(operation, params['jobs'], params['goals'], params['namingStyle'],
                                       model_slug=evidence.model_slug, field=params.get('field'))
        nonce = secrets.token_bytes(32)
        nonce_digest = hashlib.sha256(nonce).hexdigest()
        with owner_publication(transport, home, evidence.owner_generation), _connection(home, write=True) as conn:
            snapshot = build_authoritative_snapshot(conn, home, transport, evidence.owner_generation, nonsecret)
            if snapshot['sourceRevision'] != params['sourceRevision'] or snapshot['setupPolicy'] != candidate:
                raise OwnerPolicyError('source_conflict')
            receipt = require_subscription_receipt(conn, candidate)
            binding = AttemptBindingV1(str(uuid.uuid4()), key, params['expectedOrganizationId'],
                snapshot['identity']['owner']['ownerId'], request.operation, input_hash,
                snapshot['sourceRevision'], snapshot['policyGeneration'], receipt['previewDigest'],
                candidate['connectionId'], evidence.epoch, evidence.revision, evidence.account_digest,
                evidence.model_slug, ENDPOINT_IDENTITY,
                min(120, receipt['effectiveTimeoutSeconds'], snapshot['settings'].objective_timeout_seconds,
                    candidate['requestTimeoutSeconds']), request.request_sha256)
            _snapshot(conn, home, transport, nonsecret, binding)
            conn.execute('INSERT INTO organization_team_generations '
                '(idempotency_key,organization_id,input_hash,source_revision,status,created) VALUES (?,?,?,?,?,?)',
                (key, binding.organization_id, input_hash, binding.source_revision, 'running', time.time()))
            record_operation(conn, key, binding.organization_id, operation, binding.policy_generation)
            conn.execute('INSERT INTO organization_setup_subscription_attempts VALUES (?,?,?,?)',
                         (key, binding.canonical_bytes().decode('ascii'), binding.binding_digest, nonce_digest))
            validate_subscription_ledger(conn)
            after = _snapshot(conn, home, transport, nonsecret, binding)
            if after['authorityEvidenceDigest'] != snapshot['authorityEvidenceDigest']:
                raise OwnerPolicyError('source_conflict')
        admission = _Admission(home, transport, nonsecret, binding, nonce,
                               cancel or threading.Event(), snapshot['sharedAuthorityPaths'], snapshot['authorityEvidenceDigest'])
        prepared = prepare_subscription_send(admission, evidence.lease, request)
        response = complete_subscription(prepared)
        with owner_publication(transport, home, evidence.owner_generation), _connection(home, write=True) as conn:
            _exact_attempt(conn, binding, nonce_digest, dispatched=True)
            _snapshot(conn, home, transport, nonsecret, binding, allow_charged=True, admitted_catalog=True)
            outcome = conn.execute('SELECT outcome_json FROM organization_setup_subscription_outcomes WHERE idempotency_key=?',
                                   (key,)).fetchone()
            if outcome is None or json.loads(outcome[0])['terminal_kind'] != 'completed' or admission.network_cancelled():
                raise OwnerPolicyError('subscription_completion_unavailable')
            result = finish(conn, response)
            _, _, settings, _ = _current(conn, home, binding.organization_id)
            result['generationAllowance'] = _generation_allowance(conn, settings, completing_key=key)
            conn.execute("UPDATE organization_team_generations SET status='completed',result_json=? WHERE idempotency_key=?",
                         (json.dumps(result), key))
            _snapshot(conn, home, transport, nonsecret, binding, allow_charged=True, admitted_catalog=True)
        return result
    except BaseException:
        if admission is not None:
            admission.abandon()
            _persist(_failed_job, (home, admission.binding, admission.dispatch_nonce_digest, admission._dispatch_outcome != 'not_committed'),
                     time.monotonic() + 1, retain_late=True)
        raise
    finally:
        if admission is not None:
            admission.abandon()
        evidence.release()
