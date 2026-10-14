"""Captured completion headroom inside, never in addition to, objective caps."""
from decimal import Decimal


COMPLETION_POLICY = 'completion-v1'
COMPLETION_REQUEST_TYPES = ('request.review', 'request.test_review', 'request.integrate', 'request.accept')
COMPLETION_RESERVE_FIELDS = ('completion_reserve_model_calls', 'completion_reserve_tokens', 'completion_reserve_cost_usd')
COMPLETION_RESERVE_SCHEMA = """
CREATE TABLE IF NOT EXISTS organization_completion_reserve_migration (
 id INTEGER PRIMARY KEY CHECK(id=1));
CREATE TRIGGER IF NOT EXISTS completion_migration_immutable_update
 BEFORE UPDATE ON organization_completion_reserve_migration BEGIN SELECT RAISE(ABORT,'Completion migration is immutable'); END;
CREATE TRIGGER IF NOT EXISTS completion_migration_immutable_delete
 BEFORE DELETE ON organization_completion_reserve_migration BEGIN SELECT RAISE(ABORT,'Completion migration is immutable'); END;
CREATE TABLE IF NOT EXISTS objective_completion_reserves (
 objective_id TEXT PRIMARY KEY REFERENCES objectives(id), policy TEXT NOT NULL,
 model_calls INTEGER NOT NULL CHECK(model_calls>=0), tokens INTEGER NOT NULL CHECK(tokens>=0),
 cost_usd TEXT);
CREATE TABLE IF NOT EXISTS organization_model_call_classes (
 model_call_id TEXT PRIMARY KEY REFERENCES organization_model_calls(id),
 request_type TEXT NOT NULL, classification TEXT NOT NULL CHECK(classification IN ('ordinary','completion')),
 policy TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS completion_reserve_immutable_update
 BEFORE UPDATE ON objective_completion_reserves BEGIN SELECT RAISE(ABORT,'Completion reserve is immutable'); END;
CREATE TRIGGER IF NOT EXISTS completion_reserve_immutable_delete
 BEFORE DELETE ON objective_completion_reserves BEGIN SELECT RAISE(ABORT,'Completion reserve is immutable'); END;
CREATE TRIGGER IF NOT EXISTS model_call_class_immutable_update
 BEFORE UPDATE ON organization_model_call_classes BEGIN SELECT RAISE(ABORT,'Model call classification is immutable'); END;
CREATE TRIGGER IF NOT EXISTS model_call_class_immutable_delete
 BEFORE DELETE ON organization_model_call_classes BEGIN SELECT RAISE(ABORT,'Model call classification is immutable'); END;
CREATE TRIGGER IF NOT EXISTS model_call_immutable_update
 BEFORE UPDATE ON organization_model_calls BEGIN SELECT RAISE(ABORT,'Model call reservation is immutable'); END;
CREATE TRIGGER IF NOT EXISTS model_call_immutable_delete
 BEFORE DELETE ON organization_model_calls BEGIN SELECT RAISE(ABORT,'Model call reservation is immutable'); END;
CREATE TRIGGER IF NOT EXISTS request_type_immutable_update
 BEFORE UPDATE OF type ON requests WHEN NEW.type!=OLD.type
 BEGIN SELECT RAISE(ABORT,'Request type is backend-owned and immutable'); END;
"""


def configured_reserve(settings, *, model_calls=None, tokens=None, cost_usd=None):
    """Replacement hard caps may be lower than today's fresh-objective caps."""
    cost = settings.completion_reserve_cost_usd
    if cost is not None and cost_usd is not None:
        cost = format(min(Decimal(cost), Decimal(cost_usd)), 'f')
    return {'version': 1, 'policy': COMPLETION_POLICY, 'captured': {
        'modelCalls': min(settings.completion_reserve_model_calls, settings.max_model_calls if model_calls is None else model_calls),
        'tokens': min(settings.completion_reserve_tokens, settings.max_total_tokens if tokens is None else tokens),
        'configuredCostUsd': cost}}


def capture_reserve(conn, objective_id, allowance):
    captured = allowance['captured']
    conn.execute('INSERT OR IGNORE INTO objective_completion_reserves VALUES (?,?,?,?,?)',
                 (objective_id, allowance['policy'], captured['modelCalls'], captured['tokens'], captured['configuredCostUsd']))


def migrate_completion_reserves(conn):
    # The marker distinguishes an old ledger from missing new-schema evidence.
    # Later corruption must fail closed, never reclassify a completion as ordinary.
    if conn.execute('SELECT 1 FROM organization_completion_reserve_migration WHERE id=1').fetchone():
        return
    # Set-based, idempotent migration never grants protection to retained work.
    conn.execute("INSERT OR IGNORE INTO objective_completion_reserves "
                 "SELECT id,?,0,0,NULL FROM objectives", (COMPLETION_POLICY,))
    conn.execute("INSERT OR IGNORE INTO organization_model_call_classes "
                 "SELECT c.id,r.type,'ordinary','legacy-v0' FROM organization_model_calls c "
                 "JOIN requests r ON r.id=c.request_id")
    conn.execute("INSERT INTO organization_completion_reserve_migration VALUES (1)")


def completion_reserve_view(conn, objective_id, calls, budget):
    row = conn.execute('SELECT * FROM objective_completion_reserves WHERE objective_id=?', (objective_id,)).fetchone()
    if row is None or row['policy'] != COMPLETION_POLICY:
        raise ValueError('Objective completion reserve is unavailable; reconnect before resuming work')
    complete = [call for call in calls if call['classification'] == 'completion']
    captured = {'modelCalls': row['model_calls'], 'tokens': row['tokens'], 'configuredCostUsd': row['cost_usd']}
    known = not budget['legacyUsageUnknown']
    cost_enabled = budget['configuredCostLimitUsd'] is not None
    cost_known = budget['configuredCostReservedUsd'] is not None
    classified = all(call['classification'] is not None for call in calls)
    reason = ('legacy-usage-unknown' if not known else 'classification-unknown' if not classified else
              'configured-cost-unknown' if cost_enabled and not cost_known else None)
    completed = {'modelCalls': len(complete), 'tokens': sum(call['input_limit'] + call['output_limit'] for call in complete),
                 'configuredCostUsd': (format(sum((Decimal(call['reserved_cost_usd']) for call in complete), Decimal(0)), 'f')
                                       if cost_enabled and all(call['reserved_cost_usd'] is not None for call in complete) else None)}
    if not known or not classified:
        completed = dict.fromkeys(('modelCalls', 'tokens', 'configuredCostUsd'))
    total, protected, ordinary = {}, {}, {}
    for key, used, limit in (('modelCalls', 'modelCalls', 'modelCallLimit'), ('tokens', 'reservedTokens', 'tokenLimit')):
        total[key] = max(0, budget[limit] - budget[used]) if known and classified else None
        protected[key] = max(0, captured[key] - completed[key]) if known and classified else None
        ordinary[key] = max(0, total[key] - protected[key]) if known and classified else None
    cost_total = max(Decimal(0), Decimal(budget['configuredCostLimitUsd']) - Decimal(budget['configuredCostReservedUsd'])) if known and classified and cost_enabled and cost_known else None
    cost_protected = max(Decimal(0), Decimal(row['cost_usd'] or '0') - Decimal(completed['configuredCostUsd'])) if cost_total is not None else None
    for target, value in ((total, cost_total), (protected, cost_protected),
                          (ordinary, max(Decimal(0), cost_total - cost_protected) if cost_total is not None else None)):
        target['configuredCostUsd'] = format(value, 'f') if value is not None else None
    return {'version': 1, 'policy': row['policy'], 'requestTypes': list(COMPLETION_REQUEST_TYPES),
            'captured': captured, 'completionReserved': completed, 'protectedRemaining': protected,
            'ordinaryRemaining': ordinary, 'totalRemaining': total,
            'admissionUsageKnown': reason is None, 'unavailableReason': reason}


def require_ordinary_headroom(view, tokens, cost):
    remaining = view['completionReserve']['ordinaryRemaining']
    dimensions = [('modelCalls', 1, 'model-call'), ('tokens', tokens, 'reserved-token')]
    if cost is not None and view['configuredCostLimitUsd'] is not None:
        dimensions.append(('configuredCostUsd', cost, 'configured-cost'))
    for field, required, label in dimensions:
        value = remaining[field]
        if value is None or Decimal(value) < required:
            raise ValueError(f'The next ordinary model call would consume protected completion {label} reserve; no call was sent. Completion is not guaranteed.')
