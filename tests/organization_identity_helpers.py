"""Shared real-ledger setup and identity/context queries for organization identity tests."""
from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_identity import agent_context_view, agent_identity_view
from tests.organization_package_helpers import claim_after_decomposition


def _memory(store, agent='worker-1'):
    with store._connect() as conn:
        return agent_context_view(conn, agent)


def _identity(store, agent='worker-1'):
    with store._connect() as conn:
        return agent_identity_view(conn, agent)


def _work_claim(store, key, **task_fields):
    obj = store.create_objective('Prepare ' + key, idempotency_key=key)
    plan = claim_after_decomposition(store)
    assert plan['type'] == 'request.plan'
    store.finish(plan, {'tasks': [{'title': key, 'description': 'Use the supplied source material.',
                                 'type': 'work.draft', **task_fields}], 'workers': 2})
    claim = store.claim_next()
    while claim and claim['type'] == 'request.hire':
        assert store.finish(claim, {})
        claim = store.claim_next()
    assert claim['type'] == 'work.draft'
    return obj, claim


def _complete(store):
    while claim := store.claim_next():
        context = store.context(claim)
        evidence = [item['id'] for item in context['evidence']]
        results = {
            'request.hire': {},
            'request.review': {'approved': True, 'summary': 'The scoped source material is covered.', 'evidenceIds': evidence},
            'request.integrate': {'summary': 'Integrated source-grounded outcome.', 'deliverable': 'The complete integrated analysis.'},
            'request.accept': {'approved': True, 'summary': 'Integrated outcome meets all criteria.',
                               'evidenceIds': evidence, 'conflicts': [], 'criteriaResults': [
                                   {'criterion': criterion, 'satisfied': True, 'evidenceIds': evidence,
                                    'reason': 'Covered in the exact reviewed output.'}
                                   for criterion in context['objective']['acceptanceCriteria']]},
        }
        assert store.finish(claim, results[claim['type']])
    assert all(obj['status'] == 'completed' for obj in store.snapshot()['objectives'])


def _domain_settings():
    return OrganizationSettings.from_config({'organization': {'roster': [
        {'id': 'engineering', 'name': 'Engineering manager', 'role': 'Manager', 'team': 'engineering'},
        {'id': 'research', 'name': 'Research manager', 'role': 'Manager', 'team': 'research'},
        {'id': 'engineer', 'name': 'Engineer', 'team': 'engineering', 'manager_id': 'engineering',
         'capabilities': ['work.draft'], 'responsibilities': ['Develop source-grounded proposals.'],
         'purpose': 'Turn engineering requirements into concrete proposals.'},
        {'id': 'researcher', 'name': 'Researcher', 'team': 'research', 'manager_id': 'research',
         'capabilities': ['work.draft']},
    ]}})
