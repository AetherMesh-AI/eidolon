"""First-use guidance must stay inert and must not certify live execution."""
import builtins
import copy
import io
import json
import socket
import subprocess

import pytest

from eidolon_cli.organization_config import OrganizationSettings
from eidolon_cli.organization_setup import setup_view
from eidolon_cli.organization_store import OrganizationStore
from eidolon_cli.runtime_provider import _maybe_apply_codex_app_server_runtime


def test_default_roster_includes_inherited_model_leaders_but_not_control_executors(tmp_path):
    store = OrganizationStore(tmp_path / 'organization.db')
    agents = store.snapshot()['agents']
    setup = setup_view({}, agents)
    inherited = {agent['id'] for agent in agents if agent['id'] in {'executive', 'manager', 'reviewer'}
                 or agent['id'].startswith('worker-')}
    assert setup['provider'] == {'status': 'unchecked', 'blockers': [],
                                 'inheritedMembers': len(inherited), 'overriddenMembers': 0}
    assert not setup['backgroundOptIn']
    assert store.snapshot()['objectives'] == []


def test_maximum_multiteam_roster_counts_include_its_persistent_reviewers(tmp_path):
    roster = [{'id': f'writer-{index}', 'name': f'Writer {index}', 'team': f'team-{index}',
               'capabilities': ['work.draft']} for index in range(64)]
    settings = OrganizationSettings.from_config({'organization': {'max_members': 64, 'roster': roster}})
    agents = OrganizationStore(tmp_path / 'organization.db', settings=settings).snapshot()['agents']
    setup = setup_view({}, agents)
    model_members = [agent for agent in agents if set(agent['capabilities']).intersection(
        {'work.draft', 'request.plan', 'request.review', 'request.accept'})]
    assert setup['provider']['inheritedMembers'] == len(model_members)
    assert setup['provider']['inheritedMembers'] > 128
    assert setup['provider']['overriddenMembers'] == 0


@pytest.mark.parametrize('provider', ['openai', 'openai-codex', 'codex', 'anthropic', 'auto', 'custom'])
def test_configured_transport_warning_matches_explicit_runtime_opt_in(provider):
    from eidolon_cli.providers import normalize_provider
    config = {'provider': provider, 'openai_runtime': 'codex_app_server'}
    result = setup_view({'model': config}, [{'lifecycle': 'active', 'capabilities': ['request.plan']}])
    actual_mode = _maybe_apply_codex_app_server_runtime(
        provider=normalize_provider(provider), api_mode='chat_completions', model_cfg=config)
    if result['provider']['status'] == 'warning':
        assert actual_mode == 'codex_app_server'
    if provider == 'openai-codex':
        assert result['provider']['status'] == 'warning'
    assert result['provider']['status'] != 'ready'


def test_overrides_disabled_members_and_built_in_reviewers_are_not_conflated(tmp_path):
    settings = OrganizationSettings.from_config({'organization': {'roster': [
        {'id': 'writer', 'name': 'Writer', 'provider': 'anthropic', 'model': 'selected-model', 'capabilities': ['work.draft']},
        {'id': 'inactive', 'name': 'Inactive', 'provider': 'openai-codex', 'model': 'selected-model', 'enabled': False,
         'capabilities': ['work.draft']},
    ]}})
    agents = OrganizationStore(tmp_path / 'org.db', settings=settings).snapshot()['agents']
    config = {'model': {'provider': 'openai-codex', 'openai_runtime': 'codex_app_server'}}
    result = setup_view(config, agents)
    assert result['provider']['status'] == 'warning'  # Built-in leaders still inherit.
    assert result['provider']['overriddenMembers'] == 1
    config['model']['provider'] = 'anthropic'
    result = setup_view(config, agents)
    assert result['provider']['status'] == 'unchecked'  # Disabled Codex member cannot block.


def test_projection_never_resolves_credentials_reads_files_or_returns_raw_configuration(monkeypatch):
    config = {'model': {'provider': 'custom', 'default': 'SECRET_MODEL', 'api_key': 'SECRET_KEY',
                        'base_url': 'https://secret.example/private?token=SECRET'},
              'organization': {'gateway_enabled': True, 'read_roots': ['/private/path']}}
    original = copy.deepcopy(config)
    agents = [{'lifecycle': 'active', 'capabilities': ['request.plan']}]

    def forbidden(*_args, **_kwargs):
        pytest.fail('Setup projection attempted external discovery')

    from eidolon_cli import runtime_provider
    monkeypatch.setattr(runtime_provider, 'resolve_runtime_provider', forbidden)
    monkeypatch.setattr(builtins, 'open', forbidden)
    monkeypatch.setattr(io, 'open', forbidden)
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    result = setup_view(config, agents)
    assert result['backgroundOptIn'] is True
    assert result['provider']['status'] == 'unchecked'
    serialized = json.dumps(result)
    assert not any(value in serialized for value in ['SECRET', 'private', 'custom', 'selected-model'])
    assert config == original


@pytest.mark.parametrize('organization', [None, [], {'gateway_enabled': 'true'}, {'gateway_enabled': 1}])
def test_only_explicit_boolean_reports_background_opt_in(organization):
    assert setup_view({'model': None, 'organization': organization}, [])['backgroundOptIn'] is False


def test_alias_and_automatic_routes_are_unchecked_without_credential_resolution():
    # An alias can select a saved custom endpoint before the built-in alias.
    # The setup panel must not infer an app-server route from that spelling.
    config = {'model': {'provider': 'codex', 'openai_runtime': 'codex_app_server'},
              'providers': {'codex': {'api': 'https://fixture.invalid/v1', 'api_mode': 'chat_completions'}}}
    agents = [{'lifecycle': 'active', 'capabilities': ['request.plan']}]
    assert setup_view(config, agents)['provider']['status'] == 'unchecked'
    config['model']['provider'] = 'auto'
    assert setup_view(config, agents)['provider']['status'] == 'unchecked'


def test_configured_warning_does_not_claim_the_resolved_fallback_is_blocked(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from eidolon_cli import runtime_provider
    from eidolon_cli.config import load_config_readonly
    from eidolon_cli.organization_executor import _guard_route

    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    config_path = tmp_path / 'config.yaml'
    config_text = ('model:\n  provider: openai-codex\n  default: fixture-model\n'
                   '  openai_runtime: codex_app_server\n')
    config_path.write_text(config_text)
    # A pool miss can take the OAuth fallback, which currently uses Responses.
    # Keep the real resolver ladder while replacing only credential sources.
    monkeypatch.setattr(runtime_provider, 'load_pool',
                        lambda _provider: SimpleNamespace(has_credentials=lambda: False))
    monkeypatch.setattr(runtime_provider, 'resolve_codex_runtime_credentials',
                        lambda: {'api_key': 'synthetic-test-only',
                                 'base_url': 'https://chatgpt.com/backend-api/codex'})

    def forbidden(*_args, **_kwargs):
        pytest.fail('Configuration regression attempted a network or process call')

    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    runtime = runtime_provider.resolve_runtime_provider(
        requested='openai-codex', target_model='fixture-model')
    assert runtime['api_mode'] == 'codex_responses'
    _guard_route(runtime, 'fixture-model')

    agents = [{'lifecycle': 'active', 'capabilities': ['request.plan']}]
    setup = setup_view(load_config_readonly(), agents)
    assert setup['provider']['status'] == 'warning'
    assert setup['provider']['blockers'] == ['codex_app_server']
    assert config_path.read_text() == config_text
