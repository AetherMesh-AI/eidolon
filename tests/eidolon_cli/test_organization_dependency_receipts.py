"""Real accepted fixtures, passive receipts and adversarial retained-state changes."""
from dataclasses import FrozenInstanceError, replace
import hashlib
import json
import sqlite3

import pytest

from eidolon_cli.organization_dependency_receipts import accepted_dependency_receipt
from eidolon_cli.organization_outcome_contracts import definitions, record_human_judgment, write_contract
from eidolon_cli.organization_store import OrganizationStore
from tests.eidolon_cli.test_organization_acceptance import _plan, _work_and_review, _integrate, _decision
from tests.eidolon_cli.test_organization_outcome_contracts import decision, judgment_args


def accepted(tmp_path, *, kind='deliverable', delivery_mode='managed_artifact', legacy=False):
    store = OrganizationStore(tmp_path / 'state.db')
    objective = store.create_objective('Recommendation', 'Choose a supported option.', idempotency_key='create',
        delivery_mode=delivery_mode, acceptance_criteria=['One supported recommendation'] if legacy else [
            {'id': 'recommendation', 'text': 'One supported recommendation', 'kind': kind}])
    _plan(store)
    _work_and_review(store)
    if kind == 'human_judgment':
        integrate = store.claim_next()
        assert store.finish(integrate, {'summary': 'Combined', 'deliverable': 'Choose A from the supplied facts.'})
        record_human_judgment(store, objective['id'], **judgment_args(store))
        claim = store.claim_next()
    else:
        claim = _integrate(store)
    assert store.finish(claim, _decision(store, claim) if legacy else decision(store, claim))
    return store, objective['id'], claim['id']


def receipt(store, objective_id):
    with store._connect() as conn:
        return accepted_dependency_receipt(conn, objective_id)


def damage(store, sql, values=()):
    """Deliberate corruption of an isolated fixture, never production storage."""
    with store._write() as conn:
        conn.execute(sql, values)


def test_real_acceptance_is_deterministic_immutable_and_read_only(tmp_path):
    store, objective_id, request_id = accepted(tmp_path)
    initial = receipt(store, objective_id)
    assert initial.acceptance_request_id == request_id
    assert initial.accepted_round == 0
    assert initial.to_dict()['kind'] == 'accepted_historical_deliverable'
    assert initial.to_dict()['liveSourceVerified'] is False
    with pytest.raises(FrozenInstanceError):
        initial.accepted_round = 4
    with pytest.raises(FrozenInstanceError):
        initial.artifacts[0].sha256 = 'changed'
    detached = initial.to_dict()
    detached['artifacts'][0]['sha256'] = 'changed'
    assert receipt(store, objective_id) == initial
    with sqlite3.connect(f'file:{store.path}?mode=ro', uri=True, isolation_level=None) as conn:
        conn.row_factory = sqlite3.Row
        before = conn.total_changes
        allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_TRANSACTION}
        conn.set_authorizer(lambda operation, *args: sqlite3.SQLITE_OK if operation in allowed else sqlite3.SQLITE_DENY)
        assert accepted_dependency_receipt(conn, objective_id) == initial
        assert conn.total_changes == before and not conn.in_transaction
        conn.execute('BEGIN')
        assert accepted_dependency_receipt(conn, objective_id).sha256 == initial.sha256
        assert conn.in_transaction
        conn.rollback()
    # Independent reopen changes no receipt identity and uses the actual completed request.
    assert receipt(OrganizationStore(store.path), objective_id) == initial


@pytest.mark.parametrize('mutation', [
    "UPDATE objectives SET cancelled=1",
    "UPDATE objective_control SET status='pending'",
    "UPDATE objective_control SET status='reviewing'",
    "UPDATE objective_control SET status='blocked'",
])
def test_unsettled_or_cancelled_objectives_are_ineligible(tmp_path, mutation):
    store, objective_id, _ = accepted(tmp_path)
    assert receipt(store, objective_id) is not None
    damage(store, mutation)
    assert receipt(store, objective_id) is None


def test_absent_legacy_and_source_delivery_have_no_receipt(tmp_path):
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    assert accepted_dependency_receipt(conn, 'missing') is None
    assert conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] == 0
    for name, options in [('legacy', {'legacy': True}), ('source', {'delivery_mode': 'source_project'})]:
        directory = tmp_path / name
        directory.mkdir()
        store, objective_id, _ = accepted(directory, **options)
        assert receipt(store, objective_id) is None
        assert receipt(store, 'missing') is None


@pytest.mark.parametrize('mutation', [
    "UPDATE objective_control SET round=round+1",
    "UPDATE objectives SET description='Changed scope'",
    "UPDATE objective_control SET amended_scope='Changed scope'",
    "UPDATE objective_control SET criteria='[\"Changed criterion\"]'",
    "UPDATE objective_control SET required_checks='[\"project_tests\"]'",
    "UPDATE objective_acceptances SET approved=0",
    "UPDATE objective_acceptances SET agent_id='manager'",
    "UPDATE objective_acceptances SET evidence_ids='[]'",
    "UPDATE objective_acceptances SET criteria_results='[]'",
    "UPDATE objective_acceptances SET conflicts='[\"Unresolved\"]'",
    "UPDATE requests SET status='queued' WHERE type='request.accept'",
    "UPDATE requests SET type='request.review' WHERE type='request.accept'",
    "UPDATE requests SET agent_id='manager' WHERE type='request.accept'",
    "UPDATE requests SET payload='{}' WHERE type='request.accept'",
    "UPDATE requests SET payload='not json' WHERE type='request.accept'",
    "UPDATE requests SET payload='{}' WHERE type='request.integrate'",
    "UPDATE tasks SET status='queued'",
    "UPDATE reviews SET approved=0",
    "UPDATE requests SET status='cancelled' WHERE type='request.review'",
    "UPDATE requests SET payload='{}' WHERE type='request.review'",
    "UPDATE objective_deliverables SET round=round+1",
    "UPDATE objective_deliverables SET evidence_ids='[]'",
    "UPDATE objective_deliverables SET author_id='executive'",
    "UPDATE evidence SET content='Corrupted task bytes'",
    "UPDATE objective_deliverables SET content='Corrupted final bytes'",
])
def test_stale_or_corrupt_accepted_storage_fails_closed(tmp_path, mutation):
    store, objective_id, _ = accepted(tmp_path)
    if "SET type=" in mutation:
        damage(store, 'DROP TRIGGER request_type_immutable_update')
    damage(store, mutation)
    with pytest.raises(ValueError):
        receipt(store, objective_id)


@pytest.mark.parametrize('table', ['evidence', 'objective_deliverables'])
def test_rehashed_changed_bytes_still_fail_original_acceptance_binding(tmp_path, table):
    store, objective_id, _ = accepted(tmp_path)
    changed = 'New bytes with a valid replacement hash.'
    damage(store, f'UPDATE {table} SET content=?,sha256=?', (changed, hashlib.sha256(changed.encode()).hexdigest()))
    with pytest.raises(ValueError, match='binding changed'):
        receipt(store, objective_id)


@pytest.mark.parametrize('table', ['objective_contract_acceptances', 'objective_human_judgments', 'objective_acceptances',
                                   'workspace_roots', 'objective_projects', 'reviews'])
def test_missing_required_schema_is_not_recreated(tmp_path, table):
    store, objective_id, _ = accepted(tmp_path)
    with sqlite3.connect(store.path) as conn:
        conn.execute(f'DROP TABLE {table}')
    with pytest.raises(ValueError):
        receipt(store, objective_id)
    with sqlite3.connect(store.path) as conn:
        assert conn.execute('SELECT 1 FROM sqlite_master WHERE name=?', (table,)).fetchone() is None


def test_missing_typed_binding_is_corruption_not_legacy(tmp_path):
    store, objective_id, _ = accepted(tmp_path)
    damage(store, 'DROP TRIGGER immutable_objective_contract_acceptances_delete')
    damage(store, 'DELETE FROM objective_contract_acceptances')
    with pytest.raises(ValueError, match='stale or missing'):
        receipt(store, objective_id)


@pytest.mark.parametrize('value', ['[]', '[{"criterionId":"other"}]', '{}', 'null'])
def test_exact_typed_bindings_are_required(tmp_path, value):
    store, objective_id, _ = accepted(tmp_path)
    damage(store, 'DROP TRIGGER immutable_objective_contract_acceptances_update')
    damage(store, 'UPDATE objective_contract_acceptances SET evidence_bindings=?', (value,))
    with pytest.raises(ValueError, match='binding changed'):
        receipt(store, objective_id)


def test_new_contract_revision_never_reuses_acceptance(tmp_path):
    store, objective_id, _ = accepted(tmp_path)
    initial = receipt(store, objective_id)
    with store._write() as conn:
        new = [{'id': 'new-id', 'text': 'One supported recommendation', 'kind': 'deliverable'}]
        write_contract(conn, objective_id, definitions(new, ''))
    with pytest.raises(ValueError, match='stale or missing'):
        receipt(store, objective_id)
    assert initial.contract_revision == 1


@pytest.mark.parametrize('field,value', [('approved', 0), ('reason', 'Tampered reason'), ('deliverable_sha256', '0' * 64)])
def test_human_judgment_is_exact_and_revalidated(tmp_path, field, value):
    store, objective_id, _ = accepted(tmp_path, kind='human_judgment')
    assert receipt(store, objective_id) is not None
    damage(store, 'DROP TRIGGER immutable_objective_human_judgments_update')
    damage(store, f'UPDATE objective_human_judgments SET {field}=?', (value,))
    with pytest.raises(ValueError, match='human judgment'):
        receipt(store, objective_id)


def test_newly_accepted_round_does_not_match_old_receipt(tmp_path):
    store, objective_id, _ = accepted(tmp_path)
    old = receipt(store, objective_id)
    # Replan through production machinery, then independently complete the new round.
    with store._write() as conn:
        store._replan(conn, objective_id, 'Reassess the supported recommendation.')
    assert receipt(store, objective_id) is None
    _plan(store)
    _work_and_review(store)
    claim = _integrate(store)
    assert store.finish(claim, decision(store, claim))
    new = receipt(store, objective_id)
    assert new.accepted_round == old.accepted_round + 1
    assert new.acceptance_request_id != old.acceptance_request_id
    assert new.sha256 != old.sha256


def test_managed_validation_hashes_exact_retained_workspace_bytes_without_source_reads(tmp_path, monkeypatch):
    from tests.eidolon_cli.test_organization_edits import _setup, _propose, _review
    store, source, objective = _setup(tmp_path)
    with store._write() as conn:
        conn.execute("UPDATE objective_control SET delivery_mode='managed_artifact',required_checks='[\"managed_validation\"]'")
        text = json.loads(conn.execute('SELECT criteria FROM objective_control').fetchone()[0])[0]
        write_contract(conn, objective['id'], definitions([
            {'id': 'validation', 'text': text, 'kind': 'authorized_check', 'checkKind': 'managed_validation'}], ''))
    _propose(store)
    _review(store)
    assert store.finish(store.claim_next(), {})
    assert store.finish(store.claim_next(), {})
    claim = _integrate(store)
    assert store.finish(claim, decision(store, claim))
    initial = receipt(store, objective['id'])
    assert any(item.kind == 'managed_validation' for item in initial.artifacts)
    from tools import organization_file_read
    def forbidden(*args, **kwargs):
        raise AssertionError('Historical receipt must never read live source files')
    monkeypatch.setattr(organization_file_read, 'organization_file_read_scope', forbidden)
    # This is historical managed output: changing external files is not a new certification.
    source.write_text('Unrelated external source changed', encoding='utf-8')
    assert receipt(store, objective['id']) == initial
    with sqlite3.connect(store.path) as conn:
        conn.execute('DROP TRIGGER immutable_workspace_revisions_update')
        conn.execute("UPDATE workspace_revisions SET content='Corrupt retained bytes' WHERE revision>0")
    with pytest.raises(ValueError):
        receipt(store, objective['id'])


def test_selected_project_identity_is_pinned_and_revocation_invalidates(tmp_path):
    from eidolon_cli.organization_config import OrganizationSettings
    from tests.organization_package_helpers import claim_after_decomposition
    root = tmp_path / 'notes'
    root.mkdir()
    settings = OrganizationSettings.from_config({'organization': {'read_roots': [str(root)],
        'projects': [{'id': 'notes', 'root': 'root0', 'recipe': None, 'team': 'general'}]}})
    store = OrganizationStore(tmp_path / 'state.db', settings)
    objective = store.create_objective('Plan the folder', project_ids=['notes'], delivery_mode='managed_artifact',
        idempotency_key='create', acceptance_criteria=[{'id': 'plan', 'text': 'A useful folder plan', 'kind': 'deliverable'}])
    plan = claim_after_decomposition(store)
    assert store.finish(plan, {'tasks': [{'title': 'Plan', 'description': 'Choose useful folders',
                                         'type': 'work.analyze', 'projectId': 'notes', 'team': 'general'}]})
    _work_and_review(store)
    claim = _integrate(store)
    assert store.finish(claim, decision(store, claim))
    initial = receipt(store, objective['id'])
    assert initial.to_dict()['projects'][0]['id'] == 'notes'
    store.reload_configuration(replace(store.settings, revoked_root_aliases=('root0',)))
    with pytest.raises(ValueError, match='revoked'):
        receipt(store, objective['id'])


def test_selectively_reused_evidence_retains_exact_immutable_provenance(tmp_path):
    from tests.eidolon_cli.test_organization_selective_repair import fixture
    store, objective_id, _, accept, rejection = fixture(tmp_path)
    assert store.finish(accept, rejection)
    reviews = 0
    while reviews < 2:
        claim = store.claim_next()
        context = store.context(claim)
        if claim['type'] == 'work.draft':
            assert store.finish(claim, {'summary': 'Corrected', 'deliverable': 'Corrected ' + context['task']['title']})
        else:
            assert claim['type'] == 'request.review'
            assert store.finish(claim, {'approved': True, 'summary': 'Independently reviewed corrected bytes',
                                       'evidenceIds': [item['id'] for item in context['evidence']]})
            reviews += 1
    claim = _integrate(store)
    assert store.finish(claim, decision(store, claim))
    result = receipt(store, objective_id)
    assert result.accepted_round == 1
    damage(store, 'DROP TRIGGER immutable_objective_reused_evidence_update')
    damage(store, "UPDATE objective_reused_evidence SET sha256='corrupt'")
    with pytest.raises(ValueError, match='Reused evidence'):
        receipt(store, objective_id)


def test_source_integration_contract_is_ineligible_even_with_managed_delivery(tmp_path):
    from tests.eidolon_cli.test_organization_edits import _setup, _propose, _review
    store, source, objective = _setup(tmp_path)
    with store._write() as conn:
        conn.execute("UPDATE objective_control SET delivery_mode='managed_artifact',required_checks='[\"source_integration\"]'")
        text = json.loads(conn.execute('SELECT criteria FROM objective_control').fetchone()[0])[0]
        write_contract(conn, objective['id'], definitions([{'id': 'source', 'text': text, 'kind': 'source_integration'}], ''))
    _, artifact, _, _ = _propose(store)
    _review(store)
    assert store.finish(store.claim_next(), {})
    assert store.finish(store.claim_next(), {})
    pending = next(row for row in store.snapshot()['requests'] if row['type'] == 'request.merge')
    source.write_text(artifact['editProposal']['newContent'], encoding='utf-8', newline='')
    assert store.resolve(pending['id'], 'record_handoff', 'Applied reviewed bytes', [artifact['id']], idempotency_key='handoff')
    claim = _integrate(store)
    assert store.finish(claim, decision(store, claim))
    assert receipt(store, objective['id']) is None


def test_failed_reader_does_not_rollback_callers_transaction(tmp_path):
    store, objective_id, _ = accepted(tmp_path)
    with store._write() as conn:
        conn.execute("UPDATE evidence SET content='Corrupt'")
        with pytest.raises(ValueError):
            accepted_dependency_receipt(conn, objective_id)
        assert conn.in_transaction
        assert conn.execute('SELECT content FROM evidence').fetchone()[0] == 'Corrupt'


def test_deleted_typed_contract_schema_is_not_misclassified_as_legacy(tmp_path):
    store, objective_id, _ = accepted(tmp_path)
    with sqlite3.connect(store.path) as conn:
        for table in ('objective_contract_acceptances', 'objective_contract_revisions', 'objective_human_judgments'):
            conn.execute(f'DROP TABLE {table}')
    with pytest.raises(ValueError, match='typed contract is missing'):
        receipt(store, objective_id)


def _accept_project_stage(store, objective_id, claim):
    import threading
    store.run_project_stage(claim, threading.Event())
    with store._connect() as conn:
        result = json.loads(conn.execute('SELECT record FROM project_run_results').fetchone()[0])
        assert result['status'] == 'passed', result
    assert store.finish(claim, {})
    review = store.claim_next()
    assert review['type'] == 'request.test_review'
    assert store.finish(review, {'approved': True, 'summary': 'Independent exact runtime receipt review',
                                'evidenceIds': json.loads(review['payload'])['evidenceIds']})
    accept = _integrate(store)
    assert store.finish(accept, decision(store, accept))
    return receipt(store, objective_id)


def _bind_project_contract(store, objective_id, check):
    with store._write() as conn:
        text = json.loads(conn.execute('SELECT criteria FROM objective_control WHERE objective_id=?', (objective_id,)).fetchone()[0])[0]
        conn.execute("UPDATE objective_control SET delivery_mode='managed_artifact',required_checks=? WHERE objective_id=?",
                     (json.dumps([check]), objective_id))
        write_contract(conn, objective_id, definitions([
            {'id': 'runtime', 'text': text, 'kind': 'authorized_check', 'checkKind': check}], ''))


@pytest.mark.linux_only
@pytest.mark.parametrize('mutation', ['bytes', 'review', 'round'])
def test_native_retained_project_run_and_stale_proof(tmp_path, mutation):
    import os
    from eidolon_cli.organization_project_runner import probe_project_runner
    probe = probe_project_runner()
    if probe['status'] != 'passed':
        if os.environ.get('EIDOLON_REQUIRE_PROJECT_SANDBOX') == '1':
            pytest.fail('Required native sandbox unavailable: ' + probe['stderr'])
        pytest.skip('Native execution unavailable: ' + probe['stderr'])
    from tests.eidolon_cli.test_organization_edits import _setup, _read, _review
    from tests.eidolon_cli.test_organization_project_runner import GOOD_TEST
    from eidolon_cli.organization_project_config import parse_project_grants
    store, source, objective = _setup(tmp_path, content=GOOD_TEST)
    source.rename(source.with_name('test_project.py'))
    path = 'root0/test_project.py'
    grants = parse_project_grants([{'id': 'tests', 'files': [path], 'execution': {'recipe': 'python_unittest'}}], 1)
    staff = tuple(replace(member, tool_grants=(*member.tool_grants, 'run_tests')) for member in store.settings.roster)
    store = OrganizationStore(store.path, replace(store.settings, roster=staff,
        tool_grants=(*store.settings.tool_grants, 'run_tests'), project_grants=grants))
    _bind_project_contract(store, objective['id'], 'project_tests')
    work = store.claim_next()
    observed, _ = _read(store, work, path=path)
    assert store.finish(work, {'summary': 'Equivalent independently reviewed arithmetic', 'edit': {
        'path': path, 'baseRevision': observed['workspaceRevision'], 'baseSha256': observed['sourceSha256'],
        'oldText': '1 + 2', 'newText': '2 + 1'}})
    _review(store)
    assert store.finish(store.claim_next(), {})
    assert store.finish(store.claim_next(), {})
    initial = _accept_project_stage(store, objective['id'], store.claim_next())
    assert any(item.kind == 'project_execution' for item in initial.artifacts)
    table = {'bytes': 'project_run_results', 'review': 'project_run_reviews', 'round': 'project_run_starts'}[mutation]
    damage(store, f'DROP TRIGGER immutable_{table}_update')
    damage(store, {'bytes': "UPDATE project_run_results SET record='{}'",
                   'review': 'UPDATE project_run_reviews SET approved=0',
                   'round': 'UPDATE project_run_starts SET round=round+1'}[mutation])
    with pytest.raises(ValueError):
        receipt(store, objective['id'])


@pytest.mark.linux_only
@pytest.mark.parametrize('mutation', ['bytes', 'missing'])
def test_native_retained_build_outputs_are_exact_and_complete(tmp_path, mutation):
    import os
    if os.environ.get('EIDOLON_REQUIRE_NODE_SANDBOX') != '1':
        pytest.skip('Native Docker/TypeScript execution is not explicitly enabled')
    assert os.environ.get('EIDOLON_TEST_TYPESCRIPT_BUNDLE')
    from tests.eidolon_cli.test_organization_project_typescript_build import _build_reviewed_store
    store, _, objective, claim, _ = _build_reviewed_store(tmp_path)
    _bind_project_contract(store, objective['id'], 'project_build')
    initial = _accept_project_stage(store, objective['id'], claim)
    assert sum(item.kind == 'project_build_output' for item in initial.artifacts) == 2
    operation = 'update' if mutation == 'bytes' else 'delete'
    damage(store, f'DROP TRIGGER immutable_project_build_outputs_{operation}')
    damage(store, "UPDATE project_build_outputs SET record='{}'" if mutation == 'bytes' else
           'DELETE FROM project_build_outputs WHERE id=(SELECT id FROM project_build_outputs LIMIT 1)')
    with pytest.raises(ValueError):
        receipt(store, objective['id'])
