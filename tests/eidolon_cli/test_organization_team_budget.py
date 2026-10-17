"""Setup policy adoption is an audited CAS, never a reset of physical charges."""
from contextlib import contextmanager
from dataclasses import asdict
import json
import sqlite3
from types import SimpleNamespace

import pytest

from eidolon_cli import organization_metadata as metadata
from eidolon_cli import organization_owner_policy as owner
from eidolon_cli import organization_team_budget as budget
from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_policy import _fingerprint, persisted_settings
from eidolon_cli.organization_store import ORGANIZATION_SCHEMA
from eidolon_cli.organization_team_setup import _fresh


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """Real SQL/schema and source reader, with only storage kept in memory."""
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    (tmp_path / 'config.yaml').write_text('{}')
    conn = sqlite3.connect(':memory:', isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.executescript(ORGANIZATION_SCHEMA)
    identity = metadata._save_in_transaction(conn,
        {'displayName': 'Budget fixture', 'ownerDisplayName': 'Owner',
         'expectedOrganizationId': None, 'expectedRevision': None}, 'metadata', 'initial')['metadata']
    settings = OrganizationSettings()
    values = asdict(settings)
    conn.execute('INSERT INTO organization_policy VALUES (1,1,?,?)',
                 (_fingerprint(values), json.dumps(values, sort_keys=True)))

    @contextmanager
    def connection(home, *, write=False):
        assert home == tmp_path
        conn.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
        try:
            yield conn
        except BaseException:
            conn.rollback()
            raise
        else:
            conn.commit() if write else conn.rollback()

    monkeypatch.setattr(budget, '_connection', connection)
    monkeypatch.setattr(owner, '_refresh_existing', lambda home: None)
    yield SimpleNamespace(conn=conn, home=tmp_path, identity=identity, settings=settings)
    conn.close()


def state(ledger):
    return budget.budget_policy(ledger.home, ledger.identity['organizationId'])


def candidate(ledger, **changes):
    snapshot = state(ledger)
    return {'expectedOrganizationId': snapshot['organizationId'],
            'expectedGeneration': snapshot['policyGeneration'], 'sourceRevision': snapshot['sourceRevision'],
            'configuration': {**budget.SUGGESTED_POLICY, **changes}}


def reviewed(ledger, key='activate', **changes):
    params = candidate(ledger, **changes)
    preview = budget.preview_budget(ledger.home, params)
    return {**params, 'previewVersion': preview['previewVersion'], 'confirmed': True, 'idempotencyKey': key}


def dump(ledger):
    return list(ledger.conn.iterdump())


def generation(ledger, key, *, status='failed', dispatched=1, tokens=200):
    ledger.conn.execute('INSERT INTO organization_team_generations '
        '(idempotency_key,organization_id,input_hash,source_revision,status,dispatched,reserved_tokens,created) '
        'VALUES (?,?,?,?,?,?,?,0)',
        (key, ledger.identity['organizationId'], 'hash:' + key, 'source', status, dispatched, tokens))


def test_explicit_activation_retains_legacy_serialization_and_visible_audit_with_exact_replay(ledger):
    before = dump(ledger)
    settings_json = ledger.conn.execute('SELECT settings,fingerprint,generation FROM organization_policy').fetchone()
    first = state(ledger)
    assert first['configuration'] is None
    assert first['setupBudget']['remaining']['totalCalls'] == min(3, ledger.settings.max_model_calls)
    apply = reviewed(ledger)
    assert dump(ledger) == before
    assert not budget._exists(ledger.conn, budget._TABLE)
    # A suggested preset or YAML entry cannot silently opt into a new allowance.
    (ledger.home / 'config.yaml').write_text(json.dumps({'organization': {'setup_budget': budget.SUGGESTED_POLICY}}))
    assert state(ledger)['configuration'] is None
    assert tuple(ledger.conn.execute('SELECT settings,fingerprint,generation FROM organization_policy').fetchone()) == tuple(settings_json)
    (ledger.home / 'config.yaml').write_text('{}')
    result = budget.apply_budget(ledger.home, apply)
    assert result['receipt']['policyGeneration'] == first['policyGeneration'] + 1
    assert _fresh(ledger.conn, ledger.settings)
    assert persisted_settings(ledger.conn) == ledger.settings
    assert ledger.conn.execute('SELECT settings,fingerprint FROM organization_policy').fetchone()[:] == settings_json[:2]
    assert ledger.conn.execute("SELECT count(*) FROM events WHERE kind='configuration' AND agent_id='owner'").fetchone()[0] == 1
    assert state(ledger)['latestReceipt'] == result['receipt']
    assert ledger.conn.execute('SELECT count(*) FROM agent_history').fetchone()[0] == 0
    budget.apply_budget(ledger.home, reviewed(ledger, key='later', totalCallLimit=8))
    (ledger.home / 'config.yaml').write_text('organization: [invalid')
    saved = dump(ledger)
    assert budget.apply_budget(ledger.home, apply) == result
    assert dump(ledger) == saved
    with pytest.raises(owner.OwnerPolicyError, match='idempotency_conflict'):
        budget.apply_budget(ledger.home, {**apply, 'configuration': {**apply['configuration'], 'totalCallLimit': 9}})


def test_policy_input_and_cas_reject_unreviewed_changes_without_writes(ledger, monkeypatch):
    for change in ({'version': True}, {'mode': 'unlimited'}, {'improveCallLimit': True},
                   {'generationCallLimit': -1}, {'totalCallLimit': 1001}, {'totalCallLimit': 2}):
        with pytest.raises(ValueError):
            budget.validate_policy({**budget.SUGGESTED_POLICY, **change})
    assert budget.validate_policy({**budget.SUGGESTED_POLICY, 'improveCallLimit': 0,
                                  'generationCallLimit': 0, 'totalCallLimit': 0})['totalCallLimit'] == 0
    apply = reviewed(ledger)
    before = dump(ledger)
    for change, reason in (({'expectedGeneration': 999}, 'generation_conflict'),
                           ({'sourceRevision': '0' * 64}, 'source_conflict'),
                           ({'previewVersion': '0' * 64}, 'preview_conflict')):
        with pytest.raises(owner.OwnerPolicyError, match=reason):
            budget.apply_budget(ledger.home, {**apply, **change})
        assert dump(ledger) == before
    with pytest.raises(ValueError, match='confirm'):
        budget.apply_budget(ledger.home, {**apply, 'confirmed': False})
    with monkeypatch.context() as scoped:
        scoped.setattr(budget, 'managed_fields', lambda: {'setup_budget'})
        with pytest.raises(owner.OwnerPolicyError, match='managed_configuration'):
            budget.apply_budget(ledger.home, apply)
    assert dump(ledger) == before
    generation(ledger, 'new-charge')
    with pytest.raises(owner.OwnerPolicyError, match='source_conflict'):
        budget.apply_budget(ledger.home, apply)
    refreshed = reviewed(ledger)
    budget.apply_budget(ledger.home, refreshed)
    with pytest.raises(owner.OwnerPolicyError, match='generation_conflict'):
        budget.apply_budget(ledger.home, {**refreshed, 'idempotencyKey': 'concurrent-review'})


def test_pending_uncertain_and_unadopted_sources_block_but_history_is_not_hidden(ledger):
    for status in ('running', 'uncertain'):
        generation(ledger, status, status=status, dispatched=0)
        with pytest.raises(owner.OwnerPolicyError, match='team_generation_in_progress_or_uncertain'):
            budget.preview_budget(ledger.home, candidate(ledger))
        ledger.conn.execute('DELETE FROM organization_team_generations WHERE idempotency_key=?', (status,))
    (ledger.home / 'config.yaml').write_text('{"organization":{"max_workers":3}}')
    with pytest.raises(owner.OwnerPolicyError, match='source_policy_conflict'):
        budget.preview_budget(ledger.home, candidate(ledger))
    (ledger.home / 'config.yaml').write_text('{}')
    ledger.conn.execute('INSERT INTO organization_configuration VALUES (1,?,0)',
                        (json.dumps({'roster': [], 'max_inflight': 2, 'max_members': 16}),))
    with pytest.raises(owner.OwnerPolicyError, match='organization_not_empty'):
        budget.preview_budget(ledger.home, candidate(ledger))


def test_failed_audit_commit_rolls_back_override_schema_generation_and_receipt(ledger):
    generation(ledger, 'retained')
    apply = reviewed(ledger)
    ledger.conn.execute("CREATE TRIGGER fail_setup_audit BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT,'audit_failure'); END")
    before = dump(ledger)
    with pytest.raises(sqlite3.IntegrityError, match='audit_failure'):
        budget.apply_budget(ledger.home, apply)
    assert dump(ledger) == before
    assert not budget._exists(ledger.conn, budget._TABLE)
    ledger.conn.execute('DROP TRIGGER fail_setup_audit')
    budget.apply_budget(ledger.home, apply)
    assert budget.operation_counts(ledger.conn)['legacy'] == 1


def test_legacy_mapping_is_complete_immutable_and_missing_unknown_or_orphan_history_blocks(ledger):
    generation(ledger, 'old-send')
    generation(ledger, 'old-preflight', dispatched=0, tokens=0)
    budget.apply_budget(ledger.home, reviewed(ledger))
    assert budget.operation_counts(ledger.conn) == {'legacy': 1, 'generateTeam': 0, 'improveTeamInput': 0, 'total': 1}
    assert ledger.conn.execute('SELECT count(*) FROM ' + budget._TABLE).fetchone()[0] == 2
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        ledger.conn.execute('DELETE FROM ' + budget._TABLE)
    for corruption in ('missing', 'wrong-org', 'wrong-hash', 'future-generation', 'unknown-op', 'orphan'):
        ledger.conn.execute('BEGIN')
        try:
            ledger.conn.execute('DROP TRIGGER organization_team_generation_operations_immutable_update')
            ledger.conn.execute('DROP TRIGGER organization_team_generation_operations_immutable_delete')
            # Restore triggers after mutation, so content validation is exercised.
            if corruption == 'missing':
                ledger.conn.execute('DELETE FROM ' + budget._TABLE + " WHERE idempotency_key='old-send'")
            elif corruption == 'orphan':
                ledger.conn.execute('PRAGMA defer_foreign_keys=ON')
                ledger.conn.execute('INSERT INTO ' + budget._TABLE + ' VALUES (?,?,?,?,?)',
                                    ('orphan', ledger.identity['organizationId'], 'hash', 'legacy', 1))
            else:
                fields = {'wrong-org': ('organization_id', 'other'), 'wrong-hash': ('input_hash', 'other'),
                          'future-generation': ('policy_generation', 999), 'unknown-op': ('operation', 'unknown')}
                field, value = fields[corruption]
                if corruption == 'unknown-op':
                    ledger.conn.execute('PRAGMA ignore_check_constraints=ON')
                ledger.conn.execute('UPDATE ' + budget._TABLE + ' SET ' + field + '=?', (value,))
            for sql in budget.OPERATION_DDL[1:]:
                ledger.conn.execute(sql)
            with pytest.raises(owner.OwnerPolicyError, match='setup_budget_corrupt'):
                budget.read_policy(ledger.conn)
        finally:
            ledger.conn.rollback()
            ledger.conn.execute('PRAGMA ignore_check_constraints=OFF')
    generation(ledger, 'new', status='running', dispatched=0, tokens=0)
    with pytest.raises(owner.OwnerPolicyError, match='setup_budget_corrupt'):
        budget.read_policy(ledger.conn)
    current = ledger.conn.execute('SELECT generation FROM organization_policy').fetchone()[0]
    budget.record_operation(ledger.conn, 'new', ledger.identity['organizationId'], 'improveTeamInput', current)
    assert budget.operation_for(ledger.conn, 'new') == 'improveTeamInput'
    ledger.conn.execute("UPDATE organization_team_generations SET status='failed',dispatched=1,reserved_tokens=300 WHERE idempotency_key='new'")
    view = budget.budget_view(ledger.conn, ledger.settings)
    assert view['usage'] == {'legacy': 1, 'generateTeam': 0, 'improveTeamInput': 1, 'total': 2}
    assert view['reservedTokens'] == 500
    assert view['remaining']['improveCalls'] == min(view['limits']['improveCalls'] - 1, view['remaining']['totalCalls'])


def test_active_policy_requires_its_immutable_owner_receipt(ledger):
    budget.apply_budget(ledger.home, reviewed(ledger))
    for corruption in ('missing', 'changed-policy', 'wrong-owner', 'future-generation'):
        ledger.conn.execute('BEGIN')
        try:
            if corruption == 'changed-policy':
                saved = {'setup_budget': {**budget.SUGGESTED_POLICY, 'totalCallLimit': 8}}
                ledger.conn.execute('UPDATE organization_owner_budget_policy SET configuration=?', (json.dumps(saved),))
            else:
                ledger.conn.execute('DROP TRIGGER organization_owner_policy_receipts_immutable_update')
                ledger.conn.execute('DROP TRIGGER organization_owner_policy_receipts_immutable_delete')
                if corruption == 'missing':
                    ledger.conn.execute('DELETE FROM organization_owner_policy_receipts')
                elif corruption == 'wrong-owner':
                    ledger.conn.execute("UPDATE organization_owner_policy_receipts SET owner_id='other'")
                else:
                    raw = json.loads(ledger.conn.execute('SELECT result_json FROM organization_owner_policy_receipts').fetchone()[0])
                    raw['receipt']['policyGeneration'] = 999
                    ledger.conn.execute('UPDATE organization_owner_policy_receipts SET result_json=?', (json.dumps(raw),))
                for sql in budget.OWNER_POLICY_DDL[2:]:
                    ledger.conn.execute(sql)
            with pytest.raises(owner.OwnerPolicyError, match='setup_budget_corrupt'):
                budget.read_policy(ledger.conn)
        finally:
            ledger.conn.rollback()
    assert budget.read_policy(ledger.conn) == budget.SUGGESTED_POLICY
