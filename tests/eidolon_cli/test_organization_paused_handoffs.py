"""Reorganizations preserve the exact worker that owns an unfinished conversation."""
import pytest

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_store import OrganizationStore


def _member(ident, **values):
    return {'id': ident, 'name': ident.title(), 'team': 'red',
            'capabilities': ['work.draft'], **values}


def _paused_work(tmp_path, *, pinned):
    roster = [
        _member('old-manager', role='Manager', capabilities=['request.plan', 'request.integrate']),
        _member('new-manager', role='Manager', capabilities=['request.plan', 'request.integrate']),
        _member('staffer', role='Manager', capabilities=['request.hire'], authority=['staff.manage']),
        _member('source', manager_id='old-manager'),
    ]
    settings = OrganizationSettings.from_config({'organization': {'roster': roster}})
    store = OrganizationStore(tmp_path / 'organization.db', settings)
    store.create_objective('Retain a worker conversation', idempotency_key='work',
                           manager_id='old-manager')
    plan = store.claim_next()
    task = {'title': 'Draft', 'description': 'Ask who will read this brief.', 'type': 'work.draft',
            'team': 'red', 'managerId': 'old-manager'}
    if pinned:
        task['agentId'] = 'source'
    assert store.finish(plan, {'tasks': [task]})
    hire = store.claim_next()
    assert hire['type'] == 'request.hire'
    assert store.finish(hire, {})
    paused = store.claim_next()
    assert paused['agent_id'] == 'source'
    assert store.finish(paused, {'requests': [
        {'type': 'request.question', 'requestedOutcome': 'Who is the audience?'}]})
    assert store.claim_next() is None
    snapshot = store.snapshot()
    question = next(row for row in snapshot['requests'] if row['parentRequestId'] == paused['id'])
    assert question['status'] == 'pending_intervention'
    identity = next(agent['identityId'] for agent in snapshot['agents'] if agent['id'] == 'source')
    return store, identity, paused, question


def _change(store, proposal, *, agent_mode, key):
    if not agent_mode:
        roster = {member['id']: member for member in store.configuration_snapshot()['roster']}
        roster.update({member['id']: member for member in proposal.get('members', [])})
        config = {'roster': list(roster.values()), 'transfers': proposal.get('transfers', [])}
        return None, lambda: store.configure_organization(
            config, expected_generation=store._policy_generation, idempotency_key=key)
    objective = store.create_objective('Apply the scoped reorganization', idempotency_key=key)
    plan = store.claim_next()
    assert plan['objective_id'] == objective['id'] and plan['type'] == 'request.plan'
    assert store.finish(plan, {'requests': [
        {'type': 'request.hire', 'team': 'red', 'requestedOutcome': 'Apply the exact staffing proposal.',
         'managementProposal': proposal}]})
    hire = store.claim_next()
    assert hire['type'] == 'request.hire' and hire['agent_id'] == 'staffer'
    return objective, lambda: store.finish(hire, {})


def _ledger(store):
    with store._connect() as conn:
        return list(conn.iterdump())


def _assert_resumes(store, identity, paused, question, worker, manager):
    store = OrganizationStore(store.path)
    assert store.respond(question['id'], 'The engineering team.', idempotency_key='answer')
    resumed = store.claim_next()
    assert resumed['id'] == paused['id'] and resumed['agent_id'] == worker
    context = store.context(resumed)
    assert context['task']['managingAgentId'] == manager
    assert context['requestResponses'][0]['requesterId'] == 'source'
    assert context['requestResponses'][0]['response']['text'] == 'The engineering team.'
    assert not store.finish(paused, {'summary': 'Stale worker turn', 'deliverable': 'Must not commit.'})
    assert store.finish(resumed, {'summary': 'Scoped brief', 'deliverable': 'The engineering brief.'})
    after = next(agent for agent in store.snapshot()['agents'] if agent['id'] == 'source')
    assert after['identityId'] == identity
    with store._connect() as conn:
        assert conn.execute('SELECT agent_id FROM agent_history WHERE request_id=?', (resumed['id'],)).fetchone()[0] == worker
        assert conn.execute('SELECT agent_id FROM request_continuations WHERE request_id=?', (paused['id'],)).fetchone()[0] == worker


@pytest.mark.parametrize('pinned', [False, True])
@pytest.mark.parametrize('agent_mode', [False, True])
def test_manager_handoff_requires_paused_worker_to_retain_a_valid_assignment(tmp_path, pinned, agent_mode):
    store, identity, paused, question = _paused_work(tmp_path, pinned=pinned)
    transfer = {'fromAgentId': 'old-manager', 'toAgentId': 'new-manager', 'taskIds': [paused['task_id']]}
    management, save = _change(store, {'transfers': [transfer]}, agent_mode=agent_mode, key='invalid')
    before, configuration, generation = _ledger(store), store.configuration_snapshot(), store._policy_generation
    with pytest.raises(ValueError, match='paused worker'):
        save()
    assert _ledger(store) == before
    assert store.configuration_snapshot() == configuration and store._policy_generation == generation
    if management:
        assert store.cancel(management['id'])
    # Keeping the exact worker and changing its reporting line in the same
    # transaction preserves both explicit task pins and implicit continuations.
    worker = next(member for member in configuration['roster'] if member['id'] == 'source')
    proposal = {'members': [{**worker, 'manager_id': 'new-manager'}], 'transfers': [transfer]}
    management, save = _change(store, proposal, agent_mode=agent_mode, key='valid')
    assert save()
    if management:
        assert store.cancel(management['id'])
    _assert_resumes(store, identity, paused, question, 'source', 'new-manager')


@pytest.mark.parametrize('change', [
    {'manager_id': 'new-manager'}, {'team': 'blue'}, {'capabilities': []}, {'enabled': False},
])
def test_unpinned_paused_worker_edits_require_an_explicit_continuation_transfer(tmp_path, change):
    store, identity, paused, question = _paused_work(tmp_path, pinned=False)
    worker = next(member for member in store.configuration_snapshot()['roster'] if member['id'] == 'source')
    proposal = {'members': [{**worker, **change}]}
    _, save = _change(store, proposal, agent_mode=False, key='invalid')
    before, configuration, generation = _ledger(store), store.configuration_snapshot(), store._policy_generation
    with pytest.raises(ValueError, match='paused worker'):
        save()
    assert _ledger(store) == before
    assert store.configuration_snapshot() == configuration and store._policy_generation == generation
    # The worker handoff itself selects the destination's manager. No parallel
    # manager transfer or implicit continuation reassignment is necessary.
    proposal['members'].append(_member('target', manager_id='new-manager'))
    proposal['transfers'] = [{'fromAgentId': 'source', 'toAgentId': 'target',
                              'taskIds': [paused['task_id']], 'includeMemory': True}]
    _, save = _change(store, proposal, agent_mode=False, key='valid')
    assert save()
    with store._connect() as conn:
        assert conn.execute('SELECT agent_id FROM task_assignments WHERE task_id=?', (paused['task_id'],)).fetchone()[0] == 'target'
    _assert_resumes(store, identity, paused, question, 'target', 'new-manager')
