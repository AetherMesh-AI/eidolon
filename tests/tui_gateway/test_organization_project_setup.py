"""Guided project setup uses existing authority and the real authenticated RPC path."""
from dataclasses import replace
import json
from pathlib import Path

import pytest
import yaml

from eidolon_cli import organization_service as services
from eidolon_cli.organization_store import OrganizationStore
import tui_gateway.server as server


PROJECT = {'id': 'alpha', 'root': 'root0', 'recipe': 'alpha-tests', 'team': 'builders'}
SECOND = {'id': 'beta', 'root': 'root1', 'recipe': 'beta-tests', 'team': 'builders'}


def rpc(method, **params):
    return server.dispatch({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})


def result(method, **params):
    response = rpc(method, **params)
    assert 'error' not in response, response
    return response['result']


def write_config(home):
    home.mkdir(parents=True, exist_ok=True)
    roots = [home / 'private-alpha', home / 'private-beta']
    for root in roots:
        root.mkdir(exist_ok=True)
    text = ('# Preserve the owner\'s comments and unrelated settings.\n'
            'model:\n  provider: custom\n  default: private-model-marker\n'
            '  api_key: private-secret-marker\n'
            'unknown_extension:\n  quoted: "yes"\n  nested: [1, two]\n'
            'organization:\n  team: core\n  gateway_enabled: false\n'
            '  capabilities: [work.inspect, work.edit]\n  tool_grants: [read_file, patch]\n'
            f'  read_roots: {json.dumps([str(root) for root in roots])}\n'
            '  project_grants:\n'
            '    - id: alpha-tests\n      files: [root0/example.txt]\n      execution: {root: root0}\n'
            '    - id: beta-tests\n      files: [root1/example.txt]\n      execution: {root: root1}\n'
            '  roster:\n    - id: editor\n      name: Editor\n      team: builders\n'
            '      capabilities: [work.inspect, work.edit]\n      tool_grants: [read_file, patch]\n'
            '  projects: [] # Existing project bindings stay in this list.\n')
    (home / 'config.yaml').write_text(text, encoding='utf-8')
    return home / 'config.yaml'


@pytest.fixture(autouse=True)
def isolated_services(monkeypatch):
    services.stop_services()
    monkeypatch.setattr(services, '_services', {})

    def forbidden(*args, **kwargs):
        pytest.fail('Project setup must not start execution or discover a provider')

    from eidolon_cli import runtime_provider
    monkeypatch.setattr(services.OrganizationService, 'start', forbidden)
    monkeypatch.setattr(services, '_execute', forbidden)
    monkeypatch.setattr(runtime_provider, 'resolve_runtime_provider', forbidden)
    yield
    assert services.stop_services()


@pytest.fixture
def configured(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    monkeypatch.setenv('HERMES_HOME', str(home))
    return write_config(home)


def ledger_dump(service):
    with service.store._connect() as conn:
        return '\n'.join(conn.iterdump())


def test_setup_and_draft_are_inert_and_export_only_one_project_item(configured):
    original = configured.read_bytes()
    service = services.get_service()
    setup = result('organization.projectSetup')
    assert setup['version'] == 1 and setup['projects'] == []
    assert len(setup['revision']) == 64
    assert setup['roots'] == ['root0', 'root1']
    assert setup['recipes'] == [{'id': 'alpha-tests', 'root': 'root0'}, {'id': 'beta-tests', 'root': 'root1'}]
    assert {'core', 'builders'} <= set(setup['teams'])
    assert setup['blocked'] is False and setup['blockers'] == []
    before = ledger_dump(service)
    draft = result('organization.projectDraft', project=PROJECT, expectedRevision=setup['revision'])
    assert draft['version'] == 1 and draft['revision'] == setup['revision']
    assert draft['project'] == PROJECT
    assert yaml.safe_load(draft['yaml']) == [PROJECT]
    assert 'private-' not in json.dumps([setup, draft])
    assert configured.read_bytes() == original
    assert ledger_dump(service) == before
    assert service.settings.projects == service.store.settings.projects == ()
    assert result('organization.projectSetup') == setup
    assert not service.running
    # Repeated exports neither activate the draft nor register a duplicate.
    assert result('organization.projectDraft', project=PROJECT, expectedRevision=setup['revision']) == draft
    assert OrganizationStore(service.store.path).settings.projects == ()


@pytest.mark.parametrize('project', [
    {**PROJECT, 'id': '../escape'}, {**PROJECT, 'id': 'Alpha'},
    {**PROJECT, 'root': '/private/root'}, {**PROJECT, 'root': 'root2'},
    {**PROJECT, 'recipe': 'beta-tests'}, {**PROJECT, 'recipe': 'new-recipe'},
    {**PROJECT, 'team': 'new-team'}, {**PROJECT, 'team': ' builders'},
    {**PROJECT, 'files': ['root0/extra.txt']}, {**PROJECT, 'root': None},
    None, [],
])
def test_invalid_project_cannot_expand_authority_or_mutate_state(configured, project):
    service = services.get_service()
    setup = result('organization.projectSetup')
    before = configured.read_bytes()
    ledger = ledger_dump(service)
    response = rpc('organization.projectDraft', project=project, expectedRevision=setup['revision'])
    assert response['error']['code'] == -32602, response
    assert configured.read_bytes() == before
    assert ledger_dump(service) == ledger
    assert result('organization.projectSetup')['revision'] == setup['revision']


@pytest.mark.parametrize('field,value', [('id', 'on'), ('id', 'off'), ('team', 'on'), ('team', 'off')])
def test_yaml_ambiguous_names_remain_quoted_strings(configured, field, value):
    if field == 'team':
        configured.write_text(configured.read_text().replace('team: builders', f'team: "{value}"'))
    setup = result('organization.projectSetup')
    project = {**PROJECT, field: value}
    draft = result('organization.projectDraft', project=project, expectedRevision=setup['revision'])
    assert yaml.safe_load(draft['yaml']) == [project]
    from ruamel.yaml import YAML
    assert YAML(typ='safe').load(draft['yaml']) == [project]


def test_duplicate_existing_ids_and_roots_are_rejected(configured):
    configured.write_text(configured.read_text().replace('projects: []',
        'projects: ' + json.dumps([PROJECT])))
    setup = result('organization.projectSetup')
    before = configured.read_bytes()
    for project in [{**SECOND, 'id': PROJECT['id']}, {**PROJECT, 'id': 'other'}]:
        response = rpc('organization.projectDraft', project=project, expectedRevision=setup['revision'])
        assert response['error']['code'] == -32602, response
        assert configured.read_bytes() == before
    assert result('organization.projectSetup')['projects'] == [PROJECT]


def test_file_and_policy_changes_make_old_draft_revisions_stale(configured):
    service = services.get_service()
    setup = result('organization.projectSetup')
    configured.write_text(configured.read_text() + '# Concurrent owner edit\n')
    before = configured.read_bytes()
    assert 'error' in rpc('organization.projectDraft', project=PROJECT, expectedRevision=setup['revision'])
    assert configured.read_bytes() == before
    refreshed = result('organization.projectSetup')
    assert refreshed['revision'] != setup['revision']
    service.reload_configuration(replace(service.settings, team='new-core'))
    ledger = ledger_dump(service)
    assert 'error' in rpc('organization.projectDraft', project=PROJECT, expectedRevision=refreshed['revision'])
    assert configured.read_bytes() == before
    assert ledger_dump(service) == ledger


def test_open_work_blocks_export_and_cancelled_history_keeps_bindings(configured):
    configured.write_text(configured.read_text().replace('projects: []',
        'projects: ' + json.dumps([PROJECT])))
    result('organization.projectSetup')
    service = services.get_service()
    objective = service.store.create_objective('Inspect alpha', project_ids=['alpha'], idempotency_key='inspect')
    claim = service.store.claim_next()
    assert service.store.finish(claim, {'intervention': 'Owner decision required'})
    before = configured.read_bytes()
    ledger = ledger_dump(service)
    blocked = result('organization.projectSetup')
    assert blocked['blocked'] and 'active_objectives' in blocked['blockers']
    assert 'error' in rpc('organization.projectDraft', project=SECOND, expectedRevision=blocked['revision'])
    assert ledger_dump(service) == ledger
    assert configured.read_bytes() == before
    assert service.cancel(objective['id'])
    ready = result('organization.projectSetup')
    assert ready['blocked'] is False
    ledger = ledger_dump(service)
    draft = result('organization.projectDraft', project=SECOND, expectedRevision=ready['revision'])
    assert yaml.safe_load(draft['yaml']) == [SECOND]
    assert ledger_dump(service) == ledger
    assert configured.read_bytes() == before
    historical = service.store.snapshot()['objectives'][0]
    assert historical['status'] == 'cancelled'
    assert historical['projects'] == objective['projects']
    assert [project.id for project in service.store.settings.projects] == ['alpha']


def test_rpc_authentication_and_parameter_boundary(configured):
    for method in ['organization.projectSetup', 'organization.projectDraft', 'organization.projectSave']:
        request = {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': {}}
        assert server.handle_request(request)['error']['code'] == 4001
    for method, params in [
        ('organization.projectSetup', {'path': '/tmp/foreign'}),
        ('organization.projectSetup', {'profile': '../escape'}),
        ('organization.projectSetup', {'profile': []}),
        ('organization.projectDraft', {'project': PROJECT}),
        ('organization.projectDraft', {'project': PROJECT, 'expectedRevision': 1}),
        ('organization.projectDraft', {'project': PROJECT, 'expectedRevision': 'x' * 64, 'path': '/tmp/foreign'}),
        ('organization.projectSave', {'project': PROJECT, 'expectedRevision': 'x' * 64,
                                      'idempotencyKey': 'save', 'confirmSave': True, 'path': '/tmp/foreign'}),
        ('organization.projectSave', {'project': PROJECT, 'expectedRevision': 'x' * 64,
                                      'idempotencyKey': 'save', 'confirmSave': False}),
        ('organization.projectSave', {'project': PROJECT, 'expectedRevision': 'x' * 64,
                                      'idempotencyKey': 'save', 'confirmSave': 1}),
        ('organization.projectSave', {'project': PROJECT, 'expectedRevision': 'x' * 64,
                                      'idempotencyKey': 'save', 'confirmSave': True, 'profile': '../escape'}),
    ]:
        assert rpc(method, **params)['error']['code'] == -32602
    assert rpc('organization.addProject', project=PROJECT, expectedRevision='x' * 64)['error']['code'] == -32601


def test_named_profile_draft_uses_only_selected_authority_and_leaves_all_files_unchanged(tmp_path, monkeypatch):
    from eidolon_cli import profiles
    root = tmp_path / 'profiles-root'
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setattr(profiles, '_get_profiles_root', lambda: root / 'profiles')
    monkeypatch.setattr(profiles, '_get_default_eidolon_home', lambda: root)
    monkeypatch.setenv('HERMES_HOME', str(root))
    configs = {name: write_config(root if name == 'launch' else root / 'profiles' / name)
               for name in ['launch', 'alpha', 'beta']}
    configs['beta'].write_text(configs['beta'].read_text().replace('team: builders', 'team: others'))
    originals = {name: path.read_bytes() for name, path in configs.items()}
    alpha = result('organization.projectSetup', profile='alpha')
    result('organization.projectDraft', profile='alpha', project=PROJECT, expectedRevision=alpha['revision'])
    beta = result('organization.projectSetup', profile='beta')
    assert 'builders' not in beta['teams']
    assert 'error' in rpc('organization.projectDraft', profile='beta', project=PROJECT,
                          expectedRevision=beta['revision'])
    for name, path in configs.items():
        assert path.read_bytes() == originals[name]
    assert result('organization.projectSetup', profile='alpha')['projects'] == []


def test_managed_configuration_cannot_be_exported(configured, monkeypatch):
    from eidolon_cli import config
    monkeypatch.setattr(config.managed_scope, 'is_key_managed', lambda key: key == 'organization.projects')
    setup = result('organization.projectSetup')
    before = configured.read_bytes()
    assert setup['blocked'] and 'managed_configuration' in setup['blockers']
    assert rpc('organization.projectDraft', project=PROJECT,
               expectedRevision=setup['revision'])['error']['code'] == -32602
    assert configured.read_bytes() == before


def test_durable_team_changes_require_refresh_and_current_team_selection(configured):
    service = services.get_service()
    setup = result('organization.projectSetup')
    configuration = service.store.configuration_snapshot()
    configuration['roster'][0]['team'] = 'new-builders'
    service.store.configure_organization(configuration,
        expected_generation=service.store._policy_generation, idempotency_key='change-team')
    before = configured.read_bytes()
    assert 'error' in rpc('organization.projectDraft', project=PROJECT, expectedRevision=setup['revision'])
    fresh = result('organization.projectSetup')
    assert fresh['revision'] != setup['revision']
    assert 'new-builders' in fresh['teams'] and 'builders' not in fresh['teams']
    ledger = ledger_dump(service)
    assert 'error' in rpc('organization.projectDraft', project=PROJECT, expectedRevision=fresh['revision'])
    draft = result('organization.projectDraft', project={**PROJECT, 'team': 'new-builders'},
                   expectedRevision=fresh['revision'])
    assert draft['project']['team'] == 'new-builders'
    assert configured.read_bytes() == before
    assert ledger_dump(service) == ledger


def test_open_objective_without_pending_request_is_still_blocked(configured):
    result('organization.projectSetup')
    service = services.get_service()
    objective = service.store.create_objective('Recover interrupted planning', idempotency_key='legacy-open')
    with service.store._write() as conn:
        conn.execute("UPDATE requests SET status='completed' WHERE objective_id=?", (objective['id'],))
    setup = result('organization.projectSetup')
    assert setup['blocked'] and 'active_objectives' in setup['blockers']
    before = ledger_dump(service)
    assert 'error' in rpc('organization.projectDraft', project=PROJECT, expectedRevision=setup['revision'])
    assert ledger_dump(service) == before


def test_registry_limit_cannot_be_bypassed_by_export(configured):
    config = yaml.safe_load(configured.read_text())
    organization = config['organization']
    organization['read_roots'] = []
    organization['project_grants'] = []
    organization['projects'] = []
    for index in range(8):
        root = configured.parent / f'repository-{index}'
        root.mkdir()
        organization['read_roots'].append(str(root))
        organization['project_grants'].append({'id': f'recipe-{index}',
            'files': [f'root{index}/example.txt'], 'execution': {'root': f'root{index}'}})
        organization['projects'].append({'id': f'project-{index}', 'root': f'root{index}',
                                        'recipe': f'recipe-{index}', 'team': 'builders'})
    configured.write_text(yaml.safe_dump(config))
    setup = result('organization.projectSetup')
    before = configured.read_bytes()
    response = rpc('organization.projectDraft', project={'id': 'extra', 'root': 'root0',
                   'recipe': 'recipe-0', 'team': 'builders'}, expectedRevision=setup['revision'])
    assert response['error']['code'] == -32602
    assert 'at most 8' in response['error']['message']
    assert configured.read_bytes() == before
    assert result('organization.projectSetup')['projects'] == organization['projects']


def test_reading_external_file_changes_never_adopts_them_into_live_policy(configured, monkeypatch):
    service = services.get_service()
    setup = result('organization.projectSetup')
    settings = service.store.settings
    ledger = ledger_dump(service)
    configured.write_text(configured.read_text().replace('team: builders', 'team: new-builders'))
    before = configured.read_bytes()

    def forbidden(*args, **kwargs):
        pytest.fail('Reading or exporting setup must never adopt file authority')

    monkeypatch.setattr(service.store, '_adopt_policy', forbidden)
    fresh = result('organization.projectSetup')
    assert fresh['revision'] != setup['revision']
    assert 'new-builders' in fresh['teams']
    result('organization.projectDraft', project={**PROJECT, 'team': 'new-builders'},
           expectedRevision=fresh['revision'])
    assert service.store.settings == service.settings == settings
    assert configured.read_bytes() == before
    assert ledger_dump(service) == ledger


def test_export_does_not_reserve_project_or_change_later_intake(configured):
    setup = result('organization.projectSetup')
    draft = result('organization.projectDraft', project=PROJECT, expectedRevision=setup['revision'])
    service = services.get_service()
    # Draft review can overlap intake in another runtime because exporting has
    # no effect on project authority and is not an activation acknowledgement.
    outsider = OrganizationStore(service.store.path)
    objective = outsider.create_objective('Unchanged routing', idempotency_key='after-draft')
    assert objective['projects'] == []
    assert outsider.settings.projects == ()
    assert service.store.settings.projects == ()
    assert yaml.safe_load(configured.read_text())['organization']['projects'] == []
    assert yaml.safe_load(draft['yaml']) == [PROJECT]
    assert result('organization.projectSetup')['blocked'] is True


@pytest.mark.parametrize('warm', [False, True], ids=['cold', 'warm'])
@pytest.mark.parametrize('failure', ['malformed', 'nonmapping', 'unreadable', 'invalid_utf8'])
@pytest.mark.parametrize('method', ['organization.projectSetup', 'organization.projectDraft'])
def test_bad_configuration_fails_privately_without_recovery_writes(configured, monkeypatch, warm, failure, method):
    revision = '0' * 64
    service = None
    if warm:
        revision = result('organization.projectSetup')['revision']
        service = services.get_service()
    content = {
        'malformed': b'private-syntax-marker: [broken\n',
        'nonmapping': b'- private-syntax-marker\n',
        'invalid_utf8': b'private-syntax-marker: \xff\xfe\n',
    }.get(failure, configured.read_bytes())
    configured.write_bytes(content)
    before_files = {path.relative_to(configured.parent) for path in configured.parent.rglob('*')}
    ledger = ledger_dump(service) if service else None
    read_bytes = Path.read_bytes
    if failure == 'unreadable':
        def denied(path):
            if path == configured:
                raise PermissionError(f'private-syntax-marker cannot read {configured}')
            return read_bytes(path)
        monkeypatch.setattr(Path, 'read_bytes', denied)
    params = {} if method == 'organization.projectSetup' else {
        'project': PROJECT, 'expectedRevision': revision}
    response = rpc(method, **params)
    assert response['error']['code'] == -32602, response
    message = response['error']['message']
    assert 'Profile configuration' in message
    assert 'private-syntax-marker' not in message
    assert str(configured) not in message
    assert str(configured.parent) not in message
    assert read_bytes(configured) == content
    assert {path.relative_to(configured.parent) for path in configured.parent.rglob('*')} == before_files
    if warm:
        assert ledger_dump(service) == ledger
    else:
        assert services._services == {}
        assert not services.state_path().exists()


def test_fresh_setup_and_draft_never_create_ledger_or_service(configured, monkeypatch):
    before = configured.read_bytes()
    before_files = {path.relative_to(configured.parent) for path in configured.parent.rglob('*')}

    def forbidden(*args, **kwargs):
        pytest.fail('Read-only repository guidance must not initialize a service or ledger')

    monkeypatch.setattr(services, 'get_service', forbidden)
    monkeypatch.setattr(OrganizationStore, '__init__', forbidden)
    setup = result('organization.projectSetup')
    draft = result('organization.projectDraft', project=PROJECT, expectedRevision=setup['revision'])
    assert yaml.safe_load(draft['yaml']) == [PROJECT]
    assert configured.read_bytes() == before
    assert {path.relative_to(configured.parent) for path in configured.parent.rglob('*')} == before_files
    assert services._services == {}
    assert not services.state_path().exists()


def test_cold_existing_ledger_never_adopts_externally_changed_file_authority(configured, monkeypatch):
    service = services.get_service()
    settings = service.store.settings
    assert services.stop_services()
    services._services.clear()
    before_ledger = ledger_dump(service)
    config = yaml.safe_load(configured.read_text())
    config['organization']['roster'][0]['team'] = 'new-builders'
    config['organization']['read_roots'].reverse()
    config['organization']['projects'] = [{**PROJECT, 'team': 'new-builders'}]
    configured.write_text(yaml.safe_dump(config))
    before = configured.read_bytes()

    def forbidden(*args, **kwargs):
        pytest.fail('Read-only repository guidance must not reopen an adopting store or service')

    monkeypatch.setattr(services, 'get_service', forbidden)
    monkeypatch.setattr(OrganizationStore, '__init__', forbidden)
    setup = result('organization.projectSetup')
    assert setup['projects'] == config['organization']['projects']
    draft = result('organization.projectDraft', project={**SECOND, 'team': 'new-builders'},
                   expectedRevision=setup['revision'])
    assert draft['project']['id'] == SECOND['id']
    assert ledger_dump(service) == before_ledger
    assert service.store.settings == service.settings == settings
    assert configured.read_bytes() == before
    assert services._services == {}
