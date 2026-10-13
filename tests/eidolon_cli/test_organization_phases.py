"""Stable display phase codes from real ledger transitions, without model calls."""
import sqlite3

import pytest

from eidolon_cli.organization_store import OrganizationStore
from tests.eidolon_cli.test_organization_acceptance import _decision, _integrate, _objective, _work_and_review
from tests.organization_package_helpers import decompose


def test_phase_codes_follow_intervention_resume_and_durable_acceptance(tmp_path):
    store = OrganizationStore(tmp_path / 'state.db')
    objective = _objective(store)

    def phase(code, text):
        record = store.snapshot()['objectives'][0]
        assert record['id'] == objective['id']
        assert (record['phaseCode'], record['phase']) == (code, text)
        return record

    phase('executive_planning', 'Executive decomposition and manager planning')
    decompose(store)
    plan = store.claim_next()
    store.finish(plan, {'intervention': 'Which facts should be prioritized?'})
    phase('intervention', 'Pending intervention')
    store.resolve(plan['id'], 'provide_input', 'Prefer lower operational risk.', idempotency_key='answer')
    phase('executive_planning', 'Executive decomposition and manager planning')
    claim = store.claim_next()
    store.finish(claim, {'tasks': [{'title': 'Analyze', 'description': 'Support the recommendation', 'type': 'work.analyze'}]})
    phase('execution_review', 'Execution and review')
    _work_and_review(store)
    record = phase('integration_review', 'Integrated outcome acceptance')
    assert record['progress'] == 100 and record['status'] != 'completed'
    accept = _integrate(store)
    phase('integration_review', 'Integrated outcome acceptance')
    store.finish(accept, _decision(store, accept))
    store = OrganizationStore(store.path)
    record = phase('accepted', 'Accepted integrated outcome')
    assert record['acceptance']['status'] == 'accepted'
    assert record['acceptance']['deliverableId']


@pytest.mark.parametrize('status,expected,text', [
    ('planning', 'legacy_planning', 'Legacy manager planning'),
    ('legacy_completed', 'legacy_reviewed', 'Legacy reviewed outcome'),
])
def test_legacy_phase_codes_preserve_retained_phase_text(tmp_path, status, expected, text):
    store = OrganizationStore(tmp_path / 'state.db')
    objective = _objective(store)
    # Seed the retained legacy shape; migration behavior is covered separately.
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE objective_planning SET mode='legacy' WHERE objective_id=?", (objective['id'],))
        conn.execute('UPDATE objective_control SET status=? WHERE objective_id=?', (status, objective['id']))
    record = store.snapshot()['objectives'][0]
    assert record['phaseCode'] == expected
    assert record['phase'] == text
    assert record['acceptance']['deliverableId'] is None
