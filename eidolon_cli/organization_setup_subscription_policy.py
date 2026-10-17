"""Inert contracts for a future, explicit setup-inheritance retirement.

There is deliberately no activation, persistence, RPC registration, or inference
path here. Snapshots MUST be constructed from authoritative server reads inside
the eventual owner transaction, never deserialized from request parameters.
Validating a preview does not authorize a write. A future writer must re-read its
closed schema, identity, source, managed policy, scope and ledger under its lock,
check the real transport capability, rebuild the preview and atomically CAS the
source/generation. The digest is an integrity comparison, not a signature.
"""
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from uuid import UUID

ACKNOWLEDGEMENT = {
    'version': 1,
    'code': 'retire_setup_inheritance_non_hard_usage_v1',
    'text': ('I retire only the listed setup token, context, output and cost '
             'guarantees. Setup uses subscription allowance with bounded calls '
             'and a local timeout, without a hard token or USD guarantee. '
             'Usage may continue after local cancellation. Historical origins '
             'are unknown. Execution constraints and prior charges remain.'),
}
_CALL_FIELDS = ('improveCallLimit', 'generationCallLimit', 'totalCallLimit')
_CANDIDATE_FIELDS = {'version', 'mode', *_CALL_FIELDS, 'requestTimeoutSeconds',
                     'connectionId', 'connectionRevision', 'modelSlug'}
_EXECUTION_FIELDS = {'max_model_calls', 'max_total_tokens', 'max_context_tokens',
                     'max_output_tokens', 'max_cost_usd', 'model_costs',
                     'objective_timeout_seconds'}
_SNAPSHOT_FIELDS = {'version', 'organizationId', 'ownerId', 'policyGeneration',
                    'sourceRevision', 'persistedSourceRevision', 'authority',
                    'scope', 'workHistoryCount', 'managedFields',
                    'sharedBinding', 'sharedPin', 'allOrganizationConstraints',
                    'executionConstraints', 'capturedExecutionBudgets',
                    'attempts', 'currentCallLimits'}


class SubscriptionPolicyError(ValueError):
    """Stable, nonsecret rejection reason."""


def _fail(code):
    raise SubscriptionPolicyError(code)


def _exact(value, fields, code):
    if type(value) is not dict or set(value) != fields:
        _fail(code)


def _integer(value, minimum, maximum, code):
    if type(value) is not int or not minimum <= value <= maximum:
        _fail(code)


def _text(value, maximum, code):
    if (type(value) is not str or not value or len(value) > maximum
            or value != value.strip() or any(ord(c) < 32 for c in value)):
        _fail(code)


def _uuid(value):
    try:
        if type(value) is not str or str(UUID(value)) != value:
            _fail('identity_invalid')
    except (ValueError, AttributeError):
        _fail('identity_invalid')


def _money(value, *, nullable=False):
    if nullable and value is None:
        return
    if type(value) is not str or not re.fullmatch(r'(0|[1-9][0-9]*)(\.[0-9]+)?', value):
        _fail('cost_invalid')
    try:
        if not Decimal(value).is_finite() or Decimal(value) < 0:
            _fail('cost_invalid')
    except InvalidOperation:
        _fail('cost_invalid')


def _canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'),
                          ensure_ascii=True, allow_nan=False)
    except (ValueError, TypeError):
        _fail('noncanonical_data')


def _copy(value):
    return json.loads(_canonical(value))


def preview_digest(preview):
    """Digest inert preview content; never use this as proof of owner authority."""
    return hashlib.sha256(_canonical(preview).encode('utf-8')).hexdigest()


def require_local_owner(transport, home, generation):
    """Check the server-issued capability and its exact connection generation.

    `generation` is the opaque object captured server-side, never a JSON value.
    Registration is exclusively the existing authenticated gateway's job.
    """
    from tui_gateway.organization_credential_transport import owner_home, owner_generation
    actual_home = owner_home(transport)
    if (actual_home is None or generation is None
            or owner_generation(transport) is not generation
            or actual_home != Path(home).resolve()):
        _fail('local_owner_capability_required')


def validate_candidate(raw):
    _exact(raw, _CANDIDATE_FIELDS, 'candidate_schema_unsupported')
    if type(raw['version']) is not int or raw['version'] != 3 or raw['mode'] != 'subscription_calls':
        _fail('candidate_schema_unsupported')
    for field in _CALL_FIELDS:
        _integer(raw[field], 0, 1000, 'call_limit_invalid')
    if max(raw['improveCallLimit'], raw['generationCallLimit']) > raw['totalCallLimit']:
        _fail('call_limit_invalid')
    _integer(raw['requestTimeoutSeconds'], 1, 120, 'timeout_invalid')
    _integer(raw['connectionRevision'], 1, 2**63 - 1, 'connection_revision_invalid')
    for field in ('connectionId', 'modelSlug'):
        _text(raw[field], 256, 'connection_or_model_invalid')
    return _copy(raw)


def _snapshot(raw):
    _exact(raw, _SNAPSHOT_FIELDS, 'snapshot_schema_unsupported')
    if type(raw['version']) is not int or raw['version'] != 1:
        _fail('snapshot_schema_unsupported')
    for key in ('organizationId', 'ownerId'):
        _uuid(raw[key])
    _integer(raw['policyGeneration'], 1, 2**63 - 1, 'generation_invalid')
    for key in ('sourceRevision', 'persistedSourceRevision'):
        _text(raw[key], 256, 'source_invalid')
    if raw['sourceRevision'] != raw['persistedSourceRevision']:
        _fail('source_conflict')
    if raw['authority'] != 'local_owner' or raw['scope'] != 'setup_inheritance':
        _fail('authority_or_scope_unknown')
    _integer(raw['workHistoryCount'], 0, 2**63 - 1, 'work_history_invalid')
    if raw['workHistoryCount']:
        _fail('organization_not_empty')
    managed = raw['managedFields']
    if type(managed) is not list or any(type(v) is not str for v in managed):
        _fail('managed_scope_unknown')
    # Unknown managed fields cannot silently acquire a nonblocking interpretation.
    if managed:
        _fail('managed_configuration')
    if raw['sharedBinding'] is not None or raw['sharedPin'] is not None:
        _fail('shared_scope_unsupported')
    if type(raw['allOrganizationConstraints']) is not list or raw['allOrganizationConstraints']:
        _fail('enclosing_constraint_or_unknown_scope')
    execution = raw['executionConstraints']
    _exact(execution, _EXECUTION_FIELDS, 'execution_schema_unsupported')
    for field in _EXECUTION_FIELDS - {'max_cost_usd', 'model_costs'}:
        _integer(execution[field], 1, 2**63 - 1, 'execution_constraint_invalid')
    _money(execution['max_cost_usd'], nullable=True)
    if type(execution['model_costs']) is not list:
        _fail('pricing_schema_unsupported')
    priced_routes = set()
    for pricing in execution['model_costs']:
        _exact(pricing, {'provider', 'model', 'input_usd_per_million',
                         'output_usd_per_million'}, 'pricing_schema_unsupported')
        for field in ('provider', 'model'):
            _text(pricing[field], 256, 'pricing_schema_unsupported')
        route = (pricing['provider'], pricing['model'])
        if route in priced_routes:
            _fail('pricing_schema_unsupported')
        priced_routes.add(route)
        for field in ('input_usd_per_million', 'output_usd_per_million'):
            _money(pricing[field])
    if type(raw['capturedExecutionBudgets']) is not list:
        _fail('captured_execution_invalid')
    # Fresh profiles cannot have captured work budgets. Never silently drop them.
    if raw['capturedExecutionBudgets']:
        _fail('organization_not_empty')
    limits = raw['currentCallLimits']
    _exact(limits, set(_CALL_FIELDS), 'existing_call_limits_invalid')
    for value in limits.values():
        _integer(value, 0, 1000, 'existing_call_limits_invalid')
    if max(limits['improveCallLimit'], limits['generationCallLimit']) > limits['totalCallLimit']:
        _fail('existing_call_limits_invalid')
    attempts = raw['attempts']
    if type(attempts) is not list:
        _fail('attempt_ledger_invalid')
    seen = set()
    for row in attempts:
        _exact(row, {'id', 'operation', 'status', 'dispatched', 'reservedTokens', 'reservedCost'},
               'attempt_ledger_invalid')
        _text(row['id'], 256, 'attempt_ledger_invalid')
        if row['id'] in seen:
            _fail('attempt_ledger_invalid')
        seen.add(row['id'])
        if type(row['operation']) is not str or row['operation'] not in {'legacy', 'improveTeamInput', 'generateTeam'}:
            _fail('attempt_ledger_invalid')
        if type(row['status']) is not str:
            _fail('attempt_ledger_invalid')
        if row['status'] in {'running', 'uncertain'}:
            _fail('attempt_running_or_uncertain')
        if row['status'] not in {'completed', 'failed'} or type(row['dispatched']) is not bool:
            _fail('attempt_ledger_invalid')
        _integer(row['reservedTokens'], 0, 2**63 - 1, 'attempt_ledger_invalid')
        _money(row['reservedCost'], nullable=True)
        # This snapshot describes the supported bounded-API legacy ledger.
        # Unknown subscription usage needs a later schema, never a zero charge.
        if row['status'] == 'completed' and not row['dispatched']:
            _fail('attempt_ledger_invalid')
        if row['dispatched'] and row['reservedTokens'] <= 0:
            _fail('attempt_ledger_invalid')
        if not row['dispatched'] and (row['reservedTokens'] != 0 or row['reservedCost'] is not None):
            _fail('attempt_ledger_invalid')
    return _copy(raw)


def build_preview(snapshot, candidate):
    """Build a detached disclosure from trusted reads, not an authorization."""
    source, replacement = _snapshot(snapshot), validate_candidate(candidate)
    execution = source['executionConstraints']
    retired = [{'guarantee': 'setup_product_total_tokens', 'value': 120000,
                'source': 'setup_product_ceiling', 'scope': 'setup_only'}]
    for field in ('max_total_tokens', 'max_context_tokens', 'max_output_tokens',
                  'max_cost_usd', 'model_costs'):
        value = execution[field]
        if value is None or (field == 'model_costs' and not value):
            continue
        retired.append({'guarantee': field, 'value': value,
                        'source': 'execution_configuration_inheritance',
                        'scope': 'setup_only', 'historicalOrigin': 'unknown'})
    # A candidate can tighten but cannot use retirement to relax inherited calls.
    effective = {field: min(replacement[field], source['currentCallLimits'][field],
                            execution['max_model_calls']) for field in _CALL_FIELDS}
    effective['improveCallLimit'] = min(effective['improveCallLimit'], effective['totalCallLimit'])
    effective['generationCallLimit'] = min(effective['generationCallLimit'], effective['totalCallLimit'])
    counts = dict.fromkeys(('legacy', 'improveTeamInput', 'generateTeam', 'total'), 0)
    for row in source['attempts']:
        if row['dispatched']:
            counts[row['operation']] += 1
            counts['total'] += 1
    remaining_total = max(0, effective['totalCallLimit'] - counts['total'])
    remaining = {'total': remaining_total,
                 'improveTeamInput': min(remaining_total, max(0, effective['improveCallLimit'] - counts['improveTeamInput'])),
                 'generateTeam': min(remaining_total, max(0, effective['generationCallLimit'] - counts['generateTeam']))}
    return {'previewSchemaVersion': 1, 'action': 'retire_setup_inheritance',
            'organizationId': source['organizationId'], 'ownerId': source['ownerId'],
            'policyGeneration': source['policyGeneration'],
            'sourceRevision': source['sourceRevision'],
            'retiredGuarantees': retired, 'replacement': replacement,
            'effectiveCallLimits': effective,
            'effectiveLocalTimeoutSeconds': min(120, replacement['requestTimeoutSeconds'],
                                                execution['objective_timeout_seconds']),
            'unchangedExecutionConstraints': execution,
            'unchangedCapturedExecutionBudgets': source['capturedExecutionBudgets'],
            'retainedAttempts': source['attempts'], 'retainedCallCounts': counts,
            'remainingCalls': remaining,
            'historicalOrigin': 'unknown', 'acknowledgement': _copy(ACKNOWLEDGEMENT),
            'hardTokenGuarantee': False, 'hardUsdGuarantee': False,
            'activationSupported': False}


def validate_review(snapshot, candidate, *, organization_id, owner_id,
                    expected_generation, source_revision, reviewed_digest,
                    acknowledgement, transport, home, transport_generation):
    """Rebuild exact review/CAS data. Does not apply or authorize policy."""
    require_local_owner(transport, home, transport_generation)
    preview = build_preview(snapshot, candidate)
    if organization_id != preview['organizationId'] or owner_id != preview['ownerId']:
        _fail('identity_conflict')
    if type(expected_generation) is not int or expected_generation != preview['policyGeneration']:
        _fail('generation_conflict')
    if source_revision != preview['sourceRevision']:
        _fail('source_conflict')
    if type(acknowledgement) is not dict or _canonical(acknowledgement) != _canonical(ACKNOWLEDGEMENT):
        _fail('acknowledgement_required')
    if type(reviewed_digest) is not str or reviewed_digest != preview_digest(preview):
        _fail('preview_conflict')
    return _copy(preview)
