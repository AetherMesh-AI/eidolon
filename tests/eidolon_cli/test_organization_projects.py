"""Owner selection, durable project routing and fail-closed authority changes."""
from tests.organization_package_helpers import claim_after_decomposition
from dataclasses import replace
import json
import sqlite3

import pytest

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_store import OrganizationStore
from eidolon_cli.organization_projects import objective_projects, task_project
from eidolon_cli.organization_executor import _parse_output, _prompt


def configured(tmp_path):
    roots = [tmp_path / 'alpha', tmp_path / 'beta']
    for root in roots:
        root.mkdir(exist_ok=True)
        (root / 'example.txt').write_text('old value\n')
    return OrganizationSettings.from_config({'organization': {
        'max_workers': 2, 'max_inflight': 2, 'capabilities': ['work.edit', 'work.inspect'],
        'tool_grants': ['read_file', 'patch'], 'read_roots': [str(root) for root in roots],
        'project_grants': [{'id': name, 'files': [f'root{i}/example.txt'], 'execution': {'root': f'root{i}'}}
                           for i, name in enumerate(['alpha-tests', 'beta-tests'])],
        'projects': [{'id': name, 'root': f'root{i}', 'recipe': f'{name}-tests', 'team': name}
                     for i, name in enumerate(['alpha', 'beta'])],
        'roster': [{'id': f'editor-{name}', 'name': f'Editor {name}', 'team': name,
                    'capabilities': ['work.edit', 'work.inspect'], 'tool_grants': ['read_file', 'patch']}
                   for name in ['alpha', 'beta']]}})


def plan_task(name, index, **extra):
    return {'title': 'Edit ' + name, 'description': 'Update the selected project', 'type': 'work.edit',
            'team': name, 'projectId': name, 'writePaths': [f'root{index}/example.txt'],
            'agentId': 'editor-' + name, **extra}


def test_explicit_project_selection_survives_restart_and_routes_independent_work(tmp_path):
    settings = configured(tmp_path)
    store = OrganizationStore(tmp_path / 'state.db', settings)
    objective = store.create_objective('Update both repos', project_ids=['beta', 'alpha'], idempotency_key='both')
    assert [p['id'] for p in objective['projects']] == ['alpha', 'beta']
    assert store.create_objective('Update both repos', project_ids=['alpha', 'beta'], idempotency_key='both')['id'] == objective['id']
    with pytest.raises(ValueError, match='different objective'):
        store.create_objective('Update both repos', project_ids=['alpha'], idempotency_key='both')
    plan = claim_after_decomposition(store)
    prompt = _prompt(plan, store.context(plan), 'request.plan')
    assert all(root not in prompt for root in settings.read_roots)
    output = {'tasks': [plan_task('alpha', 0), plan_task('beta', 1)], 'workers': 2}
    cleaned = _parse_output(json.dumps(output), 'request.plan', store.context(plan))
    assert [task['projectId'] for task in cleaned['tasks']] == ['alpha', 'beta']
    assert store.finish(plan, cleaned)
    hire = store.claim_next()
    assert hire['type'] == 'request.hire'
    assert store.finish(hire, {})
    reopened = OrganizationStore(store.path)
    claims = [reopened.claim_next(), store.claim_next()]
    assert {claim['agent_id'] for claim in claims} == {'editor-alpha', 'editor-beta'}
    for claim in claims:
        context = reopened.context(claim)
        assert context['task']['projectId'] == claim['team']
        assert context['task']['project']['team'] == claim['team']
        assert context['toolPolicy']['readRootAliases'] == [context['task']['project']['root']]
    with reopened._connect(write=True) as conn:
        for claim in claims:
            binding = task_project(conn, claim['task_id'])
            assert binding in objective_projects(conn, objective['id'])
        with pytest.raises(sqlite3.IntegrityError, match='Immutable'):
            conn.execute('DELETE FROM task_projects WHERE task_id=?', (claims[0]['task_id'],))
    changed = OrganizationStore(store.path, replace(settings, read_roots=tuple(reversed(settings.read_roots))))
    assert changed.claim_next() is None
    assert all(request['status'] == 'pending_intervention' for request in changed.snapshot()['requests'] if request['type'] == 'work.edit')


@pytest.mark.parametrize('attack', ['unknown_selection', 'no_selection', 'unknown_project', 'wrong_team', 'cross_root', 'missing_project', 'missing_paths', 'recipe_changed', 'duplicate_aliases'])
def test_project_authority_cannot_be_inferred_or_expanded(tmp_path, attack):
    settings = configured(tmp_path)
    if attack == 'duplicate_aliases':
        from dataclasses import asdict
        raw = json.loads(json.dumps(asdict(settings)))
        raw['projects'][1]['root'] = 'root0'
        with pytest.raises(ValueError, match='distinct'):
            OrganizationSettings.from_config({'organization': raw})
        return
    store = OrganizationStore(tmp_path / 'state.db', settings)
    if attack in {'unknown_selection', 'no_selection'}:
        with pytest.raises(ValueError):
            store.create_objective('Change repo', project_ids=['unknown'] if attack == 'unknown_selection' else [], idempotency_key='bad')
        assert store.snapshot()['objectives'] == []
        return
    objective = store.create_objective('Change repo', project_ids=['alpha'], idempotency_key='goal')
    if attack == 'recipe_changed':
        grant = settings.project_grants[0]
        changed = replace(grant, files=(*grant.files, 'root0/extra.txt'))
        store = OrganizationStore(store.path, replace(settings, project_grants=(changed, settings.project_grants[1])))
        assert store.claim_next() is None
        assert store.snapshot()['requests'][0]['status'] == 'pending_intervention'
        return
    specification = plan_task('alpha', 0)
    if attack == 'unknown_project': specification['projectId'] = 'beta'
    if attack == 'wrong_team': specification['team'] = 'beta'
    if attack == 'cross_root': specification['writePaths'] = ['root1/example.txt']
    if attack == 'missing_project': specification.pop('projectId')
    if attack == 'missing_paths': specification.pop('writePaths')
    claim = claim_after_decomposition(store)
    with pytest.raises(ValueError):
        store.finish(claim, {'tasks': [specification], 'workers': 1})
    # Invalid model output rolls back atomically; no partial task survives.
    snapshot = store.snapshot()
    assert snapshot['tasks'] == []
    assert next(r for r in snapshot['requests'] if r['id'] == claim['id'])['status'] == 'running'


def test_empty_non_git_folder_can_plan_and_inspect_without_execution_recipe(tmp_path):
    folder = tmp_path / 'empty-folder'
    folder.mkdir()
    settings = OrganizationSettings.from_config({'organization': {
        'read_roots': [str(folder)], 'capabilities': ['work.inspect', 'work.edit'],
        'tool_grants': ['read_file', 'patch'],
        'projects': [{'id': 'notes', 'root': 'root0', 'recipe': None, 'team': 'general'}],
    }})
    store = OrganizationStore(tmp_path / 'state.db', settings)
    objective = store.create_objective('Plan the contents of this folder', project_ids=['notes'], delivery_mode='managed_artifact', idempotency_key='folder')
    assert objective['projects'][0]['recipe'] is None
    with store._connect() as conn:
        binding = objective_projects(conn, objective['id'])[0]
        assert binding['recipeSha256'] is None
    assert list(folder.iterdir()) == []
    plan = claim_after_decomposition(store)
    with pytest.raises(ValueError, match='planning and inspection only'):
        store.finish(plan, {'tasks': [{'title': 'Write notes', 'description': 'Create notes',
            'type': 'work.edit', 'team': 'general', 'projectId': 'notes',
            'writePaths': ['root0/notes.txt']}], 'workers': 1})
    assert store.snapshot()['tasks'] == []
    assert list(folder.iterdir()) == []
    assert store.finish(plan, {'tasks': [{'title': 'Inspect folder', 'description': 'Inspect authorized content',
        'type': 'work.inspect', 'team': 'general', 'projectId': 'notes'}], 'workers': 1})
    reopened = OrganizationStore(store.path)
    assert reopened.settings.projects[0].recipe is None
    assert list(folder.iterdir()) == []


def test_planning_project_still_pins_folder_and_recipe_state(tmp_path):
    from eidolon_cli.organization_projects import selected_projects, validate_objective_projects
    root = tmp_path / 'notes'
    root.mkdir()
    settings = OrganizationSettings.from_config({'organization': {
        'read_roots': [str(root)],
        'projects': [{'id': 'notes', 'root': 'root0', 'recipe': None, 'team': 'general'}]}})
    store = OrganizationStore(tmp_path / 'state.db', settings)
    objective = store.create_objective('Plan', project_ids=['notes'], delivery_mode='managed_artifact', idempotency_key='plan')
    with store._connect() as conn:
        assert validate_objective_projects(conn, objective['id'], settings) == selected_projects(['notes'], settings)
        with pytest.raises(ValueError, match='binding changed'):
            validate_objective_projects(conn, objective['id'], replace(settings, read_roots=(str(tmp_path / 'other'),)))
    for invalid in ['', False, [], 'unknown']:
        with pytest.raises(ValueError):
            OrganizationSettings.from_config({'organization': {'read_roots': [str(root)],
                'projects': [{'id': 'notes', 'root': 'root0', 'recipe': invalid, 'team': 'general'}]}})


@pytest.mark.parametrize('mode,checks', [('source_project', []), ('managed_artifact', ['project_tests']), ('managed_artifact', ['source_integration'])])
def test_planning_folder_rejects_unsupported_delivery_before_admission(tmp_path, mode, checks):
    root = tmp_path / 'empty'
    root.mkdir()
    settings = OrganizationSettings.from_config({'organization': {'read_roots': [str(root)],
        'projects': [{'id': 'notes', 'root': 'root0', 'recipe': None, 'team': 'general'}]}})
    store = OrganizationStore(tmp_path / 'state.db', settings)
    with pytest.raises(ValueError, match='Planning-only folders require'):
        store.create_objective('Plan', project_ids=['notes'], delivery_mode=mode, required_checks=checks, idempotency_key='bad')
    assert store.snapshot()['objectives'] == []
    assert store.snapshot()['requests'] == []


@pytest.mark.parametrize('task_type', ['work.draft', 'work.analyze'])
def test_empty_folder_planning_reaches_independently_reviewed_acceptance(tmp_path, task_type):
    from tests.eidolon_cli.test_organization_acceptance import _integrate, _decision
    root = tmp_path / 'empty'
    root.mkdir()
    settings = OrganizationSettings.from_config({'organization': {
        'read_roots': [str(root)],
        'projects': [{'id': 'notes', 'root': 'root0', 'recipe': None, 'team': 'general'}]}})
    store = OrganizationStore(tmp_path / 'state.db', settings)
    objective = store.create_objective('Plan an empty workspace', project_ids=['notes'],
        delivery_mode='managed_artifact', idempotency_key='plan')
    plan = claim_after_decomposition(store)
    for check in ['project_tests', 'source_integration', 'managed_validation']:
        with pytest.raises(ValueError, match='Planning-only'):
            store.finish(plan, {'requiredChecks': [check], 'tasks': [{'title': 'Plan',
                'description': 'Describe a useful folder structure', 'type': task_type,
                'team': 'general', 'projectId': 'notes'}]})
        assert store.snapshot()['tasks'] == []
    store.finish(plan, {'tasks': [{'title': 'Plan', 'description': 'Describe a useful folder structure',
        'type': task_type, 'team': 'general', 'projectId': 'notes'}]})
    work = store.claim_next()
    assert work['type'] == task_type
    store.finish(work, {'summary': 'Prepared structure', 'deliverable': 'Use docs and notes folders; no source files have been created.'})
    with store._connect() as conn:
        with pytest.raises(ValueError):
            store._current_evidence(conn, objective['id'])
    review = store.claim_next()
    evidence = [item['id'] for item in store.context(review)['evidence']]
    store.finish(review, {'approved': True, 'summary': 'The supplied planning artifact meets the task.', 'evidenceIds': evidence})
    acceptance = _integrate(store)
    store.finish(acceptance, _decision(store, acceptance))
    reopened = OrganizationStore(store.path)
    assert reopened.snapshot()['objectives'][0]['status'] == 'completed'
    assert list(root.iterdir()) == []


def test_owner_amendment_cannot_add_unavailable_planning_checks(tmp_path):
    root = tmp_path / 'empty'
    root.mkdir()
    settings = OrganizationSettings.from_config({'organization': {'read_roots': [str(root)],
        'projects': [{'id': 'notes', 'root': 'root0', 'recipe': None, 'team': 'general'}]}})
    store = OrganizationStore(tmp_path / 'state.db', settings)
    store.create_objective('Plan', project_ids=['notes'], delivery_mode='managed_artifact', idempotency_key='plan')
    claim = store.claim_next()
    store.finish(claim, {'intervention': 'Please clarify the objective scope.'})
    for check in ['project_tests', 'source_integration', 'managed_validation']:
        with pytest.raises(ValueError, match='Planning-only'):
            store.resolve(claim['id'], 'amend_scope', 'Create a planning document.',
                          required_checks=[check], idempotency_key=check)
    with store._connect() as conn:
        assert json.loads(conn.execute('SELECT required_checks FROM objective_control').fetchone()[0]) == []
        assert conn.execute('SELECT COUNT(*) FROM owner_resolutions').fetchone()[0] == 0
