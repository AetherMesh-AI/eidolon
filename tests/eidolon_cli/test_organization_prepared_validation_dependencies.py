"""Prepared acceptance cannot become a legacy historical dependency authority."""
import json

import pytest

from eidolon_cli.organization_store import OrganizationStore
from eidolon_cli.organization_objective_dependencies import (
    TABLE, ObjectiveDependencyError, accepted_dependency_context,
    add_objective_dependency, objective_dependencies_satisfied,
)
from eidolon_cli.organization_project_validation import canonical, digest
from eidolon_cli.organization_milestones import _objective
from tests.eidolon_cli.test_organization_prepared_validation_lifecycle import (
    running, owner, prepared, folder_profile, finish_host, review_result, finish_acceptance,  # noqa: F401
)

pytestmark = pytest.mark.linux_only


def test_prepared_dependency_rejects_creation_and_retained_link_after_reopen(running, monkeypatch):
    store = running.store
    _calls, review = finish_host(running, monkeypatch)
    assert store.finish(review, review_result(running, review))
    finish_acceptance(store)
    successor = store.create_objective('Downstream fixture', 'Use historical evidence only.',
        idempotency_key='prepared-successor', project_ids=['notes'], delivery_mode='managed_artifact',
        acceptance_criteria=[{'id': 'summary', 'kind': 'deliverable', 'text': 'Explain the evidence.'}])['id']
    with store._write() as conn:
        owner_id = conn.execute("SELECT identity_id FROM agent_identity WHERE agent_id='owner'").fetchone()[0]
        manager = conn.execute('SELECT manager_id FROM objective_assignments WHERE objective_id=?', (successor,)).fetchone()[0]
        scope = _objective(conn, successor)[2]
        with pytest.raises(ObjectiveDependencyError, match='predecessor_not_accepted'):
            add_objective_dependency(conn, running.objective, successor,
                predecessor_receipt_sha256='a' * 64, predecessor_receipt={}, successor_scope_sha256=scope,
                owner_identity_id=owner_id, manager_id=manager)
        assert conn.execute(f'SELECT count(*) FROM {TABLE}').fetchone()[0] == 0
        # Model a retained legacy/invalid pin without forging a successful receipt.
        # Claim and context must recompute eligibility, never trust this record.
        record = {'ownerIdentityId': owner_id,
                  'predecessor': {'objectiveId': running.objective},
                  'successor': {'objectiveId': successor},
                  'authority': {'leaders': {'manager': {'id': manager}}}}
        conn.execute(f'INSERT INTO {TABLE} VALUES (?,?,?,?)',
                     (running.objective, successor, canonical(record), digest(record)))
    reopened = OrganizationStore(store.path, store.settings)
    with reopened._connect() as conn:
        assert not objective_dependencies_satisfied(conn, successor)
        with pytest.raises(ObjectiveDependencyError):
            accepted_dependency_context(conn, successor)
        before = conn.execute('SELECT attempts FROM requests WHERE objective_id=?', (successor,)).fetchall()
        assert all(row[0] == 0 for row in before)
    claim = reopened.claim_next(recoverable=True)
    assert claim is None or claim['objective_id'] != successor
    with reopened._connect() as conn:
        assert all(row[0] == 0 for row in conn.execute('SELECT attempts FROM requests WHERE objective_id=?', (successor,)))
        assert json.loads(conn.execute(f'SELECT record FROM {TABLE}').fetchone()[0]) == record
