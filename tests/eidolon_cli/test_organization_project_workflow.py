"""Real SQLite and granted filesystem coverage for the bounded project pipeline."""
from dataclasses import replace
import hashlib
import json
import sqlite3
import time

import pytest

from eidolon_cli.organization_project_validation import parse_edit_result, validate_manifest
from eidolon_cli.organization_store import OrganizationStore
from tests.eidolon_cli.test_organization_edits import _setup as _base_setup, _read, _review, _count, PATH


def _setup(tmp_path, **options):
    return _base_setup(tmp_path.resolve(), **options)


def project_proposal(store, source, *, checks=None, second=True, old='old value', new='new value'):
    claim = store.claim_next()
    assert claim['type'] == 'work.edit'
    paths = [PATH]
    if second:
        (source.parent / 'config.json').write_bytes(b'{"enabled":false,"name":"caf\xc3\xa9"}\r\n')
        paths.append('root0/config.json')
    edits = []
    for index, path in enumerate(paths):
        read, _ = _read(store, claim, path=path, call=f'read-{index}')
        edits.append({'path': path, 'baseRevision': read['workspaceRevision'], 'baseSha256': read['sourceSha256'],
                      'oldText': old if index == 0 else 'false', 'newText': new if index == 0 else 'true'})
    store.finish(claim, {'summary': 'Propose the complete two-file change', 'edits': edits, 'validations': checks or []})
    with store._connect() as conn:
        evidence = conn.execute('SELECT id FROM evidence WHERE request_id=?', (claim['id'],)).fetchone()[0]
    return evidence


def apply_validate(store):
    _review(store)
    apply = store.claim_next()
    assert apply['type'] == 'request.apply'
    assert store.finish(apply, {})
    validate = store.claim_next()
    assert validate['type'] == 'request.validate'
    assert store.finish(validate, {})
    return validate


@pytest.mark.parametrize("native_os", [pytest.param("linux", marks=pytest.mark.linux_only),
                                      pytest.param("macos", marks=pytest.mark.macos_only)])
def test_multifile_bytes_validate_and_exact_owner_handoff_are_durable(tmp_path, native_os):
    store, source, objective = _setup(tmp_path)
    original = source.read_bytes()
    evidence = project_proposal(store, source, checks=[
        {'kind': 'text_contains', 'path': PATH, 'text': 'new value'},
        {'kind': 'text_absent', 'path': PATH, 'text': 'old value'},
        {'kind': 'json_valid', 'path': 'root0/config.json'},
        {'kind': 'json_value', 'path': 'root0/config.json', 'pointer': '/enabled', 'equals': True}])
    validate = apply_validate(store)
    proof = store.evidence(evidence)['editProposal']
    receipt = proof['validationReceipt']
    assert receipt['status'] == 'passed' and len(receipt['checks']) == 6
    assert receipt['notExecuted'] == ['project_commands', 'functional_tests']
    assert source.read_bytes() == original
    assert len(proof['files']) == 2
    reopened = OrganizationStore(store.path, store.settings)
    assert not reopened.finish(validate, {})
    merge = next(row for row in store.snapshot()['requests'] if row['type'] == 'request.merge')
    with pytest.raises(ValueError, match='does not match'):
        store.resolve(merge['id'], 'record_handoff', 'I say it is done', [evidence], idempotency_key='verify')
    assert _count(store, 'project_handoff_receipts') == 0
    for file in proof['files']:
        (source.parent / file['sourcePath'].split('/', 1)[1]).write_bytes(file['newContent'].encode('utf-8'))
    assert store.resolve(merge['id'], 'record_handoff', 'Applied the reviewed output', [evidence], idempotency_key='verify')
    assert not reopened.resolve(merge['id'], 'record_handoff', 'Applied the reviewed output', [evidence], idempotency_key='verify')
    handoff = store.evidence(evidence)['editProposal']['sourceVerificationReceipt']
    assert handoff['status'] == 'verified' and handoff['scope'] == 'latest_validated_project_heads'
    assert handoff['sourceWritesPerformed'] is False
    assert {row['sha256'] for row in handoff['files']} == {row['newSha256'] for row in proof['files']}
    assert _count(store, 'project_handoff_receipts') == 1
    assert store.claim_next()['type'] == 'request.integrate'


@pytest.mark.parametrize("native_os", [pytest.param("linux", marks=pytest.mark.linux_only),
                                      pytest.param("macos", marks=pytest.mark.macos_only)])
def test_managed_artifact_does_not_force_original_source_merge(tmp_path, native_os):
    store, source, objective = _setup(tmp_path)
    with store._write() as conn:
        conn.execute("UPDATE objective_control SET delivery_mode='managed_artifact' WHERE objective_id=?", (objective['id'],))
    evidence = project_proposal(store, source)
    apply_validate(store)
    assert store.evidence(evidence)['editProposal']['validationReceipt']['status'] == 'passed'
    assert not any(row['type'] == 'request.merge' for row in store.snapshot()['requests'])
    assert store.claim_next()['type'] == 'request.integrate'
    assert b'old value' in source.read_bytes()


@pytest.mark.parametrize("native_os", [pytest.param("linux", marks=pytest.mark.linux_only),
                                      pytest.param("macos", marks=pytest.mark.macos_only)])
def test_failed_assertion_retains_receipt_and_never_claims_functional_success(tmp_path, native_os):
    store, source, _ = _setup(tmp_path)
    evidence = project_proposal(store, source, checks=[{'kind': 'json_value', 'path': 'root0/config.json',
                                                     'pointer': '/enabled', 'equals': False}])
    apply_validate(store)
    proof = store.evidence(evidence)['editProposal']
    assert proof['status'] == 'applied'
    assert proof['validationReceipt']['status'] == 'failed'
    assert proof['validationReceipt']['checks'][-1]['passed'] is False
    snapshot = store.snapshot()
    assert snapshot['tasks'][0]['status'] == 'blocked'
    assert any(row['type'] == 'request.validation_failed' and row['status'] == 'pending_intervention' for row in snapshot['requests'])
    assert not any(row['type'] in {'request.merge', 'request.integrate'} for row in snapshot['requests'])
    with store._write() as conn:
        with pytest.raises(sqlite3.IntegrityError, match='Immutable'):
            conn.execute("UPDATE project_validation_receipts SET result='{}'")


@pytest.mark.parametrize("native_os", [pytest.param("linux", marks=pytest.mark.linux_only),
                                      pytest.param("macos", marks=pytest.mark.macos_only)])
def test_multifile_competing_base_never_partially_applies(tmp_path, native_os):
    store, source, objective = _setup(tmp_path)
    evidence = project_proposal(store, source)
    _review(store)
    apply = store.claim_next()
    # Another valid revision arrived for the second file after proposal review.
    with store._write() as conn:
        row = conn.execute("SELECT * FROM workspace_revisions WHERE path='root0/config.json'").fetchone()
        text = '{"competing":true}'
        conn.execute('INSERT INTO workspace_revisions VALUES (?,?,?,?,?,?,?)',
                     (objective['id'], row['path'], 1, row['workspace_id'], text, hashlib.sha256(text.encode()).hexdigest(), time.time()))
        conn.execute('UPDATE workspace_heads SET revision=1 WHERE path=?', (row['path'],))
    with pytest.raises(ValueError, match='stale'):
        store.finish(apply, {})
    with store._connect() as conn:
        assert conn.execute('SELECT revision FROM workspace_heads WHERE path=?', (PATH,)).fetchone()[0] == 0
    assert _count(store, 'edit_applications') == 0
    assert store.evidence(evidence)['editProposal']['status'] == 'approved'


@pytest.mark.parametrize("native_os", [pytest.param("linux", marks=pytest.mark.linux_only),
                                      pytest.param("macos", marks=pytest.mark.macos_only)])
@pytest.mark.parametrize('stop', ['cancel', 'expire', 'revoke'])
def test_validation_fences_cancellation_expiry_and_policy_revocation(tmp_path, stop, native_os):
    store, source, objective = _setup(tmp_path)
    evidence = project_proposal(store, source)
    _review(store)
    store.finish(store.claim_next(), {})
    validation = store.claim_next()
    assert validation['type'] == 'request.validate'
    if stop == 'cancel':
        store.cancel(objective['id'])
        assert not store.finish(validation, {})
    elif stop == 'expire':
        with store._write() as conn:
            conn.execute('UPDATE requests SET lease=? WHERE id=?', (time.time() - 1, validation['id']))
        assert not store.finish(validation, {})
        assert store.recover_expired() == 1
        assert store.retry(validation['id'])
        retried = store.claim_next()
        assert retried['id'] == validation['id'] and retried['attempts'] == 2
        assert store.finish(retried, {})
        assert _count(store, 'project_validation_receipts') == 1
        return
    else:
        revoked = OrganizationStore(store.path, replace(store.settings, tool_grants=('read_file',)))
        assert not store.finish(validation, {})
        assert revoked.snapshot()['objectives'][0]['status'] == 'needs_input'
    assert _count(store, 'project_validation_receipts') == 0
    assert store.evidence(evidence)['editProposal']['status'] == 'applied'


@pytest.mark.parametrize("native_os", [pytest.param("linux", marks=pytest.mark.linux_only),
                                      pytest.param("macos", marks=pytest.mark.macos_only)])
def test_successive_same_path_edits_supersede_merge_gate_and_verify_final_manifest(tmp_path, native_os):
    store, source, objective = _setup(tmp_path)
    first = project_proposal(store, source)
    apply_validate(store)
    first_merge = next(r for r in store.snapshot()['requests'] if r['type'] == 'request.merge')
    # A bounded replan preserves managed revisions and the historical proposal.
    store.resolve(first_merge['id'], 'request_replan', 'Refine the first file again', idempotency_key='replan')
    plan = store.claim_next()
    assert plan['type'] == 'request.plan'
    store.finish(plan, {'workers': 1, 'tasks': [{'title': 'Refine', 'description': 'Refine current output',
                                             'type': 'work.edit', 'team': 'engineering'}]})
    second = project_proposal(store, source, second=False, old='new value', new='final value')
    apply_validate(store)
    snapshot = store.snapshot()
    old = next(r for r in snapshot['requests'] if r['id'] == first_merge['id'])
    assert old['status'] == 'cancelled'
    merges = [r for r in snapshot['requests'] if r['type'] == 'request.merge' and r['status'] == 'pending_intervention']
    assert len(merges) == 1
    second_proposal = store.evidence(second)['editProposal']
    first_proposal = store.evidence(first)['editProposal']
    source.write_bytes(second_proposal['newContent'].encode('utf-8'))
    (source.parent / 'config.json').write_bytes(first_proposal['files'][1]['newContent'].encode('utf-8'))
    store.resolve(merges[0]['id'], 'record_handoff', 'Applied final latest output', [second], idempotency_key='final')
    for evidence in (first, second):
        observed = store.evidence(evidence)['editProposal']['sourceVerificationReceipt']
        assert observed['status'] == 'verified'
        assert {r['path']: r['revision'] for r in observed['files']} == {PATH: 2, 'root0/config.json': 1}
        assert len(observed['coveredProposalIds']) == 2


@pytest.mark.parametrize('check', [
    {'kind': 'shell', 'path': PATH, 'command': 'pytest'},
    {'kind': 'json_value', 'path': PATH, 'pointer': '/~x', 'equals': True},
    {'kind': 'json_value', 'path': PATH, 'pointer': '', 'equals': float('nan')},
    {'kind': 'sha256', 'path': PATH, 'equals': 'bad'},
    {'kind': 'text_contains', 'path': PATH, 'text': ''},
    {'kind': 'json_valid', 'path': 'root0/unread.json'},
])
def test_invalid_validation_never_enters_proposal(check):
    output = {'summary': 'Change', 'edit': {'path': PATH, 'baseRevision': 0, 'baseSha256': 'a' * 64,
                                          'oldText': 'old', 'newText': 'new'}, 'validations': [check]}
    with pytest.raises(ValueError):
        parse_edit_result(output)


@pytest.mark.parametrize('content,kind,expected', [
    ('{"x":true}', 'json_valid', True), ('{"x":1,"x":2}', 'json_valid', False),
    ('{"x":NaN}', 'json_valid', False), ('raise RuntimeError("never execute")', 'python_syntax', True),
    ('if :', 'python_syntax', False),
])
def test_validators_parse_without_executing_and_report_exact_limits(content, kind, expected):
    files = [{'path': PATH, 'revision': 1, 'sha256': hashlib.sha256(content.encode()).hexdigest(), 'content': content}]
    result = validate_manifest(files, [{'kind': kind, 'path': PATH}])
    assert result['checks'][-1]['passed'] is expected
    assert result['status'] == ('passed' if expected else 'failed')
    assert result['notExecuted'] == ['project_commands', 'functional_tests']


@pytest.mark.parametrize("native_os", [pytest.param("linux", marks=pytest.mark.linux_only),
                                      pytest.param("macos", marks=pytest.mark.macos_only)])
def test_same_round_successive_edits_keep_one_actionable_merge_and_immutable_supersession(tmp_path, native_os):
    store, source, _ = _setup(tmp_path, tasks=2)
    with store._write() as conn:
        tasks = conn.execute('SELECT id FROM tasks ORDER BY rowid').fetchall()
        conn.execute('UPDATE tasks SET dependencies=? WHERE id=?', (json.dumps([tasks[0]['id']]), tasks[1]['id']))
    first = project_proposal(store, source)
    apply_validate(store)
    earlier = next(row for row in store.snapshot()['requests'] if row['type'] == 'request.merge')
    second = project_proposal(store, source, second=False, old='new value', new='final value')
    apply_validate(store)
    requests = store.snapshot()['requests']
    old = next(row for row in requests if row['id'] == earlier['id'])
    assert old['status'] == 'cancelled' and 'not verified' in old['reason']
    latest = next(row for row in requests if row['type'] == 'request.merge' and row['status'] == 'pending_intervention')
    with store._connect() as conn:
        receipt = conn.execute('SELECT * FROM project_merge_supersessions').fetchone()
        assert receipt['request_id'] == earlier['id'] and receipt['superseded_by'] == latest['id']
    with pytest.raises(ValueError):
        store.resolve(earlier['id'], 'record_handoff', 'Old gate cannot be verified', [first], idempotency_key='obsolete')
    source.write_bytes(store.evidence(second)['editProposal']['newContent'].encode())
    (source.parent / 'config.json').write_bytes(store.evidence(first)['editProposal']['files'][1]['newContent'].encode())
    assert store.resolve(latest['id'], 'record_handoff', 'Final manifest applied', [second], idempotency_key='latest')
    assert store.claim_next()['type'] == 'request.integrate'
    assert store.evidence(first)['editProposal']['sourceVerificationReceipt']['status'] == 'verified'


@pytest.mark.parametrize("native_os", [pytest.param("linux", marks=pytest.mark.linux_only),
                                      pytest.param("macos", marks=pytest.mark.macos_only)])
def test_source_handoff_refuses_symlinks_and_wrong_evidence_without_receipt(tmp_path, native_os):
    store, source, _ = _setup(tmp_path)
    evidence = project_proposal(store, source, second=False)
    apply_validate(store)
    merge = next(row for row in store.snapshot()['requests'] if row['type'] == 'request.merge')
    source.write_bytes(store.evidence(evidence)['editProposal']['newContent'].encode())
    with pytest.raises(ValueError, match='exact'):
        store.resolve(merge['id'], 'record_handoff', 'Owner text cannot change target', ['evidence-forged'], idempotency_key='wrong')
    outside = tmp_path / 'outside'
    source.rename(outside)
    source.symlink_to(outside)
    with pytest.raises(ValueError, match='symlink'):
        store.resolve(merge['id'], 'record_handoff', 'Follow my link', [evidence], idempotency_key='link')
    assert _count(store, 'project_handoff_receipts') == 0


@pytest.mark.parametrize("native_os", [pytest.param("linux", marks=pytest.mark.linux_only),
                                      pytest.param("macos", marks=pytest.mark.macos_only)])
@pytest.mark.parametrize('two_files', [False, True])
def test_successful_replan_supersedes_failed_checks_without_erasing_failed_history(tmp_path, two_files, native_os):
    store, source, _ = _setup(tmp_path)
    failed_evidence = project_proposal(store, source, second=two_files,
        checks=[{'kind': 'text_absent', 'path': PATH, 'text': 'new value'}])
    apply_validate(store)
    failed = next(row for row in store.snapshot()['requests'] if row['type'] == 'request.validation_failed')
    store.resolve(failed['id'], 'request_replan', 'Correct the failed output', idempotency_key='correct')
    plan = store.claim_next()
    store.finish(plan, {'workers': 1, 'tasks': [{'title': 'Correct', 'description': 'Correct assertion',
                                             'type': 'work.edit', 'team': 'engineering'}]})
    fixed_evidence = project_proposal(store, source, second=False, old='new value', new='final value',
        checks=[{'kind': 'text_contains', 'path': PATH, 'text': 'final value'}])
    apply_validate(store)
    gate = next(row for row in store.snapshot()['requests'] if row['type'] == 'request.merge' and row['status'] == 'pending_intervention')
    assert any(action['action'] == 'record_handoff' for action in gate['allowedResolutions'])
    fixed = store.evidence(fixed_evidence)['editProposal']
    source.write_bytes(fixed['newContent'].encode())
    if two_files:
        (source.parent / 'config.json').write_bytes(store.evidence(failed_evidence)['editProposal']['files'][1]['newContent'].encode())
    store.resolve(gate['id'], 'record_handoff', 'Applied corrected final output', [fixed_evidence], idempotency_key='corrected')
    historical = store.evidence(failed_evidence)['editProposal']
    assert historical['validationReceipt']['status'] == 'failed'
    assert historical['sourceVerificationReceipt'] is None
    handoff = store.evidence(fixed_evidence)['editProposal']['sourceVerificationReceipt']
    assert handoff['status'] == 'verified' and handoff['manifestValidation']['status'] == 'passed'
    assert any(row['status'] == 'failed' for row in handoff['validations'])
    with store._connect() as conn:
        store.verify_project_objective_acceptance(conn, gate['objectiveId'])


@pytest.mark.parametrize("native_os", [pytest.param("linux", marks=pytest.mark.linux_only),
                                      pytest.param("macos", marks=pytest.mark.macos_only)])
def test_replan_cannot_discard_unverified_retained_source_obligation(tmp_path, native_os):
    store, source, objective = _setup(tmp_path)
    evidence = project_proposal(store, source, second=False)
    apply_validate(store)
    first_gate = next(row for row in store.snapshot()['requests'] if row['type'] == 'request.merge')
    store.resolve(first_gate['id'], 'request_replan', 'Reconsider next steps', idempotency_key='again')
    # Even a later text-only plan cannot erase retained source-project output.
    with store._write() as conn:
        assert store.ensure_source_handoff(conn, objective['id']) is True
    gates = [row for row in store.snapshot()['requests'] if row['type'] == 'request.merge' and row['status'] == 'pending_intervention']
    assert len(gates) == 1 and gates[0]['id'] != first_gate['id']
    with store._connect() as conn:
        with pytest.raises(ValueError, match='Source-project acceptance'):
            store.verify_project_objective_acceptance(conn, objective['id'])
    assert b'old value' in source.read_bytes()


@pytest.mark.parametrize("native_os", [pytest.param("linux", marks=pytest.mark.linux_only),
                                      pytest.param("macos", marks=pytest.mark.macos_only)])
def test_draft_only_replan_cannot_accept_while_original_project_is_unchanged(tmp_path, native_os):
    store, source, objective = _setup(tmp_path)
    evidence = project_proposal(store, source, second=False)
    apply_validate(store)
    gate = next(row for row in store.snapshot()['requests'] if row['type'] == 'request.merge')
    settings = replace(store.settings, capabilities=('work.edit', 'work.draft'),
                       roster=tuple(replace(staff, capabilities=('work.edit', 'work.draft')) for staff in store.settings.roster))
    store = OrganizationStore(store.path, settings)
    store.resolve(gate['id'], 'request_replan', 'Write a summary of progress', idempotency_key='summary-plan')
    plan = store.claim_next()
    store.finish(plan, {'workers': 1, 'tasks': [{'title': 'Summarize', 'description': 'Summarize progress',
                                             'type': 'work.draft', 'team': 'engineering'}]})
    work = store.claim_next()
    assert work['type'] == 'work.draft'
    store.finish(work, {'summary': 'Prepared progress summary', 'deliverable': 'The managed edit is ready for source integration.'})
    review = store.claim_next()
    assert review['type'] == 'request.review'
    store.finish(review, {'approved': True, 'summary': 'Summary is supported.',
                          'evidenceIds': json.loads(review['payload'])['evidenceIds']})
    snapshot = store.snapshot()
    assert snapshot['objectives'][0]['status'] == 'needs_input'
    assert not any(row['type'] == 'request.integrate' for row in snapshot['requests'])
    current_gate = next(row for row in snapshot['requests'] if row['type'] == 'request.merge' and row['status'] == 'pending_intervention')
    assert current_gate['id'] != gate['id']
    assert b'old value' in source.read_bytes()
    source.write_bytes(store.evidence(evidence)['editProposal']['newContent'].encode())
    store.resolve(current_gate['id'], 'record_handoff', 'Applied the retained exact project output', [evidence], idempotency_key='summary-merge')
    assert store.claim_next()['type'] == 'request.integrate'


def legacy_applied_fixture(tmp_path, *, corrupt=False):
    """Materialize the old single-file record format without new project tables."""
    from eidolon_cli.organization_edits import _proposal_digest
    store, source, objective = _setup(tmp_path)
    evidence = project_proposal(store, source, second=False)
    _review(store)
    store.finish(store.claim_next(), {})
    with store._write() as conn:
        proposal = conn.execute('SELECT * FROM edit_proposals').fetchone()
        payload = {'proposalId': proposal['id'], 'proposalSha256': proposal['sha256'],
                   'taskId': proposal['task_id'], 'workspaceId': proposal['workspace_id'],
                   'sourcePath': proposal['path'], 'appliedRevision': 1}
        merge = store._request(conn, objective['id'], 'request.merge', 'engineering', 3, payload=payload)
        store._pending(conn, conn.execute('SELECT * FROM requests WHERE id=?', (merge,)).fetchone(),
                       'The reviewed edit was committed to the managed workspace. Original source files are unchanged.')
        conn.execute("UPDATE tasks SET status='completed'")
    # New tables are absent in the fixture, and all exact single-file digests use
    # version 1. These are data-format operations, not imports of old source code.
    with sqlite3.connect(store.path) as conn:
        conn.row_factory = sqlite3.Row
        proposal = conn.execute('SELECT * FROM edit_proposals').fetchone()
        legacy_hash = _proposal_digest(proposal)
        for table in ('edit_proposals', 'edit_reviews', 'edit_applications'):
            conn.execute(f'DROP TRIGGER immutable_{table}_update')
        conn.execute('UPDATE edit_proposals SET sha256=?', ('0' * 64 if corrupt else legacy_hash,))
        conn.execute('UPDATE edit_reviews SET proposal_sha256=?', (legacy_hash,))
        conn.execute('UPDATE edit_applications SET proposal_sha256=?', (legacy_hash,))
        for row in conn.execute('SELECT id,payload FROM requests').fetchall():
            payload = json.loads(row['payload'])
            if 'proposalSha256' in payload:
                payload['proposalSha256'] = legacy_hash
                conn.execute('UPDATE requests SET payload=? WHERE id=?', (json.dumps(payload), row['id']))
        conn.execute("DELETE FROM request_policy WHERE request_id IN (SELECT id FROM requests WHERE type='request.validate')")
        conn.execute("DELETE FROM requests WHERE type='request.validate'")
        for table in ('edit_project_specs', 'project_validation_receipts', 'project_handoff_receipts',
                      'project_merge_supersessions', 'objective_task_rounds', 'objective_control', 'objective_usage'):
            conn.execute(f'DROP TABLE {table}')
        conn.execute("UPDATE agents SET accepts='[\"request.apply\"]' WHERE id LIKE 'control:apply%'")
    return store.path, store.settings, source, objective, evidence, merge


@pytest.mark.parametrize("native_os", [pytest.param("linux", marks=pytest.mark.linux_only),
                                      pytest.param("macos", marks=pytest.mark.macos_only)])
def test_legacy_open_applied_merge_migrates_exact_validation_once_without_reapplication(tmp_path, native_os):
    path, settings, source, objective, evidence, merge = legacy_applied_fixture(tmp_path)
    original = source.read_bytes()
    store = OrganizationStore(path, settings)
    validate = store.claim_next()
    assert validate['type'] == 'request.validate' and json.loads(validate['payload'])['legacyApplication'] is True
    assert _count(store, 'edit_applications') == 1
    assert _count(store, 'workspace_revisions') == 2
    assert store.finish(validate, {})
    proof = store.evidence(evidence)['editProposal']
    assert proof['validationReceipt']['status'] == 'passed'
    assert source.read_bytes() == original
    reopened = OrganizationStore(path, settings)
    requests = reopened.snapshot()['requests']
    assert sum(row['type'] == 'request.validate' for row in requests) == 1
    gate = next(row for row in requests if row['id'] == merge)
    assert gate['status'] == 'pending_intervention'
    source.write_bytes(proof['newContent'].encode())
    assert reopened.resolve(merge, 'record_handoff', 'Applied the retained legacy output', [evidence], idempotency_key='legacy-verified')
    assert reopened.claim_next()['type'] == 'request.integrate'


@pytest.mark.parametrize("native_os", [pytest.param("linux", marks=pytest.mark.linux_only),
                                      pytest.param("macos", marks=pytest.mark.macos_only)])
def test_corrupt_legacy_application_stays_visible_and_never_reapplies(tmp_path, native_os):
    path, settings, source, _, _, merge = legacy_applied_fixture(tmp_path, corrupt=True)
    original = source.read_bytes()
    store = OrganizationStore(path, settings)
    assert store.claim_next() is None
    with store._connect() as conn:
        gate = conn.execute('SELECT * FROM requests WHERE id=?', (merge,)).fetchone()
        assert gate['status'] == 'pending_intervention' and 'incomplete' in gate['reason']
        assert not conn.execute("SELECT 1 FROM requests WHERE type='request.validate'").fetchone()
    assert _count(store, 'edit_applications') == 1 and source.read_bytes() == original


@pytest.mark.parametrize("native_os", [pytest.param("linux", marks=pytest.mark.linux_only),
                                      pytest.param("macos", marks=pytest.mark.macos_only)])
@pytest.mark.parametrize('two_files', [False, True])
def test_unknown_superseded_validation_stays_unknown_without_poisoning_repaired_output(tmp_path, native_os, two_files):
    store, source, _ = _setup(tmp_path)
    original_evidence = project_proposal(store, source, second=two_files)
    _review(store)
    store.finish(store.claim_next(), {})
    stopped = store.claim_next()
    assert stopped['type'] == 'request.validate'
    assert store.fail(stopped, 'Backend stopped before validation receipt committed')
    store.resolve(stopped['id'], 'request_replan', 'Repair and validate latest output', idempotency_key='unknown-replan')
    plan = store.claim_next()
    store.finish(plan, {'workers': 1, 'tasks': [{'title': 'Repair', 'description': 'Verify final output',
                                             'type': 'work.edit', 'team': 'engineering'}]})
    final_evidence = project_proposal(store, source, second=False, old='new value', new='final value')
    apply_validate(store)
    gate = next(row for row in store.snapshot()['requests'] if row['type'] == 'request.merge' and row['status'] == 'pending_intervention')
    action = next(action for action in gate['allowedResolutions'] if action['action'] == 'record_handoff')
    expected = {PATH: (2, final_evidence)}
    if two_files:
        expected['root0/config.json'] = (1, original_evidence)
    assert {item['path']: (item['revision'], item['evidenceId']) for item in action['sourceManifest']} == expected
    assert all(set(item) == {'path', 'revision', 'sha256', 'proposalId', 'evidenceId'} for item in action['sourceManifest'])
    source.write_bytes(store.evidence(final_evidence)['editProposal']['newContent'].encode())
    if two_files:
        (source.parent / 'config.json').write_bytes(store.evidence(original_evidence)['editProposal']['files'][1]['newContent'].encode())
    store.resolve(gate['id'], 'record_handoff', 'Applied the exact final manifest', [final_evidence], idempotency_key='unknown-fixed')
    original = store.evidence(original_evidence)['editProposal']
    assert original['validationReceipt'] is None and original['sourceVerificationReceipt'] is None
    final = store.evidence(final_evidence)['editProposal']['sourceVerificationReceipt']
    assert final['manifestValidation']['status'] == 'passed'
    assert any(row['status'] == 'unknown' and row['validationSha256'] is None for row in final['validations'])


@pytest.mark.parametrize("native_os", [pytest.param("linux", marks=pytest.mark.linux_only),
                                      pytest.param("macos", marks=pytest.mark.macos_only)])
def test_granted_discovery_and_reads_link_real_durable_evidence(tmp_path, native_os):
    from types import SimpleNamespace
    from eidolon_cli.organization_tool_executor import tool_execution, validate_retained_receipts
    from tests.eidolon_cli.test_organization_tool_executor import _agent, _call
    store, source, objective = _setup(tmp_path)
    grants = ('read_file', 'patch', 'list_files', 'search_files')
    settings = replace(store.settings, tool_grants=grants,
                       roster=tuple(replace(staff, tool_grants=grants) for staff in store.settings.roster))
    store = OrganizationStore(store.path, settings)
    claim = store.claim_next()
    context = store.context(claim)
    context.update(recordToolStart=lambda *args: store.record_tool_start(claim, *args),
                   recordToolFinish=lambda *args: store.record_tool_finish(claim, *args),
                   requestToolReceipts=lambda: store.request_tool_receipts(claim),
                   resolveWorkspaceSource=lambda path, loader: store.capture_workspace_source(claim, path, loader))
    with tool_execution(context, 'work.edit') as execution:
        agent, messages = _agent(execution), []
        calls = [_call('root0', name='list_files', call_id='list'),
                 _call('root0', name='search_files', call_id='search', query='old value'),
                 _call(PATH, call_id='read')]
        agent._execute_tool_calls(SimpleNamespace(tool_calls=calls), messages, 'real-project')
        read = json.loads(messages[-1]['content'])
        edit = {'path': PATH, 'baseRevision': read['workspaceRevision'], 'baseSha256': read['sourceSha256'],
                'oldText': 'old value', 'newText': 'new value'}
        execution.verify_completion(edit)
    store.finish(claim, {'summary': 'Observed and proposed exact output', 'edit': edit})
    proof = store.context(store.claim_next())['evidence'][0]
    assert [row['toolName'] for row in proof['toolReceipts']] == ['list_files', 'search_files', 'read_file']
    validate_retained_receipts(proof['toolReceipts'])
    assert all(row['status'] == 'completed' for row in proof['toolReceipts'])


@pytest.mark.parametrize("native_os", [pytest.param("linux", marks=pytest.mark.linux_only),
                                      pytest.param("macos", marks=pytest.mark.macos_only)])
def test_cancelled_discovery_outcome_is_unknown_and_cannot_be_replayed(tmp_path, native_os, monkeypatch):
    from types import SimpleNamespace
    from eidolon_cli.organization_tool_executor import tool_execution
    from tests.eidolon_cli.test_organization_tool_executor import _agent, _call
    store, source, objective = _setup(tmp_path)
    grants = ('read_file', 'patch', 'list_files')
    settings = replace(store.settings, tool_grants=grants,
                       roster=tuple(replace(staff, tool_grants=grants) for staff in store.settings.roster))
    store = OrganizationStore(store.path, settings)
    claim = store.claim_next()
    context = store.context(claim)
    context.update(recordToolStart=lambda *args: store.record_tool_start(claim, *args),
                   recordToolFinish=lambda *args: store.record_tool_finish(claim, *args),
                   requestToolReceipts=lambda: store.request_tool_receipts(claim),
                   resolveWorkspaceSource=lambda path, loader: store.capture_workspace_source(claim, path, loader))
    with tool_execution(context, 'work.edit') as execution:
        agent, messages = _agent(execution), []
        original = execution.scope.discover_files
        def cancel_after_read(*args, **kwargs):
            result = original(*args, **kwargs)
            store.cancel(objective['id'])
            agent._organization_cancel.set()
            return result
        monkeypatch.setattr(execution.scope, 'discover_files', cancel_after_read)
        with pytest.raises(InterruptedError, match='cancelled'):
            agent._execute_tool_calls(SimpleNamespace(tool_calls=[_call('root0', name='list_files')]), messages, 'task')
        assert messages == []
    receipts = store.tool_receipts(claim['id'])
    assert receipts[0]['status'] == 'unknown' and 'result' not in receipts[0]
    with pytest.raises(ValueError, match='lease'):
        store.record_tool_start(claim, receipts[0]['toolCallId'], 'list_files', receipts[0]['arguments'])


@pytest.mark.parametrize("native_os", [pytest.param("linux", marks=pytest.mark.linux_only),
                                      pytest.param("macos", marks=pytest.mark.macos_only)])
def test_managed_only_partial_repair_has_immutable_aggregate_validation_artifact(tmp_path, native_os):
    from eidolon_cli.organization_project_workspace import project_validation_view, project_validation_artifact
    store, source, objective = _setup(tmp_path)
    with store._write() as conn:
        conn.execute("UPDATE objective_control SET delivery_mode='managed_artifact' WHERE objective_id=?", (objective['id'],))
    original_evidence = project_proposal(store, source)
    _review(store)
    store.finish(store.claim_next(), {})
    validation = store.claim_next()
    store.fail(validation, 'Validation outcome not committed')
    store.resolve(validation['id'], 'request_replan', 'Repair the managed artifact', idempotency_key='repair-artifact')
    plan = store.claim_next()
    store.finish(plan, {'workers': 1, 'tasks': [{'title': 'Repair', 'description': 'Repair first file, preserve second',
                                             'type': 'work.edit', 'team': 'engineering'}]})
    final_evidence = project_proposal(store, source, second=False, old='new value', new='final value',
        checks=[{'kind': 'text_contains', 'path': PATH, 'text': 'final value'}])
    apply_validate(store)
    with store._write() as conn:
        proof = store.prepare_project_acceptance(conn, objective['id'])
        repeated = store.prepare_project_acceptance(conn, objective['id'])
        assert proof['id'] == repeated['id']
        assert project_validation_view(conn, objective['id']) == proof
        artifact = project_validation_artifact(conn, proof['id'])
        with pytest.raises(sqlite3.IntegrityError, match='Immutable'):
            conn.execute("UPDATE objective_project_validations SET result='{}'")
    assert artifact['sha256'] == hashlib.sha256(artifact['content'].encode()).hexdigest()
    assert proof['scope'] == 'latest_managed_project_heads' and proof['status'] == 'passed'
    assert proof['filesCount'] == 2 and proof['checksCount'] == 3
    assert {item['path']: item['evidenceId'] for item in proof['manifest']} == {PATH: final_evidence, 'root0/config.json': original_evidence}
    assert any(row['status'] == 'unknown' for row in proof['historicalValidations'])
    assert proof['notExecuted'] == ['project_commands', 'functional_tests']
    assert store.evidence(original_evidence)['editProposal']['validationReceipt'] is None
    assert not any(row['type'] == 'request.merge' for row in store.snapshot()['requests'])
    assert b'old value' in source.read_bytes()
    assert json.loads((source.parent / 'config.json').read_text())['enabled'] is False
    assert _count(store, 'objective_project_validations') == 1
