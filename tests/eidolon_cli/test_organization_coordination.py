"""Real ledger ownership, concurrency and recovery without provider calls."""
from tests.organization_package_helpers import claim_after_decomposition
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import subprocess
import time

import pytest

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_store import OrganizationStore
from eidolon_cli.organization_coordination import resource_identity
from eidolon_cli.organization_executor import _parse_output, OrganizationExecutionError
from tests.eidolon_cli.test_organization_edits import _read, _propose, _review


def ledger(tmp_path, paths, dependencies=None, roots=None):
    project = tmp_path / 'project'
    project.mkdir(exist_ok=True)
    (project / 'example.txt').write_text('old value\n')
    (project / 'other.txt').write_text('other value\n')
    settings = OrganizationSettings.from_config({'organization': {
        'max_workers': 4, 'max_inflight': 4, 'capabilities': ['work.edit'],
        'read_roots': [str(root) for root in (roots or [project])],
        'tool_grants': ['read_file', 'patch'],
        'roster': [{'id': f'editor-{i}', 'name': f'Editor {i}', 'team': 'engineering',
                    'capabilities': ['work.edit'], 'tool_grants': ['read_file', 'patch']} for i in range(4)]}})
    store = OrganizationStore(tmp_path / 'state.db', settings)
    objective = store.create_objective('Maintain the projects', idempotency_key='goal')
    plan = claim_after_decomposition(store)
    tasks = [{'title': f'Edit {i}', 'description': 'Make a scoped change', 'type': 'work.edit',
              'team': 'engineering', 'agentId': f'editor-{i}',
              **({'writePaths': scope} if scope is not None else {}),
              'dependsOn': (dependencies or {}).get(i, [])} for i, scope in enumerate(paths)]
    store.finish(plan, {'tasks': tasks, 'workers': len(tasks)})
    hire = store.claim_next()
    assert hire['type'] == 'request.hire'
    store.finish(hire, {})
    return store, objective


def task(store, claim):
    return next(row for row in store.snapshot()['tasks'] if row['id'] == claim['task_id'])


def test_concurrent_stores_admit_independent_paths_but_reserve_complete_conflict_sets(tmp_path, monkeypatch):
    other = tmp_path / 'other-repository'
    other.mkdir()
    store, _ = ledger(tmp_path, [['root0/example.txt', 'root0/nested/a.txt'],
                                ['root0/nested/a.txt', 'root0/new.txt'],
                                ['root0/other.txt'], ['root1/example.txt']],
                       roots=[tmp_path / 'project', other])
    def claim(_):
        return OrganizationStore(store.path).claim_next()
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = [row for row in pool.map(claim, range(4)) if row]
    assert {row['agent_id'] for row in claims} == {'editor-0', 'editor-2', 'editor-3'}
    waiting = next(row for row in store.snapshot()['tasks'] if row['assignedAgentId'] == 'editor-1')
    first = next(row for row in claims if row['agent_id'] == 'editor-0')
    assert waiting['status'] == 'queued'
    assert waiting['coordination']['blockingTaskIds'] == [first['task_id']]
    assert waiting['coordination']['state'] == 'waiting'
    assert store.context(first)['task']['writePaths'] == ['root0/example.txt', 'root0/nested/a.txt']
    with store._connect() as conn:
        request = conn.execute('SELECT attempts FROM requests WHERE task_id=?', (waiting['id'],)).fetchone()
        assert request['attempts'] == 0
        assert not conn.execute('SELECT 1 FROM task_reservations WHERE task_id=?', (waiting['id'],)).fetchone()
    assert OrganizationStore(store.path).claim_next() is None
    monkeypatch.setattr(store, '_write_resources', lambda paths: pytest.fail('Parked conflicts must not scan the filesystem'))
    assert store.claim_next() is None


@pytest.mark.parametrize('native_os', [pytest.param('linux', marks=pytest.mark.linux_only),
                                      pytest.param('macos', marks=pytest.mark.macos_only)])
@pytest.mark.parametrize('alias', ['symlink', 'nested-root', 'worktree', 'case', 'unicode'])
def test_aliases_and_linked_worktrees_cannot_split_logical_file_ownership(tmp_path, alias, native_os):
    project = tmp_path / 'project'
    project.mkdir()
    subprocess.run(['git', 'init', '-q', str(project)], check=True)
    subprocess.run(['git', '-C', str(project), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                    'commit', '--allow-empty', '-qm', 'base'], check=True)
    if alias == 'symlink':
        alternate = tmp_path / 'alias'
        alternate.symlink_to(project, target_is_directory=True)
        first, second = 'root0/example.txt', 'root1/example.txt'
    elif alias == 'unicode':
        alternate = project
        first, second = 'root0/caf\u00e9.txt', 'root0/cafe\u0301.txt'
    elif alias == 'case':
        alternate = project
        first, second = 'root0/Example.txt', 'root0/example.txt'
    elif alias == 'nested-root':
        alternate = project / 'nested'
        alternate.mkdir()
        first, second = 'root0/nested/example.txt', 'root1/example.txt'
    else:
        alternate = tmp_path / 'worktree'
        subprocess.run(['git', '-C', str(project), 'worktree', 'add', '-qb', 'parallel', str(alternate)], check=True)
        first, second = 'root0/example.txt', 'root1/example.txt'
    assert resource_identity(project, first.split('/', 1)[1]) == resource_identity(alternate, second.split('/', 1)[1])
    store, _ = ledger(tmp_path, [[first], [second]], roots=[project, alternate])
    claim = store.claim_next()
    assert store.claim_next() is None
    waiting = next(row for row in store.snapshot()['tasks'] if row['id'] != claim['task_id'])
    assert waiting['coordination']['blockingTaskIds'] == [claim['task_id']]


@pytest.mark.linux_only
def test_review_revision_and_uncertain_restart_keep_ownership_until_validated_or_cancelled(tmp_path):
    store, objective = ledger(tmp_path, [['root0/example.txt'], ['root0/example.txt']])
    first = store.claim_next()
    _propose(store, first)
    _review(store, approved=False)
    revision = store.claim_next()
    assert revision['task_id'] == first['task_id'] and store.claim_next() is None
    with store._write() as conn:
        conn.execute('UPDATE requests SET lease=? WHERE id=?', (time.time() - 1, revision['id']))
    reopened = OrganizationStore(store.path)
    assert reopened.recover_expired() == 1
    assert reopened.claim_next() is None
    assert task(reopened, first)['coordination']['state'] == 'reserved'
    assert reopened.finish(revision, {'intervention': 'late'}) is False
    assert reopened.retry(revision['id'], idempotency_key='retry')
    revision = reopened.claim_next()
    assert revision['task_id'] == first['task_id']
    _propose(reopened, revision)
    _review(reopened)
    apply = reopened.claim_next()
    assert apply['type'] == 'request.apply'
    assert reopened.finish(apply, {})
    validation = reopened.claim_next()
    assert validation['type'] == 'request.validate'
    assert reopened.finish(validation, {})
    second = reopened.claim_next()
    assert second['task_id'] != first['task_id']
    assert task(reopened, first)['coordination']['state'] == 'released'
    source, _ = _read(reopened, second)
    assert source['workspaceRevision'] == 1 and 'new value' in source['content']
    assert reopened.cancel(objective['id'])
    assert task(reopened, second)['coordination']['state'] == 'released'
    assert reopened.finish(second, {'intervention': 'late'}) is False


def test_dependencies_legacy_edits_and_typed_handoffs_do_not_create_hold_and_wait_cycles(tmp_path):
    store, _ = ledger(tmp_path, [None, ['root0/example.txt'], ['root0/other.txt']], dependencies={2: [0]})
    first = store.claim_next()
    assert store.claim_next() is None  # Legacy scopes remain conservative.
    snapshot = store.snapshot()
    sibling = next(row for row in snapshot['requests'] if row['type'] == 'work.edit' and row['taskId'] != first['task_id'])
    with pytest.raises(ValueError, match='acyclic'):
        store.finish(first, {'requests': [{'type': 'request.question', 'requestedOutcome': 'Use sibling output',
                                          'dependencyIds': [sibling['id']]}]})
    assert task(store, first)['coordination']['state'] == 'reserved'
    dependent = next(row for row in snapshot['tasks'] if row['dependsOn'])
    assert 'independently reviewed dependencies' in dependent['coordination']['reason']
    # A question without a cyclic dependency can pause the same persistent worker.
    store.finish(first, {'requests': [{'type': 'request.question', 'requestedOutcome': 'Clarify expected text'}]})
    assert task(store, first)['status'] == 'blocked'
    assert task(store, first)['coordination']['state'] == 'reserved'


@pytest.mark.linux_only
def test_model_scope_is_validated_preserved_and_enforced_at_proposal_boundary(tmp_path):
    store, _ = ledger(tmp_path, [['root0/example.txt']])
    claim = store.claim_next()
    source, _ = _read(store, claim, path='root0/other.txt')
    with pytest.raises(ValueError, match='exceeds.*writePaths'):
        store.finish(claim, {'summary': 'Outside declared scope', 'edit': {'path': 'root0/other.txt',
            'baseRevision': source['workspaceRevision'], 'baseSha256': source['sourceSha256'],
            'oldText': 'other value', 'newText': 'replacement'}})
    output = {'tasks': [{'title': 'Scoped change', 'description': 'Change exact bytes', 'type': 'work.edit',
                         'team': 'engineering', 'writePaths': ['root0/example.txt']}], 'workers': 1}
    assert _parse_output(json.dumps(output), 'request.plan', {})['tasks'][0]['writePaths'] == ['root0/example.txt']
    output['tasks'][0]['writePaths'] = ['root0/../escape']
    with pytest.raises(OrganizationExecutionError):
        _parse_output(json.dumps(output), 'request.plan', {})


def test_aging_prevents_new_high_priority_requests_starving_an_older_ready_request(tmp_path):
    store = OrganizationStore(tmp_path / 'state.db', replace(OrganizationSettings(), max_inflight=1))
    oldest = store.create_objective('Old low priority work', priority='low', idempotency_key='old')
    with store._write() as conn:
        conn.execute('UPDATE requests SET created=? WHERE objective_id=?', (time.time() - 301, oldest['id']))
    store.create_objective('New high priority work', priority='high', idempotency_key='new')
    assert store.claim_next()['objective_id'] == oldest['id']


def test_service_runs_independent_assignments_together_and_fences_cancelled_results(tmp_path):
    import threading
    from eidolon_cli.organization_service import OrganizationService
    store, objective = ledger(tmp_path, [['root0/example.txt'], ['root0/example.txt'], ['root0/other.txt']])
    entered = {f'editor-{index}': threading.Event() for index in range(3)}
    release = threading.Event()
    cancelled = []

    def execute(request, context, cancel):
        entered[request['agent_id']].set()
        assert cancel.wait(5)
        cancelled.append(request['id'])
        assert release.wait(5)
        return {'intervention': 'Late result must not replace cancellation'}

    service = OrganizationService(store, home=tmp_path, executor=execute, poll_seconds=0.01)
    try:
        service.start()
        assert entered['editor-0'].wait(5) and entered['editor-2'].wait(5)
        assert not entered['editor-1'].is_set()
        assert len([row for row in store.snapshot()['requests'] if row['status'] == 'running']) == 2
        assert service.cancel(objective['id'])
    finally:
        release.set()
        assert service.stop(5)
    reopened = OrganizationStore(store.path)
    assert len(cancelled) == 2
    assert all(row['coordination']['state'] == 'released' for row in reopened.snapshot()['tasks'])
    assert not any(row['status'] in {'running', 'pending_intervention'} for row in reopened.snapshot()['requests'])


def test_bounded_replan_releases_old_scope_without_replacing_persistent_ownership(tmp_path):
    store, _ = ledger(tmp_path, [['root0/example.txt']])
    claim = store.claim_next()
    identity = next(row['identityId'] for row in store.snapshot()['agents'] if row['id'] == claim['agent_id'])
    store.fail(claim, 'Need a revised exact file scope', retryable=False)
    assert store.resolve(claim['id'], 'request_replan', text='Use the other file instead', idempotency_key='replan')
    assert task(store, claim)['coordination']['state'] == 'released'
    plan = claim_after_decomposition(store)
    store.finish(plan, {'tasks': [{'title': 'Revised scope', 'description': 'Change the other file',
        'type': 'work.edit', 'team': 'engineering', 'agentId': claim['agent_id'], 'writePaths': ['root0/other.txt']}]})
    revised = store.claim_next()
    assert revised['agent_id'] == claim['agent_id'] and revised['task_id'] != claim['task_id']
    assert next(row['identityId'] for row in store.snapshot()['agents'] if row['id'] == revised['agent_id']) == identity


def test_cycle_detection_includes_latent_conflict_behind_an_unfinished_dependency(tmp_path):
    store, _ = ledger(tmp_path, [['root0/other.txt'], ['root0/example.txt'], ['root0/example.txt']],
                       dependencies={2: [0]})
    independent = store.claim_next()
    holder = store.claim_next()
    assert independent['agent_id'] == 'editor-0' and holder['agent_id'] == 'editor-1'
    blocked = next(row for row in store.snapshot()['requests']
                   if row['type'] == 'work.edit' and row['status'] == 'queued')
    scope = next(row for row in store.snapshot()['tasks'] if row['id'] == blocked['taskId'])
    assert set(scope['coordination']['blockingTaskIds']) == {independent['task_id'], holder['task_id']}
    with pytest.raises(ValueError, match='acyclic'):
        store.finish(holder, {'requests': [{'type': 'request.question', 'requestedOutcome': 'Use queued result',
                                           'dependencyIds': [blocked['id']]}]})
    assert task(store, holder)['status'] == 'working'


@pytest.mark.linux_only
@pytest.mark.timeout(5)
def test_git_metadata_is_bounded_and_cannot_block_on_a_replaced_special_file(tmp_path, monkeypatch):
    import os
    from eidolon_cli.organization_coordination import _metadata_path
    metadata = tmp_path / 'git-metadata'
    metadata.write_text('gitdir: ../repository')
    assert _metadata_path(metadata, 'gitdir:') == (tmp_path.parent / 'repository').resolve()
    metadata.write_bytes(b'x' * 4097)
    with pytest.raises(ValueError, match='too large'):
        _metadata_path(metadata)
    metadata.write_text('gitdir: ../repository')
    original_open = os.open

    def replace_with_fifo(path, flags, *args, **kwargs):
        if path == metadata:
            metadata.unlink()
            os.mkfifo(metadata)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, 'open', replace_with_fifo)
    with pytest.raises(ValueError, match='regular file'):
        _metadata_path(metadata, 'gitdir:')


def test_changed_repository_identity_requires_replan_before_claim(tmp_path):
    store, _ = ledger(tmp_path, [['root0/example.txt']])
    (tmp_path / 'project' / '.git').mkdir()
    assert store.claim_next() is None
    request = next(row for row in store.snapshot()['requests'] if row['type'] == 'work.edit')
    assert request['status'] == 'pending_intervention' and request['attempts'] == 0
    assert 'ownership changed' in request['reason']
    with store._connect() as conn:
        assert conn.execute('SELECT count(*) FROM task_reservations').fetchone()[0] == 0
