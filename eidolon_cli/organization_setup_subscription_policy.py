"""Explicit owner-reviewed setup subscription policy and immutable local audit.

Retires setup inheritance only; execution settings and lifetime call debt remain.
Server preflight occurs before publication/writer locks. Audit is never a lease.
"""
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
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
                     'connectionId', 'authorizationEpoch', 'modelSlug'}
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
    _uuid(raw['authorizationEpoch'])
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
    # Explicit owner review may raise setup limits; objective call inheritance remains.
    effective = {field: min(replacement[field], execution['max_model_calls']) for field in _CALL_FIELDS}
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



# These names are the audited 344303165 configuration surface, deliberately not
# derived from future defaults or dataclass members. The dedicated text sender
# never constructs general-runtime hooks, tools or plugin admission.
ORDINARY_NAMESPACES = frozenset('''model providers fallback_providers credential_pool_strategies
 toolsets database runtime max_concurrent_sessions max_live_sessions session agent terminal web
 browser checkpoints context_file_max_chars context_file_read_timeout file_read_max_chars
 mcp_discovery_timeout mcp_single_query_discovery_timeout mcp tool_output tool_loop_guardrails
 compression prompt_caching openrouter bedrock auxiliary display dashboard privacy tts stt voice
 wake_word human_delay context memory delegation prefill_messages_file goals loops moa skills
 curator honcho timezone slack discord whatsapp telegram mattermost matrix approvals command_allowlist
 quick_commands platform_hints plugins hooks hooks_auto_accept personalities security cron kanban
 bot_mode code_execution tools logging model_catalog model_overrides models_dev network monitoring
 gateway streaming sessions onboarding telemetry doctor updates lsp x_search secrets
 paste_collapse_threshold paste_collapse_threshold_fallback paste_collapse_char_threshold computer_use
 proxy desktop nous vertex local_runtime _config_version custom_providers fallback_model mcp_servers
 image_gen video_gen smart_model_routing platform_toolsets known_plugin_toolsets known_builtin_toolsets
 tool_gateway_declined_tools group_sessions_per_user thread_sessions_per_user stt_echo_transcripts
 reset_triggers always_log_local filter_silence_narration multiplex_profiles profile_routes platforms
 require_mention unauthorized_dm_behavior signal timeouts'''.split())
OBJECTIVE_DEFAULT_KEYS = frozenset('''max_model_calls max_total_tokens max_cost_usd
 objective_timeout_seconds max_context_tokens max_output_tokens model_costs
 completion_reserve_model_calls completion_reserve_tokens completion_reserve_cost_usd'''.split())
EXECUTION_KEYS = frozenset('''max_workers max_inflight max_members max_request_depth
 max_requests_per_stage max_tasks max_open_objectives max_attempts max_revisions max_replans
 max_stages max_owner_resolutions lease_seconds timeout_seconds max_tool_calls
 pacing_conserve_below_percent pacing_critical_below_percent pacing_conserve_interval_seconds
 pacing_critical_interval_seconds pacing_conserve_max_inflight pacing_critical_max_inflight
 max_tool_result_chars max_project_runs adaptive_pacing_enabled model_routes model_assignments
 communication_scope team capabilities tool_grants read_roots project_grants projects roster
 gateway_enabled'''.split())


@dataclass(frozen=True, repr=False)
class SubscriptionPreflight:
    binding: object
    store: object
    revision: int
    epoch: str
    account_digest: str
    model_slug: str
    catalog_evidence: dict
    owner_generation: object
    cheap_guard: object
    lease: object
    release: object

    def __reduce__(self):
        raise TypeError('Subscription preflight cannot be serialized')


_RECEIPT_TABLE = 'organization_setup_subscription_policy_receipts'
_ATTEMPT_TABLE = 'organization_setup_subscription_attempts'
_DISPATCH_TABLE = 'organization_setup_subscription_dispatches'
_OUTCOME_TABLE = 'organization_setup_subscription_outcomes'
SUBSCRIPTION_TABLES = {_RECEIPT_TABLE, _ATTEMPT_TABLE, _DISPATCH_TABLE, _OUTCOME_TABLE}
SUBSCRIPTION_DDL = (
    '''CREATE TABLE IF NOT EXISTS organization_setup_subscription_policy_receipts (
       idempotency_key TEXT PRIMARY KEY, organization_id TEXT NOT NULL, owner_id TEXT NOT NULL,
       input_hash TEXT NOT NULL, prior_generation INTEGER NOT NULL CHECK(prior_generation>0),
       applied_generation INTEGER NOT NULL UNIQUE CHECK(applied_generation=prior_generation+1),
       source_revision TEXT NOT NULL, preview_digest TEXT NOT NULL, receipt_json TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS organization_setup_subscription_attempts (
       idempotency_key TEXT PRIMARY KEY REFERENCES organization_team_generations(idempotency_key),
       binding_json TEXT NOT NULL, binding_digest TEXT NOT NULL UNIQUE,
       dispatch_nonce_digest TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS organization_setup_subscription_dispatches (
       idempotency_key TEXT PRIMARY KEY REFERENCES organization_setup_subscription_attempts(idempotency_key),
       binding_digest TEXT NOT NULL, dispatch_nonce_digest TEXT NOT NULL, request_sha256 TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS organization_setup_subscription_outcomes (
       idempotency_key TEXT PRIMARY KEY REFERENCES organization_setup_subscription_attempts(idempotency_key),
       binding_digest TEXT NOT NULL, dispatch_nonce_digest TEXT NOT NULL,
       outcome_hash TEXT NOT NULL, outcome_json TEXT NOT NULL)''',
) + tuple(f'''CREATE TRIGGER IF NOT EXISTS {table}_immutable_{action.lower()}
 BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'Subscription audit is immutable'); END'''
          for table in sorted(SUBSCRIPTION_TABLES) for action in ('UPDATE', 'DELETE'))
_RECEIPT_FIELDS = set('''version action idempotencyKey organizationId ownerId priorGeneration
 appliedGeneration sourceRevision previewDigest candidate effectiveCallLimits effectiveTimeoutSeconds
 retiredGuarantees unchangedExecutionDigest retainedAttemptsDigest retainedCallCounts acknowledgement
 authorityEvidenceDigest connectionBindingDigest catalogEvidenceDigest'''.split())


def _digest_valid(value):
    return type(value) is str and re.fullmatch('[a-f0-9]{64}', value) is not None


def _load_canonical(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                _fail('subscription_ledger_corrupt')
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=pairs,
                           parse_constant=lambda _: _fail('subscription_ledger_corrupt'))
        if _canonical(value) != raw:
            _fail('subscription_ledger_corrupt')
        return value
    except (TypeError, ValueError):
        _fail('subscription_ledger_corrupt')


def _subscription_schema(conn):
    present = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")} & SUBSCRIPTION_TABLES
    if not present:
        return False
    if present != SUBSCRIPTION_TABLES:
        _fail('subscription_schema_unsupported')
    normalize = lambda sql: ''.join(sql.lower().replace('if not exists', '').split()).rstrip(';')
    for sql in SUBSCRIPTION_DDL:
        name = sql.split('IF NOT EXISTS ', 1)[1].split()[0]
        row = conn.execute('SELECT sql FROM sqlite_master WHERE name=?', (name,)).fetchone()
        if row is None or normalize(row[0]) != normalize(sql):
            _fail('subscription_schema_unsupported')
    expected_triggers = {f'{table}_immutable_{action}' for table in SUBSCRIPTION_TABLES
                         for action in ('update', 'delete')}
    actual_triggers = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name IN (?,?,?,?)",
        tuple(sorted(SUBSCRIPTION_TABLES)))}
    if actual_triggers != expected_triggers:
        _fail('subscription_schema_unsupported')
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND sql IS NOT NULL "
                    'AND tbl_name IN (?,?,?,?)', tuple(sorted(SUBSCRIPTION_TABLES))).fetchone():
        _fail('subscription_schema_unsupported')
    return True


def _receipts(conn):
    from eidolon_cli.organization_metadata import _inspect
    metadata = _inspect(conn)
    if metadata['state'] != 'ready':
        _fail('subscription_ledger_corrupt')
    identity = metadata['metadata']
    current = conn.execute('SELECT generation FROM organization_policy WHERE id=1').fetchone()[0]
    result = []
    for row in conn.execute('SELECT * FROM ' + _RECEIPT_TABLE + ' ORDER BY applied_generation'):
        receipt = _load_canonical(row['receipt_json'])
        _exact(receipt, _RECEIPT_FIELDS, 'subscription_ledger_corrupt')
        validate_candidate(receipt['candidate'])
        if (receipt['version'] != 1 or type(receipt['version']) is not int
                or receipt['action'] != 'retireSetupInheritance'
                or receipt['organizationId'] != identity['organizationId']
                or receipt['ownerId'] != identity['owner']['ownerId']
                or receipt['organizationId'] != row['organization_id']
                or receipt['ownerId'] != row['owner_id']
                or receipt['idempotencyKey'] != row['idempotency_key']
                or receipt['priorGeneration'] != row['prior_generation']
                or receipt['appliedGeneration'] != row['applied_generation']
                or type(receipt['priorGeneration']) is not int
                or type(receipt['appliedGeneration']) is not int
                or not 1 <= receipt['priorGeneration'] < receipt['appliedGeneration'] <= current
                or receipt['appliedGeneration'] != receipt['priorGeneration'] + 1
                or receipt['sourceRevision'] != row['source_revision']
                or receipt['previewDigest'] != row['preview_digest']
                or receipt['acknowledgement'] != ACKNOWLEDGEMENT):
            _fail('subscription_ledger_corrupt')
        for key in ('sourceRevision', 'previewDigest', 'unchangedExecutionDigest',
                    'retainedAttemptsDigest', 'authorityEvidenceDigest',
                    'connectionBindingDigest', 'catalogEvidenceDigest'):
            if not _digest_valid(receipt[key]):
                _fail('subscription_ledger_corrupt')
        if not _digest_valid(row['input_hash']):
            _fail('subscription_ledger_corrupt')
        _exact(receipt['effectiveCallLimits'], set(_CALL_FIELDS), 'subscription_ledger_corrupt')
        for key, value in receipt['effectiveCallLimits'].items():
            _integer(value, 0, receipt['candidate'][key], 'subscription_ledger_corrupt')
        _integer(receipt['effectiveTimeoutSeconds'], 1, 120, 'subscription_ledger_corrupt')
        _exact(receipt['retainedCallCounts'], {'legacy', 'generateTeam', 'improveTeamInput', 'total'},
               'subscription_ledger_corrupt')
        if (any(type(v) is not int or v < 0 for v in receipt['retainedCallCounts'].values())
                or receipt['retainedCallCounts']['total'] != sum(receipt['retainedCallCounts'][k]
                    for k in ('legacy', 'generateTeam', 'improveTeamInput'))
                or type(receipt['retiredGuarantees']) is not list):
            _fail('subscription_ledger_corrupt')
        limits = receipt['effectiveCallLimits']
        if (max(limits['improveCallLimit'], limits['generationCallLimit']) > limits['totalCallLimit']
                or receipt['effectiveTimeoutSeconds'] > receipt['candidate']['requestTimeoutSeconds']
                or receipt['connectionBindingDigest'] != receipt['candidate']['connectionId']):
            _fail('subscription_ledger_corrupt')
        seen = set()
        for guarantee in receipt['retiredGuarantees']:
            _exact(guarantee, {'guarantee', 'value', 'source', 'scope', 'historicalOrigin'},
                   'subscription_ledger_corrupt')
            name = guarantee['guarantee']
            if (name not in {'setup_product_total_tokens', 'max_total_tokens', 'max_context_tokens',
                             'max_output_tokens', 'max_cost_usd', 'model_costs'} or name in seen
                    or guarantee['scope'] != 'setup_only' or guarantee['historicalOrigin'] != 'unknown'
                    or guarantee['source'] != ('setup_product_ceiling' if name == 'setup_product_total_tokens'
                                               else 'execution_configuration_inheritance')):
                _fail('subscription_ledger_corrupt')
            seen.add(name)
        if not {'setup_product_total_tokens', 'max_total_tokens', 'max_context_tokens', 'max_output_tokens'} <= seen:
            _fail('subscription_ledger_corrupt')
        result.append(receipt)
    return result


def require_subscription_receipt(conn, policy):
    if not _subscription_schema(conn):
        _fail('subscription_schema_unsupported')
    receipts = _receipts(conn)
    if not receipts or receipts[-1]['candidate'] != policy:
        _fail('subscription_ledger_corrupt')
    return receipts[-1]


def validate_subscription_ledger(conn, *, allow_missing_attempt=None):
    """No migration or repair. Every typed attempt has one existing debt row."""
    if not _subscription_schema(conn):
        return
    from eidolon_cli.organization_setup_subscription_usage import AttemptBindingV1, TransportObservationV1
    receipts = _receipts(conn)
    generations = {row['idempotency_key']: row for row in conn.execute('SELECT * FROM organization_team_generations')}
    tags = {row['idempotency_key']: row for row in conn.execute('SELECT * FROM organization_team_generation_operations')}
    attempts = {row['idempotency_key']: row for row in conn.execute('SELECT * FROM ' + _ATTEMPT_TABLE)}
    dispatches = {row['idempotency_key']: row for row in conn.execute('SELECT * FROM ' + _DISPATCH_TABLE)}
    outcomes = {row['idempotency_key']: row for row in conn.execute('SELECT * FROM ' + _OUTCOME_TABLE)}
    if set(dispatches) - set(attempts) or set(outcomes) - set(dispatches) or set(attempts) - set(generations):
        _fail('subscription_ledger_corrupt')
    current_generation = conn.execute('SELECT generation FROM organization_policy WHERE id=1').fetchone()[0]
    first_generation = receipts[0]['appliedGeneration'] if receipts else None
    for key, row in generations.items():
        tag = tags.get(key)
        if (tag is None or tag['organization_id'] != row['organization_id']
                or tag['input_hash'] != row['input_hash']
                or type(tag['policy_generation']) is not int
                or not 1 <= tag['policy_generation'] <= current_generation):
            _fail('subscription_ledger_corrupt')
        if (first_generation is not None and tag['operation'] != 'legacy'
                and tag['policy_generation'] >= first_generation and key not in attempts
                and key != allow_missing_attempt):
            _fail('subscription_ledger_corrupt')
    for key, attempt in attempts.items():
        try:
            binding = AttemptBindingV1(**_load_canonical(attempt['binding_json']))
        except (ValueError, TypeError):
            _fail('subscription_ledger_corrupt')
        row, tag = generations[key], tags[key]
        prior = [r for r in receipts if r['appliedGeneration'] <= binding.policy_generation]
        if not prior:
            _fail('subscription_ledger_corrupt')
        receipt = prior[-1]
        candidate = receipt['candidate']
        operation = 'generateTeam' if binding.operation == 'generate' else 'improveTeamInput'
        if (binding.idempotency_key != key or binding.binding_digest != attempt['binding_digest']
                or not _digest_valid(attempt['dispatch_nonce_digest'])
                or binding.organization_id != row['organization_id']
                or binding.owner_id != receipt['ownerId'] or binding.input_hash != row['input_hash']
                or binding.source_revision != row['source_revision']
                or binding.policy_generation != tag['policy_generation'] or tag['operation'] != operation
                or binding.review_digest != receipt['previewDigest']
                or binding.connection_id != candidate['connectionId']
                or binding.account_binding_digest != receipt['connectionBindingDigest']
                or binding.authorization_epoch != candidate['authorizationEpoch']
                or binding.model_slug != candidate['modelSlug']
                or binding.timeout_seconds > receipt['effectiveTimeoutSeconds']
                or bool(row['dispatched']) != (key in dispatches)):
            _fail('subscription_ledger_corrupt')
        marker = dispatches.get(key)
        if marker and any(marker[field] != value for field, value in (
                ('binding_digest', binding.binding_digest),
                ('dispatch_nonce_digest', attempt['dispatch_nonce_digest']),
                ('request_sha256', binding.request_sha256))):
            _fail('subscription_ledger_corrupt')
        outcome = outcomes.get(key)
        if (row['reserved_tokens'] != 0 or row['reserved_cost'] is not None
                or row['shared_receipt'] is not None
                or (row['status'] == 'completed' and outcome is None)):
            _fail('subscription_ledger_corrupt')
        if outcome:
            try:
                observation = TransportObservationV1.from_dict(_load_canonical(outcome['outcome_json']))
            except (TypeError, ValueError, AttributeError):
                _fail('subscription_ledger_corrupt')
            if (outcome['binding_digest'] != binding.binding_digest
                    or outcome['dispatch_nonce_digest'] != attempt['dispatch_nonce_digest']
                    or observation.binding_digest != binding.binding_digest
                    or observation.dispatch_nonce_digest != attempt['dispatch_nonce_digest']
                    or observation.request_sha256 != binding.request_sha256
                    or observation.attempt_id != binding.attempt_id
                    or observation.outcome_hash != outcome['outcome_hash']
                    or observation.terminal_kind not in {'completed', 'failed', 'incomplete'}
                    or row['status'] == 'completed' and observation.terminal_kind != 'completed'):
                _fail('subscription_ledger_corrupt')


def fence_preflight(evidence, *, admitted_catalog=False):
    if type(evidence) is not SubscriptionPreflight or evidence.cheap_guard() is not True:
        _fail('local_owner_capability_required')
    import time
    expiry = evidence.catalog_evidence.get('expiresAt')
    if (type(expiry) not in (int, float) or not math.isfinite(expiry)
            or not admitted_catalog and expiry <= time.time()):
        _fail('catalog_evidence_expired')
    result = evidence.store.attest_subscription_binding_nonblocking(evidence.binding,
        expected_revision=evidence.revision, expected_epoch=evidence.epoch,
        expected_account_digest=evidence.account_digest, lease_generation_guard=evidence.cheap_guard)
    expected = {'account_binding_digest': evidence.account_digest,
                'authorization_epoch': evidence.epoch, 'store_revision': evidence.revision}
    if result != expected:
        _fail('connection_binding_changed')


def _closed_sources(conn, home):
    from eidolon_cli.config_primitives import _CONFIG_LOCK, managed_install_system
    from eidolon_cli.organization_config import profile_configuration_scope
    from eidolon_cli.organization_project_setup import _config_content
    from eidolon_cli.organization_owner_policy import _source
    from eidolon_cli import managed_scope
    with _CONFIG_LOCK, profile_configuration_scope(home):
        content, raw = _config_content(Path(home) / 'config.yaml')
        if type(raw) is not dict or set(raw) - (ORDINARY_NAMESPACES | {'organization'}):
            _fail('unsupported_scope')
        organization = raw.get('organization', {})
        if type(organization) is not dict:
            _fail('unsupported_scope')
        if 'shared_allowance' in organization:
            _fail('shared_scope_unsupported')
        if 'gateway_enabled' in organization and type(organization['gateway_enabled']) is not bool:
            _fail('unsupported_scope')
        if set(organization) - (OBJECTIVE_DEFAULT_KEYS | EXECUTION_KEYS):
            _fail('unsupported_scope')
        managed_scope.invalidate_managed_cache()
        managed = managed_scope.load_managed_config()
        if type(managed) is not dict or set(managed) - (ORDINARY_NAMESPACES | {'organization'}):
            _fail('unsupported_managed_scope')
        if 'organization' in managed or managed_install_system(Path(home)) is not None:
            _fail('managed_configuration')
        settings, source, locked = _source(home, conn)
        if locked:
            _fail('managed_configuration')
        authority = preview_digest({'source': hashlib.sha256(content).hexdigest(),
            'managed': managed, 'effective': asdict(settings),
            'organizationScopes': {key: ('objective_defaults' if key in OBJECTIVE_DEFAULT_KEYS
                else 'execution_only') for key in sorted(organization)},
            'ordinaryNamespaces': sorted(set(raw) - {'organization'})})
    return settings, source, authority


def _closed_ledger_schema(conn):
    """Current known table definitions, with optional groups fully absent or exact."""
    from contextlib import closing
    import sqlite3
    from eidolon_cli.organization_store import ORGANIZATION_SCHEMA
    from eidolon_cli.organization_metadata import _TABLES, _DDL
    from eidolon_cli.organization_team_budget import OPERATION_DDL
    normalize = lambda sql: ''.join(sql.lower().replace('if not exists', '').split()).rstrip(';')
    with closing(sqlite3.connect(':memory:')) as expected:
        expected.executescript(ORGANIZATION_SCHEMA)
        for sql in (*_DDL, *OPERATION_DDL, *SUBSCRIPTION_DDL):
            expected.execute(sql)
        expected_objects = {row[0]: (row[1], row[2], row[3]) for row in expected.execute(
            "SELECT name,type,tbl_name,sql FROM sqlite_master WHERE type IN ('trigger','index')")}
        known = {row[0]: row[1] for row in expected.execute(
            "SELECT name,sql FROM sqlite_master WHERE type='table'")}
    actual = {row[0]: row[1] for row in conn.execute(
        "SELECT name,sql FROM sqlite_master WHERE type='table'")}
    allowed = set(known) | set(_TABLES) | SUBSCRIPTION_TABLES | {'organization_team_generation_operations'}
    if set(actual) - allowed:
        _fail('unsupported_scope')
    actual_objects = {row[0]: (row[1], row[2], row[3]) for row in conn.execute(
        "SELECT name,type,tbl_name,sql FROM sqlite_master WHERE type IN ('trigger','index')")}
    expected_objects = {name: value for name, value in expected_objects.items() if value[1] in actual}
    if set(actual_objects) != set(expected_objects):
        _fail('unsupported_scope')
    for name, value in actual_objects.items():
        expected_value = expected_objects[name]
        if (value[:2] != expected_value[:2]
                or (normalize(value[2]) if value[2] is not None else None)
                != (normalize(expected_value[2]) if expected_value[2] is not None else None)):
            _fail('unsupported_scope')
    for name in set(actual) & set(known):
        if normalize(actual[name]) != normalize(known[name]):
            _fail('unsupported_scope')
    if 'organization_team_generation_operations' in actual:
        from eidolon_cli.organization_team_budget import _operation_schema
        _operation_schema(conn)
    validate_subscription_ledger(conn)


def _no_enclosing_scope(conn, home, organization_id):
    from eidolon_cli.organization_shared_budget import _read_scope_pin
    from eidolon_cli.organization_shared_allowance import (
        resolve_binding, capture_installation_scope, SharedAllowanceLedger)
    pin = _read_scope_pin(conn)
    binding = resolve_binding(home, organization_id, required=pin is not None)
    scope = capture_installation_scope(home)
    ledger = SharedAllowanceLedger(scope['root'], home=scope['home'])
    # A pool without an allocation still represents enclosing intent. Resolve
    # above proves its schema/identity; never interpret an unreadable pool as absent.
    if pin is not None or binding is not None or ledger._presence():
        _fail('shared_scope_unsupported')
    return []


def require_policy_unpaused(conn):
    """Honor the existing all-host pause without constructing or resuming a store."""
    from eidolon_cli.organization_policy import OrganizationPolicyStore
    if OrganizationPolicyStore._policy_paused(conn):
        _fail('organization_policy_paused')


def build_authoritative_snapshot(conn, home, transport, generation, preflight_evidence, *, completing_key=None, policy_review=False, admitted_catalog=False):
    from eidolon_cli.organization_team_setup import _current, _fresh
    from eidolon_cli.organization_team_budget import read_policy, operation_counts, _generation_rows
    from eidolon_cli.organization_policy import persisted_settings
    require_local_owner(transport, home, generation)
    evidence = preflight_evidence
    if type(evidence) is not SubscriptionPreflight or evidence.owner_generation is not generation:
        _fail('connection_binding_changed')
    identity, policy, settings, revision = _current(conn, home, evidence.binding.organization_id)
    require_policy_unpaused(conn)
    _closed_ledger_schema(conn)
    closed_settings, source_digest, authority_digest = _closed_sources(conn, home)
    if closed_settings != settings or persisted_settings(conn) != settings:
        _fail('source_policy_conflict')
    if not _fresh(conn, settings) or conn.execute('SELECT 1 FROM objective_budgets LIMIT 1').fetchone():
        _fail('organization_not_empty')
    if conn.execute("SELECT 1 FROM organization_team_generations WHERE status IN ('running','uncertain') "
                    'AND idempotency_key<>? LIMIT 1', (completing_key or '',)).fetchone():
        _fail('attempt_running_or_uncertain')
    constraints = _no_enclosing_scope(conn, home, identity['organizationId'])
    from eidolon_cli.organization_shared_allowance import capture_installation_scope, SharedAllowanceLedger
    scope = capture_installation_scope(home)
    shared = SharedAllowanceLedger(scope['root'], home=scope['home'])
    setup = read_policy(conn)
    if setup and setup['version'] == 3 and not policy_review:
        receipt = require_subscription_receipt(conn, setup)
        if (setup['connectionId'] != evidence.account_digest or setup['authorizationEpoch'] != evidence.epoch
                or setup['modelSlug'] != evidence.model_slug
                or receipt['connectionBindingDigest'] != evidence.account_digest):
            _fail('connection_binding_changed')
    if (evidence.binding.organization_id != identity['organizationId']
            or evidence.binding.owner_id != identity['owner']['ownerId']):
        _fail('connection_binding_changed')
    catalog = evidence.catalog_evidence
    if (type(catalog) is not dict or catalog.get('state') != 'ready'
            or catalog.get('connectionId') != evidence.account_digest
            or catalog.get('authorizationEpoch') != evidence.epoch
            or catalog.get('connectionRevision') != evidence.revision
            or catalog.get('sourceRevision') != revision
            or evidence.model_slug not in {m['slug'] for m in catalog.get('models', [])}):
        _fail('catalog_evidence_required')
    rows = [dict(row) for row in _generation_rows(conn)]
    tags = [dict(row) for row in conn.execute('SELECT * FROM organization_team_generation_operations '
            'ORDER BY idempotency_key')] if setup else []
    fence_preflight(evidence, admitted_catalog=admitted_catalog)
    return {'identity': identity, 'policyGeneration': policy['generation'], 'sourceRevision': revision,
        'settings': settings, 'setupPolicy': setup, 'executionDigest': preview_digest(asdict(settings)),
        'authorityEvidenceDigest': authority_digest, 'retainedAttemptsDigest': preview_digest([rows, tags]),
        'retainedCallCounts': operation_counts(conn), 'allOrganizationConstraints': constraints,
        'sourceDigest': source_digest, 'catalogEvidenceDigest': preview_digest(catalog),
        'connectionBindingDigest': evidence.account_digest,
        'sharedAuthorityPaths': (shared.path, shared.marker)}


def _runtime_owner(home, transport):
    import sys
    from tui_gateway.organization_credential_transport import owner_generation
    if sys.platform != 'darwin':
        _fail('subscription_platform_unsupported')
    generation = owner_generation(transport)
    require_local_owner(transport, home, generation)
    return generation


def _parameters(params, *, apply=False):
    fields = {'expectedOrganizationId', 'sourceRevision', 'expectedGeneration', 'configuration'}
    if apply:
        fields |= {'previewVersion', 'idempotencyKey', 'confirmed', 'acknowledgement'}
    _exact(params, fields, 'request_schema_unsupported')
    from eidolon_cli.organization_owner_policy import _envelope
    _envelope(params)
    validate_candidate(params['configuration'])
    if apply:
        _text(params['idempotencyKey'], 128, 'idempotency_key_invalid')
        if (params['confirmed'] is not True or params['acknowledgement'] != ACKNOWLEDGEMENT
                or not _digest_valid(params['previewVersion'])):
            _fail('acknowledgement_required')


def _preview_current(conn, home, params, transport, generation, evidence):
    snapshot = build_authoritative_snapshot(conn, home, transport, generation, evidence, policy_review=True)
    if snapshot['identity']['organizationId'] != params['expectedOrganizationId']:
        _fail('identity_conflict')
    if snapshot['sourceRevision'] != params['sourceRevision']:
        _fail('source_conflict')
    if snapshot['policyGeneration'] != params['expectedGeneration']:
        _fail('generation_conflict')
    candidate = validate_candidate(params['configuration'])
    if (candidate['connectionId'] != evidence.account_digest
            or candidate['authorizationEpoch'] != evidence.epoch or candidate['modelSlug'] != evidence.model_slug):
        _fail('connection_binding_changed')
    settings = snapshot['settings']
    effective = {k: min(candidate[k], settings.max_model_calls) for k in _CALL_FIELDS}
    for key in ('improveCallLimit', 'generationCallLimit'):
        effective[key] = min(effective[key], effective['totalCallLimit'])
    counts = snapshot['retainedCallCounts']
    remaining = max(0, effective['totalCallLimit'] - counts['total'])
    retired = [{'guarantee': 'setup_product_total_tokens', 'value': 120000,
                'source': 'setup_product_ceiling', 'scope': 'setup_only', 'historicalOrigin': 'unknown'}]
    execution = asdict(settings)
    for field in ('max_total_tokens', 'max_context_tokens', 'max_output_tokens', 'max_cost_usd', 'model_costs'):
        value = execution[field]
        if value is not None and value != ():
            retired.append({'guarantee': field, 'value': value, 'source': 'execution_configuration_inheritance',
                            'scope': 'setup_only', 'historicalOrigin': 'unknown'})
    preview = {'capabilityVersion': 1, 'organizationId': snapshot['identity']['organizationId'],
        'ownerId': snapshot['identity']['owner']['ownerId'], 'sourceRevision': snapshot['sourceRevision'],
        'policyGeneration': snapshot['policyGeneration'], 'configuration': candidate,
        'priorPolicy': snapshot['setupPolicy'], 'effectiveCallLimits': effective,
        'effectiveTimeoutSeconds': min(120, candidate['requestTimeoutSeconds'], settings.objective_timeout_seconds),
        'retainedInheritanceCallCeiling': settings.max_model_calls, 'retainedCallCounts': counts,
        'remainingCalls': {'total': remaining,
            'generateTeam': min(remaining, max(0, effective['generationCallLimit'] - counts['generateTeam'])),
            'improveTeamInput': min(remaining, max(0, effective['improveCallLimit'] - counts['improveTeamInput']))},
        'retiredGuarantees': retired, 'unchangedExecutionDigest': snapshot['executionDigest'],
        'retainedAttemptsDigest': snapshot['retainedAttemptsDigest'],
        'authorityEvidenceDigest': snapshot['authorityEvidenceDigest'],
        'connectionBindingDigest': snapshot['connectionBindingDigest'],
        'catalogEvidenceDigest': snapshot['catalogEvidenceDigest'],
        'allOrganizationConstraints': snapshot['allOrganizationConstraints'],
        'acknowledgement': _copy(ACKNOWLEDGEMENT), 'hardTokenGuarantee': False, 'hardUsdGuarantee': False,
        'warnings': ['source_files_are_not_sqlite_atomic', 'local_timeout_does_not_stop_upstream_usage',
                     'historical_origins_unknown', 'execution_constraints_unchanged', 'prior_charges_retained']}
    preview = _copy(preview)
    preview['previewVersion'] = preview_digest(preview)
    return preview, snapshot


def preview_subscription_policy(home, params, transport):
    _parameters(params)
    generation = _runtime_owner(home, transport)
    from tui_gateway.methods_organization_setup_subscription import subscription_preflight
    from eidolon_cli.organization_owner_policy import _connection
    evidence = subscription_preflight(transport, home, params['configuration'])
    try:
        with _connection(home) as conn:
            return _preview_current(conn, home, params, transport, generation, evidence)[0]
    finally:
        evidence.release()


def apply_subscription_policy(home, params, transport):
    _parameters(params, apply=True)
    generation = _runtime_owner(home, transport)
    from eidolon_cli.organization_owner_policy import _connection, _identity, _schema, _writer
    from eidolon_cli.organization_owner_policy_config import OWNER_POLICY_DDL
    from eidolon_cli.organization_team_budget import OPERATION_DDL, read_policy
    from tui_gateway.organization_credential_transport import owner_publication
    from tui_gateway.methods_organization_setup_subscription import subscription_preflight, turnover_subscription_context
    input_hash = preview_digest({'action': 'retireSetupInheritance', 'params': params})
    # Historical replay requires current local-owner identity, but never grant or
    # catalog resurrection. It returns no readiness or credential capability.
    with _connection(home) as conn:
        identity = _identity(conn, params['expectedOrganizationId'])
        if _subscription_schema(conn):
            for row in conn.execute('SELECT input_hash,receipt_json FROM ' + _RECEIPT_TABLE + ' WHERE idempotency_key=?',
                                    (params['idempotencyKey'],)):
                _receipts(conn)
                if row['input_hash'] != input_hash:
                    _fail('idempotency_conflict')
                return {'capabilityVersion': 1, 'receipt': _load_canonical(row['receipt_json']), 'historical': True}
    evidence = subscription_preflight(transport, home, params['configuration'])
    try:
        with owner_publication(transport, home, generation), _connection(home, write=True) as conn:
            # Recheck the key after acquiring the writer; concurrent apply never duplicates.
            identity = _identity(conn, params['expectedOrganizationId'])
            if _subscription_schema(conn):
                row = conn.execute('SELECT input_hash,receipt_json FROM ' + _RECEIPT_TABLE +
                                   ' WHERE idempotency_key=?', (params['idempotencyKey'],)).fetchone()
                if row:
                    _receipts(conn)
                    if row['input_hash'] != input_hash:
                        _fail('idempotency_conflict')
                    return {'capabilityVersion': 1, 'receipt': _load_canonical(row['receipt_json']), 'historical': True}
            preview, snapshot = _preview_current(conn, home, params, transport, generation, evidence)
            if preview['previewVersion'] != params['previewVersion']:
                _fail('preview_conflict')
            previous = read_policy(conn)
            policy = _schema(conn)
            store = _writer(conn, home, snapshot['settings'], policy)
            store._require_current_policy(conn)
            for sql in (*OWNER_POLICY_DDL, *OPERATION_DDL, *SUBSCRIPTION_DDL):
                conn.execute(sql)
            if previous is None:
                conn.execute('INSERT INTO organization_team_generation_operations '
                    'SELECT idempotency_key,organization_id,input_hash,?,? FROM organization_team_generations',
                    ('legacy', policy['generation'] + 1))
            row = conn.execute('SELECT configuration FROM organization_owner_budget_policy WHERE id=1').fetchone()
            override = json.loads(row[0]) if row else {}
            override['setup_budget'] = preview['configuration']
            conn.execute('INSERT INTO organization_owner_budget_policy VALUES (1,?) ON CONFLICT(id) '
                         'DO UPDATE SET configuration=excluded.configuration', (_canonical(override),))
            store._adopt_policy(conn, force=True, resume=False)
            if store.settings != snapshot['settings'] or store._policy_generation != snapshot['policyGeneration'] + 1:
                _fail('source_policy_conflict')
            receipt = {'version': 1, 'action': 'retireSetupInheritance', 'idempotencyKey': params['idempotencyKey'],
                'organizationId': identity['organizationId'], 'ownerId': identity['owner']['ownerId'],
                'priorGeneration': snapshot['policyGeneration'], 'appliedGeneration': store._policy_generation,
                'sourceRevision': params['sourceRevision'], 'previewDigest': preview['previewVersion'],
                'candidate': preview['configuration'], 'effectiveCallLimits': preview['effectiveCallLimits'],
                'effectiveTimeoutSeconds': preview['effectiveTimeoutSeconds'],
                **{key: preview[key] for key in ('retiredGuarantees', 'unchangedExecutionDigest',
                    'retainedAttemptsDigest', 'retainedCallCounts', 'acknowledgement', 'authorityEvidenceDigest',
                    'connectionBindingDigest', 'catalogEvidenceDigest')}}
            conn.execute('INSERT INTO ' + _RECEIPT_TABLE + ' VALUES (?,?,?,?,?,?,?,?,?)',
                (params['idempotencyKey'], identity['organizationId'], identity['owner']['ownerId'], input_hash,
                 snapshot['policyGeneration'], store._policy_generation, params['sourceRevision'],
                 preview['previewVersion'], _canonical(receipt)))
            settings, source, authority = _closed_sources(conn, home)
            if (settings != snapshot['settings'] or source != snapshot['sourceDigest']
                    or authority != snapshot['authorityEvidenceDigest']):
                _fail('source_conflict')
            _no_enclosing_scope(conn, home, identity['organizationId'])
            validate_subscription_ledger(conn)
            read_policy(conn)
            fence_preflight(evidence)
            require_local_owner(transport, home, generation)
    finally:
        evidence.release()
    # Committed receipt is never rolled back because cache turnover failed.
    result = {'capabilityVersion': 1, 'receipt': receipt, 'historical': False}
    try:
        result.update(turnover_subscription_context(transport, home, identity['organizationId']))
    except (RuntimeError, ValueError, OSError):
        result['contextState'] = 'refresh_required'
    return result


def readiness_subscription_policy(home, expected_id, transport):
    """Passive metadata only: no native read, lease, catalog request or refresh."""
    from eidolon_cli.organization_owner_policy import _connection
    from eidolon_cli.organization_team_setup import _current, _fresh
    from eidolon_cli.organization_team_budget import read_policy, budget_view
    generation = _runtime_owner(home, transport)
    with _connection(home) as conn:
        identity, policy, settings, revision = _current(conn, home, expected_id)
        setup = read_policy(conn)
        reason = None
        try:
            require_policy_unpaused(conn)
            _closed_ledger_schema(conn)
            _closed_sources(conn, home)
            _no_enclosing_scope(conn, home, expected_id)
            if not _fresh(conn, settings) or conn.execute('SELECT 1 FROM objective_budgets LIMIT 1').fetchone():
                _fail('organization_not_empty')
            if conn.execute("SELECT 1 FROM organization_team_generations WHERE status IN ('running','uncertain') LIMIT 1").fetchone():
                _fail('attempt_running_or_uncertain')
        except SubscriptionPolicyError as exc:
            reason = str(exc)
        require_local_owner(transport, home, generation)
        return {'capabilityVersion': 1, 'organizationId': identity['organizationId'],
            'ownerId': identity['owner']['ownerId'],
            'policyVersion': setup['version'] if setup else 1, 'policyGeneration': policy['generation'],
            'sourceRevision': revision, 'configuration': setup, 'setupBudget': budget_view(conn, settings),
            'state': 'blocked' if reason else 'available', 'reason': reason,
            'connectionReadiness': 'unchecked', 'modelAvailability': 'unchecked', 'executionVerified': False}
