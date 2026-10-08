"""Real-ledger membership edits, explicit authority and retained transfer provenance."""
from dataclasses import replace
import json
import time

import pytest

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_roster import configured_staff
from eidolon_cli.organization_store import OrganizationStore
from tests.organization_package_helpers import claim_after_decomposition


def member(ident, **values):
    return {'id': ident, 'name': ident.title(), 'team': 'red', 'capabilities': ['work.draft'], **values}


def settings(*members, **values):
    return OrganizationSettings.from_config({'organization': {'roster': list(members), **values}})


def configure(store, **values):
    return store.configure_organization(values, expected_generation=store._policy_generation,
                                        idempotency_key=str(time.time_ns()))


def staffing_store(tmp_path, *, authority=True, managed_teams=(), **values):
    leader = member('staffer', role='Manager', capabilities=['request.hire'],
                    authority=['staff.manage'] if authority else [], managed_teams=list(managed_teams),
                    responsibilities=['Own staffing everywhere; this text confers no authority.'])
    return OrganizationStore(tmp_path / 'organization.db', settings(leader, member('source'), **values))


def hire_request(store, proposal, actor='staffer', team='red', owner=False):
    objective = store.create_objective('Scoped organization work', idempotency_key=str(time.time_ns()),
                                       delivery_mode='managed_artifact')
    with store._write() as conn:
        request_id = store._request(conn, objective['id'], 'request.hire', team, 3,
                                    payload={'managementProposal': proposal})
        store._stamp_claim_policy(conn, request_id)
        conn.execute('UPDATE requests SET status=?,agent_id=?,token=?,lease=? WHERE id=?',
                     ('pending_intervention' if owner else 'running', actor, 'test-token', time.time() + 60, request_id))
        return dict(conn.execute('SELECT * FROM requests WHERE id=?', (request_id,)).fetchone())


def apply(store, request, proposal, actor='staffer'):
    with store._write() as conn:
        return store.apply_management(conn, request, proposal, actor)


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


def open_task(store, source='source'):
    objective = store.create_objective('Transfer scoped work', idempotency_key=str(time.time_ns()))
    claim = claim_after_decomposition(store)
    assert store.finish(claim, {'tasks': [{'title': 'Draft', 'description': 'Write this', 'team': 'red',
                                         'type': 'work.draft', 'agentId': source}], 'workers': 1})
    task_id = next(task['id'] for task in store.snapshot()['tasks'] if task['objectiveId'] == objective['id'])
    with store._write() as conn:
        conn.execute("UPDATE agent_context SET memory=?,revision=3 WHERE agent_id=?",
                     (json.dumps({'facts': ['Scoped source fact'], 'decisions': [], 'lessons': [], 'openQuestions': []}), source))
        request = conn.execute("SELECT * FROM requests WHERE task_id=? AND type='work.draft'", (task_id,)).fetchone()
        conn.execute('INSERT INTO request_continuations VALUES (?,?)', (request['id'], source))
        conn.execute("UPDATE requests SET status='waiting_response',agent_id=? WHERE id=?", (source, request['id']))
        historical = store._request(conn, objective['id'], 'work.draft', 'red', 3, task_id)
        conn.execute("UPDATE requests SET status='completed',agent_id=? WHERE id=?", (source, historical))
        conn.execute('INSERT INTO agent_history VALUES (?,?,?,?,?,?,?,?)',
                     (historical, source, objective['id'], task_id, 'work.draft', 'Retained original history', '[]', time.time()))
    return task_id, request['id'], historical


def test_explicit_transfer_preserves_identity_authorship_and_bounded_context_with_audit(tmp_path):
    store = OrganizationStore(tmp_path / 'organization.db', settings(member('source')))
    task, request, historical = open_task(store)
    with store._connect() as conn:
        identity = conn.execute("SELECT identity_id FROM agent_identity WHERE agent_id='source'").fetchone()[0]
    result = configure(store, roster=[member('target')], transfers=[{
        'fromAgentId': 'source', 'toAgentId': 'target', 'taskIds': [task], 'includeMemory': True}])
    with store._connect() as conn:
        assert conn.execute('SELECT agent_id FROM task_assignments WHERE task_id=?', (task,)).fetchone()[0] == 'target'
        assert conn.execute('SELECT agent_id FROM request_continuations WHERE request_id=?', (request,)).fetchone()[0] == 'target'
        assert conn.execute('SELECT agent_id FROM agent_history WHERE request_id=?', (historical,)).fetchone()[0] == 'source'
        assert conn.execute("SELECT identity_id FROM agent_identity WHERE agent_id='source'").fetchone()[0] == identity
        source = conn.execute("SELECT memory FROM agent_context WHERE agent_id='source'").fetchone()[0]
        target = conn.execute("SELECT memory FROM agent_context WHERE agent_id='target'").fetchone()[0]
        assert json.loads(source)['facts'] == json.loads(target)['facts'] == ['Scoped source fact']
        audit = conn.execute("SELECT * FROM organization_management_audit WHERE kind='transfer'").fetchone()
        assert json.loads(audit['after_state'])['taskIds'] == [task]
        assert 'Scoped source fact' not in audit['after_state']
    assert result['configuration']['roster'][0]['id'] == 'target'


def test_failed_transfer_rolls_back_members_identity_memory_and_policy(tmp_path):
    store = OrganizationStore(tmp_path / 'organization.db', settings(member('source')))
    task, _, _ = open_task(store)
    before, generation = store.configuration_snapshot(), store._policy_generation
    with pytest.raises(ValueError, match='existing open'):
        configure(store, roster=[member('target')], transfers=[{
            'fromAgentId': 'source', 'toAgentId': 'target', 'taskIds': [task, 'missing'], 'includeMemory': True}])
    assert store.configuration_snapshot() == before
    assert store._policy_generation == generation
    with store._connect() as conn:
        assert not conn.execute("SELECT 1 FROM agents WHERE id='target'").fetchone()
        assert conn.execute('SELECT agent_id FROM task_assignments WHERE task_id=?', (task,)).fetchone()[0] == 'source'
        assert not conn.execute('SELECT 1 FROM organization_configuration').fetchone()


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


def leadership_goal(tmp_path):
    roster = [member('lead-one', role='Executive', capabilities=['request.accept']),
              member('lead-two', role='Executive', capabilities=['request.accept']),
              member('team-lead', role='Manager', manager_id='lead-one',
                     capabilities=['request.plan', 'request.integrate']),
              member('source', manager_id='team-lead')]
    store = OrganizationStore(tmp_path / 'organization.db', settings(*roster))
    objective = store.create_objective('Explicit leadership handoff', idempotency_key='goal',
                                       executive_id='lead-one', manager_id='team-lead')
    plan = store.claim_next()
    store.finish(plan, {'tasks': [{'title': str(index), 'description': 'Scoped draft', 'type': 'work.draft',
                                  'team': 'red', 'agentId': 'source', 'managerId': 'team-lead'} for index in range(2)],
                        'workers': 1})
    tasks = [task['id'] for task in store.snapshot()['tasks']]
    return store, roster, objective['id'], tasks, plan['id']


def test_manager_handoff_moves_exact_open_tasks_and_objective_but_preserves_old_authors(tmp_path):
    store, roster, objective, tasks, plan = leadership_goal(tmp_path)
    updated = [entry for entry in roster if entry['id'] != 'team-lead']
    updated = [{**entry, 'manager_id': 'new-lead'} if entry['id'] == 'source' else entry for entry in updated]
    updated.append(member('new-lead', role='Manager', manager_id='lead-one',
                          capabilities=['request.plan', 'request.integrate']))
    result = configure(store, roster=updated, transfers=[{
        'fromAgentId': 'team-lead', 'toAgentId': 'new-lead', 'taskIds': tasks, 'includeMemory': True}])
    with store._connect() as conn:
        assignment = conn.execute('SELECT * FROM objective_assignments WHERE objective_id=?', (objective,)).fetchone()
        assert assignment['manager_id'] == 'new-lead' and assignment['executive_id'] == 'lead-one'
        assert {row[0] for row in conn.execute('SELECT manager_id FROM task_assignments')} == {'new-lead'}
        assert conn.execute('SELECT agent_id FROM requests WHERE id=?', (plan,)).fetchone()[0] == 'team-lead'
    assert any(change['kind'] == 'transfer' for change in result['recentChanges'])


def test_executive_handoff_requires_every_open_task_and_preserves_manager_identity(tmp_path):
    store, roster, objective, tasks, _ = leadership_goal(tmp_path)
    updated = [{**entry, 'manager_id': 'lead-two'} if entry['id'] == 'team-lead' else entry
               for entry in roster if entry['id'] != 'lead-one']
    with pytest.raises(ValueError, match='every open task'):
        configure(store, roster=updated, transfers=[{
            'fromAgentId': 'lead-one', 'toAgentId': 'lead-two', 'taskIds': tasks[:1], 'includeMemory': False}])
    configure(store, roster=updated, transfers=[{
        'fromAgentId': 'lead-one', 'toAgentId': 'lead-two', 'taskIds': tasks, 'includeMemory': False}])
    with store._connect() as conn:
        assert conn.execute('SELECT executive_id FROM objective_assignments WHERE objective_id=?', (objective,)).fetchone()[0] == 'lead-two'
        assert {row[0] for row in conn.execute('SELECT manager_id FROM task_assignments')} == {'team-lead'}


def test_live_objective_leadership_cannot_be_disabled_without_handoff(tmp_path):
    store, roster, _, _, _ = leadership_goal(tmp_path)
    with pytest.raises(ValueError, match='open-objective'):
        configure(store, roster=[{**entry, 'enabled': False} if entry['id'] == 'lead-one' else entry for entry in roster])
    assert next(staff for staff in store.settings.roster if staff.id == 'lead-one').enabled


def test_configuration_replay_is_detectable_while_new_work_is_running(tmp_path):
    store = OrganizationStore(tmp_path / 'organization.db')
    generation = store._policy_generation
    result = store.configure_organization({'max_inflight': 1}, expected_generation=generation, idempotency_key='saved')
    store.create_objective('After save', idempotency_key='goal')
    claim = store.claim_next()
    assert store.configuration_recorded({'max_inflight': 1}, expected_generation=generation, idempotency_key='saved')
    assert store.configure_organization({'max_inflight': 1}, expected_generation=generation, idempotency_key='saved') == result
    assert store.heartbeat(claim)


def test_staffing_cannot_route_a_new_member_to_an_unmanaged_team_leader(tmp_path):
    store = staffing_store(tmp_path)
    configure(store, roster=[*store.configuration_snapshot()['roster'],
                              member('blue-lead', role='Manager', team='blue', capabilities=['request.plan'])])
    proposal = {'members': [member('new-red', manager_id='blue-lead')]}
    request = hire_request(store, proposal)
    with pytest.raises(ValueError, match='reporting lines'):
        apply(store, request, proposal)
    assert not any(staff.id == 'new-red' for staff in configured_staff(store.settings))


def test_default_logical_members_obey_explicit_member_limit():
    configuration = OrganizationSettings.from_config({'organization': {'max_members': 1}})
    assert len(configured_staff(configuration)) == 1


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


def paused_unpinned_work(tmp_path):
    store = OrganizationStore(tmp_path / 'organization.db', settings(member('source', team='general')))
    objective = store.create_objective('Unpinned work and follow-up', idempotency_key='unpinned')
    plan = claim_after_decomposition(store)
    assert store.finish(plan, {'tasks': [
        {'title': 'Prior work', 'description': 'Write the first brief', 'type': 'work.draft'},
        {'title': 'Follow-up', 'description': 'Ask for the audience and continue', 'type': 'work.draft', 'dependsOn': [0]},
    ]})
    hire = store.claim_next()
    assert hire['type'] == 'request.hire'
    assert store.finish(hire, {})
    first = store.claim_next()
    assert first['agent_id'] == 'source'
    assert store.finish(first, {'summary': 'Original authored work', 'deliverable': 'Original source result.',
                                'memory': {'facts': ['Retain the first brief.']}})
    review = store.claim_next()
    assert review['type'] == 'request.review'
    assert store.finish(review, {'approved': True, 'summary': 'Checked the first brief',
                                 'evidenceIds': [item['id'] for item in store.context(review)['evidence']]})
    paused = store.claim_next()
    assert paused['agent_id'] == 'source' and paused['task_id'] != first['task_id']
    assert store.finish(paused, {'requests': [{'type': 'request.question', 'requestedOutcome': 'Who is the audience?'}]})
    assert store.claim_next() is None
    question = next(row for row in store.snapshot()['requests'] if row['parentRequestId'] == paused['id'])
    assert question['status'] == 'pending_intervention'
    with store._connect() as conn:
        assert conn.execute('SELECT agent_id FROM task_assignments WHERE task_id=?', (paused['task_id'],)).fetchone()[0] is None
        assert conn.execute('SELECT agent_id FROM request_continuations WHERE request_id=?', (paused['id'],)).fetchone()[0] == 'source'
    return store, objective, first, paused, question


def test_unpinned_paused_continuation_transfers_then_answer_resumes_target_with_source_history(tmp_path):
    store, _, first, paused, question = paused_unpinned_work(tmp_path)
    before = next(agent for agent in store.snapshot()['agents'] if agent['id'] == 'source')
    original_roster = store.configuration_snapshot()['roster']
    configure(store, roster=[*original_roster, member('target', team='general')], transfers=[{
        'fromAgentId': 'source', 'toAgentId': 'target', 'taskIds': [paused['task_id']], 'includeMemory': True}])
    with store._connect() as conn:
        assert conn.execute('SELECT agent_id FROM task_assignments WHERE task_id=?', (paused['task_id'],)).fetchone()[0] == 'target'
        assert conn.execute('SELECT agent_id FROM request_continuations WHERE request_id=?', (paused['id'],)).fetchone()[0] == 'target'
        assert conn.execute('SELECT requester_id FROM request_contracts WHERE request_id=?', (question['id'],)).fetchone()[0] == 'source'
    store = OrganizationStore(store.path)
    assert store.respond(question['id'], 'The engineering team.', idempotency_key='transfer-answer')
    resumed = store.claim_next()
    assert resumed['id'] == paused['id'] and resumed['task_id'] == paused['task_id'] and resumed['agent_id'] == 'target'
    assert not store.finish(paused, {'summary': 'Stale source result', 'deliverable': 'Must not commit.'})
    context = store.context(resumed)
    assert context['requestResponses'][0]['requesterId'] == 'source'
    assert context['requestResponses'][0]['response']['text'] == 'The engineering team.'
    assert store.finish(resumed, {'summary': 'Transferred follow-up', 'deliverable': 'The engineering follow-up.'})
    source = next(agent for agent in store.snapshot()['agents'] if agent['id'] == 'source')
    assert source['identityId'] == before['identityId']
    assert source['context']['recentHistory'] == before['context']['recentHistory']
    with store._connect() as conn:
        assert conn.execute('SELECT agent_id FROM agent_history WHERE request_id=?', (first['id'],)).fetchone()[0] == 'source'
        assert conn.execute('SELECT agent_id FROM agent_history WHERE request_id=?', (resumed['id'],)).fetchone()[0] == 'target'


def test_unpinned_paused_transfer_rejects_unrelated_source_and_preserves_continuation(tmp_path):
    store, _, _, paused, _ = paused_unpinned_work(tmp_path)
    configure(store, roster=[*store.configuration_snapshot()['roster'], member('unrelated', team='general')])
    before, generation = store.configuration_snapshot(), store._policy_generation
    with pytest.raises(ValueError, match='paused continuation'):
        configure(store, roster=[*before['roster'], member('target', team='general')], transfers=[{
            'fromAgentId': 'unrelated', 'toAgentId': 'target', 'taskIds': [paused['task_id']], 'includeMemory': False}])
    assert store.configuration_snapshot() == before and store._policy_generation == generation
    with store._connect() as conn:
        assert conn.execute('SELECT agent_id FROM task_assignments WHERE task_id=?', (paused['task_id'],)).fetchone()[0] is None
        assert conn.execute('SELECT agent_id FROM request_continuations WHERE request_id=?', (paused['id'],)).fetchone()[0] == 'source'
        assert not conn.execute("SELECT 1 FROM agents WHERE id='target'").fetchone()


def test_unpinned_unclaimed_work_cannot_be_transferred_using_an_arbitrary_source(tmp_path):
    store = OrganizationStore(tmp_path / 'organization.db', settings(member('source', team='general')))
    store.create_objective('Unclaimed work', idempotency_key='unclaimed')
    assert store.finish(claim_after_decomposition(store), {'tasks': [
        {'title': 'Draft', 'description': 'Not yet owned by a worker', 'type': 'work.draft'}]})
    task = store.snapshot()['tasks'][0]
    with pytest.raises(ValueError, match='paused continuation'):
        configure(store, roster=[*store.configuration_snapshot()['roster'], member('target', team='general')], transfers=[{
            'fromAgentId': 'source', 'toAgentId': 'target', 'taskIds': [task['id']], 'includeMemory': False}])
    with store._connect() as conn:
        assert not conn.execute('SELECT 1 FROM request_continuations').fetchone()
        assert conn.execute('SELECT agent_id FROM task_assignments WHERE task_id=?', (task['id'],)).fetchone()[0] is None
