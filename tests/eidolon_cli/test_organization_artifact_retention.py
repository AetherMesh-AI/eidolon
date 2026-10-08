"""Large retained-artifact invariant, isolated from the shorter ledger contracts.

This keeps the canonical per-file timeout meaningful on slower native Windows
runners without reducing the history size, lifecycle operations or assertions.
"""
from eidolon_cli.organization_store import OrganizationStore
from tests.eidolon_cli.test_organization_store import objective, plan, work, review, accept_objective


def test_every_visible_task_retains_its_latest_artifact_reference(tmp_path):
    store = OrganizationStore(tmp_path / 'organization' / 'state.db')
    # More than the previous global artifact window, but still visible history.
    for number in range(9):
        objective(store, str(number))
        plan(store, [{'title': f'Section {n}', 'description': 'Produce text', 'type': 'work.draft', 'dependsOn': [n-1] if n else []} for n in range(12)])
        for _ in range(12):
            work(store, 'Full retained text: ' + 'x'*3000)
            review(store)
        accept_objective(store)
    snapshot = store.snapshot()
    assert len(snapshot['tasks']) == 108
    assert all(task['evidence'] for task in snapshot['tasks'])
    for task in snapshot['tasks']:
        proof = task['evidence'][0]
        assert proof['truncated']
        assert store.evidence(proof['id'])['content'].startswith(proof['content'])
