"""A shared ledger's durable grants fence obsolete runtime instances."""
from dataclasses import replace
import sqlite3

import pytest

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_store import OrganizationStore


def test_policy_change_fences_claims_and_stale_admission_but_owner_can_cancel(tmp_path):
    first = OrganizationStore(tmp_path / 'state.db')
    objective = first.create_objective('Prepare a brief', idempotency_key='once')
    claim = first.claim_next()
    changed = OrganizationStore(first.path, replace(first.settings, team='new-team'))
    assert not first.heartbeat(claim)
    assert not first.finish(claim, {'tasks': []})
    assert first.claim_next() is None
    with pytest.raises(ValueError, match='restart this runtime'):
        first.create_objective('New request', idempotency_key='stale')
    with pytest.raises(ValueError, match='restart this runtime'):
        first.retry(claim['id'], idempotency_key='stale-retry')
    snapshot = first.snapshot()
    assert snapshot['runtime']['state'] == 'policy_changed'
    assert not snapshot['runtime']['readFileEnabled']
    assert not snapshot['runtime']['workspaceApplyEnabled']
    # Cancellation is an owner control, not a grant to restart execution.
    assert first.cancel(objective['id'])
    assert changed.snapshot()['objectives'][0]['status'] == 'cancelled'


def test_read_only_reopen_preserves_current_roster_and_policy_generation(tmp_path):
    settings = OrganizationSettings.from_config({'organization': {
        'team': 'engineering', 'capabilities': ['work.edit'],
        'tool_grants': ['read_file', 'patch'], 'read_roots': [str(tmp_path)],
        'roster': [{'id': 'editor', 'name': 'Editor', 'team': 'engineering',
                    'capabilities': ['work.edit'], 'tool_grants': ['read_file', 'patch']}],
    }})
    store = OrganizationStore(tmp_path / 'state.db', settings)
    store.create_objective('Prepare an edit', idempotency_key='once')
    claim = store.claim_next()
    reopened = OrganizationStore(store.path)
    assert reopened.settings == settings
    assert reopened._policy_generation == store._policy_generation
    assert store.heartbeat(claim)
    assert not any(agent['id'].startswith('worker-') for agent in reopened.snapshot()['agents'])


def test_authority_aba_does_not_resurrect_old_runtime_or_lease(tmp_path):
    original = OrganizationStore(tmp_path / 'state.db')
    original.create_objective('Prepare a draft', idempotency_key='once')
    claim = original.claim_next()
    OrganizationStore(original.path, replace(original.settings, capabilities=()))
    restored = OrganizationStore(original.path, original.settings)
    assert restored._policy_generation > original._policy_generation
    assert not original.finish(claim, {'tasks': []})
    assert original.claim_next() is None
    assert restored.retry(claim['id'], idempotency_key='new-policy')
    retry = restored.claim_next()
    assert retry['token'] != claim['token']
    assert restored.heartbeat(retry)


def test_request_claim_requires_current_persisted_policy_stamp(tmp_path):
    store = OrganizationStore(tmp_path / 'state.db')
    store.create_objective('Prepare a draft', idempotency_key='once')
    with pytest.raises(sqlite3.IntegrityError, match='current organization policy'):
        with store._write() as conn:
            conn.execute("UPDATE requests SET status='running',token='unstamped' WHERE type='request.plan'")
    claim = store.claim_next()
    assert claim['token'] != 'unstamped' and store.heartbeat(claim)


def test_operational_slot_change_does_not_expand_grants_or_displace_owned_calls(tmp_path):
    store = OrganizationStore(tmp_path / 'state.db', replace(OrganizationSettings(), max_inflight=1))
    store.create_objective('Prepare a brief', idempotency_key='once')
    claim = store.claim_next()
    peer = OrganizationStore(store.path, replace(store.settings, max_inflight=2))
    assert peer._policy_generation == store._policy_generation
    assert store.heartbeat(claim)
    assert peer.settings.tool_grants == store.settings.tool_grants == ()
