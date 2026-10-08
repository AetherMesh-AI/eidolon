"""Actual profile-scoped RPC guidance with temporary config and real ledger."""
import json

import pytest

from eidolon_cli import organization_service as services
import tui_gateway.server as server


@pytest.fixture(autouse=True)
def isolated_services(monkeypatch):
    services.stop_services()
    monkeypatch.setattr(services, '_services', {})
    yield
    assert services.stop_services()


def test_setup_tracks_current_profile_config_without_calls_or_configuration_writes(tmp_path, monkeypatch):
    from eidolon_cli import profiles, runtime_provider
    root = tmp_path / 'profile-root'
    monkeypatch.setattr(profiles, '_get_profiles_root', lambda: root / 'profiles')
    monkeypatch.setattr(profiles, '_get_default_eidolon_home', lambda: root)

    def forbidden(*_args, **_kwargs):
        pytest.fail('Reading setup must not execute work or discover a provider')

    monkeypatch.setattr(runtime_provider, 'resolve_runtime_provider', forbidden)
    monkeypatch.setattr(services, '_execute', forbidden)
    for name, provider, opt_in in [('alpha', 'openai-codex', 'false'), ('beta', 'custom', 'true')]:
        home = root / 'profiles' / name
        home.mkdir(parents=True)
        config = (f'model:\n  provider: {provider}\n  openai_runtime: codex_app_server\n'
                  '  default: secret-model-marker\n  api_key: secret-key-marker\n'
                  f'organization:\n  gateway_enabled: {opt_in}\n')
        (home / 'config.yaml').write_text(config)
        response = server.dispatch({'jsonrpc': '2.0', 'id': 1, 'method': 'organization.snapshot',
                                    'params': {'profile': name}})
        assert 'error' not in response, response
        snapshot = response['result']
        assert snapshot['runtime']['profile'] == name
        setup = snapshot['runtime']['setup']
        assert setup['provider']['status'] == ('warning' if name == 'alpha' else 'unchecked')
        assert setup['backgroundOptIn'] is (name == 'beta')
        assert not snapshot['objectives'] and not snapshot['requests']
        assert 'secret-' not in json.dumps(snapshot)
        assert (home / 'config.yaml').read_text() == config
    assert all(not service.running for service in services._services.values())
