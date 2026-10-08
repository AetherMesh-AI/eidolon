"""Shared real-ledger fixtures and lifecycle setup for organization outcome tests."""
import pytest

from eidolon_cli.organization_store import OrganizationStore
from tests.organization_package_helpers import claim_after_decomposition


@pytest.fixture
def store(tmp_path):
    return OrganizationStore(tmp_path / 'organization' / 'state.db')


def accepted(store, key='accepted'):
    objective = store.create_objective('A supported decision', idempotency_key=key, delivery_mode='managed_artifact')
    claim = claim_after_decomposition(store)
    assert claim['type'] == 'request.plan'
    store.finish(claim, {'tasks': [{'title': 'Analyze', 'description': 'Compare options', 'type': 'work.analyze'}]})
    claim = store.claim_next()
    store.finish(claim, {'summary': 'Compared options', 'deliverable': 'Option A has the strongest support.'})
    claim = store.claim_next()
    ids = [item['id'] for item in store.context(claim)['evidence']]
    store.finish(claim, {'approved': True, 'summary': 'Supported task result', 'evidenceIds': ids})
    assert store.outcomes()['items'] == []
    claim = store.claim_next()
    assert claim['type'] == 'request.integrate'
    store.finish(claim, {'summary': 'Combined decision', 'deliverable': 'Choose A based on the supplied evidence.'})
    claim = store.claim_next()
    context = store.context(claim)
    ids = [item['id'] for item in context['evidence']]
    result = {'approved': True, 'summary': 'Complete objective accepted', 'evidenceIds': ids, 'conflicts': [],
              'criteriaResults': [{'criterion': criterion, 'satisfied': True, 'reason': 'Supported by exact evidence',
                                   'evidenceIds': ids} for criterion in context['objective']['acceptanceCriteria']]}
    store.finish(claim, result)
    return objective, claim, ids


def cancelled(store, key='cancelled'):
    objective = store.create_objective('Cancelled work', idempotency_key=key)
    store.cancel(objective['id'])
    return objective


def item(store):
    return store.outcomes()['items'][0]
