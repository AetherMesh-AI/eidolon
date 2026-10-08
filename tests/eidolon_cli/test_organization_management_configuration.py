"""Real-ledger organization configuration invariants."""
from dataclasses import replace
import pytest
from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_roster import configured_staff
from eidolon_cli.organization_store import OrganizationStore
from tests.organization_management_helpers import (
    configure, member, settings,
)


def test_configuration_persists_exact_roster_across_normal_seed_restart_and_is_idempotent(tmp_path):
    seed = settings(member('source'))
    store = OrganizationStore(tmp_path / 'organization.db', seed)
    original = next(agent for agent in store.snapshot()['agents'] if agent['id'] == 'source')
    generation = store._policy_generation
    changed = member('source', name='Retained specialist', responsibilities=['Accountable for red work'],
                     scope='Red documentation', authority=['answer.question'],
                     capabilities=['work.draft', 'request.question'])
    config = {'roster': [changed, member('extra')], 'max_members': 20, 'max_inflight': 1}
    result = store.configure_organization(config, expected_generation=generation, idempotency_key='save')
    assert result['generation'] > generation
    assert store.configure_organization(config, expected_generation=generation, idempotency_key='save') == result
    with pytest.raises(ValueError, match='idempotency'):
        store.configure_organization({**config, 'max_inflight': 2}, expected_generation=generation, idempotency_key='save')
    with pytest.raises(ValueError, match='reload'):
        store.configure_organization(config, expected_generation=generation, idempotency_key='new-save')
    reopened = OrganizationStore(store.path, seed)
    assert reopened.configuration_snapshot() == result['configuration']
    after = next(agent for agent in reopened.snapshot()['agents'] if agent['id'] == 'source')
    assert after['identityId'] == original['identityId']
    assert after['name'] == 'Retained specialist'
    with reopened._connect() as conn:
        assert conn.execute('SELECT count(*) FROM organization_management_receipts').fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM organization_management_audit').fetchone()[0] == 3


@pytest.mark.parametrize('change', [
    {'tool_grants': ['terminal']}, {'read_roots': ['/']}, {'capabilities': ['work.edit']},
    {'max_members': True}, {'max_members': 0}, {'max_members': 65}, {'max_inflight': 5},
    {'roster': [member('bad', role='Director')]}, {'roster': [member('bad', api_key='secret')]},
])
def test_configuration_rejects_security_settings_and_invalid_limits_atomically(tmp_path, change):
    store = OrganizationStore(tmp_path / 'organization.db')
    before, generation = store.configuration_snapshot(), store._policy_generation
    with pytest.raises(ValueError):
        configure(store, **change)
    assert store.configuration_snapshot() == before
    assert store._policy_generation == generation


def test_configuration_rejects_running_calls_and_keeps_the_lease(tmp_path):
    store = OrganizationStore(tmp_path / 'organization.db')
    store.create_objective('Plan this', idempotency_key='goal')
    claim = store.claim_next()
    with pytest.raises(ValueError, match='running'):
        configure(store, max_inflight=1)
    assert store.heartbeat(claim)


def test_configuration_replay_is_detectable_while_new_work_is_running(tmp_path):
    store = OrganizationStore(tmp_path / 'organization.db')
    generation = store._policy_generation
    result = store.configure_organization({'max_inflight': 1}, expected_generation=generation, idempotency_key='saved')
    store.create_objective('After save', idempotency_key='goal')
    claim = store.claim_next()
    assert store.configuration_recorded({'max_inflight': 1}, expected_generation=generation, idempotency_key='saved')
    assert store.configure_organization({'max_inflight': 1}, expected_generation=generation, idempotency_key='saved') == result
    assert store.heartbeat(claim)


def test_saved_membership_cannot_restore_revoked_startup_tool_grants(tmp_path):
    seed = settings(member('reader', capabilities=['work.inspect'], tool_grants=['read_file']),
                    capabilities=['work.inspect'], tool_grants=['read_file'], read_roots=[str(tmp_path)])
    store = OrganizationStore(tmp_path / 'organization.db', seed)
    configure(store, max_inflight=1)
    reopened = OrganizationStore(store.path, replace(seed, tool_grants=()))
    assert reopened.settings.tool_grants == ()
    assert reopened.settings.roster[0].tool_grants == ('read_file',)
    with reopened._connect() as conn:
        assert 'outside' in reopened._staff_reason(conn, {'id': 'reader'}, 'work.inspect')


def test_legacy_large_seed_roster_gets_a_compatible_bound_but_explicit_limit_is_enforced():
    roster = [member(f'person-{index}') for index in range(20)]
    assert settings(*roster).max_members == 20
    with pytest.raises(ValueError, match='max_members'):
        settings(*roster, max_members=16)


def test_default_logical_members_obey_explicit_member_limit():
    configuration = OrganizationSettings.from_config({'organization': {'max_members': 1}})
    assert len(configured_staff(configuration)) == 1
