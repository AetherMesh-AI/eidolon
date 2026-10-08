"""Project-scoped execution ledger and real independent repository integration."""
from dataclasses import replace
import json
import os
import subprocess
import threading

import pytest

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_store import OrganizationStore
from eidolon_cli.organization_project_execution import project_execution_view
from tools.organization_file_read import organization_file_read_scope
from tests.eidolon_cli.test_organization_project_security import _simulated_terminal_result


def git(repo, *args):
    return subprocess.run(['git', '-c', 'maintenance.auto=false', '-c', 'gc.auto=0', '-C', str(repo), *args],
        check=True, capture_output=True, env={**os.environ, 'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_AUTHOR_NAME': 'Test', 'GIT_AUTHOR_EMAIL': 'test@localhost',
            'GIT_COMMITTER_NAME': 'Test', 'GIT_COMMITTER_EMAIL': 'test@localhost'}).stdout


def configured(tmp_path, *, selected=('alpha', 'beta'), source=False):
    tmp_path = tmp_path.resolve()
    roots = []
    for i in range(2):
        root = tmp_path / f'repo{i}'
        root.mkdir()
        (root / 'test_sample.py').write_text(f'# independent project {i}\n')
        if source:
            git(root, 'init', '-b', 'main')
            git(root, 'add', '.')
            git(root, 'commit', '-m', 'Initial independent source')
        roots.append(str(root))
    settings = OrganizationSettings.from_config({'organization': {
        'capabilities': ['work.inspect'], 'tool_grants': ['read_file', 'run_tests'],
        'read_roots': roots,
        'project_grants': [{'id': f'recipe{i}', 'files': [f'root{i}/test_sample.py'],
                            'execution': {'root': f'root{i}'}} for i in range(2)],
        'projects': [{'id': name, 'root': f'root{i}', 'recipe': f'recipe{i}', 'team': 'engineering'}
                     for i, name in enumerate(('alpha', 'beta'))],
        'roster': [{'id': 'inspector', 'name': 'Inspector', 'team': 'engineering',
                    'capabilities': ['work.inspect'], 'tool_grants': ['read_file', 'run_tests']}]}})
    if source:
        settings = replace(settings, tool_grants=(*settings.tool_grants, 'integrate_source'),
            roster=tuple(replace(member, tool_grants=(*member.tool_grants, 'integrate_source'))
                         for member in settings.roster))
    store = OrganizationStore(tmp_path / 'state.db', settings)
    objective = store.create_objective('Review both projects', idempotency_key='two',
        delivery_mode='source_project' if source else 'managed_artifact', required_checks=['project_tests'], project_ids=list(selected))
    return store, objective


def reviewed_inspections(store, objective, *, projects=('alpha', 'beta')):
    plan = store.claim_next()
    assert plan['type'] == 'request.plan'
    store.finish(plan, {'workers': 1, 'tasks': [
        {'title': f'Inspect {name}', 'description': 'Read the exact selected project file.',
         'type': 'work.inspect', 'team': 'engineering', 'agentId': 'inspector',
         'projectId': name, 'dependsOn': []} for name in projects]})
    while True:
        claim = store.claim_next()
        assert claim is not None
        if claim['type'] == 'request.hire':
            store.finish(claim, {})
        elif claim['type'] == 'work.inspect':
            with store._connect() as conn:
                binding = json.loads(conn.execute('SELECT p.binding FROM task_projects t JOIN objective_projects p '
                    'ON p.objective_id=t.objective_id AND p.project_id=t.project_id WHERE t.task_id=?',
                    (claim['task_id'],)).fetchone()[0])
            args = {'path': binding['root'] + '/test_sample.py', 'offset': 1, 'limit': 2000}
            receipt = store.record_tool_start(claim, 'inspect', 'read_file', args)
            with organization_file_read_scope(store.settings.read_roots, store.settings.max_tool_result_chars) as scope:
                raw = scope.read_file(**args)
            assert json.loads(raw)['success']
            store.record_tool_finish(claim, receipt['id'], raw, 'completed')
            store.finish(claim, {'summary': 'Read exact project file', 'deliverable': 'The selected source was inspected.'})
        elif claim['type'] == 'request.review':
            store.finish(claim, {'approved': True, 'summary': 'Exact read evidence independently checked',
                                 'evidenceIds': json.loads(claim['payload'])['evidenceIds']})
        else:
            assert claim['type'] == 'request.project_test'
            return claim


@pytest.mark.linux_only
def test_each_project_requires_its_own_latest_reviewed_run(tmp_path, monkeypatch):
    from eidolon_cli import organization_project_runner as runner
    store, objective = configured(tmp_path)
    claim = reviewed_inspections(store, objective)
    dispatched = []
    def ledger_result(files, grant, cancel):
        dispatched.append((grant['root'], [file['path'] for file in files]))
        return _simulated_terminal_result(files)
    monkeypatch.setattr(runner, 'run_project_tests', ledger_result)
    reviewed = []
    while claim['type'] != 'request.integrate':
        payload = json.loads(claim['payload'])
        assert payload['projectId'] in {'alpha', 'beta'}
        if claim['type'] == 'request.project_test':
            store.run_project_stage(claim, threading.Event())
            store.finish(claim, {})
        else:
            assert claim['type'] == 'request.test_review'
            if not reviewed:
                with store._write() as conn:
                    conn.execute('UPDATE requests SET payload=? WHERE id=?',
                                 (json.dumps({**payload, 'projectId': 'beta' if payload['projectId'] == 'alpha' else 'alpha'}), claim['id']))
                with pytest.raises(ValueError, match='exact evidence'):
                    store.finish(claim, {'approved': True, 'summary': 'Incorrect cross-project review',
                                         'evidenceIds': payload['evidenceIds']})
                with store._write() as conn:
                    conn.execute('UPDATE requests SET payload=? WHERE id=?', (json.dumps(payload), claim['id']))
            store.finish(claim, {'approved': True, 'summary': 'Exact project-specific ledger evidence reviewed',
                                 'evidenceIds': payload['evidenceIds']})
            reviewed.append(payload['projectId'])
        if len(reviewed) < 2:
            with store._connect() as conn, pytest.raises(ValueError):
                store.verify_project_tests(conn, objective['id'])
        claim = store.claim_next()
        assert claim is not None
    assert sorted(reviewed) == ['alpha', 'beta']
    assert sorted(dispatched) == [('root0', ['root0/test_sample.py']), ('root1', ['root1/test_sample.py'])]
    with store._connect() as conn:
        runs = store.verify_project_test_runs(conn, objective['id'])
        assert len(runs) == 2
        view = project_execution_view(conn, objective['id'])
        assert {item['projectId'] for item in view['projects']} == {'alpha', 'beta'}
        assert all(item['execution']['review']['approved'] for item in view['projects'])
        assert {artifact['id'] for _, artifact in runs}.issubset(json.loads(claim['payload'])['evidenceIds'])


@pytest.mark.linux_only
def test_selected_project_without_contributing_task_cannot_execute_or_accept(tmp_path):
    store, objective = configured(tmp_path)
    claim = reviewed_inspections(store, objective, projects=('alpha',))
    with store._connect() as conn:
        with pytest.raises(ValueError, match='reviewed inspection or edit'):
            store._project_grant(conn, objective['id'], 'beta')
        with pytest.raises(ValueError):
            store.verify_project_tests(conn, objective['id'])
        with pytest.raises(ValueError, match='Every selected project'):
            store.verify_project_coverage(conn, objective['id'])
    assert json.loads(claim['payload'])['projectId'] == 'alpha'


@pytest.mark.linux_only
def test_both_projects_publish_exact_separate_source_receipts(tmp_path, monkeypatch):
    from pathlib import Path
    from eidolon_cli import organization_project_runner as runner
    store, objective = configured(tmp_path, source=True)
    before = {root: (git(Path(root), 'rev-parse', 'HEAD'), (Path(root) / '.git/index').read_bytes())
              for root in store.settings.read_roots}
    claim = reviewed_inspections(store, objective)
    monkeypatch.setattr(runner, 'run_project_tests', lambda files, grant, cancel: _simulated_terminal_result(files))
    integrated = []
    while claim['type'] != 'request.integrate':
        payload = json.loads(claim['payload'])
        if claim['type'] in {'request.project_test', 'request.source_integrate'}:
            if claim['type'] == 'request.source_integrate' and not integrated:
                with store._write() as conn:
                    other_run = next(run['id'] for run, _ in store.verify_project_test_runs(conn, objective['id'])
                                     if run['id'] != payload['runId'])
                    conn.execute('UPDATE requests SET payload=? WHERE id=?',
                                 (json.dumps({**payload, 'runId': other_run}), claim['id']))
                with pytest.raises(ValueError, match='exact reviewed tested source base'):
                    store.run_project_stage(claim, threading.Event())
                with store._write() as conn:
                    conn.execute('UPDATE requests SET payload=? WHERE id=?', (json.dumps(payload), claim['id']))
            store.run_project_stage(claim, threading.Event())
            store.finish(claim, {})
            if claim['type'] == 'request.source_integrate':
                integrated.append(payload['projectId'])
                if len(integrated) == 1:
                    with store._connect() as conn:
                        assert not store.verified_source_integration(conn, objective['id'])
        else:
            assert claim['type'] == 'request.test_review'
            store.finish(claim, {'approved': True, 'summary': 'Review exact project test ledger',
                                 'evidenceIds': payload['evidenceIds']})
        claim = store.claim_next()
        assert claim is not None
    assert sorted(integrated) == ['alpha', 'beta']
    with store._connect() as conn:
        assert store.verified_source_integration(conn, objective['id'])
        artifacts = store.current_source_evidences(conn, objective['id'])
        assert len(artifacts) == 2
        assert {item['id'] for item in artifacts}.issubset(json.loads(claim['payload'])['evidenceIds'])
        receipts = [json.loads(item['content']) for item in artifacts]
    assert {receipt['rootAlias'] for receipt in receipts} == {'root0', 'root1'}
    for root in store.settings.read_roots:
        assert (git(Path(root), 'rev-parse', 'HEAD'), (Path(root) / '.git/index').read_bytes()) == before[root]
    receipt = receipts[0]
    root = store.settings.read_roots[int(receipt['rootAlias'][4:])]
    git(Path(root), 'update-ref', '-d', receipt['ref'])
    with store._connect() as conn, pytest.raises(ValueError, match='removed or changed'):
        store.verified_source_integration(conn, objective['id'])


@pytest.mark.linux_only
def test_interrupted_project_is_durable_unknown_and_binding_revocation_blocks(tmp_path, monkeypatch):
    from eidolon_cli import organization_project_runner as runner
    store, objective = configured(tmp_path)
    claim = reviewed_inspections(store, objective)
    def interrupted(files, grant, cancel):
        raise RuntimeError('Interrupted test process')
    monkeypatch.setattr(runner, 'run_project_tests', interrupted)
    with pytest.raises(RuntimeError, match='Interrupted'):
        store.run_project_stage(claim, threading.Event())
    reopened = OrganizationStore(store.path, store.settings)
    with reopened._connect() as conn:
        view = project_execution_view(conn, objective['id'])
        assert view['projects'][0]['execution']['status'] == 'unknown'
        assert view['projects'][1]['execution'] is None
        assert 'unknown outcome' in reopened.project_stage_unavailability(conn, claim)
        with pytest.raises(ValueError, match='unconfirmed'):
            reopened.verify_project_tests(conn, objective['id'])
    revoked = OrganizationStore(store.path, replace(store.settings, read_roots=tuple(reversed(store.settings.read_roots))))
    with revoked._connect() as conn, pytest.raises(ValueError, match='binding changed or was revoked'):
        revoked._project_grant(conn, objective['id'], 'alpha')


def _native_two_repository_loop(tmp_path, native_os):
    """Real filesystem, SQLite, fixed sandbox runner and Git branches; no provider calls."""
    from pathlib import Path
    from tests.eidolon_cli.test_organization_edits import _read
    store, _ = configured(tmp_path, source=True)
    # This fixture's first objective is not the native run being tested.
    with store._write() as conn:
        conn.execute("UPDATE objectives SET cancelled=1")
        conn.execute("UPDATE requests SET status='cancelled'")
    settings = replace(store.settings, capabilities=('work.edit',),
        tool_grants=(*store.settings.tool_grants, 'patch'),
        roster=tuple(replace(member, capabilities=('work.edit',),
                            tool_grants=(*member.tool_grants, 'patch')) for member in store.settings.roster))
    store = OrganizationStore(store.path, settings)
    before = {}
    for root in settings.read_roots:
        root = Path(root)
        text = ('import unittest\n\ndef add(a, b):\n    return a - b\n\n'
                'class ArithmeticTests(unittest.TestCase):\n'
                '    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n')
        (root / 'test_sample.py').write_text(text)
        git(root, 'add', '.')
        git(root, 'commit', '-m', 'Add failing arithmetic regression')
        before[str(root)] = (git(root, 'rev-parse', 'HEAD'), (root / '.git/index').read_bytes(), text)
    objective = store.create_objective('Correct both repositories', idempotency_key='native-two',
        delivery_mode='source_project', required_checks=['project_tests', 'managed_validation', 'source_integration'],
        project_ids=['alpha', 'beta'])
    claim = store.claim_next()
    assert claim['type'] == 'request.plan'
    store.finish(claim, {'workers': 1, 'tasks': [
        {'title': f'Correct {name}', 'description': 'Fix subtraction to addition and preserve the regression.',
         'type': 'work.edit', 'team': 'engineering', 'agentId': 'inspector', 'projectId': name,
         'writePaths': [f'root{i}/test_sample.py'], 'dependsOn': []}
        for i, name in enumerate(('alpha', 'beta'))]})
    seen_tests, seen_sources, unavailable = set(), set(), False
    for _ in range(40):
        claim = store.claim_next()
        if claim is None:
            break
        kind, payload = claim['type'], json.loads(claim['payload'])
        if kind == 'work.edit':
            with store._connect() as conn:
                from eidolon_cli.organization_projects import task_project
                project = task_project(conn, claim['task_id'])
            path = project['root'] + '/test_sample.py'
            observed, _ = _read(store, claim, path=path)
            result = {'summary': 'Correct addition without changing the assertion', 'edits': [{
                'path': path, 'baseRevision': observed['workspaceRevision'], 'baseSha256': observed['sourceSha256'],
                'oldText': 'return a - b', 'newText': 'return a + b'}],
                'validations': [{'kind': 'python_syntax', 'path': path}]}
        elif kind == 'request.review':
            result = {'approved': True, 'summary': 'Exact arithmetic edit independently checked',
                'evidenceIds': payload['evidenceIds'], 'proposalId': payload['proposalId'],
                'proposalSha256': payload['proposalSha256']}
        elif kind in {'request.project_test', 'request.source_integrate'}:
            store.run_project_stage(claim, threading.Event())
            result = {}
            if kind == 'request.project_test':
                seen_tests.add(payload['projectId'])
                with store._connect() as conn:
                    execution = project_execution_view(conn, objective['id'], project_id=payload['projectId'])
                unavailable |= execution['status'] == 'unsupported'
            else:
                seen_sources.add(payload['projectId'])
        elif kind == 'request.test_review':
            proof = store.evidence(payload['evidenceIds'][0])
            exact = json.loads(proof['content'])
            assert exact['projectId'] == payload['projectId']
            assert exact['execution']['testCount'] == 1
            assert exact['execution']['isolation']['established'] is True
            assert len(exact['snapshot']) == 1 and 'return a + b' in exact['snapshot'][0]['content']
            result = {'approved': True, 'summary': 'Real isolated regression and exact source reviewed',
                      'evidenceIds': payload['evidenceIds']}
        elif kind == 'request.integrate':
            assert seen_tests == seen_sources == {'alpha', 'beta'}
            result = {'summary': 'Both projects reviewed and tested', 'deliverable': 'Both exact source branches correct addition.'}
        elif kind == 'request.accept':
            with store._connect() as conn:
                criteria = json.loads(conn.execute('SELECT criteria FROM objective_control WHERE objective_id=?',
                                                   (objective['id'],)).fetchone()[0])
            result = {'approved': True, 'summary': 'Both projects meet the exact objective',
                'evidenceIds': payload['evidenceIds'], 'conflicts': [],
                'criteriaResults': [{'criterion': criterion, 'satisfied': True,
                                    'evidenceIds': payload['evidenceIds'], 'reason': 'Both real tests and branches verified'}
                                   for criterion in criteria]}
        else:
            assert kind in {'request.hire', 'request.apply', 'request.validate'}
            result = {}
        store.finish(claim, result)
    else:
        pytest.fail('Native project loop did not reach a terminal state')
    current = next(item for item in store.snapshot()['objectives'] if item['id'] == objective['id'])
    for root in settings.read_roots:
        assert (git(Path(root), 'rev-parse', 'HEAD'), (Path(root) / '.git/index').read_bytes(),
                (Path(root) / 'test_sample.py').read_text()) == before[root]
    if native_os == 'windows':
        assert current['status'] == 'needs_input'
        assert not seen_tests and not seen_sources
        assert any('POSIX no-follow directory-descriptor support' in str(row['reason'])
                   for row in store.snapshot()['requests'] if row['status'] == 'pending_intervention')
        return
    if native_os == 'linux' and os.environ.get('EIDOLON_REQUIRE_PROJECT_SANDBOX') == '1':
        assert not unavailable, current
    if unavailable:
        assert current['status'] == 'needs_input'
        assert not seen_sources
        with store._connect() as conn:
            assert conn.execute('SELECT count(*) FROM project_source_receipts').fetchone()[0] == 0
    else:
        assert current['status'] == 'completed'
        assert seen_tests == seen_sources == {'alpha', 'beta'}
        for item in current['projectExecution']['projects']:
            receipt = item['execution']['sourceIntegration']
            root = settings.read_roots[int(receipt['rootAlias'][4:])]
            assert b'return a + b' in git(Path(root), 'show', receipt['ref'] + ':test_sample.py')
    return store, objective


@pytest.mark.linux_only
def test_retained_edits_keep_their_project_snapshot_and_grants_after_replan(tmp_path, monkeypatch):
    from eidolon_cli import organization_project_runner as runner
    from eidolon_cli.organization_project_workspace import final_source_manifest
    monkeypatch.setattr(runner, 'run_project_tests', lambda files, grant, cancel: _simulated_terminal_result(files))
    store, objective = _native_two_repository_loop(tmp_path, 'linux')
    with store._write() as conn:
        # A new round has no current tasks yet. Previously applied reviewed edits
        # remain exact per-project inputs, but prior-round tests cannot accept it.
        store._replan(conn, objective['id'], 'Review the retained cross-project result again')
        store.verify_project_coverage(conn, objective['id'])
        manifest = final_source_manifest(conn, objective['id'])[0]
        assert {item['path'] for item in manifest} == {'root0/test_sample.py', 'root1/test_sample.py'}
        for project_id, root in (('alpha', 'root0'), ('beta', 'root1')):
            grant = store._project_grant(conn, objective['id'], project_id)
            snapshot = store._project_snapshot(conn, objective['id'], grant)
            assert [item['path'] for item in snapshot] == [root + '/test_sample.py']
            assert snapshot[0]['revision'] == 1 and 'return a + b' in snapshot[0]['content']
            with pytest.raises(ValueError, match='not been executed'):
                store.verify_project_tests(conn, objective['id'], project_id)
        assert not store.verified_source_integration(conn, objective['id'])


@pytest.mark.parametrize('native_os', [pytest.param('linux', marks=pytest.mark.linux_only),
                                      pytest.param('macos', marks=pytest.mark.macos_only),
                                      pytest.param('windows', marks=pytest.mark.windows_only)])
def test_native_two_repository_edit_test_review_source_acceptance(tmp_path, native_os):
    _native_two_repository_loop(tmp_path, native_os)
