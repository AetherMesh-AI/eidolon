"""Conservative bridge between profile reservations and a shared allowance ledger.

The shared debit commits first. A later local rollback retains that debit; this
module never refunds it or treats a receipt lookup as permission to send again.
"""
from __future__ import annotations

import json
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
import time

SHARED_CALL_SCHEMA = """
CREATE TABLE IF NOT EXISTS organization_shared_scope_pin (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1), binding_json TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS shared_scope_pin_immutable_update
 BEFORE UPDATE ON organization_shared_scope_pin
 BEGIN SELECT RAISE(ABORT,'Shared allowance scope pin is immutable'); END;
CREATE TRIGGER IF NOT EXISTS shared_scope_pin_immutable_delete
 BEFORE DELETE ON organization_shared_scope_pin
 BEGIN SELECT RAISE(ABORT,'Shared allowance scope pin is immutable'); END;
CREATE TABLE IF NOT EXISTS organization_shared_call_receipts (
 call_key TEXT PRIMARY KEY,
 model_call_id TEXT UNIQUE REFERENCES organization_model_calls(id),
 owner_chat_turn_id TEXT UNIQUE REFERENCES owner_chat_turns(id),
 receipt_json TEXT NOT NULL, created REAL NOT NULL,
 CHECK ((model_call_id IS NOT NULL) != (owner_chat_turn_id IS NOT NULL)));
CREATE TRIGGER IF NOT EXISTS shared_call_receipt_immutable_update
 BEFORE UPDATE ON organization_shared_call_receipts
 BEGIN SELECT RAISE(ABORT,'Shared allowance receipt is immutable'); END;
CREATE TRIGGER IF NOT EXISTS shared_call_receipt_immutable_delete
 BEFORE DELETE ON organization_shared_call_receipts
 BEGIN SELECT RAISE(ABORT,'Shared allowance receipt is immutable'); END;
"""


def organization_id(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='organization_metadata'").fetchone():
        return None
    row = conn.execute('SELECT organization_id FROM organization_metadata WHERE singleton=1').fetchone()
    return row[0] if row else None


def configured_call_cost(settings, provider, model, input_limit, output_limit):
    rate = next((item for item in settings.model_costs if (item.provider, item.model) == (provider, model)), None)
    if rate is None:
        return None
    cost = ((Decimal(input_limit) * Decimal(rate.input_usd_per_million)
             + Decimal(output_limit) * Decimal(rate.output_usd_per_million)) / 1_000_000
            ).quantize(Decimal('0.000000001'), rounding=ROUND_CEILING)
    return format(cost, 'f')


def _state_home(path):
    path = Path(path)
    return path.parent.parent if path.parent.name == 'organization' else path.parent


def _binding_pin(binding):
    return {key: binding[key] for key in ('root', 'home', 'ledgerId', 'organizationId', 'ownerId')}


def _read_scope_pin(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    prior = conn.execute('SELECT binding_json FROM organization_shared_scope_pin WHERE singleton=1').fetchone() if 'organization_shared_scope_pin' in tables else None
    retained = ('organization_shared_call_receipts' in tables and
                conn.execute('SELECT 1 FROM organization_shared_call_receipts LIMIT 1').fetchone() is not None)
    retained = retained or ('organization_team_generations' in tables and conn.execute(
        'SELECT 1 FROM organization_team_generations WHERE shared_receipt IS NOT NULL LIMIT 1').fetchone() is not None)
    retained = retained or ('organization_request_intakes' in tables and conn.execute(
        'SELECT 1 FROM organization_request_intakes WHERE shared_receipt IS NOT NULL LIMIT 1').fetchone() is not None)
    if prior is None:
        if retained:
            raise ValueError('Shared allowance scope provenance is missing; no call was sent.')
        return None
    try:
        pin = json.loads(prior[0])
        if (not isinstance(pin, dict) or set(pin) != {'root', 'home', 'ledgerId', 'organizationId', 'ownerId'}
                or any(not isinstance(value, str) or not value for value in pin.values())):
            raise ValueError()
        return pin
    except (ValueError, TypeError):
        raise ValueError('Shared allowance scope provenance is unreadable; no call was sent.') from None


def reserve_shared_call(store, conn, payload):
    """Called with the profile writer held; all shared writers follow this order."""
    from eidolon_cli.organization_shared_allowance import (
        debit, physical_call_key, resolve_binding, verify_receipt,
    )
    anchor = getattr(store, '_shared_state_path', Path(store.path).resolve())
    home, current_home = _state_home(anchor), _state_home(Path(store.path).resolve())
    if Path(anchor).resolve() != Path(anchor):
        raise ValueError('Organization store canonical path changed after opening; no call was sent.')
    pin = _read_scope_pin(conn)
    if pin is not None and pin['home'] != str(home):
        raise ValueError('Shared allowance scope provenance changed; no call was sent.')
    binding = resolve_binding(home, organization_id(conn), **({'required': True} if pin else {}))
    if current_home != home:
        other = resolve_binding(current_home, organization_id(conn))
        if pin or binding is not None or other is not None:
            raise ValueError('Shared allowance store path changed after opening; no call was sent.')
    if binding is None:
        return None
    if pin is not None and pin != _binding_pin(binding):
        raise ValueError('Shared allowance ledger identity changed; no call was sent.')
    key = physical_call_key(binding, payload)
    # An already-debited key must fail here. Reconciliation is audit-only and
    # cannot turn an uncertain previous physical send into a fresh permission.
    receipt = debit(binding, key, payload)
    verify_receipt(binding, receipt, key, payload)
    return key, receipt, _binding_pin(binding)


def record_shared_receipt(conn, reserved, *, model_call_id=None, owner_chat_turn_id=None):
    if reserved is None:
        return
    key, receipt, pin = reserved
    conn.execute('INSERT OR IGNORE INTO organization_shared_scope_pin VALUES (1,?)',
                 (json.dumps(pin, sort_keys=True, separators=(',', ':'), allow_nan=False),))
    conn.execute('INSERT INTO organization_shared_call_receipts VALUES (?,?,?,?,?)',
                 (key, model_call_id, owner_chat_turn_id,
                  json.dumps(receipt, sort_keys=True, separators=(',', ':'), allow_nan=False), time.time()))


def shared_projection(conn, *, internal=False):
    """Passive manager view, with member-visible effective headroom only."""
    from eidolon_cli.organization_shared_allowance import read_member_projection, resolve_binding, SharedAllowanceError
    row = next((row for row in conn.execute('PRAGMA database_list') if row[1] == 'main'), None)
    if row is None or not row[2]:
        return None
    try:
        pin = _read_scope_pin(conn)
    except ValueError as exc:
        return {'state': 'blocked', 'reason': str(exc), 'scope': 'owner_declared_installation'}
    try:
        home = _state_home(Path(row[2]).resolve())
        binding = resolve_binding(home, organization_id(conn), required=pin is not None)
        if pin is not None and (binding is None or pin != _binding_pin(binding)):
            return {'state': 'blocked', 'reason': 'Shared allowance ledger identity changed.', 'scope': 'owner_declared_installation'}
        value = read_member_projection(home, organization_id(conn), required=pin is not None)
    except SharedAllowanceError as exc:
        return {'state': 'blocked', 'reason': str(exc), 'scope': 'owner_declared_installation'}
    except (OSError, RuntimeError):
        return {'state': 'blocked', 'reason': 'Shared allowance storage is unavailable.', 'scope': 'owner_declared_installation'}
    if value.get('state') != 'ready' or value.get('member') is None:
        return None
    effective = {}
    for field in ('ordinaryRemaining', 'remaining'):
        effective[field] = {}
        for dimension in ('modelCalls', 'tokens', 'configuredCostUsd'):
            candidates = [value[name][field][dimension] for name in ('pool', 'member')
                          if value[name]['limits'][dimension] is not None]
            amount = min((Decimal(item) for item in candidates), default=None) if all(item is not None for item in candidates) else None
            effective[field][dimension] = (format(amount, 'f') if dimension == 'configuredCostUsd' else int(amount)) if amount is not None else None
    effective['protectedRemaining'] = {}
    for dimension in ('modelCalls', 'tokens', 'configuredCostUsd'):
        remaining, ordinary = effective['remaining'][dimension], effective['ordinaryRemaining'][dimension]
        amount = max(Decimal(0), Decimal(remaining) - Decimal(ordinary)) if remaining is not None and ordinary is not None else None
        effective['protectedRemaining'][dimension] = (format(amount, 'f') if dimension == 'configuredCostUsd' else int(amount)) if amount is not None else None
    effective['configuredCostBounded'] = any(value[name]['limits']['configuredCostUsd'] is not None for name in ('pool', 'member'))
    result = {**{key: value[key] for key in ('state', 'revision', 'period', 'member', 'observation', 'pacing')},
              'effectiveCapacity': effective, 'scope': 'owner_declared_installation',
              'limitation': 'Snapshot of enrolled organization execution and owner-agent chat admission capacity; not a reservation or verified provider quota. Shared contention may be inferred. No automatic refill or refund.'}
    if internal or (binding is not None and binding['home'] == binding['root']):
        result['pool'] = value['pool']
    return result


def shared_claim_wait(conn, request):
    """Avoid occupying workers for known shared waits; debit rechecks atomically."""
    from eidolon_cli.organization_pacing import OrganizationPacingDeferred, paced_request, _timestamp
    if not paced_request(request['type']):
        return None
    value = shared_projection(conn)
    if not value or value.get('state') != 'ready':
        return None
    pacing = value.get('pacing') or {}
    until = pacing.get('deferUntil')
    if until and _timestamp(until, 'deferUntil') > time.time():
        return OrganizationPacingDeferred('Shared organization allowance pacing: ' + pacing['reason'],
                                         _timestamp(until, 'deferUntil'))
    return None


def shared_claim_reason(conn, request):
    from eidolon_cli.organization_pacing import NON_MODEL_REQUEST_TYPES, _timestamp
    from eidolon_cli.organization_completion_reserve import COMPLETION_REQUEST_TYPES
    if request['type'] in NON_MODEL_REQUEST_TYPES:
        return None
    value = shared_projection(conn, internal=True)
    if not value:
        return None
    if value.get('state') == 'blocked':
        return value['reason']
    period = value['period']
    if not _timestamp(period['startsAt'], 'startsAt') <= time.time() < _timestamp(period['endsAt'], 'endsAt'):
        return 'Shared organization allowance period is inactive; owner configuration is required before another model call.'
    field = 'remaining' if request['type'] in COMPLETION_REQUEST_TYPES else 'ordinaryRemaining'
    for name in ('pool', 'member'):
        view = value[name]
        for dimension, remaining in view[field].items():
            if view['limits'][dimension] is not None and (remaining is None or Decimal(remaining) <= 0):
                return 'Shared organization allowance ' + name + ' ' + dimension + ' capacity is exhausted or unknown; no new model call is authorized.'
    return None


def shared_cost_enabled(budget):
    value = budget.get('sharedAllowance') or {}
    return bool((value.get('effectiveCapacity') or {}).get('configuredCostBounded'))
