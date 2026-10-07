"""Absent sources stay distinct from empty files through exact reviewed creation."""
from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace

import pytest

from eidolon_cli.organization_store import OrganizationStore
from eidolon_cli.organization_tool_executor import tool_execution
from eidolon_cli.organization_project_config import parse_project_grants
from tests.eidolon_cli.test_organization_tool_executor import _agent, _call
from tests.eidolon_cli.test_organization_edits import _setup, _read, _review
from tests.eidolon_cli.test_organization_project_workflow import apply_validate
from tools.organization_file_read import organization_file_read_scope


def _create(path, read, content='created\n'):
    return {'operation': 'create', 'path': path, 'baseRevision': read['workspaceRevision'],
            'baseSha256': read['sourceSha256'], 'newText': content}


@pytest.mark.linux_only
@pytest.mark.parametrize('scenario', ['durable', 'empty_create', 'empty_existing', 'fabricated', 'competing', 'revoked',
                                     'symlink', 'parent_symlink', 'parent_file', 'protected', 'oversize', 'overlap'])
def test_creation_requires_exact_absence_review_and_current_authority(tmp_path, scenario):
    store, original, objective = _setup(tmp_path)
    claim = store.claim_next()
    path = 'root0/new/note.txt'
    target = original.parent / 'new' / 'note.txt'
    if scenario in {'symlink', 'parent_symlink', 'parent_file', 'protected'}:
        if scenario == 'symlink':
            target.parent.mkdir()
            target.symlink_to(original.parent / 'not-there')
        elif scenario == 'parent_symlink':
            target.parent.symlink_to(tmp_path / 'absent-directory', target_is_directory=True)
        elif scenario == 'parent_file':
            target.parent.write_text('ordinary file')
        else:
            path = 'root0/.ssh/new.txt'
        with organization_file_read_scope(store.settings.read_roots, 20000,
                resolve_workspace_source=lambda selected, loader: store.capture_workspace_source(claim, selected, loader)) as scope:
            result = json.loads(scope.read_file(path))
        assert result['success'] is False and result['blocked'] is True
        with store._connect() as conn:
            assert conn.execute('SELECT count(*) FROM workspace_revisions').fetchone()[0] == 0
        return
    if scenario == 'empty_existing':
        target.parent.mkdir()
        target.write_bytes(b'')
    context = store.context(claim)
    context.update(recordToolStart=lambda *args: store.record_tool_start(claim, *args),
                   recordToolFinish=lambda *args: store.record_tool_finish(claim, *args),
                   requestToolReceipts=lambda: store.request_tool_receipts(claim),
                   resolveWorkspaceSource=lambda selected, loader: store.capture_workspace_source(claim, selected, loader))
    with tool_execution(context, 'work.edit') as execution:
        agent, messages = _agent(execution), []
        agent._execute_tool_calls(SimpleNamespace(tool_calls=[_call(path)]), messages, 'create-project')
        read = json.loads(messages[-1]['content'])
        assert read['success'], read
        if scenario != 'empty_existing':
            execution.verify_completion(_create(path, read))
    assert read.get('sourceExists', True) is (scenario == 'empty_existing')
    content = '' if scenario == 'empty_create' else 'created\n'
    output = {'summary': 'Create a new exact file', 'edits': [_create(path, read, content)],
              'validations': [{'kind': 'text_contains', 'path': path, 'text': 'created'}]}
    if scenario == 'empty_create':
        output['validations'] = []
    if scenario == 'empty_existing':
        with pytest.raises(ValueError, match='absent'):
            store.finish(claim, output)
        assert target.read_bytes() == b''
        return
    if scenario == 'fabricated':
        output['edits'][0]['path'] = 'root0/not-observed.txt'
        output['validations'] = []
        with pytest.raises(ValueError, match='missing'):
            store.finish(claim, output)
        return
    if scenario == 'oversize':
        output['edits'][0]['newText'] = 'x' * 32769
        with pytest.raises(ValueError, match='byte'):
            store.finish(claim, output)
        return
    if scenario == 'overlap':
        with organization_file_read_scope(store.settings.read_roots, 20000,
                resolve_workspace_source=lambda selected, loader: store.capture_workspace_source(claim, selected, loader)) as scope:
            result = json.loads(scope.read_file(path + '/child.txt'))
        assert result['success'] is False
        return
    assert store.finish(claim, output)
    with store._connect() as conn:
        evidence = conn.execute('SELECT id FROM evidence WHERE request_id=?', (claim['id'],)).fetchone()[0]
    proof = store.evidence(evidence)['editProposal']
    assert proof['files'][0]['operation'] == 'create'
    assert '--- /dev/null\n' in proof['files'][0]['diff']
    if scenario in {'competing', 'revoked'}:
        _review(store)
        apply = store.claim_next()
        if scenario == 'competing':
            with store._write() as conn:
                conn.execute('INSERT INTO workspace_revisions VALUES (?,?,?,?,?,?,?)',
                             (objective['id'], path, 1, objective['id'], 'other', hashlib.sha256(b'other').hexdigest(), 0))
                conn.execute('UPDATE workspace_heads SET revision=1 WHERE path=?', (path,))
            with pytest.raises(ValueError, match='stale'):
                store.finish(apply, {})
        else:
            revoked = OrganizationStore(store.path, replace(store.settings, tool_grants=('read_file',)))
            assert not store.finish(apply, {})
            assert revoked.snapshot()['objectives'][0]['status'] == 'needs_input'
        with store._connect() as conn:
            assert conn.execute('SELECT count(*) FROM edit_applications').fetchone()[0] == 0
        return
    apply_validate(store)
    reopened = OrganizationStore(store.path, store.settings)
    proof = reopened.evidence(evidence)['editProposal']
    assert proof['validationReceipt']['status'] == 'passed'
    assert proof['files'][0]['newContent'] == content
    assert not target.exists() and not target.parent.exists()
    assert original.read_bytes()
    with organization_file_read_scope(store.settings.read_roots, 20000) as scope:
        assert json.loads(scope.read_file(path))['success'] is False


@pytest.mark.linux_only
@pytest.mark.parametrize('conflict', ['none', 'file', 'empty_file', 'symlink', 'parent_symlink', 'unobserved'])
def test_created_source_lineage_survives_replan_and_refuses_source_conflicts(tmp_path, conflict):
    store, original, objective = _setup(tmp_path)
    path = 'root0/new/app.py'
    claim = store.claim_next()
    read, _ = _read(store, claim, path=path)
    store.finish(claim, {'summary': 'Create source', 'edits': [_create(path, read, 'value = 1\n')]})
    apply_validate(store)
    merge = next(row for row in store.snapshot()['requests'] if row['type'] == 'request.merge')
    store.resolve(merge['id'], 'request_replan', 'Refine the new source', idempotency_key='replan-create')
    plan = store.claim_next()
    store.finish(plan, {'workers': 1, 'tasks': [{'title': 'Refine creation', 'description': 'Refine new source',
                                               'type': 'work.edit', 'team': 'engineering'}]})
    claim = store.claim_next()
    read, _ = _read(store, claim, path=path)
    assert read['sourceExists'] is True and read['workspaceRevision'] == 1
    store.finish(claim, {'summary': 'Refine source', 'edit': {'path': path, 'baseRevision': 1,
                         'baseSha256': read['sourceSha256'], 'oldText': '1', 'newText': '2'}})
    apply_validate(store)
    grant = parse_project_grants([{'id': 'new-source', 'files': [path],
                                  'execution': {'recipe': 'python_unittest'}}], 1)[0]
    target = original.parent / 'new' / 'app.py'
    if conflict in {'file', 'empty_file', 'symlink'}:
        target.parent.mkdir()
        if conflict == 'symlink':
            target.symlink_to(original)
        else:
            target.write_text('unreviewed' if conflict == 'file' else '')
    elif conflict == 'parent_symlink':
        target.parent.symlink_to(tmp_path / 'missing', target_is_directory=True)
    elif conflict == 'unobserved':
        grant = replace(grant, files=(*grant.files, 'root0/never-reviewed.py'))
    with store._connect() as conn:
        if conflict != 'none':
            with pytest.raises(ValueError, match='preimage|unavailable'):
                store._project_snapshot(conn, objective['id'], grant)
            return
        snapshot = store._project_snapshot(conn, objective['id'], grant)
        assert len(snapshot) == 1
        assert snapshot[0]['operation'] == 'create' and snapshot[0]['base_sha256'] is None
        assert snapshot[0]['revision'] == 2 and snapshot[0]['content'] == 'value = 2\n'
    assert not target.exists()
