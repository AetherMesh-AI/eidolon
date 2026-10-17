"""Backend-only public-client sign-in. Not an RPC or inference registration.

Official protocol: developers.openai.com/siwc/token-sharing-open-source/sign-in
Discovery (2026-10-17): auth.openai.com/.well-known/openid-configuration.
Production endpoints are constants, never configuration or token-provided URLs.
Lock order: service -> durable attempt -> store. HTTP runs outside all locks.
A cancellation after exchange admission cannot undo remote provider work.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from functools import wraps
import hashlib
import hmac
import http.client
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
from eidolon_cli.organization_setup_subscription_store import TrustedBinding

AUTHORIZE = 'https://auth.openai.com/api/accounts/authorize'
TOKEN = 'https://auth.openai.com/api/accounts/oauth/token'
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
    'operation_failed', 'publication_uncertain', 'reauthorization_required', 'disconnect_required', 'setup_unavailable'})
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
            reason = error.reason if type(error) is AuthError else 'operation_failed'
            raise AuthError(reason) from None
    return call


def _text(value, maximum=16384):
    if (type(value) is not str or not value or len(value) > maximum
            or any(ord(c) < 33 or ord(c) > 126 for c in value)):
        raise AuthError('invalid_request')
    return value


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


def _production_http(url, form=None, *, cancel=None):
    """One request with bounded caller time, including stalled DNS resolution.

    A capped daemon worker may only connect, never send the authorization form.
    Timeout/cancel abandons it without releasing its slot until it really exits.
    A late connection is closed. Auto-connect is disabled for every HTTP send.
    Cancellation after physical send admission cannot undo remote work.
    """
    paths = {TOKEN: '/api/accounts/oauth/token', JWKS: '/.well-known/jwks.json'}
    if url not in paths or (url == JWKS and form is not None) or (url == TOKEN and form is None):
        raise AuthError('invalid_request')
    deadline = time.monotonic() + _HTTP_TIMEOUT
    abandoned, connected, finished = threading.Event(), threading.Event(), threading.Event()
    response_socket = [None]

    def send_allowed():
        return (not abandoned.is_set() and time.monotonic() < deadline
                and (cancel is None or not cancel.is_set()))

    class GuardedConnection(http.client.HTTPSConnection):
        def send(self, data):
            # Guard every physical header/body send, not merely request assembly.
            # No auto-connect: a watchdog-closed socket can never start new DNS.
            if not send_allowed() or self.sock is None:
                raise AuthError('exchange_failed')
            return super().send(data)

    connection = GuardedConnection('auth.openai.com', timeout=min(10, _HTTP_TIMEOUT))
    connection.auto_open = 0
    slots = _CONNECT_SLOTS

    def interrupt():
        abandoned.set()
        sock = response_socket[0] or connection.sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        try:
            connection.close()
        except Exception:
            pass

    def connect_only():
        try:
            if send_allowed():
                connection.connect()
        except Exception:
            abandoned.set()
        finally:
            # DNS/TLS may return after the caller has already timed out. This
            # worker cannot request(), and cannot resurrect its abandoned socket.
            try:
                if not send_allowed():
                    connection.close()
            except Exception:
                pass
            finally:
                connected.set()
                slots.release()

    def watchdog():
        while not finished.wait(min(.05, max(0, deadline - time.monotonic()))):
            if not send_allowed():
                interrupt()
                return

    if not send_allowed() or not slots.acquire(blocking=False):
        raise AuthError('exchange_failed')
    worker = threading.Thread(target=connect_only, daemon=True)
    try:
        worker.start()
    except Exception:
        slots.release()
        connection.close()
        raise AuthError('exchange_failed') from None
    watch = threading.Thread(target=watchdog, daemon=True)
    try:
        watch.start()
        while not connected.wait(min(.05, max(0, deadline - time.monotonic()))):
            if not send_allowed():
                raise AuthError('exchange_failed')
        # Only this bounded caller can admit a request. The connect worker never
        # holds the form, invokes request, or releases a still-running worker slot.
        if not send_allowed() or connection.sock is None:
            raise AuthError('exchange_failed')
        data = urlencode(form).encode('ascii') if form is not None else None
        connection.request('POST' if form is not None else 'GET', paths[url], body=data,
                           headers={'Accept': 'application/json',
                                    'Content-Type': 'application/x-www-form-urlencoded'})
        response_socket[0] = connection.sock
        response = connection.getresponse()
        if response.status != 200 or response.getheader('Content-Encoding', 'identity') != 'identity':
            raise AuthError('exchange_failed')
        length = response.getheader('Content-Length')
        if length is not None and (not length.isdecimal() or int(length) > MAX_BYTES):
            raise AuthError('exchange_failed')
        body = response.read(MAX_BYTES + 1)
        if not send_allowed():
            raise AuthError('exchange_failed')
        return _json(body)
    except Exception:
        raise AuthError('exchange_failed') from None
    finally:
        finished.set()
        interrupt()


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

    @_safe
    def verify(self, token, issued_client, nonce):
        _text(token, 24576)
        header = jwt.get_unverified_header(token)
        if header.get('alg') != 'RS256' or header.get('crit') or header.get('b64') is False:
            raise AuthError('invalid_identity')
        key = self._key(_text(header.get('kid'), 256))
        claims = jwt.decode(token, key, algorithms=['RS256'], audience=issued_client,
                            issuer=ISSUER, options={'require': ['exp', 'iss', 'aud', 'sub', 'nonce']})
        if claims.get('aud') != issued_client or not hmac.compare_digest(_text(claims.get('nonce')), nonce):
            raise AuthError('invalid_identity')
        if type(claims.get('exp')) is not int or claims['exp'] <= self._clock():
            raise AuthError('invalid_identity')
        return _text(claims.get('sub'), 2048)


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
                              or not callable(owner_context.setup_guard)):
            raise AuthError('invalid_request')
        self._store, self._host, self._owner = store, host, owner_context
        self._clock = clock
        self._setup_guard = owner_context.setup_guard or (lambda: True)
        self._browser = browser_open if synthetic and browser_open is not None else webbrowser.open
        self._http = http if synthetic and http is not None else _production_http
        self._verifier = TokenVerifier(self._http, clock)
        self._lock, self._attempt, self._retired = threading.RLock(), None, False
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
            scope = tokens.get('scope')
            if type(scope) is not str or len(scope) > 2048:
                raise AuthError('insufficient_scope')
            scopes = frozenset(scope.split())
            if not REQUIRED_SCOPES <= scopes:
                raise AuthError('insufficient_scope')
            if tokens.get('token_type', '').lower() != 'bearer':
                raise AuthError('invalid_identity')
            for name in ('access_token', 'refresh_token', 'id_token'):
                _text(tokens.get(name), 24576)
            lifetime = tokens.get('expires_in')
            if type(lifetime) is not int or not 1 <= lifetime <= 604800:
                raise AuthError('invalid_identity')
            account = self._verifier.verify(tokens['id_token'], client, attempt.nonce)
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
                    self._guard(attempt)
                    revision = self._store.connect_grant(binding, access_token=tokens['access_token'],
                        refresh_token=tokens['refresh_token'], id_token=tokens['id_token'],
                        expires_at=int(self._clock()) + lifetime, granted_scopes=scopes,
                        auth_attempt_id=attempt.attempt_id,
                        expected_revision=publication.record.expected_store_revision)
                    publication.commit(revision)
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
            if self._attempt is None:
                record = self._host.inspect()
                if record is not None:
                    self._record_scope(record)
                    stage = {'pending': 'reauthorization_required',
                             'exchange_admitted': 'reauthorization_required',
                             'publishing': 'reconciliation_required',
                             'published': 'completed', 'cancelled': 'cancelled'}[record.stage]
                    if record.expires_at <= self._clock() and record.stage in {'pending', 'exchange_admitted'}:
                        stage = 'expired'
                    result.update(attemptId=record.attempt_id, stage=stage, expiresAt=record.expires_at)
            return result

    def _record_scope(self, record):
        if (record.organization_id != self._owner.organization_id
                or record.owner_id != self._owner.owner_id
                or record.source_digest != self._owner.source_digest):
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
            if self._attempt is None:
                record = self._host.inspect()
                if record is None or record.attempt_id != attempt_id:
                    raise AuthError('stale_attempt')
                self._record_scope(record)
                self._host.cancel(attempt_id)
                return {'attemptId': attempt_id, 'stage': 'cancelled', 'expiresAt': record.expires_at}
            if self._attempt.attempt_id != attempt_id:
                raise AuthError('stale_attempt')
            self._cancel_locked(self._attempt)
            return self._status_locked()

    @_safe
    def disconnect(self, binding, expected_revision):
        with self._lock, self._authority():
            self._guard()
            self._binding(binding)
            if self._attempt:
                self._cancel_locked(self._attempt)
            return self._store.disconnect(expected_revision=expected_revision)

    @_safe
    def disconnect_current(self, expected_revision):
        with self._lock, self._authority():
            self._guard()
            binding = self._store.returning_binding()
            if binding is not None:
                self._binding(binding)
            if self._attempt:
                self._cancel_locked(self._attempt)
            return self._store.disconnect(expected_revision=expected_revision)

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
        with self._host.reconciliation_guard(attempt_id) as publication:
            self._record_scope(publication.record)
            if publication.record.expected_store_revision != prior_revision:
                raise AuthError('stale_attempt')
            self._guard()
            revision = self._store.reconcile_auth_attempt(binding, auth_attempt_id=attempt_id,
                prior_store_revision=prior_revision, granted_scopes=scopes)
            publication.commit(revision)
        if self._attempt is not None and self._attempt.attempt_id == attempt_id:
            self._attempt.stage = 'connected'
        return revision

    def _retire(self):
        with self._lock:
            self._retired = True
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
