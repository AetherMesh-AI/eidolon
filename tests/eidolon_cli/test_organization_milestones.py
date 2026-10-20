"""Milestone planning persistence does not launch or finance work."""
import json
import sqlite3

import pytest

from eidolon_cli.organization_store import OrganizationStore
from eidolon_cli.organization_milestones import milestone_projection
from tests.eidolon_cli.test_organization_projects import configured


def fixture(tmp_path):
    settings = configured(tmp_path)
    store = OrganizationStore(tmp_path / 'state.db', settings=settings)
    first = store.create_objective('First', idempotency_key='first', project_ids=['alpha'])['id']
    second = store.create_objective('Second', idempotency_key='second', project_ids=['alpha', 'beta'])['id']
    return store, first, second


def save(store, ident, objectives=(), dependencies=(), projects=('alpha',), rev=0, key=None):
    return store.revise_milestone(ident, title='Milestone ' + ident, project_ids=list(projects),
        objective_ids=list(objectives), dependency_ids=list(dependencies), expected_revision=rev,
        idempotency_key=key or f'{ident}-{rev}')


def accept(store, objective):
    with store._write() as conn:
        conn.execute("UPDATE objective_control SET status='accepted' WHERE objective_id=?", (objective,))
        conn.execute("UPDATE requests SET status='cancelled' WHERE objective_id=?", (objective,))


def test_revision_replay_restart_and_no_work_or_budget_mutation(tmp_path):
    store, first, second = fixture(tmp_path)
    with store._connect() as conn:
        baseline = {table: [tuple(r) for r in conn.execute('SELECT * FROM '+table)]
                    for table in ('requests', 'objective_budgets', 'objective_usage')}
    receipt = save(store, 'release', [first, second], projects=['alpha', 'beta'])
    assert save(store, 'release', [first, second], projects=['alpha', 'beta']) == receipt
    reopened = OrganizationStore(store.path, settings=store.settings)
    view, = reopened.milestones()
    assert view['revision'] == 1 and view['status'] == 'in_progress'
    assert view['automaticContinuation'] is False
    assert len(view['projects']) == 2 and len(view['objectives']) == 2
    with store._connect() as conn:
        assert baseline == {table: [tuple(r) for r in conn.execute('SELECT * FROM '+table)] for table in baseline}
    with pytest.raises(ValueError, match='stale'):
        save(store, 'release', [first], key='new-key')
    with pytest.raises(ValueError, match='different input'):
        save(store, 'release', [first])


def test_dependencies_and_objective_scope_changes_invalidate_completion(tmp_path):
    store, first, second = fixture(tmp_path)
    save(store, 'first', [first])
    save(store, 'release', [second], ['first'], ['alpha', 'beta'])
    assert store.milestones()[1]['status'] == 'blocked'
    accept(store, first); accept(store, second)
    assert all(m['status'] == 'complete' for m in store.milestones())
    save(store, 'first', [first], rev=1)
    release = next(m for m in store.milestones() if m['id'] == 'release')
    assert release['status'] == 'needs_attention'
    assert release['reasons'] == ['dependency_revision_changed']
    save(store, 'release', [second], ['first'], ['alpha', 'beta'], rev=1)
    assert all(m['status'] == 'complete' for m in store.milestones())
    with store._write() as conn:
        conn.execute("UPDATE objective_control SET criteria=? WHERE objective_id=?", (json.dumps(['Changed requirement']), first))
    assert store.milestones()[0]['status'] == 'needs_attention'
    assert 'objective_scope_changed' in store.milestones()[0]['reasons']


def test_cycle_and_outside_project_links_fail_atomically(tmp_path):
    store, first, second = fixture(tmp_path)
    save(store, 'one', [first]); save(store, 'two', [first], ['one'])
    with pytest.raises(ValueError, match='cycles'):
        save(store, 'one', [first], ['two'], rev=1)
    with pytest.raises(ValueError, match='scope'):
        save(store, 'outside', [second])
    with pytest.raises(ValueError, match='already exist'):
        save(store, 'unknown', [first], ['missing'])
    assert [(m['id'], m['revision']) for m in store.milestones()] == [('one', 1), ('two', 1)]


def test_legacy_passive_read_is_empty_and_no_migration(tmp_path):
    store, first, second = fixture(tmp_path)
    with store._connect(write=True) as conn:
        for table in ('organization_milestone_links', 'organization_milestone_receipts', 'organization_milestone_revisions'):
            conn.execute('DROP TABLE '+table)
        before = '\n'.join(conn.iterdump())
        assert milestone_projection(conn) == []
        assert '\n'.join(conn.iterdump()) == before
    assert store.milestones() == []
    OrganizationStore(store.path, settings=store.settings)
    assert store.milestones() == []
    with store._connect(write=True) as conn:
        conn.execute('DROP TABLE organization_milestone_links')
    with pytest.raises(ValueError, match='incomplete'):
        store.milestones()


def test_no_empty_milestone_or_cancelled_objective_claims_completion(tmp_path):
    store, first, second = fixture(tmp_path)
    save(store, 'empty')
    save(store, 'work', [first])
    assert store.milestones()[0]['status'] == 'planned'
    with store._write() as conn:
        conn.execute('UPDATE objectives SET cancelled=1 WHERE id=?', (first,))
    assert store.milestones()[1]['status'] == 'needs_attention'


def test_history_is_immutable_and_foreign_key_preserves_objectives(tmp_path):
    store, first, second = fixture(tmp_path)
    save(store, 'work', [first]); save(store, 'work', [first], rev=1)
    with store._connect(write=True) as conn:
        assert conn.execute('SELECT count(*) FROM organization_milestone_revisions').fetchone()[0] == 2
        for table in ('organization_milestone_links', 'organization_milestone_receipts', 'organization_milestone_revisions'):
            with pytest.raises(sqlite3.IntegrityError, match='Immutable'):
                conn.execute('DELETE FROM '+table)


def test_archive_preserves_completion_and_evidence_links(tmp_path):
    store, first, second = fixture(tmp_path)
    save(store, 'work', [first]); accept(store, first)
    before = store.milestones()
    store.set_objective_archived(first, True, expected_revision=0, idempotency_key='archive')
    assert store.milestones() == before
    assert OrganizationStore(store.path, settings=store.settings).milestones() == before


def test_insert_failure_rolls_back_revision_links_and_receipt(tmp_path):
    store, first, second = fixture(tmp_path)
    with store._connect(write=True) as conn:
        conn.execute("CREATE TRIGGER reject_milestone_receipt BEFORE INSERT ON organization_milestone_receipts BEGIN SELECT RAISE(ABORT,'Injected failure'); END;")
    with pytest.raises(sqlite3.IntegrityError, match='Injected'):
        save(store, 'work', [first])
    with store._connect() as conn:
        for table in ('organization_milestone_revisions', 'organization_milestone_links', 'organization_milestone_receipts'):
            assert conn.execute('SELECT count(*) FROM '+table).fetchone()[0] == 0


def test_read_only_projection_keeps_entire_database_unchanged(tmp_path):
    store, first, second = fixture(tmp_path)
    save(store, 'work', [first])
    with store._connect() as conn:
        before = '\n'.join(conn.iterdump())
    store.milestones()
    with store._connect() as conn:
        assert '\n'.join(conn.iterdump()) == before


def test_legacy_and_partial_schema_compatibility(tmp_path):
    from eidolon_cli.organization_observation import _compatible_schema
    store, first, second = fixture(tmp_path)
    with store._connect(write=True) as conn:
        for table in ('organization_milestone_links', 'organization_milestone_receipts', 'organization_milestone_revisions'):
            conn.execute('DROP TABLE '+table)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert _compatible_schema(conn, tables)
        conn.execute('CREATE TABLE organization_milestone_links (x TEXT)')
        tables.add('organization_milestone_links')
        assert not _compatible_schema(conn, tables)


def test_same_text_typed_kind_change_invalidates_milestone_completion(tmp_path):
    from eidolon_cli.organization_outcome_contracts import write_contract
    store, first, _ = fixture(tmp_path)
    with store._write() as conn:
        write_contract(conn, first, [{'id': 'result', 'text': 'Same requirement', 'kind': 'deliverable'}])
    save(store, 'work', [first]); accept(store, first)
    assert store.milestones()[0]['status'] == 'complete'
    with store._write() as conn:
        before = conn.execute('SELECT criteria FROM objective_control WHERE objective_id=?', (first,)).fetchone()[0]
        write_contract(conn, first, [{'id': 'result', 'text': 'Same requirement', 'kind': 'human_judgment'}])
        assert conn.execute('SELECT criteria FROM objective_control WHERE objective_id=?', (first,)).fetchone()[0] == before
    view, = store.milestones()
    assert view['status'] == 'needs_attention'
    assert view['reasons'] == ['objective_scope_changed']
    assert not view['objectiveStates'][0]['accepted']
    assert OrganizationStore(store.path, settings=store.settings).milestones() == [view]


def test_legacy_contract_absence_is_supported_by_milestone_projection(tmp_path):
    store, first, _ = fixture(tmp_path)
    with store._connect(write=True) as conn:
        for table in ('objective_contract_acceptances', 'objective_human_judgments', 'objective_contract_revisions'):
            conn.execute('DROP TABLE '+table)
    save(store, 'work', [first]); accept(store, first)
    assert store.milestones()[0]['status'] == 'complete'
