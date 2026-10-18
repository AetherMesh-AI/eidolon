"""Explicit, exact-intent local fencing; never a dispatch or replay capability."""
from dataclasses import dataclass
import json
import sqlite3
import time

from eidolon_cli.organization_owner_policy import OwnerPolicyError, _connection, _hash
from eidolon_cli.organization_ledger_writer import PUBLICATION_WAIT_SECONDS
from eidolon_cli.organization_setup_subscription_admission import _exact_attempt, _ledger_connection
from eidolon_cli.organization_setup_subscription_usage import AttemptBindingV1


_INPUT_FIELDS = {'expectedOrganizationId', 'sourceRevision', 'jobs', 'goals', 'namingStyle', 'operation'}
_OPERATIONS = {'generate': ('generateTeam', None), 'improve_jobs': ('improveTeamInput', 'jobs'),
               'improve_goals': ('improveTeamInput', 'goals')}


def _parameters(params, *, reconcile):
    from eidolon_cli.organization_metadata import _uuid
    from eidolon_cli.organization_roster import _text
    from eidolon_cli.organization_team_admission import _input
    fields = _INPUT_FIELDS | ({'idempotencyKey', 'confirmed'} if reconcile else set())
    if (type(params) is not dict or set(params) != fields
            or type(params['operation']) is not str or params['operation'] not in _OPERATIONS):
        raise ValueError('Unsupported setup request reconciliation parameters')
    _uuid(params['expectedOrganizationId'], 'expectedOrganizationId')
    revision = params['sourceRevision']
    if type(revision) is not str or len(revision) != 64 or any(c not in '0123456789abcdef' for c in revision):
        raise ValueError('Source revision requires the original request version')
    _, field = _OPERATIONS[params['operation']]
    _input(params['jobs'], 'Jobs', 4000, required=field in {None, 'jobs'})
    _input(params['goals'], 'Goals', 4000, required=field in {None, 'goals'})
    _input(params['namingStyle'], 'Naming style', 500, required=False)
    if reconcile:
        if params['confirmed'] is not True:
            raise ValueError('Explicit local reconciliation confirmation is required')
        _text(params['idempotencyKey'], 'Idempotency key', 128)


def _original(params, key):
    operation, field = _OPERATIONS[params['operation']]
    original = {name: params[name] for name in _INPUT_FIELDS - {'operation'}}
    original['idempotencyKey'] = key
    if field is not None:
        original['field'] = field
    return operation, original


def _proof(conn, home, params, key, transport, generation, *, statuses):
    from eidolon_cli.organization_setup_subscription_policy import validate_subscription_ledger, require_subscription_receipt
    from eidolon_cli.organization_team_budget import read_policy
    from eidolon_cli.organization_team_generation import subscription_request
    validate_subscription_ledger(conn)
    attempt = conn.execute('SELECT * FROM organization_setup_subscription_attempts WHERE idempotency_key=?', (key,)).fetchone()
    if attempt is None:
        raise OwnerPolicyError('subscription_attempt_conflict')
    binding = AttemptBindingV1(**json.loads(attempt['binding_json']))
    row = _exact_attempt(conn, binding, attempt['dispatch_nonce_digest'], dispatched=False)
    if (row['status'] not in statuses
            or conn.execute('SELECT 1 FROM organization_setup_subscription_dispatches WHERE idempotency_key=?', (key,)).fetchone()
            or conn.execute('SELECT 1 FROM organization_setup_subscription_outcomes WHERE idempotency_key=?', (key,)).fetchone()):
        raise OwnerPolicyError('subscription_attempt_conflict')
    operation, original = _original(params, key)
    request = subscription_request(operation, original['jobs'], original['goals'], original['namingStyle'],
        model_slug=binding.model_slug, field=original.get('field'))
    candidate = read_policy(conn)
    if (candidate or {}).get('version') != 3:
        raise OwnerPolicyError('source_conflict')
    receipt = require_subscription_receipt(conn, candidate)
    if (binding.idempotency_key != key or binding.organization_id != params['expectedOrganizationId']
            or binding.input_hash != _hash({'operation': operation, 'input': original})
            or binding.operation != params['operation'] or binding.request_sha256 != request.request_sha256
            or binding.source_revision != params['sourceRevision']
            or binding.review_digest != receipt['previewDigest']):
        raise OwnerPolicyError('subscription_attempt_conflict')
    return binding, row, _metadata_fence(conn, home, transport, generation, binding, candidate)


def _metadata_fence(conn, home, transport, generation, binding, candidate):
    """Current owner/source/setup constraints, with no store or catalog access."""
    from eidolon_cli.organization_setup_subscription_policy import (
        require_local_owner, require_policy_unpaused, _closed_ledger_schema, _closed_sources, _no_enclosing_scope,
    )
    from eidolon_cli.organization_team_setup import _current, _fresh
    from eidolon_cli.organization_policy import persisted_settings
    from eidolon_cli.organization_team_budget import budget_view
    require_local_owner(transport, home, generation)
    identity, policy, settings, revision = _current(conn, home, binding.organization_id)
    require_policy_unpaused(conn)
    _closed_ledger_schema(conn)
    closed_settings, _, authority = _closed_sources(conn, home)
    if (identity['owner']['ownerId'] != binding.owner_id or revision != binding.source_revision
            or policy['generation'] != binding.policy_generation or closed_settings != settings
            or persisted_settings(conn) != settings or not _fresh(conn, settings)
            or conn.execute('SELECT 1 FROM objective_budgets LIMIT 1').fetchone()
            or candidate['connectionId'] != binding.connection_id
            or candidate['authorizationEpoch'] != binding.authorization_epoch
            or candidate['modelSlug'] != binding.model_slug
            or binding.timeout_seconds > min(120, settings.objective_timeout_seconds, candidate['requestTimeoutSeconds'])):
        raise OwnerPolicyError('source_conflict')
    if conn.execute("SELECT 1 FROM organization_team_generations WHERE status IN ('running','uncertain') AND idempotency_key<>? LIMIT 1",
                    (binding.idempotency_key,)).fetchone():
        raise OwnerPolicyError('subscription_attempt_conflict')
    _no_enclosing_scope(conn, home, binding.organization_id)
    budget = budget_view(conn, settings, completing_key=binding.idempotency_key)
    bucket = 'generationCalls' if binding.operation == 'generate' else 'improveCalls'
    if budget['status'] != 'available' or budget['remaining'][bucket] <= 0:
        raise OwnerPolicyError('setup_budget_exhausted_or_blocked')
    return authority


@dataclass(frozen=True, repr=False)
class _LocalGrant:
    store: object
    binding: object
    revision: int
    epoch: str
    account_digest: str
    guard: object


def _local_grant(home, params, transport, generation):
    """Explicit local authentication only; no model catalog, lease or HTTP."""
    from tui_gateway.methods_organization_setup_subscription import _current_context
    context, service = _current_context(transport, home, params['expectedOrganizationId'])
    if context.owner_generation is not generation or context.source_digest != params['sourceRevision']:
        raise OwnerPolicyError('source_conflict')
    state = service.status()
    if state['connectionState'] != 'active' or state['cleanupPending']:
        raise OwnerPolicyError('subscription_attempt_conflict')
    descriptor = service.current_descriptor(state['connectionRevision'])
    cancelled, owner_guard = service._lease_cancel, context.lease_generation_guard
    return _LocalGrant(service._store, descriptor['binding'], descriptor['revision'], descriptor['authorizationEpoch'],
        service._store._binding_digest(descriptor['binding']), lambda: not cancelled.is_set() and owner_guard() is True)


def _grant_fence(grant, binding):
    if (grant.binding.organization_id != binding.organization_id or grant.binding.owner_id != binding.owner_id
            or grant.account_digest != binding.account_binding_digest or grant.epoch != binding.authorization_epoch
            or grant.revision != binding.store_revision or grant.guard() is not True):
        raise OwnerPolicyError('subscription_attempt_conflict')
    actual = grant.store.attest_subscription_binding_nonblocking(grant.binding,
        expected_revision=grant.revision, expected_epoch=grant.epoch,
        expected_account_digest=grant.account_digest, lease_generation_guard=grant.guard)
    if actual != {'account_binding_digest': grant.account_digest, 'authorization_epoch': grant.epoch,
                  'store_revision': grant.revision}:
        raise OwnerPolicyError('subscription_attempt_conflict')


def _offer(binding, disposition):
    return {'version': 1, 'disposition': disposition, 'idempotencyKey': binding.idempotency_key,
            'operation': binding.operation, 'sourceRevision': binding.source_revision}


def inspect_unsent_request(home, params, *, transport):
    """Passive unique exact-input discovery; the returned hint grants no action."""
    from tui_gateway.organization_credential_transport import owner_publication
    _parameters(params, reconcile=False)
    from eidolon_cli.organization_setup_subscription_policy import _runtime_owner
    try:
        generation = _runtime_owner(home, transport)
        with owner_publication(transport, home, generation), _connection(home) as conn:
            # At most two rows are considered. Ambiguity is never a row chooser.
            rows = conn.execute("SELECT idempotency_key FROM organization_team_generations WHERE status='running' LIMIT 2").fetchall()
            if len(rows) != 1:
                return {'pending': None}
            binding, _, _ = _proof(conn, home, params, rows[0]['idempotency_key'], transport, generation,
                statuses={'running'})
            return {'pending': _offer(binding, 'reconcile_unsent')}
    except Exception:
        return {'pending': None}


def reconcile_unsent_request(home, params, *, transport):
    """Commit the terminal failed fence before any delayed dispatch can proceed.

    Dispatch commits its marker and dispatched=1 before a permit can be minted.
    SQLite serialization therefore either exposes that marker (refusal) or lets
    this transaction commit failed first; delayed dispatch then fails running.
    No timeout or live-process observation establishes no-send truth.
    """
    from tui_gateway.organization_credential_transport import owner_publication
    _parameters(params, reconcile=True)
    from eidolon_cli.organization_setup_subscription_policy import _runtime_owner
    generation = _runtime_owner(home, transport)
    try:
        grant = _local_grant(home, params, transport, generation)
        with owner_publication(transport, home, generation), _ledger_connection(
                home, write=True, deadline=time.monotonic() + PUBLICATION_WAIT_SECONDS,
                cancelled=lambda: bool(getattr(transport, 'closed', False))) as conn:
            binding, row, before = _proof(conn, home, params, params['idempotencyKey'], transport, generation,
                statuses={'running', 'failed'})
            _grant_fence(grant, binding)
            if row['status'] == 'running':
                conn.execute("UPDATE organization_team_generations SET status='failed' WHERE idempotency_key=? AND status='running' AND dispatched=0",
                    (binding.idempotency_key,))
            _, row, after = _proof(conn, home, params, binding.idempotency_key, transport, generation,
                statuses={'failed'})
            _grant_fence(grant, binding)
            if before != after:
                raise OwnerPolicyError('source_conflict')
    except sqlite3.Error:
        raise OwnerPolicyError('subscription_reconciliation_unavailable') from None
    return {'recovery': _offer(binding, 'retry_unsent')}
