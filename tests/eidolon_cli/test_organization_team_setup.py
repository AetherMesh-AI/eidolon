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
