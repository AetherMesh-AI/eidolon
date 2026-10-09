"""Objective handoffs preserve independent peer-question continuations."""
from eidolon_cli.organization_store import OrganizationStore
from tests.organization_package_helpers import claim_after_decomposition
from tests.organization_management_helpers import configure, member, settings
from tests.organization_question_helpers import legacy_question_creation  # noqa: F401


def test_objective_handoff_preserves_unselected_peer_question_continuation(tmp_path, legacy_question_creation):
    roster = [member('team-lead', role='Manager',
                     capabilities=['request.plan', 'request.integrate', 'request.question'],
                     authority=['answer.question']),
              member('new-lead', role='Manager', capabilities=['request.plan', 'request.integrate']),
              member('source', manager_id='team-lead')]
    store = OrganizationStore(tmp_path / 'organization.db', settings(*roster))
    goal = store.create_objective('Keep distinct responsibilities', idempotency_key='goal', manager_id='team-lead')
    assert store.finish(claim_after_decomposition(store), {'tasks': [
        {'title': 'Draft', 'description': 'Needs a peer answer', 'team': 'red', 'type': 'work.draft', 'agentId': 'source'}]})
    assert store.finish(store.claim_next(), {})  # Activate the configured worker.
    work = store.claim_next()
    assert store.finish(work, {'requests': [{'type': 'request.question', 'requestedOutcome': 'Which audience?'}]})
    question = store.claim_next()
    assert question['agent_id'] == 'team-lead'
    assert store.finish(question, {'requests': [{'type': 'request.question', 'team': 'blue',
                                                'requestedOutcome': 'Which engineering audience?'}]})
    assert store.claim_next() is None
    nested = next(row for row in store.snapshot()['requests'] if row['parentRequestId'] == question['id'])
    configure(store, transfers=[{'fromAgentId': 'team-lead', 'toAgentId': 'new-lead',
                                 'objectiveIds': [goal['id']]}])
    store = OrganizationStore(store.path)
    assert store.respond(nested['id'], 'Engineering leads', idempotency_key='peer-answer')
    resumed = store.claim_next()
    assert resumed['id'] == question['id'] and resumed['agent_id'] == 'team-lead'
    assert store.finish(resumed, {'answer': 'Engineering leads', 'decision': 'answered'})
    resumed_work = store.claim_next()
    assert resumed_work['id'] == work['id'] and resumed_work['agent_id'] == 'source'
    with store._connect() as conn:
        assert conn.execute('SELECT manager_id FROM task_assignments WHERE task_id=?', (work['task_id'],)).fetchone()[0] == 'team-lead'
        assert conn.execute('SELECT manager_id FROM objective_assignments WHERE objective_id=?', (goal['id'],)).fetchone()[0] == 'new-lead'
