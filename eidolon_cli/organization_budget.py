"""Durable admission ceilings; unknown provider usage never refunds a reservation.

Money is computed only from explicit owner-configured route ceilings. It is an
admission policy, not a provider invoice or a claim to know current token prices.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import json
import time
import uuid

BUDGET_SCHEMA = """
CREATE TABLE IF NOT EXISTS objective_budgets (
 objective_id TEXT PRIMARY KEY REFERENCES objectives(id), max_model_calls INTEGER NOT NULL,
 max_total_tokens INTEGER NOT NULL, deadline REAL NOT NULL, max_cost_usd TEXT,
 legacy_usage_unknown INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS organization_model_calls (
 id TEXT PRIMARY KEY, request_id TEXT NOT NULL REFERENCES requests(id), token TEXT NOT NULL,
 objective_id TEXT NOT NULL REFERENCES objectives(id), provider TEXT NOT NULL, model TEXT NOT NULL,
 input_limit INTEGER NOT NULL, output_limit INTEGER NOT NULL, reserved_cost_usd TEXT,
 created REAL NOT NULL, pricing TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS model_calls_objective ON organization_model_calls(objective_id);
CREATE TABLE IF NOT EXISTS organization_evidence_passes (
 request_id TEXT NOT NULL REFERENCES requests(id), token TEXT NOT NULL, pass_index INTEGER NOT NULL,
 report TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(request_id,token,pass_index));
CREATE TABLE IF NOT EXISTS organization_context_receipts (
 request_id TEXT NOT NULL REFERENCES requests(id), token TEXT NOT NULL,
 report TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(request_id,token));
"""


@dataclass(frozen=True)
class OrganizationModelCost:
    provider: str
    model: str
    input_usd_per_million: str
    output_usd_per_million: str


def money(value, label, *, allow_zero=True):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError(f'{label} must be an explicit finite USD decimal')
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f'{label} must be an explicit finite USD decimal') from exc
    if not amount.is_finite() or amount < 0 or amount > 1_000_000 or (not allow_zero and not amount):
        raise ValueError(f'{label} must be a finite positive USD decimal (at most 1000000)')
    if amount.as_tuple().exponent < -9:
        raise ValueError(f'{label} supports at most nine decimal places')
    return format(amount, 'f')


def parse_model_costs(value):
    if not isinstance(value, list) or len(value) > 64:
        raise ValueError('organization.model_costs must be a bounded list of exact provider/model ceilings')
    result, seen = [], set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {'provider', 'model', 'input_usd_per_million', 'output_usd_per_million'}:
            raise ValueError('Each model cost requires provider, model, input_usd_per_million and output_usd_per_million')
        for field in ('provider', 'model'):
            if not isinstance(item[field], str) or not item[field].strip() or len(item[field]) > 300 or '*' in item[field]:
                raise ValueError('Model cost ceilings require exact bounded provider and model names')
        route = (item['provider'].strip(), item['model'].strip())
        if route in seen:
            raise ValueError('Model cost ceiling routes must be unique')
        seen.add(route)
        result.append(OrganizationModelCost(*route, *(money(item[field], field) for field in ('input_usd_per_million', 'output_usd_per_million'))))
    return tuple(result)


def _minimum_money(original, configured):
    values = [Decimal(value) for value in (original, configured) if value is not None]
    return min(values) if values else None


def budget_view(conn, objective_id, settings):
    row = conn.execute('SELECT * FROM objective_budgets WHERE objective_id=?', (objective_id,)).fetchone()
    if row is None:
        return {}
    calls = conn.execute('SELECT input_limit,output_limit,reserved_cost_usd FROM organization_model_calls WHERE objective_id=?', (objective_id,)).fetchall()
    cost_limit = _minimum_money(row['max_cost_usd'], settings.max_cost_usd)
    costs_complete = all(call['reserved_cost_usd'] is not None for call in calls) and not row['legacy_usage_unknown']
    cost = sum((Decimal(call['reserved_cost_usd']) for call in calls if call['reserved_cost_usd'] is not None), Decimal(0))
    from eidolon_cli.organization_store import _iso
    deadline = min(row['deadline'], conn.execute('SELECT created FROM objectives WHERE id=?', (objective_id,)).fetchone()[0] + settings.objective_timeout_seconds)
    return {'modelCalls': len(calls), 'modelCallLimit': min(row['max_model_calls'], settings.max_model_calls),
            'reservedTokens': sum(call['input_limit'] + call['output_limit'] for call in calls),
            'tokenLimit': min(row['max_total_tokens'], settings.max_total_tokens),
            'deadlineAt': _iso(deadline), 'deadlineTimestamp': deadline,
            'configuredCostReservedUsd': format(cost, 'f') if costs_complete else None,
            'configuredCostLimitUsd': format(cost_limit, 'f') if cost_limit is not None else None,
            'legacyUsageUnknown': bool(row['legacy_usage_unknown']),
            'budgetScope': 'Durable admission reservations, including interrupted or unreported calls; never refunded. USD uses only configured exact-route ceilings, not current provider prices or an invoice. The deadline includes time awaiting owner input.'}


def budget_reason(conn, objective_id, settings):
    view = budget_view(conn, objective_id, settings)
    if not view:
        return 'Objective budget is unavailable; reconnect before resuming work.'
    if view['legacyUsageUnknown']:
        return 'Prior model usage is unknown. Retained work remains available; create a separately budgeted objective before authorizing more calls.'
    if time.time() >= view['deadlineTimestamp']:
        return 'Objective deadline reached. Automatic execution has stopped; create a revised objective to authorize a new deadline.'
    if view['modelCalls'] >= view['modelCallLimit']:
        return 'Objective model-call budget exhausted. Automatic execution has stopped.'
    if view['reservedTokens'] >= view['tokenLimit']:
        return 'Objective reserved-token budget exhausted. Automatic execution has stopped.'
    if view['configuredCostLimitUsd'] is not None:
        if view['configuredCostReservedUsd'] is None:
            return 'Earlier model cost is unknown. A configured USD ceiling cannot authorize another call; review the retained work and start a separately budgeted objective.'
        if Decimal(view['configuredCostReservedUsd']) >= Decimal(view['configuredCostLimitUsd']):
            return 'Objective configured-cost budget exhausted. Automatic execution has stopped.'
    return None


class OrganizationBudgetStore:
    def _initialize_budget(self, conn, objective_id, *, legacy=False):
        created = conn.execute('SELECT created FROM objectives WHERE id=?', (objective_id,)).fetchone()[0]
        unknown = legacy and conn.execute("SELECT 1 FROM objective_usage WHERE objective_id=? AND stage NOT IN ('request.hire','request.apply','request.validate')", (objective_id,)).fetchone() is not None
        conn.execute('INSERT OR IGNORE INTO objective_budgets VALUES (?,?,?,?,?,?)',
                     (objective_id, self.settings.max_model_calls, self.settings.max_total_tokens,
                      created + self.settings.objective_timeout_seconds, self.settings.max_cost_usd, int(unknown)))

    def _migrate_budgets(self, conn):
        for row in conn.execute('SELECT id FROM objectives').fetchall():
            self._initialize_budget(conn, row['id'], legacy=True)

    def reserve_model_call(self, claim, *, provider, model, input_limit, output_limit):
        from eidolon_cli.organization_store import _text
        provider, model = _text(provider, 'Model provider', 300), _text(model, 'Model route', 300)
        if any(type(value) is not int or not 1 <= value <= 2_000_000 for value in (input_limit, output_limit)):
            raise ValueError('Model reservation requires bounded input and output token ceilings')
        if input_limit + output_limit > self.settings.max_context_tokens or output_limit > self.settings.max_output_tokens:
            raise ValueError('Model reservation exceeds the configured context/output ceiling')
        with self._write() as conn:
            request = self._owned(conn, claim)
            if request is None:
                raise ValueError('Request is no longer owned; no model call was admitted')
            reason = budget_reason(conn, request['objective_id'], self.settings)
            if reason:
                raise ValueError(reason)
            view = budget_view(conn, request['objective_id'], self.settings)
            if view['reservedTokens'] + input_limit + output_limit > view['tokenLimit']:
                raise ValueError('The next model call would exceed the objective reserved-token budget; no call was sent.')
            rate = next((item for item in self.settings.model_costs if (item.provider, item.model) == (provider, model)), None)
            cost = None if rate is None else ((Decimal(input_limit) * Decimal(rate.input_usd_per_million) + Decimal(output_limit) * Decimal(rate.output_usd_per_million)) / 1_000_000).quantize(Decimal('0.000000001'), rounding=ROUND_CEILING)
            if view['configuredCostLimitUsd'] is not None:
                if cost is None:
                    raise ValueError('No configured cost ceiling exists for this exact provider/model. No model call was sent; configure both input and output ceilings before retrying.')
                if Decimal(view['configuredCostReservedUsd']) + cost > Decimal(view['configuredCostLimitUsd']):
                    raise ValueError('The next model call would exceed the objective configured-cost budget; no call was sent.')
            ident = 'modelcall_' + uuid.uuid4().hex
            pricing = {'policyGeneration': self._policy_generation,
                       'inputUsdPerMillion': rate.input_usd_per_million if rate else None,
                       'outputUsdPerMillion': rate.output_usd_per_million if rate else None}
            conn.execute('INSERT INTO organization_model_calls VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                         (ident, request['id'], request['token'], request['objective_id'], provider, model,
                          input_limit, output_limit, format(cost, 'f') if cost is not None else None, time.time(), json.dumps(pricing)))
            return ident

    def record_context_receipt(self, claim, report):
        if not isinstance(report, dict):
            raise ValueError('Context receipt must be a bounded JSON object')
        serialized = json.dumps(report, sort_keys=True, allow_nan=False)
        if len(serialized) > 100000:
            raise ValueError('Context receipt metadata exceeds its bound')
        with self._write() as conn:
            if self._owned(conn, claim) is None:
                raise ValueError('Request is no longer owned; no context receipt was accepted')
            old = conn.execute('SELECT report FROM organization_context_receipts WHERE request_id=? AND token=?', (claim['id'], claim['token'])).fetchone()
            if old:
                if old[0] != serialized:
                    raise ValueError('Context receipt is immutable for an execution attempt')
                return False
            conn.execute('INSERT INTO organization_context_receipts VALUES (?,?,?,?)', (claim['id'], claim['token'], serialized, time.time()))
            return True

    def record_evidence_pass(self, claim, report):
        if not isinstance(report, dict) or type(report.get('index')) is not int or not 0 <= report['index'] < 64:
            raise ValueError('Evidence pass must have a bounded integer index')
        serialized = json.dumps(report, sort_keys=True, allow_nan=False)
        if len(serialized) > 24000:
            raise ValueError('Evidence pass exceeds its retained audit bound')
        with self._write() as conn:
            if self._owned(conn, claim) is None:
                raise ValueError('Request is no longer owned; no evidence pass was accepted')
            old = conn.execute('SELECT report FROM organization_evidence_passes WHERE request_id=? AND token=? AND pass_index=?',
                               (claim['id'], claim['token'], report['index'])).fetchone()
            if old:
                if old[0] != serialized:
                    raise ValueError('Evidence passes are immutable for an execution attempt')
                return False
            conn.execute('INSERT INTO organization_evidence_passes VALUES (?,?,?,?,?)',
                         (claim['id'], claim['token'], report['index'], serialized, time.time()))
            return True

    def _verify_context_completion(self, conn, request, result):
        receipt = conn.execute('SELECT report FROM organization_context_receipts WHERE request_id=? AND token=?', (request['id'], request['token'])).fetchone()
        passes = [json.loads(row[0]) for row in conn.execute('SELECT report FROM organization_evidence_passes WHERE request_id=? AND token=? ORDER BY pass_index', (request['id'], request['token']))]
        if receipt is None and not passes:
            return  # Existing deterministic/backend-only executors do not send model context.
        if receipt is None:
            raise ValueError('Evidence passes require a complete terminal context receipt')
        from eidolon_cli.organization_evidence import verify_context_receipt
        verify_context_receipt(self.context(dict(request)), json.loads(receipt[0]), passes,
                               approved=result.get('approved') is True)

    def execution_audit(self, request_id):
        from eidolon_cli.organization_store import _text, _iso
        identifier = _text(request_id, 'Request ID', 128)
        with self._connect() as conn:
            if conn.execute('SELECT 1 FROM requests WHERE id=?', (identifier,)).fetchone() is None:
                raise ValueError('Request not found in this profile')
            calls = [{**dict(row), 'pricing': json.loads(row['pricing']), 'createdAt': _iso(row['created'])} for row in conn.execute('SELECT * FROM organization_model_calls WHERE request_id=? ORDER BY created,id', (identifier,))]
            contexts = [{'attemptToken': row['token'], 'report': json.loads(row['report']), 'createdAt': _iso(row['created'])}
                        for row in conn.execute('SELECT * FROM organization_context_receipts WHERE request_id=? ORDER BY created', (identifier,))]
            passes = [{'attemptToken': row['token'], 'report': json.loads(row['report']), 'createdAt': _iso(row['created'])}
                      for row in conn.execute('SELECT * FROM organization_evidence_passes WHERE request_id=? ORDER BY created,pass_index', (identifier,))]
            return {'requestId': identifier, 'modelCalls': calls, 'contexts': contexts, 'evidencePasses': passes}
