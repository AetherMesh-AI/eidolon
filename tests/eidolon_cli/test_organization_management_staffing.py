"""Real-ledger organization staffing invariants."""
import pytest
from eidolon_cli.organization_roster import configured_staff
from eidolon_cli.organization_store import OrganizationStore
from tests.organization_management_helpers import (
    apply, configure, hire_request, member, settings, staffing_store,
)


def test_management_has_separate_capacity_and_explicit_authority(tmp_path):
    store = staffing_store(tmp_path, max_workers=1, max_inflight=1)
    proposal = {'members': [member('new-worker'), member('new-lead', role='Manager', capabilities=['request.hire'],
                                                    authority=['staff.manage'])]}
    request = hire_request(store, proposal)
    result = apply(store, request, proposal)
    assert len(result['configuration']['roster']) == 4 > store.settings.max_inflight
    assert store.settings.max_workers == 1
    assert store.heartbeat(request)
    with store._connect() as conn:
        assert conn.execute("SELECT active FROM staff_state WHERE agent_id='new-worker'").fetchone()[0] == 1
        assert conn.execute('SELECT generation FROM request_policy WHERE request_id=?', (request['id'],)).fetchone()[0] == result['generation']
    assert apply(store, request, proposal) == result
    reopened = OrganizationStore(store.path, settings(member('old-seed')))
    assert {staff.id for staff in configured_staff(reopened.settings)} == {'staffer', 'source', 'new-worker', 'new-lead'}


@pytest.mark.parametrize('new_member', [
    member('outside', team='blue'),
    member('tools', tool_grants=['read_file']),
    member('provider', provider='never-configured', model='untrusted'),
    member('authority', authority=['answer.decision']),
    member('global', role='Manager', capabilities=['request.hire'], authority=['staff.manage'], managed_teams=['*']),
    member('edit', capabilities=['work.edit']),
])
def test_scoped_staffing_rejects_cross_team_and_automatic_authority_or_tool_expansion(tmp_path, new_member):
    store = staffing_store(tmp_path, tool_grants=['read_file'])
    proposal = {'members': [new_member]}
    request = hire_request(store, proposal)
    before = store.configuration_snapshot()
    with pytest.raises(ValueError):
        apply(store, request, proposal)
    assert store.configuration_snapshot() == before
    assert store.heartbeat(request)
    with store._connect() as conn:
        assert not conn.execute('SELECT 1 FROM organization_management_audit').fetchone()


def test_responsibility_text_is_not_authority_and_cross_team_needs_explicit_scope(tmp_path):
    store = staffing_store(tmp_path, authority=False)
    proposal = {'members': [member('new')]}
    request = hire_request(store, proposal)
    with pytest.raises(ValueError, match='explicit staff.manage'):
        apply(store, request, proposal)
    scoped = staffing_store(tmp_path / 'explicit', managed_teams=['*'])
    proposal = {'members': [member('blue', team='blue')]}
    request = hire_request(scoped, proposal, team='blue')
    apply(scoped, request, proposal)
    assert any(staff.id == 'blue' for staff in configured_staff(scoped.settings))


def test_owner_approves_only_exact_pending_proposal_and_cannot_change_global_policy(tmp_path):
    store = staffing_store(tmp_path)
    proposal = {'members': [member('blue', team='blue', authority=['answer.question'], capabilities=['request.question'])]}
    request = hire_request(store, proposal, owner=True)
    with pytest.raises(ValueError, match='exactly'):
        apply(store, request, {'members': [member('swapped')]}, 'owner')
    result = apply(store, request, proposal, 'owner')
    assert result['configuration']['roster'][-1]['id'] == 'blue'
    assert store.settings.tool_grants == ()
    with store._connect() as conn:
        rows = conn.execute('SELECT * FROM organization_management_audit').fetchall()
    assert rows and all(row['request_id'] == request['id'] and row['actor_id'] == 'owner' for row in rows)


def test_staffing_cannot_route_a_new_member_to_an_unmanaged_team_leader(tmp_path):
    store = staffing_store(tmp_path)
    configure(store, roster=[*store.configuration_snapshot()['roster'],
                              member('blue-lead', role='Manager', team='blue', capabilities=['request.plan'])])
    proposal = {'members': [member('new-red', manager_id='blue-lead')]}
    request = hire_request(store, proposal)
    with pytest.raises(ValueError, match='reporting lines'):
        apply(store, request, proposal)
    assert not any(staff.id == 'new-red' for staff in configured_staff(store.settings))


def test_staffing_cannot_reuse_a_retired_cross_team_identity_to_acquire_its_memory(tmp_path):
    store = staffing_store(tmp_path)
    original = store.configuration_snapshot()['roster']
    configure(store, roster=[*original, member('retired-blue', team='blue')])
    configure(store, roster=original)
    proposal = {'members': [member('retired-blue', team='red')]}
    request = hire_request(store, proposal)
    with pytest.raises(ValueError, match='retained identity'):
        apply(store, request, proposal)
    with store._connect() as conn:
        assert conn.execute("SELECT team FROM agents WHERE id='retired-blue'").fetchone()[0] == 'blue'
        assert conn.execute("SELECT source FROM staff_state WHERE agent_id='retired-blue'").fetchone()[0] == 'retired'
