"""Starter previews are untrusted, read-only and creation is an exact owner decision."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from eidolon_cli import organization_team_setup as team
from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_metadata import save_metadata
from eidolon_cli.organization_owner_policy import OwnerPolicyError
from eidolon_cli.organization_store import OrganizationStore
from eidolon_constants import set_eidolon_home_override, reset_eidolon_home_override


@pytest.fixture
def profile(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    home = tmp_path / '.eidolon'
    home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    token = set_eidolon_home_override(home)
    (home / 'config.yaml').write_text(json.dumps({'model': {'default': 'local', 'provider': 'openai'}}))
    path = home / 'organization' / 'state.db'
    identity = save_metadata(path, display_name='Team', owner_display_name='Owner',
        expected_organization_id=None, expected_revision=None, idempotency_key='metadata')['metadata']
    store = OrganizationStore(path)
    yield SimpleNamespace(home=home, path=path, identity=identity, store=store)
    reset_eidolon_home_override(token)


def params(profile):
    setup = team.team_setup(profile.home, profile.identity['organizationId'])
    return {'expectedOrganizationId': profile.identity['organizationId'],
            'sourceRevision': setup['sourceRevision'], 'team': deepcopy(setup['presets'][0]['team'])}


def dump(profile):
    with sqlite3.connect(profile.path) as conn:
        return list(conn.iterdump())


def confirmed(profile, draft=None):
    value = draft or params(profile)
    preview = team.preview_team(profile.home, value)
    return {**value, 'previewVersion': preview['previewVersion'], 'idempotencyKey': 'create', 'confirmed': True}


def test_reads_preview_no_mutation_and_exact_idempotent_creation(profile, monkeypatch):
    before = dump(profile)
    value = params(profile)
    value['team']['executive']['name'] = 'Athena'
    apply = confirmed(profile, value)
    assert dump(profile) == before
    def forbidden(*args, **kwargs):
        raise AssertionError('Setup must not construct a mutable service/store')
    monkeypatch.setattr(OrganizationStore, '__init__', forbidden)
    result = team.create_team(profile.home, apply)
    assert result['state'] == 'created'
    after = dump(profile)
    assert team.create_team(profile.home, apply) == result
    assert dump(profile) == after
    assert team.team_setup(profile.home, profile.identity['organizationId'])['state'] == 'configured'
    with sqlite3.connect(profile.path) as conn:
        assert conn.execute("SELECT name FROM agents WHERE id='executive'").fetchone()[0] == 'Athena'
        assert conn.execute('SELECT count(*) FROM objectives').fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM requests').fetchone()[0] == 0
        assert conn.execute("SELECT source FROM staff_state WHERE agent_id='worker-1'").fetchone()[0] == 'retired'
        assert json.loads(conn.execute("SELECT policy FROM staff_state WHERE agent_id='analyst'").fetchone()[0])['tools'] == []


def test_restart_retains_names_identity_and_no_duplicate_seed_workers(profile):
    apply = confirmed(profile)
    apply['team']['executive']['name'] = 'Athena'
    apply['previewVersion'] = team.preview_team(profile.home, apply)['previewVersion']
    team.create_team(profile.home, apply)
    with sqlite3.connect(profile.path) as conn:
        identities = dict(conn.execute('SELECT agent_id,identity_id FROM agent_identity'))
    reopened = OrganizationStore(profile.path, OrganizationSettings())
    with sqlite3.connect(profile.path) as conn:
        assert dict(conn.execute('SELECT agent_id,identity_id FROM agent_identity')) == identities
        assert conn.execute("SELECT name FROM agents WHERE id='executive'").fetchone()[0] == 'Athena'
        assert conn.execute("SELECT active FROM staff_state WHERE agent_id='worker-1'").fetchone()[0] == 0
    assert [staff.id for staff in reopened.settings.roster] == ['team-lead', 'analyst', 'writer']


@pytest.mark.parametrize('change', [
    lambda d: d.update(credentials='secret'),
    lambda d: d['executive'].update(id='other'),
    lambda d: d['members'][1].update(tool_grants=['read_file']),
    lambda d: d['members'][1].update(provider='openai', model='paid'),
    lambda d: d['members'][1].update(authority=['staff.manage']),
    lambda d: d['members'][1].update(manager_id='missing'),
    lambda d: d['members'][0].update(manager_id='analyst'),
    lambda d: d['members'][1].update(id='team-lead'),
    lambda d: d['members'][1].update(name='TEAM LEAD'),
    lambda d: d['members'][1].update(capabilities=['request.plan']),
    lambda d: d['members'][0].update(capabilities=[]),
    lambda d: d['members'][0].update(capabilities=['request.plan', 'request.integrate', 'request.hire']),
    lambda d: d['members'][1].update(purpose=''),
    lambda d: d['members'][1].update(responsibilities=[]),
    lambda d: d['members'][1].update(team='other'),
    lambda d: d['members'][1].update(capabilities=['work.inspect']),
])
def test_untrusted_fields_invalid_roles_routes_names_fail_without_changes(profile, change):
    value = params(profile)
    change(value['team'])
    before = dump(profile)
    with pytest.raises(ValueError):
        team.preview_team(profile.home, value)
    assert dump(profile) == before


def test_stale_preview_confirmation_identity_and_retry_conflicts(profile):
    apply = confirmed(profile)
    with pytest.raises(ValueError):
        team.create_team(profile.home, {**apply, 'confirmed': False})
    with pytest.raises(OwnerPolicyError, match='preview_conflict'):
        team.create_team(profile.home, {**apply, 'previewVersion': 'stale'})
    value = deepcopy(apply)
    value['team']['members'][1]['name'] = 'Changed'
    with pytest.raises(OwnerPolicyError, match='preview_conflict'):
        team.create_team(profile.home, value)
    team.create_team(profile.home, apply)
    with pytest.raises(OwnerPolicyError, match='idempotency_conflict'):
        team.create_team(profile.home, value)


@pytest.mark.parametrize('existing', ['objective', 'owner_chat', 'roster', 'memory'])
def test_existing_organization_never_replaced(profile, existing):
    value = params(profile)
    if existing == 'objective':
        profile.store.create_objective('Keep history', idempotency_key='goal')
    elif existing == 'roster':
        profile.store.configure_organization({'roster': []}, expected_generation=1, idempotency_key='manual')
    elif existing == 'memory':
        with sqlite3.connect(profile.path) as conn:
            conn.execute("UPDATE agent_context SET revision=1 WHERE agent_id='executive'")
    else:
        with sqlite3.connect(profile.path) as conn:
            identity = conn.execute("SELECT identity_id FROM agent_identity WHERE agent_id='executive'").fetchone()[0]
            conn.execute('INSERT INTO owner_chat_threads VALUES (?,?,?,?,?,?,?)',
                         ('thread', identity, 'executive', '{}', 1, 32, 100000))
    before = dump(profile)
    assert team.team_setup(profile.home, profile.identity['organizationId'])['state'] == 'configured'
    with pytest.raises(OwnerPolicyError):
        team.preview_team(profile.home, value)
    assert dump(profile) == before


def test_codex_app_server_and_missing_model_readiness_are_truthful(profile):
    (profile.home / 'config.yaml').write_text(json.dumps({'model': {'provider': 'openai-codex', 'default': 'gpt', 'openai_runtime': 'codex_app_server'}}))
    assert 'codex_app_server' in team.team_setup(profile.home, profile.identity['organizationId'])['readiness']['blockers']
    (profile.home / 'config.yaml').write_text('{}')
    # Default config may contain a default model; this is config guidance, never a credential probe.
    assert team.team_setup(profile.home, profile.identity['organizationId'])['readiness']['status'] in {'unchecked', 'blocked'}


def test_create_failure_rolls_back_roster_names_and_receipt(profile):
    apply = confirmed(profile)
    with sqlite3.connect(profile.path) as conn:
        conn.execute("CREATE TRIGGER reject_setup BEFORE INSERT ON organization_team_setup BEGIN SELECT RAISE(ABORT,'injected_failure'); END")
    before = dump(profile)
    with pytest.raises(OwnerPolicyError):
        team.create_team(profile.home, apply)
    assert dump(profile) == before
    with sqlite3.connect(profile.path) as conn:
        conn.execute('DROP TRIGGER reject_setup')
    assert team.create_team(profile.home, apply)['state'] == 'created'


def test_concurrent_creates_are_atomic_and_idempotent(profile):
    from concurrent.futures import ThreadPoolExecutor
    apply = confirmed(profile)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: team.create_team(profile.home, apply), range(2)))
    assert results[0] == results[1]
    with sqlite3.connect(profile.path) as conn:
        assert conn.execute('SELECT count(*) FROM organization_team_setup').fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM organization_configuration').fetchone()[0] == 1


@pytest.mark.parametrize('model', [
    {'provider': 'openai-api', 'default': 'gpt', 'openai_runtime': 'codex_app_server'},
    {'provider': 'anthropic', 'default': 'claude'},
    {'provider': 'bedrock', 'default': 'claude'},
])
def test_known_unsupported_generation_routes_are_blocked_before_call(profile, model):
    (profile.home / 'config.yaml').write_text(json.dumps({'model': model}))
    readiness = team.team_setup(profile.home, profile.identity['organizationId'])['readiness']
    assert readiness['status'] == 'blocked'
    assert 'generation_route_unsupported' in readiness['blockers']
    # Manual creation is independent of inference readiness.
    assert team.create_team(profile.home, confirmed(profile))['state'] == 'created'


def test_new_members_disclose_legacy_inheritance_without_inventing_assignments(profile):
    from tests.eidolon_cli.test_organization_owner_policy import routing
    configuration = json.loads((profile.home / 'config.yaml').read_text())
    configuration['organization'] = routing()
    (profile.home / 'config.yaml').write_text(json.dumps(configuration))
    value = params(profile)
    preview = team.preview_team(profile.home, value)
    assert preview['readiness']['status'] == 'unchecked'
    assert preview['readiness']['warnings'] == ['new_member_routes_unassigned']
    assert preview['executiveModelMode'] == 'execFixed'
    assert all('provider' not in member and 'model' not in member for member in preview['team']['members'])
    original = (profile.home / 'config.yaml').read_bytes()
    team.create_team(profile.home, confirmed(profile, value))
    assert (profile.home / 'config.yaml').read_bytes() == original


def test_assignments_for_retired_seed_workers_require_separate_owner_change(profile):
    from tests.eidolon_cli.test_organization_owner_policy import routing
    configuration = json.loads((profile.home / 'config.yaml').read_text())
    configuration['organization'] = routing(agent='worker-1')
    (profile.home / 'config.yaml').write_text(json.dumps(configuration))
    value = params(profile)
    before = dump(profile)
    with pytest.raises(ValueError, match='Model assignments must identify configured persistent agents'):
        team.preview_team(profile.home, value)
    assert dump(profile) == before


def forbid_setup_side_effects(monkeypatch):
    """Leave real config/readiness/SQLite reads intact; forbid execution and I/O sends."""
    import socket
    from eidolon_cli import runtime_provider, organization_team_generation
    from tests.eidolon_cli.test_organization_owner_policy import forbid_execution
    forbid_execution(monkeypatch)
    def forbidden(*args, **kwargs):
        raise AssertionError('Passive team setup attempted provider initialization or network access')
    monkeypatch.setattr(runtime_provider, 'resolve_runtime_provider', forbidden)
    monkeypatch.setattr(organization_team_generation, '_runtime_kwargs', forbidden)
    monkeypatch.setattr(organization_team_generation, '_create_agent', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)


def durable_files(home):
    # SQLite may maintain transient WAL/SHM bookkeeping even for read-only clients.
    # Compare actual database/config bytes as well as every other durable file.
    return {str(path.relative_to(home)): path.read_bytes() for path in home.rglob('*')
            if path.is_file() and not path.name.endswith(('-wal', '-shm'))}


@pytest.mark.parametrize('existing_directory', [False, True])
def test_unconfigured_team_status_never_creates_home_config_or_state(tmp_path, monkeypatch, existing_directory):
    import uuid
    home = tmp_path / 'unopened-profile'
    if existing_directory:
        home.mkdir()
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(home))
    token = set_eidolon_home_override(home)
    forbid_setup_side_effects(monkeypatch)
    try:
        organization_id = str(uuid.uuid4())
        statuses = [team.team_setup(home, organization_id) for _ in range(3)]
        assert all(status == statuses[0] for status in statuses)
        assert statuses[0]['state'] == 'unconfigured'
        assert statuses[0]['reason'] == 'unconfigured'
        assert home.exists() is existing_directory
        assert not (home / 'config.yaml').exists()
        assert not (home / 'organization').exists()
        assert durable_files(home) == {}
    finally:
        reset_eidolon_home_override(token)


def test_metadata_only_status_does_not_initialize_execution_tables(tmp_path, monkeypatch):
    home = tmp_path / '.eidolon'
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('HERMES_HOME', str(home))
    token = set_eidolon_home_override(home)
    try:
        path = home / 'organization' / 'state.db'
        identity = save_metadata(path, display_name='Names only', owner_display_name='Owner',
            expected_organization_id=None, expected_revision=None, idempotency_key='metadata')['metadata']
        before = durable_files(home)
        with sqlite3.connect(path) as conn:
            logical_before = list(conn.iterdump())
        forbid_setup_side_effects(monkeypatch)
        statuses = [team.team_setup(home, identity['organizationId']) for _ in range(3)]
        assert all(status == statuses[0] for status in statuses)
        assert statuses[0]['state'] == 'unconfigured'
        assert statuses[0]['reason'] == 'execution_not_initialized'
        assert durable_files(home) == before
        with sqlite3.connect(path) as conn:
            assert list(conn.iterdump()) == logical_before
            assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name IN ('agents','requests','organization_policy')").fetchone()
        assert not (home / 'config.yaml').exists()
    finally:
        reset_eidolon_home_override(token)


@pytest.mark.parametrize('config', [
    {'model': {'default': 'local', 'provider': 'openai'}},
    {'_config_version': 1, 'model': {'default': 'gpt', 'provider': 'openai-api', 'openai_runtime': 'codex_app_server'}},
    {'_config_version': 1, 'model': {'default': 'anthropic/claude', 'provider': 'nous'},
     'nous': {'anthropic_wire': 'native'}},
])
def test_repeated_seeded_status_preserves_database_config_and_logical_state(profile, monkeypatch, config):
    (profile.home / 'config.yaml').write_text(json.dumps(config))
    before = durable_files(profile.home)
    logical_before = dump(profile)
    forbid_setup_side_effects(monkeypatch)
    statuses = [team.team_setup(profile.home, profile.identity['organizationId']) for _ in range(3)]
    assert all(status == statuses[0] for status in statuses)
    assert statuses[0]['state'] == 'available'
    if config.get('_config_version') == 1:
        assert statuses[0]['readiness']['status'] == 'blocked'
    else:
        assert statuses[0]['readiness']['status'] == 'unchecked'
    assert durable_files(profile.home) == before
    assert dump(profile) == logical_before


@pytest.mark.parametrize('state', ['absent', 'empty_home', 'metadata_only', 'seeded'])
def test_cold_import_and_status_are_nonprovisioning(tmp_path, state):
    """A fresh interpreter must not hide first-import or first-load writes in caches."""
    import os
    import subprocess
    import sys
    import uuid
    from contextlib import closing
    home = tmp_path / '.eidolon'
    path = home / 'organization' / 'state.db'
    organization_id = str(uuid.uuid4())
    if state == 'empty_home':
        home.mkdir()
    if state in {'metadata_only', 'seeded'}:
        identity = save_metadata(path, display_name='Cold profile', owner_display_name='Owner',
            expected_organization_id=None, expected_revision=None, idempotency_key='metadata')['metadata']
        organization_id = identity['organizationId']
        (home / 'config.yaml').write_text(json.dumps({'_config_version': 1,
            'model': {'provider': 'openai-api', 'default': 'local'}}))
    if state == 'seeded':
        OrganizationStore(path)
    before = durable_files(tmp_path)
    directories_before = sorted(str(item.relative_to(tmp_path)) for item in tmp_path.rglob('*') if item.is_dir())
    if path.exists():
        with closing(sqlite3.connect(path)) as conn:
            logical_before = list(conn.iterdump())
    script = '''
import json
import sys
from pathlib import Path

external_attempts = []
def deny_external(event, args):
    if event in {'socket.connect', 'subprocess.Popen'}:
        external_attempts.append(event)
        raise AssertionError('Passive setup attempted external I/O: ' + event)
sys.addaudithook(deny_external)

from agent import secret_scope
from eidolon_cli import runtime_provider

def forbidden(*args, **kwargs):
    external_attempts.append('execution_or_provider')
    raise AssertionError('Passive setup initialized execution, credentials or a provider')
secret_scope.get_secret = forbidden
runtime_provider.resolve_runtime_provider = forbidden
from eidolon_cli import organization_service, organization_store
organization_service.get_service = forbidden
organization_service.OrganizationService.__init__ = forbidden
organization_service.OrganizationService.start = forbidden
organization_store.OrganizationStore.__init__ = forbidden
from eidolon_cli import organization_team_setup
home = Path(sys.argv[1])
values = [organization_team_setup.team_setup(home, sys.argv[2]) for _ in range(2)]
assert values[0] == values[1]
assert not external_attempts, external_attempts
print(json.dumps(values[0]))
'''
    env = {**os.environ, 'HOME': str(tmp_path), 'USERPROFILE': str(tmp_path),
           'HERMES_HOME': str(home), 'PYTHONDONTWRITEBYTECODE': '1'}
    env.pop('EIDOLON_HOME', None)
    completed = subprocess.run([sys.executable, '-c', script, str(home), organization_id],
        cwd=Path(__file__).resolve().parents[2], env=env, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    status = json.loads(completed.stdout.strip().splitlines()[-1])
    assert status['state'] == ('available' if state == 'seeded' else 'unconfigured')
    assert durable_files(tmp_path) == before
    assert sorted(str(item.relative_to(tmp_path)) for item in tmp_path.rglob('*') if item.is_dir()) == directories_before
    if path.exists():
        with closing(sqlite3.connect(path)) as conn:
            assert list(conn.iterdump()) == logical_before


def test_passive_config_cache_does_not_skip_later_normal_home_initialization(profile):
    from eidolon_cli.config import load_config, load_config_readonly
    soul = profile.home / 'SOUL.md'
    assert not soul.exists()
    passive = load_config_readonly(initialize_home=False)
    assert not soul.exists()
    assert load_config() == passive
    assert soul.exists()
    assert (profile.home / 'sessions').is_dir()
