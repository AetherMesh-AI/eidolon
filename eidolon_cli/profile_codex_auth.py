"""Strict workspace singleton credentials; no imports, pools or ambient routing.

This is the application's profile boundary, not protection against arbitrary Python.
Token values are never included in diagnostics or object representations.
"""
from dataclasses import dataclass, field
import json
import hashlib
import math
from pathlib import Path
import time

from eidolon_cli.profile_isolation import ProfileIsolationError, local_path, verify_isolation

PROVIDER = 'openai-codex'
ENDPOINT = 'https://chatgpt.com/backend-api/codex'


def _fail():
    raise ProfileIsolationError('The workspace Codex connection is missing, changed or unavailable; check this workspace’s account settings')


def _validate_tokens(tokens):
    if not isinstance(tokens, dict):
        _fail()
    for key in ('access_token', 'refresh_token'):
        value = tokens.get(key)
        if not isinstance(value, str) or not value.strip() or value != value.strip() or any(ord(c) < 32 for c in value):
            _fail()


def _identity(home):
    home = local_path(home)
    marker = verify_isolation(home)
    if marker is None:
        _fail()
    stat = home.stat()
    config = local_path(home / 'config.yaml')
    if config.stat().st_size > 2_000_000:
        _fail()
    return (stat.st_dev, stat.st_ino, json.dumps(marker, sort_keys=True),
            hashlib.sha256(config.read_bytes()).hexdigest())


def _load(home):
    path = local_path(home / 'auth.json')
    try:
        if path.stat().st_size > 2_000_000:
            _fail()
        store = json.loads(path.read_text(encoding='utf-8-sig'))
        from eidolon_cli.auth import AUTH_STORE_VERSION
        if store.get('version') != AUTH_STORE_VERSION:
            _fail()
        state = store['providers'][PROVIDER]
        tokens = state['tokens']
        _validate_tokens(tokens)
        from eidolon_cli.auth import _decode_jwt_claims
        claims = _decode_jwt_claims(tokens['access_token'])
        account = claims.get('https://api.openai.com/auth', {}).get('chatgpt_account_id')
        expiry = claims.get('exp')
        if (not isinstance(account, str) or not account or len(account) > 256
                or any(ord(c) < 32 for c in account)
                or type(expiry) not in (int, float) or not math.isfinite(expiry)):
            _fail()
        return store, dict(tokens), account, expiry
    except (OSError, KeyError, TypeError, ValueError, AttributeError):
        _fail()


@dataclass(repr=False)
class ProfileCodexAuth:
    home: Path
    identity: tuple
    tokens: dict = field(repr=False)
    account: str = field(repr=False)

    @classmethod
    def read(cls, home):
        from eidolon_cli.auth import _auth_store_lock
        home = local_path(home)
        identity = _identity(home)
        with _auth_store_lock(target_path=home / 'auth.json'):
            _, tokens, account, _ = _load(home)
            if _identity(home) != identity:
                _fail()
        return cls(home, identity, tokens, account)

    def check(self):
        if _identity(self.home) != self.identity:
            _fail()
        store, tokens, account, expiry = _load(self.home)
        if tokens != self.tokens or account != self.account:
            _fail()
        return store, expiry

    def refresh(self, check_authority, *, force=False):
        from eidolon_cli.auth import _auth_store_lock, _save_auth_store
        from eidolon_cli.auth_codex import refresh_codex_oauth_pure
        with _auth_store_lock(target_path=self.home / 'auth.json'):
            check_authority()
            store, expiry = self.check()
            if not force and expiry > time.time() + 60:
                return False
            fresh = refresh_codex_oauth_pure(self.tokens['access_token'], self.tokens['refresh_token'],
                                             timeout_seconds=15, private_transport=True)
            check_authority()
            self.check()  # Do not resurrect a connection removed/replaced during refresh.
            _validate_tokens(fresh)
            from eidolon_cli.auth import _decode_jwt_claims
            claims = _decode_jwt_claims(fresh['access_token'])
            account = claims.get('https://api.openai.com/auth', {}).get('chatgpt_account_id')
            exp = claims.get('exp')
            if account != self.account or type(exp) not in (int, float) or not math.isfinite(exp) or exp <= time.time():
                _fail()
            updated = {**self.tokens, **fresh}
            store['providers'][PROVIDER]['tokens'] = updated
            _save_auth_store(store, target_path=self.home / 'auth.json')
            self.tokens = updated
            self.check()
            return True


def check_agent_auth(agent, *, client=None, request=None):
    binding = getattr(agent, '_isolated_codex_auth', None)
    if binding is None:
        _fail()
    _, expiry = binding.check()
    if getattr(agent, '_credential_pool', None) is not None:
        _fail()
    if agent.api_key != binding.tokens['access_token'] or agent.base_url.rstrip('/') != ENDPOINT:
        _fail()
    if client is not None:
        if expiry <= time.time() or client.api_key != agent.api_key or str(client.base_url).rstrip('/') != ENDPOINT:
            _fail()
        from agent.codex_headers import codex_cloudflare_headers
        expected = codex_cloudflare_headers(agent.api_key, base_url=ENDPOINT)
        if (client.max_retries != 0 or client.organization or client.project
                or client._custom_headers != expected or client._custom_query
                or client._client.follow_redirects or client._client._trust_env):
            _fail()
    if request is not None:
        headers = request.get('extra_headers') or {}
        if (not isinstance(headers, dict) or any(
                not isinstance(key, str) or key.lower() not in {'session_id', 'x-client-request-id'}
                or not isinstance(value, str) or len(value) > 256 or any(ord(c) < 32 for c in value)
                for key, value in headers.items()) or request.get('extra_query')):
            _fail()


def refresh_agent_auth(agent, *, force=False):
    binding = getattr(agent, '_isolated_codex_auth', None)
    if binding is None:
        _fail()
    def check():
        from eidolon_cli.profile_isolation import validate_agent_send
        validate_agent_send(agent)
        if agent._organization_cancel.is_set() or time.monotonic() >= agent._organization_deadline:
            raise InterruptedError('Organization execution cancelled before credential refresh')
    changed = binding.refresh(check, force=force)
    if changed:
        agent.api_key = binding.tokens['access_token']
        agent._client_kwargs['api_key'] = agent.api_key
    return changed


def record_attempt(agent, observation, response):
    """Record reported usage per physical attempt; absent usage stays unknown."""
    usage = getattr(response, 'usage', None)
    def count(name):
        value = getattr(usage, name, None)
        return value if type(value) is int and 0 <= value <= 100_000_000 else None
    callback = getattr(agent, '_organization_record_observation', None)
    if callback is None:
        agent._organization_observation_failed = True
        return
    try:
        callback(observation['call_id'], input_tokens=count('input_tokens'),
                 output_tokens=count('output_tokens'),
                 elapsed_seconds=max(0.0, time.monotonic() - observation['started']),
                 outcome='response' if response is not None else 'error')
    except Exception:
        # Retain reservation and unknown usage; never retry a provider request.
        agent._organization_observation_failed = True
