"""Explicit, ledger-only setup allowances with retained physical-send charges.

The absent override is the legacy shared-three policy. Only the owner apply
transaction creates the operation ledger; ordinary runtime initialization never
changes the policy, serialization, or schema of an unactivated organization.
"""
from decimal import Decimal, InvalidOperation
import json

from eidolon_cli.organization_metadata import _inspect, _text, _uuid
from eidolon_cli.organization_model_routing import _json
from eidolon_cli.organization_owner_policy import (
    OwnerPolicyError, _connection, _envelope, _hash, _identity, _replay,
    _schema, _source, _writer,
)
from eidolon_cli.organization_owner_policy_config import OWNER_POLICY_DDL, managed_fields


SUGGESTED_POLICY = {'version': 2, 'mode': 'bounded_api', 'improveCallLimit': 4,
                    'generationCallLimit': 3, 'totalCallLimit': 7}
_OPERATIONS = {'legacy', 'improveTeamInput', 'generateTeam'}
_TABLE = 'organization_team_generation_operations'
OPERATION_DDL = (
    '''CREATE TABLE IF NOT EXISTS organization_team_generation_operations (
       idempotency_key TEXT PRIMARY KEY REFERENCES organization_team_generations(idempotency_key),
       organization_id TEXT NOT NULL, input_hash TEXT NOT NULL,
       operation TEXT NOT NULL CHECK(operation IN ('legacy','improveTeamInput','generateTeam')),
       policy_generation INTEGER NOT NULL CHECK(policy_generation>0))''',
    '''CREATE TRIGGER IF NOT EXISTS organization_team_generation_operations_immutable_update
       BEFORE UPDATE ON organization_team_generation_operations
       BEGIN SELECT RAISE(ABORT,'Setup operation is immutable'); END''',
    '''CREATE TRIGGER IF NOT EXISTS organization_team_generation_operations_immutable_delete
       BEFORE DELETE ON organization_team_generation_operations
       BEGIN SELECT RAISE(ABORT,'Setup operation is immutable'); END''',
)


def validate_policy(raw):
    """Validate an exact owner candidate; zero explicitly disables setup calls."""
    if (not isinstance(raw, dict) or set(raw) != set(SUGGESTED_POLICY)
            or type(raw['version']) is not int or raw['version'] != 2
            or raw['mode'] != 'bounded_api'):
        raise ValueError('Setup budget requires version 2, bounded_api mode and all three call limits')
    for key in ('improveCallLimit', 'generationCallLimit', 'totalCallLimit'):
        if type(raw[key]) is not int or not 0 <= raw[key] <= 1000:
            raise ValueError('Setup call limits must be integers between 0 and 1000')
    if max(raw['improveCallLimit'], raw['generationCallLimit']) > raw['totalCallLimit']:
        raise ValueError('Each setup operation limit must fit inside the total call limit')
    return {key: raw[key] for key in SUGGESTED_POLICY}


def validate_stored_policy(raw):
    if isinstance(raw, dict) and raw.get('version') == 3:
        from eidolon_cli.organization_setup_subscription_policy import validate_candidate
        return validate_candidate(raw)
    return validate_policy(raw)


def _exists(conn, name):
    return conn.execute('SELECT 1 FROM sqlite_master WHERE name=?', (name,)).fetchone() is not None


def _operation_schema(conn):
    def normalized(sql):
        return ''.join(sql.lower().replace('if not exists', '').split()).rstrip(';')
    for sql in OPERATION_DDL:
        name = sql.split('IF NOT EXISTS ', 1)[1].split()[0]
        row = conn.execute('SELECT sql FROM sqlite_master WHERE name=?', (name,)).fetchone()
        if row is None or normalized(row[0]) != normalized(sql):
            raise OwnerPolicyError('setup_budget_unsupported')


def _stored_policy(conn):
    row = (conn.execute('SELECT configuration FROM organization_owner_budget_policy WHERE id=1').fetchone()
           if _exists(conn, 'organization_owner_budget_policy') else None)
    try:
        saved = json.loads(row[0]) if row else {}
        if not isinstance(saved, dict):
            raise ValueError()
        policy = validate_stored_policy(saved['setup_budget']) if 'setup_budget' in saved else None
    except (ValueError, TypeError, KeyError):
        raise OwnerPolicyError('setup_budget_corrupt') from None
    if policy is None:
        if _exists(conn, _TABLE):
            raise OwnerPolicyError('setup_budget_corrupt')
    else:
        _operation_schema(conn)
    return policy


def _generation_rows(conn):
    rows = conn.execute('SELECT idempotency_key,organization_id,input_hash,source_revision,status,'
                        'dispatched,reserved_tokens,reserved_cost FROM organization_team_generations '
                        'ORDER BY idempotency_key').fetchall()
    for row in rows:
        if (type(row['dispatched']) is not int or row['dispatched'] not in (0, 1)
                or type(row['reserved_tokens']) is not int or row['reserved_tokens'] < 0
                or row['status'] not in {'running', 'uncertain', 'completed', 'failed'}):
            raise OwnerPolicyError('setup_budget_corrupt')
        if row['dispatched'] and row['reserved_cost'] is not None:
            try:
                cost = Decimal(row['reserved_cost'])
                if not cost.is_finite() or cost < 0:
                    raise ValueError()
            except (TypeError, ValueError, InvalidOperation):
                raise OwnerPolicyError('setup_budget_corrupt') from None
    return rows


def _operation_rows(conn, rows, *, allow_missing=None):
    descriptor = _inspect(conn)
    if descriptor['state'] != 'ready':
        raise OwnerPolicyError('setup_budget_corrupt')
    organization_id = descriptor['metadata']['organizationId']
    current = conn.execute('SELECT generation FROM organization_policy WHERE id=1').fetchone()[0]
    tags = {row['idempotency_key']: dict(row) for row in conn.execute('SELECT * FROM ' + _TABLE)}
    if set(tags) - {row['idempotency_key'] for row in rows}:
        raise OwnerPolicyError('setup_budget_corrupt')
    for row in rows:
        tag = tags.get(row['idempotency_key'])
        if row['organization_id'] != organization_id:
            raise OwnerPolicyError('setup_budget_corrupt')
        if tag is None and row['idempotency_key'] == allow_missing:
            continue
        if (tag is None or tag['operation'] not in _OPERATIONS
                or tag['organization_id'] != organization_id
                or tag['input_hash'] != row['input_hash']
                or type(tag['policy_generation']) is not int
                or not 1 <= tag['policy_generation'] <= current):
            raise OwnerPolicyError('setup_budget_corrupt')
    return tags


def read_policy(conn):
    """Return the explicitly adopted policy, rejecting incomplete accounting."""
    policy = _stored_policy(conn)
    from eidolon_cli.organization_setup_subscription_policy import SUBSCRIPTION_TABLES, validate_subscription_ledger
    present = any(_exists(conn, name) for name in SUBSCRIPTION_TABLES)
    if present and (policy is None or policy['version'] != 3):
        raise OwnerPolicyError('subscription_mode_transition_unsupported')
    if policy is not None:
        _operation_rows(conn, _generation_rows(conn))
        _require_receipt(conn, policy)
        validate_subscription_ledger(conn)
    return policy


def operation_counts(conn):
    policy = read_policy(conn)
    rows = (_generation_rows(conn) if policy is not None else
            conn.execute('SELECT dispatched FROM organization_team_generations').fetchall())
    tags = _operation_rows(conn, rows) if policy is not None else {}
    counts = {'legacy': 0, 'improveTeamInput': 0, 'generateTeam': 0, 'total': 0}
    for row in rows:
        if row['dispatched']:
            operation = tags[row['idempotency_key']]['operation'] if policy is not None else 'legacy'
            counts[operation] += 1
            counts['total'] += 1
    return counts


def operation_for(conn, key):
    policy = read_policy(conn)
    if policy is None:
        return 'legacy'
    row = conn.execute('SELECT operation FROM ' + _TABLE + ' WHERE idempotency_key=?', (key,)).fetchone()
    if row is None:
        raise OwnerPolicyError('setup_budget_corrupt')
    return row[0]


def record_operation(conn, key, org, operation, generation):
    """Called in the SAME transaction that inserts the trusted inference row."""
    policy = _stored_policy(conn)
    if policy is None:
        return
    _require_receipt(conn, policy)
    if operation not in {'improveTeamInput', 'generateTeam'}:
        raise ValueError('Only a trusted setup operation can bind a new inference')
    rows = _generation_rows(conn)
    _operation_rows(conn, rows, allow_missing=key)
    row = next((row for row in rows if row['idempotency_key'] == key), None)
    current = conn.execute('SELECT generation FROM organization_policy WHERE id=1').fetchone()[0]
    if (row is None or row['organization_id'] != org or row['dispatched']
            or row['status'] != 'running' or type(generation) is not int or generation != current):
        raise OwnerPolicyError('setup_budget_corrupt')
    conn.execute('INSERT INTO ' + _TABLE + ' VALUES (?,?,?,?,?)',
                 (key, org, row['input_hash'], operation, generation))


def budget_view(conn, settings, *, completing_key=None):
    policy = read_policy(conn)
    counts = operation_counts(conn)
    rows = (_generation_rows(conn) if policy is not None else
            conn.execute('SELECT idempotency_key,status,dispatched,reserved_tokens,reserved_cost '
                         'FROM organization_team_generations').fetchall())
    sent = [row for row in rows if row['dispatched']]
    total = min(policy['totalCallLimit'] if policy else 3, settings.max_model_calls)
    improve = min(policy['improveCallLimit'], total) if policy else total
    generate = min(policy['generationCallLimit'], total) if policy else total
    remaining = max(0, total - counts['total'])
    improve_remaining = min(remaining, max(0, improve - counts['improveTeamInput']))
    generate_remaining = min(remaining, max(0, generate - counts['generateTeam']))
    if policy and policy['version'] == 3:
        return _subscription_budget_view(conn, policy, settings, rows, counts, total, improve, generate,
                                         remaining, improve_remaining, generate_remaining, completing_key)
    invalid_tokens = any(type(row['reserved_tokens']) is not int or row['reserved_tokens'] < 0 for row in sent)
    tokens = sum(row['reserved_tokens'] for row in sent) if not invalid_tokens else 0
    token_limit = min(120000, settings.max_total_tokens)
    costs, invalid_cost = [], False
    for row in sent:
        try:
            cost = Decimal(row['reserved_cost'])
            if not cost.is_finite() or cost < 0:
                raise ValueError()
            costs.append(cost)
        except (TypeError, ValueError, InvalidOperation):
            invalid_cost = True
    cost_total = format(sum(costs, Decimal(0)), 'f') if not invalid_cost else None
    pending = {row['status'] for row in rows if row['idempotency_key'] != completing_key}
    status = 'available'
    if 'uncertain' in pending:
        status = 'uncertain'
    elif 'running' in pending:
        status = 'running'
    elif not remaining:
        status = 'exhausted'
    elif invalid_tokens:
        status = 'blocked'
    elif settings.max_cost_usd is not None and invalid_cost:
        status = 'blocked'
    elif (not remaining or not (improve_remaining or generate_remaining) or tokens >= token_limit
          or (settings.max_cost_usd is not None and Decimal(cost_total) >= Decimal(settings.max_cost_usd))):
        status = 'exhausted'
    return {'policyVersion': 2 if policy else 1, 'policy': policy, 'usage': counts,
            'limits': {'improveCalls': improve, 'generationCalls': generate, 'totalCalls': total,
                       'totalTokens': token_limit, 'maxCostUsd': settings.max_cost_usd},
            'remaining': {'improveCalls': improve_remaining, 'generationCalls': generate_remaining,
                          'totalCalls': remaining}, 'status': status,
            'reservedTokens': tokens, 'reservedCostUsd': cost_total}


def _current(conn, home, expected_id):
    identity = _identity(conn, expected_id)
    policy = _schema(conn)
    settings, source, locked = _source(home, conn)
    for table in ('organization_team_setup', 'organization_team_generations'):
        if not _exists(conn, table):
            raise OwnerPolicyError('execution_not_initialized')
    setup = read_policy(conn)
    rows = [dict(row) for row in _generation_rows(conn)]
    tags = [dict(row) for row in conn.execute('SELECT * FROM ' + _TABLE + ' ORDER BY idempotency_key')] if setup else []
    revision = _hash([source, identity, policy['generation'], setup, rows, tags])
    return identity, policy, settings, revision, locked


def _latest_receipt(conn, identity):
    if not _exists(conn, 'organization_owner_policy_receipts'):
        return None
    latest = None
    for row in conn.execute('SELECT * FROM organization_owner_policy_receipts WHERE organization_id=? AND owner_id=?',
                            (identity['organizationId'], identity['owner']['ownerId'])):
        try:
            result = json.loads(row['result_json'])
            receipt = result['receipt']
            if receipt.get('action') != 'setupBudget':
                continue
            if (receipt['organizationId'] != identity['organizationId']
                    or receipt['ownerId'] != identity['owner']['ownerId']
                    or receipt['idempotencyKey'] != row['idempotency_key']
                    or type(receipt['policyGeneration']) is not int
                    or receipt['policyGeneration'] < 1):
                raise ValueError()
            validate_policy(receipt['configuration'])
            if latest is None or receipt['policyGeneration'] > latest['policyGeneration']:
                latest = receipt
        except (ValueError, KeyError, TypeError, AttributeError):
            raise OwnerPolicyError('setup_budget_corrupt') from None
    return latest


def _require_receipt(conn, policy):
    if policy['version'] == 3:
        from eidolon_cli.organization_setup_subscription_policy import require_subscription_receipt
        return require_subscription_receipt(conn, policy)
    descriptor = _inspect(conn)
    if descriptor['state'] != 'ready':
        raise OwnerPolicyError('setup_budget_corrupt')
    receipt = _latest_receipt(conn, descriptor['metadata'])
    generation = conn.execute('SELECT generation FROM organization_policy WHERE id=1').fetchone()[0]
    if (receipt is None or receipt['policyGeneration'] > generation
            or _json(receipt['configuration']) != _json(policy)):
        raise OwnerPolicyError('setup_budget_corrupt')


def budget_policy(home, expected_id):
    _uuid(expected_id, 'expectedOrganizationId')
    with _connection(home) as conn:
        identity, policy, settings, revision, locked = _current(conn, home, expected_id)
        from eidolon_cli.organization_team_setup import _fresh
        return {'capabilityVersion': 1, 'state': 'ready', 'organizationId': identity['organizationId'],
                'ownerId': identity['owner']['ownerId'], 'policyGeneration': policy['generation'],
                'sourceRevision': revision, 'configuration': read_policy(conn),
                'suggestedConfiguration': dict(SUGGESTED_POLICY),
                'managed': 'setup_budget' in locked, 'available': _fresh(conn, settings),
                'setupBudget': budget_view(conn, settings), 'latestReceipt': _latest_receipt(conn, identity)}


def _preview(conn, home, params):
    identity, policy, settings, revision, locked = _current(conn, home, params['expectedOrganizationId'])
    if params['expectedGeneration'] != policy['generation']:
        raise OwnerPolicyError('generation_conflict')
    if params['sourceRevision'] != revision:
        raise OwnerPolicyError('source_conflict')
    from eidolon_cli.organization_team_setup import _fresh
    if not _fresh(conn, settings):
        raise OwnerPolicyError('organization_not_empty')
    if 'setup_budget' in locked:
        raise OwnerPolicyError('managed_configuration')
    if conn.execute("SELECT 1 FROM organization_team_generations WHERE status IN ('running','uncertain') LIMIT 1").fetchone():
        raise OwnerPolicyError('team_generation_in_progress_or_uncertain')
    if read_policy(conn) and read_policy(conn)['version'] == 3:
        raise OwnerPolicyError('subscription_mode_transition_unsupported')
    canonical = validate_policy(params.get('configuration'))
    from eidolon_cli.organization_policy import persisted_settings
    if persisted_settings(conn) != settings:
        raise OwnerPolicyError('source_policy_conflict')
    before = budget_view(conn, settings)
    core = {'action': 'setupBudget', 'organizationId': identity['organizationId'],
            'ownerId': identity['owner']['ownerId'], 'policyGeneration': policy['generation'],
            'sourceRevision': revision, 'configuration': canonical, 'usage': before}
    return ({'capabilityVersion': 1, **{key: value for key, value in core.items() if key != 'action'},
             'previewVersion': _hash(core), 'changes': [{'field': 'setup_budget',
                 'before': before['policy'], 'after': canonical}],
             'warnings': ['existing_charges_retained', 'legacy_calls_aggregate_only',
                          'enclosing_call_token_cost_limits_retained', 'no_calls_or_grants_created']},
            identity, policy, settings)


def preview_budget(home, params):
    _envelope(params)
    with _connection(home) as conn:
        return _preview(conn, home, params)[0]


def apply_budget(home, params):
    _envelope(params)
    if params.get('confirmed') is not True:
        raise ValueError('Review and confirm the setup budget before applying')
    key = _text(params.get('idempotencyKey'), 'idempotencyKey', 128)
    version = params.get('previewVersion')
    if not isinstance(version, str) or len(version) != 64:
        raise ValueError('previewVersion requires the reviewed setup budget')
    digest = _hash({'action': 'setupBudget', 'params': params})
    with _connection(home, write=True) as conn:
        identity = _identity(conn, params['expectedOrganizationId'])
        previous = _replay(conn, key, digest, identity, 'setupBudget', params)
        if previous is not None:
            return previous
        preview, identity, policy, settings = _preview(conn, home, params)
        if version != preview['previewVersion']:
            raise OwnerPolicyError('preview_conflict')
        if _json(params['configuration']) != _json(preview['configuration']):
            raise ValueError('Apply requires the exact canonical preview candidate')
        for sql in OWNER_POLICY_DDL:
            conn.execute(sql)
        store = _writer(conn, home, settings, policy)
        store._require_current_policy(conn)
        previous_policy = read_policy(conn)
        if previous_policy is None:
            for sql in OPERATION_DDL:
                conn.execute(sql)
            conn.execute('INSERT INTO ' + _TABLE + ' SELECT idempotency_key,organization_id,input_hash,?,? '
                         'FROM organization_team_generations', ('legacy', policy['generation'] + 1))
        row = conn.execute('SELECT configuration FROM organization_owner_budget_policy WHERE id=1').fetchone()
        override = json.loads(row[0]) if row else {}
        override['setup_budget'] = preview['configuration']
        conn.execute('INSERT INTO organization_owner_budget_policy VALUES (1,?) ON CONFLICT(id) '
                     'DO UPDATE SET configuration=excluded.configuration', (_json(override),))
        store._adopt_policy(conn, force=True, resume=False)
        if store.settings != settings:
            raise OwnerPolicyError('source_conflict')
        if 'setup_budget' in managed_fields():
            raise OwnerPolicyError('managed_configuration')
        receipt = {'idempotencyKey': key, 'organizationId': identity['organizationId'],
                   'ownerId': identity['owner']['ownerId'], 'action': 'setupBudget',
                   'policyGeneration': store._policy_generation, 'sourceRevision': preview['sourceRevision'],
                   'previewVersion': version, 'configuration': preview['configuration']}
        result = {'capabilityVersion': 1, 'receipt': receipt}
        conn.execute('INSERT INTO organization_owner_policy_receipts VALUES (?,?,?,?,?)',
                     (key, identity['organizationId'], identity['owner']['ownerId'], digest, _json(result)))
        read_policy(conn)
        store._event(conn, None, 'Owner reviewed setup call limits; all prior setup charges were retained.',
                     'configuration', 'owner')
    try:
        from eidolon_cli.organization_owner_policy import _refresh_existing
        _refresh_existing(home)
    except Exception:
        import logging
        logging.getLogger(__name__).exception('Setup budget committed; cached service refresh deferred')
    return result



def _subscription_budget_view(conn, policy, settings, rows, counts, total, improve, generate,
                              remaining, improve_remaining, generate_remaining, completing_key):
    from eidolon_cli.organization_setup_subscription_usage import TransportObservationV1
    pending = {row['status'] for row in rows if row['idempotency_key'] != completing_key}
    status = ('uncertain' if 'uncertain' in pending else 'running' if 'running' in pending
              else 'exhausted' if not remaining or not (improve_remaining or generate_remaining) else 'available')
    observations = []
    for row in conn.execute('SELECT idempotency_key,outcome_json FROM organization_setup_subscription_outcomes '
                            'ORDER BY idempotency_key'):
        observation = TransportObservationV1.from_dict(json.loads(row['outcome_json']))
        observations.append({'idempotencyKey': row['idempotency_key'],
                             'terminalKind': observation.terminal_kind, 'usage': observation.usage.to_dict()})
    attempts = conn.execute('SELECT count(*) FROM organization_setup_subscription_dispatches').fetchone()[0]
    observed = [row['usage'] for row in observations if row['usage']['token_status'] == 'observed']
    all_observed = attempts > 0 and len(observed) == attempts
    usage = {'version': 1, 'tokenStatus': 'observed' if all_observed else 'unknown',
             'inputTokens': sum(row['input_tokens'] for row in observed) if all_observed else None,
             'outputTokens': sum(row['output_tokens'] for row in observed) if all_observed else None,
             'totalTokens': sum(row['total_tokens'] for row in observed) if all_observed else None,
             'costStatus': 'unknown', 'costUsd': None, 'subscriptionCalls': attempts,
             'observations': observations}
    return {'policyVersion': 3, 'policy': policy, 'usage': counts,
            'limits': {'improveCalls': improve, 'generationCalls': generate, 'totalCalls': total,
                       'totalTokens': None, 'maxCostUsd': None},
            'remaining': {'improveCalls': improve_remaining, 'generationCalls': generate_remaining,
                          'totalCalls': remaining}, 'status': status,
            'reservedTokens': None, 'reservedCostUsd': None, 'setupUsage': usage,
            'requestTimeoutSeconds': min(120, policy['requestTimeoutSeconds'], settings.objective_timeout_seconds),
            'hardTokenGuarantee': False, 'hardUsdGuarantee': False}
