"""Exact reviewed workspace revisions exercised through independent SQLite stores."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
import sqlite3
import subprocess
import threading
import time

import pytest

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_edits import MAX_EDIT_BYTES, _diff
from eidolon_cli.organization_store import OrganizationStore
from tools.organization_file_read import organization_file_read_scope


SOURCE = '  café\r\nold value\r\n\r\n'
PATH = 'root0/example.txt'


def _setup(tmp_path, *, tasks=1, content=SOURCE):
    project = tmp_path / 'project'
    project.mkdir()
    source = project / 'example.txt'
    source.write_bytes(content.encode('utf-8'))
    settings = OrganizationSettings.from_config({'organization': {
        'capabilities': ['work.edit'], 'tool_grants': ['read_file', 'patch'],
        'read_roots': [str(project)], 'max_tool_result_chars': 20000,
        'roster': [{'id': f'editor-{i}', 'name': f'Editor {i}', 'team': 'engineering',
                    'capabilities': ['work.edit'], 'tool_grants': ['read_file', 'patch']}
                   for i in range(2)]}})
    store = OrganizationStore(tmp_path / 'state.db', settings)
    objective = store.create_objective('Improve the project', priority='high', idempotency_key='one')
    plan = store.claim_next()
    store.finish(plan, {'workers': tasks, 'tasks': [{'title': f'Edit {i}', 'description': 'Replace old value.',
         'type': 'work.edit', 'team': 'engineering'} for i in range(tasks)]})
    hire = store.claim_next()
    assert hire['type'] == 'request.hire'
    store.finish(hire, {})
    return store, source, objective


def _read(store, claim, *, call='source', path=PATH, offset=1, limit=2000):
    args = {'path': path, 'offset': offset, 'limit': limit}
    receipt = store.record_tool_start(claim, call, 'read_file', args)
    with organization_file_read_scope(store.settings.read_roots, store.settings.max_tool_result_chars,
             resolve_workspace_source=lambda selected, loader: store.capture_workspace_source(claim, selected, loader)) as scope:
        raw = scope.read_file(**args)
    result = json.loads(raw)
    assert result['success'], result
    store.record_tool_finish(claim, receipt['id'], raw, 'completed')
    return result, receipt


def _propose(store, claim=None, *, old='old value', new='new value'):
    claim = claim or store.claim_next()
    assert claim['type'] == 'work.edit'
    source, receipt = _read(store, claim)
    result = {'summary': 'Prepared exact edit', 'edit': {'path': PATH,
             'baseRevision': source['workspaceRevision'], 'baseSha256': source['sourceSha256'],
             'oldText': old, 'newText': new}}
    assert store.finish(claim, result)
    with store._connect() as conn:
        evidence = conn.execute('SELECT id FROM evidence WHERE request_id=?', (claim['id'],)).fetchone()['id']
    return claim, store.evidence(evidence), result, receipt


def _review(store, *, approved=True):
    claim = store.claim_next()
    assert claim['type'] == 'request.review'
    payload = json.loads(claim['payload'])
    result = {'approved': approved, 'summary': 'Verified exact change' if approved else 'Please revise the replacement',
              'evidenceIds': payload['evidenceIds'], 'proposalId': payload['proposalId'],
              'proposalSha256': payload['proposalSha256']}
    assert store.finish(claim, result)
    return claim, result


def _head(store, objective):
    with store._connect() as conn:
        return dict(conn.execute('SELECT v.* FROM workspace_revisions v JOIN workspace_heads h '
                                 'USING(objective_id,path,revision) WHERE objective_id=? AND path=?',
                                 (objective['id'], PATH)).fetchone())


def _count(store, table):
    with store._connect() as conn:
        return conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0]


def test_exact_utf8_application_is_durable_idempotent_and_keeps_source_merge_explicit(tmp_path):
    store, source, objective = _setup(tmp_path)
    claim, evidence, result, _ = _propose(store, new=' new\tvalue ')
    proposal = evidence['editProposal']
    expected = SOURCE.replace('old value', ' new\tvalue ')
    assert proposal['baseContent'] == SOURCE and proposal['newContent'] == expected
    assert proposal['baseSha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert proposal['newSha256'] == hashlib.sha256(expected.encode()).hexdigest()
    assert proposal['baseRevision'] == proposal['currentRevision'] == 0
    assert proposal['workspaceId'] == objective['id']
    assert '+ new\tvalue \r\n' in proposal['diff'] and '-old value\r\n' in proposal['diff']
    assert 'diff' not in evidence['content'] or proposal['diff'] not in evidence['content']
    assert not store.finish(claim, result)
    review, _ = _review(store)
    apply = store.claim_next()
    assert apply['type'] == 'request.apply' and apply['team'] == claim['team'] and apply['priority'] == claim['priority']
    assert apply['agent_id'] != claim['agent_id'] != review['agent_id']
    assert store.finish(apply, {})
    # Simulate a lost acknowledgement: another process observes the committed
    # receipt, and a repeated finish cannot apply again.
    reopened = OrganizationStore(store.path, store.settings)
    assert not reopened.finish(apply, {})
    saved = reopened.evidence(evidence['id'])['editProposal']
    assert saved['status'] == 'applied' and saved['reviewStatus'] == 'approved'
    assert saved['appliedRevision'] == saved['currentRevision'] == 1
    assert saved['appliedAt']
    assert _head(reopened, objective)['content'] == expected
    assert _count(reopened, 'edit_applications') == 1
    snapshot = reopened.snapshot()
    assert snapshot['objectives'][0]['status'] == 'needs_input'
    assert snapshot['tasks'][0]['status'] == 'completed'
    merge = next(row for row in snapshot['requests'] if row['type'] == 'request.merge')
    assert merge['taskId'] is None and merge['status'] == 'pending_intervention'
    assert merge['team'] == claim['team'] and merge['priority'] == claim['priority']
    assert 'Original source files are unchanged' in merge['reason']
    assert source.read_bytes() == SOURCE.encode()
    # Cancellation after commit cannot describe that immutable revision undone.
    assert reopened.cancel(objective['id'])
    assert reopened.evidence(evidence['id'])['editProposal']['status'] == 'applied'
    assert _head(reopened, objective)['content'] == expected


@pytest.mark.parametrize('problem', ['no_read', 'invented_revision', 'wrong_hash', 'ambiguous', 'empty_old', 'no_change',
                                      'oversized_new', 'invented_diff', 'receipt_hash', 'receipt_revision', 'receipt_path'])
def test_proposals_require_exact_bounded_source_and_current_persisted_receipt(tmp_path, problem):
    store, _, objective = _setup(tmp_path, content='old old\r\nunique\r\n')
    claim = store.claim_next()
    source, receipt = _read(store, claim)
    edit = {'path': PATH, 'baseRevision': source['workspaceRevision'], 'baseSha256': source['sourceSha256'],
            'oldText': 'unique', 'newText': 'better'}
    if problem == 'no_read':
        with store._write() as conn:
            conn.execute('DELETE FROM tool_receipts WHERE id=?', (receipt['id'],))
    elif problem == 'invented_revision':
        edit['baseRevision'] += 99
    elif problem == 'wrong_hash':
        edit['baseSha256'] = '0' * 64
    elif problem == 'ambiguous':
        edit['oldText'] = 'old'
    elif problem == 'empty_old':
        edit['oldText'] = ''
    elif problem == 'no_change':
        edit['newText'] = 'unique'
    elif problem == 'oversized_new':
        edit['newText'] = 'é' * (MAX_EDIT_BYTES // 2 + 1)
    elif problem == 'invented_diff':
        edit['diff'] = 'trust this model diff'
    else:
        with store._write() as conn:
            row = conn.execute('SELECT * FROM tool_receipts WHERE id=?', (receipt['id'],)).fetchone()
            raw = json.loads(row['result'])
            if problem == 'receipt_hash':
                raw['sourceSha256'] = '0' * 64
            elif problem == 'receipt_revision':
                raw['workspaceRevision'] += 1
            else:
                raw['path'] = 'root0/different.txt'
            serialized = json.dumps(raw)
            conn.execute('UPDATE tool_receipts SET result=?,result_sha256=? WHERE id=?',
                         (serialized, hashlib.sha256(serialized.encode()).hexdigest(), receipt['id']))
    with pytest.raises(ValueError):
        store.finish(claim, {'summary': 'Rejected mutation', 'edit': edit})
    assert _head(store, objective)['revision'] == 0
    assert _count(store, 'edit_proposals') == _count(store, 'evidence') == 0


@pytest.mark.parametrize('tamper', ['proposal_hash', 'proposal_bytes', 'review_hash', 'reviewer', 'evidence_bytes', 'review_rejected'])
def test_apply_rejects_changed_proposal_review_or_evidence_atomically(tmp_path, tamper):
    store, source, objective = _setup(tmp_path)
    _, evidence, _, _ = _propose(store)
    review, _ = _review(store)
    apply = store.claim_next()
    with store._write() as conn:
        if tamper.startswith('proposal_'):
            # Simulate out-of-band corruption beyond normal immutable SQL APIs.
            conn.execute('DROP TRIGGER immutable_edit_proposals_update')
            field, value = ('sha256', '0' * 64) if tamper == 'proposal_hash' else ('new_content', 'Changed externally')
            conn.execute(f'UPDATE edit_proposals SET {field}=?', (value,))
        elif tamper == 'review_hash':
            payload = json.loads(apply['payload'])
            payload['proposalSha256'] = '0' * 64
            conn.execute('UPDATE requests SET payload=? WHERE id=?', (json.dumps(payload), apply['id']))
        elif tamper == 'reviewer':
            conn.execute('UPDATE reviews SET agent_id=? WHERE request_id=?', ('editor-0', review['id']))
        elif tamper == 'evidence_bytes':
            conn.execute('UPDATE evidence SET content=? WHERE id=?', ('changed', evidence['id']))
        else:
            conn.execute('UPDATE reviews SET approved=0 WHERE request_id=?', (review['id'],))
    with pytest.raises(ValueError):
        store.finish(apply, {})
    assert _count(store, 'edit_applications') == 0 and _head(store, objective)['revision'] == 0
    assert source.read_bytes() == SOURCE.encode()


def test_reviewer_must_echo_exact_proposal_and_rejection_does_not_queue_application(tmp_path):
    store, _, _ = _setup(tmp_path)
    _, evidence, _, _ = _propose(store)
    review = store.claim_next()
    payload = json.loads(review['payload'])
    result = {'approved': True, 'summary': 'Approve exact proposal', 'evidenceIds': payload['evidenceIds']}
    with pytest.raises(ValueError, match='exact proposal'):
        store.finish(review, result)
    assert _count(store, 'reviews') == 0
    result.update({'proposalId': payload['proposalId'], 'proposalSha256': '0' * 64})
    with pytest.raises(ValueError, match='exact proposal'):
        store.finish(review, result)
    result.update({'proposalSha256': payload['proposalSha256'], 'approved': False, 'summary': 'Need another approach'})
    assert store.finish(review, result)
    retained = store.evidence(evidence['id'])['editProposal']
    assert retained['reviewStatus'] == 'rejected' and retained['reviewReason'] == result['summary']
    assert not any(r['type'] == 'request.apply' for r in store.snapshot()['requests'])
    assert store.claim_next()['type'] == 'work.edit'


@pytest.mark.parametrize('change', ['org_patch', 'author_patch', 'disabled', 'retired', 'root_reorder'])
def test_revoked_authority_or_root_rebinding_blocks_preclaim_and_inflight_apply(tmp_path, change):
    store, source, objective = _setup(tmp_path)
    _, evidence, _, _ = _propose(store)
    _review(store)
    apply = store.claim_next()
    settings = store.settings
    if change == 'org_patch':
        settings = replace(settings, tool_grants=('read_file',))
    elif change == 'author_patch':
        settings = replace(settings, roster=tuple(replace(staff, tool_grants=('read_file',)) for staff in settings.roster))
    elif change == 'disabled':
        settings = replace(settings, roster=tuple(replace(staff, enabled=False) for staff in settings.roster))
    elif change == 'retired':
        settings = replace(settings, roster=())
    else:
        elsewhere = tmp_path / 'different-project'
        elsewhere.mkdir()
        settings = replace(settings, read_roots=(str(elsewhere), *settings.read_roots))
    # A live handler must recheck even without recreating the facade/claim route.
    store.settings = settings
    with pytest.raises(ValueError):
        store.finish(apply, {})
    with store._connect() as conn:
        assert store.edit_apply_unavailability(conn, apply)
    assert _head(store, objective)['revision'] == 0 and _count(store, 'edit_applications') == 0
    assert store.evidence(evidence['id'])['editProposal']['status'] == 'approved'
    assert source.read_bytes() == SOURCE.encode()


def test_two_reviewed_proposals_same_base_can_commit_only_once_across_connections(tmp_path):
    store, source, objective = _setup(tmp_path, tasks=2)
    first, second = store.claim_next(), store.claim_next()
    assert first['agent_id'] != second['agent_id']
    _, evidence_a, _, _ = _propose(store, first, new='first choice')
    _, evidence_b, _, _ = _propose(store, second, new='second choice')
    _review(store)
    _review(store)
    # Two admitted dispatchers can race even though the normal single applier
    # slot serializes claims. Exercise final commit fencing with real leases.
    apply_a = store.claim_next()
    with store._write() as conn:
        row = conn.execute("SELECT id FROM requests WHERE type='request.apply' AND status='queued'").fetchone()
        store._stamp_claim_policy(conn, row['id'])
        conn.execute("UPDATE requests SET status='running',agent_id=?,token='second-token',lease=?,attempts=1 WHERE id=?",
                     (apply_a['agent_id'], time.time() + 60, row['id']))
        apply_b = dict(conn.execute('SELECT * FROM requests WHERE id=?', (row['id'],)).fetchone())
    barrier = threading.Barrier(2)
    def apply(claim):
        other = OrganizationStore(store.path, store.settings)
        barrier.wait()
        try:
            return other.finish(claim, {})
        except ValueError as error:
            return str(error)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(apply, [apply_a, apply_b]))
    assert outcomes.count(True) == 1
    assert any(isinstance(outcome, str) and 'stale' in outcome for outcome in outcomes)
    assert _count(store, 'edit_applications') == 1 and _head(store, objective)['revision'] == 1
    states = [store.evidence(ev['id'])['editProposal']['status'] for ev in (evidence_a, evidence_b)]
    assert states.count('applied') == 1 and states.count('approved') == 1
    assert source.read_bytes() == SOURCE.encode()


def test_cancel_during_source_read_keeps_sqlite_unlocked_and_prevents_capture(tmp_path):
    store, _, objective = _setup(tmp_path)
    claim = store.claim_next()
    begun, release = threading.Event(), threading.Event()
    def loader():
        begun.set()
        assert release.wait(5)
        return SOURCE
    with ThreadPoolExecutor(max_workers=1) as pool:
        capture = pool.submit(store.capture_workspace_source, claim, PATH, loader)
        assert begun.wait(5)
        assert OrganizationStore(store.path, store.settings).cancel(objective['id'])
        release.set()
        with pytest.raises(ValueError, match='lease'):
            capture.result()
    assert _count(store, 'workspace_revisions') == _count(store, 'workspace_roots') == 0


def test_cancellation_commits_before_apply_so_no_revision_is_published(tmp_path):
    store, source, objective = _setup(tmp_path)
    _, evidence, _, _ = _propose(store)
    _review(store)
    apply = store.claim_next()
    assert store.cancel(objective['id'])
    assert not store.finish(apply, {})
    assert _count(store, 'edit_applications') == 0 and _head(store, objective)['revision'] == 0
    assert store.evidence(evidence['id'])['editProposal']['status'] == 'approved'
    assert source.read_bytes() == SOURCE.encode()


def test_immutable_rows_and_exact_deletion_preserve_empty_file_revision(tmp_path):
    store, source, objective = _setup(tmp_path, content='no newline')
    _, evidence, _, _ = _propose(store, old='no newline', new='')
    assert '\\ No newline at end of file' in evidence['editProposal']['diff']
    with store._write() as conn:
        for table in ('workspace_revisions', 'workspace_roots', 'edit_proposals'):
            with pytest.raises(sqlite3.IntegrityError, match='Immutable'):
                conn.execute(f'DELETE FROM {table}')
    _review(store)
    assert store.finish(store.claim_next(), {})
    assert _head(store, objective)['content'] == ''
    assert source.is_file() and source.read_bytes() == b'no newline'


def test_read_only_grants_allow_proposal_and_review_but_application_requires_explicit_patch(tmp_path):
    store, source, objective = _setup(tmp_path)
    store.settings = replace(store.settings, tool_grants=('read_file',),
                             roster=tuple(replace(staff, tool_grants=('read_file',)) for staff in store.settings.roster))
    _, evidence, _, _ = _propose(store)
    _review(store)
    assert store.claim_next() is None
    pending = next(row for row in store.snapshot()['requests'] if row['type'] == 'request.apply')
    assert pending['status'] == 'pending_intervention' and 'patch' in pending['reason']
    assert store.evidence(evidence['id'])['editProposal']['applicationReason'] == pending['reason']
    assert _head(store, objective)['revision'] == 0 and source.read_bytes() == SOURCE.encode()


@pytest.mark.parametrize('unsafe', ['bad\x00control', 'ghp_' + 'A' * 36, 'https://user:password@example.com',
                                   'é' * (MAX_EDIT_BYTES // 2 + 1), '\ud800'])
def test_authoritative_capture_rejects_unsafe_or_oversized_alternate_loader_content(tmp_path, unsafe):
    store, _, _ = _setup(tmp_path)
    claim = store.claim_next()
    with pytest.raises(ValueError):
        store.capture_workspace_source(claim, PATH, lambda: unsafe)
    assert _count(store, 'workspace_revisions') == _count(store, 'workspace_roots') == 0


@pytest.mark.parametrize('unsafe', ['bad\x00control', 'ghp_' + 'A' * 36, 'https://user:password@example.com'])
def test_proposal_rejects_new_secret_or_control_content_without_silent_redaction(tmp_path, unsafe):
    store, source, objective = _setup(tmp_path)
    claim = store.claim_next()
    read, _ = _read(store, claim)
    with pytest.raises(ValueError):
        store.finish(claim, {'summary': 'Unsafe new content', 'edit': {'path': PATH,
            'baseRevision': read['workspaceRevision'], 'baseSha256': read['sourceSha256'],
            'oldText': 'old value', 'newText': unsafe}})
    assert _count(store, 'edit_proposals') == 0 and _head(store, objective)['content'] == SOURCE
    assert source.read_bytes() == SOURCE.encode()


def test_partial_read_of_maximum_source_binds_exact_full_utf8_revision_and_replacement(tmp_path):
    content = '\ufeffold value\r\n' + ('é\n' * 10000)
    # Keep the exact byte limit observable without relying on the result budget.
    content += 'x' * (MAX_EDIT_BYTES - len(content.encode()))
    assert len(content.encode()) == MAX_EDIT_BYTES
    store, source, objective = _setup(tmp_path, content=content)
    claim = store.claim_next()
    read, _ = _read(store, claim, limit=1)
    assert read['truncated'] and read['content'] == '\ufeffold value\r\n'
    assert read['sourceSha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert store.finish(claim, {'summary': 'Bounded source replacement', 'edit': {'path': PATH,
        'baseRevision': read['workspaceRevision'], 'baseSha256': read['sourceSha256'],
        'oldText': 'old value', 'newText': 'new value'}})
    _review(store)
    assert store.finish(store.claim_next(), {})
    assert _head(store, objective)['content'] == content.replace('old value', 'new value')
    assert source.read_bytes() == content.encode()


def test_overlapping_old_text_is_not_a_unique_replacement(tmp_path):
    store, _, _ = _setup(tmp_path, content='aaa')
    claim = store.claim_next()
    read, _ = _read(store, claim)
    with pytest.raises(ValueError, match='exactly one'):
        store.finish(claim, {'summary': 'Ambiguous overlap', 'edit': {'path': PATH,
            'baseRevision': read['workspaceRevision'], 'baseSha256': read['sourceSha256'],
            'oldText': 'aa', 'newText': 'b'}})
    assert _count(store, 'edit_proposals') == 0


def test_expired_apply_lease_and_duplicate_proposal_requests_cannot_advance_head_twice(tmp_path):
    store, _, objective = _setup(tmp_path)
    _, evidence, _, _ = _propose(store)
    _review(store)
    apply = store.claim_next()
    with store._write() as conn:
        conn.execute('UPDATE requests SET lease=? WHERE id=?', (time.time() - 1, apply['id']))
    assert not store.finish(apply, {})
    assert _count(store, 'edit_applications') == 0
    assert store.recover_expired() == 1
    assert store.retry(apply['id'])
    retried = store.claim_next()
    assert retried['attempts'] == 2 and retried['token'] != apply['token']
    assert store.finish(retried, {})
    with store._write() as conn:
        duplicate_id = store._request(conn, objective['id'], 'request.apply', apply['team'], apply['priority'],
                                      apply['task_id'], json.loads(apply['payload']))
    duplicate = store.claim_next()
    assert duplicate['id'] == duplicate_id
    assert store.finish(duplicate, {})
    assert _count(store, 'edit_applications') == 1 and _head(store, objective)['revision'] == 1
    assert store.evidence(evidence['id'])['editProposal']['status'] == 'applied'
    assert store.snapshot()['tasks'][0]['status'] == 'completed'
    assert sum(r['type'] == 'request.merge' for r in store.snapshot()['requests']) == 1


def test_reopened_root_alias_cannot_rebind_existing_workspace_or_new_files(tmp_path):
    store, _, objective = _setup(tmp_path)
    claim = store.claim_next()
    _read(store, claim)
    other = tmp_path / 'other'
    other.mkdir()
    changed = OrganizationStore(store.path, replace(store.settings, read_roots=(str(other), *store.settings.read_roots)))
    with pytest.raises(ValueError, match='lease'):
        changed.capture_workspace_source(claim, PATH, lambda: 'unrelated source')
    assert changed.retry(claim['id'])
    claim = changed.claim_next()
    for path in (PATH, 'root0/new.txt'):
        with pytest.raises(ValueError, match='alias changed'):
            changed.capture_workspace_source(claim, path, lambda: 'unrelated source')
    assert _count(changed, 'workspace_revisions') == 1
    assert _head(changed, objective)['content'] == SOURCE


def test_full_proposal_view_rejects_detached_or_corrupted_immutable_evidence(tmp_path):
    store, _, _ = _setup(tmp_path)
    _, evidence, _, receipt = _propose(store)
    with store._write() as conn:
        conn.execute('DELETE FROM evidence_tools WHERE evidence_id=? AND receipt_id=?', (evidence['id'], receipt['id']))
    with pytest.raises(ValueError, match='matching persisted successful'):
        store.evidence(evidence['id'])


@pytest.mark.parametrize(('before', 'after'), [
    ('\ufeffold\r\nlast\r\n', '\ufeffnew\r\nlast\r\n'),
    ('old\r\nno newline', 'new\r\nlast\n'),
    ('old\nfinal\n', 'new\nfinal'),
    ('remove entire content', ''),
])
def test_backend_diff_roundtrips_exact_bytes_in_an_isolated_fixture(tmp_path, before, after):
    # The diff prefixes describe aliases; stripping a/root0 maps to this
    # explicitly selected derived copy. Production never invokes Git.
    fixture = tmp_path / 'copy.txt'
    fixture.write_bytes(before.encode('utf-8'))
    result = subprocess.run(['git', 'apply', '-p2', '-'],
                            input=_diff('root0/copy.txt', before, after).encode('utf-8'),
                            cwd=tmp_path, capture_output=True, timeout=10, check=False)
    assert result.returncode == 0, result.stderr.decode()
    assert fixture.read_bytes() == after.encode('utf-8')


@pytest.mark.parametrize('change', ['patch', 'root', 'team'])
def test_policy_revocation_by_another_store_fences_old_apply_even_after_policy_restoration(tmp_path, change):
    old, source, objective = _setup(tmp_path)
    original_settings = old.settings
    _, evidence, _, _ = _propose(old)
    _review(old)
    apply = old.claim_next()
    if change == 'patch':
        settings = replace(original_settings, tool_grants=('read_file',),
                           roster=tuple(replace(staff, tool_grants=('read_file',)) for staff in original_settings.roster))
    elif change == 'root':
        other = tmp_path / 'new-root'
        other.mkdir()
        settings = replace(original_settings, read_roots=(str(other),))
    else:
        settings = replace(original_settings, roster=tuple(replace(staff, team='different-team')
                                                          for staff in original_settings.roster))
    changed = OrganizationStore(old.path, settings)
    assert old.finish(apply, {}) is False
    assert old.heartbeat(apply) is False
    assert _count(changed, 'edit_applications') == 0
    # Returning to the original config must not resurrect the old claim (ABA).
    restored = OrganizationStore(old.path, original_settings)
    assert old.finish(apply, {}) is False
    assert restored.evidence(evidence['id'])['editProposal']['status'] == 'approved'
    assert _head(restored, objective)['revision'] == 0
    assert source.read_bytes() == SOURCE.encode()
