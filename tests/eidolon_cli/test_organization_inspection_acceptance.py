"""Real Git/SQLite acceptance proof; command outcomes are synthetic ledger inputs."""
from tests.organization_package_helpers import claim_after_decomposition
from dataclasses import replace
import json
from pathlib import Path
import threading

import pytest

from eidolon_cli import organization_project_runner as runner
from eidolon_cli.organization_store import OrganizationStore
from tests.eidolon_cli.test_organization_acceptance import _decision
from tests.eidolon_cli.test_organization_multi_project_execution import configured, git, reviewed_inspections
from tests.eidolon_cli.test_organization_project_security import _simulated_terminal_result
from tools.organization_file_read import organization_file_read_scope


def inspection_objective(tmp_path, monkeypatch, *, delivery_mode, explicit_source, legacy=False, source_grant=True):
    tmp_path.mkdir(parents=True, exist_ok=True)
    store, old = configured(tmp_path, source=True)
    store.cancel(old['id'])
    if legacy:
        settings = replace(store.settings, read_roots=store.settings.read_roots[:1],
                           project_grants=store.settings.project_grants[:1], projects=())
        if not source_grant:
            settings = replace(settings,
                tool_grants=tuple(item for item in settings.tool_grants if item != 'integrate_source'),
                roster=tuple(replace(member, tool_grants=tuple(item for item in member.tool_grants
                    if item != 'integrate_source')) for member in settings.roster))
        store = OrganizationStore(store.path, settings)
    checks = ['project_tests', 'source_integration'] if explicit_source else ['project_tests']
    objective = store.create_objective('Inspect and retain both selected repositories',
        idempotency_key='inspection-source', delivery_mode=delivery_mode,
        required_checks=checks, project_ids=None if legacy else ['alpha', 'beta'])
    monkeypatch.setattr(runner, 'run_project_tests', lambda files, grant, cancel: _simulated_terminal_result(files))
    if not legacy:
        return store, objective, reviewed_inspections(store, objective)
    plan = claim_after_decomposition(store)
    assert plan['type'] == 'request.plan'
    store.finish(plan, {'workers': 1, 'tasks': [{'title': 'Inspect the legacy project',
        'description': 'Read the selected project file.', 'type': 'work.inspect',
        'team': 'engineering', 'agentId': 'inspector'}]})
    claim = store.claim_next()
    if claim['type'] == 'request.hire':
        store.finish(claim, {})
        claim = store.claim_next()
    assert claim['type'] == 'work.inspect'
    args = {'path': 'root0/test_sample.py', 'offset': 1, 'limit': 2000}
    receipt = store.record_tool_start(claim, 'inspect', 'read_file', args)
    with organization_file_read_scope(store.settings.read_roots, store.settings.max_tool_result_chars) as scope:
        raw = scope.read_file(**args)
    assert json.loads(raw)['success']
    store.record_tool_finish(claim, receipt['id'], raw, 'completed')
    store.finish(claim, {'summary': 'Read exact source', 'deliverable': 'The project source was inspected.'})
    review = store.claim_next()
    assert review['type'] == 'request.review'
    store.finish(review, {'approved': True, 'summary': 'Exact inspection independently reviewed',
                         'evidenceIds': json.loads(review['payload'])['evidenceIds']})
    claim = store.claim_next()
    assert claim['type'] == 'request.project_test'
    return store, objective, claim


def finish_project_stage(store, claim):
    if claim['type'] in {'request.project_test', 'request.source_integrate'}:
        store.run_project_stage(claim, threading.Event())
        store.finish(claim, {})
    else:
        assert claim['type'] == 'request.test_review'
        store.finish(claim, {'approved': True, 'summary': 'Exact synthetic command ledger independently reviewed',
                            'evidenceIds': json.loads(claim['payload'])['evidenceIds']})
    next_claim = store.claim_next()
    assert next_claim is not None
    return next_claim


@pytest.mark.parametrize('native_os', [pytest.param('linux', marks=pytest.mark.linux_only),
                                      pytest.param('macos', marks=pytest.mark.macos_only)])
@pytest.mark.parametrize(('delivery_mode', 'explicit_source'), [
    ('source_project', True), ('managed_artifact', True), ('source_project', False)])
def test_inspected_projects_accept_exact_current_source_receipts_without_managed_edits(
        tmp_path, monkeypatch, native_os, delivery_mode, explicit_source):
    store, objective, claim = inspection_objective(tmp_path, monkeypatch,
        delivery_mode=delivery_mode, explicit_source=explicit_source)
    before = {root: (git(root, 'rev-parse', 'HEAD'), (Path(root) / '.git/index').read_bytes(),
                     (Path(root) / 'test_sample.py').read_bytes()) for root in store.settings.read_roots}
    while claim['type'] != 'request.integrate':
        claim = finish_project_stage(store, claim)
    with store._connect() as conn:
        assert conn.execute('SELECT count(*) FROM edit_applications').fetchone()[0] == 0
        receipts = store.current_source_evidences(conn, objective['id'])
        runs = store.verify_project_test_runs(conn, objective['id'])
        assert len(receipts) == len(runs) == 2
        expected_ids = {item['id'] for item in receipts} | {artifact['id'] for _, artifact in runs}
        assert expected_ids.issubset(json.loads(claim['payload'])['evidenceIds'])
        for project_id in ('alpha', 'beta'):
            assert store.verified_source_integration(conn, objective['id'], project_id)
        # Successful source delivery never invents managed edit validation.
        with pytest.raises(ValueError, match='no applied project evidence'):
            store.verify_project_objective_acceptance(conn, objective['id'],
                require_source=True, require_validation=True)
    for artifact in receipts:
        receipt = json.loads(artifact['content'])
        root = store.settings.read_roots[int(receipt['rootAlias'][4:])]
        assert git(root, 'show', receipt['ref'] + ':test_sample.py') == before[root][2]
        assert (git(root, 'rev-parse', 'HEAD'), (Path(root) / '.git/index').read_bytes(),
                (Path(root) / 'test_sample.py').read_bytes()) == before[root]
    store.finish(claim, {'summary': 'Both snapshots retained',
        'deliverable': 'Both reviewed inspection snapshots have exact independently verified output branches.'})
    store = OrganizationStore(store.path, store.settings)
    accept = store.claim_next()
    assert accept['type'] == 'request.accept'
    assert expected_ids.issubset(item['id'] for item in store.context(accept)['evidence'])
    decision = _decision(store, accept)
    assert store.finish(accept, decision)
    reopened = OrganizationStore(store.path, store.settings)
    outcome = next(row for row in reopened.snapshot()['objectives'] if row['id'] == objective['id'])
    assert outcome['status'] == 'completed'
    assert outcome['acceptance']['status'] == 'accepted'
    assert not reopened.finish(accept, decision)


@pytest.mark.parametrize('native_os', [pytest.param('linux', marks=pytest.mark.linux_only),
                                      pytest.param('macos', marks=pytest.mark.macos_only)])
@pytest.mark.parametrize('explicit_source', [True, False])
def test_inspection_source_acceptance_rechecks_every_project_and_current_proof(
        tmp_path, monkeypatch, native_os, explicit_source):
    store, objective, claim = inspection_objective(tmp_path, monkeypatch,
        delivery_mode='source_project', explicit_source=explicit_source)

    def verify(conn, target=None):
        (target or store).verify_project_objective_acceptance(conn, objective['id'], require_source=explicit_source)

    # Inspection, tests, or one published repository cannot satisfy a two-project objective.
    while claim['type'] != 'request.integrate':
        with store._connect() as conn:
            if conn.execute('SELECT count(*) FROM project_source_receipts').fetchone()[0] < 2:
                with pytest.raises(ValueError):
                    verify(conn)
        claim = finish_project_stage(store, claim)
    store = OrganizationStore(store.path, store.settings)
    with store._connect() as conn:
        verify(conn)
        receipts = conn.execute('SELECT * FROM project_source_receipts ORDER BY request_id').fetchall()
        run = conn.execute('SELECT * FROM project_run_starts WHERE id=?', (receipts[1]['run_id'],)).fetchone()
        review = conn.execute('SELECT * FROM project_run_reviews WHERE run_id=?', (run['id'],)).fetchone()
        # Corrupt one field at a time inside a rolled-back transaction, preserving the
        # immutable ledger and all other valid project proof between adversarial cases.
        attacks = [
            ('project_source_receipts', 'delete', 'DELETE FROM project_source_receipts WHERE request_id=?',
             (receipts[1]['request_id'],)),
            ('project_source_receipts', 'update', 'UPDATE project_source_receipts SET sha256=? WHERE request_id=?',
             ('0' * 64, receipts[1]['request_id'])),
            ('project_source_receipts', 'update', 'UPDATE project_source_receipts SET run_id=? WHERE request_id=?',
             (receipts[0]['run_id'], receipts[1]['request_id'])),
            ('project_run_results', 'update', 'UPDATE project_run_results SET sha256=? WHERE run_id=?',
             ('0' * 64, run['id'])),
            ('project_run_reviews', 'update', 'UPDATE project_run_reviews SET approved=0 WHERE request_id=?',
             (review['request_id'],)),
            (None, None, 'UPDATE objective_control SET round=round+1 WHERE objective_id=?', (objective['id'],)),
            (None, None, 'INSERT INTO project_run_starts VALUES (?,?,?,?,?,?,?,?,?,?,?)',
             tuple({**dict(run), 'id': 'unknown-new-run', 'token': 'unknown-new-token',
                    'created': run['created'] + 1}.values())),
        ]
        for table, operation, sql, params in attacks:
            conn.execute('SAVEPOINT invalid_proof')
            try:
                if table:
                    conn.execute(f'DROP TRIGGER immutable_{table}_{operation}')
                conn.execute(sql, params)
                with pytest.raises(ValueError):
                    verify(conn)
            finally:
                conn.execute('ROLLBACK TO invalid_proof')
                conn.execute('RELEASE invalid_proof')
            verify(conn)
        receipt = json.loads(receipts[1]['record'])
    root = store.settings.read_roots[int(receipt['rootAlias'][4:])]
    commit = git(root, 'rev-parse', receipt['ref']).decode().strip()
    for replacement in (None, git(root, 'rev-parse', 'HEAD').decode().strip()):
        git(root, 'update-ref', '-d', receipt['ref']) if replacement is None else git(root, 'update-ref', receipt['ref'], replacement)
        with store._connect() as conn, pytest.raises(ValueError, match='removed or changed'):
            verify(conn)
        git(root, 'update-ref', receipt['ref'], commit)
    original = (Path(root) / 'test_sample.py').read_text()
    (Path(root) / 'test_sample.py').write_text(original + '# changed after testing\n')
    with store._connect() as conn, pytest.raises(ValueError, match='snapshot no longer matches'):
        verify(conn)
    (Path(root) / 'test_sample.py').write_text(original)
    settings = store.settings
    changed_grants = [
        replace(settings, read_roots=tuple(reversed(settings.read_roots))),
        replace(settings, project_grants=(replace(settings.project_grants[0],
            files=(*settings.project_grants[0].files, 'root0/another.py')), *settings.project_grants[1:])),
        replace(settings, tool_grants=tuple(item for item in settings.tool_grants if item != 'integrate_source')),
        replace(settings, roster=tuple(replace(member,
            tool_grants=tuple(item for item in member.tool_grants if item != 'run_tests')) for member in settings.roster)),
        replace(settings, roster=tuple(replace(member,
            tool_grants=tuple(item for item in member.tool_grants if item != 'integrate_source')) for member in settings.roster)),
    ]
    for changed in changed_grants:
        revoked = OrganizationStore(store.path, changed)
        with revoked._connect() as conn, pytest.raises(ValueError):
            verify(conn, revoked)
        assert revoked.evidence('project_source_' + receipts[1]['request_id'])['content'] == receipts[1]['record']
    restored = OrganizationStore(store.path, settings)
    with restored._connect() as conn:
        verify(conn, restored)
        assert conn.execute('SELECT count(*) FROM objective_acceptances').fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM project_source_receipts').fetchone()[0] == 2

    if explicit_source:
        return
    # Legacy default-mode inspection with tests alone never acquired a source
    # delivery obligation. Its real ledger must remain independently acceptable.
    legacy, goal, claim = inspection_objective(tmp_path / 'tests-only', monkeypatch,
        delivery_mode='source_project', explicit_source=False, legacy=True, source_grant=False)
    while claim['type'] != 'request.integrate':
        claim = finish_project_stage(legacy, claim)
    with legacy._connect() as conn:
        assert conn.execute('SELECT count(*) FROM project_run_starts WHERE source_base IS NOT NULL').fetchone()[0] == 0
        legacy.verify_project_objective_acceptance(conn, goal['id'])
    legacy.finish(claim, {'summary': 'Reviewed test ledger', 'deliverable': 'The legacy inspection and test receipts were reviewed.'})
    accept = legacy.claim_next()
    assert legacy.finish(accept, _decision(legacy, accept))

    # A pinned source base, including an interrupted start without any receipt,
    # survives authority changes and later rounds as a delivery obligation.
    for interrupted in (False, True):
        legacy, goal, claim = inspection_objective(tmp_path / f'source-intended-{interrupted}', monkeypatch,
            delivery_mode='source_project', explicit_source=False, legacy=True)
        if interrupted:
            def interrupt_run(files, grant, cancel):
                raise RuntimeError('Interrupted before result persistence')
            monkeypatch.setattr(runner, 'run_project_tests', interrupt_run)
            with pytest.raises(RuntimeError, match='Interrupted'):
                legacy.run_project_stage(claim, threading.Event())
        else:
            while claim['type'] != 'request.source_integrate':
                claim = finish_project_stage(legacy, claim)
        revoked = OrganizationStore(legacy.path, replace(legacy.settings,
            tool_grants=tuple(item for item in legacy.settings.tool_grants if item != 'integrate_source')))
        with revoked._connect() as conn:
            assert conn.execute('SELECT count(*) FROM project_source_receipts').fetchone()[0] == 0
            assert conn.execute('SELECT count(*) FROM project_run_starts WHERE source_base IS NOT NULL').fetchone()[0] == 1
            for round_number in (0, 1):
                conn.execute('UPDATE objective_control SET round=? WHERE objective_id=?', (round_number, goal['id']))
                with pytest.raises(ValueError, match='current-round source integration'):
                    revoked.verify_project_objective_acceptance(conn, goal['id'])
