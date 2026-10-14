"""At-most-once, bounded setup inference; uncertain sends never auto-replay."""
from decimal import Decimal
import json
import time

from eidolon_cli.organization_owner_policy import OwnerPolicyError, _connection, _hash, _writer
from eidolon_cli.organization_metadata import _uuid
from eidolon_cli.organization_roster import _text
from eidolon_cli.organization_team_setup import _current, _fresh, _preview


def _reserve(home, params, *, provider, model, input_limit, output_limit, selected_provider=None):
    if (type(input_limit) is not int or type(output_limit) is not int
            or not 0 < input_limit <= 32768 or not 0 < output_limit <= 8000):
        raise ValueError('Team generation exceeds its bounded input/output allowance')
    with _connection(home, write=True) as conn:
        _, policy, settings, revision = _current(conn, home, params['expectedOrganizationId'])
        if revision != params['sourceRevision'] or not _fresh(conn, settings):
            raise OwnerPolicyError('source_conflict')
        key = params['idempotencyKey']
        row = conn.execute('SELECT * FROM organization_team_generations WHERE idempotency_key=?', (key,)).fetchone()
        if not row or row['status'] != 'running' or row['dispatched']:
            raise ValueError('A team generation permits exactly one physical provider send')
        previous = conn.execute('SELECT reserved_tokens,reserved_cost FROM organization_team_generations WHERE dispatched=1').fetchall()
        tokens = input_limit + output_limit
        if (len(previous) >= min(3, settings.max_model_calls)
                or sum(item['reserved_tokens'] for item in previous) + tokens > min(120000, settings.max_total_tokens)
                or tokens > settings.max_context_tokens or output_limit > settings.max_output_tokens):
            raise ValueError('Starter generation allowance is exhausted or exceeds the configured token limits; use a manual team')
        from eidolon_cli.organization_shared_budget import configured_call_cost, reserve_shared_call
        route_provider = str(selected_provider or provider)
        cost = configured_call_cost(settings, route_provider, str(model), input_limit, output_limit)
        if settings.max_cost_usd is not None:
            if cost is None or any(item['reserved_cost'] is None for item in previous):
                raise ValueError('Starter generation requires configured exact-route prices under the existing cost limit')
            if sum((Decimal(item['reserved_cost']) for item in previous), Decimal(0)) + Decimal(cost) > Decimal(settings.max_cost_usd):
                raise ValueError('Starter generation exceeds the configured cost limit; use a manual team')
        store = _writer(conn, home, settings, policy)
        shared = reserve_shared_call(store, conn, {
            'kind': 'team_setup', 'generationId': _hash([params['expectedOrganizationId'], key]),
            'provider': route_provider, 'model': str(model), 'inputLimit': input_limit,
            'outputLimit': output_limit, 'reservedCostUsd': cost})
        receipt = None
        if shared:
            call_key, receipt, pin = shared
            conn.execute('INSERT OR IGNORE INTO organization_shared_scope_pin VALUES (1,?)',
                         (json.dumps(pin, sort_keys=True, separators=(',', ':')),))
        conn.execute('UPDATE organization_team_generations SET dispatched=1,reserved_tokens=?,reserved_cost=?,shared_receipt=? WHERE idempotency_key=?',
                     (tokens, cost, json.dumps(receipt) if receipt else None, key))


def _input(value, field, maximum, *, required=True):
    if (not isinstance(value, str) or len(value) > maximum
            or any(ord(char) < 32 and char not in '\n\r\t' for char in value)
            or (required and not value.strip())):
        raise ValueError(f'{field} must be valid text of at most {maximum} characters')
    return value.strip()


def generate_team(home, params, *, cancel=None):
    _uuid(params.get('expectedOrganizationId'), 'expectedOrganizationId')
    key = _text(params.get('idempotencyKey'), 'Idempotency key', 128)
    jobs = _input(params.get('jobs'), 'Jobs', 4000)
    goals = _input(params.get('goals'), 'Goals', 4000)
    naming = _input(params.get('namingStyle'), 'Naming style', 500, required=False)
    digest = _hash(params)
    reservation_params = {**params, 'idempotencyKey': key}
    from eidolon_cli.organization_team_generation import generate, TeamProposalRejected
    with _connection(home, write=True) as conn:
        _, _, settings, revision = _current(conn, home, params['expectedOrganizationId'])
        prior = conn.execute('SELECT * FROM organization_team_generations WHERE idempotency_key=?', (key,)).fetchone()
        if prior:
            if prior['organization_id'] != params['expectedOrganizationId'] or prior['input_hash'] != digest:
                raise OwnerPolicyError('idempotency_conflict')
            if prior['status'] == 'completed':
                return json.loads(prior['result_json'])
            raise OwnerPolicyError('generation_' + prior['status'])
        if params.get('sourceRevision') != revision or not _fresh(conn, settings):
            raise OwnerPolicyError('source_conflict')
        if conn.execute("SELECT 1 FROM organization_team_generations WHERE status IN ('running','uncertain')").fetchone():
            # Restart-interrupted sends remain uncertain and cannot admit new spend.
            raise OwnerPolicyError('generation_in_progress_or_uncertain')
        conn.execute('INSERT INTO organization_team_generations '
            '(idempotency_key,organization_id,input_hash,source_revision,status,created) VALUES (?,?,?,?,?,?)',
            (key, params['expectedOrganizationId'], digest, revision, 'running', time.time()))
    received = False
    try:
        team = generate(home, jobs, goals, naming,
            reserve_model_call=lambda **values: _reserve(home, reservation_params, **values), cancel=cancel)
        received = True
        # Both parsed output and any edits must pass the identical owner validator.
        with _connection(home, write=True) as conn:
            row = conn.execute('SELECT dispatched FROM organization_team_generations WHERE idempotency_key=?', (key,)).fetchone()
            if not row or not row['dispatched']:
                raise ValueError('Team generation did not reserve a physical provider send')
            result = _preview(conn, home, {**params, 'team': team})[0]
            conn.execute("UPDATE organization_team_generations SET status='completed',result_json=? WHERE idempotency_key=?",
                         (json.dumps(result), key))
        return result
    except BaseException as exc:
        known_response = received or isinstance(exc, TeamProposalRejected)
        with _connection(home, write=True) as conn:
            conn.execute("UPDATE organization_team_generations SET status=CASE WHEN dispatched=1 AND ?=0 THEN 'uncertain' ELSE 'failed' END WHERE idempotency_key=?", (known_response, key))
        raise
