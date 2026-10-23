"""Real isolated workspace/auth files; synthetic tokens and refresh responses only."""
import base64
import json
import time
from types import SimpleNamespace

import pytest

from eidolon_cli import profile_isolation as isolation
from eidolon_cli.profile_codex_auth import ProfileCodexAuth, ENDPOINT
from tests.eidolon_cli.test_organization_workspaces import origin, create, scope, deny_external_network  # noqa: F401


def token(account='synthetic-account', expiry=None):
    claims = {'exp': time.time() + 3600 if expiry is None else expiry,
              'https://api.openai.com/auth': {'chatgpt_account_id': account}}
    encoded = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=')
    return 'synthetic.' + encoded + '.unsigned'


def auth(home, access=None):
    value = {'version': 1, 'providers': {'openai-codex': {'tokens': {
        'access_token': access or token(), 'refresh_token': 'synthetic-refresh'}},
        'untouched': {'state': 'preserve'}}, 'credential_pool': {'untouched': []}}
    (home / 'auth.json').write_text(json.dumps(value))
    return value


@pytest.fixture
def workspace(origin, monkeypatch):
    home, _ = create(origin)
    (home / 'config.yaml').write_text(json.dumps({'model': {'provider': 'openai-codex', 'default': 'gpt-5.4'}}))
    deny_external_network(monkeypatch)
    with scope(home):
        yield home


def test_exact_owned_runtime_without_ambient_fallback(workspace, monkeypatch):
    value = auth(workspace)
    monkeypatch.setenv('OPENAI_API_KEY', 'foreign-key')
    monkeypatch.setenv('HERMES_CODEX_BASE_URL', 'https://foreign.invalid')
    from eidolon_cli import auth as facade
    monkeypatch.setattr(facade, 'resolve_codex_runtime_credentials', lambda **kw: pytest.fail('ambient resolver'))
    route = isolation.isolated_runtime(requested='openai-codex')
    assert route['api_key'] == value['providers']['openai-codex']['tokens']['access_token']
    assert route['base_url'] == ENDPOINT
    assert route['api_mode'] == 'codex_responses'
    assert route['source'] == 'profile-local-codex-auth'
    with pytest.raises(isolation.ProfileIsolationError):
        isolation.isolated_runtime(requested='openai-api')


@pytest.mark.parametrize('change', ['missing', 'malformed', 'revoked', 'different', 'symlink'])
def test_auth_missing_replaced_or_revoked_is_refused(workspace, change, tmp_path):
    auth(workspace)
    binding = ProfileCodexAuth.read(workspace)
    path = workspace / 'auth.json'
    if change == 'missing': path.unlink()
    elif change == 'malformed': path.write_text('{')
    elif change == 'revoked': path.write_text('{"providers":{}}')
    elif change == 'different': auth(workspace, token('other-account'))
    else:
        other = tmp_path / 'foreign.json'
        other.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(other)
    with pytest.raises(isolation.ProfileIsolationError): binding.check()


def test_refresh_is_profile_bound_and_preserves_other_state(workspace, monkeypatch):
    before = auth(workspace, token(expiry=time.time() - 1))
    binding = ProfileCodexAuth.read(workspace)
    from eidolon_cli import auth_codex
    calls = []
    fresh = token()
    def refresh(access, secret, **kwargs):
        calls.append(kwargs)
        assert access == before['providers']['openai-codex']['tokens']['access_token']
        assert secret == 'synthetic-refresh'
        return {'access_token': fresh, 'refresh_token': 'synthetic-rotated'}
    monkeypatch.setattr(auth_codex, 'refresh_codex_oauth_pure', refresh)
    checks = []
    assert binding.refresh(lambda: checks.append(True))
    assert len(checks) == 2
    assert calls == [{'timeout_seconds': 15, 'private_transport': True}]
    after = json.loads((workspace / 'auth.json').read_text())
    assert after['providers']['untouched'] == before['providers']['untouched']
    assert after['credential_pool'] == before['credential_pool']
    assert after['providers']['openai-codex']['tokens']['access_token'] == fresh
    binding.check()


@pytest.mark.parametrize('change', ['revoke', 'account', 'cancel'])
def test_refresh_cannot_resurrect_or_switch_connection(workspace, monkeypatch, change):
    auth(workspace, token(expiry=time.time() - 1))
    binding = ProfileCodexAuth.read(workspace)
    before = (workspace / 'auth.json').read_bytes()
    from eidolon_cli import auth_codex
    cancelled = False
    def refresh(*args, **kwargs):
        nonlocal cancelled
        if change == 'revoke': (workspace / 'auth.json').write_text('{"providers":{}}')
        if change == 'cancel': cancelled = True
        return {'access_token': token('other-account' if change == 'account' else 'synthetic-account'),
                'refresh_token': 'synthetic-new'}
    def check():
        if cancelled: raise InterruptedError('Synthetic cancellation')
    monkeypatch.setattr(auth_codex, 'refresh_codex_oauth_pure', refresh)
    with pytest.raises((isolation.ProfileIsolationError, InterruptedError)): binding.refresh(check)
    if change == 'revoke': assert json.loads((workspace / 'auth.json').read_text()) == {'providers': {}}
    else: assert (workspace / 'auth.json').read_bytes() == before

@pytest.mark.parametrize('headers', [{'Authorization': 'Bearer foreign'}, {'chatgpt-account-id': 'foreign'}, {'HOST': 'foreign.invalid'}])
def test_physical_send_rejects_request_identity_overrides(workspace, headers):
    value = auth(workspace)
    binding = ProfileCodexAuth.read(workspace)
    from eidolon_cli.profile_codex_auth import check_agent_auth
    agent = SimpleNamespace(_isolated_codex_auth=binding, api_key=value['providers']['openai-codex']['tokens']['access_token'], base_url=ENDPOINT)
    with pytest.raises(isolation.ProfileIsolationError):
        check_agent_auth(agent, request={'extra_headers': headers})
    check_agent_auth(agent, request={'extra_headers': {'session_id': 'local-session', 'x-client-request-id': 'local-request'}})


def test_invalid_refresh_never_overwrites_owned_auth(workspace, monkeypatch):
    auth(workspace, token(expiry=time.time() - 1))
    before = (workspace / 'auth.json').read_bytes()
    binding = ProfileCodexAuth.read(workspace)
    from eidolon_cli import auth_codex
    monkeypatch.setattr(auth_codex, 'refresh_codex_oauth_pure', lambda *a, **kw: {'access_token': token(), 'refresh_token': ''})
    with pytest.raises(isolation.ProfileIsolationError): binding.refresh(lambda: None)
    assert (workspace / 'auth.json').read_bytes() == before


def test_credential_pool_is_refused_at_construction(workspace):
    value = auth(workspace)
    with pytest.raises(isolation.ProfileIsolationError):
        isolation.initialize_agent(SimpleNamespace(), platform='organization', provider='openai-codex', model='gpt-5.4',
            api_key=value['providers']['openai-codex']['tokens']['access_token'], base_url=ENDPOINT,
            api_mode='codex_responses', enabled_toolsets=[], fallback_model=None, credential_pool=object())
