"""Owner selection, durable project routing and fail-closed authority changes."""
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
    plan = store.claim_next()
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
    with reopened._connect() as conn:
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
    claim = store.claim_next()
    with pytest.raises(ValueError):
        store.finish(claim, {'tasks': [specification], 'workers': 1})
    # Invalid model output rolls back atomically; no partial task survives.
    snapshot = store.snapshot()
    assert snapshot['tasks'] == []
    assert snapshot['requests'][0]['status'] == 'running'
