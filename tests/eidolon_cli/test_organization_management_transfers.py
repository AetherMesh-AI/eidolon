"""Real-ledger organization transfers invariants."""
import json
import pytest
from eidolon_cli.organization_store import OrganizationStore
from tests.organization_package_helpers import claim_after_decomposition
from tests.organization_management_helpers import (
    configure, member, open_task, paused_unpinned_work, settings,
)


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
