"""Backend-only public-client sign-in. Not an RPC or inference registration.

Official protocol: developers.openai.com/siwc/token-sharing-open-source/sign-in
Discovery (2026-10-17): auth.openai.com/.well-known/openid-configuration.
Production endpoints are constants, never configuration or token-provided URLs.
Lock order: service -> owner-publication -> durable attempt -> store. HTTP runs outside all locks.
A cancellation after exchange admission cannot undo remote provider work.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from functools import wraps
import hashlib
import hmac
import http.client  # noqa: F401 - compatibility alias for transport test injection.
import json
import re
import secrets
import socket
import threading
import time
from urllib.parse import parse_qsl, urlencode
import uuid
import weakref
import webbrowser

import jwt
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from eidolon_cli.organization_setup_subscription_store import TrustedBinding, StoreError

AUTHORIZE = 'https://auth.openai.com/api/accounts/authorize'
TOKEN = 'https://auth.openai.com/api/accounts/oauth/token'
REVOKE = 'https://auth.openai.com/api/accounts/oauth/revoke'
JWKS = 'https://auth.openai.com/.well-known/jwks.json'
ISSUER = 'https://auth.openai.com'
RESOURCE = 'https://api.openai.com/v1'
REQUIRED_SCOPES = frozenset({'offline_access', 'resource.invoke', 'chatgpt.tokens.use.direct'})
SCOPES = 'openid profile email offline_access resource.invoke chatgpt.tokens.use.direct'
MAX_BYTES = 65536
TTL = 600
_REASONS = frozenset({'invalid_request', 'invalid_callback', 'access_denied',
    'invalid_identity', 'insufficient_scope', 'exchange_failed', 'attempt_expired',
    'stale_owner', 'stale_attempt', 'attempt_pending', 'browser_unavailable',
    'operation_failed', 'publication_uncertain', 'reauthorization_required', 'disconnect_required', 'setup_unavailable', 'refresh_pending', 'refresh_uncertain',
    'connection_unavailable', 'grant_expired', 'revision_conflict', 'lease_cancelled',
    'owner_generation_changed', 'auth_attempt_mismatch', 'invalid_grant',
    'ticket_conflict', 'ticket_retired', 'transition_pending', 'storage_recovery_required',
    'ticket_sequence_exhausted', 'operation_mismatch'})
_SERVICES = weakref.WeakSet()
_SERVICES_LOCK = threading.Lock()
_CONNECT_SLOTS = threading.BoundedSemaphore(4)
_HTTP_TIMEOUT = 15


class AuthError(RuntimeError):
    def __init__(self, reason):
        self.reason = reason if type(reason) is str and reason in _REASONS else 'operation_failed'
        super().__init__('Setup subscription: ' + self.reason)


def _safe(method):
    @wraps(method)
    def call(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except Exception as error:
            reason = error.reason if type(error) in {AuthError, StoreError} else 'operation_failed'
            raise AuthError(reason) from None
    return call


def _text(value, maximum=16384):
    if (type(value) is not str or not value or len(value) > maximum
            or any(ord(c) < 33 or ord(c) > 126 for c in value)):
        raise AuthError('invalid_request')
    return value


def _scopes(value):
    # OAuth scope-token excludes space, double quote and backslash. Canonical
    # single spaces avoid quietly accepting duplicate or control-filled grants.
    if type(value) is not str or not 0 < len(value) <= 2048:
        raise AuthError('insufficient_scope')
    parts = value.split(' ')
    if (any(not part or any(not (ord(c) == 0x21 or 0x23 <= ord(c) <= 0x5B
            or 0x5D <= ord(c) <= 0x7E) for c in part) for part in parts)
            or len(parts) != len(set(parts))):
        raise AuthError('insufficient_scope')
    return frozenset(parts)


def _decimal(value, *, positive=False):
    if (type(value) is not str or re.fullmatch(r'0|[1-9][0-9]{0,18}', value) is None
            or not int(positive) <= int(value) <= 9223372036854775807):
        raise AuthError('invalid_request')
    return value


def _ticket(value):
    if (type(value) is not dict or set(value) != {'version', 'sequence', 'nonce', 'kind',
            'bindingDigest', 'authorizationEpoch', 'priorStoreRevision', 'sourceRevision'}
            or type(value['version']) is not int or value['version'] != 1
            or value['kind'] not in ('refresh', 'revoke')
            or type(value['priorStoreRevision']) is not int
            or not 0 <= value['priorStoreRevision'] <= 9223372036854775806):
        raise AuthError('invalid_request')
    _decimal(value['sequence'], positive=True)
    for key in ('nonce', 'bindingDigest', 'sourceRevision'):
        if type(value[key]) is not str or re.fullmatch('[0-9a-f]{64}', value[key]) is None:
            raise AuthError('invalid_request')
    epoch = value['authorizationEpoch']
    if type(epoch) is not str or str(uuid.UUID(epoch)) != epoch or uuid.UUID(epoch).version != 4:
        raise AuthError('invalid_request')
    return dict(value)


def _cleanup_pending(state):
    value = state.get('cleanup_pending') if type(state) is dict else None
    if type(value) is not bool:
        raise AuthError('storage_recovery_required')
    return value


def _operation_history(value):
    unavailable = {'issuedThrough': None, 'retiredThrough': None, 'state': 'recovery_required'}
    if type(value) is not dict or set(value) != set(unavailable):
        return unavailable
    if value['state'] in ('legacy', 'recovery_required'):
        return dict(value) if value['issuedThrough'] is None and value['retiredThrough'] is None else unavailable
    try:
        issued, retired = _decimal(value['issuedThrough']), _decimal(value['retiredThrough'])
    except AuthError:
        return unavailable
    if value['state'] != 'available' or int(retired) > int(issued):
        return unavailable
    return dict(value)


def _operation_view(value, *, passive=False):
    keys = {'state', 'ticket', 'revision', 'verification'} | (set() if passive else {'receipt'})
    if (type(value) is not dict or set(value) != keys
            or value['state'] not in ('none', 'allocating', 'prepared', 'admitted', 'completed',
                                      'uncertain', 'recovery_required', 'local_retired')
            or type(value['revision']) is not int or not 0 <= value['revision'] < 2**63
            or value['verification'] != ('metadata_only' if passive else 'verified_current')):
        raise AuthError('storage_recovery_required')
    result = dict(value)
    if value['ticket'] is not None:
        result['ticket'] = _ticket(value['ticket'])
    if not passive and value['receipt'] is not None:
        receipt = value['receipt']
        if (type(receipt) is not dict or set(receipt) != {'kind', 'revision', 'localRetired', 'remoteRevocation'}
                or receipt['kind'] not in ('refresh', 'revoke', 'local')
                or type(receipt['revision']) is not int or not 0 <= receipt['revision'] < 2**63
                or type(receipt['localRetired']) is not bool
                or receipt['localRetired'] != (receipt['kind'] in ('revoke', 'local'))
                or receipt['remoteRevocation'] not in ('not_applicable', 'unconfirmed', 'confirmed')
                or (receipt['kind'] == 'refresh' and receipt['remoteRevocation'] != 'not_applicable')
                or (receipt['kind'] == 'local' and receipt['remoteRevocation'] != 'unconfirmed')
                or (receipt['kind'] == 'revoke' and receipt['remoteRevocation'] == 'not_applicable')):
            raise AuthError('storage_recovery_required')
        result['receipt'] = dict(receipt)
    return result


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AuthError('invalid_request')
        result[key] = value
    return result


def _json(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_BYTES:
        raise AuthError('exchange_failed')
    value = json.loads(raw, object_pairs_hook=_pairs)
    if type(value) is not dict:
        raise AuthError('exchange_failed')
    return value


def _production_http(url, form=None, *, cancel=None, bearer=None):
    """Compatibility boundary over the fixed-operation bounded transport."""
    from eidolon_cli.organization_setup_subscription_http import request, HttpError
    try:
        return request(url, form, cancel=cancel, bearer=bearer,
                       _timeout=_HTTP_TIMEOUT, _slots=_CONNECT_SLOTS)
    except HttpError as error:
        raise AuthError('invalid_request' if error.reason == 'invalid_request'
                        else 'exchange_failed') from None


class TokenVerifier:
    def __init__(self, http, clock=time.time):
        self._http, self._clock = http, clock
        self._keys, self._until = {}, 0
        self._lock = threading.Lock()

    def __repr__(self):
        return '<TokenVerifier>'

    def _key(self, kid):
        with self._lock:
            cached = self._keys.get(kid) if self._clock() < self._until else None
        if cached is not None:
            return cached
        document = self._http(JWKS)  # Never hold a cache/service/store lock for HTTP.
        entries = document.get('keys')
        if type(entries) is not list or not 0 < len(entries) <= 32:
            raise AuthError('invalid_identity')
        keys = {}
        for entry in entries:
            if type(entry) is not dict:
                raise AuthError('invalid_identity')
            name = _text(entry.get('kid'), 256)
            if name in keys or entry.get('kty') != 'RSA' or entry.get('alg', 'RS256') != 'RS256':
                raise AuthError('invalid_identity')
            if entry.get('use', 'sig') != 'sig' or entry.get('key_ops', ['verify']) != ['verify']:
                raise AuthError('invalid_identity')
            # Do not follow x5u/jku or construct keys from private RSA material.
            if any(k in entry for k in ('d', 'p', 'q', 'dp', 'dq', 'qi')):
                raise AuthError('invalid_identity')
            _text(entry.get('n'), 1400)
            _text(entry.get('e'), 8)
            key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps({k: entry[k] for k in ('kty', 'n', 'e')}))
            if (not isinstance(key, RSAPublicKey) or not 2048 <= key.key_size <= 8192
                    or key.public_numbers().e != 65537):
                raise AuthError('invalid_identity')
            keys[name] = key
        with self._lock:
            self._keys, self._until = keys, self._clock() + 300
        if kid not in keys:
            raise AuthError('invalid_identity')
        return keys[kid]

    def _claims(self, token, issued_client, *, initial):
        _text(token)
        header = jwt.get_unverified_header(token)
        if header.get('alg') != 'RS256' or header.get('crit') or header.get('b64') is False:
            raise AuthError('invalid_identity')
        key = self._key(_text(header.get('kid'), 256))
        required = ['exp', 'iat', 'iss', 'aud', 'sub'] + (['nonce'] if initial else [])
        claims = jwt.decode(token, key, algorithms=['RS256'], audience=issued_client,
                            issuer=ISSUER, options={'require': required})
        if (claims.get('aud') != issued_client or type(claims.get('exp')) is not int
                or claims['exp'] <= self._clock() or type(claims.get('iat')) is not int
                or claims['iat'] > self._clock() or claims['iat'] >= claims['exp']):
            raise AuthError('invalid_identity')
        _text(claims.get('sub'), 2048)
        if 'azp' in claims and claims['azp'] != issued_client:
            raise AuthError('invalid_identity')
        if 'auth_time' in claims and (type(claims['auth_time']) is not int
                or not 0 <= claims['auth_time'] <= claims['iat']):
            raise AuthError('invalid_identity')
        return claims

    @staticmethod
    def _access_hash(claims, access_token):
        if 'at_hash' in claims:
            _text(access_token)
            expected = base64.urlsafe_b64encode(hashlib.sha256(
                access_token.encode('ascii')).digest()[:16]).rstrip(b'=').decode('ascii')
            if not hmac.compare_digest(_text(claims['at_hash']), expected):
                raise AuthError('invalid_identity')

    @_safe
    def verify_initial(self, token, issued_client, nonce, access_token):
        claims = self._claims(token, issued_client, initial=True)
        if not hmac.compare_digest(_text(claims['nonce']), nonce):
            raise AuthError('invalid_identity')
        self._access_hash(claims, access_token)
        return {'subject': claims['sub'], 'audience': issued_client, 'nonce': nonce,
                'authTime': claims.get('auth_time'), 'authorizedParty': claims.get('azp')}

    @_safe
    def verify(self, token, issued_client, nonce):
        # Compatibility for initial identity-only callers; an at_hash requires
        # the full initial response and cannot be silently ignored here.
        return self.verify_initial(token, issued_client, nonce, None)['subject']

    @_safe
    def verify_refresh(self, token, issued_client, evidence, access_token):
        """Refresh ID is optional; when supplied verify it independently.

        Never decode historical ID tokens to rebuild original consent evidence.
        Missing nonce/auth_time/azp is allowed; supplied contradictions are not.
        """
        claims = self._claims(token, issued_client, initial=False)
        if claims['sub'] != evidence['subject'] or issued_client != evidence['audience']:
            raise AuthError('invalid_identity')
        if 'nonce' in claims and not hmac.compare_digest(_text(claims['nonce']), evidence['nonce']):
            raise AuthError('invalid_identity')
        if 'auth_time' in claims and claims['auth_time'] != evidence['authTime']:
            raise AuthError('invalid_identity')
        if 'azp' in claims and evidence['authorizedParty'] not in (None, claims['azp']):
            raise AuthError('invalid_identity')
        self._access_hash(claims, access_token)
        return True


@_safe
def parse_callback(raw, peer, port):
    """Strict origin-form HTTP/1.x parser; never echo request bytes in errors."""
    if type(raw) is not bytes or len(raw) > 12288 or peer != '127.0.0.1':
        raise AuthError('invalid_callback')
    head, separator, body = raw.partition(b'\r\n\r\n')
    if not separator or body:
        raise AuthError('invalid_callback')
    lines = head.decode('ascii').split('\r\n')
    if len(lines) > 33:
        raise AuthError('invalid_callback')
    method, target, protocol = lines[0].split(' ')
    if (method != 'GET' or protocol not in {'HTTP/1.0', 'HTTP/1.1'}
            or len(target) > 8192 or not target.startswith('/auth/callback?')
            or '#' in target or re.search(r'%(?![0-9a-fA-F]{2})', target)):
        raise AuthError('invalid_callback')
    headers = {}
    for line in lines[1:]:
        name, value = line.split(':', 1)
        if not re.fullmatch('[A-Za-z0-9-]+', name) or any(ord(c) < 32 for c in value):
            raise AuthError('invalid_callback')
        name = name.lower()
        if name in headers:
            raise AuthError('invalid_callback')
        headers[name] = value.strip()
    if (headers.get('host') != '127.0.0.1:' + str(port)
            or 'transfer-encoding' in headers
            or headers.get('content-length', '0') != '0'):
        raise AuthError('invalid_callback')
    fields = _pairs(parse_qsl(target.split('?', 1)[1], keep_blank_values=True,
                             strict_parsing=True, max_num_fields=12, encoding='utf-8', errors='strict'))
    if set(fields) - {'state', 'code', 'client_id', 'scope', 'error', 'error_description', 'error_uri'}:
        raise AuthError('invalid_callback')
    _text(fields.get('state'), 256)
    if 'error' in fields:
        if 'code' in fields:
            raise AuthError('invalid_callback')
        _text(fields['error'], 256)
    else:
        _text(fields.get('code'), 4096)
    if 'client_id' in fields:
        _text(fields['client_id'], 256)
    return fields


class LoopbackListener:
    """Small bounded socket listener; no BaseHTTPRequestHandler logging paths."""
    def __init__(self, callback, expires_at, clock=time.time):
        self._callback, self._expires, self._clock = callback, expires_at, clock
        self._stop = threading.Event()
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.bind(('127.0.0.1', 0))
        self._socket.listen(4)
        self._socket.settimeout(.25)
        self.port = self._socket.getsockname()[1]
        self.redirect_uri = 'http://127.0.0.1:' + str(self.port) + '/auth/callback'
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __repr__(self):
        return '<LoopbackListener>'

    def start(self):
        self._thread.start()

    def close(self):
        self._stop.set()
        self._socket.close()

    def _serve(self):
        try:
            while not self._stop.is_set() and self._clock() < self._expires:
                try:
                    connection, peer = self._socket.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                with connection:
                    connection.settimeout(1)
                    raw, fields, ok = b'', None, False
                    deadline = time.monotonic() + 2
                    try:
                        while b'\r\n\r\n' not in raw and len(raw) <= 12288:
                            if time.monotonic() >= deadline:
                                raise AuthError('invalid_callback')
                            data = connection.recv(min(2048, 12289 - len(raw)))
                            if not data:
                                break
                            raw += data
                        fields = parse_callback(raw, peer[0], self.port)
                        ok = self._callback(fields)
                    except Exception:
                        pass  # Deliberately no traceback, URL, or request logging.
                    try:
                        body = b'Return to Eidolon.' if ok else b'Request rejected.'
                        response = (b'HTTP/1.1 ' + (b'200 OK' if ok else b'400 Bad Request')
                                    + b'\r\nContent-Type: text/plain\r\nCache-Control: no-store\r\n'
                                    + b'Referrer-Policy: no-referrer\r\nConnection: close\r\nContent-Length: '
                                    + str(len(body)).encode('ascii') + b'\r\n\r\n' + body)
                        connection.sendall(response)
                    except Exception:
                        pass
                    if ok:
                        break
        finally:
            self.close()


@dataclass(frozen=True, repr=False)
class OwnerContext:
    profile_home: str
    organization_id: str
    owner_id: str
    source_digest: str
    owner_generation: object
    transport: object
    owner_guard: object
    publication_authority: object = None
    setup_guard: object = None
    lease_generation_guard: object = None


@dataclass(repr=False)
class _Attempt:
    attempt_id: str
    expires_at: int
    prior_revision: int
    state: str
    nonce: str
    verifier: str
    returning_binding: object
    listener: object = None
    stage: str = 'pending'
    cancel: threading.Event = field(default_factory=threading.Event)
    verified_binding: object = None
    verified_scopes: object = None


class SubscriptionAuthService:
    def __init__(self, store, host, owner_context, *, synthetic=False,
                 browser_open=None, http=None, clock=time.time):
        if type(owner_context) is not OwnerContext or (not synthetic and (browser_open is not None or http is not None or clock is not time.time)):
            raise AuthError('invalid_request')
        if not synthetic and (not callable(owner_context.publication_authority)
                              or not callable(owner_context.setup_guard)
                              or not callable(owner_context.lease_generation_guard)):
            raise AuthError('invalid_request')
        self._store, self._host, self._owner = store, host, owner_context
        self._clock = clock
        self._setup_guard = owner_context.setup_guard or (lambda: True)
        self._browser = browser_open if synthetic and browser_open is not None else webbrowser.open
        self._http = http if synthetic and http is not None else _production_http
        self._verifier = TokenVerifier(self._http, clock)
        self._lock, self._attempt, self._retired = threading.RLock(), None, False
        self._lease_cancel = threading.Event()
        self._leases = set()
        self._generation_guard = owner_context.lease_generation_guard or (lambda: not self._lease_cancel.is_set())
        with _SERVICES_LOCK:
            _SERVICES.add(self)

    def __repr__(self):
        return '<SubscriptionAuthService>'

    @contextmanager
    def _authority(self):
        factory = self._owner.publication_authority
        with factory() if factory is not None else nullcontext():
            self._guard()
            yield

    def _guard(self, attempt=None):
        if self._retired or self._owner.owner_guard() is not True:
            raise AuthError('stale_owner')
        if attempt is not None:
            if self._setup_guard() is not True:
                raise AuthError('setup_unavailable')
            if self._attempt is not attempt or attempt.cancel.is_set():
                raise AuthError('stale_attempt')
            if self._clock() >= attempt.expires_at:
                raise AuthError('attempt_expired')

    def _fresh_guard(self):
        self._guard()
        if self._setup_guard() is not True:
            raise AuthError('setup_unavailable')

    def _attempt_generation_guard(self, attempt=None):
        """Only local cancellation/generation checks beneath attempt locks.

        Durable owner/source/setup eligibility must already have been checked
        under outer publication authority, before acquiring host/store locks.
        """
        if self._retired or not self._lease_guard():
            raise AuthError('stale_owner')
        if attempt is not None:
            if self._attempt is not attempt or attempt.cancel.is_set():
                raise AuthError('stale_attempt')
            if self._clock() >= attempt.expires_at:
                raise AuthError('attempt_expired')

    def _binding(self, binding):
        owner = self._owner
        if (type(binding) is not TrustedBinding or binding.host_id != self._host.installation_id()
                or binding.profile_locator != owner.profile_home
                or binding.organization_id != owner.organization_id or binding.owner_id != owner.owner_id
                or binding.issued_client_id == 'dynamic_agent_client'):
            raise AuthError('invalid_request')

    @_safe
    def begin(self, expected_revision, returning_binding=None):
        with self._lock, self._authority():
            self._guard()
            if self._setup_guard() is not True:
                raise AuthError('setup_unavailable')
            if self._attempt and self._attempt.stage in {'pending', 'exchanging'} and self._clock() < self._attempt.expires_at:
                raise AuthError('attempt_pending')
            state = self._store.status()
            if _cleanup_pending(state):
                raise AuthError('storage_recovery_required')
            history = _operation_history(self._store.history_status())
            if history['state'] in {'recovery_required', 'legacy'}:
                raise AuthError('storage_recovery_required')
            if state['revision'] != expected_revision:
                raise AuthError('stale_attempt')
            if state['state'] not in {'absent', 'disconnected'}:
                raise AuthError('disconnect_required')
            if returning_binding is None:
                returning_binding = self._store.returning_binding()
            if returning_binding is not None:
                self._binding(returning_binding)
            if self._attempt and self._attempt.listener is not None:
                self._attempt.listener.close()
            attempt = _Attempt(str(uuid.uuid4()), int(self._clock()) + TTL, expected_revision,
                               secrets.token_urlsafe(32), secrets.token_urlsafe(32),
                               secrets.token_urlsafe(48), returning_binding)
            self._host.begin(attempt_id=attempt.attempt_id, organization_id=self._owner.organization_id,
                             owner_id=self._owner.owner_id, expected_store_revision=expected_revision,
                             source_digest=self._owner.source_digest, expires_at=attempt.expires_at)
            self._attempt = attempt
            try:
                attempt.listener = LoopbackListener(lambda fields: self._callback(attempt, fields), attempt.expires_at, self._clock)
                params = {'client_id': returning_binding.issued_client_id if returning_binding else 'dynamic_agent_client',
                          'ext_agent_host_id': self._host.installation_id(), 'response_type': 'code',
                          'redirect_uri': attempt.listener.redirect_uri, 'scope': SCOPES,
                          'resource': RESOURCE, 'state': attempt.state, 'nonce': attempt.nonce,
                          'code_challenge_method': 'S256', 'code_challenge': base64.urlsafe_b64encode(
                              hashlib.sha256(attempt.verifier.encode('ascii')).digest()).rstrip(b'=').decode('ascii')}
                if returning_binding is None:
                    params['agent_name_hint'] = 'Eidolon'
                self._guard(attempt)
                attempt.listener.start()
                url = AUTHORIZE + '?' + urlencode(params)
            except Exception:
                self._cancel_locked(attempt)
                raise AuthError('browser_unavailable') from None
        # System-browser launch may block. Never retain service/publication locks.
        try:
            self._guard(attempt)
            if not self._browser(url):
                raise AuthError('browser_unavailable')
            with self._lock:
                self._guard(attempt)
                return self._status_locked()
        except Exception:
            with self._lock:
                self._cancel_locked(attempt)
            raise AuthError('browser_unavailable') from None

    def _callback(self, attempt, fields):
        claimed = False
        publication_started = False
        try:
            with self._lock, self._authority():
                self._guard(attempt)
                if attempt.stage != 'pending' or not hmac.compare_digest(fields['state'], attempt.state):
                    return False
                claimed = True
                if 'error' in fields:
                    self._cancel_locked(attempt)
                    attempt.stage = 'access_denied'
                    return True
                binding = attempt.returning_binding
                client = fields.get('client_id', binding.issued_client_id if binding else None)
                _text(client, 256)
                if client == 'dynamic_agent_client' or (binding and client != binding.issued_client_id):
                    raise AuthError('invalid_identity')
                self._guard(attempt)
                self._host.admit_exchange(attempt.attempt_id)
                attempt.stage = 'exchanging'
                form = {'grant_type': 'authorization_code', 'client_id': client, 'code': fields['code'],
                        'code_verifier': attempt.verifier, 'redirect_uri': attempt.listener.redirect_uri, 'resource': RESOURCE}
            # Admission is durable. Never retry this request, including uncertain results.
            self._guard(attempt)
            tokens = (_production_http(TOKEN, form, cancel=attempt.cancel)
                      if self._http is _production_http else self._http(TOKEN, form))
            self._guard(attempt)
            if type(tokens) is not dict or len(json.dumps(tokens).encode('utf-8')) > MAX_BYTES:
                raise AuthError('invalid_identity')
            scope = tokens.get('scope')
            if type(scope) is not str or len(scope) > 2048:
                raise AuthError('insufficient_scope')
            scopes = _scopes(scope)
            if not REQUIRED_SCOPES <= scopes:
                raise AuthError('insufficient_scope')
            if type(tokens.get('token_type')) is not str or tokens['token_type'].lower() != 'bearer':
                raise AuthError('invalid_identity')
            for name in ('access_token', 'refresh_token', 'id_token'):
                _text(tokens.get(name))
            lifetime = tokens.get('expires_in')
            if type(lifetime) is not int or not 1 <= lifetime <= 604800:
                raise AuthError('invalid_identity')
            evidence = self._verifier.verify_initial(tokens['id_token'], client, attempt.nonce, tokens['access_token'])
            account = evidence['subject']
            self._guard(attempt)
            if binding is not None and account != binding.account_id:
                raise AuthError('invalid_identity')
            if binding is None:
                binding = TrustedBinding(self._host.installation_id(), self._owner.profile_home,
                                         self._owner.organization_id, self._owner.owner_id,
                                         str(uuid.uuid4()), client, account)
            with self._lock, self._authority():
                self._guard(attempt)
                attempt.verified_binding, attempt.verified_scopes = binding, scopes
                publication_started = True
                with self._host.publication_guard(attempt.attempt_id) as publication:
                    self._attempt_generation_guard(attempt)
                    revision = self._store.connect_grant(binding, access_token=tokens['access_token'],
                        refresh_token=tokens['refresh_token'], id_token=tokens['id_token'],
                        expires_at=int(self._clock()) + lifetime, granted_scopes=scopes,
                        auth_attempt_id=attempt.attempt_id, identity_evidence=evidence,
                        expected_revision=publication.record.expected_store_revision)
                    publication.commit(revision)
                # Native publication can block while source eligibility changes.
                # Preserve exact commit proof, but never report stale success.
                self._guard(attempt)
                attempt.stage = 'connected'
            return True
        except Exception:
            with self._lock:
                if self._attempt is attempt and not attempt.cancel.is_set():
                    attempt.stage = 'publication_uncertain' if publication_started else 'failed'
            return True
        finally:
            # PKCE state is intentionally memory-only and never resurrected on restart.
            if claimed and attempt.stage != 'pending':
                attempt.verifier = attempt.state = attempt.nonce = ''

    def _status_locked(self):
        attempt = self._attempt
        if attempt is None:
            return {'attemptId': None, 'stage': 'idle', 'expiresAt': None}
        stage = attempt.stage
        if stage == 'pending' and self._clock() >= attempt.expires_at:
            stage = 'expired'
        return {'attemptId': attempt.attempt_id, 'stage': stage, 'expiresAt': attempt.expires_at}

    @_safe
    def status(self):
        with self._lock:
            self._guard()
            result = self._status_locked()
            state = self._store.status()
            result['connectionRevision'] = state['revision']
            result['connectionState'] = state['state']
            result['cleanupPending'] = _cleanup_pending(state)
            result['operation'] = _operation_view(self._store.ticket_status(), passive=True)
            if self._attempt is None:
                record = self._host.inspect()
                if record is not None:
                    self._record_scope(record, require_source=False)
                    if record.source_digest != self._owner.source_digest:
                        # Historical source grants no resume/exchange authority,
                        # but preserve the exact local cancellation target.
                        stage = 'stale_source'
                        result['canCancel'] = record.stage in {'pending', 'exchange_admitted'}
                    else:
                        stage = {'pending': 'reauthorization_required',
                                 'exchange_admitted': 'reauthorization_required',
                                 'publishing': 'reconciliation_required',
                                 'published': 'completed', 'cancelled': 'cancelled'}[record.stage]
                        if record.expires_at <= self._clock() and record.stage in {'pending', 'exchange_admitted'}:
                            stage = 'expired'
                    result.update(attemptId=record.attempt_id, stage=stage, expiresAt=record.expires_at)
            return result

    def _record_scope(self, record, *, require_source=True):
        if (record.organization_id != self._owner.organization_id
                or record.owner_id != self._owner.owner_id
                or (require_source and record.source_digest != self._owner.source_digest)):
            raise AuthError('stale_attempt')

    def _cancel_locked(self, attempt):
        attempt.cancel.set()
        if attempt.listener is not None:
            attempt.listener.close()
        try:
            if attempt.stage != 'connected':
                attempt.stage = 'cancelled'
                self._host.cancel(attempt.attempt_id)
        finally:
            attempt.verifier = attempt.state = attempt.nonce = ''

    @_safe
    def cancel(self, attempt_id):
        with self._lock, self._authority():
            self._guard()
            record = self._host.inspect()
            if record is None or record.attempt_id != attempt_id:
                raise AuthError('stale_attempt')
            self._record_scope(record, require_source=False)
            if record.stage not in {'pending', 'exchange_admitted', 'cancelled'}:
                raise AuthError('stale_attempt')
            if self._attempt is None:
                self._host.cancel(attempt_id)
                return {'attemptId': attempt_id, 'stage': 'cancelled', 'expiresAt': record.expires_at}
            if self._attempt.attempt_id != attempt_id:
                raise AuthError('stale_attempt')
            self._cancel_locked(self._attempt)
            return self._status_locked()

    def _lease_guard(self):
        # Store-lock callbacks MUST NOT enter SQLite or any coordinator lock.
        return not self._lease_cancel.is_set() and self._generation_guard() is True

    def _track_lease(self, lease):
        self._leases.add(lease)
        return lease

    def _retire_active_leases(self):
        # Caller holds service authority. Close old capabilities individually:
        # ordinary rotation/disconnect must not poison the next revoke lease.
        for lease in tuple(self._leases):
            lease.close()
        self._leases.clear()

    def release_lease(self, lease):
        lease.close()
        with self._lock:
            self._leases.discard(lease)

    @_safe
    def current_descriptor(self, expected_revision):
        with self._lock, self._authority():
            if self._setup_guard() is not True:
                raise AuthError('setup_unavailable')
            binding, revision = self._store.connection_descriptor()
            self._binding(binding)
            if revision != expected_revision:
                raise AuthError('stale_attempt')
            descriptor = self._store.grant_descriptor(binding, revision)
            return {'binding': binding, 'revision': revision,
                    'authorizationEpoch': descriptor['authorizationEpoch'],
                    'expiresAt': descriptor['expiresAt']}

    @_safe
    def lease_access_current(self, expected_revision):
        with self._lock, self._authority():
            if self._setup_guard() is not True:
                raise AuthError('setup_unavailable')
            binding, revision = self._store.connection_descriptor()
            self._binding(binding)
            if revision != expected_revision:
                raise AuthError('stale_attempt')
            return self._track_lease(self._store.lease_access_token(binding, revision,
                owner_generation=self._owner.owner_generation, owner_guard=self._lease_guard,
                cancel=self._lease_cancel))

    def _request(self, url, form, lease):
        return (_production_http(url, form, cancel=lease)
                if self._http is _production_http else self._http(url, form))

    def _operation_binding(self, ticket=None, *, allow_absent=False, require_source=True):
        binding = self._store.returning_binding()
        if binding is not None or not allow_absent:
            self._binding(binding)
        if require_source and ticket is not None and ticket['sourceRevision'] != self._owner.source_digest:
            raise AuthError('stale_attempt')
        return binding

    def _operation_result(self, binding, value, *, catalog=True):
        result = _operation_view(value)
        result['catalogRevision'] = None
        self._guard()
        ticket, receipt = result['ticket'], result['receipt']
        if (catalog and result['state'] == 'completed' and ticket is not None
                and ticket['kind'] == 'refresh' and receipt is not None
                and receipt['kind'] == 'refresh' and not receipt['localRetired']
                and ticket['sourceRevision'] == self._owner.source_digest
                and self._setup_guard() is True):
            self._fresh_guard()
            descriptor = self._store.grant_descriptor(binding, result['revision'])
            if (descriptor['authorizationEpoch'] != ticket['authorizationEpoch']
                    or descriptor['tokenRevision'] != result['revision']
                    or receipt['revision'] != result['revision']
                    or result['revision'] != ticket['priorStoreRevision'] + 1):
                raise AuthError('operation_mismatch')
            self._fresh_guard()
            result['catalogRevision'] = result['revision']
        return result

    @_safe
    def prepare_operation(self, kind, expected_revision):
        if kind not in ('refresh', 'revoke'):
            raise AuthError('invalid_request')
        with self._lock, self._authority():
            if kind == 'refresh':
                self._fresh_guard()
            binding = self._operation_binding(allow_absent=kind == 'revoke')
            if kind == 'revoke':
                if self._attempt:
                    self._cancel_locked(self._attempt)
                self._retire_active_leases()
            if binding is None:
                receipt = self._store.local_disconnect(None, expected_revision=expected_revision,
                    owner_guard=self._lease_guard, cancel=self._lease_cancel)
                return self._operation_result(None, {'state': 'local_retired', 'ticket': None,
                    'receipt': receipt, 'revision': receipt['revision'],
                    'verification': 'verified_current'}, catalog=False)
            try:
                ticket = self._store.prepare_ticket(binding, expected_revision, kind=kind,
                    source_revision=self._owner.source_digest, owner_guard=self._lease_guard,
                    cancel=self._lease_cancel)
                ticket = _ticket(ticket)
                result = self._store.reconcile_ticket(binding, ticket=ticket,
                    owner_guard=self._lease_guard, cancel=self._lease_cancel)
            except StoreError as error:
                if kind != 'revoke':
                    raise
                if error.reason == 'ticket_conflict':
                    # Explicit Disconnect may locally retire a stranded refresh,
                    # but a metadata hint alone cannot authorize this fallback.
                    hint = _operation_view(self._store.ticket_status(), passive=True)
                    pending = hint['ticket']
                    if (pending is None or pending['kind'] != 'refresh'
                            or hint['state'] not in ('admitted', 'uncertain')):
                        raise
                    checked = _operation_view(self._store.reconcile_ticket(binding, ticket=pending,
                        owner_guard=self._lease_guard, cancel=self._lease_cancel))
                    if (checked['state'] not in ('admitted', 'uncertain')
                            or checked['ticket'] != pending):
                        raise
                    if checked['revision'] != expected_revision:
                        raise AuthError('stale_attempt')
                elif error.reason not in {
                        'storage_recovery_required', 'transition_pending', 'publication_uncertain'}:
                    raise
                receipt = self._store.local_disconnect(binding, expected_revision=expected_revision,
                    owner_guard=self._lease_guard, cancel=self._lease_cancel)
                result = {'state': 'local_retired', 'ticket': None, 'receipt': receipt,
                          'revision': receipt['revision'], 'verification': 'verified_current'}
            if kind == 'refresh':
                self._fresh_guard()
            return self._operation_result(binding, result, catalog=False)

    @_safe
    def execute_operation(self, ticket):
        ticket = _ticket(ticket)
        with self._lock, self._authority():
            binding = self._operation_binding(ticket)
            if ticket['kind'] == 'refresh':
                self._fresh_guard()
            current = self._store.reconcile_ticket(binding, ticket=ticket,
                owner_guard=self._lease_guard, cancel=self._lease_cancel)
            current = _operation_view(current)
            if current['state'] != 'prepared':
                return self._operation_result(binding, current)
            self._retire_active_leases()
            if ticket['kind'] == 'refresh':
                lease = self._track_lease(self._store.admit_refresh(binding, ticket['priorStoreRevision'],
                    ticket=ticket, owner_generation=self._owner.owner_generation,
                    owner_guard=self._lease_guard, cancel=self._lease_cancel))
            else:
                if self._attempt:
                    self._cancel_locked(self._attempt)
                receipt, lease = self._store.disconnect_grant(binding,
                    expected_revision=ticket['priorStoreRevision'], ticket=ticket,
                    owner_guard=self._lease_guard, cancel=self._lease_cancel)
                if lease is not None:
                    self._track_lease(lease)
                else:
                    if receipt['kind'] == 'local':
                        return self._operation_result(binding, {'state': 'local_retired',
                            'ticket': None, 'receipt': receipt, 'revision': receipt['revision'],
                            'verification': 'verified_current'}, catalog=False)
                    result = self._store.reconcile_ticket(binding, ticket=ticket,
                        owner_guard=self._lease_guard, cancel=self._lease_cancel)
                    return self._operation_result(binding, result, catalog=False)
        if ticket['kind'] == 'refresh':
            return self._execute_refresh(binding, ticket, lease)
        try:
            def revoke(token, cancellation):
                self._guard()
                return self._request(REVOKE, {'token': token, 'token_type_hint': 'refresh_token',
                    'client_id': binding.issued_client_id}, cancellation)
            try:
                lease.run(revoke)
            except Exception:
                pass  # Only a verified exact local receipt may establish retirement.
            with self._lock, self._authority():
                result = self._store.reconcile_ticket(binding, ticket=ticket,
                    owner_guard=self._lease_guard, cancel=self._lease_cancel)
                return self._operation_result(binding, result, catalog=False)
        finally:
            self.release_lease(lease)

    def _execute_refresh(self, binding, ticket, lease):
        try:
            def exchange(refresh_token, cancellation):
                self._guard()
                if self._setup_guard() is not True:
                    raise AuthError('setup_unavailable')
                tokens = self._request(TOKEN, {'grant_type': 'refresh_token',
                    'client_id': binding.issued_client_id, 'refresh_token': refresh_token,
                    'resource': RESOURCE}, cancellation)
                if type(tokens) is not dict or len(json.dumps(tokens).encode('utf-8')) > MAX_BYTES:
                    raise AuthError('invalid_identity')
                for name in ('access_token', 'refresh_token'):
                    _text(tokens.get(name))
                if tokens.get('refresh_token') == refresh_token:
                    raise AuthError('invalid_identity')
                if type(tokens.get('token_type')) is not str or tokens['token_type'].lower() != 'bearer':
                    raise AuthError('invalid_identity')
                lifetime = tokens.get('expires_in')
                if type(lifetime) is not int or not 1 <= lifetime <= 604800:
                    raise AuthError('invalid_identity')
                scopes = frozenset(lease.granted_scopes)
                if 'scope' in tokens:
                    scope = tokens['scope']
                    if _scopes(scope) != scopes:
                        raise AuthError('insufficient_scope')
                identity = tokens.get('id_token')
                if 'id_token' in tokens:
                    _text(identity)
                    self._verifier.verify_refresh(identity, binding.issued_client_id,
                                                  lease.identity_evidence, tokens['access_token'])
                with self._lock, self._authority():
                    if self._setup_guard() is not True or cancellation.is_set():
                        raise AuthError('stale_owner')
                    result = self._store.commit_refresh(binding,
                        ticket=ticket,
                        access_token=tokens['access_token'], refresh_token=tokens['refresh_token'],
                        id_token=identity, expires_at=int(self._clock()) + lifetime,
                        granted_scopes=scopes, identity_evidence=lease.identity_evidence,
                        owner_guard=self._lease_guard, cancel=self._lease_cancel)
                return result
            revision = lease.run(exchange)
            with self._lock, self._authority():
                self._fresh_guard()
                result = self._store.reconcile_ticket(binding, ticket=ticket,
                    owner_guard=self._lease_guard, cancel=self._lease_cancel)
                if result['revision'] != revision:
                    raise AuthError('operation_mismatch')
                return self._operation_result(binding, result)
        finally:
            self.release_lease(lease)

    @_safe
    def reconcile_operation(self, ticket):
        ticket = _ticket(ticket)
        with self._lock, self._authority():
            binding = self._operation_binding(ticket, require_source=False)
            result = self._store.reconcile_ticket(binding, ticket=ticket,
                owner_guard=self._lease_guard, cancel=self._lease_cancel)
            return self._operation_result(binding, result)

    @_safe
    def cancel_operation(self, ticket):
        ticket = _ticket(ticket)
        with self._lock, self._authority():
            binding = self._operation_binding(ticket, require_source=False)
            current = self._store.reconcile_ticket(binding, ticket=ticket,
                owner_guard=self._lease_guard, cancel=self._lease_cancel)
            if _operation_view(current)['state'] != 'prepared':
                raise AuthError('ticket_conflict')
            self._retire_active_leases()
            result = self._store.cancel_ticket(binding, ticket=ticket,
                owner_guard=self._lease_guard, cancel=self._lease_cancel)
            return self._operation_result(binding, result, catalog=False)

    @_safe
    def recover_operation_current(self):
        with self._lock, self._authority():
            binding = self._operation_binding()
            result = self._store.reconcile_transition(binding,
                owner_guard=self._lease_guard, cancel=self._lease_cancel)
            result = self._operation_result(binding, result)
            result['cleanupPending'] = _cleanup_pending(self._store.status())
            self._guard()
            return result

    @_safe
    def reconcile_current(self):
        with self._lock, self._authority():
            self._guard()
            attempt = self._attempt
            if attempt is None or attempt.verified_binding is None or attempt.stage != 'publication_uncertain':
                raise AuthError('stale_attempt')
            return self._reconcile_locked(attempt.verified_binding, attempt.attempt_id,
                                          attempt.prior_revision, attempt.verified_scopes)

    @_safe
    def reconcile(self, binding, attempt_id, prior_revision, scopes):
        with self._lock, self._authority():
            self._guard()
            return self._reconcile_locked(binding, attempt_id, prior_revision, scopes)

    def _reconcile_locked(self, binding, attempt_id, prior_revision, scopes):
        self._binding(binding)
        self._fresh_guard()  # Durable owner/source/setup read precedes attempt lock.
        with self._host.reconciliation_guard(attempt_id) as publication:
            self._record_scope(publication.record)
            if publication.record.expected_store_revision != prior_revision:
                raise AuthError('stale_attempt')
            self._attempt_generation_guard()
            if (self._attempt is not None and self._attempt.attempt_id == attempt_id
                    and self._attempt.cancel.is_set()):
                raise AuthError('stale_attempt')
            revision = self._store.reconcile_auth_attempt(binding, auth_attempt_id=attempt_id,
                prior_store_revision=prior_revision, granted_scopes=scopes)
            publication.commit(revision)
        # Read durable eligibility only after releasing attempt/store locks.
        self._fresh_guard()
        if self._attempt is not None and self._attempt.attempt_id == attempt_id:
            self._attempt.stage = 'connected'
        return revision

    def _retire(self):
        with self._lock:
            self._retired = True
            self._lease_cancel.set()
            self._retire_active_leases()
            if self._attempt:
                try:
                    self._cancel_locked(self._attempt)
                except Exception:
                    pass  # Local cancellation/closure already applied; no secret logs.


def cancel_transport(transport, *, generation=None):
    """Called outside registry lock on retirement; identity comparison only."""
    with _SERVICES_LOCK:
        services = [service for service in _SERVICES if service._owner.transport is transport
                    and service._owner.owner_generation is generation]
    for service in services:
        service._retire()
