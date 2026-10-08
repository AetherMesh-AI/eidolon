"""An owner's explicit scope decision is the only way to remove required checks."""
from tests.organization_package_helpers import claim_after_decomposition
import hashlib
import json
import sqlite3
from dataclasses import replace

import pytest

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_store import OrganizationStore
from tests.eidolon_cli.test_organization_acceptance import _decision, _integrate, _objective, _plan, _work_and_review


def test_owner_check_replacement_is_exact_atomic_durable_and_changes_acceptance(tmp_path):
    store = OrganizationStore(tmp_path / 'state.db', replace(OrganizationSettings(), max_replans=2))
    _objective(store, required_checks=['project_tests'])
    first = store.claim_next()
    store.fail(first, 'Project test execution is unavailable')
    assert store.resolve(first['id'], 'amend_scope', 'Prepare a recommendation.', idempotency_key='keep')
    original = store.snapshot()['objectives'][0]
    assert original['requiredChecks'] == ['project_tests']
    assert original['ownerResolutions'][0]['scopeAmendment']['choices']['requiredChecks'] is None
    _plan(store)
    _work_and_review(store)
    assert store.claim_next() is None
    accept = next(row for row in store.snapshot()['requests'] if row['type'] == 'request.project_test')
    assert accept['status'] == 'pending_intervention'
    with store._connect() as conn:
        with pytest.raises(ValueError, match='have not been executed'):
            store._verify_required_checks(conn, accept['objectiveId'], ['project_tests'])
    before = store.snapshot()['objectives'][0]
    amendment = dict(required_checks=[], acceptance_criteria=['One evidence-backed recommendation'], idempotency_key='drop')
    with store._write() as conn:
        conn.execute("CREATE TRIGGER fail_scope_audit BEFORE INSERT ON owner_scope_amendments BEGIN SELECT RAISE(ABORT,'audit write failed'); END")
    with pytest.raises(sqlite3.IntegrityError, match='audit write failed'):
        store.resolve(accept['id'], 'amend_scope', 'Document the recommendation only.', **amendment)
    assert store.snapshot()['objectives'][0] == before
    with store._write() as conn:
        conn.execute('DROP TRIGGER fail_scope_audit')
    assert store.resolve(accept['id'], 'amend_scope', 'Document the recommendation only.', **amendment)
    store = OrganizationStore(store.path, store.settings)
    final_scope = store.snapshot()['objectives'][0]
    record = final_scope['ownerResolutions'][-1]
    audit = record['scopeAmendment']
    assert audit['before'] == {'scope': before['description'], 'acceptanceCriteria': before['acceptance']['criteria'],
                                'requiredChecks': ['project_tests'], 'round': 1}
    assert audit['after'] == {'scope': final_scope['description'], 'acceptanceCriteria': amendment['acceptance_criteria'],
                               'requiredChecks': [], 'round': 2}
    assert audit['choices'] == {'requiredChecks': [], 'acceptanceCriteria': amendment['acceptance_criteria']}
    encoded = json.dumps({key: value for key, value in audit.items() if key != 'sha256'}, sort_keys=True, separators=(',', ':'))
    assert audit['sha256'] == hashlib.sha256(encoded.encode()).hexdigest()
    with store._connect() as conn:
        assert conn.execute('SELECT input_hash FROM owner_resolutions WHERE id=?', (record['id'],)).fetchone()[0] == audit['inputSha256']
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        with store._write() as conn:
            conn.execute("UPDATE owner_scope_amendments SET record='{}' WHERE resolution_id=?", (record['id'],))
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        with store._write() as conn:
            conn.execute('DELETE FROM owner_scope_amendments WHERE resolution_id=?', (record['id'],))
    assert store.resolution_recorded(accept['id'], 'amend_scope', 'Document the recommendation only.', **amendment)
    assert not store.resolve(accept['id'], 'amend_scope', 'Document the recommendation only.', **amendment)
    for changed in ({'required_checks': None}, {'required_checks': ['project_tests']}, {'acceptance_criteria': ['Different criterion']}):
        with pytest.raises(ValueError, match='different input'):
            store.resolve(accept['id'], 'amend_scope', 'Document the recommendation only.', **{**amendment, **changed})
    _plan(store)
    _work_and_review(store)
    last = _integrate(store)
    assert store.finish(last, _decision(store, last))
    accepted = OrganizationStore(store.path).snapshot()['objectives'][0]
    assert accepted['status'] == 'completed' and accepted['requiredChecks'] == []
    assert accepted['ownerResolutions'][-1] == record
    assert accepted['originalDescription'].startswith('Produce one coherent recommendation')


def test_amendment_validation_replan_and_model_results_cannot_silently_drop_checks(tmp_path):
    store = OrganizationStore(tmp_path / 'state.db', replace(OrganizationSettings(), max_replans=1))
    _objective(store, required_checks=['project_tests'])
    first = store.claim_next()
    store.fail(first, 'Owner decision needed')
    for action, fields in [
        ('request_replan', {'required_checks': []}),
        ('provide_input', {'acceptance_criteria': ['Different']}),
        ('amend_scope', {'required_checks': ['project_tests', 'project_tests']}),
        ('amend_scope', {'required_checks': ['invented_check']}),
        ('amend_scope', {'required_checks': {}}),
        ('amend_scope', {'required_checks': [['project_tests']]}),
        ('amend_scope', {'acceptance_criteria': []}),
        ('amend_scope', {'acceptance_criteria': ['x'] * 13}),
        ('amend_scope', {'acceptance_criteria': ['x' * 2001]}),
        ('amend_scope', {'acceptance_criteria': [' ']}),
    ]:
        with pytest.raises(ValueError):
            store.resolve(first['id'], action, 'Change scope', idempotency_key='invalid', **fields)
    for oversized in ('x' * 2001, 'x' * 12001):
        with pytest.raises(ValueError):
            store.resolve(first['id'], 'amend_scope', oversized, idempotency_key='long')
    assert store.snapshot()['objectives'][0]['ownerResolutions'] == []
    store.resolve(first['id'], 'request_replan', 'Try another bounded approach.', idempotency_key='replan')
    plan = claim_after_decomposition(store)
    assert store.context(plan)['objective']['requiredChecks'] == ['project_tests']
    store.finish(plan, {'requiredChecks': [], 'tasks': [{'title': 'Analyze', 'description': 'Evidence-backed result', 'type': 'work.analyze'}]})
    _work_and_review(store)
    assert store.claim_next() is None
    accept = next(row for row in store.snapshot()['requests'] if row['type'] == 'request.project_test')
    assert accept['status'] == 'pending_intervention'
    with store._connect() as conn:
        with pytest.raises(ValueError, match='have not been executed'):
            store._verify_required_checks(conn, accept['objectiveId'], ['project_tests'])
    with pytest.raises(ValueError, match='unavailable'):
        store.resolve(accept['id'], 'amend_scope', 'Only document', required_checks=[], idempotency_key='over-round')
    assert store.snapshot()['objectives'][0]['requiredChecks'] == ['project_tests']
