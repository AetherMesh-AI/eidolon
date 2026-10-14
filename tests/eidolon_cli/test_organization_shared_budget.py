"""Local reservation commits never erase or replay an earlier shared debit."""
from dataclasses import replace
import hashlib
import json
import os
import sqlite3
import sys
from types import ModuleType

import pytest

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_store import OrganizationStore
from eidolon_cli.organization_shared_budget import configured_call_cost
from eidolon_cli.organization_budget import OrganizationModelCost


@pytest.fixture
def shared(monkeypatch):
    module = ModuleType('eidolon_cli.organization_shared_allowance')
    rows = {}
    module.SharedAllowanceError = ValueError
    module.read_member_projection = lambda *args, **kwargs: {'state': 'unconfigured'}
    module.enabled = True
    module.bad_verify = False
    module.resolve_binding = lambda home, organization_id, **kwargs: {'poolId': 'fixture-pool',
        'root': str(home), 'home': str(home), 'ledgerId': 'fixture-ledger',
        'organizationId': organization_id or 'fixture-organization', 'ownerId': 'fixture-owner'} if module.enabled else None
    module.physical_call_key = lambda binding, payload: hashlib.sha256(json.dumps({
        key: payload.get(key) for key in ('kind', 'objectiveId', 'requestId', 'attemptToken', 'callIndex', 'turnId')
    }, sort_keys=True).encode()).hexdigest()

    def debit(binding, key, payload):
        if key in rows:
            raise ValueError('already_debited')
        receipt = {'callKey': key, 'poolId': binding['poolId'], 'payload': dict(payload)}
        rows[key] = receipt
        return receipt

    def verify(binding, receipt, key, payload):
        if module.bad_verify or rows.get(key) != receipt or receipt['payload'] != payload:
            raise ValueError('receipt_mismatch')
        return receipt

    module.debit, module.verify_receipt = debit, verify
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return module, rows


def setup(tmp_path, **limits):
    store = OrganizationStore(tmp_path / 'organization' / 'state.db', replace(OrganizationSettings(), **limits))
    objective = store.create_objective('Bounded shared work', idempotency_key='shared-work')
    claim = store.claim_next()
    assert claim is not None
    return store, objective, claim


def reserve(store, claim):
    return store.reserve_model_call(claim, provider='fixture', model='fixture', input_limit=100, output_limit=100)


def local_rows(store, name):
    with store._connect() as conn:
        return [dict(row) for row in conn.execute('SELECT * FROM ' + name)]


def test_disabled_shared_allowance_preserves_local_reservation(tmp_path, shared):
    shared[0].enabled = False
    store, _, claim = setup(tmp_path)
    reserve(store, claim)
    assert len(local_rows(store, 'organization_model_calls')) == 1
    assert local_rows(store, 'organization_shared_call_receipts') == []
    assert shared[1] == {}


def test_objective_call_records_exact_shared_receipt_in_local_transaction(tmp_path, shared):
    store, objective, claim = setup(tmp_path)
    ident = reserve(store, claim)
    receipt = local_rows(store, 'organization_shared_call_receipts')[0]
    payload = json.loads(receipt['receipt_json'])['payload']
    assert payload == {'kind': 'objective', 'objectiveId': objective['id'], 'requestId': claim['id'],
        'attemptToken': claim['token'], 'callIndex': 1, 'requestType': claim['type'],
        'provider': 'fixture', 'model': 'fixture', 'inputLimit': 100, 'outputLimit': 100, 'reservedCostUsd': None}
    assert receipt['model_call_id'] == ident and receipt['owner_chat_turn_id'] is None
    with store._write() as conn:
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            conn.execute('DELETE FROM organization_shared_call_receipts')


def test_global_debit_survives_local_rollback_and_cannot_authorize_retry(tmp_path, shared):
    store, _, claim = setup(tmp_path)
    with store._write() as conn:
        conn.execute("CREATE TRIGGER reject_local_call BEFORE INSERT ON organization_model_calls BEGIN SELECT RAISE(ABORT,'local_commit_failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match='local_commit_failure'):
        reserve(store, claim)
    assert len(shared[1]) == 1
    assert local_rows(store, 'organization_model_calls') == []
    assert local_rows(store, 'organization_shared_call_receipts') == []
    with store._write() as conn:
        conn.execute('DROP TRIGGER reject_local_call')
    with pytest.raises(ValueError, match='already_debited'):
        reserve(store, claim)
    assert len(shared[1]) == 1 and local_rows(store, 'organization_model_calls') == []


def test_local_cap_refusal_precedes_any_shared_debit(tmp_path, shared):
    store, _, claim = setup(tmp_path, max_model_calls=1)
    reserve(store, claim)
    with pytest.raises(ValueError, match='budget exhausted'):
        reserve(store, claim)
    assert len(shared[1]) == 1


def test_unverified_global_receipt_blocks_local_commit(tmp_path, shared):
    store, _, claim = setup(tmp_path)
    shared[0].bad_verify = True
    with pytest.raises(ValueError, match='receipt_mismatch'):
        reserve(store, claim)
    assert len(shared[1]) == 1 and local_rows(store, 'organization_model_calls') == []


def test_owner_agent_chat_debits_same_scope_and_retains_single_send_guard(tmp_path, shared):
    store, _, _ = setup(tmp_path)
    identity = next(a['identityId'] for a in store.snapshot()['agents'] if a['id'] == 'manager')
    thread = store.owner_chat_open('manager', identity)
    turn, _ = store.owner_chat_reserve(thread, identity, 'Hello', None, 'owner-send')
    store.owner_chat_context(turn)
    store.owner_chat_dispatch(turn, provider='custom', selected_provider='fixture', model='fixture', input_limit=100, output_limit=100)
    receipt = local_rows(store, 'organization_shared_call_receipts')[0]
    payload = json.loads(receipt['receipt_json'])['payload']
    assert payload['kind'] == 'owner_chat' and payload['provider'] == 'fixture'
    assert payload['threadId'] == thread and payload['turnId'] == turn and payload['identityId'] == identity
    assert receipt['model_call_id'] is None and receipt['owner_chat_turn_id'] == turn
    with pytest.raises(ValueError, match='exactly one'):
        store.owner_chat_dispatch(turn, provider='custom', selected_provider='fixture', model='fixture', input_limit=100, output_limit=100)
    assert len(shared[1]) == 1


def test_cost_uses_exact_owner_route_and_rounds_up(tmp_path):
    settings = replace(OrganizationSettings(), model_costs=(OrganizationModelCost('named', 'm', '0.000001', '0.000001'),))
    assert configured_call_cost(settings, 'named', 'm', 1, 1) == '0.000000001'
    assert configured_call_cost(settings, 'custom', 'm', 1, 1) is None


def test_passive_schema_accepts_absent_new_audit_table_without_recreating_it(tmp_path):
    from eidolon_cli.organization_observation import _compatible_schema
    store, _, _ = setup(tmp_path)
    with store._write() as conn:
        conn.execute('DROP TABLE organization_shared_call_receipts')
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert _compatible_schema(conn, tables)
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='organization_shared_call_receipts'").fetchone()


def test_existing_scope_pin_blocks_changed_ledger_identity(tmp_path, shared):
    store, _, claim = setup(tmp_path)
    reserve(store, claim)
    original = shared[0].resolve_binding
    shared[0].resolve_binding = lambda *args, **kwargs: {**original(*args, **kwargs), 'ledgerId': 'different-ledger'}
    with pytest.raises(ValueError, match='ledger identity changed'):
        reserve(store, claim)
    assert len(shared[1]) == len(local_rows(store, 'organization_model_calls')) == 1


@pytest.mark.skipif(os.name == 'nt', reason='Symlink replacement fixture requires POSIX filesystem semantics')
def test_canonical_store_ancestor_retarget_cannot_erase_anchor(tmp_path, shared):
    home = tmp_path / 'original'
    home.mkdir()
    store, _, claim = setup(home)
    moved = tmp_path / 'moved'
    home.rename(moved)
    home.symlink_to(moved, target_is_directory=True)
    with pytest.raises(ValueError, match='canonical path changed'):
        reserve(store, claim)
    assert shared[1] == {}
    assert local_rows(store, 'organization_model_calls') == []


@pytest.mark.skipif(os.name == 'nt', reason='Symlink replacement fixture requires POSIX filesystem semantics')
def test_ordinary_alias_retarget_blocks_shared_scope(tmp_path, shared):
    first, second = tmp_path / 'first', tmp_path / 'second'
    first.mkdir(); second.mkdir()
    original, _, _ = setup(first)
    destination, _, claim = setup(second)
    alias = tmp_path / 'alias'
    alias.symlink_to(first, target_is_directory=True)
    store = OrganizationStore(alias / 'organization' / 'state.db', original.settings)
    alias.unlink(); alias.symlink_to(second, target_is_directory=True)
    with pytest.raises(ValueError, match='store path changed'):
        reserve(store, claim)
    assert shared[1] == {}
    assert local_rows(destination, 'organization_model_calls') == []
